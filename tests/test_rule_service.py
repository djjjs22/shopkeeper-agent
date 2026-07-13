# -*- coding: utf-8 -*-
"""
业务规则服务单元测试（2026-07-14 P2 新增）

跑法：
    /Users/lunasama/.workbuddy/binaries/python/versions/3.13.12/bin/python3 tests/test_rule_service.py
"""

import importlib.util
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
RULE_PATH = os.path.join(
    os.path.dirname(THIS_DIR), "app", "services", "rule_service.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("rule_service", RULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_match_rules(label: str, query: str, must_contain: list[str], must_not_contain: list[str] = None):
    mod = _load_module()
    mod.clear_cache()
    result = mod.match_rules(query)
    ok = all(w in result for w in must_contain)
    if must_not_contain:
        ok = ok and all(w not in result for w in must_not_contain)
    mark = "✅" if ok else "❌"
    print(f"{mark} {label}\n   query={query!r}\n   got  ={result}")
    return ok


def test_format_for_prompt(label: str, query: str, must_contain: list[str]):
    mod = _load_module()
    mod.clear_cache()
    result = mod.format_for_prompt(query)
    ok = all(s in result for s in must_contain)
    mark = "✅" if ok else "❌"
    print(f"{mark} {label}\n   got first 200: {result[:200]!r}")
    return ok


# 用 S = "..." 的别名定义 SQL 字符串，避免外层引号冲突
S_PAID = "(fo.status = 'paid' AND fo.paid_at IS NOT NULL)"
S_REFUND = "(fo.refund_status = 'refunded')"
S_HUABEI = "(dr.region_name IN ('北京', '天津', '河北', '山西', '内蒙古'))"
S_GOLD = "(dc.member_level = '黄金')"
S_VIP = "(dc.member_level IN ('黄金', '钻石'))"
S_YTD = "(YEAR(fo.order_date) = YEAR(CURRENT_DATE))"


def run_all():
    passed = 0
    total = 0

    # ── match_rules 测试 ──
    cases = [
        # 订单状态
        (
            "已付款订单",
            "已付款的订单",
            [S_PAID],
        ),
        (
            "已退款订单",
            "已退款的订单",
            [S_REFUND],
        ),
        # 地区
        (
            "华北地区",
            "华北销售额",
            [S_HUABEI],
        ),
        (
            '长 alias 优先（不误匹配"华"）',
            "华北地区",
            [S_HUABEI],
        ),
        # 会员等级
        (
            "黄金会员",
            "黄金会员的销售额",
            [S_GOLD],
        ),
        (
            "VIP 客户 = 黄金 + 钻石",
            "VIP 客户的销售额",
            [S_VIP],
        ),
        # 多规则同时命中
        (
            "多规则复合（已付款 + 华北 + 黄金）",
            "已付款的华北黄金会员销售额",
            [S_PAID, S_HUABEI, S_GOLD],
        ),
        # 时间
        (
            "本年至今（YTD）",
            "本年至今的 GMV",
            [S_YTD],
        ),
        # 无匹配
        (
            "无匹配",
            "随便问个问题",
            [],
        ),
    ]
    for case in cases:
        label, query, must_contain = case[0], case[1], case[2]
        must_not = case[3] if len(case) > 3 else None
        total += 1
        if test_match_rules(label, query, must_contain, must_not):
            passed += 1

    print()

    # ── format_for_prompt 测试 ──
    fmt_cases = [
        (
            "多规则 prompt 文本",
            "已付款的华北黄金会员销售额",
            [
                "已匹配的业务规则",
                "已付款订单",
                "华北地区",
                "黄金会员",
                "必须直接使用",
            ],
        ),
        ("无匹配返回 无", "随便问个", ["无"]),
    ]
    for label, query, must_contain in fmt_cases:
        total += 1
        if test_format_for_prompt(label, query, must_contain):
            passed += 1

    print(f"\n{'=' * 60}\n{passed}/{total} passed")
    return passed == total


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
