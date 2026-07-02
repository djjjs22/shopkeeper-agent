"""
召回率评估脚本

指标定义：
  Recall（召回率）= 召回命中数 / 标准答案总数
    例：标准答案 3 个字段，召回了其中 2 个 → Recall = 2/3 = 66.7%
  Precision（准确率）= 召回命中数 / 实际召回数
    例：实际召回了 5 个字段，其中 2 个是标准答案 → Precision = 2/5 = 40%

运行方式：
  cd D:\shopkeeper-agent-main
  python tests/eval_recall.py
"""
from tests.eval_data import TEST_CASES


def evaluate_one_case(case:dict)  -> dict:
    """
       评估单条用例的召回质量

       参数：
           case: 测试用例字典，含 query / expected_tables / expected_columns / expected_metrics

       返回：
           评估结果字典：
           {
               "query": 原始问题,
               "table_recall": 表召回率,
               "column_recall": 字段召回率,
               "metric_recall": 指标召回率,
               "hit_columns": 命中的字段列表,
               "missed_columns": 漏召回的字段列表,
           }
       """
    # TODO 1：调用你项目里的 recall 节点，得到实际召回结果
    # actual_tables, actual_columns, actual_metrics = mock_recall(case["query"])
    actual_tables,actual_columns,actual_metrics = mock_recall(case["query"])
    # TODO 2：计算表召回率
    #table_recall = len(set(actual_tables) & set(case["expected_tables"])) / len(case["expected_tables"])
    table_recall = len(set(actual_tables) & set(case["expected_tables"])) / len(case["expected_tables"])
    # TODO 3：计算字段召回率
    column_recall = len(set(actual_columns) & set(case["expected_columns"])) / len(case["expected_columns"])
    # 在 column_recall 下面加一行
    # 指标召回率（.get 是因为有些用例可能没有 expected_metrics）
    expected_metrics = case.get("expected_metrics", [])
    if expected_metrics:
        metric_recall = len(set(actual_metrics) & set(expected_metrics)) / len(
            expected_metrics)
    else:
        metric_recall = 1.0  # 没要求召回到指标 → 默认 100%

    # TODO 4：找出漏召回的字段
    missed_columns = set(case["expected_columns"]) - set(actual_columns)
    #命中的字段
    hit_columns = list(set(actual_columns) & set(case["expected_columns"]))
    # TODO 5：组装返回结果
    # 把这一行替换掉
    return {
        "query": case["query"],
        "table_recall": table_recall,
        "column_recall": column_recall,
        "metric_recall": metric_recall,
        "hit_columns": hit_columns,
        "missed_columns": missed_columns,
    }

def evaluate_all_cases() -> dict:
    # 步骤 1：循环调用 evaluate_one_case
    results = []
    for case in TEST_CASES:
        result = evaluate_one_case(case)
        results.append(result)
    # 步骤 2：把每条结果存到列表
    column_recalls = [r["column_recall"] for r in results]
    # 步骤 3：算平均召回率
    avg_table_recall = sum(r["table_recall"] for r in results) / len(results)
    avg_column_recall = sum(r["column_recall"] for r in results) / len(results)
    avg_metric_recall = sum(r["metric_recall"] for r in results) / len(results)
    # 步骤 4：找召回率最低的 3 个用例
    worst_cases = sorted(results, key=lambda x: x["column_recall"])[:3]
    # 步骤 5：组装汇总字典返回
    return {
            "total_cases": len(results),
            "avg_table_recall": avg_table_recall,
            "avg_column_recall": avg_column_recall,
            "avg_metric_recall": avg_metric_recall,
            "worst_cases": worst_cases,
        }


