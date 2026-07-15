"""
应用内定时任务调度器

负责在 FastAPI 启动时注册定时任务，关闭时取消所有任务。

当前注册的任务：
- 每天凌晨 02:00 跑 archive_old_sessions()，把 Redis 7 天前的数据迁移到 MySQL

设计选择：APScheduler（in-process，AsyncIOScheduler）
- 优点：跟 FastAPI 同进程，无需额外服务（k8s CronJob / celery beat）
- 缺点：多副本部署时每个副本都会跑——靠 MySQL 唯一键防重复归档

═══════════════════════════════════════════════════════════════════════
  核心知识点（设计决策的"为什么"）
═══════════════════════════════════════════════════════════════════════

【知识点 1：APScheduler 三种调度器对比】

  - BlockingScheduler：阻塞当前线程，不能跟 FastAPI 共存
  - BackgroundScheduler：后台线程，但 asyncio 里要小心线程切换
  - AsyncIOScheduler：在事件循环里调度任务，跟 FastAPI 完美兼容 ★ 本项目用这个

  【面试加分点】能讲出"为什么不能用 BlockingScheduler"和
  "BackgroundScheduler 在 async 上下文里的坑"。

【知识点 2：CronTrigger vs cron 字符串】

  CronTrigger(hour=2, minute=0)  # ← Python 对象
  vs
  "0 2 * * *"                      # ← cron 字符串

  - CronTrigger 更可读（IDE 能补全、类型检查、不会写错字段顺序）
  - cron 字符串更短但容易写错（* * * * * 5 个字段谁记得清）
  - 复杂规则 CronTrigger 也能搞定（"每月第一个周一"等）

【知识点 3：coalesce=True 的工程意义】

  应用停了 3 天再启动，APScheduler 默认会"补跑"3 次（每天 1 次）。
  coalesce=True 表示：错过多次只跑 1 次。

  现实意义：
  - Redis 7 天前的数据 3 天没归档 ≠ 重要性 ×3
  - 1 次归档已经够，不需要重复浪费资源
  - 避免"启动雪崩"（同时触发 3 个归档任务）

【知识点 4：max_instances=1 防重叠执行】

  假设上一次归档因为 Redis 卡住没跑完，下一次调度（02:00 第二天）又触发。
  max_instances=1 强制排队——上一个完成才能开始下一个。

  现实意义：归档任务里要 SCAN 整个 Redis 内存、并写入 MySQL，
  并发跑会双重 IO、可能锁竞争。

【知识点 5：_safe_archive 包装层 - 异常隔离】

  APScheduler 任务抛异常会被记为"MISSED"——下次调度可能受影响。
  包一层 try/except 让业务异常不影响调度器。

  ```python
  async def _safe_archive():
      try:
          await archive_old_sessions(...)
      except Exception as e:
          logger.error(...)  # 记一行就够
          # 不要 raise，让 APScheduler 以为任务成功
  ```

  实际错误（Redis 挂了、MySQL 写失败）已经在 archive_old_sessions
  内部记录日志，这里只记"归档任务异常"的高层信号。

【知识点 6：多副本部署的风险和缓解】

  风险：3 个 FastAPI 实例（k8s deployment replicas=3）每个都跑 APScheduler，
  每天 02:00 会有 3 个归档任务并发。

  缓解方案（按推荐度排序）：
  1. MySQL 唯一键（session_id 是主键）+ ON DUPLICATE KEY UPDATE
     → 第一个抢到的写成功，后两个 SQL 不报错只更新
  2. APScheduler 配 SQLAlchemyJobStore（共享任务锁）
     → 性能开销大，本项目没必要
  3. 单独部署一个"worker"实例跑调度器
     → 增加运维复杂度

  本项目用方案 1——因为 archive_sessions.py 内部 SQL 已经用
  ON DUPLICATE KEY UPDATE，不需要额外处理。
"""
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.scripts.archive_sessions import archive_old_sessions
from app.core.log import logger

# 模块级单例（跟 redis_client_manager 同样的模式）
_scheduler: Optional[AsyncIOScheduler] = None


def start_scheduler() -> None:
    """
    启动调度器，注册归档任务（每天 02:00）

    幂等性：重复调用不会出错（早返回），保证 lifespan 重启时不崩。
    """
    global _scheduler
    if _scheduler is not None:
        logger.warning("[scheduler] 已经在运行，跳过启动")
        return

    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        # 包装一层：捕获异常不让 APScheduler 把任务标记为失败（见"知识点 5"）
        _safe_archive,
        trigger=CronTrigger(hour=2, minute=0),
        id="archive_sessions",
        name="归档 7 天前的 session 到 MySQL",
        replace_existing=True,
        # 多副本部署时，第一个抢到 MySQL 行的会成功，其他会因为 ON DUPLICATE KEY 不出错
        coalesce=True,  # 错过多次时合并成一次（见"知识点 3"）
        max_instances=1,  # 同一时刻只能跑一个实例（见"知识点 4"）
    )
    _scheduler.start()
    logger.info("[scheduler] 启动成功，归档任务已注册（每天 02:00）")


def stop_scheduler() -> None:
    """
    关闭调度器

    wait=False 表示不等待正在跑的任务完成——
    FastAPI 关闭时不能阻塞整个进程（lifespan yield 后就要退出）。
    """
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[scheduler] 已停止")
        _scheduler = None


async def _safe_archive() -> None:
    """
    归档任务包装：捕获异常避免 APScheduler 标记任务失败（见"知识点 5"）

    异常处理：归档失败不应影响下次调度。
    失败原因（Redis 不可用、MySQL 写失败）已经在 archive_old_sessions
    内部记录日志，这里只记"任务异常"的高层信号。
    """
    try:
        count = await archive_old_sessions(days_threshold=7)
        logger.info(f"[scheduler] 归档任务完成，归档 {count} 个 session")
    except Exception as e:
        # 已经记录过具体错误，这里只记一行
        logger.error(f"[scheduler] 归档任务异常: {e}")
