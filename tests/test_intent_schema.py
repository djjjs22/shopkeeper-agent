# -*- coding: utf-8 -*-
"""
test_intent_schema.py
=====================

Pydantic QueryIntent 模型的单元测试。

覆盖场景：
- 完整字段构造（标准 happy path）
- alias 映射：from JSON 字段 → from_ Python 属性
- 缺字段用默认值（所有 list 默认空、from 默认空串、limit 默认 None）
- extra="ignore"：LLM 输出多余字段不报错
- type 错误：limit 是字符串、select 不是数组 → ValidationError
- model_dump(by_alias=True)：还回 JSON 时用 from 而不是 from_
"""

import pytest
from pydantic import ValidationError

from app.entities.intent_schema import JoinClause, QueryIntent, SelectExpr


class TestSelectExpr:
    def test_basic(self):
        e = SelectExpr(expr="SUM(fo.order_amount)", alias="销售额")
        assert e.expr == "SUM(fo.order_amount)"
        assert e.alias == "销售额"


class TestJoinClause:
    def test_default_type_is_inner(self):
        j = JoinClause(table="dim_region dr", on="fo.region_id = dr.region_id")
        assert j.type == "INNER"

    def test_explicit_left(self):
        j = JoinClause(type="LEFT", table="t a", on="a.id = b.id")
        assert j.type == "LEFT"

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            JoinClause(type="CROSS", table="t a", on="a.id = b.id")


class TestQueryIntent:
    """Pydantic QueryIntent 模型的所有字段行为"""

    def test_full_payload(self):
        """标准 happy path：完整 JSON 输入能解析"""
        payload = {
            "select": [{"expr": "SUM(fo.order_amount)", "alias": "销售额"}],
            "from": "fact_order fo",
            "joins": [
                {
                    "type": "INNER",
                    "table": "dim_region dr",
                    "on": "fo.region_id = dr.region_id",
                }
            ],
            "where": ["dr.region_name = '华北'"],
            "group_by": ["dr.region_name"],
            "order_by": ["销售额 DESC"],
            "limit": 10,
        }
        q = QueryIntent.model_validate(payload)
        assert q.from_ == "fact_order fo"  # alias 映射
        assert len(q.select) == 1
        assert q.select[0].alias == "销售额"
        assert q.joins[0].table == "dim_region dr"
        assert q.limit == 10

    def test_alias_round_trip(self):
        """model_dump(by_alias=True) 必须还原 JSON 字段名 from（不是 from_）"""
        q = QueryIntent.model_validate({"from": "fact_order fo"})
        dumped = q.model_dump(by_alias=True)
        assert "from" in dumped
        assert "from_" not in dumped
        assert dumped["from"] == "fact_order fo"

    def test_missing_fields_default(self):
        """所有 list 字段缺省时用空 list，limit 缺省 None"""
        q = QueryIntent.model_validate({})
        assert q.select == []
        assert q.joins == []
        assert q.where == []
        assert q.group_by == []
        assert q.order_by == []
        assert q.from_ == ""
        assert q.limit is None

    def test_extra_fields_ignored(self):
        """LLM 偶尔输出额外字段（如 thinking 残留），不能因此报错"""
        q = QueryIntent.model_validate(
            {
                "from": "t",
                "_thinking": "the user wants...",
                "extra_meta": {"key": "value"},
            }
        )
        assert q.from_ == "t"
        # extra 字段不会出现在 dump 里
        dumped = q.model_dump(by_alias=True)
        assert "_thinking" not in dumped
        assert "extra_meta" not in dumped

    def test_limit_wrong_type_rejected(self):
        """limit 是 dict 而非 int/None 时应抛 ValidationError（不能 coerce）"""
        with pytest.raises(ValidationError) as exc_info:
            QueryIntent.model_validate({"from": "t", "limit": {"v": 10}})
        assert "limit" in str(exc_info.value).lower()

    def test_select_not_list_rejected(self):
        """select 是 dict 而非 list 应抛 ValidationError"""
        with pytest.raises(ValidationError):
            QueryIntent.model_validate(
                {"from": "t", "select": {"expr": "x", "alias": "y"}}
            )

    def test_select_item_missing_alias_rejected(self):
        """SelectExpr 缺 alias 应抛 ValidationError"""
        with pytest.raises(ValidationError):
            QueryIntent.model_validate(
                {"from": "t", "select": [{"expr": "x"}]}
            )

    def test_join_missing_required_field(self):
        """JoinClause 缺 'on' 字段应抛 ValidationError"""
        with pytest.raises(ValidationError):
            QueryIntent.model_validate(
                {"from": "t", "joins": [{"table": "dim_x a"}]}
            )