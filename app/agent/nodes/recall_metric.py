# -*- coding: utf-8 -*-
"""
指标召回节点

负责根据用户问题从指标向量知识库中召回候选指标
它帮助 Agent 把"销售额 转化率 客单价"等业务表达映射到已定义指标
实现路径：关键词扩展 -> Embedding -> Qdrant 相似度检索 -> MetricInfo 去重

性能优化（asyncio.gather 并行化，P2 #8 重构后）：
  N 个关键词的 Embedding + Qdrant 检索并行发起，IO 重叠等待
  详见 _recall_helpers.py
"""

from app.core.timing import timed_node
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.nodes._recall_helpers import (
    parallel_recall_dedup,
)
from app.agent.state import DataAgentState
from app.core.log import logger
from app.core.retry import retry_once
from app.entities.metric_info import MetricInfo


@timed_node
async def recall_metric(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """召回和用户问题语义相关的业务指标"""

    writer = runtime.stream_writer
    step = "召回指标信息"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        query = state["query"]
        keywords = state["keywords"]
        # 2026-07-20 改造（#16）：从 state 读 extract_keywords 节点已经算好的扩展词，
        # 不再各自调 LLM（节省 2 次调用 / 成本 -25%）
        extended = state.get("extended_keywords_by_dim", {}).get("metric", [])
        embedding_client = runtime.context["embedding_client"]
        metric_qdrant_repository = runtime.context["metric_qdrant_repository"]

        # 构造"单关键词→embedding→qdrant"的检索单元（带 1 次重试，刀 13）
        async def _search_one_keyword(keyword: str) -> list[MetricInfo]:
            embedding = await embedding_client.aembed_query(keyword)
            return await retry_once(
                lambda: metric_qdrant_repository.search(embedding),
                label=f"recall_metric:{keyword}",
            )

        # 并行检索 + 去重
        retrieved_metric_infos = await parallel_recall_dedup(
            keywords=keywords + extended,
            search_one=_search_one_keyword,
            dedup_key=lambda m: m.id,
            label="recall_metric",
        )

        logger.info(f"检索到指标信息：{[m.id for m in retrieved_metric_infos]}")
        writer({"type": "progress", "step": step, "status": "success"})
        return {"retrieved_metric_infos": retrieved_metric_infos}
    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise