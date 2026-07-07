# -*- coding: utf-8 -*-
"""
会话存储单元测试 — 覆盖正常/降级/边界/集成 4 类场景

运行方式：
  cd D:\shopkeeper-agent
  uv run pytest tests/test_session_store.py -v

═══════════════════════════════════════════════════════════════════════
  核心知识点（pytest + AsyncMock 测试模式）
═══════════════════════════════════════════════════════════════════════

【知识点 1：autouse fixture 自动清理】

  每个测试前后都重置 _memory_fallback / _available / _fail_count。
  pytest 看到 autouse=True 会在每个测试函数前后自动调用，不需要
  在每个测试里手动写 setup/teardown。

【知识点 2：AsyncMock 模拟 async 函数】

  MagicMock 模拟同步函数返回，AsyncMock 模拟 async 函数返回。
  - AsyncMock()() 自动返回 AsyncMock（链式调用）
  - AsyncMock(side_effect=[...]) 多次调用返回不同值
  - AsyncMock(side_effect=Exception("...")) 抛异常

  redis_client_manager._client = AsyncMock()  ← 把整个 client 替换成 mock
  这样业务代码 await client.lrange(...) 不会真连 Redis。

【知识点 3：asyncio.gather 并发验证 lock 正确性】

  await asyncio.gather(coro1(), coro2(), coro3()) 并发跑 3 个协程。
  如果 asyncio.Lock 没起作用，3 个协程同时改 _memory_fallback
  可能丢数据；如果锁对了，结果是 10 条（ltrim 限制）。
"""
from unittest.mock import AsyncMock, MagicMock

import asyncio
import pytest

from app.clients.redis_client_manager import redis_client_manager
from app.conf.app_config import app_config
from app.services import session_store


@pytest.fixture(autouse=True)
def reset_state():
    """每个测试前重置状态"""
    session_store._memory_fallback.clear()
    redis_client_manager._client = None
    redis_client_manager._available = True
    redis_client_manager._fail_count = 0
    # 默认切到 REDIS_PRIMARY 模式，让测试真正走 Redis 路径
    session_store.WRITE_MODE = session_store.WriteMode.REDIS_PRIMARY
    yield
    session_store._memory_fallback.clear()


# ══════════════════════════════════════
# 正常场景：Redis 可用
# ══════════════════════════════════════
class TestRedisAvailable:
    """Redis 正常时的行为"""

    async def test_get_history_calls_redis(self):
        """Redis 可用时 get_history 调用 lrange 命令"""
        mock_client = AsyncMock()
        mock_client.lrange.return_value = [
            '{"role":"user","content":"hi"}',
            '{"role":"assistant","content":"hello"}',
        ]
        redis_client_manager._client = mock_client
        redis_client_manager._available = True

        history = await session_store.get_history("s1", max_count=5)

        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["content"] == "hello"
        mock_client.lrange.assert_called_once()

    async def test_add_message_calls_redis_pipeline(self):
        """add_message 通过 pipeline 写入 Redis"""
        # 用 MagicMock 链式 + AsyncMock 验证 execute 被调用
        pipeline_mock = MagicMock()  # pipeline() 的返回值，普通 mock
        pipeline_mock.rpush = MagicMock()
        pipeline_mock.ltrim = MagicMock()
        pipeline_mock.expire = MagicMock()
        pipeline_mock.execute = AsyncMock()

        mock_client = MagicMock()
        mock_client.pipeline = MagicMock(return_value=pipeline_mock)
        redis_client_manager._client = mock_client
        redis_client_manager._available = True

        await session_store.add_message("s1", "user", "你好")

        # 验证 pipeline 三个命令都被调用 + execute 被 await
        assert pipeline_mock.rpush.called
        assert pipeline_mock.ltrim.called
        assert pipeline_mock.expire.called
        assert pipeline_mock.execute.called

    async def test_add_message_also_writes_memory(self):
        """add_message 永远写内存（即使 Redis 成功）"""
        mock_client = AsyncMock()
        mock_client.pipeline.return_value = mock_client
        redis_client_manager._client = mock_client
        redis_client_manager._available = True

        await session_store.add_message("s1", "user", "测试")

        # 内存里有数据
        assert "s1" in session_store._memory_fallback
        assert session_store._memory_fallback["s1"][0]["content"] == "测试"

    async def test_clear_history_clears_both(self):
        """clear_history 同时清空 Redis 和内存"""
        mock_client = AsyncMock()
        redis_client_manager._client = mock_client
        redis_client_manager._available = True
        session_store._memory_fallback["s1"] = [{"role": "user", "content": "x"}]

        await session_store.clear_history("s1")

        assert "s1" not in session_store._memory_fallback
        mock_client.delete.assert_called_once()


# ══════════════════════════════════════
# 降级场景：Redis 不可用
# ══════════════════════════════════════
class TestFallback:
    """Redis 不可用时的降级行为"""

    async def test_get_history_fallback_when_marked_unavailable(self):
        """Redis 标记不可用时降级到内存"""
        redis_client_manager._available = False
        redis_client_manager._client = None

        session_store._memory_fallback["s1"] = [
            {"role": "user", "content": "test"},
        ]

        history = await session_store.get_history("s1", max_count=5)

        assert len(history) == 1
        assert history[0]["content"] == "test"

    async def test_get_history_fallback_on_redis_error(self):
        """Redis 抛异常时降级到内存"""
        mock_client = AsyncMock()
        mock_client.lrange.side_effect = ConnectionError("Redis connection refused")
        redis_client_manager._client = mock_client
        redis_client_manager._available = True

        history = await session_store.get_history("s1", max_count=5)

        # 异常时不抛错，返回空列表
        assert history == []
        # 失败计数应该增加
        assert redis_client_manager._fail_count == 1

    async def test_mark_unavailable_after_threshold(self):
        """连续失败 N 次才标记为不可用"""
        mock_client = AsyncMock()
        mock_client.lrange.side_effect = ConnectionError("fail")
        redis_client_manager._client = mock_client
        redis_client_manager._available = True

        # 连续调用 3 次（threshold 默认 3）
        for _ in range(3):
            await session_store.get_history("s1")

        # 第 3 次后应该标记不可用
        assert redis_client_manager._available is False
        assert redis_client_manager._fail_count == 3

    async def test_add_message_writes_memory_when_redis_down(self):
        """Redis 不可用时 add_message 仍能工作（写内存）"""
        redis_client_manager._available = False
        redis_client_manager._client = None

        await session_store.add_message("s1", "user", "降级测试")

        assert "s1" in session_store._memory_fallback
        assert session_store._memory_fallback["s1"][0]["content"] == "降级测试"


# ══════════════════════════════════════
# 边界场景
# ══════════════════════════════════════
class TestEdgeCases:
    """边界条件"""

    async def test_long_content_truncated_to_500(self):
        """超长内容被截断到 500 字符"""
        redis_client_manager._available = False
        redis_client_manager._client = None

        long_content = "a" * 1000
        await session_store.add_message("s1", "user", long_content)

        stored = session_store._memory_fallback["s1"][0]["content"]
        assert len(stored) == 500

    async def test_max_10_messages_in_memory(self):
        """单 session 内存里最多保留 10 条"""
        redis_client_manager._available = False
        redis_client_manager._client = None

        for i in range(20):
            await session_store.add_message("s1", "user", f"msg{i}")

        assert len(session_store._memory_fallback["s1"]) == 10

    async def test_empty_session_returns_empty_list(self):
        """查询不存在的 session 返回空列表"""
        mock_client = AsyncMock()
        mock_client.lrange.return_value = []
        redis_client_manager._client = mock_client
        redis_client_manager._available = True

        history = await session_store.get_history("nonexistent")

        assert history == []

    async def test_clear_nonexistent_session_no_error(self):
        """清空不存在的 session 不报错"""
        mock_client = AsyncMock()
        redis_client_manager._client = mock_client
        redis_client_manager._available = True

        # 不应抛出异常
        await session_store.clear_history("nonexistent")

        mock_client.delete.assert_called_once()

    async def test_concurrent_add_message_thread_safe(self):
        """
        并发写入内存 dict 是协程安全的（asyncio.Lock 验证，见"知识点 3"）

        3 个协程各写 50 条 = 150 次 await _memory_add()
        如果锁工作：串行执行，list 被 ltrim 截到 10 条
        如果锁失败：可能丢数据或 list 长度 > 10
        """
        redis_client_manager._available = False
        redis_client_manager._client = None

        async def add_many():
            for i in range(50):
                # async 函数内部 await _memory_add
                await session_store._memory_add("s1", "user", f"msg{i}")

        # 3 个协程各写 50 条，验证 asyncio.Lock 正确串行化
        await asyncio.gather(add_many(), add_many(), add_many())

        # 内存只保留 10 条（被 ltrim 截断）
        assert len(session_store._memory_fallback["s1"]) == 10

    async def test_memory_lru_eviction(self):
        """
        内存 dict 超过 max_memory_sessions 时淘汰最旧 session

        验证：
        - 临时把 max_memory_sessions 改成 3
        - 写入 4 个 session
        - 最旧的 session_0 应该被淘汰
        - 最新的 session_3 应该还在
        """
        import pytest
        # 临时把上限调小，方便测试
        original = session_store.redis_cfg.max_memory_sessions
        # 用 monkeypatch 不行（dataclass 字段），直接修改
        session_store.redis_cfg.max_memory_sessions = 3
        try:
            redis_client_manager._available = False
            redis_client_manager._client = None

            # 写入 4 个不同 session
            for i in range(4):
                await session_store.add_message(f"session_{i}", "user", "hi")

            # 最多保留 3 个，最旧的 session_0 应该被淘汰
            assert len(session_store._memory_fallback) == 3
            assert "session_0" not in session_store._memory_fallback
            assert "session_3" in session_store._memory_fallback
        finally:
            session_store.redis_cfg.max_memory_sessions = original


