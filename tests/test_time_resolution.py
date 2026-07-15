# -*- coding: utf-8 -*-
"""
时间解析单元测试（2026-07-14 P0 扩展新增）

为什么有这个文件
================
P0 扩展了 _resolve_relative_time()，新加了"最近 N 天 / 过去 N 天 / 本周 / 本月 / 今年"
等模式。本文件用一组已知 case 验证 Python 算日期的正确性。

跑法
====
    /Users/lunasama/.workbuddy/binaries/python/versions/3.13.12/bin/python3 tests/test_time_resolution.py

注意
====
本文件直接在测试里执行 _resolve_relative_time 的源码（用 exec），
通过注入 mock 的 date 来控制"今天"。这种方式避免了从 app.agent.nodes.rewrite_query
import（那个文件依赖 langchain，本地 3.13 装不上完整环境）。

如果上游 _resolve_relative_time 改了，**本文件必须同步更新**。
"""

import sys
from datetime import date, timedelta

# ─────────────────────────────────────────────────────────────────────
# 测试用的 _resolve_relative_time 源码（与 app/agent/nodes/rewrite_query.py 保持一致）
# ⚠️ 改上游时必须同步这里
# ─────────────────────────────────────────────────────────────────────
_SOURCE_CODE = '''
import re
from datetime import date, timedelta

def _resolve_relative_time(text):
    today = date.today()
    start_date = ""
    end_date = ""
    raw_expression = ""

    if "上一个自然月" in text:
        raw_expression = "上一个自然月"
        if today.month == 1:
            start = date(today.year - 1, 12, 1)
            end = date(today.year - 1, 12, 31)
        else:
            start = date(today.year, today.month - 1, 1)
            next_month_first = date(today.year, today.month, 1)
            end = next_month_first - timedelta(days=1)
        start_date = start.strftime("%Y-%m-%d")
        end_date = end.strftime("%Y-%m-%d")
        text = text.replace("上一个自然月", "").strip()

    if "当前自然月" in text:
        if not raw_expression:
            raw_expression = "当前自然月"
        start = date(today.year, today.month, 1)
        start_date = start.strftime("%Y-%m-%d")
        end_date = today.strftime("%Y-%m-%d")
        text = text.replace("当前自然月", "").strip()

    if "当前自然季度" in text:
        if not raw_expression:
            raw_expression = "当前自然季度"
        quarter_start_month = (today.month - 1) // 3 * 3 + 1
        start = date(today.year, quarter_start_month, 1)
        start_date = start.strftime("%Y-%m-%d")
        end_date = today.strftime("%Y-%m-%d")
        text = text.replace("当前自然季度", "").strip()

    if "上一个自然年" in text:
        if not raw_expression:
            raw_expression = "上一个自然年"
        start_date = f"{today.year - 1}-01-01"
        end_date = f"{today.year - 1}-12-31"
        text = text.replace("上一个自然年", "").strip()

    if "去年同一时期" in text:
        if not raw_expression:
            raw_expression = "去年同一时期"
        start_date = f"{today.year - 1}-{today.month:02d}-01"
        next_month_first = date(today.year, today.month, 1)
        end_date = (next_month_first - timedelta(days=1)).strftime("%Y-%m-%d")
        text = text.replace("去年同一时期", "").strip()

    m = re.search(r"(最近|过去)\s*(\d+)\s*天", text)
    if m and not raw_expression:
        n = int(m.group(2))
        kw = m.group(1)
        raw_expression = f"{kw} {n} 天"
        end = today
        start = today - timedelta(days=n - 1 if kw == "最近" else n)
        start_date = start.strftime("%Y-%m-%d")
        end_date = end.strftime("%Y-%m-%d")
        text = text.replace(m.group(0), "").strip()

    if "本周" in text and not raw_expression:
        raw_expression = "本周"
        monday = today - timedelta(days=today.weekday())
        start_date = monday.strftime("%Y-%m-%d")
        end_date = today.strftime("%Y-%m-%d")
        text = text.replace("本周", "").strip()

    if "本月" in text and not raw_expression:
        raw_expression = "本月"
        start = date(today.year, today.month, 1)
        start_date = start.strftime("%Y-%m-%d")
        end_date = today.strftime("%Y-%m-%d")
        text = text.replace("本月", "").strip()

    if "今年" in text and not raw_expression:
        raw_expression = "今年"
        start = date(today.year, 1, 1)
        start_date = start.strftime("%Y-%m-%d")
        end_date = today.strftime("%Y-%m-%d")
        text = text.replace("今年", "").strip()

    time_range = {
        "start_date": start_date,
        "end_date": end_date,
        "raw_expression": raw_expression,
    }
    return text, time_range
'''


