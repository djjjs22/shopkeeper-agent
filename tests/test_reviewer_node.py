# -*- coding: utf-8 -*-
"""
test_reviewer_node.py
=====================

Reviewer 节点 + _parse_review_decision 单元测试。

覆盖：
- _parse_review_decision 解析各种 LLM 输出
- max_loop 保护
- 异常兜底
"""

import pytest

from app.agent.nodes.reviewer_node import _parse_review_decision, MAX_REVIEW_LOOP


class TestParseReviewDecision:
    """_parse_review_decision(raw) 解析各种 LLM 输出"""

    def test_well_formed_high_confidence(self):
        conf, action = _parse_review_decision(
            '{"confidence": 0.95, "action": "pass", "reason": "ok"}'
        )
        assert conf == 0.95
        assert action is None

    def test_well_formed_low_confidence(self):
        conf, action = _parse_review_decision(
            '{"confidence": 0.3, "action": "retry", "reason": "missing field"}'
        )
        assert conf == 0.3
        assert action == "retry"

    def test_malformed_json_returns_default(self):
        """JSON 解析失败 → 默认 0.5 + retry（fail-open）"""
        conf, action = _parse_review_decision("not json at all")
        assert conf == 0.5
        assert action == "retry"

    def test_markdown_wrapped_json(self):
        """LLM 输出经常被 ```json 围栏包（safe_parse_json 应该剥）"""
        raw = '```json\n{"confidence": 0.85, "action": "pass"}\n```'
        conf, action = _parse_review_decision(raw)
        assert conf == 0.85
        assert action is None

    def test_think_block_in_output(self):
        """LLM 输出可能含  块（safe_parse_json 应该跳过）"""
        raw = '一些解释的话 {"confidence": 0.6, "action": "retry"}'
        conf, action = _parse_review_decision(raw)
        assert conf == 0.6
        assert action == "retry"

    def test_action_aliases(self):
        """action 支持多种写法（pass / ok / accept 都视为 pass）"""
        conf, action = _parse_review_decision('{"confidence": 0.9, "action": "accept"}')
        assert action is None
        conf, action = _parse_review_decision('{"confidence": 0.9, "action": "ok"}')
        assert action is None

    def test_confidence_not_number(self):
        """confidence 不是数字 → 兜底 0.5"""
        conf, action = _parse_review_decision(
            '{"confidence": "high", "action": "pass"}'
        )
        assert conf == 0.5

    def test_missing_fields(self):
        """缺字段 → 兜底默认值"""
        conf, action = _parse_review_decision("{}")
        assert conf == 0.5
        assert action == "retry"


class TestMaxLoopProtection:
    """max_loop 保护：避免反思回路爆炸"""

    def test_max_loop_constant_is_2(self):
        """经验值 2 轮：第 1 轮 retry 后再审一次，再不行就返回"""
        assert MAX_REVIEW_LOOP == 2