def mock_recall(query: str):
    """
    模拟召回结果

    返回：(tables, columns, metrics) 三个列表的元组
    """
    mock_data = {
        # ── 场景 1：简单单表（5 条全部完全命中）──
        "全国有多少个客户": {
            "tables": ["dim_customer"],
            "columns": ["dim_customer.customer_id"],
            "metrics": [],
        },
        "商品的总数量": {
            "tables": ["dim_product"],
            "columns": ["dim_product.product_id"],
            "metrics": [],
        },
        "列出所有的地区": {
            "tables": ["dim_region"],
            "columns": ["dim_region.region_name"],
            "metrics": [],
        },
        "有多少个会员等级": {
            "tables": ["dim_customer"],
            "columns": ["dim_customer.member_level"],
            "metrics": [],
        },
        "商品都有哪些品类": {
            "tables": ["dim_product"],
            "columns": ["dim_product.category_name"],
            "metrics": [],
        },

        # ── 场景 2：JOIN 多表（5 条）──
        "查一下华东上个月的销售额": {
            "tables": ["fact_order", "dim_region"],  # ✅ 完全命中
            "columns": ["fact_order.order_amount", "dim_region.region_name"],
            "metrics": ["GMV"],
        },
        "每个品类的销量是多少": {
            "tables": ["fact_order", "dim_product"],
            "columns": ["fact_order.order_quantity"],  # ⚠️ 漏召 dim_product.category_name
            "metrics": [],
        },
        "钻石会员的订单总额": {
            "tables": ["fact_order", "dim_customer"],
            "columns": ["fact_order.order_amount", "dim_customer.member_level"],
            "metrics": [],
        },
        "手机品类的销售额": {
            "tables": ["fact_order", "dim_product", "fact_order"],
            # ⚠️ 多召了无关的 fact_order
            "columns": ["fact_order.order_amount", "dim_product.category_name"],
            "metrics": [],
        },
        "各地区的客户数量": {
            "tables": ["dim_customer", "dim_region"],
            "columns": ["dim_customer.customer_id", "dim_region.region_name"],
            "metrics": [],
        },

        # ── 场景 3：含时间过滤（4 条）──
        "2025年第一季度的总销售额": {
            "tables": ["fact_order", "dim_date"],
            "columns": ["fact_order.order_amount", "dim_date.date"],
            "metrics": ["GMV"],
        },
        "上个月的订单数": {
            "tables": ["fact_order", "dim_date"],
            "columns": ["fact_order.order_id", "dim_date.date"],
            "metrics": [],
        },
        "最近7天的销量": {
            "tables": ["fact_order"],  # ⚠️ 漏召 dim_date
            "columns": ["fact_order.order_quantity"],
            "metrics": [],
        },
        "2024年全年GMV": {
            "tables": ["fact_order", "dim_date"],
            "columns": ["fact_order.order_amount", "dim_date.date"],
            "metrics": ["GMV"],
        },

        # ── 场景 4：枚举值（3 条）──
        "华北的销售额": {
            "tables": ["fact_order", "dim_region"],
            "columns": ["fact_order.order_amount", "dim_region.region_name"],
            "metrics": [],
        },
        "金牌会员的订单": {
            "tables": ["fact_order", "dim_customer"],
            "columns": ["fact_order.order_id", "dim_customer.member_level"],
            "metrics": [],
        },
        "电脑品类的销量": {
            "tables": ["fact_order", "dim_product"],
            "columns": ["fact_order.order_quantity", "dim_product.category_name"],
            "metrics": [],
        },

        # ── 场景 5：业务指标（3 条）──
        "统计每个地区的GMV": {
            "tables": ["fact_order", "dim_region"],
            "columns": ["fact_order.order_amount", "dim_region.region_name"],
            "metrics": ["GMV"],
        },
        "各品类的总销售额": {
            "tables": ["fact_order", "dim_product"],
            "columns": ["fact_order.order_amount", "dim_product.category_name"],
            "metrics": ["GMV"],
        },
        "客单价是多少": {
            "tables": ["fact_order"],
            "columns": ["fact_order.order_amount", "fact_order.order_id"],
            "metrics": ["客单价"],
        },
    }

    # 防御：万一 query 不在 mock_data 里，返回空集
    if query not in mock_data:
        return [], [], []

    # 返回三个列表
    data = mock_data[query]
    return data["tables"], data["columns"], data["metrics"]


if __name__ == "__main__":
    print("=" * 60)
    print("召回率评估报告")
    print("=" * 60)

    summary = evaluate_all_cases()

    print(f"📊 总用例数：{summary['total_cases']}")
    print(f"📈 平均表召回率：{summary['avg_table_recall']:.1%}")
    print(f"📈 平均字段召回率：{summary['avg_column_recall']:.1%}")
    print(f"📈 平均指标召回率：{summary['avg_metric_recall']:.1%}")

    print("\n🔍 召回率最低的 3 个用例：")
    for case in summary["worst_cases"]:
        print(f"  - {case['query']}")
        print(f"    字段召回率: {case['column_recall']:.1%}")
        print(f"    漏召字段: {case['missed_columns']}")
