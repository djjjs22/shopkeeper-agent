"""
`user_profile` ORM 模型

用户长期偏好（Semantic Memory）的持久化结构。
保存偏好类型、内容、置信度、更新时间，供 generate_intent 节点读取。

置信度策略：
- 抽取规则触发时写入 confidence=0.5
- 连续命中（3 次以上同偏好）→ 0.9
- 矛盾时以最新为准（覆盖）
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UserProfileMySQL(Base):
    """用户偏好表 ORM 模型"""

    __tablename__ = "user_profile"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, comment="自增主键")
    user_id: Mapped[str] = mapped_column(String(64), index=True, comment="用户/会话 ID")
    preference_type: Mapped[str] = mapped_column(String(32), comment="preferred_dim/common_term/timezone")
    content: Mapped[str] = mapped_column(Text, comment="偏好内容")
    confidence: Mapped[float] = mapped_column(Float, default=0.5, comment="置信度 0-1")
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="最近更新时间"
    )
