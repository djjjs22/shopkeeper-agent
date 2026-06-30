"""
ColumnInfo 业务实体

字段元数据的纯数据结构，不依赖 ORM 模型，
在 Service / Repository / Agent 节点之间流转。
"""

from dataclasses import dataclass, field


@dataclass
class ColumnInfo:
    """字段元数据业务实体"""

    id: str
    name: str | None = None
    type: str | None = None
    role: str | None = None
    examples: list | None = field(default_factory=list)
    description: str | None = None
    alias: list[str] | None = field(default_factory=list)
    table_id: str | None = None
