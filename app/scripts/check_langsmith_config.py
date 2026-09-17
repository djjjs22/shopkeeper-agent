"""
LangSmith 配置检查脚本（不调 API，只看本地 .env）

用法：
    uv run python -m app.scripts.check_langsmith_config

输出：
    - .env 里的 LANGCHAIN_TRACING_V2 状态
    - LANGCHAIN_API_KEY 是否仍是占位符
    - LANGCHAIN_PROJECT / LANGCHAIN_ENDPOINT 是否设了
    - 不调任何 LangSmith API，纯本地检查

设计：
    - 读 .env 文件（不调外部）
    - 不返回 key 内容（只检查格式 / 长度 / 是不是占位符）
    - 退出码 0 = 配好可启用；退出码 1 = 还需藤子手动改 .env
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _load_dotenv() -> dict[str, str]:
    """读 .env 解析为 dict（不调 dotenv 库，避免依赖）"""
    env_path = Path(__file__).parents[2] / ".env"
    if not env_path.exists():
        return {}
    result: dict[str, str] = {}
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def check_langsmith_config() -> tuple[bool, list[str]]:
    """
    Returns:
        (all_ok, messages): all_ok=True 表示 .env 已配好可启用 LangSmith
    """
    msgs: list[str] = []
    all_ok = True

    env = _load_dotenv()
    if not env:
        msgs.append("❌ .env 文件不存在（从 .env.example 复制：cp .env.example .env）")
        return False, msgs

    msgs.append(f"✅ .env 文件存在（{len(env)} 个变量）")

    # ── 检查 LANGCHAIN_TRACING_V2 ──
    tracing = env.get("LANGCHAIN_TRACING_V2", "false").lower()
    if tracing == "true":
        msgs.append("✅ LANGCHAIN_TRACING_V2=true（启用）")
    else:
        msgs.append(
            "⚠️  LANGCHAIN_TRACING_V2=false（未启用，"
            "改成 true 才能上报 trace）"
        )
        all_ok = False

    # ── 检查 LANGCHAIN_API_KEY（不打印 key，只看格式）──
    api_key = env.get("LANGCHAIN_API_KEY", "")
    if not api_key:
        msgs.append("❌ LANGCHAIN_API_KEY 未设")
        all_ok = False
    elif api_key.startswith("lsv2_pt-your-"):
        msgs.append(
            "❌ LANGCHAIN_API_KEY 仍是占位符（"
            f"'{api_key[:20]}...'）—— 替换成真实 key"
        )
        all_ok = False
    elif not api_key.startswith("lsv2_pt_"):
        msgs.append(
            f"⚠️  LANGCHAIN_API_KEY 格式不像 LangSmith key "
            f"（开头 '{api_key[:10]}...'，通常是 lsv2_pt_）"
        )
        all_ok = False
    else:
        # 真实 key，但只显示前 8 位 + 后 4 位，中间 ****
        masked = f"{api_key[:8]}...{api_key[-4:]}（长度 {len(api_key)}）"
        msgs.append(f"✅ LANGCHAIN_API_KEY 已设（{masked}）")

    # ── 检查 LANGCHAIN_PROJECT ──
    project = env.get("LANGCHAIN_PROJECT", "")
    if not project:
        msgs.append("⚠️  LANGCHAIN_PROJECT 未设（默认 'default'，建议 'shopkeeper-agent'）")
    else:
        msgs.append(f"✅ LANGCHAIN_PROJECT={project}")

    # ── 检查 LANGCHAIN_ENDPOINT ──
    endpoint = env.get("LANGCHAIN_ENDPOINT", "")
    if not endpoint:
        msgs.append("⚠️  LANGCHAIN_ENDPOINT 未设（默认 https://api.smith.langchain.com）")
    else:
        msgs.append(f"✅ LANGCHAIN_ENDPOINT={endpoint}")

    return all_ok, msgs


def main() -> int:
    all_ok, msgs = check_langsmith_config()
    print("=" * 60)
    print("LangSmith 配置检查（不调 API，只看 .env）")
    print("=" * 60)
    for msg in msgs:
        print(msg)
    print("=" * 60)
    if all_ok:
        print("✅ 配置完整 — 重启 uvicorn 后即可上报 trace")
        print("   uv run uvicorn app.main:app --reload")
        return 0
    else:
        print("⚠️  还需修改 .env —— 详见上方 ❌ 项")
        return 1


if __name__ == "__main__":
    sys.exit(main())
