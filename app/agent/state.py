"""
电商问数 Agent 状态定义

State 是 LangGraph 各节点之间传递和更新的共享数据
本章在用户原始问题之外，新增关键词列表和三路召回结果
并把召回到的实体整理成后续提示词更容易消费的表信息和指标信息
SQL 生成闭环会继续写入候选 SQL 以及校验错误信息，用于控制校正或执行分支
"""

from typing import Optional, TypedDict

from app.entities.column_info import ColumnInfo
from app.entities.metric_info import MetricInfo
from app.entities.plan_schema import QueryPlan
from app.entities.value_info import ValueInfo


class MetricInfoState(TypedDict):
    """面向 SQL 生成提示词的指标信息"""

    name: str
    description: str
    # 指标依赖的字段 id，用来提示模型不要脱离业务口径随意计算
    relevant_columns: list[str]
    alias: list[str]


class ColumnInfoState(TypedDict):
    """表上下文中的字段信息"""

    name: str
    type: str
    role: str
    # 字段真实样例值，尤其用于辅助 where 条件里的枚举值选择
    examples: list
    description: str
    alias: list[str]


class TableInfoState(TypedDict):
    """SQL 生成阶段真正传给模型的表结构上下文"""

    name: str
    role: str
    description: str
    columns: list[ColumnInfoState]


class DateInfoState(TypedDict):
    """SQL 生成阶段使用的当前日期上下文"""

    date: str
    weekday: str
    quarter: str


class TimeRangeState(TypedDict):
    """查询改写节点输出的结构化时间范围

    关键设计：query 字段保留原句，time_range 单独存时间范围。
    这样 jieba 分词和 Embedding 召回看到的是自然语言原句，不会被
    "2025-12-01至2025-12-31" 这种机械字符串污染。
    """

    # 显式时间范围的起止日期（YYYY-MM-DD），None 表示无显式范围
    start_date: str
    end_date: str
    # 原始时间表达，用于日志和回溯（如 "上个月"、"最近30天"）
    raw_expression: str


class InheritedContext(TypedDict):
    """从历史对话中继承的上下文（RFC 刀1 改造：实体/条件/维度继承）

    关键设计：多轮对话时用户经常省略主语/条件（"换成华北"、"按门店拆"），
    本结构化字段显式记录从历史继承了什么，让 generate_intent 节点不用
    再去历史对话里"猜"。
    """

    # 实体继承：如 ["SKU1", "SKU2", "SKU3"]、["客户A", "客户B"]
    entities: list[str]
    # 条件继承：如 ["region='华北'", "year=2025"]，可直接拼到 SQL WHERE
    conditions: list[str]
    # 维度继承：如 ["省份", "门店"]，是用户新加的分组维度
    dimensions: list[str]


class DBInfoState(TypedDict):
    """SQL 生成阶段使用的数据库环境信息"""

    dialect: str
    version: str


class DataAgentState(TypedDict):
    """一次问数链路中的核心状态（single-agent 13 节点链路使用）

    意图分类和查询改写（刀1）在链路最前面执行：
      query   — 用户当前问题，**始终是原始输入**，不被改写节点覆盖
      history — 多轮对话历史，单独存储，需要历史的节点自行取用
      intent  — 意图分类结果：chitchat / metadata_query / data_query
      time_range — 结构化时间范围，rewrite_query 节点的输出，SQL 生成时消费
      inherited_from_history — 从历史对话继承的实体/条件/维度（2026-07-14 改造）
      query_intent — 结构化查询意图（JSON），generate_intent 节点输出，generate_sql 节点消费
    """

    # ── 用户输入与对话上下文 ──
    query: str  # 用户当前问题，只放原始输入，永不被改写节点覆盖（2026-07-14 改造）
    history: list  # 多轮对话历史 [{"role": ..., "content": ...}]，需要历史的节点自己从 state 取
    intent: str  # 意图分类结果，控制 graph 条件边路由
    time_range: TimeRangeState  # 查询改写输出的结构化时间范围（2026-07-14 改造）
    inherited_from_history: InheritedContext  # 从历史继承的实体/条件/维度（2026-07-14 改造）
    query_intent: dict  # 结构化查询意图（JSON），generate_intent 输出（2026-07-14 改造）

    # ── 召回阶段 ──
    keywords: list[str]  # 抽取的关键词
    # 2026-07-20 新增：合并版 LLM 关键词扩展（一次调用产出三维度）
    # 三路 recall 节点直接读这里，不再各自调 LLM（节省 2 次调用 / 成本 -25%）
    extended_keywords_by_dim: dict[str, list[str]]  # {"column":[...], "value":[...], "metric":[...]}
    retrieved_column_infos: list[ColumnInfo]  # 检索到的字段信息
    retrieved_metric_infos: list[MetricInfo]  # 检索到的指标信息
    retrieved_value_infos: list[ValueInfo]  # 检索到的取值信息

    # ── 过滤与生成阶段 ──
    table_infos: list[TableInfoState]  # 合并和补齐后的表结构上下文
    metric_infos: list[MetricInfoState]  # 合并后的指标上下文
    date_info: DateInfoState  # 当前日期 星期和季度信息
    db_info: DBInfoState  # 数据库方言和版本信息

    sql: str  # 生成或校正后的SQL

    error: str  # 校验SQL时出现的错误信息


class MultiAgentState(DataAgentState):
    """Multi-Agent 链路专属状态（2026-07-20 #10 拆分）

    2026-07-17 改造前这些字段塞在 DataAgentState 里，single-agent 13 节点链路
    永远是 None，但所有节点函数签名都 import 同一 state 类型，认知负担大。
    现在拆出来，supervisor_graph 用 MultiAgentState，graph / data_subgraph
    沿用 DataAgentState（更干净，新人读 graph.py 时不再被一堆 None 字段困惑）。

    cached_pre_state 留在本 schema（不迁 context）：LangGraph 的 runtime.context
    是只读的，节点运行时不能修改；cached_pre_state 要在 reviewer retry 时更新，
    必须放 state 里。它属于 multi-agent 专属，放这里不影响 single-agent。
    """

    plan: Optional[QueryPlan]  # Planner 输出的执行计划
    sub_results: list  # 每个 sub_query 跑完的结果 [{sub_id, query, sql, rows, error}, ...]
    final_response: dict  # Aggregator 合并后的最终回复 {answer, sub_results, is_synthesized}
    confidence: float  # Reviewer 打分 0-1
    review_action: Optional[str]  # "retry" / None；空表示通过
    review_loop_count: int  # 反思轮数（max_loop=2 保护）
    cached_pre_state: Optional[dict]  # 共享前置 subgraph 的结果缓存（reviewer retry 复用）
