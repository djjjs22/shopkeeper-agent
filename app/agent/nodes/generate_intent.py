# -*- coding: utf-8 -*-
"""
查询意图生成节点（RFC 刀1 改造：大模型角色压缩）

为什么有这个节点
================
改前（2026-07-14 前）：
  `generate_sql` 节点让 LLM 一次性干两件事：
    1. 理解业务意图（哪些表、哪些条件、哪些聚合）
    2. 写 SQL 语法（SELECT 关键字、JOIN 顺序、缩进）
  这违反 "LLM 角色压缩" 原则 —— 让 LLM 干确定性工作（写 SQL 语法）会
  引入格式不一致、缩进差异、关键字大小写等不稳定因素。

改后（2026-07-14 后）：
  - 本节点（`generate_intent`）只让 LLM 干"语义层"工作
    输入：用户问题 + 表结构 + 指标定义 + 时间范围 + 历史继承
    输出：结构化 JSON intent（业务意图）
  - `generate_sql` 节点变成"渲染节点"（只调 sql_template.render_sql）
  - 好处：见 prompts/generate_intent.prompt 顶部说明

输入上下文来源
==============
- state["query"]: 用户原句（永不被改写，参见 rewrite_query 节点）
- state["time_range"]: 结构化时间范围（2026-07-14 改造后由 rewrite_query 提供）
- state["inherited_from_history"]: 从历史对话继承的实体/条件/维度
- state["table_infos"]: filter_table 节点输出的精简表结构
- state["metric_infos"]: filter_metric 节点输出的精简指标
- state["date_info"] / state["db_info"]: 来自 add_extra_context 节点

输出
====
- state["query_intent"]: dict，schema 见 sql_template.py 顶部 docstring
- 失败时降级为空 intent（generate_sql 节点会用 SELECT 1 兜底）
"""

import yaml
from langchain_core.prompts import PromptTemplate
from app.core.timing import timed_node
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import get_llm
from app.agent.state import DataAgentState, InheritedContext, TimeRangeState
from app.core.log import logger
from app.core.pydantic_parser import PydanticIntentParser
from app.core.retry import retry_once

# 2026-07-17 改造：SafeJsonOutputParser → PydanticIntentParser
# 动机：SafeJsonOutputParser 只剥 think + json.loads，下游读取字段时无类型保护
# 改后：先剥 think 块 + 抓围栏，再 Pydantic QueryIntent 强校验
# 失败 retry 1 次，仍失败降级空 intent（generate_sql 节点用 SELECT 1 兜底）
from app.entities.intent_schema import QueryIntent
from app.prompt.prompt_loader import load_prompt

# 节点级 parser 单例（无状态，可复用）
_intent_parser = PydanticIntentParser(pydantic_object=QueryIntent)


# ─────────────────────────────────────────────────────────────────────
# 辅助：把结构化字段格式化成 prompt 可读文本
# ─────────────────────────────────────────────────────────────────────

def _format_time_range_for_prompt(time_range: TimeRangeState | None) -> str:
    """time_range → 可读文本

    无显式时间时返回 "无"，让 LLM 知道不写时间条件。
    """
    if not time_range or not time_range.get("raw_expression"):
        return "无"
    return (
        f"原始表达: {time_range['raw_expression']}\n"
        f"起止日期: {time_range['start_date']} 至 {time_range['end_date']}"
    )


def _format_inherited_for_prompt(inherited: InheritedContext | None) -> str:
    """inherited_from_history → 可读文本

    把实体/条件/维度三类继承信息格式化成 LLM 易读的形式。
    """
    if not inherited:
        return "无"
    parts = []
    if inherited.get("entities"):
        parts.append(f"实体: {', '.join(inherited['entities'])}")
    if inherited.get("conditions"):
        parts.append(f"条件: {' AND '.join(inherited['conditions'])}")
    if inherited.get("dimensions"):
        parts.append(f"维度: {', '.join(inherited['dimensions'])}")
    return "; ".join(parts) if parts else "无"


def _format_business_rules_for_prompt(query: str) -> str:
    """根据 query 匹配业务规则，格式化成 LLM prompt 可读文本

    2026-07-14 P2 改造：把业务规则引擎接入 generate_intent 节点。
    改前：所有 WHERE 条件靠 LLM 拼，业务规则（如"已付款"=什么状态）容易拼错。
    改后：先在 Python 里匹配业务规则，把 WHERE 条件直接喂给 LLM。
         LLM 必须**直接用**这些条件，不准改写（prompt 里会强调）。

    2026-07-15 P2 废弃：远端 schema_resolver 提供更深的表字段查询能力，
    业务规则改成"在 metric_resolver 里查"。删掉 rule_service 后本函数固定返回"无"，
    保留函数定义只是为了让 generate_intent 的 prompt 变量 `business_rules` 不报错。
    """
    return "无"


# ─────────────────────────────────────────────────────────────────────
# 节点主体
# ─────────────────────────────────────────────────────────────────────

