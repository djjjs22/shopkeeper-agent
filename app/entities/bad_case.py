"""
BadCase 业务实体

数据飞轮的核心沉淀物：每次"系统表现不佳"的查询自动归集到这里，
周期性 review 后进 gold_dataset，驱动 prompt / recall 优化。

对应"第 5 章 Online Eval：Bad Case 自动归集"。

error_type 分类（组合信号源，见 docs/AI应用架构升级路线.md 第 5.5 节）：
    - sql_fail: validate_sql / correct_sql 失败（SQL 执行报错）
    - review_low: reviewer 评分 < 0.5（multi-agent 路径）
    - user_thumb_down: 用户主动 👎（feedback 端点）
    - rewrite_signal: 用户 30s 内改问同一意图（飞轮 v2，需 session 时序分析）
    - execution_mismatch: execution eval 跑出来结果集对不上 gold（CI 回归期发现）
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class BadCase:
    """失败 case 业务实体

    status 流转：
        - new: 刚归集，未 review
        - triaged: 人工看过，已分类到某个失败模式（join_recall_miss / value_recall_miss / prompt_ambiguity / ...）
        - fixed: 已通过 prompt 改写 / recall 调优修复，进 gold_dataset 验证
    """

    query: str
    sql: str
    error_type: str
    detail: str | None = None
    session_id: str | None = None
    status: str = "new"
    failure_mode: str | None = None  # triaged 后的归类
    created_at: datetime | None = None
    reviewed_at: datetime | None = None
