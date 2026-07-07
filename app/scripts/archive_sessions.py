"""
会话冷数据归档脚本

每天凌晨 02:00 把 Redis 里 7 天前的 session 迁移到 MySQL 的 session_archive 表
解决"7 天热数据 + 30 天冷数据"的数据分层需求

运行方式：
  cd D:/shopkeeper-agent
  uv run python -m app.scripts.archive_sessions --days 7
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta

from loguru import logger

from app.clients.mysql_client_manager import meta_mysql_client_manager
from app.clients.redis_client_manager import redis_client_manager


async def archive_old_sessions(days_threshold: int = 7) -> int:
    """
    归档 N 天前的 session 到 MySQL

    Args:
        days_threshold: 超过 N 天的 session 被归档

    Returns:
        归档的 session 数量
    """
    redis = await redis_client_manager.get_client()
    if redis is None:
        logger.warning("[归档任务] Redis 不可用，跳过本次归档")
        return 0

    cutoff = datetime.now() - timedelta(days=days_threshold)
    archived = 0
    skipped = 0

    # SCAN 比 KEYS 安全（不会阻塞 Redis）
    async for key in redis.scan_iter(match="session:*"):
        # 检查 TTL：剩余 TTL > 24h 的都是 7 天内创建的，不归档
        ttl = await redis.ttl(key)
        if ttl > 86400:  # TTL > 24h，说明还没到 7 天
            skipped += 1
            continue

        # 读取历史
        raw = await redis.lrange(key, 0, -1)
        if not raw:
            await redis.delete(key)
            continue

        # 解析 session_id
        session_id = key.split(":", 1)[1] if ":" in key else key
        messages = [json.loads(m) for m in raw]

        # 写入 MySQL
        try:
            async with meta_mysql_client_manager.session_factory() as session:
                from sqlalchemy import text
                # ON DUPLICATE KEY UPDATE 防止重复归档
                await session.execute(
                    text(
                        "INSERT INTO session_archive (session_id, messages, archived_at) "
                        "VALUES (:sid, :msgs, :at) "
                        "ON DUPLICATE KEY UPDATE messages = :msgs, archived_at = :at"
                    ),
                    {
                        "sid": session_id,
                        "msgs": json.dumps(messages, ensure_ascii=False),
                        "at": datetime.now(),
                    },
                )
                await session.commit()

            # 从 Redis 删除（已迁移到 MySQL）
            await redis.delete(key)
            archived += 1

        except Exception as e:
            logger.error(f"[归档任务] session {session_id} 归档失败: {e}")
            continue

    logger.info(
        f"[归档任务] 完成: 归档 {archived} 个, 跳过 {skipped} 个, "
        f"cutoff={cutoff.isoformat()}"
    )
    return archived


async def main_async(days_threshold: int) -> int:
    """异步主函数"""
    # 初始化客户端
    redis_client_manager.init()
    meta_mysql_client_manager.init()

    try:
        return await archive_old_sessions(days_threshold)
    finally:
        await redis_client_manager.close()
        await meta_mysql_client_manager.close()


def main():
    parser = argparse.ArgumentParser(description="归档旧 session 到 MySQL")
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="归档多少天前的 session（默认 7）",
    )
    args = parser.parse_args()

    archived = asyncio.run(main_async(args.days))
    print(f"归档完成: {archived} 个 session")
    return 0 if archived >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
