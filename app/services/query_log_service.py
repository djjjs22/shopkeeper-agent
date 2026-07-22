"""
查询日志服务（数据飞轮的"成功信号"源）

对应 docs/AI应用架构升级路线.md 第 5.5 节。

与 bad_case_collector 的分工：
   - bad_case_collector：在节点层调用（validate/correct/reviewer），记失败信号
   - query_log_service：在 query_service 层调用（那里有 session_id + latency + success），
     记每次查询的完整执行记录

消费方：
   - pattern_bank_service：扫描 query_log where success=1，把成功 SQL 抽成模板
   - 周报：统计成功率、p95 latency、按 intent 分组

设计要点：
1. **query_service 层调用**：那里能拿到 session_id / latency / 最终 SQL / success
2. **后台执行**：同 bad_case_collector，asyncio.create_task 包裹，不阻塞 SSE 响应
3. **fail-open**：记录失败只 log warning
"""

import asyncio
from typing import Optional

from app.core.log import logger


class QueryLogService:
    """查询日志服务（模块级单例）"""

    def __init__(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    def enable(self) -> None:
        self._enabled = True

    def record(
        self,
        session_id: str,
        query: str,
        sql: str,
        success: bool,
        latency_ms: Optional[float] = None,
        reviewer_score: Optional[float] = None,
        intent: Optional[str] = None,
    ) -> None:
        """记录一次查询日志（fire-and-forget）

        在 query_service.py 成功拿到结果后调用：
            query_log_service.record(
                session_id=session_id, query=query, sql=sql,
                success=True, latency_ms=elapsed_ms,
            )
        """
        if not self._enabled or not query:
            return
        try:
            asyncio.create_task(
                self._write_to_db(
                    session_id, query, sql, success, latency_ms, reviewer_score, intent
                )
            )
        except RuntimeError:
            logger.debug("[query_log] 无事件循环，跳过记录")

    async def _write_to_db(
        self,
        session_id: str,
        query: str,
        sql: str,
        success: bool,
        latency_ms: Optional[float],
        reviewer_score: Optional[float],
        intent: Optional[str],
    ) -> None:
        try:
            from app.entities.query_log import QueryLog
            from app.clients.mysql_client_manager import meta_mysql_client_manager

            meta_mysql_client_manager.init()
            async with meta_mysql_client_manager.session_factory() as session:
                from app.repositories.mysql.meta.meta_mysql_repository import (
                    MetaMySQLRepository,
                )

                repo = MetaMySQLRepository(session)
                repo.save_query_log(
                    QueryLog(
                        session_id=session_id,
                        query=query,
                        sql=sql or "",
                        success=success,
                        latency_ms=latency_ms,
                        reviewer_score=reviewer_score,
                        intent=intent,
                    )
                )
                await session.commit()
        except Exception as e:
            logger.warning(f"[query_log] 记录失败（不影响业务）: {e}")


# 模块级单例
query_log_service = QueryLogService()
