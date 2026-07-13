# -*- coding: utf-8 -*-
"""
同义词服务（2026-07-14 P1 新增）

为什么有这个文件
================
改前问题：用户说"销售额"，LLM 自己去猜"GMV"、"营业额"，猜错率高。
改后方案：业务同义词沉淀在 conf/synonyms.yaml，本服务提供：
  1. `expand_query(query)` —— 把 query 里的主词/别名都展开，召回时一个词命中多向量
  2. `get_aliases(text)` —— 找到 text 里匹配的所有主词 + 别名组合，返回给 LLM prompt

关键设计
========
- **同义词只做"扩展"不做"改写"**。原始 query 保留，只是把别名加进去
  例："最近 7 天销售额" → "最近 7 天销售额 GMV 营业额 成交总额 order_amount"
- **不修改 query 字段**（防止 7-14 那次的"覆盖污染"问题）
- **懒加载 + 缓存**：用 @lru_cache，启动后只读一次 YAML

使用示例
========
```python
from app.services.synonyms_service import expand_query, get_aliases, format_for_prompt

# 召回时用：把别名加到 query 后再切 / 检索
expanded = expand_query("最近 7 天销售额")
# → "最近 7 天销售额 GMV 营业额 成交总额 order_amount"

# 召回 prompt 用：把同义词字典作为参考传给 LLM
aliases_text = format_for_prompt()
# → "metrics:\n  销售额: [GMV, 营业额, ...]\n  ..."
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
def load_synonyms() -> dict:
    """加载 conf/synonyms.yaml，启动后只读一次

    Returns:
        {
          "metrics": {"销售额": ["GMV", ...], ...},
          "dimensions": {"客户": ["customer", ...], ...},
          "values": {"微信": ["wechat", ...], ...}
        }
    """
    path = Path(__file__).parents[2] / "conf" / "synonyms.yaml"
    if not path.exists():
        # 配置文件不存在时降级为空字典（不让服务崩溃）
        return {"metrics": {}, "dimensions": {}, "values": {}}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {"metrics": {}, "dimensions": {}, "values": {}}


def _build_lookup_index() -> dict[str, tuple[str, str]]:
    """构建反向索引：alias → (category, primary)

    改前问题：每次 expand_query 都要遍历所有主词 + 别名，时间复杂度 O(N*M)
    改后：构建一次反向索引，后续查询 O(1)

    Returns:
        {
          "GMV": ("metrics", "销售额"),
          "营业额": ("metrics", "销售额"),
          "微信": ("values", "微信"),
          ...
        }
    """
    synonyms = load_synonyms()
    index: dict[str, tuple[str, str]] = {}
    for category, mapping in synonyms.items():
        for primary, aliases in mapping.items():
            # 主词也进索引（自己映射到自己）
            index[primary] = (category, primary)
            # 别名进索引
            for alias in aliases:
                if alias and alias not in index:  # 不覆盖（保留第一次出现）
                    index[alias] = (category, primary)
    return index


# 同样缓存
_lookup_index_cache: dict[str, tuple[str, str]] | None = None


def _get_lookup_index() -> dict[str, tuple[str, str]]:
    """获取反向索引（带缓存）"""
    global _lookup_index_cache
    if _lookup_index_cache is None:
        _lookup_index_cache = _build_lookup_index()
    return _lookup_index_cache


# ─────────────────────────────────────────────────────────────────────
# 对外 API
# ─────────────────────────────────────────────────────────────────────

def expand_query(query: str) -> str:
    """把 query 里的主词/别名都扩展出来

    例：
        "最近 7 天销售额" → "最近 7 天销售额 GMV 营业额 成交总额 order_amount"
        "微信支付的订单" → "微信支付的订单 wechat wx_pay 微信支付 WeChat"

    注意：
    - 原始 query 完全保留，只是把别名"加在后面"
    - 不修改任何字符顺序，只是补充召回需要的关键词
    - 用于召回节点（jieba 分词 / Embedding 之前调用一次）
    """
    if not query:
        return query

    index = _get_lookup_index()
    additions: list[str] = []
    seen: set[str] = set()

    # 按 token 长度从长到短匹配，避免 "微信支付" 被 "微信" 抢先匹配
    sorted_keys = sorted(index.keys(), key=len, reverse=True)

    for alias in sorted_keys:
        if alias in query and alias not in seen:
            category, primary = index[alias]
            # 把主词和所有别名都加进去
            mapping = load_synonyms()[category].get(primary, [])
            for word in [primary] + mapping:
                if word not in seen and word not in query:
                    additions.append(word)
                    seen.add(word)
            seen.add(alias)

    if not additions:
        return query
    return query + " " + " ".join(additions)


def get_aliases(text: str) -> list[str]:
    """找到 text 里所有匹配的主词 + 别名组合（去重后返回）

    例：
        "最近 7 天销售额" → ["销售额", "GMV", "营业额", "成交总额", "order_amount"]
    """
    if not text:
        return []

    index = _get_lookup_index()
    matches: list[str] = []
    seen: set[str] = set()

    sorted_keys = sorted(index.keys(), key=len, reverse=True)
    for alias in sorted_keys:
        if alias in text and alias not in seen:
            category, primary = index[alias]
            mapping = load_synonyms()[category].get(primary, [])
            for word in [primary] + mapping:
                if word not in seen:
                    matches.append(word)
                    seen.add(word)
            seen.add(alias)

    return matches


def format_for_prompt() -> str:
    """把同义词字典格式化成 LLM prompt 可读的文本

    用于把同义词字典塞进召回 prompt，让 LLM 扩展关键词时参考。
    """
    synonyms = load_synonyms()
    lines = []
    for category, mapping in synonyms.items():
        cat_name = {"metrics": "业务指标", "dimensions": "业务维度", "values": "业务取值"}.get(category, category)
        lines.append(f"## {cat_name}")
        for primary, aliases in mapping.items():
            lines.append(f"- {primary}：{', '.join(aliases)}")
        lines.append("")
    return "\n".join(lines)


def clear_cache() -> None:
    """清缓存（测试用，配置改了需要重载）"""
    global _lookup_index_cache
    load_synonyms.cache_clear()
    _lookup_index_cache = None
