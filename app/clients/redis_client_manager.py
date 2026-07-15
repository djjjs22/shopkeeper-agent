"""
Redis 客户端管理器

负责在应用启动时建立 Redis 连接池、运行时提供连接、并维护可用性状态
当 Redis 不可用时通过 _available 标志让业务层走内存降级路径
后台协程每 30s 主动 PING，发现 Redis 恢复时自动 mark_available

使用方式：
    from app.clients.redis_client_manager import redis_client_manager
    client = await redis_client_manager.get_client()
    if client is None:
        # 走降级路径
    else:
        # 正常使用 Redis

═══════════════════════════════════════════════════════════════════════
  核心知识点（设计决策的"为什么"）
═══════════════════════════════════════════════════════════════════════

【知识点 1：单例模式 - 模块级全局对象】

  redis_client_manager 在文件底部实例化，整个应用只有一个。
  - 节省连接池内存（不每次新建 redis_async.Redis）
  - 状态统一（_available、_fail_count 在所有请求间共享）
  - 测试时可重置 _client / _available（mock 测试友好）

  缺点：单例难做依赖注入测试——但本项目 FastAPI lifespan 管理生命周期，
  业务代码直接 import 单例即可。

【知识点 2：同步 init + 异步 start 的分离】

  init() 是同步的（跟项目其他 manager 一致），只创建客户端实例。
  start() 是异步的，负责启动探活协程。

  为什么分开？
  - 项目里 qdrant_client_manager、es_client_manager 等都是同步 init
  - 探活协程本质是 async task，需要 event loop
  - 同步 init 在 lifespan 里调用方便，async start 在 yield 之前单独 await

【知识点 3：asyncio.create_task 创建后台协程】

  asyncio.create_task(coro) 把协程"挂到后台"立刻返回 Task 对象。
  - 不 await 也不会阻塞主流程
  - Task 对象保存在 self._probe_task 用于后续管理
  - 必须显式 cancel() 否则协程会一直跑下去

  【易错】漏 cancel 会在 close() 时留下 zombie 协程，协程持有
  已关闭的 client 会在下次 await 时抛异常。

【知识点 4：探活"只在不可用时"触发 - 优化策略】

  if not self._available 才 PING，避免无意义的健康检查流量。
  - Redis 健康时：mark_available() 已经在业务成功路径里调过了
  - Redis 不可用时：业务调用失败 → mark_unavailable() → 探活协程开始工作
  - Redis 恢复时：探活 PING 成功 → mark_available() → 业务恢复走 Redis

  这种"被动 + 主动"的混合模式是工业级健康检查的常见做法。

【知识点 5：CancelledError 必须 re-raise】

  asyncio.CancelledError 是协程被 cancel() 时抛出的特殊异常。
  - Python 3.8+ 推荐做法：捕获后**重新 raise**，否则 asyncio 会打 warning
  - 业务代码应该捕获 Exception 但让 CancelledError 透传

  ```python
  try:
      while True:
          await asyncio.sleep(30)
  except asyncio.CancelledError:
      logger.info("协程取消")
      raise  # ← 必须 raise，否则协程被认为正常完成
  ```

【知识点 6：失败计数 + 阈值 = 防抖动】

  连续失败 N 次才标记 Redis 不可用（fail_threshold=3）。
  避免"网络抖一下就降级、抖完又恢复"的循环。

  经典场景：
  - t=0 业务调用失败 → _fail_count=1, _available=True
  - t=1 业务调用失败 → _fail_count=2, _available=True
  - t=2 业务调用失败 → _fail_count=3, _available=False（真正降级）
  - t=3 业务调用成功 → _fail_count=0, _available=True（恢复）

  工业级阈值通常是 3-5 次。太小容易误降级，太大不敏感。

【知识点 7：连接池 max_connections=20】

  redis-py 默认 max_connections 是 2^31，实际是无限的。
  项目里设 20 是因为：
  - FastAPI 异步 + 每次请求都要用连接，太多会耗尽 fd
  - 太少会形成请求队列（head-of-line blocking）
  - 20 是经验值，配合连接复用刚好够单实例用

  生产大流量场景要按 QPS 估算：max_connections ≥ QPS × avg_latency。
"""
import asyncio
from typing import Optional

import redis.asyncio as redis_async

from app.conf.app_config import app_config
from app.core.log import logger

redis_cfg = app_config.redis_cfg


