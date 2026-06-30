"""
ColumnMetric 业务实体

字段与指标关联关系的纯数据结构，不依赖 ORM 模型，
在 Service / Repository 之间流转。
"""

from dataclasses import dataclass


@dataclass
class ColumnMetric:
    """字段与指标关联关系业务实体"""

    column_id: str
    metric_id: str
