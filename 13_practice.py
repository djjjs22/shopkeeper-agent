"""
第13章 SQL生成前的信息过滤与补全 - 代码实操练习
==================================================
把每个 TODO 替换成正确的代码，然后运行 python 13_practice.py 验证结果。
"""

# ============================================================
# 练习1：filter_table - 模型返回选择结果，程序裁剪
# ============================================================

def filter_table(table_infos, model_result):
    """
    table_infos: 候选表结构，格式如下：
      [
        {"name": "fact_order",  "columns": [
            {"name": "order_amount", "type": "decimal"},
            {"name": "region_id",    "type": "bigint"},
            {"name": "order_id",     "type": "bigint"},
        ]},
        {"name": "dim_region",  "columns": [
            {"name": "region_id",   "type": "bigint"},
            {"name": "region_name", "type": "varchar"},
        ]},
        {"name": "dim_date",    "columns": [
            {"name": "date_id",    "type": "bigint"},
            {"name": "order_date", "type": "date"},
        ]},
      ]

    model_result: 模型返回的选择结果，格式如下：
      {"fact_order": ["order_amount", "region_id"], "dim_region": ["region_id", "region_name"]}

    返回：过滤后的 table_infos
    """
    filtered = []

    for table_info in table_infos:
        table_name = table_info["name"]

        # TODO 1A: 判断这张表是否在模型返回的结果里
        if table_name in model_result:
            # TODO 1B: 用列表推导式，只保留模型选中了的字段
            table_info["columns"] = [
                col for col in table_info["columns"]
                if col["name"] in model_table[table_name]
            ]

            # TODO 1C: 把这张表加到结果列表里
            filtered.append(table_info)

    return filtered


# ============================================================
# 练习2：filter_metric - 模型返回指标名列表，程序过滤
# ============================================================

def filter_metric(metric_infos, model_result):
    """
    metric_infos: 候选指标，格式如下：
      [
        {"name": "GMV",     "description": "销售总额", "relevant_columns": ["fact_order.order_amount"]},
        {"name": "AOV",     "description": "客单价",   "relevant_columns": ["fact_order.order_amount", "fact_order.order_id"]},
        {"name": "订单数",   "description": "订单数量",  "relevant_columns": ["fact_order.order_id"]},
      ]

    model_result: 模型返回的选中指标名列表，例如 ["GMV"] 或 []

    返回：过滤后的 metric_infos
    """
    # TODO 2: 用列表推导式，只保留 name 在 model_result 中的指标
    filtered = ___

    return filtered


# ============================================================
# 练习3：add_extra_context - 季度计算
# ============================================================

def get_quarter(month):
    """
    输入月份（1-12），返回对应的季度字符串。
    例如：month=3  → "Q1"
          month=12 → "Q4"
          month=6  → "Q2"
    """
    # TODO 3: 计算季度，公式为 (month - 1) // 3 + 1
    quarter_num = ___
    return f"Q{quarter_num}"


# ============================================================
# 练习4：YAML dump 参数理解
# ============================================================

def explain_yaml_params():
    """
    项目中用 yaml.dump(table_infos, allow_unicode=True, sort_keys=False)
    写出两个参数的作用：
    """
    answer = """
    allow_unicode=True 的作用：___

    sort_keys=False 的作用：___
    """
    return answer


# ============================================================
# 练习5：遍歷列表时不直接 remove
# ============================================================

def safe_filter_vs_remove():
    """
    下面两段代码，哪段会漏删元素？为什么？
    """
    code_a = """
nums = [10, 20, 30, 40, 50]
filtered = []
for n in nums:
    if n % 2 == 0:
        filtered.append(n)  # 只保留偶数？不，这是想删偶数但写法错了
"""

    code_b = """
nums = [10, 20, 30, 40, 50]
for n in nums:
    if n % 2 == 0:
        nums.remove(n)
# 期望得到 [30]（只保留奇数），实际得到 [20, 30, 50]
"""

    # TODO 5: 写出 code_b 会漏删的原因
    reason = "___"

    return reason


# ============================================================
# 练习6：filter_metric 返回空数组的场景判断
# ============================================================

