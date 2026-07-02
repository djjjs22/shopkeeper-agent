
# -*- coding: utf-8 -*-
"""
召回率评估测试集

每条用例的格式：
  query: 用户输入的自然语言
  expected_tables: 应该被召回到的表名（列表）
  expected_columns: 应该被召回到的字段名（列表，格式 "表名.字段名"）
  expected_metrics: 应该被召回到的指标名（列表，可选）

藤子任务：至少 20 条，覆盖 5 种典型场景
"""

# 场景 1：简单单表查询（5 条）
# 场景 2：JOIN 多表查询（5 条）
# 场景 3：含时间过滤的查询（4 条）
# 场景 4：枚举值匹配（3 条）
# 场景 5：业务指标查询（3 条）

TEST_CASES = [
    # ── 场景 1：简单单表查询 ──
    {
        "query": "全国有多少个客户",
        "expected_tables": ["dim_customer"],
        "expected_columns": ["dim_customer.customer_id"],
    },
    {
        "query": "商品的总数量",
        "expected_tables": ["dim_product"],
        "expected_columns": ["dim_product.product_id"],
    },
    {
        "query": "列出所有的地区",
        "expected_tables": ["dim_region"],
        "expected_columns": ["dim_region.region_name"],
    },
    {
        "query": "有多少个会员等级",
        "expected_tables": ["dim_customer"],
        "expected_columns": ["dim_customer.member_level"],
    },
    {
        "query": "商品都有哪些品类",
        "expected_tables": ["dim_product"],
        "expected_columns": ["dim_product.category_name"],
    },

    # ── 场景 2：JOIN 多表查询（事实表 + 维度表）──
    {
        "query": "查一下华东上个月的销售额",
        "expected_tables": ["fact_order", "dim_region"],
        "expected_columns": ["fact_order.order_amount", "dim_region.region_name"],
        "expected_metrics": ["GMV"],
    },
    {
        "query": "每个品类的销量是多少",
        "expected_tables": ["fact_order", "dim_product"],
        "expected_columns": ["fact_order.order_quantity", "dim_product.category_name"],
    },
    {
        "query": "钻石会员的订单总额",
        "expected_tables": ["fact_order", "dim_customer"],
        "expected_columns": ["fact_order.order_amount", "dim_customer.member_level"],
    },
    {
        "query": "手机品类的销售额",
        "expected_tables": ["fact_order", "dim_product"],
        "expected_columns": ["fact_order.order_amount", "dim_product.category_name"],
    },
    {
        "query": "各地区的客户数量",
        "expected_tables": ["dim_customer", "dim_region"],
        "expected_columns": ["dim_customer.customer_id", "dim_region.region_name"],
    },

    # ── 场景 3：含时间过滤 ──
    {
        "query": "2025年第一季度的总销售额",
        "expected_tables": ["fact_order", "dim_date"],
        "expected_columns": ["fact_order.order_amount", "dim_date.date"],
        "expected_metrics": ["GMV"],
    },
    {
        "query": "上个月的订单数",
        "expected_tables": ["fact_order", "dim_date"],
        "expected_columns": ["fact_order.order_id", "dim_date.date"],
    },
    {
        "query": "最近7天的销量",
        "expected_tables": ["fact_order", "dim_date"],
        "expected_columns": ["fact_order.order_quantity", "dim_date.date"],
    },
    {
        "query": "2024年全年GMV",
        "expected_tables": ["fact_order", "dim_date"],
        "expected_columns": ["fact_order.order_amount", "dim_date.date"],
        "expected_metrics": ["GMV"],
    },

    # ── 场景 4：枚举值匹配（取值召回）──
    {
        "query": "华北的销售额",
        "expected_tables": ["fact_order", "dim_region"],
        "expected_columns": ["fact_order.order_amount", "dim_region.region_name"],
    },
    {
        "query": "金牌会员的订单",
        "expected_tables": ["fact_order", "dim_customer"],
        "expected_columns": ["fact_order.order_id", "dim_customer.member_level"],
    },
    {
        "query": "电脑品类的销量",
        "expected_tables": ["fact_order", "dim_product"],
        "expected_columns": ["fact_order.order_quantity", "dim_product.category_name"],
    },

    # ── 场景 5：业务指标查询 ──
    {
        "query": "统计每个地区的GMV",
        "expected_tables": ["fact_order", "dim_region"],
        "expected_columns": ["fact_order.order_amount", "dim_region.region_name"],
        "expected_metrics": ["GMV"],
    },
    {
        "query": "各品类的总销售额",
        "expected_tables": ["fact_order", "dim_product"],
        "expected_columns": ["fact_order.order_amount", "dim_product.category_name"],
        "expected_metrics": ["GMV"],
    },
    {
        "query": "客单价是多少",
        "expected_tables": ["fact_order"],
        "expected_columns": ["fact_order.order_amount", "fact_order.order_id"],
        "expected_metrics": ["客单价"],
    },
]
