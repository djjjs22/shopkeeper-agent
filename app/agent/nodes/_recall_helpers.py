# -*- coding: utf-8 -*-
"""
Recall 节点公共 helper（P2 #8：DRY 重构）

3 个 recall 节点（recall_column / recall_metric / recall_value）原本各自重复：
  1. PromptTemplate + llm + JsonOutputParser 做关键词扩展
  2. 通用关键词 + LLM 扩展词 set 去重
  3. asyncio.gather 并行检索（return_exceptions=True）
  4. 单个关键词检索失败仅 warning，不阻断其他关键词
  5. 按 id 去重写入 state

差异只在：扩展 prompt 名称、单关键词检索函数、去重字段、最终 state key。
抽到这里后 3 个节点各自只剩 ~15 行业务代码，可读性和复用性都更好。

新增召回维度（如业务术语召回）只需写 search_one 函数 + 注册到节点里，无需复制模板。
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from langchain_core.prompts import PromptTemplate

from app.agent.llm import llm
from app.core.log import logger
# 2026-07-11 改造：JsonOutputParser → SafeJsonOutputParser
# 场景：三路召回的关键词扩展（M3 模型 think 污染）
# 详见 app/core/safe_json_parser.py 顶部 + docs/notes/eval_e2e_think兼容改造-20260711.md
from app.core.safe_json_parser import SafeJsonOutputParser
from app.prompt.prompt_loader import load_prompt

T = TypeVar("T")


async def expand_keywords_with_llm(
    prompt_name: str,
    query: str,
) -> list[str]:
    """用 LLM 把用户原始问题扩展成指定维度的检索词列表

    改前（2026-07-14 P1 前）：
      只把原始 query 喂给 LLM，LLM 自己"猜"业务同义词（"销售额"是不是"GMV"），猜错率高。
    改后（2026-07-14 P1）：
      先用 synonyms_service.expand_query() 把 query 里的同义词扩展出来
      （"销售额" → "销售额 GMV 营业额 成交总额 order_amount"），
      再喂给 LLM。LLM 看到的是已经扩展的 query，扩展关键词时不会再漏别名。

    Args:
        prompt_name: 扩展 prompt 文件名（不带 .prompt 后缀），
            如 "extend_keywords_for_column_recall"
        query: 用户原始问题

    Returns:
        LLM 生成的关键词列表（JSON 数组）。LLM 输出异常时降级为空列表，
        不阻断后续基于通用关键词的召回。
    """
    # ── P1 改造：先用同义词服务扩展 query ──
    # 导入放在函数内（避免循环依赖）
    from app.services.synonyms_service import expand_query

    expanded_query = expand_query(query)
    logger.debug(
        f"[expand_keywords_with_llm:{prompt_name}] 同义词扩展: "
        f"{query!r} → {expanded_query!r}"
    )

    prompt = PromptTemplate(
        template=load_prompt(prompt_name),
        input_variables=["query"],
    )
    # 所有 extend_keywords_for_*_recall prompt 都要求只输出 JSON 数组
    # 用 SafeJsonOutputParser 兼容 M3/DeepSeek 的 <think>...</think> 块
    chain = prompt | llm | SafeJsonOutputParser()

    try:
        # 用扩展后的 query 喂给 LLM（不是原始 query）
        result = await chain.ainvoke({"query": expanded_query})
        # 防御性：LLM 偶发输出非 list 时降级为空，避免下游崩溃
        if not isinstance(result, list):
            logger.warning(
                f"[expand_keywords_with_llm:{prompt_name}] LLM 返回非 list（{type(result)}），降级为空"
            )
            return []
        return [str(item) for item in result]
    except Exception as exc:
        # LLM 输出解析失败时降级，保留原始 keywords 走兜底
        logger.warning(
            f"[expand_keywords_with_llm:{prompt_name}] 扩展失败，使用空列表: {exc}"
        )
        return []


async def parallel_recall_dedup(
    keywords: list[str],
    search_one: Callable[[str], Awaitable[list[T]]],
    dedup_key: Callable[[T], str],
    label: str,
) -> list[T]:
    """并行检索多个关键词，按 dedup_key 去重返回

    Args:
        keywords: 合并后的关键词列表（去重前）
        search_one: 单个关键词的检索函数，工厂方式传入以便重试工具重新创建协程
        dedup_key: 实体去重字段（如 lambda c: c.id）
        label: 日志前缀，便于定位是哪个节点/哪个关键词出问题

    Returns:
        去重后的实体列表（顺序按首次出现）
    """
    # ── 性能优化：asyncio.gather 并行化关键词循环 ──
    # 所有外部调用（embedding / qdrant / es）都是 IO 密集型，并行化收益显著
    tasks = [_safe_search(search_one, kw, label) for kw in keywords]
    all_results = await asyncio.gather(*tasks, return_exceptions=True)

    # 用去重 key 做唯一键，多关键词命中同一条实体只保留一份
    seen: dict[str, T] = {}
    for result_item in all_results:
        if isinstance(result_item, Exception):
            # 单个关键词检索失败不影响其他关键词的召回结果
            logger.warning(f"[{label}] 关键词检索失败（跳过）: {result_item}")
            continue
        for item in result_item:
            key = dedup_key(item)
            if key not in seen:
                seen[key] = item
    return list(seen.values())


async def _safe_search(
    search_one: Callable[[str], Awaitable[list[T]]],
    keyword: str,
    label: str,
) -> list[T]:
    """search_one 的统一包装，方便日后插入重试/超时/埋点

    当前仅传递 keyword + 透传异常给 asyncio.gather(return_exceptions=True)；
    留这个 wrapper 是为了将来加 retry / tracing 时只改一处。
    """
    return await search_one(keyword)