class RedisClientManager:
    """Redis 客户端单例 + 可用性状态管理 + 后台探活"""

    def __init__(self):
        # redis-py 客户端实例（懒初始化，在 init() 里创建）
        self._client: Optional[redis_async.Redis] = None
        # 当前是否可用（False 时业务层走内存降级）
        self._available: bool = False
        # 连续失败计数（达到阈值才标记不可用）
        self._fail_count: int = 0
        # 后台探活协程的 Task 句柄（用于 cancel）
        self._probe_task: Optional[asyncio.Task] = None

    def init(self) -> None:
        """
        应用启动时建立连接池，失败不抛异常（允许降级启动）

        【注意】init 是同步的，只创建客户端实例。
        连接验证（ping）放到第一次业务调用或 start() 里。
        同步 init 跟项目其他客户端管理器风格保持一致。
        """
        try:
            self._client = redis_async.from_url(
                redis_cfg.url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=redis_cfg.socket_timeout,
                socket_timeout=redis_cfg.socket_timeout,
                max_connections=redis_cfg.max_connections,
            )
            # ping 是异步的，但 lifespan 中是同步初始化阶段
            # 这里只能创建 client，连接验证放到第一次调用时
            self._available = True
            self._fail_count = 0
            logger.info(f"[Redis] 客户端已创建: {redis_cfg.url}")
        except Exception as e:
            self._available = False
            logger.warning(
                f"[Redis] 启动时连接失败: {e}，将以降级模式（内存 dict）运行"
            )

    async def start(self) -> None:
        """
        异步启动：启动后台探活协程

        在 lifespan 中由 init() 之后调用。即使 Redis 暂时不可用，
        探活协程也会持续尝试 PING（自我恢复机制）。
        """
        # 启动探活协程（即使 init 失败也启动，让它持续重试）
        if self._probe_task is None or self._probe_task.done():
            self._probe_task = asyncio.create_task(self._probe_loop())
            logger.info(
                f"[Redis] 探活协程已启动（间隔 {redis_cfg.probe_interval_seconds}s）"
            )

    async def _probe_loop(self) -> None:
        """
        后台探活协程：每 30s PING 一次

        设计意图：业务调用可能在 Redis 恢复后没有调用（极端情况：用户只读不写），
        这时候 mark_available 永远不会被触发。后台主动 PING 是兜底。

        【生命周期】
        - 创建：start() 里 asyncio.create_task
        - 取消：close() 里 task.cancel() → CancelledError → 协程退出
        - 异常：业务调用失败 → mark_unavailable → 下次探活尝试恢复
        """
        try:
            while True:
                await asyncio.sleep(redis_cfg.probe_interval_seconds)
                # 只在标记为不可用时才主动探活（避免无意义的 PING）
                if self._client is not None and not self._available:
                    try:
                        await self._client.ping()
                        # PING 成功 → 标记可用（重置失败计数）
                        self.mark_available()
                        logger.info("[Redis] 探活成功，恢复可用")
                    except Exception as e:
                        # 探活失败不需要处理，业务调用会自己 mark_unavailable
                        logger.debug(f"[Redis] 探活失败: {e}")
        except asyncio.CancelledError:
            # 【知识点 5】CancelledError 必须重新 raise
            logger.info("[Redis] 探活协程已取消")
            raise

    async def close(self) -> None:
        """
        应用关闭时释放探活协程 + 连接池

        关闭顺序很重要：
        1. 先 cancel 探活协程（否则协程持有已关闭的 client 会报错）
        2. 再 close redis 连接池

        await self._probe_task 等待 cancel 生效（捕获 CancelledError 防 warning）。
        """
        # 第一步：取消后台探活协程
        if self._probe_task is not None and not self._probe_task.done():
            self._probe_task.cancel()
            try:
                await self._probe_task
            except asyncio.CancelledError:
                # 正常情况：cancel 一定会抛 CancelledError，吞掉它
                pass
            self._probe_task = None

        # 第二步：关闭 redis 连接池
        if self._client is not None:
            try:
                await self._client.close()
                logger.info("[Redis] 连接已关闭")
            except Exception as e:
                logger.warning(f"[Redis] 关闭连接时异常: {e}")

    async def get_client(self) -> Optional[redis_async.Redis]:
        """
        获取 Redis 客户端

        Returns:
            redis_async.Redis 实例（可用时）
            None（不可用时，业务层走内存降级路径）
        """
        if not self._available or self._client is None:
            return None
        return self._client

    def mark_unavailable(self) -> None:
        """
        业务调用 Redis 失败时调用

        连续失败 N 次才标记 Redis 不可用（避免网络抖动误降级，见"知识点 6"）。
        """
        self._fail_count += 1
        threshold = redis_cfg.fail_threshold
        if self._fail_count >= threshold and self._available:
            self._available = False
            logger.warning(
                f"[Redis] 连续失败 {self._fail_count} 次（阈值 {threshold}），"
                f"标记为不可用，自动降级到内存"
            )

    def mark_available(self) -> None:
        """
        业务调用 Redis 成功时调用

        重置失败计数 + 恢复可用标志。
        如果之前是 False，会打印"恢复可用"日志（用于排查）。
        """
        if not self._available:
            logger.info("[Redis] 恢复可用")
        self._available = True
        self._fail_count = 0


# 全局单例（见"知识点 1"）
redis_client_manager = RedisClientManager()
