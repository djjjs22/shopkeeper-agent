"""
MetricInfo 业务实体

指标元数据的纯数据结构，不依赖 ORM 模型，
在 Service / Repository / Agent 节点之间流转。
"""

from dataclasses import dataclass, field


@dataclass
class MetricInfo:
    """指标元数据业务实体"""

    id: str
    name: str | None = None
    description: str | None = None
    relevant_columns: list[str] | None = field(default_factory=list)
    alias: list[str] | None = field(default_factory=list)
    # 2026-09-16 加固：复杂业务指标的 SQL 模板（占位符 <date>/<value>/<number>）
    # yaml 配置 → 同步到 MySQL → generate_intent 召回时返回 → render 替换占位符
    sql_template: str | None = None
