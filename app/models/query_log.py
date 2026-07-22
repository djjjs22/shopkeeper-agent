"""
`query_log` ORM 模型

每次问数查询的执行记录，数据飞轮的起点。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class QueryLogMySQL(Base):
    """查询日志表 ORM 模型"""

    __tablename__ = "query_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, comment="自增主键")
    session_id: Mapped[str] = mapped_column(String(64), index=True, comment="会话 ID")
    query: Mapped[str] = mapped_column(Text, comment="用户原始问题")
    sql: Mapped[str | None] = mapped_column(Text, comment="最终执行的 SQL")
    success: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否成功执行")
    latency_ms: Mapped[float | None] = mapped_column(Float, comment="端到端耗时（毫秒）")
    reviewer_score: Mapped[float | None] = mapped_column(Float, comment="multi-agent reviewer 评分")
    intent: Mapped[str | None] = mapped_column(String(32), comment="意图分类 chitchat/metadata_query/data_query")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now(), index=True, comment="创建时间"
    )
