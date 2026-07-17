# -*- coding: utf-8 -*-
"""
data_subgraph.py
================

把现有 13 节点 graph 拆成两段 subgraph，给 supervisor_graph 用：

**设计动机**：
- 现有 graph（graph.py）是串行的 13 节点：classify → rewrite → recall → filter → generate → run
- Multi-Agent 场景下，多个 sub_query 共享前置步骤（classify / rewrite / extract_keywords / 3 路召回）
- 不拆的话，每个 sub_query 各自跑一遍完整 13 节点，前置重复工作 = 浪费时间

**拆分原则**：
- 前置 subgraph（preprocess）：跑 1 次，输出 intent + rewrite + keywords + 召回结果
- 后置 subgraph（postprocess）：每个 sub_query 跑 1 次，从 merge_retrieved_info 状态开始

**为什么不直接改 graph.py**：
- graph.py 是稳定生产链路，改坏了影响全网
- supervisor_graph 是 opt-in，独立维护
- data_subgraph.py 是 bridge：复用节点但定义新拓扑

**复用现有节点**（不重写）：
- classify_intent / rewrite_query / extract_keywords / recall_* / merge_retrieved_info
- filter_* / add_extra_context / generate_intent / generate_sql / validate_sql / correct_sql / run_sql
- respond_chitchat / respond_metadata：保留在原 graph.py 不动（multi-agent 不需要）
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agent.context import DataAgentContext
from app.agent.nodes.add_extra_context import add_extra_context
from app.agent.nodes.classify_intent import classify_intent
from app.agent.nodes.correct_sql import correct_sql
from app.agent.nodes.extract_keywords import extract_keywords
from app.agent.nodes.filter_metric import filter_metric
from app.agent.nodes.filter_table import filter_table
from app.agent.nodes.generate_intent import generate_intent
from app.agent.nodes.generate_sql import generate_sql
from app.agent.nodes.merge_retrieved_info import merge_retrieved_info
from app.agent.nodes.recall_column import recall_column
from app.agent.nodes.recall_metric import recall_metric
from app.agent.nodes.recall_value import recall_value
from app.agent.nodes.rewrite_query import rewrite_query
from app.agent.nodes.run_sql import run_sql
from app.agent.nodes.validate_sql import validate_sql
from app.agent.state import DataAgentState


# ─────────────────────────────────────────────────────────────────────
# 前置 subgraph：classify_intent → ... → merge_retrieved_info
# ─────────────────────────────────────────────────────────────────────

def build_preprocessing_subgraph():
    """共享前置：从 query 到 merge_retrieved_info 的中间状态

    输入 state 含: query (用户原句)
    输出 state 含: intent, time_range, inherited_from_history, keywords,
                  retrieved_column_infos, retrieved_metric_infos,
                  retrieved_value_infos, table_infos, metric_infos

    注意：multi-agent 假设 intent == data_query，所以 classify_intent
    走 conditional edge 直奔 rewrite_query（不走 respond_chitchat/respond_metadata）。
    但保留 conditional edge 以防 LLM 误判时降级。
    """
    g = StateGraph(state_schema=DataAgentState, context_schema=DataAgentContext)

    # 注册节点（只注册前置段用到的）
    g.add_node("classify_intent", classify_intent)
    g.add_node("rewrite_query", rewrite_query)
    g.add_node("extract_keywords", extract_keywords)
    g.add_node("recall_column", recall_column)
    g.add_node("recall_value", recall_value)
    g.add_node("recall_metric", recall_metric)
    g.add_node("merge_retrieved_info", merge_retrieved_info)

    # 边：classify_intent → 按 intent 路由
    g.add_edge(START, "classify_intent")

    # 直接强制走 rewrite_query（multi-agent 假定 data_query）
    # 如果 LLM 误判成 chitchat/metadata_query，后置会出错但不会崩溃
    # —— 这种 case 应该不会发生（用户主动勾 multi-agent = 复杂查询）
    g.add_edge("classify_intent", "rewrite_query")

    g.add_edge("rewrite_query", "extract_keywords")

    # 三路召回并行
    g.add_edge("extract_keywords", "recall_column")
    g.add_edge("extract_keywords", "recall_value")
    g.add_edge("extract_keywords", "recall_metric")

    # 三路汇入 merge
    g.add_edge("recall_column", "merge_retrieved_info")
    g.add_edge("recall_value", "merge_retrieved_info")
    g.add_edge("recall_metric", "merge_retrieved_info")

    g.add_edge("merge_retrieved_info", END)

    return g.compile()


# ─────────────────────────────────────────────────────────────────────
# 后置 subgraph：filter_table → ... → run_sql
# ─────────────────────────────────────────────────────────────────────

def build_postprocess_subgraph():
    """后置：从前置完成后的状态（已经含 table_infos / metric_infos）开始，
    每个 sub_query 跑一次，输出 run_sql 的结果

    输入 state 含: query (sub_query 的 query), table_infos, metric_infos, db_info, date_info
    输出 state 含: sql, error (None=成功),  + state["rows"] 由 run_sql 通过 writer 推送

    注意：postprocess 不重跑 classify_intent / rewrite_query —— 这些
    共享结果由预处理阶段产出，注入 input state 即可。
    """
    g = StateGraph(state_schema=DataAgentState, context_schema=DataAgentContext)

    g.add_node("filter_table", filter_table)
    g.add_node("filter_metric", filter_metric)
    g.add_node("add_extra_context", add_extra_context)
    g.add_node("generate_intent", generate_intent)
    g.add_node("generate_sql", generate_sql)
    g.add_node("validate_sql", validate_sql)
    g.add_node("correct_sql", correct_sql)
    g.add_node("run_sql", run_sql)

    # 边：filter_* 并行汇入 add_extra_context
    g.add_edge(START, "filter_table")
    g.add_edge(START, "filter_metric")
    g.add_edge("filter_table", "add_extra_context")
    g.add_edge("filter_metric", "add_extra_context")

    # 串行：context → intent → sql → validate
    g.add_edge("add_extra_context", "generate_intent")
    g.add_edge("generate_intent", "generate_sql")
    g.add_edge("generate_sql", "validate_sql")

    # validate_sql 条件分支
    g.add_conditional_edges(
        "validate_sql",
        lambda state: "run_sql" if state.get("error") is None else "correct_sql",
        path_map={"run_sql": "run_sql", "correct_sql": "correct_sql"},
    )
    g.add_edge("correct_sql", "run_sql")
    g.add_edge("run_sql", END)

    return g.compile()


# 模块级缓存实例（多次调用复用）
_preprocessing = None
_postprocess = None


def get_preprocessing_subgraph():
    global _preprocessing
    if _preprocessing is None:
        _preprocessing = build_preprocessing_subgraph()
    return _preprocessing


def get_postprocess_subgraph():
    global _postprocess
    if _postprocess is None:
        _postprocess = build_postprocess_subgraph()
    return _postprocess