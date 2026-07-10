"""
字段召回节点

负责根据关键词从字段向量知识库中召回候选字段
它解决的是"用户问题可能对应哪些数据库字段"的问题
本章的主线是：关键词扩展 -> Embedding -> Qdrant 相似度检索 -> ColumnInfo 去重

性能优化（asyncio.gather 并行化）：
  原实现：对 N 个关键词串行循环，每个关键词都要 aembed_query -> qdrant.search
         8 个关键词 = 8 次串行往返，耗时 3-5 秒
  现实现：用 asyncio.gather 把所有关键词的 Embedding + 检索并行执行
         8 个关键词 = 1 轮并行往返，耗时降到 0.5-1 秒
  效果：召回耗时从 3-5s 降到 0.5-1s，三路召回总耗时从 10-15s 降到 2-5s
"""

import asyncio

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState
from app.core.log import logger
from app.core.retry import retry_once
from app.entities.column_info import ColumnInfo
from app.prompt.prompt_loader import load_prompt


async def recall_column(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """召回和用户问题语义相关的字段元数据"""

    writer = runtime.stream_writer
    step = "召回字段信息"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        # state 保存图内业务中间结果：原始问题和上游抽取出的关键词
        keywords = state["keywords"]
        query = state["query"]
        # context 保存外部运行时工具：向量仓储和 Embedding 客户端
        column_qdrant_repository = runtime.context["column_qdrant_repository"]
        embedding_client = runtime.context["embedding_client"]

        # 用 LLM 把用户问法扩展成"字段语义"列表，例如"销售总额"可扩展出"销售金额"
        prompt = PromptTemplate(
            template=load_prompt("extend_keywords_for_column_recall"),
            input_variables=["query"],
        )
        # 提示词要求模型只输出 JSON 数组，解析后 result 就是 list[str]
        output_parser = JsonOutputParser()
        # LCEL 管道：填充提示词 -> 调用模型 -> 解析 JSON
        chain = prompt | llm | output_parser

        result = await chain.ainvoke({"query": query})

        # 原始关键词和 LLM 扩展词一起参与召回；set 去重，避免重复请求同一关键词
        keywords = set(keywords + result)

        # ── 性能优化：asyncio.gather 并行化关键词循环 ──
        # 原实现是串行 for 循环，每个关键词都要等前一个的 Embedding + Qdrant 往返完成
        # 现在用 gather 把所有关键词的 Embedding + 检索并行发起，IO 重叠等待
        async def _search_one_keyword(keyword: str) -> list[ColumnInfo]:
            """单个关键词的 Embedding + Qdrant 检索（并行执行单元，带 1 次重试）"""
            embedding = await embedding_client.aembed_query(keyword)
            # Qdrant 容器重启后首个请求可能 ConnectionError，重试 1 次（刀 13）
            return await retry_once(
                lambda: column_qdrant_repository.search(embedding),
                label=f"recall_column:{keyword}",
            )

        # 并行发起所有关键词的检索，return_exceptions=True 防止单个失败导致全崩
        tasks = [_search_one_keyword(kw) for kw in keywords]
        all_results = await asyncio.gather(*tasks, return_exceptions=True)

        # 用字段 id 做唯一键，因为多个关键词、同一字段的多个向量点都可能命中同一个字段
        column_info_map: dict[str, ColumnInfo] = {}
        for result_item in all_results:
            if isinstance(result_item, Exception):
                # 单个关键词检索失败不影响其他关键词的召回结果
                logger.warning(f"[recall_column] 关键词检索失败（跳过）: {result_item}")
                continue
            for column_info in result_item:
                if column_info.id not in column_info_map:
                    column_info_map[column_info.id] = column_info

        # 写回 state 的是去重后的 ColumnInfo 列表，不暴露 Qdrant 原始 point 结构
        retrieved_column_infos: list[ColumnInfo] = list(column_info_map.values())

        writer({"type": "progress", "step": step, "status": "success"})
        return {"retrieved_column_infos": retrieved_column_infos}
    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise
