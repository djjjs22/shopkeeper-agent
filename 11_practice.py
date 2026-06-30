"""
第11章 关键词抽取与多路召回 - 代码实操练习
============================================
把每个 TODO 替换成正确的代码，然后运行 python 11_practice.py 验证结果。
"""

import jieba.analyse

# ============================================================
# 练习1：extract_keywords - 关键词抽取
# ============================================================

def extract_keywords(query: str) -> list[str]:
    """从用户query中抽取关键词，过滤噪声，追加原始query兜底"""

    # TODO 1A: 写出 allow_pos 元组，至少包含 n, nr, ns, v, eng 这5种
    allow_pos = (
        "n", "nr", "ns", "v", "eng"
    )
      

    # TODO 1B: 调用 jieba.analyse.extract_tags 抽取关键词
    keywords = jieba.analyse.extract_tags(query, allowPOS=allow_pos)

    # TODO 1C: 追加原始 query，用 set 去重后转回 list
    keywords = list(set(keywords + [query]))

    return keywords


# ============================================================
# 练习2：字段召回 - 模拟去重逻辑
# ============================================================

class MockColumnInfo:
    """模拟字段信息"""
    def __init__(self, id: str, name: str, table_id: str):
        self.id = id
        self.name = name
        self.table_id = table_id
        self.examples: list[str] = []

    def __repr__(self):
        return f"ColumnInfo(id={self.id}, name={self.name}, table_id={self.table_id}, examples={self.examples})"


def recall_column_dedup(raw_results: list[MockColumnInfo]) -> list[MockColumnInfo]:
    """
    模拟字段召回去重逻辑。
    同一个 column_id 可能被多个关键词命中，需要去重。
    """
    # TODO 2: 用 column_id 做 key，构建 dict 去重
    column_info_map = {}
    for col in raw_results:
        column_info_map[col.id] = col
    return list(column_info_map.values())


# ============================================================
# 练习3：指标依赖字段补齐
# ============================================================

class MockMetricInfo:
    """模拟指标信息"""
    def __init__(self, id: str, name: str, relevant_columns: list[str]):
        self.id = id
        self.name = name
        self.relevant_columns = relevant_columns


def fill_metric_dependencies(
    column_map: dict[str, MockColumnInfo],
    metrics: list[MockMetricInfo],
    all_columns_db: dict[str, MockColumnInfo]  # 模拟 MySQL 元数据库
) -> dict[str, MockColumnInfo]:
    """
    补齐指标依赖字段。
    如果指标的 relevant_columns 中有字段不在 column_map 里，从 all_columns_db 补进去。
    """
    for metric in metrics:
        for col_id in metric.relevant_columns:
            # TODO 3A: 判断 col_id 是否不在 column_map 中
            if col_id not in column_map:
                # TODO 3B: 从 all_columns_db 中查询并补入 column_map
                column_map[col_id]= all_columns_db[col_id]

    return column_map


# ============================================================
# 练习4：字段取值合并到 examples
# ============================================================

class MockValueInfo:
    """模拟字段取值"""
    def __init__(self, column_id: str, value: str):
        self.column_id = column_id
        self.value = value


def merge_values_to_examples(
    column_map: dict[str, MockColumnInfo],
    values: list[MockValueInfo]
) -> dict[str, MockColumnInfo]:
    """
    把字段取值塞进对应字段的 examples 列表。
    如果这个字段还不存在于 column_map 中，跳过（实际代码会去 MySQL 查）。
    """
    for value_info in values:
        col_id = value_info.column_id
        value = value_info.value

        # TODO 4A: 判断字段存在且值不在 examples 中
        if col_id in column_map and value not in column_map[col_id].examples:
            # TODO 4B: 把 value 追加到字段的 examples 列表
            column_map[col_id].examples.append(value)

    return column_map


# ============================================================
# 练习5：按 table_id 分组字段
# ============================================================

def group_columns_by_table(
    column_map: dict[str, MockColumnInfo]
) -> dict[str, list[MockColumnInfo]]:
    """
    把字段按所属表分组。
    """
    table_to_columns = {}

    for col in column_map.values():
        table_id = col.table_id

        # TODO 5: 如果 table_id 不在 dict 中，先创建空列表，再追加
        if table_id not in table_to_columns:
            table_to_columns[table_id] = []
        table_to_columns[table_id].append(col)
 
    return table_to_columns


# ============================================================
# 练习6：补齐主外键
# ============================================================

def fill_key_columns(
    table_groups: dict[str, list[MockColumnInfo]],
    table_key_columns_db: dict[str, list[MockColumnInfo]]  # 模拟 MySQL 查主外键
) -> dict[str, list[MockColumnInfo]]:
    """
    补齐每张表的主外键字段。
    table_key_columns_db 存了每张表的主外键字段列表。
    """
    for table_id, columns in table_groups.items():
        existing_ids = [col.id for col in columns]

        # TODO 6A: 从 table_key_columns_db 中获取这张表的主外键字段
        key_cols = table_key_columns_db[table_id]

        for key_col in key_cols:
            # TODO 6B: 如果主外键字段不在已有列表中，追加进去
            if key_col.id not in existing_ids:
                columns.append(key_col)

    return table_groups


