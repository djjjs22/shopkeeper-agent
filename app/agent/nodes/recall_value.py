"""
字段取值召回节点

负责从字段值全文索引中召回候选取值
当用户问题里出现店铺名 类目名 地区名等业务值时，这一步可以帮助定位真实字段和值
实现路径和字段/指标召回不同：关键词扩展 -> Elasticsearch 全文检索 -> ValueInfo 去重

性能优化（asyncio.gather 并行化）：
  原实现：对 N 个关键词串行循环，每个关键词都要 es.search 一次
         8 个关键词 = 8 次串行 HTTP 往返
  现实现：用 asyncio.gather 把所有关键词的 ES 检索并行执行
         8 个关键词 = 1 轮并行往返
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
from app.entities.value_info import ValueInfo
from app.prompt.prompt_loader import load_prompt


async def recall_value(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """召回和用户问题相关的字段取值"""

    writer = runtime.stream_writer
    step = "召回字段取值"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        # query 用于让 LLM 生成字段值层面的检索词，keywords 来自上游通用关键词抽取
        query = state["query"]
        keywords = state["keywords"]
        # 字段取值更关注真实文本命中，因此这里走 Elasticsearch，而不是向量检索
        value_es_repository = runtime.context["value_es_repository"]

        # 用 LLM 把用户问法扩展成"可能出现在字段值里的词"
        # 例如"华北地区"可以补充出"华北"，避免 SQL 条件值和真实存储值不一致
        prompt = PromptTemplate(
            template=load_prompt("extend_keywords_for_value_recall"),
            input_variables=["query"],
        )
        # 字段值扩展 prompt 要求只输出 JSON 数组，解析后 result 就是 list[str]
        output_parser = JsonOutputParser()
        # LCEL 管道：填充提示词 -> 调用模型 -> 解析 JSON
        chain = prompt | llm | output_parser

        result = await chain.ainvoke({"query": query})

        # 通用关键词和字段值扩展词一起检索 ES，尽量提高真实取值召回率
        keywords = set(keywords + result)

        # ── 性能优化：asyncio.gather 并行化关键词循环 ──
        # ES 全文检索是 IO 密集型操作，并行化效果显著
        async def _search_one_keyword(keyword: str) -> list[ValueInfo]:
            """单个关键词的 ES 检索（并行执行单元，带 1 次重试）"""
            # ES 容器重启后首个请求可能 ConnectionError，重试 1 次（刀 13）
            return await retry_once(
                lambda: value_es_repository.search(keyword),
                label=f"recall_value:{keyword}",
            )

        # 并行发起所有关键词的检索，return_exceptions=True 防止单个失败导致全崩
        tasks = [_search_one_keyword(kw) for kw in keywords]
        all_results = await asyncio.gather(*tasks, return_exceptions=True)

        # 用 ValueInfo.id 去重，避免多个关键词命中同一条字段值记录
        value_infos_map: dict[str, ValueInfo] = {}
        for result_item in all_results:
            if isinstance(result_item, Exception):
                logger.warning(f"[recall_value] 关键词检索失败（跳过）: {result_item}")
                continue
            for current_value_info in result_item:
                if current_value_info.id not in value_infos_map:
                    value_infos_map[current_value_info.id] = current_value_info

        # 写回 state 的是去重后的字段值实体，后续合并节点再决定如何组织上下文
        retrieved_value_infos: list[ValueInfo] = list(value_infos_map.values())
        logger.info(f"检索到字段取值：{list(value_infos_map.keys())}")
        writer({"type": "progress", "step": step, "status": "success"})
        return {"retrieved_value_infos": retrieved_value_infos}
    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise
