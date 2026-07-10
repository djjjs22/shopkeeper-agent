# -*- coding: utf-8 -*-
"""
字段取值召回节点

负责从字段值全文索引中召回候选取值
当用户问题里出现店铺名 类目名 地区名等业务值时，这一步可以帮助定位真实字段和值
实现路径：关键词扩展 -> Elasticsearch 全文检索 -> ValueInfo 去重

性能优化（asyncio.gather 并行化，P2 #8 重构后）：
  N 个关键词并行执行 ES 检索，IO 重叠等待
  详见 _recall_helpers.py
"""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.nodes._recall_helpers import (
    expand_keywords_with_llm,
    parallel_recall_dedup,
)
from app.agent.state import DataAgentState
from app.core.log import logger


async def recall_value(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """召回和用户问题相关的字段取值"""

    writer = runtime.stream_writer
    step = "召回字段取值"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        query = state["query"]
        keywords = state["keywords"]
        value_es_repository = runtime.context["value_es_repository"]

        # 1. LLM 扩展"可能出现在字段值里的词"
        extended = await expand_keywords_with_llm(
            "extend_keywords_for_value_recall", query
        )

        # 2. 并行检索 + 去重（具体并行化与异常隔离在 helper 里）
        retrieved_value_infos = await parallel_recall_dedup(
            keywords=keywords + extended,
            search_one=value_es_repository.search,
            dedup_key=lambda v: v.id,
            label="recall_value",
        )

        logger.info(f"检索到字段取值：{[v.id for v in retrieved_value_infos]}")
        writer({"type": "progress", "step": step, "status": "success"})
        return {"retrieved_value_infos": retrieved_value_infos}
    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise