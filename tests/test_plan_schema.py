# -*- coding: utf-8 -*-
"""
test_plan_schema.py
===================

QueryPlan / SubQuery Pydantic schema 的单元测试。

覆盖：
- 合法 plan（单 query、不拆分、拆分、含 depends_on）通过校验
- 边界条件（空 plan、超 5 个、id 不连续、depends_on 引用不存在 id、self-depends）
- 容错字段（extra="ignore"）
"""

import pytest
from pydantic import ValidationError

from app.entities.plan_schema import QueryPlan, SubQuery


class TestSubQuery:
    """SubQuery 字段测试"""

    def test_defaults(self):
        """id/query 必填，depends_on 默认空 list"""
        sq = SubQuery(id=0, query="查上月销售额")
        assert sq.id == 0
        assert sq.query == "查上月销售额"
        assert sq.depends_on == []

    def test_with_depends_on(self):
        sq = SubQuery(id=2, query="算增长率", depends_on=[0, 1])
        assert sq.depends_on == [0, 1]


class TestQueryPlanValid:
    """合法 QueryPlan 应该通过"""

    def test_single_sub_query(self):
        """单 sub_query（不拆，最常见）"""
        plan = QueryPlan(
            sub_queries=[
                SubQuery(id=0, query="华北上个月订单数"),
            ]
        )
        assert len(plan.sub_queries) == 1

    def test_three_sub_with_depends_on(self):
        """三段式：本月 + 上月 + 派生指标"""
        plan = QueryPlan(
            sub_queries=[
                SubQuery(id=0, query="本月销售额"),
                SubQuery(id=1, query="上月销售额"),
                SubQuery(id=2, query="环比增长率 = (本月-上月)/上月", depends_on=[0, 1]),
            ]
        )
        assert len(plan.sub_queries) == 3
        assert plan.sub_queries[2].depends_on == [0, 1]

    def test_parallel_no_depends(self):
        """无 depends_on = 全并行"""
        plan = QueryPlan(
            sub_queries=[
                SubQuery(id=0, query="5月 GMV"),
                SubQuery(id=1, query="6月 GMV"),
            ]
        )
        for sq in plan.sub_queries:
            assert sq.depends_on == []


class TestQueryPlanInvalid:
    """非法 QueryPlan 应该抛 ValidationError"""

    def test_empty_sub_queries(self):
        """空 plan 拒绝"""
        with pytest.raises(ValidationError, match="plan 不能为空"):
            QueryPlan(sub_queries=[])

    def test_too_many_sub_queries(self):
        """超过 5 个拒绝"""
        sgs = [SubQuery(id=i, query=f"sub {i}") for i in range(6)]
        with pytest.raises(ValidationError, match="超过上限 5"):
            QueryPlan(sub_queries=sgs)

    def test_ids_not_continuous(self):
        """id 不连续（0、2）拒绝"""
        with pytest.raises(ValidationError, match="必须从 0 开始连续递增"):
            QueryPlan(
                sub_queries=[
                    SubQuery(id=0, query="A"),
                    SubQuery(id=2, query="C"),  # 跳过 1
                ]
            )

    def test_duplicate_ids(self):
        """id 重复拒绝"""
        with pytest.raises(ValidationError, match="必须从 0 开始连续递增"):
            QueryPlan(
                sub_queries=[
                    SubQuery(id=0, query="A"),
                    SubQuery(id=0, query="B"),  # 重复
                ]
            )

    def test_depends_on_nonexistent_id(self):
        """depends_on 引用了不存在的 id 拒绝"""
        with pytest.raises(ValidationError, match="不存在的 id=5"):
            QueryPlan(
                sub_queries=[
                    SubQuery(id=0, query="A"),
                    SubQuery(id=1, query="B", depends_on=[5]),
                ]
            )

    def test_self_depends_on(self):
        """self-depends 拒绝"""
        with pytest.raises(ValidationError, match="self-depends"):
            QueryPlan(
                sub_queries=[
                    SubQuery(id=0, query="A", depends_on=[0]),
                ]
            )


class TestQueryPlanFaultTolerance:
    """Pydantic 容错（与 intent_schema 一致）"""

    def test_extra_fields_ignored(self):
        """LLM 输出多余字段不报错（extra='ignore'）"""
        # 通过 model_validate 模拟 LLM 输出可能带的多余字段
        data = {
            "sub_queries": [
                {"id": 0, "query": "A", "depends_on": [], "unexpected": "noise"},
            ],
            "extra_top": "ignore me",
        }
        plan = QueryPlan.model_validate(data)
        assert len(plan.sub_queries) == 1