def should_return_empty(query, candidate_metrics):
    """
    判断模型是否应该返回空数组 []。
    返回 True 表示应该返回 []，False 表示应该至少选一个。
    """
    # TODO 6: 写出判断条件（一句话的逻辑）
    # 提示：什么时候 query 不需要业务指标？
    ___

    return True  # 占位，你需要改这行


# ============================================================
# ====== 测试用例 ======
# ============================================================

def run_tests():
    score = 0
    total = 5  # 练习4和6不参与自动评分，人工检查

    # --- 测试1：filter_table ---
    print("=" * 50)
    print("测试1：filter_table")

    table_infos = [
        {"name": "fact_order", "columns": [
            {"name": "order_amount", "type": "decimal"},
            {"name": "region_id", "type": "bigint"},
            {"name": "order_id", "type": "bigint"},
        ]},
        {"name": "dim_region", "columns": [
            {"name": "region_id", "type": "bigint"},
            {"name": "region_name", "type": "varchar"},
        ]},
        {"name": "dim_date", "columns": [
            {"name": "date_id", "type": "bigint"},
            {"name": "order_date", "type": "date"},
        ]},
    ]

    model_result = {
        "fact_order": ["order_amount", "region_id"],
        "dim_region": ["region_id", "region_name"]
    }

    result = filter_table(table_infos, model_result)

    if len(result) == 2:
        print(f"  ✅ 保留了 {len(result)} 张表（dim_date 被过滤掉）")
        score += 1
    else:
        print(f"  ❌ 期望 2 张表，实际 {len(result)} 张")

    # 检查 fact_order 的字段是否被正确裁剪
    fact_cols = [c["name"] for c in result[0]["columns"]]
    if fact_cols == ["order_amount", "region_id"]:
        print(f"  ✅ fact_order 字段裁剪正确：{fact_cols}（order_id 被过滤）")
        score += 1
    else:
        print(f"  ❌ fact_order 字段：{fact_cols}，期望 ['order_amount', 'region_id']")

    # --- 测试2：filter_metric ---
    print("\n测试2：filter_metric")

    metric_infos = [
        {"name": "GMV", "description": "销售总额", "relevant_columns": ["fact_order.order_amount"]},
        {"name": "AOV", "description": "客单价", "relevant_columns": ["fact_order.order_amount", "fact_order.order_id"]},
    ]

    # 测试保留一个指标
    result = filter_metric(metric_infos, ["GMV"])
    if len(result) == 1 and result[0]["name"] == "GMV":
        print(f"  ✅ 正确过滤：只保留 GMV")
        score += 1
    else:
        print(f"  ❌ 过滤失败：{result}")

    # 测试返回空数组
    result = filter_metric(metric_infos, [])
    if len(result) == 0:
        print(f"  ✅ 空数组正确：返回了 []")
        score += 1
    else:
        print(f"  ❌ 期望 []，实际 {result}")

    # --- 测试3：季度计算 ---
    print("\n测试3：季度计算")

    expected = {
        1: "Q1", 2: "Q1", 3: "Q1",
        4: "Q2", 5: "Q2", 6: "Q2",
        7: "Q3", 8: "Q3", 9: "Q3",
        10: "Q4", 11: "Q4", 12: "Q4",
    }

    all_pass = True
    for month, expected_q in expected.items():
        actual = get_quarter(month)
        if actual != expected_q:
            print(f"  ❌ month={month}：期望 {expected_q}，实际 {actual}")
            all_pass = False

    if all_pass:
        print(f"  ✅ 全部 12 个月份计算正确")
        score += 1
    else:
        print(f"  ❌ 季度计算有误")

    # --- 测试5：列表遍历不要 remove ---
    print("\n测试5：safe_filter_vs_remove（人工检查）")
    reason = safe_filter_vs_remove()
    print(f"  你的回答：{reason}")

    # --- 手工检查项 ---
    print("\n测试4 & 6：请人工检查你的答案")
    print("  练习4 (YAML参数)：")
    print(explain_yaml_params())
    print("  练习6 (空数组判断)：")
    print(f"  should_return_empty = {should_return_empty.__doc__}")

    # --- 总结 ---
    print("\n" + "=" * 50)
    print(f"自动评分：{score}/{total}")


if __name__ == "__main__":
    run_tests()