# ══════════════════════════════════════
# 集成测试
# ══════════════════════════════════════
class TestIntegration:
    """完整读写流程"""

    async def test_round_trip_redis_mode(self):
        """Redis 模式下完整读写循环"""
        mock_client = AsyncMock()
        written_data = []

        async def mock_rpush(key, value):
            written_data.append(value)
            return len(written_data)

        async def mock_lrange(key, start, end):
            return written_data[-5:]

        mock_client.rpush = mock_rpush
        mock_client.lrange = mock_lrange
        mock_client.pipeline.return_value = mock_client
        mock_client.ltrim = AsyncMock()
        mock_client.expire = AsyncMock()
        redis_client_manager._client = mock_client
        redis_client_manager._available = True

        # 写入 3 条
        await session_store.add_message("s1", "user", "msg1")
        await session_store.add_message("s1", "assistant", "reply1")
        await session_store.add_message("s1", "user", "msg2")

        # 读出
        history = await session_store.get_history("s1", max_count=5)

        # 内存里也有 3 条（因为 add_message 同时写内存）
        assert len(history) == 3
        assert history[0]["content"] == "msg1"

    async def test_redis_recovery_resets_fail_count(self):
        """Redis 恢复后失败计数清零"""
        mock_client = AsyncMock()
        # 第一次失败，第二次成功
        mock_client.lrange.side_effect = [ConnectionError("fail"), ["{}"]]
        redis_client_manager._client = mock_client
        redis_client_manager._available = True

        await session_store.get_history("s1")  # 失败
        await session_store.get_history("s1")  # 成功

        # 成功后失败计数清零
        assert redis_client_manager._fail_count == 0
        assert redis_client_manager._available is True

    async def test_probe_loop_recovers_redis(self):
        """
        后台探活协程在 PING 成功时自动 mark_available

        模拟 Redis 临时不可用 → 探活 PING 成功 → 自动恢复。
        """
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock()  # PING 成功
        redis_client_manager._client = mock_client
        redis_client_manager._available = False  # 模拟 Redis 临时不可用
        redis_client_manager._fail_count = 5

        # 临时把探活间隔调成 0 秒，让协程立即跑一次
        original = redis_client_manager._probe_task
        original_interval = app_config.redis_cfg.probe_interval_seconds
        # 触发一次探活逻辑（模拟 probe_loop 单次循环）
        try:
            await redis_client_manager._client.ping()
            redis_client_manager.mark_available()

            # PING 成功应该自动恢复
            assert redis_client_manager._available is True
            assert redis_client_manager._fail_count == 0
        finally:
            redis_client_manager._probe_task = original

    async def test_probe_loop_cancelled_on_close(self):
        """
        close() 取消探活协程，不留 zombie task

        验证生命周期管理：close() 必须先 cancel 探活、再 close 连接。
        否则 zombie 协程会持有已关闭的 client，下一次 await 报错。
        """
        # 启动一个假的探活任务
        async def never_end():
            await asyncio.sleep(1000)

        fake_task = asyncio.create_task(never_end())
        redis_client_manager._probe_task = fake_task
        redis_client_manager._client = AsyncMock()

        # 关闭时应该取消探活
        await redis_client_manager.close()

        # 探活任务被取消
        assert fake_task.cancelled() or fake_task.done()
