# -*- coding: utf-8 -*-
"""
字段召回节点

负责根据关键词从字段向量知识库中召回候选字段
它解决的是"用户问题可能对应哪些数据库字段"的问题
实现路径：关键词扩展 -> Embedding -> Qdrant 相似度检索 -> ColumnInfo 去重

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
from app.entities.column_info import ColumnInfo


@timed_node
async def recall_column(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """召回和用户问题语义相关的字段元数据"""

    writer = runtime.stream_writer
    step = "召回字段信息"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        query = state["query"]
        keywords = state["keywords"]
        # 2026-07-20 改造（#16）：从 state 读 extract_keywords 节点已经算好的扩展词，
        # 不再各自调 LLM（节省 2 次调用 / 成本 -25%）
        extended = state.get("extended_keywords_by_dim", {}).get("column", [])
        column_qdrant_repository = runtime.context["column_qdrant_repository"]
        embedding_client = runtime.context["embedding_client"]

        # 构造"单关键词→embedding→qdrant"的检索单元
        # 重试工具要求传入协程工厂（lambda），每次重新创建协程避免已消费
        async def _search_one_keyword(keyword: str) -> list[ColumnInfo]:
            embedding = await embedding_client.aembed_query(keyword)
            # Qdrant 容器重启后首个请求可能 ConnectionError，重试 1 次（刀 13）
            return await retry_once(
                lambda: column_qdrant_repository.search(embedding),
                label=f"recall_column:{keyword}",
            )

        # 并行检索 + 去重
        retrieved_column_infos = await parallel_recall_dedup(
            keywords=keywords + extended,
            search_one=_search_one_keyword,
            dedup_key=lambda c: c.id,
            label="recall_column",
        )

        writer({"type": "progress", "step": step, "status": "success"})
        return {"retrieved_column_infos": retrieved_column_infos}
    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise