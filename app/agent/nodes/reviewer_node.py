# -*- coding: utf-8 -*-
"""
reviewer_node.py
================

Multi-Agent Reviewer 节点（2026-07-17 改造）。

**职责**：
拿到最终回复（aggregator 输出）和原始 query，用 LLM 打分决定"要不要重跑"。

**为什么需要 reviewer**：
单 agent 体系下，错误要等用户反馈发现。
Multi-agent 体系下，aggregator 合并后让 LLM 自我审查，
confidence < 0.7 时触发反思回路回 Data Agent 重跑（最多 2 轮）。

**反思回路保护（max_loop=2）**：
- state["review_loop_count"] 跟踪反思轮数
- 超过 2 次直接返回（不再审），防延迟爆炸
- 这也是工业级 multi-agent 系统的常见坑：不限制反思轮数会被 LLM 滥用

**输入**：state["query"], state["final_response"], state["review_loop_count"]
**输出**：state["confidence"], state["review_action"] (None / "retry")
"""

from __future__ import annotations

from typing import Any

from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

# 反思回路保护：超过这个轮数就强行返回（不再审）
# 工业级 multi-agent 常见坑：不限制反思轮数会被 LLM 滥用，导致延迟爆炸
# 经验值 2 轮：第 1 轮 retry 后再审一次，再不行就返回（fail-open）
MAX_REVIEW_LOOP = 2

from app.agent.context import DataAgentContext
from app.agent.llm import get_llm
from app.agent.state import DataAgentState
from app.core.log import logger
from app.core.safe_json_parser import safe_parse_json
from app.core.timing import timed_node
from app.prompt.prompt_loader import load_prompt


def _parse_review_decision(raw: str) -> tuple[float, str | None]:
    """解析 LLM 输出为 (confidence, action)"""
    try:
        data = safe_parse_json(raw)
    except Exception:
        # 解析失败：默认低置信度让系统走 retry 路径（fail-open）
        return 0.5, "retry"

    try:
        confidence = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5

    # action 取值范围：None（直接返回）/ "retry"（回 Data Agent 重跑）
    action_raw = data.get("action", "retry")
    if action_raw == "pass" or action_raw == "ok" or action_raw == "accept":
        action: str | None = None
    else:
        action = "retry"

    return confidence, action


@timed_node
async def reviewer(state: DataAgentState, runtime: Runtime[DataAgentContext]) -> dict[str, Any]:
    """LLM 审查最终回复是否合理

    confidence < 0.7 → action="retry"，让 Data Agent 重跑
    confidence >= 0.7 → action=None，直接返回给用户

    兜底：任何异常都返回 (0.5, "retry")，让外层 supervisor 决定
    """
    writer = runtime.stream_writer
    step = "质量审查"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        # 保护反思回路不爆炸
        loop_count = state.get("review_loop_count", 0)
        if loop_count >= MAX_REVIEW_LOOP:
            logger.info(
                f"reviewer 已达 max_loop={MAX_REVIEW_LOOP}，返回（不再审）"
            )
            writer({"type": "progress", "step": step, "status": "skipped"})
            return {"confidence": 1.0, "review_action": None}

        query = state["query"]
        final = state.get("final_response", {})
        answer = final.get("answer", "")

        llm = get_llm("reviewer")

        prompt_text = load_prompt("review_answer")
        full_prompt = prompt_text.replace("{query}", query).replace("{answer}", answer)

        raw = await llm.ainvoke(full_prompt)
        # LLM 返回值可能是 AIMessage 对象（langchain 标准），提取 .content
        if hasattr(raw, "content"):
            raw = raw.content
        confidence, action = _parse_review_decision(raw)

        logger.info(
            f"reviewer: confidence={confidence:.2f} action={action} "
            f"loop={loop_count + 1}/{MAX_REVIEW_LOOP}"
        )

        writer({"type": "progress", "step": step, "status": "success"})
        return {
            "confidence": confidence,
            "review_action": action,
            "review_loop_count": loop_count + 1,
        }

    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        # 兜底：异常时返回 retry 让外层处理
        return {"confidence": 0.5, "review_action": "retry"}
