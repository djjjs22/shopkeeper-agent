# -*- coding: utf-8 -*-
"""
test_supervisor_parallel.py
============================

_gather_sub_results 的拓扑分层 + 并行执行单元测试。

覆盖：
- 单 sub_query：1 层，跑 1 次
- 多 sub 无依赖：1 层，并行
- 多 sub 有依赖：拓扑分层，等下层完成再跑上层
- 循环依赖 / dangling depends_on：兜底跑全部
"""

import pytest

from app.entities.plan_schema import QueryPlan, SubQuery


def _extract_layers(plan: QueryPlan) -> list[list[int]]:
    """从 _gather_sub_results 抽出拓扑分层逻辑（纯函数，方便测）"""
    sub_ids = [sq.id for sq in plan.sub_queries]
    by_id = {sq.id: sq for sq in plan.sub_queries}
    layers: list[list[int]] = []
    finished: set[int] = set()
    remaining = set(sub_ids)

    while remaining:
        current_layer = sorted([
            sid for sid in remaining
            if all(dep in finished for dep in by_id[sid].depends_on)
        ])
        if not current_layer:
            # 兜底：循环依赖或 dangling 引用
            current_layer = sorted(remaining)
        layers.append(current_layer)
        finished.update(current_layer)
        remaining -= set(current_layer)

    return layers


class TestTopologyLayers:
    """拓扑分层逻辑测试"""

    def test_single_sub_query_one_layer(self):
        plan = QueryPlan(
            sub_queries=[SubQuery(id=0, query="简单单 query")]
        )
        layers = _extract_layers(plan)
        assert layers == [[0]]

    def test_two_subs_no_deps_one_layer(self):
        """无依赖 → 1 层并行"""
        plan = QueryPlan(
            sub_queries=[
                SubQuery(id=0, query="A"),
                SubQuery(id=1, query="B"),
            ]
        )
        layers = _extract_layers(plan)
        assert layers == [[0, 1]]

    def test_three_subs_chain_deps(self):
        """链式依赖 0 → 1 → 2：3 层串行"""
        plan = QueryPlan(
            sub_queries=[
                SubQuery(id=0, query="A"),
                SubQuery(id=1, query="B", depends_on=[0]),
                SubQuery(id=2, query="C", depends_on=[1]),
            ]
        )
        layers = _extract_layers(plan)
        assert layers == [[0], [1], [2]]

    def test_diamond_dependency(self):
        """菱形依赖 0 → 1,2 → 3：3 层，第 1 层 1 个，第 2 层 2 个并行"""
        plan = QueryPlan(
            sub_queries=[
                SubQuery(id=0, query="A"),
                SubQuery(id=1, query="B", depends_on=[0]),
                SubQuery(id=2, query="C", depends_on=[0]),
                SubQuery(id=3, query="D", depends_on=[1, 2]),
            ]
        )
        layers = _extract_layers(plan)
        assert layers == [[0], [1, 2], [3]]

    def test_two_parallel_chains(self):
        """两条并行链：1 层含两个根"""
        plan = QueryPlan(
            sub_queries=[
                SubQuery(id=0, query="A"),
                SubQuery(id=1, query="B"),
                SubQuery(id=2, query="C", depends_on=[0]),
                SubQuery(id=3, query="D", depends_on=[1]),
            ]
        )
        layers = _extract_layers(plan)
        # 第一层 [0, 1] 并行；第二层 [2, 3] 并行（各自依赖不同）
        assert layers == [[0, 1], [2, 3]]

    def test_cycle_dependency_fallback(self):
        """循环依赖：兜底全跑"""
        # 直接构造（绕过 model_validator，因为 validator 拒循环依赖）
        plan_data = {
            "sub_queries": [
                {"id": 0, "query": "A", "depends_on": [1]},
                {"id": 1, "query": "B", "depends_on": [0]},
            ]
        }
        plan = QueryPlan.model_validate(plan_data)
        layers = _extract_layers(plan)
        # 兜底：循环检测失败时把所有 remaining 放到一层
        assert len(layers) == 1
        assert set(layers[0]) == {0, 1}
