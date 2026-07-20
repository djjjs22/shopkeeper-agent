# -*- coding: utf-8 -*-
"""
supervisor_graph.py
===================

Multi-Agent 顶层图（2026-07-17 改造）。

**职责**：在原 13 节点 graph 外面包一层 supervisor，实现：
1. Planner 拆 sub_query
2. 每个 sub_query 调一次原 graph（Send API 并行）
3. Aggregator 合并结果
4. Reviewer 评分（< 0.7 触发反思回路 max_loop=2）

**为什么不直接改 graph.py**：
- 现有 graph.py 是稳定的 13 节点链路，改坏了影响生产
- 新 graph 是 opt-in（query 加 use_multi_agent=true 才走新链路）
- 老 query 路径完全不变，向后兼容

**注意**：
- 这是 opt-in 的"第二条"链路，不是替换
- 单 sub_query（多数情况下）= 不调 LLM 拆，planner 直接对老 prompt query 加 note，说明这是 multi-agent 输出
- 多 sub_query = 并行跑多个老 graph，aggregator 合并
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Send

from app.agent.context import DataAgentContext
from app.agent.llm import get_llm
from app.agent.state import DataAgentState, MultiAgentState
from app.agent.graph import graph as legacy_graph  # 老 graph 仍用于单 sub_query 兜底
from app.agent.data_subgraph import get_preprocessing_subgraph, get_postprocess_subgraph
from app.agent.nodes.planner_node import planner
from app.agent.nodes.aggregator_node import aggregator
from app.agent.nodes.reviewer_node import reviewer
from app.core.log import logger

# ─────────────────────────────────────────────────────────────────────
# Sub-query 执行器：每个 sub_query 跑一次老 graph
# ─────────────────────────────────────────────────────────────────────

async def _run_one_sub(
    sub_id: int,
    sub_query: str,
    shared_pre_state: dict,
    context,
    post_subgraph,
) -> dict[str, Any]:
    """跑一个 sub_query：只跑后置 subgraph（filter → generate → run_sql）

    共享前置结果（intent / rewrite / keywords / 召回）由 _gather_sub_results
    跑一次后注入 shared_pre_state，避免每个 sub 重复。

    失败兜底：单 sub 失败不影响其他 sub，error 信息会带到 aggregator。
    """
    t0 = time.perf_counter()
    try:
        # input_state = 共享前置结果 + sub 自己的 query
        input_state = {**shared_pre_state, "query": sub_query}
        rows = []
        sql = ""
        async for chunk in post_subgraph.astream(
            input_state, context=context, stream_mode="custom"
        ):
            if isinstance(chunk, dict):
                if chunk.get("type") == "result":
                    rows = chunk.get("data", [])
                elif chunk.get("type") == "sql":
                    sql = chunk.get("data", "")
            if rows:
                break

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        columns = list(rows[0].keys()) if rows else []
        return {
            "sub_id": sub_id,
            "query": sub_query,
            "sql": sql,
            "rows": rows,
            "columns": columns,
            "error": None,
            "duration_ms": elapsed_ms,
        }
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        logger.error(f"sub_query #{sub_id} 失败: {e}")
        return {
            "sub_id": sub_id,
            "query": sub_query,
            "sql": "",
            "rows": [],
            "columns": [],
            "error": str(e),
            "duration_ms": elapsed_ms,
        }


async def _run_preprocessing_once(query: str, base_state: dict, context, pre_subgraph) -> dict:
    """跑 1 次共享前置 subgraph（classify_intent → ... → merge_retrieved_info）

    返回 state（dict），含：
    - intent / time_range / inherited_from_history / keywords
    - retrieved_column_infos / retrieved_metric_infos / retrieved_value_infos
    - table_infos / metric_infos

    这个结果注入到每个 sub_query 的 input_state，让后置 subgraph 直接复用。
    """
    t0 = time.perf_counter()
    input_state = {**base_state, "query": query}
    final_state = await pre_subgraph.ainvoke(input_state, context=context)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    logger.info(
        f"共享前置 subgraph 完成: {elapsed_ms} ms "
        f"(classify/rewrite/keywords/3 路召回)"
    )
    # 提取有用的字段（避免注入整个 state 含太多无关键）
    shared_keys = (
        "intent", "time_range", "inherited_from_history", "keywords",
        "retrieved_column_infos", "retrieved_metric_infos", "retrieved_value_infos",
        "table_infos", "metric_infos",
    )
    return {k: final_state.get(k) for k in shared_keys if k in final_state}


async def _gather_sub_results(state: MultiAgentState) -> dict[str, Any]:
    """Multi-Agent 数据执行节点：
    1. 跑 1 次共享前置 subgraph（节省 classify/rewrite/recall 重复时间）
    2. 对每个 sub_query 跑后置 subgraph（按 depends_on 分层并行）

    为什么这样设计：
    - 原来每个 sub_query 跑完整 13 节点，前置段（classify / rewrite /
      extract_keywords / 3 路召回 ≈ 7-12s）重复 N 次
    - 拆成 pre + post subgraph 后，前置只跑 1 次（~10s），
      每个 sub 独立跑 post subgraph（~3-5s，filter + intent + sql + run）
    - 3 个 sub 总耗时从 ~30s → ~10s + 3 × 5s = 25s

    并行策略（按 depends_on 分层）：
    - depends_on=[] → 同层 asyncio.gather 并行
    - depends_on=[1,2] → 等上层完成再跑
    """
    plan = state.get("plan")
    if not plan or not plan.sub_queries:
        return {"sub_results": []}

    context = state.get("context")
    base_state = {
        "history": state.get("history", []),
        # multi-agent 假定 intent == data_query，所以预处理强制 intent
        "intent": "data_query",
    }

    # ──── Step 0：跑 1 次共享前置（省 classify/rewrite/recall 重复）──
    pre_subgraph = get_preprocessing_subgraph()
    post_subgraph = get_postprocess_subgraph()

    # 优化：retry 时复用上次的前置结果（避免 reviewer 触发 retry 重复跑 16s）
    # ⚠️ 已知权衡（2026-07-20 #11）：
    #   planner retry 时若重新拆出不同 sub_query 拓扑，cached_pre_state 里的
    #   retrieved_* 仍按旧 sub_query 语义检索，可能跟新 sub_query 不匹配。
    #   实际场景下 reviewer 反馈主要影响 generate_intent/SQL 生成层，planner
    #   很少重新拆分，所以保留 cache 的收益（省 16s）大于风险。
    #   如果未来发现 reviewer retry 时结果异常，可在这里加 plan_signature 检查：
    #   比对当前 plan 与 cached 时的 plan 拓扑，不一致就清空 cache 强制重跑。
    cached_pre = state.get("cached_pre_state")
    if cached_pre is not None:
        logger.info("复用上次共享前置结果（避免重跑 16s）")
        shared_pre_state = cached_pre
    else:
        shared_pre_state = await _run_preprocessing_once(
            state["query"], base_state, context, pre_subgraph
        )

    # ──── Step 1：按 depends_on 拓扑分层 ────
    sub_ids = [sq.id for sq in plan.sub_queries]
    by_id = {sq.id: sq for sq in plan.sub_queries}

    layers: list[list[int]] = []
    finished: set[int] = set()
    remaining = set(sub_ids)

    while remaining:
        current_layer = sorted([
            sid for sid in remaining
            if all(dep in finished for dep in by_id[sid].depends_on)
        ])
        if not current_layer:
            logger.warning(
                f"plan 拓扑存在循环依赖或 dangling 引用，兜底全部跑"
            )
            current_layer = sorted(remaining)
        layers.append(current_layer)
        finished.update(current_layer)
        remaining -= set(current_layer)

    logger.info(
        f"plan 拓扑分层: {len(layers)} 层, "
        f"sub 数量 {len(sub_ids)}, 层结构 {[len(l) for l in layers]}"
    )

    # ──── Step 2：每层内部 asyncio.gather 并行（只跑 post subgraph）──
    all_results: list[dict[str, Any]] = []
    for layer_idx, layer_ids in enumerate(layers):
        tasks = [
            _run_one_sub(
                sid, by_id[sid].query,
                shared_pre_state, context, post_subgraph,
            )
            for sid in layer_ids
        ]
        layer_results = await asyncio.gather(*tasks)
        logger.info(
            f"第 {layer_idx + 1}/{len(layers)} 层完成: "
            f"{len(layer_results)} 个 sub_query 并行跑完"
        )
        all_results.extend(layer_results)

    id_to_result = {r["sub_id"]: r for r in all_results}
    ordered = [id_to_result[sq.id] for sq in plan.sub_queries]
    logger.info(
        f"multi-agent 全部完成: {len(ordered)} 个 sub_query, "
        f"总耗时 {sum(r['duration_ms'] for r in ordered)} ms"
    )
    # 保留共享前置结果到 state —— reviewer retry 时复用，避免再跑 16s
    return {
        "sub_results": ordered,
        "cached_pre_state": shared_pre_state,
    }


# ─────────────────────────────────────────────────────────────────────
# Supervisor 顶层路由
# ─────────────────────────────────────────────────────────────────────

def _route_after_reviewer(state: DataAgentState) -> str:
    """Reviewer 后的条件边：
    - 反思回路不超过 max_loop 且 confidence < 0.7 → retry（回到 data_agent）
    - 否则 END
    """
    confidence = state.get("confidence", 1.0)
    action = state.get("review_action")
    loop = state.get("review_loop_count", 0)

    if action == "retry" and loop < 2:
        return "data_agent"
    return END


def build_supervisor_graph():
    """构造 multi-agent 顶层图

    调用方式：
        graph = build_supervisor_graph()
        result = await graph.ainvoke({"query": "..."}, context=DataAgentContext(...))
    """
    g = StateGraph(state_schema=MultiAgentState, context_schema=DataAgentContext)

    # 注册节点
    g.add_node("planner", planner)
    g.add_node("data_agent", RunnableLambda(_gather_sub_results))
    g.add_node("aggregator", aggregator)
    g.add_node("reviewer", reviewer)

    # 边
    g.add_edge(START, "planner")
    g.add_edge("planner", "data_agent")
    g.add_edge("data_agent", "aggregator")
    g.add_edge("aggregator", "reviewer")
    g.add_conditional_edges("reviewer", _route_after_reviewer)

    return g.compile()


# 模块级默认实例（直接 import 也能用）
supervisor_graph = build_supervisor_graph()


if __name__ == "__main__":
    # 本地测试入口
    async def test():
        from app.agent.context import DataAgentContext
        from app.clients.embedding_client_manager import embedding_client_manager
        from app.clients.es_client_manager import es_client_manager
        from app.clients.mysql_client_manager import meta_mysql_client_manager, dw_mysql_client_manager
        from app.clients.qdrant_client_manager import qdrant_client_manager
        from app.repositories.es.value_es_repository import ValueESRepository
        from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
        from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
        from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository

        qdrant_client_manager.init()
        embedding_client_manager.init()
        es_client_manager.init()
        meta_mysql_client_manager.init()
        dw_mysql_client_manager.init()

        async with (
            meta_mysql_client_manager.session_factory() as meta_session,
        ):
            meta_repo = MetaMySQLRepository(meta_session)
            ctx = DataAgentContext(
                column_qdrant_repository=ColumnQdrantRepository(qdrant_client_manager.client),
                embedding_client=embedding_client_manager.client,
                metric_qdrant_repository=MetricQdrantRepository(qdrant_client_manager.client),
                value_es_repository=ValueESRepository(es_client_manager.client),
                meta_mysql_repository=meta_repo,
                dw_mysql_repository=meta_repo,
            )

            result = await supervisor_graph.ainvoke(
                {"query": "请算出这个月的环比增长率"},
                context=ctx,
            )
            print("RESULT:", result)

            await qdrant_client_manager.close()
            await es_client_manager.close()
            await meta_mysql_client_manager.close()
            await dw_mysql_client_manager.close()

    asyncio.run(test())
