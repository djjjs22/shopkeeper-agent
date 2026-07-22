"""
UserProfile 业务实体

用户长期偏好（Semantic Memory），不依赖 ORM 模型，
在 Service / Agent 节点之间流转。

对应"第 4 章 Memory B：User Profile"——记录用户在多轮会话中暴露的
偏好（默认维度、常用术语、时区等），进入 generate_intent 节点的 prompt。
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class UserProfile:
    """用户偏好业务实体

    preference_type 示例：
        - preferred_dim: 常用分组维度（"region" / "category" / ...）
        - common_term: 常用业务术语及映射（"动销率"=某公式）
        - timezone: 时区（"Asia/Shanghai"）

    confidence 语义：
        - 0.9 以上：注入 generate_intent prompt 作为默认上下文
        - 0.3-0.9：只存储不消费，等连续命中提升置信度
        - 0.3 以下：遗忘机制删除（见 memory_decay_service）
    """

    user_id: str
    preference_type: str
    content: str
    confidence: float = 0.5
    updated_at: datetime | None = None
