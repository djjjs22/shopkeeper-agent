"""
TableInfo 业务实体

表元数据的纯数据结构，不依赖 ORM 模型，
在 Service / Repository / Agent 节点之间流转。
"""

from dataclasses import dataclass


@dataclass
class TableInfo:
    """表元数据业务实体"""

    id: str
    name: str | None = None
    role: str | None = None
    description: str | None = None
