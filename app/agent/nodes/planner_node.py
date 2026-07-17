# -*- coding: utf-8 -*-
"""
planner_node.py
===============

Multi-Agent Planner 节点（2026-07-17 改造）。

**职责**：
拿到用户 query 后，用 LLM 把它拆成 0-N 个独立 sub_query，每个 sub_query 后续会单独跑 SQL。

**输入**：state["query"]
**输出**：state["plan"] = QueryPlan（sub_queries 列表）

**流程**：
1. 加载 prompts/plan_query.prompt 模板
2. 把 prompts/plan_query_examples.json 转成 few-shot 字符串（10 个示例）
3. 用 LLM 跑
4. 用 PydanticIntentParser 解析 + QueryPlan.model_validate 校验
5. 失败 retry 1 次，降级为「单 sub_query 不拆」

**关键设计**：
- 复用今天的 Pydantic schema 强校验（方向 3）：safe_parse_json + QueryPlan.model_validate
- 复用 @timed_node 装饰器（方向 1）：自动打点
- 复用 get_llm("planner")（方向 2）：按 node_profiles 路由（planner 节点暂用 strong 模型）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import get_llm
from app.agent.state import DataAgentState
from app.core.log import logger
from app.core.safe_json_parser import safe_parse_json
from app.core.timing import timed_node
from app.entities.plan_schema import QueryPlan
from app.prompt.prompt_loader import load_prompt

# prompts/plan_query_examples.json 路径（项目根 → prompts/）
# pathlib 注意：
#   planner_node.py 路径: D:/shopkeeper-agent/app/agent/nodes/planner_node.py
#   parents[0] = nodes/, parents[1] = agent/, parents[2] = app/, parents[3] = shopkeeper-agent/
_PROMPTS_DIR = Path(__file__).parents[3] / "prompts"
_EXAMPLES_PATH = _PROMPTS_DIR / "plan_query_examples.json"


def _load_examples() -> str:
    """加载 10 个示例，转成 few-shot 字符串

    设计：examples 抽到 JSON 而不是写进 .prompt，是因为 JSON 便于：
    - 增删不动 prompt 模板逻辑
    - 单元测试独立加载
    - 与代码 import 的数据流一致
    """
    data = json.loads(_EXAMPLES_PATH.read_text(encoding="utf-8"))

    examples = data.get("examples", [])
    if not examples:
        logger.warning(
            f"plan_query_examples.json 为空或 examples 字段缺失：{_EXAMPLES_PATH}"
        )
        return ""

    lines: list[str] = []
    for ex in examples:
        # 把 plan 转成可读 JSON（保持缩进让 LLM 看清结构）
        plan_json = json.dumps(ex["plan"], ensure_ascii=False, indent=2)
        lines.append(
            f"【示例 {ex['id']}】\n"
            f"用户问题：{ex['query']}\n"
            f"规划结果：{plan_json}\n"
            f"（拆分理由：{ex.get('reason', '')}）\n"
        )

    return "\n".join(lines)


@timed_node
async def planner(state: DataAgentState, runtime: Runtime[DataAgentContext]) -> dict[str, Any]:
    """把用户 query 拆成 1-N 个 sub_query

    失败兜底：默认只拆成 1 个 sub_query（即不拆，走原 single-agent 链路）。
    这样最坏情况系统还能工作，只是退化为老的 13 节点执行。
    """
    llm = get_llm("planner")  # 按 node_profiles 路由（暂走 strong，准确度优先）

    writer = runtime.stream_writer
    step = "查询规划"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        query = state["query"]
        examples = _load_examples()

        prompt_text = load_prompt("plan_query")

        # 2026-07-17 改造：字符串 replace → PromptTemplate + jinja2
        # 改前用 str.replace({examples}/{query}) 的原因：
        #   examples 含 JSON 字符串，里面有 {...} 字面量；
        #   str.format() 会把 {...} 当占位符试图解析 → KeyError。
        # 改 jinja2 后：
        #   - jinja2 不解析单层 {...}（只认 {{ var }}），JSON 字面量安全
        #   - 改用 PromptTemplate.format() 渲染，模板格式统一
        prompt = PromptTemplate(
            template=prompt_text,
            template_format="jinja2",
            input_variables=["examples", "query"],
        )
        full_prompt = prompt.format(examples=examples, query=query)

        raw = await llm.ainvoke(full_prompt)

        # LLM 返回值可能是 AIMessage 对象而不是 str（langchain 标准）
        # 提取 .content 字段再 parse —— safe_parse_json 不认识 AIMessage
        if hasattr(raw, "content"):
            raw = raw.content

        # 复用方向 3 改造：safe_parse_json + Pydantic 校验
        try:
            data = safe_parse_json(raw)
            plan = QueryPlan.model_validate(data)
            logger.info(
                f"planner 拆解成功: {len(plan.sub_queries)} 个 sub_query"
            )
        except Exception as parse_err:
            logger.warning(f"planner 解析失败，降级单 query: {parse_err}")
            plan = QueryPlan(
                sub_queries=[{"id": 0, "query": query, "depends_on": []}]
            )

        writer({"type": "progress", "step": step, "status": "success"})
        return {"plan": plan}

    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        # 兜底：原链路继续能跑
        return {
            "plan": QueryPlan(
                sub_queries=[{"id": 0, "query": state["query"], "depends_on": []}]
            )
        }
