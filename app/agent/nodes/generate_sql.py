# -*- coding: utf-8 -*-
"""
SQL 生成节点（RFC 刀1 改造：大模型角色压缩 / v2 版本）

为什么改这个文件
================
改前（2026-07-14 前）：
  本节点让 LLM 一次性干两件事：
    1. 理解业务意图（哪些表、哪些条件、哪些聚合）
    2. 写 SQL 语法（SELECT 关键字、JOIN 顺序、缩进）
  违反 "LLM 角色压缩" 原则。

改后（2026-07-14 后）：
  - 业务理解工作由 `generate_intent` 节点（LLM）完成
  - 本节点**只做 SQL 渲染**：消费 `state['query_intent']`，调用 sql_template 渲染成 SQL
  - 关键约束：**本节点不调 LLM**，纯确定性代码

输入
====
- state["query_intent"]: dict，来自 generate_intent 节点，schema 见 sql_template.py

输出
====
- state["sql"]: str，渲染后的 SQL

失败兜底
========
- query_intent 为空 dict / None → 渲染出 "SELECT 1 AS fallback"
- 模板渲染抛异常 → sql_template 已兜底返回 SELECT 1
"""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger
# 2026-07-14 改造：generate_sql 不再调 LLM，移除 llm/prompt_loader 依赖
# 2026-07-11 改造：StrOutputParser → StripThinkStrParser（已不再需要）
from app.services.sql_template import render_sql


async def generate_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """消费 query_intent，渲染成 SQL

    本节点不调 LLM（2026-07-14 改造）。如果未来需要"自然语言 SQL 重写"等
    高级功能，应该新建节点，不要在这个节点加 LLM 调用 —— 会破坏
    "LLM 角色压缩" 原则。
    """
    writer = runtime.stream_writer
    step = "生成SQL"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        # 1. 读取 intent（必须字段，没有就空 dict → 渲染出 SELECT 1 兜底）
        intent = state.get("query_intent", {})

        # 2. 渲染 SQL（确定性代码，不调 LLM）
        sql = render_sql(intent)

        logger.info(f"生成的SQL：{sql[:200]}{'...' if len(sql) > 200 else ''}")
        writer({"type": "progress", "step": step, "status": "success"})
        return {"sql": sql}

    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        # 即使抛异常也返回兜底 SQL，不阻断下游 validate_sql
        return {"sql": "SELECT 1 AS fallback"}
