"""
SqlPattern 业务实体

历史成功 SQL 沉淀出的"意图 + 模板"对（Procedural Memory），
作为 few-shot 注入 generate_intent 节点。

对应"第 4 章 Memory A：SQL Pattern Bank"——把"用户的常用问法"和
"正确 SQL 写法"对齐起来，下次相似 query 召回 top-k 模板做 few-shot。
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SqlPattern:
    """SQL 模板业务实体

    source:
        - gold: 来自 gold_dataset 跑通的 case，confidence=1.0
        - online: 来自线上 query_log 成功执行的 SQL，confidence=0.5 起步，
          随命中率（hit_count）提升而增长

    confidence 语义（与 hit_count 联动）：
        - gold 来源固定 1.0
        - online 来源 = min(0.5 + 0.1 * log2(hit_count+1), 0.95)

    sql_template 是已抽象化的模板（具体值 → 占位符），例：
        SELECT region_name, SUM(order_amount)
        FROM fact_order JOIN dim_region ...
        WHERE region_name = '<region_value>'
        GROUP BY region_name
    """

    id: str
    query_intent_text: str  # 用户原句或意图文本，用于 embedding 召回
    sql_template: str  # 抽象后的 SQL 模板
    source: str = "online"  # gold / online
    confidence: float = 0.5
    hit_count: int = 0
    vector_id: str | None = None  # Qdrant point id（与 id 解耦，便于重建索引）
    tags: list[str] = field(default_factory=list)  # 形态标签：["join", "time_filter", "having"]
    created_at: datetime | None = None
    updated_at: datetime | None = None
