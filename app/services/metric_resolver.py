# -*- coding: utf-8 -*-
"""
业务指标口径解析服务（2026-07-14 拆分自原 bind_tools 链路）

为什么有这个文件
================
改前问题（我之前写的 bind_tools 方案）：
  `generate_sql` 节点让 LLM 通过 `lookup_business_metric` tool 自己查指标
  口径。但 LLM 拿到口径描述后会自由发挥 —— 比如"支付成功率"明明定义为
  `COUNT(CASE WHEN payment_status='成功') * 1.0 / COUNT(*)`，LLM 却写成
  `SUM(CASE WHEN payment_status='成功' THEN 1 END) / SUM(1)` 看着像但
  数学上不完全等价。

改后方案：
  - 本服务在 sql_template 渲染前把指标的真实 SQL 片段准备好
  - sql_template 渲染时直接拼进去
  - LLM 在 generate_intent 节点只需说"用 支付成功率 指标"，由本服务
    翻译成完整 SQL 表达式

关键设计
========
- **降级友好**：指标不在 registry 里时返回 `None`（让 LLM 用通用业务知识）
- **同义词匹配**：用 synonyms_service 的索引处理"GMV"/"成交总额"/"销售额"
- **失败兜底**：仓库异常 / 配置缺失时返回 None，不抛异常

使用示例
========
```python
from app.services.metric_resolver import MetricResolver, lookup_metric

# 简单调用：查到支付成功率的 SQL 片段
metric = await lookup_metric("支付成功率")
# → {"name": "支付成功率",
#    "sql_expression": "COUNT(CASE WHEN payment_status='成功' THEN 1 END) * 1.0 / COUNT(*)",
#    "relevant_columns": ["fact_order.payment_status", "fact_order.order_id"],
#    "alias": ["成功率", "支付成功比例", ...]}

# 注入依赖
resolver = MetricResolver(meta_mysql_repository=meta_repo)
metric = await resolver.lookup("支付成功率")
```
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Optional

from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository


# ─────────────────────────────────────────────────────────────────────
# 依赖注入容器
# ─────────────────────────────────────────────────────────────────────


class MetricResolver:
    """业务指标解析服务的依赖容器

    用法：
        resolver = MetricResolver(meta_mysql_repository=meta_repo)
        metric = await resolver.lookup("支付成功率")
    """

    def __init__(self, meta_mysql_repository: Optional[MetaMySQLRepository] = None):
        self.meta_mysql_repository = meta_mysql_repository


# ─────────────────────────────────────────────────────────────────────
# 单点 API：查找业务指标
# ─────────────────────────────────────────────────────────────────────

_METRIC_CACHE: dict[str, dict[str, Any] | None] = {}


def _normalize_text(text: str) -> str:
    """简单归一化：去空白 + 小写

    用于同名匹配时的容错：
      "GMV" == "gmv" == " G M V "
    """
    return "".join(text.lower().split())


async def lookup_business_metric(
    metric_name: str,
    resolver: Optional[MetricResolver] = None,
    use_cache: bool = True,
) -> Optional[dict[str, Any]]:
    """查找业务指标的完整定义

    匹配策略（按优先级）：
      1. 精确匹配 metric.name
      2. 归一化匹配 metric.name（去空白 + 小写）
      3. 匹配 metric.alias 列表里的任一项
      4. 归一化匹配 alias

    Args:
        metric_name: 用户问的指标名（中文 / 英文 / 同义词都行）
        resolver: 依赖容器
        use_cache: 是否用本进程缓存

    Returns:
        找到时返回 dict：
            {
              "name": str,                # 规范名
              "sql_expression": str,       # 可直接拼到 SQL 的表达式
              "description": str,
              "relevant_columns": list,    # 用到的字段（用于 sql_template 字段引用）
              "alias": list,               # 同义词（用于 synonyms 服务联动）
            }
        没找到返回 None（让调用方走"通用业务知识"降级分支）
    """
    # 1. 缓存优先
    if use_cache and metric_name in _METRIC_CACHE:
        return _METRIC_CACHE[metric_name]

    # 2. 没有 repository：返回 None（让 LLM 走通用知识）
    if resolver is None or resolver.meta_mysql_repository is None:
        return None

    # 3. 拿全量指标列表
    try:
        metrics = await resolver.meta_mysql_repository.get_all_metric_infos()
    except Exception:
        return None

    # 4. 多级匹配
    target = _normalize_text(metric_name)
    candidate = None

    # 第一轮：精确匹配 / 归一化匹配 name
    for m in metrics:
        name = getattr(m, "name", None)
        if name == metric_name:
            candidate = m
            break
        if name and _normalize_text(name) == target:
            candidate = m
            break

    # 第二轮：alias 匹配
    if candidate is None:
        for m in metrics:
            alias = getattr(m, "alias", None) or []
            # alias 可能是 str（数据库返回差异）
            if isinstance(alias, str):
                import json

                try:
                    alias = json.loads(alias)
                except (json.JSONDecodeError, TypeError):
                    alias = []
            if metric_name in alias:
                candidate = m
                break
            # 归一化 alias 匹配
            for a in alias:
                if a and _normalize_text(a) == target:
                    candidate = m
                    break
            if candidate:
                break

    # 5. 没找到：写 None 缓存，返回 None
    if candidate is None:
        if use_cache:
            _METRIC_CACHE[metric_name] = None
        return None

    # 6. 归一化输出
    rc = getattr(candidate, "relevant_columns", None) or []
    if isinstance(rc, str):
        import json

        try:
            rc = json.loads(rc)
        except (json.JSONDecodeError, TypeError):
            rc = []

    alias = getattr(candidate, "alias", None) or []
    if isinstance(alias, str):
        import json

        try:
            alias = json.loads(alias)
        except (json.JSONDecodeError, TypeError):
            alias = []

    result = {
        "name": getattr(candidate, "name", metric_name),
        "sql_expression": getattr(candidate, "sql_expression", "")
        or getattr(candidate, "formula", ""),  # 兼容两种字段名
        "description": getattr(candidate, "description", ""),
        "relevant_columns": rc,
        "alias": alias,
    }

    # 7. 写缓存
    if use_cache:
        _METRIC_CACHE[metric_name] = result

    return result


# ─────────────────────────────────────────────────────────────────────
# 反向 API：列出全部指标（用于 prompt 注入）
# ─────────────────────────────────────────────────────────────────────


async def list_all_metrics(
    resolver: Optional[MetricResolver] = None,
) -> list[dict[str, Any]]:
    """拿到全部指标（用于 generate_intent 节点的 prompt 注入）

    返回简化版 dict list（只含 name + description + alias），
    sql_expression 太长不进 prompt，避免 prompt 过长。
    """
    if resolver is None or resolver.meta_mysql_repository is None:
        return []

    try:
        metrics = await resolver.meta_mysql_repository.get_all_metric_infos()
    except Exception:
        return []

    result = []
    for m in metrics:
        alias = getattr(m, "alias", None) or []
        if isinstance(alias, str):
            import json

            try:
                alias = json.loads(alias)
            except (json.JSONDecodeError, TypeError):
                alias = []
        result.append({
            "name": getattr(m, "name", ""),
            "description": getattr(m, "description", ""),
            "alias": alias,
        })
    return result


def clear_cache() -> None:
    """清缓存（测试用）"""
    _METRIC_CACHE.clear()