"""
会话存储 - L1 层

提供 get_history / add_message / clear_history 三个函数式 API
实现策略：Redis 为主，内存 dict 作为降级兜底（带 LRU 上限）

调用方（query_service.py）零改动

═══════════════════════════════════════════════════════════════════════
  核心知识点（设计决策的"为什么"）
═══════════════════════════════════════════════════════════════════════

【知识点 1：接口和实现分离 - 开闭原则】

  业务层依赖"接口语义"（get/add/clear 三个函数），不依赖"具体存储"。
  把 dict 操作作为可变实现，后续要换 Redis / MySQL / DynamoDB 都
  不会影响调用方。体现了"对扩展开放、对修改关闭"。

  面试讲法：
  "我先定义了 3 个稳定 API，dict 是临时实现。换 Redis 时只改
  session_store.py 内部，query_service.py 一行代码不动。"

【知识点 2：asyncio.Lock vs threading.Lock】

  FastAPI 是单线程 async 框架，事件循环里跑成百上千个协程。
  - threading.Lock 在协程里 acquire() 会**阻塞整个事件循环**，
    其他请求全部卡住（看到 P99 从 50ms 涨到 5000ms）。
  - asyncio.Lock 让当前协程 await yield，其他协程照常跑，
    只是这一个 session_id 的写操作被串行化。

  【坑】asyncio.Lock 必须在 async 函数里用。_memory_add 因此改成 async。

【知识点 3：LRU 淘汰用 dict.popitem(last=False)】

  Python 3.7+ dict 保证按插入顺序迭代，popitem(last=False) 弹出
  最早插入的键值对。等价于"淘汰最久未访问的 session"。
  生产严格 LRU 需要 OrderedDict + move_to_end()，本项目规模够用简化版。

【知识点 4：Redis Pipeline 原子性】

  redis-py 的 pipeline() 把多个命令打包成一次 TCP 往返。
  - 不用 pipeline：3 次往返 = 3 × RTT 延迟
  - 用 pipeline：1 次往返 = 1 × RTT 延迟
  - 中间网络断开时，pipeline 内命令**全部回滚**（MULTI/EXEC 语义）

【知识点 5：LTRIM 负数索引的"反直觉"语义】

  LTRIM key start stop 保留 [start, stop] 区间。
  - LTRIM key 0 9   → 保留前 10 个（删后面的）
  - LTRIM key -10 -1 → 保留最后 10 个（删前面的）★ 本项目用这个
  - LTRIM key 0 -1  → 全保留

  【易错点】stop=-1 不是"删到尾部"，是"保到尾部"。下次有人改成
  ltrim(key, -10, -2) 会丢最新一条。

【知识点 6：降级策略 - "丢历史不丢接口可用"】

  Redis 挂了之后整个会话系统直接 500，会让用户追问失效。
  业内通用做法：业务层快速失败、内存兜底，**接口保证可用**。
  代价是降级期间新写的 session 只在内存里、Redis 恢复后会丢。
  这叫"弱一致"——会话场景接受，业务关键场景不接受。

【知识点 7：WriteMode 三阶段平滑迁移】

  - MEMORY_ONLY：阶段 0，纯内存（改造前状态，向后兼容）
  - DUAL_WRITE：阶段 1，内存 + Redis 都写（验证期）
  - REDIS_PRIMARY：阶段 2，Redis 为主，内存仅作降级兜底

  通过环境变量切换，不用重新部署代码。3 个阶段的好处：
  1. 阶段 1 期间业务能验证 Redis 真的能用
  2. 任意阶段出问题可以快速回滚（改环境变量）
  3. 阶段 2 之后内存 dict 真的"只是兜底"——平时不增长
"""
import asyncio
import json
import os
from enum import Enum
from typing import Dict, List

from app.clients.redis_client_manager import redis_client_manager
from app.core.log import logger
from app.conf.app_config import app_config

redis_cfg = app_config.redis_cfg


