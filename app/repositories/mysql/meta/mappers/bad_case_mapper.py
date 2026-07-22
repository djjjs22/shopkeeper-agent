"""
BadCase 映射器

负责在失败 case 业务实体和 ORM 模型之间做转换。
"""

from dataclasses import asdict

from app.entities.bad_case import BadCase
from app.models.bad_case import BadCaseMySQL


class BadCaseMapper:
    """负责 `BadCase` 与 `BadCaseMySQL` 之间的双向转换"""

    @staticmethod
    def to_entity(model: BadCaseMySQL) -> BadCase:
        """把 ORM 模型转换回失败 case 业务实体"""
        return BadCase(
            query=model.query,
            sql=model.sql,
            error_type=model.error_type,
            detail=model.detail,
            session_id=model.session_id,
            status=model.status,
            failure_mode=model.failure_mode,
            created_at=model.created_at,
            reviewed_at=model.reviewed_at,
        )

    @staticmethod
    def to_entity_from_row(row: dict) -> BadCase:
        """把 row dict（session.execute + mappings() 产物）转成业务实体"""
        return BadCase(
            query=row["query"],
            sql=row.get("sql"),
            error_type=row["error_type"],
            detail=row.get("detail"),
            session_id=row.get("session_id"),
            status=row.get("status", "new"),
            failure_mode=row.get("failure_mode"),
            created_at=row.get("created_at"),
            reviewed_at=row.get("reviewed_at"),
        )

    @staticmethod
    def to_model(entity: BadCase) -> BadCaseMySQL:
        """把失败 case 业务实体转换成 ORM 模型用于持久化"""
        return BadCaseMySQL(**asdict(entity))
