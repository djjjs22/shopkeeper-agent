"""
UserProfile 映射器

负责在用户偏好业务实体和 ORM 模型之间做转换，
使偏好入库过程保持"业务实体 -> Mapper -> ORM 模型"的清晰分层
"""

from dataclasses import asdict

from app.entities.user_profile import UserProfile
from app.models.user_profile import UserProfileMySQL


class UserProfileMapper:
    """负责 `UserProfile` 与 `UserProfileMySQL` 之间的双向转换"""

    @staticmethod
    def to_entity(model: UserProfileMySQL) -> UserProfile:
        """把 ORM 模型转换回用户偏好业务实体"""
        return UserProfile(
            user_id=model.user_id,
            preference_type=model.preference_type,
            content=model.content,
            confidence=model.confidence,
            updated_at=model.updated_at,
        )

    @staticmethod
    def to_entity_dict(row: dict) -> UserProfile:
        """把 row dict（session.execute + mappings() 产物）转成业务实体

        与 to_entity 的区别：repository 层用 text() + mappings() 走的是裸 SQL，
        返回的是 dict 不是 ORM 实例，所以需要单独的转换方法。
        """
        return UserProfile(
            user_id=row["user_id"],
            preference_type=row["preference_type"],
            content=row["content"],
            confidence=row["confidence"],
            updated_at=row.get("updated_at"),
        )

    @staticmethod
    def to_model(entity: UserProfile) -> UserProfileMySQL:
        """把用户偏好业务实体转换成 ORM 模型用于持久化"""
        return UserProfileMySQL(**asdict(entity))
