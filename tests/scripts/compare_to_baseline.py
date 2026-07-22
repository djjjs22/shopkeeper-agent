"""
Baseline 对比脚本（CI 回归门禁）

对比最新 eval 结果与 baseline.json，准确率下降超阈值则 exit 1（阻断合并）。

用法：
  # 跑完 eval_e2e 后
  python tests/scripts/compare_to_baseline.py [--threshold 0.02]

退出码：
  0 = 通过（准确率未下降或上升）
  1 = 阻断（准确率下降超阈值）

设计：
  - 自动找 tests/results/ 下最新的 eval_e2e_*.json
  - baseline 在 tests/results/baseline.json
  - 阈值默认 2%（可配）
  - 不只看总准确率，还看按难度的 delta（避免简单 case 涨掩盖复杂 case 跌）
"""

import argparse
import json
import sys
from pathlib import Path


def find_latest_result(results_dir: Path) -> Path | None:
    """找 results 目录下最新的 eval_e2e_*.json（排除 baseline.json）"""
    candidates = sorted(
        results_dir.glob("eval_e2e_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _diff_accuracy(v: dict) -> float:
    """从 by_difficulty 的 {total, passed} 或 {accuracy} 取准确率"""
    if "accuracy" in v:
        return v["accuracy"]
    total = v.get("total", 0)
    return v.get("passed", 0) / total if total else 0


def compare(latest: dict, baseline: dict, threshold: float) -> tuple[bool, str]:
    """对比最新结果与 baseline

    Returns:
        (passed, report): passed=True 表示未超阈值；report 是人类可读报告
    """
    lines = []
    passed = True

    latest_acc = latest.get("accuracy", 0)
    baseline_acc = baseline.get("accuracy", 0)
    delta = latest_acc - baseline_acc

    lines.append(f"总准确率: baseline={baseline_acc:.1%} → latest={latest_acc:.1%} (delta={delta:+.1%})")

    if delta < -threshold:
        passed = False
        lines.append(f"  ❌ 阻断：总准确率下降 {abs(delta):.1%} > 阈值 {threshold:.1%}")
    else:
        lines.append(f"  ✓ 通过：下降幅度 {abs(delta):.1%} ≤ 阈值 {threshold:.1%}")

    # 按难度对比（避免简单 case 涨掩盖复杂 case 跌）
    lines.append("\n按难度对比:")
    baseline_diff = baseline.get("by_difficulty", {})
    latest_diff = latest.get("by_difficulty", {})
    all_diffs = sorted(set(baseline_diff.keys()) | set(latest_diff.keys()))

    regression_count = 0
    for diff in all_diffs:
        b = _diff_accuracy(baseline_diff.get(diff, {}))
        l = _diff_accuracy(latest_diff.get(diff, {}))
        if b == 0 and l == 0:
            continue
        d = l - b
        mark = "❌" if d < -threshold else "✓"
        lines.append(f"  {mark} {diff}: {b:.1%} → {l:.1%} ({d:+.1%})")
        if d < -threshold:
            regression_count += 1

    if regression_count > 0:
        passed = False
        lines.append(f"\n❌ {regression_count} 个难度类别出现回归")

    return passed, "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="对比 eval 结果与 baseline")
    parser.add_argument(
        "--threshold", type=float, default=0.02,
        help="准确率下降阈值（默认 0.02 = 2%%）",
    )
    parser.add_argument(
        "--results-dir", type=str, default="tests/results",
        help="eval 结果目录",
    )
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    baseline_path = results_dir / "baseline.json"

    if not baseline_path.exists():
        print(f"⚠ baseline.json 不存在（{baseline_path}），跳过对比")
        print("  首次运行请先 cp tests/results/eval_e2e_<ts>.json tests/results/baseline.json")
        return 0  # 无 baseline 不阻断

    latest_path = find_latest_result(results_dir)
    if latest_path is None:
        print(f"⚠ {results_dir} 下无 eval_e2e_*.json，跳过对比")
        return 0

    with open(baseline_path, encoding="utf-8") as f:
        baseline = json.load(f)
    with open(latest_path, encoding="utf-8") as f:
        latest = json.load(f)

    # 兼容 by_difficulty 格式：baseline 可能存的是 {total, passed}，转成 accuracy
    for d in [baseline, latest]:
        if "by_difficulty" in d:
            for k, v in d["by_difficulty"].items():
                if isinstance(v, dict) and "accuracy" not in v:
                    total = v.get("total", 0)
                    p = v.get("passed", 0)
                    v["accuracy"] = p / total if total else 0

    passed, report = compare(latest, baseline, args.threshold)
    print(f"\n{'='*60}")
    print(f"CI 回归对比：{latest_path.name} vs baseline.json")
    print(f"{'='*60}")
    print(report)

    if passed:
        print("\n✅ CI 通过")
        return 0
    else:
        print("\n❌ CI 阻断：准确率回归超阈值，请检查改动")
        return 1


if __name__ == "__main__":
    sys.exit(main())
