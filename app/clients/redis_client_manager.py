"""
Redis 客户端管理器

负责在应用启动时建立 Redis 连接池、运行时提供连接、并维护可用性状态
当 Redis 不可用时通过 _available 标志让业务层走内存降级路径

使用方式：
    from app.clients.redis_client_manager import redis_client_manager
    client = await redis_client_manager.get_client()
    if client is None:
        # 走降级路径
    else:
        # 正常使用 Redis
"""

from typing import Optional

import redis.asyncio as redis_async
from loguru import logger

from app.conf.app_config import app_config

redis_cfg = app_config.redis_cfg

class RedisClientManager:
    """Redis 客户端单例 + 可用性状态管理"""

    def __init__(self):
        self._client: Optional[redis_async.Redis] = None
        self._available: bool = False
        self._fail_count: int = 0

    def init(self) -> None:
        """应用启动时建立连接池，失败不抛异常（允许降级启动）"""
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

    async def close(self) -> None:
        """应用关闭时释放连接池"""
        if self._client is not None:
            try:
                await self._client.close()
                logger.info("[Redis] 连接已关闭")
            except Exception as e:
                logger.warning(f"[Redis] 关闭连接时异常: {e}")

    async def get_client(self) -> Optional[redis_async.Redis]:
        """获取 Redis 客户端（None 表示当前不可用）"""
        if not self._available or self._client is None:
            return None
        return self._client

    def mark_unavailable(self) -> None:
        """业务调用 Redis 失败时调用，连续失败 N 次才标记不可用（避免抖动）"""
        self._fail_count += 1
        threshold = redis_cfg.fail_threshold
        if self._fail_count >= threshold and self._available:
            self._available = False
            logger.warning(
                f"[Redis] 连续失败 {self._fail_count} 次（阈值 {threshold}），"
                f"标记为不可用，自动降级到内存"
            )

    def mark_available(self) -> None:
        """业务调用 Redis 成功时调用（重置失败计数 + 恢复可用标志）"""
        if not self._available:
            logger.info("[Redis] 恢复可用")
        self._available = True
        self._fail_count = 0


# 全局单例
redis_client_manager = RedisClientManager()
