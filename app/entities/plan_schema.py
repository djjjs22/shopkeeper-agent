# -*- coding: utf-8 -*-
"""
plan_schema.py
==============

Multi-Agent 改造（2026-07-17）的 Pydantic Schema 定义。

**为什么有这个文件**：
Planner 节点把用户 query 拆成 N 个 sub_query 后，下游需要结构化数据
（带 id、depends_on 等字段）来调度。每个 sub_query 后续会：
1. 被改写成 SQL（走现有 generate_intent → sql_template 渲染链路）
2. 在 Send() API 里按 depends_on 图并行/串行执行

如果用 dict 传 dict，下游每读一个字段都怕拼错。改用 Pydantic BaseModel：

- 字段类型在 schema 里固化，LLM 偶尔输出多余字段不报错（extra="ignore"）
- depends_on ID 必须有效（model_validator 自动校验）
- 单测可以独立测试 schema 校验逻辑（不依赖 LLM）

**与 intent_schema.py 的关系**：
- intent_schema.py：单 query 输出（generate_intent 节点用）
- plan_schema.py：复杂 query 拆解（planner 节点用）
两者都是 Pydantic v2 模式，结构类似但不通用——一个描述 SQL intent，
一个描述 multi-agent 执行计划。
"""

from pydantic import BaseModel, Field, model_validator


class SubQuery(BaseModel):
    """一个独立的可执行 sub_query（最终会被单独跑 SQL）"""

    id: int = Field(description="sub_query 编号，从 0 连续递增")
    query: str = Field(description="这个 sub_query 要查什么（自然语言）")
    depends_on: list[int] = Field(
        default_factory=list,
        description="依赖的其他 sub_query id 列表；空表示无依赖可并行",
    )


class QueryPlan(BaseModel):
    """Planner 输出的完整执行计划"""

    sub_queries: list[SubQuery] = Field(
        default_factory=list,
        description="拆解后的 sub_query 列表",
    )

    @model_validator(mode="after")
    def validate_plan(self) -> "QueryPlan":
        """校验：
        1. ids 必须从 0 开始连续（不能跳号或重复）
        2. depends_on 里所有 id 必须存在
        3. 不能自我依赖（id 不能在自己 depends_on 里）
        4. 拆得不能太碎（上限 5 个）
        """
        if not self.sub_queries:
            raise ValueError("plan 不能为空（sub_queries 至少 1 个）")

        if len(self.sub_queries) > 5:
            # 拆太碎也是 bad signal —— LLM 可能在 hallucinate
            # 经验值：5 已经覆盖大部分业务复杂场景
            raise ValueError(
                f"sub_queries 数量 {len(self.sub_queries)} 超过上限 5，"
                f"说明 LLM 拆得太碎。请合并。"
            )

        ids = [sq.id for sq in self.sub_queries]

        # 1. ids 必须从 0 开始连续
        expected = list(range(len(self.sub_queries)))
        if ids != expected:
            raise ValueError(
                f"sub_query id 必须从 0 开始连续递增，实际 {ids}"
            )

        # 2 + 3. depends_on 合法性 + 不能自依赖
        all_ids_set = set(ids)
        for sq in self.sub_queries:
            for dep in sq.depends_on:
                if dep not in all_ids_set:
                    raise ValueError(
                        f"sub_query {sq.id} depends_on 引用了不存在的 id={dep}"
                    )
            if sq.id in sq.depends_on:
                raise ValueError(
                    f"sub_query {sq.id} 不能 self-depends (depends_on 含自己)"
                )

        return self

    # Pydantic v2 容错配置（与 intent_schema 保持一致）
    model_config = {
        "extra": "ignore",          # LLM 偶尔输出多余字段不报错
        "populate_by_name": True,   # 兼容 alias
    }
