# -*- coding: utf-8 -*-
"""
同义词服务单元测试（2026-07-14 P1 新增）

跑法：
    /Users/lunasama/.workbuddy/binaries/python/versions/3.13.12/bin/python3 tests/test_synonyms_service.py
"""

import importlib.util
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SYNONYMS_PATH = os.path.join(
    os.path.dirname(THIS_DIR), "app", "services", "synonyms_service.py"
)


def _load_module():
    """动态加载 synonyms_service.py"""
    spec = importlib.util.spec_from_file_location("synonyms_service", SYNONYMS_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_expand_query(label: str, query: str, must_contain: list[str], must_not_contain: list[str] = None):
    """测试 expand_query：query 必含主词，扩展后必含别名"""
    mod = _load_module()
    mod.clear_cache()
    result = mod.expand_query(query)
    ok = all(word in result for word in must_contain)
    if must_not_contain:
        ok = ok and all(word not in result for word in must_not_contain)
    mark = "✅" if ok else "❌"
    print(f"{mark} {label}\n   query={query!r}\n   got  ={result!r}")
    return ok


def test_get_aliases(label: str, text: str, must_contain: list[str]):
    """测试 get_aliases：找到所有别名"""
    mod = _load_module()
    mod.clear_cache()
    result = mod.get_aliases(text)
    ok = all(word in result for word in must_contain)
    mark = "✅" if ok else "❌"
    print(f"{mark} {label}\n   text={text!r}\n   got ={result}")
    return ok


def test_format_for_prompt(label: str, must_contain: list[str]):
    """测试 format_for_prompt：拼成 LLM prompt 文本"""
    mod = _load_module()
    mod.clear_cache()
    result = mod.format_for_prompt()
    ok = all(word in result for word in must_contain)
    mark = "✅" if ok else "❌"
    print(f"{mark} {label}\n   got first 200 chars: {result[:200]!r}")
    return ok


def run_all():
    passed = 0
    total = 0

    # ── expand_query 测试 ──
    cases = [
        # 主词 → 扩展出所有别名
        ("销售额展开", "最近 7 天销售额", ["销售额", "GMV", "营业额", "成交总额", "order_amount"]),
        # 别名 → 也能识别并展开
        ("GMV（别名）展开", "最近 GMV", ["GMV", "销售额", "营业额"]),
        # 多类别同时命中
        ("指标+维度同时命中", "客户的销售额", ["销售额", "客户", "customer"]),
        # 取值（华北区）
        ("微信支付（取值）展开", "微信支付的订单", ["微信", "wechat", "微信支付"]),
        # 长串优先级（"微信支付" 比 "微信" 先匹配）
        ("长 alias 优先", "微信支付订单", ["微信支付", "微信", "wechat"]),
        # 没匹配
        ("无匹配时不扩展", "随机词", ["随机词"]),
        # 空 query
        ("空 query", "", [""]),
    ]
    for label, query, must_contain in cases:
        total += 1
        if test_expand_query(label, query, must_contain):
            passed += 1

    print()

    # ── get_aliases 测试 ──
    cases2 = [
        ("销售额别名", "最近 7 天销售额", ["销售额", "GMV", "营业额"]),
        ("GMV 别名", "GMV 趋势", ["GMV", "销售额"]),
        ("无匹配", "完全无关的词", []),
    ]
    for label, text, must_contain in cases2:
        total += 1
        if test_get_aliases(label, text, must_contain):
            passed += 1

    print()

    # ── format_for_prompt 测试 ──
    total += 1
    if test_format_for_prompt("prompt 文本包含 metrics", ["业务指标", "销售额", "GMV", "业务维度", "业务取值"]):
        passed += 1

    print(f"\n{'=' * 60}\n{passed}/{total} passed")
    return passed == total


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