# 用 exec 注入到一个独立 namespace
_namespace: dict = {}
exec(_SOURCE_CODE, _namespace)
_resolve_relative_time = _namespace["_resolve_relative_time"]


# ─────────────────────────────────────────────────────────────────────
# 测试 case
# ─────────────────────────────────────────────────────────────────────
def _test_case(label: str, query: str, mock_today: date, expect_start: str, expect_end: str):
    """通用测试：mock today + 跑 _resolve_relative_time + 验证 start/end

    通过在 namespace 里覆盖 `date` 名字来 mock 当前日期。
    fake_date 必须保留 .today() 静态方法（因为 _resolve_relative_time 内部用 date.today()）。
    """
    real_date = _namespace["date"]

    class FakeDateMeta(type):
        """让 fake_date.today() 返回 mock_today，其他构造走真实 date"""

        def today(cls):
            return mock_today

    class FakeDate(real_date, metaclass=FakeDateMeta):
        """继承真实 date 类 + metaclass 改 .today()"""
        pass

    # 关键：必须把 FakeDate 的 .today() 静态方法绑成返回 mock_today
    FakeDate.today = staticmethod(lambda: mock_today)

    _namespace["date"] = FakeDate
    try:
        text, time_range = _resolve_relative_time(query)
        if time_range["start_date"] == expect_start and time_range["end_date"] == expect_end:
            print(f"✅ {label}\n   query={query!r} → {expect_start} → {expect_end}")
            return True
        else:
            print(
                f"❌ {label}\n"
                f"   query={query!r}\n"
                f"   expect={expect_start} → {expect_end}\n"
                f"   got   ={time_range['start_date']} → {time_range['end_date']}"
            )
            return False
    finally:
        _namespace["date"] = real_date


def run_all():
    cases = [
        # ── 标准模式（回归测试）──
        ("上个月（普通月）", "上一个自然月销售额", date(2026, 7, 15), "2026-06-01", "2026-06-30"),
        ("上个月（跨年 1 月）", "上一个自然月销售额", date(2026, 1, 15), "2025-12-01", "2025-12-31"),
        ("上个月（2 月跨闰年）", "上一个自然月销售额", date(2024, 3, 15), "2024-02-01", "2024-02-29"),
        ("当前自然月", "当前自然月订单", date(2026, 7, 15), "2026-07-01", "2026-07-15"),
        ("当前自然季度（Q2 末）", "当前自然季度 GMV", date(2026, 6, 30), "2026-04-01", "2026-06-30"),
        ("当前自然季度（Q3 初）", "当前自然季度 GMV", date(2026, 7, 1), "2026-07-01", "2026-07-01"),
        ("上一个自然年", "上一个自然年销售额", date(2026, 7, 15), "2025-01-01", "2025-12-31"),

        # ── 扩展模式（P0 新增）──
        ("最近 7 天（含今天）", "最近 7 天订单", date(2026, 7, 15), "2026-07-09", "2026-07-15"),
        ("过去 30 天（不含今天）", "过去 30 天订单", date(2026, 7, 15), "2026-06-15", "2026-07-15"),
        ("最近 90 天", "最近 90 天 GMV", date(2026, 7, 15), "2026-04-17", "2026-07-15"),
        ("本周（周一）", "本周销量", date(2026, 7, 13), "2026-07-13", "2026-07-13"),
        ("本周（周三）", "本周销量", date(2026, 7, 15), "2026-07-13", "2026-07-15"),
        ("本周（周日）", "本周销量", date(2026, 7, 19), "2026-07-13", "2026-07-19"),
        ("本月", "本月订单", date(2026, 7, 15), "2026-07-01", "2026-07-15"),
        ("今年（年中）", "今年 GMV", date(2026, 7, 15), "2026-01-01", "2026-07-15"),
        ("今年（年初）", "今年 GMV", date(2026, 1, 1), "2026-01-01", "2026-01-01"),
    ]

    passed = 0
    for label, query, mock_today, expect_start, expect_end in cases:
        if _test_case(label, query, mock_today, expect_start, expect_end):
            passed += 1

    total = len(cases)
    print(f"\n{'=' * 60}\n{passed}/{total} passed")
    return passed == total


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
