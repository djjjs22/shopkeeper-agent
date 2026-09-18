# -*- coding: utf-8 -*-
"""
generate_intent 节点 helper 单元测试（2026-09-18 新增）

覆盖两个 helper:
- _date_plus_one_day: end_date 端点 → 下一天（SQL 左闭右开）
- _strip_redundant_time_conditions: 清掉 LLM 重复时间条件

不依赖 LLM / DB / 网络，纯逻辑测试。

跑法:
    cd /Users/lunasama/Downloads/Agent/shopkeeper-agent
    uv run python -m pytest tests/test_generate_intent_helpers.py -v
"""

import os
import sys

# 把项目根加到 sys.path（generate_intent 节点依赖较多,这里只测独立 helper）
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(THIS_DIR)
sys.path.insert(0, PROJECT_ROOT)

# 直接 import 节点模块（注意：会触发模块顶部 import，需要 log 等依赖）
try:
    from app.agent.nodes.generate_intent import (
        _date_plus_one_day,
        _strip_redundant_time_conditions,
    )
except ImportError as e:
    # 兜底：跳过整个文件（不是测试失败,是环境问题）
    import pytest
    pytest.skip(f"无法 import generate_intent 节点: {e}", allow_module_level=True)


class TestDatePlusOneDay:
    """end_date +1 天（端点 → 开区间）"""

    def test_q1_end_to_q2_start(self):
        """Q1 末日 2025-03-31 → Q2 首日 20250401"""
        assert _date_plus_one_day("2025-03-31") == "20250401"

    def test_q1_end_already_dash(self):
        """无横线 YYYYMMDD 同样工作"""
        assert _date_plus_one_day("20250331") == "20250401"

    def test_q4_year_boundary(self):
        """Q4 末日 2025-12-31 → 2026-01-01（跨年）"""
        assert _date_plus_one_day("2025-12-31") == "20260101"

    def test_february_non_leap(self):
        """非闰年 2 月 28 日 → 3 月 1 日"""
        assert _date_plus_one_day("2025-02-28") == "20250301"

    def test_february_leap(self):
        """闰年 2 月 28 日 → 还是 2-29（不跳月）"""
        assert _date_plus_one_day("2024-02-28") == "20240229"

    def test_february_leap_end(self):
        """闰年 2 月 29 日 → 3 月 1 日"""
        assert _date_plus_one_day("2024-02-29") == "20240301"

    def test_mid_month(self):
        """月中日期 +1"""
        assert _date_plus_one_day("2025-06-15") == "20250616"


class TestStripRedundantTimeConditions:
    """清掉 LLM 重复时间条件"""

    def test_strip_dd_year_filter(self):
        """dd.year = 2025 应被清掉"""
        where = ["dd.year = 2025", "dr.region_name = '华北'"]
        kept = _strip_redundant_time_conditions(where)
        assert kept == ["dr.region_name = '华北'"]

    def test_strip_dd_quarter_filter(self):
        """dd.quarter = 1 应被清掉"""
        where = ["dd.quarter = 1", "dr.region_name = '华北'"]
        kept = _strip_redundant_time_conditions(where)
        assert kept == ["dr.region_name = '华北'"]

    def test_strip_dd_quarter_string(self):
        """dd.quarter = 'Q1' 字符串形式也清掉"""
        where = ["dd.quarter = 'Q1'"]
        kept = _strip_redundant_time_conditions(where)
        assert kept == []

    def test_strip_year_function(self):
        """YEAR() 函数式（dim_date 没 full_date 字段,本身错）清掉"""
        where = ["YEAR(dd.full_date) = 2025"]
        kept = _strip_redundant_time_conditions(where)
        assert kept == []

    def test_strip_date_id_range(self):
        """date_id 区间由 LLM 写的也要清掉（程序性会重新注入）"""
        where = ["fo.date_id >= 20250101 AND fo.date_id < 20250401"]
        kept = _strip_redundant_time_conditions(where)
        assert kept == []

    def test_keep_unrelated_conditions(self):
        """无关的 where 条件不删"""
        where = [
            "dr.region_name = '华北'",
            "dc.member_level = '黄金'",
            "dp.category = '电子产品'",
        ]
        kept = _strip_redundant_time_conditions(where)
        assert kept == where

    def test_strip_month_filter(self):
        """dd.month = 5 应被清掉"""
        where = ["dd.month = 5", "dr.region_name = '华南'"]
        kept = _strip_redundant_time_conditions(where)
        assert kept == ["dr.region_name = '华南'"]

    def test_empty_list(self):
        """空列表不报错"""
        assert _strip_redundant_time_conditions([]) == []

    def test_q4_bug_reproduction(self):
        """复现 2026-09-18 Q4 0 行 bug:
        LLM 同时输出 date_id 区间 + dd.year/quarter,后者会冲突导致 0 行
        """
        where = [
            "dd.year = 2025",
            "dd.quarter = 4",
            "COUNT(fo.order_id)",
        ]
        kept = _strip_redundant_time_conditions(where)
        assert "dd.year = 2025" not in kept
        assert "dd.quarter = 4" not in kept
        # COUNT 是聚合不是过滤,会被原样保留（虽然这条不算时间条件,不会被剥）
        # 实际上 _REDUNDANT_TIME_PATTERNS 不匹配 COUNT,所以保留
        assert "COUNT(fo.order_id)" in kept
