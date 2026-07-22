"""
`bad_case` ORM 模型

失败 case 自动归集表，数据飞轮的核心沉淀物。
status 流转：new → triaged → fixed
"""

from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BadCaseMySQL(Base):
    """失败 case 表 ORM 模型"""

    __tablename__ = "bad_case"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, comment="自增主键")
    query: Mapped[str] = mapped_column(Text, comment="失败的用户问题")
    sql: Mapped[str | None] = mapped_column(Text, comment="生成的 SQL（可能为空）")
    error_type: Mapped[str] = mapped_column(String(32), comment="sql_fail/review_low/user_thumb_down/rewrite_signal/execution_mismatch")
    detail: Mapped[str | None] = mapped_column(Text, comment="错误详情")
    session_id: Mapped[str | None] = mapped_column(String(64), index=True, comment="会话 ID")
    status: Mapped[str] = mapped_column(String(16), default="new", index=True, comment="new/triaged/fixed")
    failure_mode: Mapped[str | None] = mapped_column(String(64), comment="triaged 后的归类")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now(), index=True, comment="归集时间"
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, comment="人工 review 时间")
