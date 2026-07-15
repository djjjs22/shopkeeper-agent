# -*- coding: utf-8 -*-
"""
SQL 模板渲染器单元测试（2026-07-14 新增）

为什么有这个文件
================
generate_sql 节点改纯渲染后，SQL 模板渲染器（sql_template.py）变成了
链路核心组件。本文件用一组已知的 intent → 已知 SQL 的样例验证模板正确性。

跑法
====
项目要求 Python >=3.14，本地只有 3.13 也能跑（sql_template 依赖的 jinja2 装上即可）：
    /Users/lunasama/.workbuddy/binaries/python/versions/3.13.12/bin/python3 -m pytest tests/test_sql_template.py -v

测试样例
========
- 正常 case：单表聚合 / 多表 JOIN / 复杂 WHERE / 继承的 SKU 列表
- 兜底 case：空 intent / None / 字段类型异常
"""

import importlib.util
import os
import sys

# 直接加载 sql_template 模块（不依赖项目其他依赖）
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(os.path.dirname(THIS_DIR), "app", "services", "sql_template.py")


def _load_module():
    """动态加载 sql_template.py"""
    spec = importlib.util.spec_from_file_location("sql_template", TEMPLATE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _test_case(label: str, intent: dict, expect: str):
    """通用测试：渲染 SQL，验证包含 expect 字符串"""
    mod = _load_module()
    sql = mod.render_sql(intent)
    if expect in sql:
        print(f"✅ {label}")
        return True
    else:
        print(f"❌ {label}\n   expect: {expect}\n   got:    {sql[:200]}")
        return False


def run_all():
    """跑所有测试"""
    cases = [
        # ── 正常 case ──
        (
            "单表聚合 + 显式时间范围",
            {
                "select": [{"expr": "SUM(fo.order_amount)", "alias": "销售额"}],
                "from": "fact_order fo",
                "joins": [
                    {"type": "INNER", "table": "dim_region dr", "on": "fo.region_id = dr.region_id"}
                ],
                "where": [
                    "dr.region_name = '华北'",
                    "fo.order_date BETWEEN '2025-12-01' AND '2025-12-31'",
                ],
                "group_by": [],
                "order_by": [],
                "limit": None,
            },
            "FROM fact_order fo",
        ),
        (
            "多表 JOIN + 分组聚合",
            {
                "select": [
                    {"expr": "dp.category", "alias": "商品品类"},
                    {"expr": "SUM(fo.order_amount)", "alias": "销售额"},
                ],
                "from": "fact_order fo",
                "joins": [
                    {"type": "INNER", "table": "dim_product dp", "on": "fo.product_id = dp.product_id"}
                ],
                "where": [],
                "group_by": ["dp.category"],
                "order_by": ["销售额 DESC"],
                "limit": None,
            },
            "GROUP BY dp.category",
        ),
        (
            "LEFT JOIN + WHERE 过滤",
            {
                "select": [{"expr": "*", "alias": "all"}],
                "from": "fact_order fo",
                "joins": [
                    {"type": "INNER", "table": "dim_product dp", "on": "fo.product_id = dp.product_id"},
                    {"type": "LEFT", "table": "dim_region dr", "on": "fo.region_id = dr.region_id"},
                ],
                "where": ["fo.order_date >= '2025-01-01'"],
                "group_by": [],
                "order_by": [],
                "limit": None,
            },
            "LEFT JOIN dim_region dr",
        ),
        (
            "LIMIT 限制",
            {
                "select": [{"expr": "COUNT(*)", "alias": "订单数"}],
                "from": "fact_order fo",
                "joins": [],
                "where": ["fo.status = 'paid'"],
                "group_by": [],
                "order_by": [],
                "limit": 100,
            },
            "LIMIT 100",
        ),
        (
            "追问 + 实体继承",
            {
                "select": [
                    {"expr": "dr.province", "alias": "省份"},
                    {"expr": "SUM(fo.order_amount)", "alias": "销售额"},
                ],
                "from": "fact_order fo",
                "joins": [
                    {"type": "INNER", "table": "dim_product dp", "on": "fo.product_id = dp.product_id"},
                    {"type": "INNER", "table": "dim_region dr", "on": "fo.region_id = dr.region_id"},
                ],
                "where": ["dp.sku IN ('SKU1', 'SKU2', 'SKU3')"],
                "group_by": ["dr.province"],
                "order_by": ["销售额 DESC"],
                "limit": None,
            },
            "dp.sku IN",
        ),
        # ── 兜底 case ──
        ("空 dict → SELECT 1", {}, "SELECT 1"),
        ("None → SELECT 1", None, "SELECT 1"),
        ("select 字段类型异常 → 降级", {"select": "not a list", "from": 123}, "SELECT 1"),
    ]

    passed = 0
    for label, intent, expect in cases:
        if _test_case(label, intent, expect):
            passed += 1

    total = len(cases)
    print(f"\n{'=' * 50}\n{passed}/{total} passed")
    return passed == total


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
