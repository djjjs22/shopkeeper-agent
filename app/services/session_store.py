"""
会话存储 - L1 层

提供 get_history / add_message / clear_history 三个函数式 API
实现策略：Redis 为主，内存 dict 作为降级兜底

调用方（query_service.py）零改动
"""

import json
import os
import threading
from enum import Enum
from typing import Dict, List

from loguru import logger

from app.clients.redis_client_manager import redis_client_manager
from app.conf.app_config import app_config

redis_cfg = app_config.redis_cfg


# ════════════════════════════════════════════════════
# 阶段切换：控制 session 写入策略
# ════════════════════════════════════════════════════
class WriteMode(Enum):
    """会话写入模式 - 用于平滑迁移"""

    MEMORY_ONLY = "memory_only"        # 阶段 0：纯内存（改造前）
    DUAL_WRITE = "dual_write"           # 阶段 1：双写（内存 + Redis）
    REDIS_PRIMARY = "redis_primary"     # 阶段 2：Redis 为主，内存仅作降级兜底
    # 注：阶段 3（代码移除）= 物理上删掉 _memory_fallback 相关代码


# 通过环境变量切换阶段，默认纯内存（向后兼容）
WRITE_MODE = WriteMode(os.getenv("SESSION_WRITE_MODE", "memory_only"))


# ════════════════════════════════════════════════════
# 内存兜底层
# 当 Redis 不可用时，所有读写都退化到这里
# ════════════════════════════════════════════════════
_memory_fallback: Dict[str, List[Dict]] = {}
_memory_lock = threading.Lock()


def _memory_add(session_id: str, role: str, content: str) -> None:
    """内存版添加消息（线程安全 + 长度截断 + 数量截断）"""
    with _memory_lock:
        if session_id not in _memory_fallback:
            _memory_fallback[session_id] = []
        # 单条消息截断到 500 字符
        truncated = content[:500] if len(content) > 500 else content
        _memory_fallback[session_id].append({"role": role, "content": truncated})
        # 只保留最近 10 条
        _memory_fallback[session_id] = _memory_fallback[session_id][-10:]


def _memory_get(session_id: str, max_count: int) -> list:
    """内存版获取历史"""
    history = _memory_fallback.get(session_id, [])
    return history[-max_count:]


# ════════════════════════════════════════════════════
# Redis 主路径
# ════════════════════════════════════════════════════
def _key(session_id: str) -> str:
    """生成 Redis key，统一前缀方便管理"""
    return f"{redis_cfg.key_prefix}{session_id}"


async def _redis_add(client, session_id: str, role: str, content: str) -> None:
    """Redis 版添加消息（pipeline 原子性更好）"""
    truncated = content[:500] if len(content) > 500 else content
    msg = json.dumps({"role": role, "content": truncated}, ensure_ascii=False)
    key = _key(session_id)

    pipe = client.pipeline()
    pipe.rpush(key, msg)
    pipe.ltrim(key, -10, -1)
    pipe.expire(key, redis_cfg.default_ttl_seconds)
    await pipe.execute()


# ════════════════════════════════════════════════════
# 公开 API - 调用方使用
# ════════════════════════════════════════════════════
async def get_history(session_id: str, max_count: int = 5) -> list:
    """
    获取某会话的历史记录

    Args:
        session_id: 会话 ID
        max_count: 最多返回几条

    Returns:
        消息列表，每个元素是 {"role": ..., "content": ...} 字典
        出错时返回空列表（自动降级）
    """
    client = await redis_client_manager.get_client()

    if client is None:
        return _memory_get(session_id, max_count)

    try:
        raw = await client.lrange(_key(session_id), -max_count, -1)
        redis_client_manager.mark_available()
        return [json.loads(item) for item in raw]
    except Exception as e:
        logger.warning(f"[session_store] Redis 读取失败，降级到内存: {e}")
        redis_client_manager.mark_unavailable()
        return _memory_get(session_id, max_count)


async def add_message(session_id: str, role: str, content: str) -> None:
    """
    追加一条消息到会话历史

    根据 WRITE_MODE 决定写入策略：
    - MEMORY_ONLY: 只写内存
    - DUAL_WRITE: 内存 + Redis 都写
    - REDIS_PRIMARY: Redis 为主，失败时降级到内存
    """
    if WRITE_MODE == WriteMode.MEMORY_ONLY:
        # 阶段 0：只写内存（改造前兼容）
        _memory_add(session_id, role, content)
        return

    if WRITE_MODE == WriteMode.DUAL_WRITE:
        # 阶段 1：双写（先内存后 Redis，Redis 失败不影响内存）
        _memory_add(session_id, role, content)
        client = await redis_client_manager.get_client()
        if client is not None:
            try:
                await _redis_add(client, session_id, role, content)
                redis_client_manager.mark_available()
            except Exception as e:
                logger.warning(f"[session_store] Redis 写入失败（双写期）: {e}")
                redis_client_manager.mark_unavailable()
        return

    if WRITE_MODE == WriteMode.REDIS_PRIMARY:
        # 阶段 2：Redis 为主，但写失败时降级到内存
        client = await redis_client_manager.get_client()
        if client is not None:
            try:
                await _redis_add(client, session_id, role, content)
                redis_client_manager.mark_available()
                # Redis 成功时也写一份到内存（降级兜底）
                _memory_add(session_id, role, content)
                return
            except Exception as e:
                logger.warning(f"[session_store] Redis 写入失败，降级到内存: {e}")
                redis_client_manager.mark_unavailable()

        # Redis 不可用时直接走内存
        _memory_add(session_id, role, content)
        return


async def clear_history(session_id: str) -> None:
    """清空某会话历史"""
    with _memory_lock:
        _memory_fallback.pop(session_id, None)

    client = await redis_client_manager.get_client()
    if client is not None:
        try:
            await client.delete(_key(session_id))
        except Exception as e:
            logger.warning(f"[session_store] Redis 删除失败（不影响主流程）: {e}")
