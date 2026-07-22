"""
`sql_pattern` ORM 模型

历史成功 SQL 沉淀出的"意图 + 模板"对（Procedural Memory）。
与 Qdrant 的 sql_pattern_collection 双写：
- MySQL 存模板全文 + 元数据 + tags
- Qdrant 存 query_intent_text 的向量（vector_id 字段引用）

source: gold / online，置信度见 app/entities/sql_pattern.py docstring
"""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import Base


class SqlPatternMySQL(Base):
    """SQL 模板表 ORM 模型"""

    __tablename__ = "sql_pattern"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="模板 ID（hash(query_intent_text)）")
    query_intent_text: Mapped[str] = mapped_column(Text, comment="用户原句或意图文本，用于 embedding")
    sql_template: Mapped[str] = mapped_column(Text, comment="抽象后的 SQL 模板")
    source: Mapped[str] = mapped_column(String(16), default="online", comment="gold/online")
    confidence: Mapped[float] = mapped_column(Float, default=0.5, comment="置信度 0-1")
    hit_count: Mapped[int] = mapped_column(Integer, default=0, comment="被召回并跑通的次数")
    vector_id: Mapped[str | None] = mapped_column(String(64), comment="Qdrant point id")
    tags: Mapped[list[Any] | None] = mapped_column(JSON, comment="形态标签 join/time_filter/having 等")
    created_at: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
