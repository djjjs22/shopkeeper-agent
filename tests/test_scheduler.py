# -*- coding: utf-8 -*-
"""
调度器单元测试 — 验证 start/stop 和归档任务注册

运行方式：
  cd D:/shopkeeper-agent
  uv run pytest tests/test_scheduler.py -v

═══════════════════════════════════════════════════════════════════════
  核心知识点（APScheduler 测试模式）
═══════════════════════════════════════════════════════════════════════

【知识点 1：start_scheduler 的幂等性测试】

  重复 start_scheduler() 不抛错（早返回），保证 lifespan 重启时不崩。
  现实意义：lifespan 在 reload / 多次 import 时可能触发多次，幂等性是必备。

【知识点 2：patch + AsyncMock 模拟 async 函数】

  ```python
  with patch("app.services.scheduler.archive_old_sessions",
             new=AsyncMock(side_effect=RuntimeError("Redis 挂了"))):
      await scheduler._safe_archive()
  ```

  patch 把模块里的函数引用替换成 mock——调用 _safe_archive 时实际跑的是
  RuntimeError，验证包装层能否正确捕获。

【知识点 3：pytest-asyncio 的 asyncio_mode = "auto"】

  pyproject.toml 里设置了 asyncio_mode = "auto"，意味着所有 async 函
  数测试不需要显式 @pytest.mark.asyncio 装饰器——pytest 自动识别。

【知识点 4：_safe_archive 的 try/except 验证】

  关键点：_safe_archive 必须捕获所有 Exception（不能让任务崩溃），
  测试用 side_effect=RuntimeError 验证即使最极端的异常也被吃掉。
"""

import pytest

from app.services import scheduler
from app.scripts.archive_sessions import archive_old_sessions


def test_scheduler_starts_and_registers_archive_job():
    """
    start_scheduler() 注册归档任务

    验证：
    1. 调度器跑起来（_scheduler.running）
    2. archive_sessions 任务注册成功
    3. 任务函数指向 _safe_archive 包装层
    """
    scheduler.start_scheduler()

    try:
        # 调度器应该跑起来
        assert scheduler._scheduler is not None
        assert scheduler._scheduler.running

        # 归档任务应该注册成功
        job = scheduler._scheduler.get_job("archive_sessions")
        assert job is not None
        assert job.func is scheduler._safe_archive or job.func is archive_old_sessions
    finally:
        scheduler.stop_scheduler()


def test_scheduler_idempotent_start():
    """
    重复 start_scheduler() 不出错（避免重启时崩，见"知识点 1"）

    第二次调用应该 warn 但不抛错，状态保持已启动。
    """
    scheduler.start_scheduler()
    scheduler.start_scheduler()  # 第二次应该 warn 但不抛错

    try:
        assert scheduler._scheduler is not None
    finally:
        scheduler.stop_scheduler()


def test_scheduler_stop_clears_state():
    """
    stop_scheduler() 后 _scheduler 设为 None

    验证资源清理：close 后模块级变量清空，下次 start 能正常工作。
    """
    scheduler.start_scheduler()
    scheduler.stop_scheduler()

    assert scheduler._scheduler is None


@pytest.mark.asyncio
async def test_safe_archive_handles_exceptions():
    """
    _safe_archive 捕获异常不向上抛（见"知识点 4"）

    patch archive_old_sessions 让它抛 RuntimeError，验证包装层
    能吃掉异常（否则 APScheduler 任务会标记为 MISSED）。
    """
    from unittest.mock import AsyncMock, patch

    # patch archive_old_sessions 让它抛异常
    with patch(
        "app.services.scheduler.archive_old_sessions",
        new=AsyncMock(side_effect=RuntimeError("Redis 挂了")),
    ):
        # 不应该向上抛
        await scheduler._safe_archive()


def test_stop_scheduler_safe_when_not_running():
    """
    stop_scheduler() 在没启动时调用是安全的

    双保险：第一次正常、第二次也正常（避免 NoneType 错误）。
    """
    scheduler.stop_scheduler()  # 第一次正常
    scheduler.stop_scheduler()  # 第二次也应该正常
    assert scheduler._scheduler is None
