# -*- coding: utf-8 -*-
"""
确定性解析服务冒烟测试（2026-07-14 service 化重构）

覆盖 3 个 service 模块的核心功能：

  1. date_resolver
     - resolve_date() 返回今天 + 5 个常用边界
     - resolve_time_range() 把 query 里的相对时间转成 SQL 片段

  2. schema_resolver
     - list_table_columns() 拿到字段 + examples
     - repository 缺失时降级为空 list

  3. metric_resolver
     - lookup_business_metric() 找到指标（精确 / 归一化 / alias 三级匹配）
     - 找不到时返回 None
     - list_all_metrics() 拿到全量列表

设计原则（2026-07-14 重构）：
  - 不依赖 LangChain Tool / bind_tools，纯 Python service
  - 不依赖真实 MySQL，用 stub repository
  - 单 case < 1 秒，整体 < 10 秒
  - 不跑 50 条端到端（用户明确要求"小样本验证"）

运行：
  cd D:/shopkeeper-agent
  .venv/Scripts/python.exe -m pytest tests/test_deterministic_resolver_smoke.py -v
"""

import sys
from datetime import date
from pathlib import Path

# 让 "from app..." 可用
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402

from app.entities.column_info import ColumnInfo  # noqa: E402
from app.entities.metric_info import MetricInfo  # noqa: E402
from app.services.date_resolver import (  # noqa: E402
    clear_cache as clear_date_cache,
    resolve_date,
    resolve_time_range,
)
from app.services.metric_resolver import (  # noqa: E402
    MetricResolver,
    clear_cache as clear_metric_cache,
    list_all_metrics,
    lookup_business_metric,
)
from app.services.schema_resolver import (  # noqa: E402
    SchemaResolver,
    clear_cache as clear_schema_cache,
    list_table_columns,
)


# ───────────────────────────────────────────────────────────────────────
# 1. date_resolver 测试
# ───────────────────────────────────────────────────────────────────────


def test_resolve_date_returns_required_fields():
    """resolve_date() 必须返回 9 个键：today / yesterday / 5 个边界 + year + iso"""
    result = resolve_date(today=date(2026, 7, 14))
    expected_keys = {
        "today",
        "yesterday",
        "this_month_start",
        "last_month_start",
        "last_month_end",
        "this_quarter_start",
        "this_year_start",
        "year",
        "iso",
    }
    assert set(result.keys()) == expected_keys
    # 关键断言：跨月/跨年的边界处理正确
    assert result["today"] == "2026-07-14"
    assert result["this_month_start"] == "2026-07-01"
    assert result["last_month_start"] == "2026-06-01"
    assert result["last_month_end"] == "2026-06-30"
    assert result["this_quarter_start"] == "2026-07-01"
    assert result["this_year_start"] == "2026-01-01"
    assert result["year"] == 2026


def test_resolve_date_handles_january_cross_year():
    """1 月跨年：last_month_start 应该是上一年 12 月"""
    result = resolve_date(today=date(2026, 1, 15))
    assert result["today"] == "2026-01-15"
    assert result["last_month_start"] == "2025-12-01"
    assert result["last_month_end"] == "2025-12-31"
    assert result["this_year_start"] == "2026-01-01"


def test_resolve_time_range_extracts_last_month():
    """'本月各天销售额' 应该匹配 this_month"""
    result = resolve_time_range("本月各天销售额")
    assert "this_month" in result["matched_tags"]
    # where_clauses 应包含 BETWEEN
    assert any("BETWEEN" in w for w in result["where_clauses"])
    # date_col 默认是 fact_order.date_id
    assert result["date_col"] == "fact_order.date_id"


def test_resolve_time_range_supports_chinese_variants():
    """中英文混合 / 同义词都要识别"""
    cases = [
        ("过去 7 天 GMV", "last_7_days"),
        ("过去 30 天新客数", "last_30_days"),
        ("上个月订单数", "last_month"),
        ("今年销售额", "this_year"),
    ]
    for query, expected_tag in cases:
        result = resolve_time_range(query)
        assert expected_tag in result["matched_tags"], f"{query} 应匹配 {expected_tag}，实际 {result['matched_tags']}"


def test_resolve_time_range_empty_query():
    """空 query：matched_tags 空，where_clauses 空，但不报错"""
    result = resolve_time_range("")
    assert result["matched_tags"] == []
    assert result["where_clauses"] == []
    # resolved_date 仍然可用
    assert "today" in result["resolved_date"]


# ───────────────────────────────────────────────────────────────────────
# 2. schema_resolver 测试
# ───────────────────────────────────────────────────────────────────────


