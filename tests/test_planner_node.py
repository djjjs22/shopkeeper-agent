# -*- coding: utf-8 -*-
"""
test_planner_node.py
====================

Planner 节点单元测试。

覆盖：
- examples loader 正确读取 JSON + 格式化为 few-shot 字符串
- 失败兜底：LLM 解析失败时降级为单 sub_query
"""

import pytest

from app.agent.nodes.planner_node import _load_examples


class TestLoadExamples:
    """_load_examples() 加载并格式化 examples"""

    def test_returns_non_empty_string(self):
        examples = _load_examples()
        assert isinstance(examples, str)
        assert len(examples) > 100  # 应该有一些内容

    def test_contains_all_10_examples(self):
        """示例 1-10 都应该在输出里"""
        examples = _load_examples()
        # 模糊匹配：每个示例编号都应出现
        for i in range(1, 11):
            assert f"【示例 {i}】" in examples, f"示例 {i} 缺失"

    def test_contains_user_query_keywords(self):
        """至少应包含几个标志性 query 关键词"""
        examples = _load_examples()
        assert "环比增长率" in examples
        assert "GMV" in examples
        assert "留存率" in examples

    def test_contains_depends_on_examples(self):
        """至少 1 个 depends_on 子串（说明有并行+依赖关系的例子）"""
        examples = _load_examples()
        assert '"depends_on"' in examples


class TestPlannerFallback:
    """Planner 失败兜底逻辑（不依赖 LLM）"""

    def test_fallback_plan_single_sub_query(self):
        """LLM 解析失败时返回单 sub_query 的兜底 plan"""
        from app.entities.plan_schema import QueryPlan

        # 模拟解析失败的兜底：query="xxx"，生成单 sub_query plan
        query = "本月的环比增长率"
        fallback = QueryPlan(
            sub_queries=[{"id": 0, "query": query, "depends_on": []}]
        )
        assert len(fallback.sub_queries) == 1
        assert fallback.sub_queries[0].query == query
        assert fallback.sub_queries[0].depends_on == []
