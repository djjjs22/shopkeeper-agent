# -*- coding: utf-8 -*-
"""
intent_schema.py
================

**为什么有这个文件**：
`generate_intent` 节点让 LLM 输出结构化 JSON（"查询意图"），下游 `generate_sql`
节点据此渲染 SQL 模板。

改前（2026-07-17 前）：
- 节点用 `SafeJsonOutputParser`（regex 剥 think 块后 `json.loads`）
- LLM 输出格式不可控：字段缺失、类型错误（如 `limit` 是字符串、缺失 `from`）都会
  让 `generate_sql` 渲染时炸 `KeyError` / `TypeError`
- 错误只能在 e2e 阶段发现，调试成本高

改后：
- 用 Pydantic `QueryIntent` 模型强校验 LLM 输出
- 解析失败 → 自动 retry 1 次 → 仍失败降级为空 intent（generate_sql 用 SELECT 1 兜底）
- 字段类型/必填项在 schema 里固化，e2e 前就能发现 schema 不匹配

**字段来源**：
与 `prompts/generate_intent.prompt` 的"JSON 结构示例"一致，详见 prompt 顶部说明。
"""

from typing import Literal

from pydantic import BaseModel, Field


class SelectExpr(BaseModel):
    """SELECT 子句中的一个表达式项

    例：{"expr": "SUM(fo.order_amount)", "alias": "销售额"}
    """

    expr: str = Field(description="聚合表达式或列名，如 SUM(fo.order_amount)、dr.province")
    alias: str = Field(description="结果列别名，作为 ORDER BY 引用目标")


class JoinClause(BaseModel):
    """JOIN 子句中的一个 JOIN 项

    例：{"type": "INNER", "table": "dim_region dr", "on": "fo.region_id = dr.region_id"}
    """

    # type 缺省时 sql_template 视作 INNER，这里也保持 INNER 默认
    type: Literal["INNER", "LEFT", "RIGHT"] = Field(
        default="INNER", description="JOIN 类型，缺省 INNER"
    )
    table: str = Field(description="目标表（含别名），如 dim_region dr")
    on: str = Field(description="JOIN 条件，如 fo.region_id = dr.region_id")


class QueryIntent(BaseModel):
    """结构化查询意图（对应 prompts/generate_intent.prompt 的 JSON 示例）

    关键设计：
    - `from_` 用下划线别名映射 JSON 的 `from`（Python 关键字）
    - 所有列表字段默认空 list（避免 LLM 缺字段时 KeyError）
    - limit 允许 int 或 None（不限制）
    - 不强制字段顺序（Pydantic v2 默认按声明顺序）
    """

    select: list[SelectExpr] = Field(
        default_factory=list, description="SELECT 表达式数组"
    )
    from_: str = Field(
        default="",
        alias="from",
        description='主表（含别名），如 "fact_order fo"',
    )
    joins: list[JoinClause] = Field(
        default_factory=list, description="JOIN 子句数组"
    )
    where: list[str] = Field(
        default_factory=list, description="WHERE 条件字符串数组"
    )
    group_by: list[str] = Field(
        default_factory=list, description="GROUP BY 字段数组"
    )
    order_by: list[str] = Field(
        default_factory=list, description='ORDER BY 字段或 "列名 DESC"'
    )
    limit: int | None = Field(default=None, description="LIMIT 数字，null 表示不限制")

    model_config = {
        # 允许 LLM 输出额外字段（如 thinking 残留），不报错
        "extra": "ignore",
        # populate_by_name 让 from_ 字段也能直接写 from
        "populate_by_name": True,
    }