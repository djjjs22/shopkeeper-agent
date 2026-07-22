"""
QueryLog 业务实体

每次问数查询的执行记录，是"数据飞轮"的起点：
- 成功记录 → Pattern Bank 的 online 数据源
- 失败记录 → bad_case 归集的辅助信号

对应"第 5 章 Online Eval + 数据飞轮"。
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class QueryLog:
    """单次查询日志业务实体

    success=True 的记录会被 pattern_bank_service 消费：
    周期性扫描 query_log，把成功 SQL 抽成模板入库。

    reviewer_score 仅 multi-agent 路径有（single-agent 为 None），
    用于"信号组合"——score<0.5 即使 success=True 也可能是假阳性。
    """

    session_id: str
    query: str
    sql: str
    success: bool
    latency_ms: float | None = None
    reviewer_score: float | None = None
    intent: str | None = None  # chitchat / metadata_query / data_query
    created_at: datetime | None = None
