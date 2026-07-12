# -*- coding: utf-8 -*-
"""
评测结果对比工具

跑法：
  cd /Users/lunasama/Downloads/Agent/shopkeeper-agent
  uv run python -m tests.eval_comparison results/eval_e2e_baseline.json results/eval_e2e_function_call.json

输出：
  - 两个版本的总准确率对比
  - 按难度的提升/下降
  - 谁修了什么 case、谁破坏了什么 case
"""

import json
import sys
from collections import defaultdict
from pathlib import Path


def load_results(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["details"]


def compare(before: list[dict], after: list[dict]) -> dict:
    """对比两个版本的评测结果"""
    before_map = {r["query"]: r for r in before}
    after_map = {r["query"]: r for r in after}

    all_queries = set(before_map.keys()) & set(after_map.keys())

    fixed = []  # before 失败 → after 通过
    broken = []  # before 通过 → after 失败
    both_passed = []
    both_failed = []

    for q in all_queries:
        b = before_map[q]
        a = after_map[q]
        if not b["passed"] and a["passed"]:
            fixed.append((q, b, a))
        elif b["passed"] and not a["passed"]:
            broken.append((q, b, a))
        elif a["passed"]:
            both_passed.append(q)
        else:
            both_failed.append(q)

    # 按难度分组
    def by_diff(results):
        d = defaultdict(list)
        for r in results:
            d[r["difficulty"]].append(r)
        return d

    before_diff = by_diff(before)
    after_diff = by_diff(after)

    return {
        "before_total": len(before),
        "before_passed": sum(1 for r in before if r["passed"]),
        "after_total": len(after),
        "after_passed": sum(1 for r in after if r["passed"]),
        "fixed": fixed,
        "broken": broken,
        "by_difficulty": {
            diff: {
                "before": f"{sum(1 for r in items if r['passed'])}/{len(items)}",
                "after": f"{sum(1 for r in after_diff.get(diff, []) if r['passed'])}/{len(after_diff.get(diff, []))}",
            }
            for diff, items in before_diff.items()
        },
    }


def print_report(comparison: dict, label_before: str, label_after: str):
    c = comparison
    print(f"\n=== 评测对比 ===")
    print(f"  Before [{label_before}]: {c['before_passed']}/{c['before_total']} = {c['before_passed']/c['before_total']:.1%}")
    print(f"  After  [{label_after}]:  {c['after_passed']}/{c['after_total']} = {c['after_passed']/c['after_total']:.1%}")
    delta = (c["after_passed"] - c["before_passed"]) / c["before_total"]
    arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
    print(f"  Delta: {arrow} {delta:+.1%}")

    print(f"\n--- 按难度 ---")
    for diff, d in sorted(c["by_difficulty"].items()):
        print(f"  {diff}:")
        print(f"    Before: {d['before']}, After: {d['after']}")

    if c["fixed"]:
        print(f"\n--- 新通过的 case ({len(c['fixed'])} 条) ---")
        for q, b, a in c["fixed"][:5]:
            print(f"  ✓ {q}")
            print(f"    Before score: {b['sql_match_score']:.2f}, After: {a['sql_match_score']:.2f}")

    if c["broken"]:
        print(f"\n--- 被破坏的 case ({len(c['broken'])} 条) ⚠️ ---")
        for q, b, a in c["broken"][:5]:
            print(f"  ✗ {q}")
            print(f"    Before score: {b['sql_match_score']:.2f}, After: {a['sql_match_score']:.2f}")
            print(f"    Generated after: {a['generated_sql'][:150]}")


def main():
    if len(sys.argv) < 3:
        print("用法: python -m tests.eval_comparison <before.json> <after.json> [label_before] [label_after]")
        print("例:   python -m tests.eval_comparison results/baseline.json results/v2.json baseline 'after Function Call'")
        sys.exit(1)

    before_path = sys.argv[1]
    after_path = sys.argv[2]
    label_before = sys.argv[3] if len(sys.argv) > 3 else "before"
    label_after = sys.argv[4] if len(sys.argv) > 4 else "after"

    if not Path(before_path).exists():
        print(f"找不到文件: {before_path}")
        sys.exit(1)
    if not Path(after_path).exists():
        print(f"找不到文件: {after_path}")
        sys.exit(1)

    before = load_results(before_path)
    after = load_results(after_path)
    comparison = compare(before, after)
    print_report(comparison, label_before, label_after)


if __name__ == "__main__":
    main()
