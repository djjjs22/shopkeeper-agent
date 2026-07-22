"""
QueryLog 映射器

负责在查询日志业务实体和 ORM 模型之间做转换。
"""

from dataclasses import asdict

from app.entities.query_log import QueryLog
from app.models.query_log import QueryLogMySQL


class QueryLogMapper:
    """负责 `QueryLog` 与 `QueryLogMySQL` 之间的双向转换"""

    @staticmethod
    def to_entity(model: QueryLogMySQL) -> QueryLog:
        """把 ORM 模型转换回查询日志业务实体"""
        return QueryLog(
            session_id=model.session_id,
            query=model.query,
            sql=model.sql,
            success=model.success,
            latency_ms=model.latency_ms,
            reviewer_score=model.reviewer_score,
            intent=model.intent,
            created_at=model.created_at,
        )

    @staticmethod
    def to_entity_from_row(row: dict) -> QueryLog:
        """把 row dict（session.execute + mappings() 产物）转成业务实体"""
        return QueryLog(
            session_id=row["session_id"],
            query=row["query"],
            sql=row.get("sql"),
            success=bool(row["success"]),
            latency_ms=row.get("latency_ms"),
            reviewer_score=row.get("reviewer_score"),
            intent=row.get("intent"),
            created_at=row.get("created_at"),
        )

    @staticmethod
    def to_model(entity: QueryLog) -> QueryLogMySQL:
        """把查询日志业务实体转换成 ORM 模型用于持久化"""
        # 业务实体没有 id（DB 自增），asdict 后不含 id 字段，ORM 默认 autoincrement
        return QueryLogMySQL(**asdict(entity))
