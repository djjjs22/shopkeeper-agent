"""
ValueInfo 业务实体

字段真实取值的纯数据结构，不依赖 ES 存储细节，
在 Service / Repository / Agent 节点之间流转。
"""

from dataclasses import dataclass


@dataclass
class ValueInfo:
    """字段取值业务实体"""

    id: str
    value: str
    column_id: str