# ============================================================
# ====== 测试用例 ======
# ============================================================

def run_tests():
    score = 0
    total = 6

    # --- 测试1：关键词抽取 ---
    print("=" * 50)
    print("测试1：extract_keywords")
    keywords = extract_keywords("李四华北地区数码产品销售额环比")
    print(f"  keywords = {keywords}")

    has_lisi = any("李四" in k for k in keywords)
    has_huabei = any("华北" in k for k in keywords)
    has_original = any("李四华北地区数码产品销售额环比" in k for k in keywords)

    if has_lisi:
        print("  ✅ 人名'李四'被保留（nr词性过滤正确）")
        score += 1
    else:
        print("  ❌ 人名'李四'丢失！nr词性没有被保留")

    if has_huabei:
        print("  ✅ 地名'华北'被保留（ns词性过滤正确）")
    else:
        print("  ❌ 地名'华北'丢失！ns词性没有被保留")

    if has_original:
        print("  ✅ 原始query被追加（兜底策略生效）")
    else:
        print("  ❌ 原始query没有被追加！")

    # --- 测试2：字段召回去重 ---
    print("\n测试2：字段召回去重")
    raw = [
        MockColumnInfo("dim_region.region_name", "地区名称", "dim_region"),
        MockColumnInfo("dim_region.region_name", "地区名称", "dim_region"),  # 重复
        MockColumnInfo("dim_product.category", "产品类别", "dim_product"),
    ]
    result = recall_column_dedup(raw)
    if len(result) == 2:
        print(f"  ✅ 去重成功：{len(raw)}条输入 → {len(result)}条输出")
        score += 1
    else:
        print(f"  ❌ 去重失败：期望2条，实际{len(result)}条")

    # --- 测试3：指标依赖字段补齐 ---
    print("\n测试3：指标依赖字段补齐")
    column_map = {
        "dim_date.order_date": MockColumnInfo("dim_date.order_date", "下单日期", "dim_date"),
    }
    metrics = [
        MockMetricInfo("gmv", "GMV", ["fact_order.sales_amount", "fact_order.discount_amount"]),
    ]
    all_columns_db = {
        "fact_order.sales_amount": MockColumnInfo("fact_order.sales_amount", "销售金额", "fact_order"),
        "fact_order.discount_amount": MockColumnInfo("fact_order.discount_amount", "折扣金额", "fact_order"),
    }
    result = fill_metric_dependencies(column_map, metrics, all_columns_db)
    if len(result) == 3 and "fact_order.sales_amount" in result:
        print(f"  ✅ 补齐成功：{list(result.keys())}")
        score += 1
    else:
        print(f"  ❌ 补齐失败：期望3个字段，实际{len(result)}个")

    # --- 测试4：字段取值合并 ---
    print("\n测试4：字段取值合并到examples")
    column_map = {
        "dim_region.region_name": MockColumnInfo("dim_region.region_name", "地区名称", "dim_region"),
    }
    values = [
        MockValueInfo("dim_region.region_name", "华北"),
        MockValueInfo("dim_region.region_name", "华北"),  # 重复值
        MockValueInfo("dim_region.region_name", "华东"),
    ]
    result = merge_values_to_examples(column_map, values)
    if result["dim_region.region_name"].examples == ["华北", "华东"]:
        print(f"  ✅ 合并成功：examples = {result['dim_region.region_name'].examples}")
        score += 1
    else:
        print(f"  ❌ 合并失败：{result['dim_region.region_name'].examples}")

    # --- 测试5：按表分组 ---
    print("\n测试5：按table_id分组")
    column_map = {
        "dim_region.region_name": MockColumnInfo("dim_region.region_name", "地区名称", "dim_region"),
        "dim_product.category": MockColumnInfo("dim_product.category", "产品类别", "dim_product"),
        "fact_order.sales_amount": MockColumnInfo("fact_order.sales_amount", "销售金额", "fact_order"),
    }
    result = group_columns_by_table(column_map)
    if len(result) == 3 and "fact_order" in result:
        print(f"  ✅ 分组成功：{list(result.keys())}")
        score += 1
    else:
        print(f"  ❌ 分组失败：{list(result.keys())}")

    # --- 测试6：补齐主外键 ---
    print("\n测试6：补齐主外键")
    table_groups = {
        "fact_order": [
            MockColumnInfo("fact_order.sales_amount", "销售金额", "fact_order"),
        ],
    }
    key_columns = {
        "fact_order": [
            MockColumnInfo("fact_order.order_id", "订单ID", "fact_order"),
            MockColumnInfo("fact_order.region_id", "地区ID", "fact_order"),
        ],
    }
    result = fill_key_columns(table_groups, key_columns)
    fact_cols = result["fact_order"]
    if len(fact_cols) == 3:
        print(f"  ✅ 补齐成功：{len(fact_cols)}个字段，包含主外键")
        score += 1
    else:
        print(f"  ❌ 补齐失败：{len(fact_cols)}个字段")

    # --- 总结 ---
    print("\n" + "=" * 50)
    print(f"总分：{score}/{total}")


if __name__ == "__main__":
    run_tests()