# ═══════════════════════════════════════════════════════════════════════
# 阶段切换：控制 session 写入策略（见"知识点 7"）
# ═══════════════════════════════════════════════════════════════════════
class WriteMode(Enum):
    """会话写入模式 - 用于平滑迁移"""

    MEMORY_ONLY = "memory_only"        # 阶段 0：纯内存（改造前）
    DUAL_WRITE = "dual_write"           # 阶段 1：双写（内存 + Redis）
    REDIS_PRIMARY = "redis_primary"     # 阶段 2：Redis 为主，内存仅作降级兜底
    # 注：阶段 3（代码移除）= 物理上删掉 _memory_fallback 相关代码


# 通过环境变量切换阶段，默认纯内存（向后兼容）
WRITE_MODE = WriteMode(os.getenv("SESSION_WRITE_MODE", "memory_only"))


# ═══════════════════════════════════════════════════════════════════════
# 内存兜底层（见"知识点 2"+"知识点 3"）
# 当 Redis 不可用时，所有读写都退化到这里
# 用 asyncio.Lock 而不是 threading.Lock（事件循环友好）
# 加上 LRU 上限（max_memory_sessions）防止内存爆炸
# ═══════════════════════════════════════════════════════════════════════
_memory_fallback: Dict[str, List[Dict]] = {}
_memory_lock = asyncio.Lock()


async def _memory_add(session_id: str, role: str, content: str) -> None:
    """
    内存版添加消息（async lock + 长度截断 + 数量截断 + LRU 淘汰）

    4 个动作按顺序执行：
    1. 获取 asyncio.Lock（不让其他协程插队）
    2. LRU 淘汰：如果 session 数量超限，弹最早的 session
    3. 截断 content 到 500 字符（防单条消息过大）
    4. 截断列表到最近 10 条（防单 session 消息过多）
    """
    async with _memory_lock:
        # LRU 淘汰：session 已存在则不算新增；新 session 且超限时，淘汰最旧的
        # Python 3.7+ dict 保插入顺序，popitem(last=False) 弹最早的
        if (
            session_id not in _memory_fallback
            and len(_memory_fallback) >= redis_cfg.max_memory_sessions
        ):
            oldest_id, _ = _memory_fallback.popitem(last=False)
            logger.debug(
                f"[session_store] 内存 dict 达上限 {redis_cfg.max_memory_sessions}，"
                f"淘汰最旧 session: {oldest_id}"
            )

        if session_id not in _memory_fallback:
            _memory_fallback[session_id] = []
        # 单条消息截断到 500 字符（防单条消息撑爆内存）
        truncated = content[:500] if len(content) > 500 else content
        _memory_fallback[session_id].append({"role": role, "content": truncated})
        # 只保留最近 10 条（防 session 列表无限增长）
        _memory_fallback[session_id] = _memory_fallback[session_id][-10:]


def _memory_get(session_id: str, max_count: int) -> list:
    """
    内存版获取历史（只读，不需要锁）

    【为什么不需要锁】dict.get() 是原子操作，不会出现"读到一半的 list"
    这种中间状态（list 本身在 CPython 里是引用计数+GIL 保护的）。
    """
    history = _memory_fallback.get(session_id, [])
    return history[-max_count:]


# ═══════════════════════════════════════════════════════════════════════
# Redis 主路径（见"知识点 4"+"知识点 5"）
# ═══════════════════════════════════════════════════════════════════════
def _key(session_id: str) -> str:
    """生成 Redis key，统一前缀方便管理（如 SCAN session:* 一次性查所有）"""
    return f"{redis_cfg.key_prefix}{session_id}"


