# -*- coding: utf-8 -*-
"""
test_aggregator_node.py
=======================

Aggregator 节点 + _format_sub_results 辅助函数单元测试。

覆盖：
- 单 sub_query 路径：不调 LLM，直接生成 summary（不依赖外部 API）
- _format_sub_results 把 state 转成 LLM 可读文本
"""

import pytest

from app.agent.nodes.aggregator_node import _format_sub_results


class TestFormatSubResults:
    """_format_sub_results(state) → 字符串"""

    def test_empty_results(self):
        result = _format_sub_results({"sub_results": []})
        assert "无 sub_query 结果" in result

    def test_single_sub_with_rows(self):
        state = {
            "sub_results": [
                {
                    "sub_id": 0,
                    "query": "本月销售额",
                    "sql": "SELECT SUM(amount) ...",
                    "columns": ["month", "amount"],
                    "rows": [{"month": "2026-01", "amount": 1000}],
                    "error": None,
                }
            ]
        }
        text = _format_sub_results(state)
        assert "sub_query #0" in text
        assert "本月销售额" in text
        assert "1 行" in text
        assert "month" in text

    def test_three_subs_with_one_failure(self):
        state = {
            "sub_results": [
                {"sub_id": 0, "query": "A", "sql": "SELECT 1", "columns": [], "rows": [], "error": None},
                {"sub_id": 1, "query": "B", "sql": "SELECT 2", "columns": [], "rows": [], "error": "timeout"},
                {"sub_id": 2, "query": "C", "sql": "SELECT 3", "columns": [], "rows": [], "error": None},
            ]
        }
        text = _format_sub_results(state)
        assert "#0" in text
        assert "#1" in text
        assert "#2" in text
        assert "timeout" in text

    def test_truncates_long_sql(self):
        """SQL 长于 200 字符要截断（避免 prompt 过长）"""
        state = {
            "sub_results": [
                {"sub_id": 0, "query": "X", "sql": "SELECT " + "a" * 500,
                 "columns": [], "rows": [], "error": None}
            ]
        }
        text = _format_sub_results(state)
        # 截断到 200 字符以内（包括 "SQL: " prefix）
        # 找 "SQL:" 行
        sql_line = [ln for ln in text.split("\n") if "SQL:" in ln][0]
        assert len(sql_line) < 250  # 200 + SQL: 前缀 + 空格
