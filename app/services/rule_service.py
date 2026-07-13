# -*- coding: utf-8 -*-
"""
业务规则服务（2026-07-14 P2 新增）

为什么有这个文件
================
改前问题：复杂业务规则（"已付款订单"=什么状态、什么字段）全靠 LLM 在 prompt 里猜。
        LLM 容易猜错："已付款" 写成 status='completed'（实际应该 'paid' AND paid_at IS NOT NULL）。
改后方案：业务规则沉淀在 conf/business_rules.yaml，本服务提供：
  1. `match_rules(query)` —— 根据 query 匹配规则，返回 WHERE 条件列表
  2. `format_for_prompt(query)` —— 把匹配的规则格式化成 LLM prompt 可读文本
  3. `get_rule_descriptions(query)` —— 拿到规则名称 + 描述，用于日志/前端展示

关键设计
========
- **规则只做"识别+返回条件"，不做"解释"**。LLM 拿到规则后必须**直接用** WHERE 条件，不准改写
- **降级友好**：配置文件不存在时返回空列表（不让服务崩溃）
- **关键词匹配按长度倒序**：避免"华北"被"华"抢先匹配
- **同一规则只匹配一次**（set 去重）

使用示例
========
```python
from app.services.rule_service import match_rules, format_for_prompt

# 在 generate_intent 节点里：
where_clauses = match_rules(state["query"])
# → ["(fo.status = 'paid' AND fo.paid_at IS NOT NULL)",
#    "(dr.region_name IN ('北京', '天津', '河北', '山西', '内蒙古'))",
#    "(dc.member_level = '黄金')"]

# 把这些作为 prompt 变量塞进 generate_intent.prompt
prompt_text = format_for_prompt(state["query"])
# → "【已匹配的业务规则】\n- 已付款订单: (fo.status = 'paid' AND fo.paid_at IS NOT NULL)\n..."
```
"""

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


# ─────────────────────────────────────────────────────────────────────
# 配置加载（懒加载 + 缓存）
# ─────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def load_rules() -> dict:
    """加载 conf/business_rules.yaml，启动后只读一次

    Returns:
        {
          "已付款订单": {
            "where": "(fo.status = 'paid' AND fo.paid_at IS NOT NULL)",
            "description": "已支付且支付时间不为空",
            "keywords": ["已付款", "已支付", ...]
          },
          ...
        }
    """
    path = Path(__file__).parents[2] / "conf" / "business_rules.yaml"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("rules", {})


def clear_cache() -> None:
    """清缓存（测试用）"""
    load_rules.cache_clear()


# ─────────────────────────────────────────────────────────────────────
# 对外 API
# ─────────────────────────────────────────────────────────────────────

def match_rules(query: str) -> list[str]:
    """根据 query 匹配业务规则，返回 WHERE 条件列表（去重）

    关键设计：
    - 关键词按长度倒序匹配（避免"华北"被"华"抢先）
    - 同一规则只匹配一次
    - 返回的 WHERE 条件可以直接拼到 SQL，不需要 LLM 再加工

    例：
        match_rules("已付款的华北黄金会员销售额")
        → [
          "(fo.status = 'paid' AND fo.paid_at IS NOT NULL)",
          "(dr.region_name IN ('北京', '天津', '河北', '山西', '内蒙古'))",
          "(dc.member_level = '黄金')"
        ]
    """
    if not query:
        return []

    rules = load_rules()
    matched_wheres: list[str] = []
    seen: set[str] = set()

    # 收集所有关键词，按长度倒序（长词优先）
    all_keywords: list[tuple[str, str]] = []  # (keyword, rule_name)
    for rule_name, rule_def in rules.items():
        for kw in rule_def.get("keywords", []):
            all_keywords.append((kw, rule_name))
    all_keywords.sort(key=lambda x: len(x[0]), reverse=True)

    for keyword, rule_name in all_keywords:
        if keyword in query and rule_name not in seen:
            where = rules[rule_name].get("where", "")
            if where:
                matched_wheres.append(where)
                seen.add(rule_name)

    return matched_wheres


def get_rule_descriptions(query: str) -> list[dict]:
    """拿到 query 命中的规则详情（名称 + 描述），用于日志/前端展示

    例：
        get_rule_descriptions("已付款的华北黄金会员销售额")
        → [
          {"name": "已付款订单", "description": "已支付且支付时间不为空"},
          {"name": "华北地区", "description": "华北五省/市/区"},
          {"name": "黄金会员", "description": "会员等级为黄金"}
        ]
    """
    if not query:
        return []

    rules = load_rules()
    matched: list[dict] = []
    seen: set[str] = set()

    all_keywords: list[tuple[str, str]] = []
    for rule_name, rule_def in rules.items():
        for kw in rule_def.get("keywords", []):
            all_keywords.append((kw, rule_name))
    all_keywords.sort(key=lambda x: len(x[0]), reverse=True)

    for keyword, rule_name in all_keywords:
        if keyword in query and rule_name not in seen:
            matched.append({
                "name": rule_name,
                "description": rules[rule_name].get("description", ""),
            })
            seen.add(rule_name)

    return matched


def format_for_prompt(query: str) -> str:
    """把 query 命中的规则格式化成 LLM prompt 可读文本

    用途：generate_intent 节点把这段文本作为 prompt 变量传给 LLM。
         LLM 看到规则后必须**直接用**这些 WHERE 条件，不准改写。

    例：
        format_for_prompt("已付款的华北黄金会员销售额")
        → "【已匹配的业务规则（必须直接使用以下 WHERE 条件）】\n
           1. 已付款订单: (fo.status = 'paid' AND fo.paid_at IS NOT NULL)\n
           2. 华北地区: (dr.region_name IN ('北京', '天津', '河北', '山西', '内蒙古'))\n
           3. 黄金会员: (dc.member_level = '黄金')\n"
    """
    if not query:
        return "无"

    rules = load_rules()
    matched: list[tuple[str, dict]] = []
    seen: set[str] = set()

    all_keywords: list[tuple[str, str]] = []
    for rule_name, rule_def in rules.items():
        for kw in rule_def.get("keywords", []):
            all_keywords.append((kw, rule_name))
    all_keywords.sort(key=lambda x: len(x[0]), reverse=True)

    for keyword, rule_name in all_keywords:
        if keyword in query and rule_name not in seen:
            matched.append((rule_name, rules[rule_name]))
            seen.add(rule_name)

    if not matched:
        return "无"

    lines = ["【已匹配的业务规则（必须直接使用以下 WHERE 条件，不要改写）】"]
    for i, (name, rule_def) in enumerate(matched, 1):
        lines.append(f"{i}. {name}: {rule_def.get('where', '')}")
        if rule_def.get("description"):
            lines.append(f"   说明: {rule_def['description']}")

    return "\n".join(lines)
