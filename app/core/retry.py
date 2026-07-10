"""
轻量级异步重试工具

用于 Qdrant / ES / Embedding 等外部依赖的瞬时故障重试。
典型场景：依赖容器重启后，客户端连接池失效，首个请求抛 ConnectionError，
重试一次往往就能恢复（刀 13）。
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.core.log import logger

T = TypeVar("T")


async def retry_once(
    coro_factory: Callable[[], Awaitable[T]],
    label: str,
    max_retries: int = 1,
) -> T:
    """执行异步操作，失败时按次数重试

    Args:
        coro_factory: 返回协程的工厂函数（每次重试都重新创建协程，避免协程已消费）
        label: 日志标识，便于定位是哪次操作在重试
        max_retries: 最大重试次数（不含首次执行）

    Raises:
        最后一次重试仍失败时，抛出原始异常
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except Exception as exc:  # noqa: BLE001 - 重试需要捕获所有异常
            last_exc = exc
            if attempt < max_retries:
                logger.warning(
                    f"[retry_once] {label} 第 {attempt + 1} 次失败，准备重试: {exc}"
                )
                # 短暂退避，避免对刚恢复的依赖服务造成冲击
                await asyncio.sleep(0.1)
            else:
                logger.error(
                    f"[retry_once] {label} 重试 {max_retries} 次后仍失败: {exc}"
                )
    assert last_exc is not None
    raise last_exc