@timed_node
async def generate_intent(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """调用 LLM 把用户问题 + 上下文转换为结构化 JSON intent

    关键设计：
    - 失败的兜底：返回空 intent（不抛异常），让下游 generate_sql 用 SELECT 1 兜底
    - prompt 严格约束 LLM "只输出 JSON，不输出 SQL"（见 generate_intent.prompt）
    - 用 PydanticIntentParser 兼容 M3/DeepSeek 的 think 块
    """
    llm = get_llm("generate_intent")  # 按 node_profiles 路由

    writer = runtime.stream_writer
    step = "生成查询意图"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        # 读取所有需要的上下文
        query = state["query"]
        table_infos = state["table_infos"]
        metric_infos = state["metric_infos"]
        date_info = state["date_info"]
        db_info = state["db_info"]
        time_range = state.get("time_range", TimeRangeState(
            start_date="", end_date="", raw_expression=""
        ))
        inherited = state.get("inherited_from_history")
        # P2 改造：根据 query 匹配业务规则（已付款/华北/黄金会员等）
        business_rules = _format_business_rules_for_prompt(query)

        # 构造 prompt
        # 2026-07-17 改造：f-string → jinja2
        # 动机：get_format_instructions() 拼接的 JSON Schema 含嵌套 {...{...}...}，
        #       f-string 模板不允许嵌套替换字段，会抛 "Nested replacement fields are not allowed"，
        #       导致 generate_intent 节点直接 fallback 为空 intent。
        #       改 jinja2 后 JSON 字面量原样写（单层 {...}），变量用 {{ var }}。
        prompt = PromptTemplate(
            template=load_prompt("generate_intent") + _intent_parser.get_format_instructions(),
            template_format="jinja2",
            input_variables=[
                "table_infos",
                "metric_infos",
                "date_info",
                "db_info",
                "time_range",
                "inherited_context",
                "business_rules",  # P2 新增
                "query",
            ],
        )
        # 用 PydanticIntentParser 强校验：剥 think + 抓围栏 → model_validate
        chain = prompt | llm | _intent_parser

        # 解析失败时 retry 1 次（保持原有"降级空 intent"兜底行为）
        try:
            intent_obj = await retry_once(
                coro_factory=lambda: chain.ainvoke({
                    # YAML 序列化表结构，保留嵌套 + 中文
                    "table_infos": yaml.dump(table_infos, allow_unicode=True, sort_keys=False),
                    "metric_infos": yaml.dump(metric_infos, allow_unicode=True, sort_keys=False),
                    "date_info": yaml.dump(date_info, allow_unicode=True, sort_keys=False),
                    "db_info": yaml.dump(db_info, allow_unicode=True, sort_keys=False),
                    "time_range": _format_time_range_for_prompt(time_range),
                    "inherited_context": _format_inherited_for_prompt(inherited),
                    "business_rules": business_rules,  # P2 新增
                    "query": query,
                }),
                label=f"{step}:LLM+Pydantic 解析",
                max_retries=1,
            )
        except Exception as e:
            # 解析失败（含 retry 后仍失败）：降级空 dict，让下游 generate_sql 用 SELECT 1 兜底
            logger.error(f"{step}: 解析失败（已 retry 1 次），降级为空 intent: {e}")
            intent = {}
        else:
            # Pydantic 强校验通过：model_dump(by_alias=True) 还原 from 字段名
            intent = intent_obj.model_dump(by_alias=True, exclude_none=False)

        # 防御性：理论上 model_dump 后一定是 dict，保留 isinstance 检查兜底
        if not isinstance(intent, dict):
            logger.warning(
                f"{step}: intent 非 dict（实际 {type(intent)}），降级为空 intent"
            )
            intent = {}

        logger.info(f"{step}: 生成的 intent keys = {list(intent.keys())}")

        # 2026-07-17 修复 P0：time_range 非空时程序性注入 WHERE 条件
        # 改前问题：LLM 拿不到 time_range（被 _format_time_range_for_prompt 兜底成空），
        #   或 LLM 拿到 time_range 但拼错字段（fact_order 没有 order_date，只有 date_id）。
        #   导致 validate_sql 报 Unknown column，correct_sql 又拿不到表元数据修复，
        #   最终 run_sql 拦截返回空。
        # 改后：time_range 非空时**程序性**把 `date_id BETWEEN start AND end` 注入
        #   intent["where"] 头部，**不依赖 LLM 拼时间条件**。
        # 2026-07-17 业务约束：仅对 dim_date 关联的 fact 表（fact_order）注入，
        #   防止给非时间维表加错条件。
        if time_range and time_range.get("start_date") and time_range.get("end_date"):
            where_list = intent.get("where")
            if not isinstance(where_list, list):
                where_list = []
            start_dash = time_range["start_date"].replace("-", "")
            end_dash = time_range["end_date"].replace("-", "")
            time_clause = (
                f"fact_order.date_id BETWEEN {start_dash} AND {end_dash}"
            )
            # 头部插入（避免 LLM 已拼的 where 把它夹在中间）
            where_list.insert(0, time_clause)
            intent["where"] = where_list
            logger.info(
                f"{step}: 程序性注入时间条件 {time_clause}（不依赖 LLM）"
            )

        writer({"type": "progress", "step": step, "status": "success"})
        return {"query_intent": intent}

    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        # 失败时返回空 intent（generate_sql 节点会用 SELECT 1 兜底）
        return {"query_intent": {}}
