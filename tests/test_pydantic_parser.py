# -*- coding: utf-8 -*-
"""
test_pydantic_parser.py
=======================

PydanticIntentParser 的单元测试，重点覆盖：
- think 块污染场景（M3/DeepSeek 模型典型输出）
- ```json``` 围栏
- schema 校验失败 → OutputParserException
- 非 JSON 输出 → OutputParserException
- format_instructions 包含 schema 提示
"""

import pytest
from langchain_core.exceptions import OutputParserException

from app.core.pydantic_parser import PydanticIntentParser
from app.entities.intent_schema import QueryIntent


@pytest.fixture
def parser() -> PydanticIntentParser:
    return PydanticIntentParser(pydantic_object=QueryIntent)


def _valid_payload() -> dict:
    return {
        "select": [{"expr": "COUNT(*)", "alias": "n"}],
        "from": "fact_order fo",
        "joins": [],
        "where": [],
        "group_by": [],
        "order_by": [],
        "limit": None,
    }


class TestParserHappyPath:
    def test_plain_json(self, parser):
        """干净的 JSON 输入"""
        import json

        text = json.dumps(_valid_payload(), ensure_ascii=False)
        result = parser.parse(text)
        assert isinstance(result, QueryIntent)
        assert result.from_ == "fact_order fo"
        assert result.select[0].alias == "n"

    def test_json_in_fence(self, parser):
        """```json ... ``` 围栏"""
        import json

        text = "```json\n" + json.dumps(_valid_payload(), ensure_ascii=False) + "\n```"
        result = parser.parse(text)
        assert result.from_ == "fact_order fo"

    def test_think_block_plus_json_fence(self, parser):
        """典型 M3/DeepSeek 输出：think 推理 + json 围栏"""
        text = (
            "<think>The user is asking for a simple count. "
            "I should output a count expression.</think>\n"
            "```json\n"
            '{"select": [{"expr": "COUNT(*)", "alias": "n"}], '
            '"from": "fact_order fo", "joins": [], "where": [], '
            '"group_by": [], "order_by": [], "limit": null}\n'
            "```"
        )
        result = parser.parse(text)
        assert result.select[0].expr == "COUNT(*)"

    def test_think_block_without_fence(self, parser):
        """think 块 + 裸 JSON（无围栏）"""
        text = (
            "<think>reasoning here</think>"
            '{"select": [{"expr": "COUNT(*)", "alias": "n"}], '
            '"from": "t", "joins": [], "where": [], '
            '"group_by": [], "order_by": [], "limit": null}'
        )
        result = parser.parse(text)
        assert result.from_ == "t"

    def test_chinese_alias_in_json_fence(self, parser):
        """中文 alias 完整保留（UTF-8 编码路径）"""
        text = (
            "```json\n"
            '{"select": [{"expr": "SUM(fo.amount)", "alias": "总销售额"}], '
            '"from": "fact_order fo", "joins": [], "where": '
            '["dr.region_name = \'华北\'"], "group_by": [], "order_by": [], "limit": null}\n'
            "```"
        )
        result = parser.parse(text)
        assert result.select[0].alias == "总销售额"
        assert result.where[0] == "dr.region_name = '华北'"


class TestParserErrors:
    """解析失败场景：必须抛 OutputParserException，让 langchain 自动 retry"""

    def test_invalid_json_raises(self, parser):
        """非 JSON 输入 → OutputParserException"""
        with pytest.raises(OutputParserException) as exc:
            parser.parse("hello world, no json here")
        assert "JSON 解析失败" in str(exc.value)

    def test_non_dict_json_raises(self, parser):
        """JSON 是 list 而非 dict → OutputParserException"""
        with pytest.raises(OutputParserException) as exc:
            parser.parse("[1, 2, 3]")
        assert "期望 dict" in str(exc.value)

    def test_schema_validation_fails(self, parser):
        """JSON 是 dict 但 SelectExpr 缺 alias → OutputParserException"""
        text = (
            '{"select": [{"expr": "COUNT(*)"}], "from": "t", '
            '"joins": [], "where": [], "group_by": [], '
            '"order_by": [], "limit": null}'
        )
        with pytest.raises(OutputParserException) as exc:
            parser.parse(text)
        # 错误信息应该提到具体字段（alias）
        assert "alias" in str(exc.value).lower()
        assert "schema 校验失败" in str(exc.value)

    def test_missing_required_join_field(self, parser):
        """JoinClause 缺 'on' → OutputParserException"""
        text = (
            '{"select": [], "from": "t", "joins": [{"table": "a b"}], '
            '"where": [], "group_by": [], "order_by": [], "limit": null}'
        )
        with pytest.raises(OutputParserException) as exc:
            parser.parse(text)
        assert "schema 校验失败" in str(exc.value)


class TestFormatInstructions:
    """format_instructions 必须在 prompt 里给 LLM 提供足够约束"""

    def test_includes_schema(self, parser):
        fi = parser.get_format_instructions()
        assert "JSON Schema" in fi
        # schema 包含 QueryIntent 的字段名
        assert "select" in fi
        assert "from" in fi
        assert "where" in fi
        assert "limit" in fi

    def test_includes_constraints(self, parser):
        fi = parser.get_format_instructions()
        # 硬约束关键词
        assert "只输出 JSON" in fi
        assert "不要输出" in fi
        # schema 块包裹在 ```json``` 里（让 LLM 看到示例）
        assert "```json" in fi