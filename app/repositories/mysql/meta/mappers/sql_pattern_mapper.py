"""
SqlPattern 映射器

负责在 SQL 模板业务实体和 ORM 模型之间做转换。
"""

import json
from dataclasses import asdict

from app.entities.sql_pattern import SqlPattern
from app.models.sql_pattern import SqlPatternMySQL


class SqlPatternMapper:
    """负责 `SqlPattern` 与 `SqlPatternMySQL` 之间的双向转换"""

    @staticmethod
    def to_entity(model: SqlPatternMySQL) -> SqlPattern:
        """把 ORM 模型转换回 SQL 模板业务实体"""
        return SqlPattern(
            id=model.id,
            query_intent_text=model.query_intent_text,
            sql_template=model.sql_template,
            source=model.source,
            confidence=model.confidence,
            hit_count=model.hit_count,
            vector_id=model.vector_id,
            tags=model.tags or [],
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    @staticmethod
    def to_entity_from_row(row: dict) -> SqlPattern:
        """把 row dict（session.execute + mappings() 产物）转成业务实体

        tags 字段在 MySQL 是 JSON，不同驱动可能返回 list 或 str，统一处理。
        """
        tags = row.get("tags")
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except json.JSONDecodeError:
                tags = []
        return SqlPattern(
            id=row["id"],
            query_intent_text=row["query_intent_text"],
            sql_template=row["sql_template"],
            source=row["source"],
            confidence=row["confidence"],
            hit_count=row["hit_count"],
            vector_id=row.get("vector_id"),
            tags=tags or [],
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    @staticmethod
    def to_model(entity: SqlPattern) -> SqlPatternMySQL:
        """把 SQL 模板业务实体转换成 ORM 模型用于持久化"""
        return SqlPatternMySQL(**asdict(entity))