class _StubMetaRepo:
    """测试用元数据仓库桩：返回固定字段列表"""

    async def get_columns_by_table_id(self, table_id):
        return [
            ColumnInfo(
                id=f"c_{table_id}_pk",
                name="order_id",
                type="varchar",
                role="primary_key",
                table_id=table_id,
                description="订单 ID",
                examples=["ORD20250101001"],
                alias=[],
            ),
            ColumnInfo(
                id=f"c_{table_id}_amount",
                name="order_amount",
                type="float",
                role="measure",
                table_id=table_id,
                description="订单金额",
                examples=["100.0", "200.0"],
                alias=["销售额"],
            ),
        ]


@pytest.mark.asyncio
async def test_list_table_columns_returns_columns():
    """带依赖时返回字段列表"""
    clear_schema_cache()
    resolver = SchemaResolver(meta_mysql_repository=_StubMetaRepo())
    cols = await list_table_columns("fact_order", resolver=resolver)
    assert len(cols) == 2
    assert cols[0]["name"] == "order_id"
    assert cols[1]["name"] == "order_amount"
    assert isinstance(cols[1]["examples"], list)


@pytest.mark.asyncio
async def test_list_table_columns_without_resolver_returns_empty():
    """没有 repository 时降级为空 list，不抛错"""
    clear_schema_cache()
    cols = await list_table_columns("fact_order", resolver=None)
    assert cols == []


@pytest.mark.asyncio
async def test_list_table_columns_uses_cache():
    """同一次问数里重复调用只查一次仓库"""
    clear_schema_cache()

    # 计数 stub
    class _CountingRepo(_StubMetaRepo):
        def __init__(self):
            self.call_count = 0

        async def get_columns_by_table_id(self, table_id):
            self.call_count += 1
            return await super().get_columns_by_table_id(table_id)

    repo = _CountingRepo()
    resolver = SchemaResolver(meta_mysql_repository=repo)
    await list_table_columns("fact_order", resolver=resolver, use_cache=True)
    await list_table_columns("fact_order", resolver=resolver, use_cache=True)
    await list_table_columns("fact_order", resolver=resolver, use_cache=True)
    assert repo.call_count == 1


# ───────────────────────────────────────────────────────────────────────
# 3. metric_resolver 测试
# ───────────────────────────────────────────────────────────────────────


class _StubMetricRepo:
    """测试用指标仓库桩"""

    def __init__(self):
        self.metrics = [
            MetricInfo(
                id="m_payment_success_rate",
                name="支付成功率",
                description="支付成功的订单数占总订单数的比例",
                relevant_columns=["fact_order.payment_status", "fact_order.order_id"],
                alias=["成功率", "付款成功率"],
            ),
            MetricInfo(
                id="m_aov",
                name="AOV",
                description="平均订单金额",
                relevant_columns=["fact_order.order_amount"],
                alias=["客单价", "平均单价"],
            ),
        ]

    async def get_all_metric_infos(self):
        return self.metrics


@pytest.mark.asyncio
async def test_lookup_business_metric_exact_match():
    """精确匹配：'支付成功率' 应能查到"""
    clear_metric_cache()
    resolver = MetricResolver(meta_mysql_repository=_StubMetricRepo())
    result = await lookup_business_metric("支付成功率", resolver=resolver)
    assert result is not None
    assert result["name"] == "支付成功率"
    assert "fact_order.payment_status" in result["relevant_columns"]
    assert "成功率" in result["alias"]


@pytest.mark.asyncio
async def test_lookup_business_metric_alias_match():
    """别名匹配：'客单价' 应能查到 AOV"""
    clear_metric_cache()
    resolver = MetricResolver(meta_mysql_repository=_StubMetricRepo())
    result = await lookup_business_metric("客单价", resolver=resolver)
    assert result is not None
    assert result["name"] == "AOV"


@pytest.mark.asyncio
async def test_lookup_business_metric_returns_none_for_unknown():
    """未知指标：返回 None，不抛错"""
    clear_metric_cache()
    resolver = MetricResolver(meta_mysql_repository=_StubMetricRepo())
    result = await lookup_business_metric("这是一个不存在的指标", resolver=resolver)
    assert result is None


@pytest.mark.asyncio
async def test_lookup_business_metric_without_resolver_returns_none():
    """没有 repository：返回 None"""
    clear_metric_cache()
    result = await lookup_business_metric("支付成功率", resolver=None)
    assert result is None


@pytest.mark.asyncio
async def test_list_all_metrics_for_prompt():
    """list_all_metrics 返回简化版（不含 sql_expression）"""
    resolver = MetricResolver(meta_mysql_repository=_StubMetricRepo())
    result = await list_all_metrics(resolver)
    assert len(result) == 2
    # 不应包含 sql_expression（避免 prompt 过长）
    assert all("sql_expression" not in m for m in result)
    # 必须有 name + description + alias
    for m in result:
        assert "name" in m
        assert "description" in m
        assert "alias" in m