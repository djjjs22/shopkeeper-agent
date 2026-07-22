"""
失败 case 自动归集器（数据飞轮的核心信号源）

对应 docs/AI应用架构升级路线.md 第 5.5 节"Bad Case 自动归集"。

设计要点：
1. **模块级单例**：节点层零改动依赖注入，直接 `from app.services.bad_case_collector
   import bad_case_collector` 后调用 `.record(...)`
2. **去重**：同 (query 前 100 字, error_type) 30 秒内只记一次。validate_sql /
   correct_sql / reviewer 三处都可能埋点，同一次失败不该灌 3 条进表
3. **后台执行**：record() 内部用 asyncio.create_task 包裹真实写库逻辑，
   调用方不需要 await——失败 case 归集是旁路，绝不阻塞主查询链路
4. **fail-open**：归集本身失败只 log warning，不影响业务（飞轮挂了不能拖垮问数）

信号源（埋点位置）：
   - validate_sql：explain 失败 → error_type="sql_fail"
   - correct_sql：校正失败 / LLM 放弃治疗 → error_type="sql_fail"
   - reviewer：confidence < 0.5 → error_type="review_low"
   - feedback 端点：用户 👎 → error_type="user_thumb_down"
   - （v2）改问信号：用户 30s 内改问同意图 → error_type="rewrite_signal"
"""

import asyncio
import hashlib
import time
from typing import Optional

from app.core.log import logger


# 去重窗口：同 (query, error_type) 在这个秒数内只记一次
_DEDUP_WINDOW_SECONDS = 30


class BadCaseCollector:
    """失败 case 归集器（模块级单例，见模块 docstring）"""

    def __init__(self) -> None:
        # 去重缓存：(query_hash, error_type) → 上次记录的 monotonic 时间戳
        self._dedup_cache: dict[tuple[str, str], float] = {}
        # 防止 create_task 在没有事件循环时崩溃（脚本入口 / 测试）
        self._enabled = True

    def disable(self) -> None:
        """测试用：关闭归集（避免单测往真实 DB 写脏数据）"""
        self._enabled = False

    def enable(self) -> None:
        self._enabled = True

    def record(
        self,
        query: str,
        sql: str,
        error_type: str,
        detail: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        """记录一条失败 case（fire-and-forget，不阻塞调用方）

        不会抛异常——归集失败只 log warning。
        在节点里这样调用：
            bad_case_collector.record(
                query=state["query"], sql=state["sql"],
                error_type="sql_fail", detail=str(e),
            )
        """
        if not self._enabled:
            return
        if not query:
            return  # 没 query 没法归因，跳过

        # 去重：同 query + 同 error_type 30s 内只记一次
        query_hash = hashlib.md5(query[:100].encode("utf-8")).hexdigest()[:16]
        dedup_key = (query_hash, error_type)
        now = time.monotonic()
        last_ts = self._dedup_cache.get(dedup_key)
        if last_ts is not None and (now - last_ts) < _DEDUP_WINDOW_SECONDS:
            return  # 窗口内已记过，跳过
        self._dedup_cache[dedup_key] = now

        # 顺带清理过期去重条目（避免缓存无限增长）
        if len(self._dedup_cache) > 1000:
            cutoff = now - _DEDUP_WINDOW_SECONDS
            self._dedup_cache = {
                k: v for k, v in self._dedup_cache.items() if v > cutoff
            }

        # 后台执行写库（不阻塞节点返回）
        try:
            asyncio.create_task(
                self._write_to_db(query, sql, error_type, detail, session_id)
            )
        except RuntimeError:
            # 没有事件循环（脚本同步调用）→ 同步降级写
            logger.debug("[bad_case] 无事件循环，跳过归集（非主链路）")

    async def _write_to_db(
        self,
        query: str,
        sql: str,
        error_type: str,
        detail: Optional[str],
        session_id: Optional[str],
    ) -> None:
        """真实写库逻辑（被 create_task 后台调用）"""
        try:
            from app.entities.bad_case import BadCase
            from app.clients.mysql_client_manager import meta_mysql_client_manager

            meta_mysql_client_manager.init()
            async with meta_mysql_client_manager.session_factory() as session:
                from app.repositories.mysql.meta.meta_mysql_repository import (
                    MetaMySQLRepository,
                )

                repo = MetaMySQLRepository(session)
                repo.save_bad_case(
                    BadCase(
                        query=query,
                        sql=sql or "",
                        error_type=error_type,
                        detail=detail,
                        session_id=session_id,
                    )
                )
                await session.commit()
            logger.debug(
                f"[bad_case] 归集成功: type={error_type} query={query[:40]}..."
            )
        except Exception as e:
            # fail-open：归集失败不影响业务
            logger.warning(f"[bad_case] 归集失败（不影响业务）: {e}")


# 模块级单例
bad_case_collector = BadCaseCollector()
