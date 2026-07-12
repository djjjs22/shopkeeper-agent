# -*- coding: utf-8 -*-
"""
端到端 SQL 生成评测集

每条用例格式：
  query: 用户自然语言
  expected_sql: 期望生成的标准 SQL（你手写）
  difficulty: 单表 / 多表 JOIN / 多维筛选 / 多轮对话
  expected_tables / expected_columns: 召回参考（可选）
  ground_truth_result: 期望查询结果（可手算几条）

数据来源：从 eval_data.py 复制 20 条 + 新增 30 条

⚠️ 注意：expected_sql 必须用项目真实 schema
   dim_region / dim_customer / dim_product / dim_date / fact_order
   指标 GMV = SUM(order_amount)
"""

TEST_CASES_E2E = [
    # ═══════════════════════════════════════════════════════════════
    # 场景 1：简单单表查询（10 条）
    # ═══════════════════════════════════════════════════════════════
    {
        "query": "全国有多少个客户",
        "expected_sql": "SELECT COUNT(DISTINCT customer_id) AS 客户总数 FROM dim_customer",
        "difficulty": "单表",
        "ground_truth_result": 10000,  # 假设值
    },
    {
        "query": "商品的总数量",
        "expected_sql": "SELECT COUNT(DISTINCT product_id) AS 商品总数 FROM dim_product",
        "difficulty": "单表",
        "ground_truth_result": 5000,
    },
    {
        "query": "列出所有的地区",
        "expected_sql": "SELECT DISTINCT region_name AS 地区 FROM dim_region",
        "difficulty": "单表",
        "ground_truth_result": ["华东", "华南", "华北", "西南", "西北", "东北"],
    },
    {
        "query": "有多少个会员等级",
        "expected_sql": "SELECT COUNT(DISTINCT member_level) AS 会员等级数 FROM dim_customer",
        "difficulty": "单表",
        "ground_truth_result": 4,
    },
    {
        "query": "商品都有哪些品类",
        "expected_sql": "SELECT DISTINCT category AS 品类 FROM dim_product",
        "difficulty": "单表",
        "ground_truth_result": ["手机数码", "家用电器", "鞋靴", "服饰", "食品饮料", "休闲零食"],
    },
    {
        "query": "总共有多少个订单",
        "expected_sql": "SELECT COUNT(order_id) AS 订单总数 FROM fact_order",
        "difficulty": "单表",
        "ground_truth_result": 500000,
    },
    {
        "query": "最大的订单金额是多少",
        "expected_sql": "SELECT MAX(order_amount) AS 最大订单金额 FROM fact_order",
        "difficulty": "单表",
        "ground_truth_result": 99999.99,
    },
    {
        "query": "订单金额的最小值",
        "expected_sql": "SELECT MIN(order_amount) AS 最小订单金额 FROM fact_order",
        "difficulty": "单表",
        "ground_truth_result": 1.0,
    },
    {
        "query": "订单平均金额",
        "expected_sql": "SELECT AVG(order_amount) AS 平均订单金额 FROM fact_order",
        "difficulty": "单表",
        "ground_truth_result": 250.5,
    },
    {
        "query": "有哪些支付方式",
        "expected_sql": "SELECT DISTINCT payment_method AS 支付方式 FROM fact_order",
        "difficulty": "单表",
        "ground_truth_result": ["支付宝", "微信", "银行卡", "信用卡"],
    },

    # ═══════════════════════════════════════════════════════════════
    # 场景 2：JOIN 多表查询（10 条）
    # ═══════════════════════════════════════════════════════════════
    {
        "query": "查一下华东上个月的销售额",
        "expected_sql": """SELECT SUM(fact_order.order_amount) AS 销售额
                          FROM fact_order
                          JOIN dim_region ON fact_order.region_id = dim_region.region_id
                          WHERE dim_region.region_name = '华东'
                            AND fact_order.date_id >= 202606""",
        "difficulty": "多表 JOIN + 时间过滤",
        "ground_truth_result": 12345678.90,
    },
    {
        "query": "每个品类的销量是多少",
        "expected_sql": """SELECT dim_product.category AS 品类, SUM(fact_order.order_quantity) AS 销量
                          FROM fact_order
                          JOIN dim_product ON fact_order.product_id = dim_product.product_id
                          GROUP BY dim_product.category""",
        "difficulty": "多表 JOIN + 聚合",
    },
    {
        "query": "黄金会员的订单总额",
        "expected_sql": """SELECT SUM(fact_order.order_amount) AS 订单总额
                          FROM fact_order
                          JOIN dim_customer ON fact_order.customer_id = dim_customer.customer_id
                          WHERE dim_customer.member_level = '黄金'""",
        "difficulty": "多表 JOIN + 过滤",
    },
    {
        "query": "手机品类的销售额",
        "expected_sql": """SELECT SUM(fact_order.order_amount) AS 销售额
                          FROM fact_order
                          JOIN dim_product ON fact_order.product_id = dim_product.product_id
                          WHERE dim_product.category = '手机数码'""",
        "difficulty": "多表 JOIN + 过滤",
    },
    {
        "query": "各地区的客户数量",
        "expected_sql": """SELECT dim_region.region_name AS 地区, COUNT(DISTINCT fact_order.customer_id) AS 客户数
                          FROM fact_order
                          JOIN dim_region ON fact_order.region_id = dim_region.region_id
                          GROUP BY dim_region.region_name""",
        "difficulty": "多表 JOIN + 聚合",
    },
    {
        "query": "黄金会员在各地区的分布",
        "expected_sql": """SELECT dim_region.region_name AS 地区, COUNT(*) AS 黄金会员数
                          FROM dim_customer
                          JOIN dim_region ON dim_customer.region_id = dim_region.region_id
                          WHERE dim_customer.member_level = '黄金'
                          GROUP BY dim_region.region_name""",
        "difficulty": "多表 JOIN + 多条件",
    },
    {
        "query": "手机品类在各地区的销售额",
        "expected_sql": """SELECT dim_region.region_name AS 地区, SUM(fact_order.order_amount) AS 销售额
                          FROM fact_order
                          JOIN dim_product ON fact_order.product_id = dim_product.product_id
                          JOIN dim_region ON fact_order.region_id = dim_region.region_id
                          WHERE dim_product.category = '手机数码'
                          GROUP BY dim_region.region_name""",
        "difficulty": "三表 JOIN + 聚合",
    },
    {
        "query": "华北地区黄金会员的客单价",
        "expected_sql": """SELECT AVG(fact_order.order_amount) AS 客单价
                          FROM fact_order
                          JOIN dim_region ON fact_order.region_id = dim_region.region_id
                          JOIN dim_customer ON fact_order.customer_id = dim_customer.customer_id
                          WHERE dim_region.region_name = '华北'
                            AND dim_customer.member_level = '黄金'""",
        "difficulty": "三表 JOIN + 多条件",
    },
    {
        "query": "统计每个会员等级的下单频次",
        "expected_sql": """SELECT dim_customer.member_level AS 会员等级, COUNT(DISTINCT fact_order.order_id) AS 下单次数
                          FROM fact_order
                          JOIN dim_customer ON fact_order.customer_id = dim_customer.customer_id
                          GROUP BY dim_customer.member_level""",
        "difficulty": "多表 JOIN + 聚合",
    },
    {
        "query": "各支付方式的使用次数",
        "expected_sql": """SELECT payment_method AS 支付方式, COUNT(*) AS 使用次数
                          FROM fact_order
                          GROUP BY payment_method
                          ORDER BY 使用次数 DESC""",
        "difficulty": "单表聚合 + 排序",
    },

    # ═══════════════════════════════════════════════════════════════
    # 场景 3：含时间过滤（10 条）
    # ═══════════════════════════════════════════════════════════════
    {
        "query": "2025年第一季度的总销售额",
        "expected_sql": """SELECT SUM(fact_order.order_amount) AS 总销售额
                          FROM fact_order
                          JOIN dim_date ON fact_order.date_id = dim_date.date
                          WHERE dim_date.year = 2025 AND dim_date.quarter = 1""",
        "difficulty": "时间过滤 + JOIN",
    },
    {
        "query": "上个月的订单数",
        "expected_sql": """SELECT COUNT(*) AS 订单数
                          FROM fact_order
                          WHERE date_id >= 202606""",
        "difficulty": "时间过滤",
    },
    {
        "query": "最近7天的销量",
        "expected_sql": """SELECT SUM(order_quantity) AS 销量
                          FROM fact_order
                          WHERE date_id >= 20260704""",
        "difficulty": "时间过滤",
    },
    {
        "query": "2024年全年GMV",
        "expected_sql": """SELECT SUM(fact_order.order_amount) AS GMV
                          FROM fact_order
                          JOIN dim_date ON fact_order.date_id = dim_date.date
                          WHERE dim_date.year = 2024""",
        "difficulty": "时间过滤 + JOIN",
    },
    {
        "query": "今年3月的销售额",
        "expected_sql": """SELECT SUM(fact_order.order_amount) AS 销售额
                          FROM fact_order
                          JOIN dim_date ON fact_order.date_id = dim_date.date
                          WHERE dim_date.year = YEAR(CURRENT_DATE)
                            AND dim_date.month = 3""",
        "difficulty": "时间过滤 + JOIN",
    },
    {
        "query": "上周的日均订单数",
        "expected_sql": """SELECT COUNT(*) / 7 AS 日均订单数
                          FROM fact_order
                          WHERE date_id >= 20260704""",
        "difficulty": "时间过滤 + 聚合",
    },
    {
        "query": "去年双十一的销售额",
        "expected_sql": """SELECT SUM(fact_order.order_amount) AS 双十一销售额
                          FROM fact_order
                          JOIN dim_date ON fact_order.date_id = dim_date.date_id
                          WHERE dim_date.year = 2025 AND dim_date.month = 11 AND dim_date.day = 11""",
        "difficulty": "复杂时间计算",
    },
    {
        "query": "本月各天的销售额",
        "expected_sql": """SELECT fact_order.date_id AS 日期, SUM(fact_order.order_amount) AS 销售额
                          FROM fact_order
                          WHERE date_id >= 20260701
                          GROUP BY fact_order.date_id
                          ORDER BY fact_order.date_id""",
        "difficulty": "时间过滤 + 聚合 + 排序",
    },
    {
        "query": "过去30天的新增客户数",
        "expected_sql": """SELECT COUNT(DISTINCT customer_id) AS 新增客户数
                          FROM dim_customer
                          WHERE register_date >= 20260611""",
        "difficulty": "时间过滤",
    },
    {
        "query": "2024年Q4各月的GMV",
        "expected_sql": """SELECT dim_date.month AS 月份, SUM(fact_order.order_amount) AS GMV
                          FROM fact_order
                          JOIN dim_date ON fact_order.date_id = dim_date.date
                          WHERE dim_date.year = 2024 AND dim_date.quarter = 4
                          GROUP BY dim_date.month
                          ORDER BY dim_date.month""",
        "difficulty": "时间过滤 + JOIN + 聚合",
    },

    # ═══════════════════════════════════════════════════════════════
    # 场景 4：枚举值匹配（5 条）
    # ═══════════════════════════════════════════════════════════════
    {
        "query": "华北的销售额",
        "expected_sql": """SELECT SUM(fact_order.order_amount) AS 销售额
                          FROM fact_order
                          JOIN dim_region ON fact_order.region_id = dim_region.region_id
                          WHERE dim_region.region_name = '华北'""",
        "difficulty": "枚举值匹配",
    },
    {
        "query": "黄金会员的订单",
        "expected_sql": """SELECT fact_order.order_id, fact_order.order_amount, fact_order.order_quantity, fact_order.customer_id
                          FROM fact_order
                          JOIN dim_customer ON fact_order.customer_id = dim_customer.customer_id
                          WHERE dim_customer.member_level = '黄金'""",
        "difficulty": "枚举值匹配",
    },
    {
        "query": "电脑品类的销量",
        "expected_sql": """SELECT SUM(fact_order.order_quantity) AS 销量
                          FROM fact_order
                          JOIN dim_product ON fact_order.product_id = dim_product.product_id
                          WHERE dim_product.category = '家用电器'""",
        "difficulty": "枚举值匹配",
    },
    {
        "query": "微信支付的订单总额",
        "expected_sql": """SELECT SUM(order_amount) AS 订单总额
                          FROM fact_order
                         """,
        "difficulty": "枚举值匹配",
    },
    {
        "query": "华南和华东的销售额对比",
        "expected_sql": """SELECT dim_region.region_name AS 地区, SUM(fact_order.order_amount) AS 销售额
                          FROM fact_order
                          JOIN dim_region ON fact_order.region_id = dim_region.region_id
                          WHERE dim_region.region_name IN ('华南', '华东')
                          GROUP BY dim_region.region_name""",
        "difficulty": "枚举值匹配 + 多值",
    },

    # ═══════════════════════════════════════════════════════════════
    # 场景 5：业务指标查询（10 条）
    # ═══════════════════════════════════════════════════════════════
    {
        "query": "统计每个地区的GMV",
        "expected_sql": """SELECT dim_region.region_name AS 地区, SUM(fact_order.order_amount) AS GMV
                          FROM fact_order
                          JOIN dim_region ON fact_order.region_id = dim_region.region_id
                          GROUP BY dim_region.region_name""",
        "difficulty": "业务指标",
    },
    {
        "query": "各品类的总销售额",
        "expected_sql": """SELECT dim_product.category AS 品类, SUM(fact_order.order_amount) AS 总销售额
                          FROM fact_order
                          JOIN dim_product ON fact_order.product_id = dim_product.product_id
                          GROUP BY dim_product.category""",
        "difficulty": "业务指标",
    },
    {
        "query": "客单价是多少",
        "expected_sql": """SELECT AVG(order_amount) AS 客单价
                          FROM fact_order""",
        "difficulty": "业务指标",
    },
    {
        "query": "用户复购率",
        "expected_sql": """SELECT
                            COUNT(DISTINCT CASE WHEN order_count > 1 THEN customer_id END) * 1.0
                            / COUNT(DISTINCT customer_id) AS 复购率
                          FROM (
                            SELECT customer_id, COUNT(*) AS order_count
                            FROM fact_order
                            GROUP BY customer_id
                          ) t""",
        "difficulty": "复杂业务指标",
    },
    {
        "query": "商品动销率",
        "expected_sql": """SELECT
                            COUNT(DISTINCT CASE WHEN order_quantity > 0 THEN product_id END) * 1.0
                            / (SELECT COUNT(*) FROM dim_product) AS 动销率
                          FROM fact_order""",
        "difficulty": "复杂业务指标",
    },
    {
        "query": "月度GMV趋势",
        "expected_sql": """SELECT dim_date.year AS 年, dim_date.month AS 月, SUM(fact_order.order_amount) AS GMV
                          FROM fact_order
                          JOIN dim_date ON fact_order.date_id = dim_date.date
                          GROUP BY dim_date.year, dim_date.month
                          ORDER BY dim_date.year, dim_date.month""",
        "difficulty": "趋势分析",
    },
    {
        "query": "TOP10 客户贡献了多少GMV",
        "expected_sql": """SELECT SUM(order_amount) AS TOP10_GMV
                          FROM (
                            SELECT customer_id, SUM(order_amount) AS order_amount
                            FROM fact_order
                            GROUP BY customer_id
                            ORDER BY order_amount DESC
                            LIMIT 10
                          ) t""",
        "difficulty": "TOP N + 子查询",
    },
    {
        "query": "新客占比",
        "expected_sql": """SELECT
                            COUNT(DISTINCT CASE WHEN is_new = 1 THEN customer_id END) * 1.0
                            / COUNT(DISTINCT customer_id) AS 新客占比
                          FROM fact_order""",
        "difficulty": "复杂业务指标",
    },
    {
        "query": "支付成功率",
        "expected_sql": """SELECT
                            COUNT(CASE WHEN payment_status = '成功' THEN 1 END) * 1.0
                            / COUNT(*) AS 支付成功率
                          FROM fact_order""",
        "difficulty": "复杂业务指标",
    },
    {
        "query": "客单价最高的TOP3地区",
        "expected_sql": """SELECT dim_region.region_name AS 地区, AVG(fact_order.order_amount) AS 客单价
                          FROM fact_order
                          JOIN dim_region ON fact_order.region_id = dim_region.region_id
                          GROUP BY dim_region.region_name
                          ORDER BY 客单价 DESC
                          LIMIT 3""",
        "difficulty": "业务指标 + TOP N",
    },

    # ═══════════════════════════════════════════════════════════════
    # 场景 6：多轮对话（5 条，测"上下文记忆"功能）
    # ═══════════════════════════════════════════════════════════════
    {
        "query": "查一下华东的销售额",
        "expected_sql": """SELECT SUM(fact_order.order_amount) AS 销售额
                          FROM fact_order
                          JOIN dim_region ON fact_order.region_id = dim_region.region_id
                          WHERE dim_region.region_name = '华东'""",
        "difficulty": "多轮 - 单表",
        "multi_turn": [
            {"role": "user", "content": "查一下华东的销售额"},
            {"role": "assistant", "content": "（已生成 SQL，返回结果 12345678.90）"},
            {"role": "user", "content": "那华南呢"},
        ],
        "expected_sql_for_last": """SELECT SUM(fact_order.order_amount) AS 销售额
                                    FROM fact_order
                                    JOIN dim_region ON fact_order.region_id = dim_region.region_id
                                    WHERE dim_region.region_name = '华南'""",
    },
    {
        "query": "那华南呢",
        "expected_sql": """SELECT SUM(fact_order.order_amount) AS 销售额
                          FROM fact_order
                          JOIN dim_region ON fact_order.region_id = dim_region.region_id
                          WHERE dim_region.region_name = '华南'""",
        "difficulty": "多轮 - 上下文",
        "depends_on_prev": "查一下华东的销售额",
    },
    {
        "query": "今年呢",
        "expected_sql": """SELECT SUM(fact_order.order_amount) AS 销售额
                          FROM fact_order
                          JOIN dim_date ON fact_order.date_id = dim_date.date
                          WHERE dim_date.year = YEAR(CURRENT_DATE)""",
        "difficulty": "多轮 - 时间补全",
        "depends_on_prev": "查一下华东的销售额",
    },
    {
        "query": "改成手机品类",
        "expected_sql": """SELECT SUM(fact_order.order_amount) AS 销售额
                          FROM fact_order
                          JOIN dim_product ON fact_order.product_id = dim_product.product_id
                          WHERE dim_product.category = '手机数码'""",
        "difficulty": "多轮 - 维度替换",
        "depends_on_prev": "查一下华东的销售额",
    },
    {
        "query": "只算黄金会员",
        "expected_sql": """SELECT SUM(fact_order.order_amount) AS 销售额
                          FROM fact_order
                          JOIN dim_customer ON fact_order.customer_id = dim_customer.customer_id
                          WHERE dim_customer.member_level = '黄金'""",
        "difficulty": "多轮 - 条件追加",
        "depends_on_prev": "查一下华东的销售额",
    },
]


# 评测配置
EVAL_CONFIG = {
    "difficulty_weights": {
        "单表": 1.0,
        "多表 JOIN + 时间过滤": 1.5,
        "业务指标": 1.5,
        "复杂业务指标": 2.0,
        "多轮 - 上下文": 2.0,
    },
    "sql_match_threshold": 0.8,  # SQL 相似度阈值（0-1）
    "result_match_threshold": 0.95,  # 结果集相似度阈值
}