async def _redis_add(client, session_id: str, role: str, content: str) -> None:
    """
    Redis 版添加消息（pipeline 一次往返，原子性更好）

    三个命令打包发送：
    1. RPUSH  追加新消息到 list 尾部
    2. LTRIM  保留最后 10 条（负数索引见"知识点 5"）
    3. EXPIRE 重置 TTL 24 小时（每次写入刷新过期时间）
    """
    truncated = content[:500] if len(content) > 500 else content
    msg = json.dumps({"role": role, "content": truncated}, ensure_ascii=False)
    key = _key(session_id)

    pipe = client.pipeline()
    pipe.rpush(key, msg)
    # LTRIM key start stop：保留 [start, stop] 区间
    # 负数索引从尾部数：-10..-1 表示"从倒数第 10 个到最后一个"——只保留最近 10 条
    # 【易错】stop=-1 不是"删到 -1"，是"保到 -1"，跟直觉相反；详见 Redis 文档
    pipe.ltrim(key, -10, -1)
    pipe.expire(key, redis_cfg.default_ttl_seconds)
    await pipe.execute()


# ═══════════════════════════════════════════════════════════════════════
# 公开 API - 调用方使用（"接口和实现分离"，见"知识点 1"）
# ═══════════════════════════════════════════════════════════════════════
async def get_history(session_id: str, max_count: int = 5) -> list:
    """
    获取某会话的历史记录

    Args:
        session_id: 会话 ID
        max_count: 最多返回几条

    Returns:
        消息列表，每个元素是 {"role": ..., "content": ...} 字典
        出错时返回空列表（自动降级到内存）

    降级链路（见"知识点 6"）：
      Redis 可用 → lrange 读最新 max_count 条
      Redis 不可用 → 走 _memory_get
      Redis 抛异常 → mark_unavailable + 走 _memory_get
    """
    client = await redis_client_manager.get_client()

    if client is None:
        return _memory_get(session_id, max_count)

    try:
        raw = await client.lrange(_key(session_id), -max_count, -1)
        # 读取成功 → 标记 Redis 可用（重置失败计数）
        redis_client_manager.mark_available()
        return [json.loads(item) for item in raw]
    except Exception as e:
        logger.warning(f"[session_store] Redis 读取失败，降级到内存: {e}")
        # 读取失败 → 标记 Redis 不可用（连续 N 次才真正标记，防抖动）
        redis_client_manager.mark_unavailable()
        return _memory_get(session_id, max_count)


async def add_message(session_id: str, role: str, content: str) -> None:
    """
    追加一条消息到会话历史

    根据 WRITE_MODE 决定写入策略（见"知识点 7"）：
    - MEMORY_ONLY: 只写内存（阶段 0，向后兼容）
    - DUAL_WRITE: 内存 + Redis 都写（阶段 1，验证期）
    - REDIS_PRIMARY: Redis 为主，失败时降级到内存（阶段 2）

    设计取舍：
    - 双写期（阶段 1）保留双写是为了容灾——Redis 写失败时内存里还有
    - 主写期（阶段 2）成功后还写一份内存，是为了让"Redis 短暂挂掉时"
      内存里至少有最近一次成功的内容（不强一致，但用户体感好）
    """
    if WRITE_MODE == WriteMode.MEMORY_ONLY:
        # 阶段 0：只写内存（改造前兼容）
        await _memory_add(session_id, role, content)
        return

    if WRITE_MODE == WriteMode.DUAL_WRITE:
        # 阶段 1：双写（先内存后 Redis，Redis 失败不影响内存）
        await _memory_add(session_id, role, content)
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
                await _memory_add(session_id, role, content)
                return
            except Exception as e:
                logger.warning(f"[session_store] Redis 写入失败，降级到内存: {e}")
                redis_client_manager.mark_unavailable()

        # Redis 不可用时直接走内存
        await _memory_add(session_id, role, content)
        return


async def clear_history(session_id: str) -> None:
    """
    清空某会话历史

    同时清两个地方：内存 dict + Redis key。
    Redis 失败不影响主流程——内存已经清掉了，Redis 多留一会无所谓。
    """
    async with _memory_lock:
        _memory_fallback.pop(session_id, None)

    client = await redis_client_manager.get_client()
    if client is not None:
        try:
            await client.delete(_key(session_id))
        except Exception as e:
            logger.warning(f"[session_store] Redis 删除失败（不影响主流程）: {e}")
