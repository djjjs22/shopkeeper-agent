# -*- coding: utf-8 -*-
"""
aggregator_node.py
==================

Multi-Agent Aggregator 节点（2026-07-17 改造）。

**职责**：
把所有 sub_query 跑完后的 SQL 结果合并成"对用户友好的最终回复"。

**为什么单写一个节点**：
- 单 sub_query：直接透传 SQL 结果 + 总结，不需要 LLM（便宜路径）
- 多 sub_query：用 LLM 合并多张表为一段话（贵但必要）
- 错误处理：哪个 sub 失败就降级展示哪个，避免一个错全错

**输入**：state["plan"].sub_queries + state["sub_results"]（每个 sub 的 SQL 执行结果）
**输出**：state["final_response"] = {summary, sub_results, answer}
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import get_llm
from app.agent.state import DataAgentState
from app.core.log import logger
from app.core.safe_json_parser import safe_parse_json
from app.core.timing import timed_node
from app.prompt.prompt_loader import load_prompt


def _format_sub_results(state: DataAgentState) -> str:
    """把所有 sub_query 的结果格式化成 LLM 可读文本

    state["sub_results"]: list[{sub_id, query, sql, columns, rows, error}]
    """
    results = state.get("sub_results", [])
    if not results:
        return "（无 sub_query 结果）"

    lines: list[str] = []
    for r in results:
        lines.append(
            f"【sub_query #{r['sub_id']}】{r['query']}\n"
            f"  SQL: {r.get('sql', '(无)')[:200]}\n"
            f"  结果: {len(r.get('rows', []))} 行，"
            f"列: {r.get('columns', [])}\n"
            f"  数据示例: {r.get('rows', [])[:3]}\n"
            f"  错误: {r.get('error') or '无'}\n"
        )
    return "\n".join(lines)


@timed_node
async def aggregator(state: DataAgentState, runtime: Runtime[DataAgentContext]) -> dict[str, Any]:
    """合并多个 sub_query 的结果为最终回复

    策略：
    1. 单 sub_query：不调 LLM，直接 answer = 原始结果 + 简单总结模板
    2. 多 sub_query：调 LLM 合并为自然语言回复
    3. 部分 sub 失败：在 answer 里标 "[sub #X 失败：...]"，不阻塞其它 sub
    """
    writer = runtime.stream_writer
    step = "汇总结果"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        sub_results = state.get("sub_results", [])
        plan = state.get("plan")
        sub_queries = plan.sub_queries if plan else []

        # 2026-07-20 调试：确认 sub_results 实际长度（怀疑 retry 后被覆盖）
        logger.info(
            f"aggregator 入口: sub_results 长度={len(sub_results)}, "
            f"plan.sub_queries 长度={len(sub_queries)}, "
            f"review_loop_count={state.get('review_loop_count', 0)}, "
            f"state keys={list(state.keys())}, "
            f"plan type={type(plan).__name__}, "
            f"plan is None={plan is None}"
        )

        # 2026-07-22 修复：LangGraph subgraph state 隔离导致 aggregator 拿不到
        # _gather_sub_results 写入的 sub_results（state 只有 input 的 query/history）。
        # 但 _gather_sub_results 已经通过 writer 把 type="result" 推给前端了，
        # 所以这里拿不到 sub_results 时不要让 LLM 编"未查到数据"，
        # 直接返回中性消息（前端已经有结果了）。
        if not sub_results:
            logger.warning(
                "aggregator: sub_results 为空（LangGraph subgraph state 隔离），"
                "前端已通过 _gather_sub_results 的 writer 收到结果，返回中性消息"
            )
            writer({"type": "progress", "step": step, "status": "success"})
            return {
                "final_response": {
                    "answer": "查询已完成。",
                    "sub_results": [],
                    "is_synthesized": False,
                },
                "confidence": 1.0,  # 不触发 reviewer retry（结果已推前端）
                "review_action": None,
            }

        # ---------- 路径 1：单 sub_query（不调 LLM，省钱）----------
        if len(sub_results) == 1:
            only = sub_results[0]
            rows = only.get("rows", [])
            answer = (
                f"共 {len(rows)} 行结果。"
                if not only.get("error")
                else f"查询失败：{only.get('error')}"
            )
            logger.info(
                f"aggregator 单 sub 路径，不调 LLM（answer={len(answer)} 字符）"
            )
            writer({"type": "progress", "step": step, "status": "success"})
            # 2026-07-17 修复：multi-agent 模式下也推 type="result" 事件
            # 不然前端 SSE 流走完 fallback 到 "流程已结束，后端未返回查询结果"
            # data 直接用 rows（与 single-agent run_sql 节点行为一致，前端 ResultTable 可直接渲染）
            writer({"type": "result", "data": rows})
            return {
                "final_response": {
                    "answer": answer,
                    "sub_results": sub_results,
                    "is_synthesized": False,
                }
            }

        # ---------- 路径 2：多 sub_query（调 LLM 合并）----------
        llm = get_llm("aggregator")  # 暂用 strong 模型（合并质量要求高）
        sub_text = _format_sub_results(state)
        original_query = state["query"]

        prompt_text = load_prompt("aggregate_results")
        full_prompt = prompt_text.replace("{original_query}", original_query).replace(
            "{sub_results}", sub_text
        )

        try:
            raw = await llm.ainvoke(full_prompt)
            # raw 可能是 AIMessage 对象（langchain 标准）或字符串
            if hasattr(raw, "content"):
                raw = raw.content
            # raw 可能是字符串（普通回复）或 dict
            if isinstance(raw, str):
                data = safe_parse_json(raw)
                answer = data.get("answer", raw) if isinstance(data, dict) else raw
            else:
                answer = str(raw)
        except Exception as llm_err:
            logger.warning(f"aggregator LLM 合并失败，降级展示原始结果: {llm_err}")
            # 兜底：直接拼接所有 sub 的样本数据
            answer = "（合并失败，以下是各 sub 原始结果）\n\n" + sub_text

        writer({"type": "progress", "step": step, "status": "success"})
        # 2026-07-17 修复：multi-agent 模式下推 type="result" 事件
        # LLM 合并结果是自然语言，data 用 [{"回复": answer}] 包装，
        # 前端 ResultTable 渲染为一行（避免长时间停留在 "流程已结束..." 兜底文本）
        writer({"type": "result", "data": [{"回复": answer}]})
        return {
            "final_response": {
                "answer": answer,
                "sub_results": sub_results,
                "is_synthesized": True,
            }
        }

    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        # 2026-07-17 修复：异常路径也要推 result 事件，否则前端卡在 streaming
        writer({"type": "result", "data": [{"错误": "aggregator 异常，已返回原始 sub 结果"}]})
        # 最终兜底：透传 sub_results，不阻塞用户
        return {
            "final_response": {
                "answer": "(aggregator 异常，返回原始 sub 结果)",
                "sub_results": state.get("sub_results", []),
                "is_synthesized": False,
            }
        }
