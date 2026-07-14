# -*- coding: utf-8 -*-
"""
表 schema 解析服务（2026-07-14 拆分自原 bind_tools 链路）

为什么有这个文件
================
改前问题（我之前写的 bind_tools 方案）：
  `generate_sql` 节点让 LLM 通过 `list_table_columns` tool 自己查表字段 + 字段
  真实值。但 LLM 在收到 schema 后还要手写 SQL，格式不稳定 + 容易选错字段
  名（比如把 `payment_method` 写成 `pay_method`）。

改后方案：
  - 本服务在 sql_template 渲染前，把候选表的字段 + 真实值准备好
  - sql_template 渲染时直接消费这些字段，把字段名写进 SQL 模板
  - LLM 在 generate_intent 节点只需说"用 fact_order.payment_method"，不用
    操心字段真实值（这是元数据已知的事）

关键设计
========
- **MetaRepository 注入**：跟 query_service / meta_knowledge_service 同款
  模式，让测试可以塞 stub。
- **批量缓存**：同一次问数里 `list_table_columns("fact_order")` 调两次也只查一次
- **失败降级**：表不存在 / 字段为空时返回空 list，不抛异常（链路容错）

使用示例
========
```python
from app.services.schema_resolver import SchemaResolver, resolve_table_columns

# 简单调用：拿到 fact_order 的字段列表
cols = await resolve_table_columns("fact_order")
# → [{"name": "order_id", "type": "varchar", ...}, ...]

# 注入依赖：生产环境用真仓库，测试用 stub
resolver = SchemaResolver(meta_mysql_repository=meta_repo)
cols = await resolver.list_columns("fact_order")
```
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Optional

from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository


# ─────────────────────────────────────────────────────────────────────
# 依赖注入容器
# ─────────────────────────────────────────────────────────────────────


class SchemaResolver:
    """schema 解析服务的依赖容器

    用法：
        resolver = SchemaResolver(meta_mysql_repository=meta_repo)
        cols = await resolver.list_columns("fact_order")
    """

    def __init__(self, meta_mysql_repository: Optional[MetaMySQLRepository] = None):
        self.meta_mysql_repository = meta_mysql_repository


# ─────────────────────────────────────────────────────────────────────
# 单点 API：拿到某张表的字段
# ─────────────────────────────────────────────────────────────────────

_TABLE_COLUMNS_CACHE: dict[str, list[dict[str, Any]]] = {}


async def list_table_columns(
    table_name: str,
    resolver: Optional[SchemaResolver] = None,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """拿到某张表的所有字段 + 字段真实样例值

    Args:
        table_name: 物理表名（不含 schema），如 "fact_order"、"dim_product"
        resolver: 依赖容器（生产环境从 DataAgentContext 取，测试里塞 stub）
        use_cache: 是否使用本进程缓存（默认 True）

    Returns:
        list[dict]，每个元素是：
            {
              "name": str,
              "type": str,        # MySQL 类型字符串，如 "varchar(50)" / "int"
              "role": str,        # "primary_key" / "foreign_key" / "dimension" / "measure"
              "description": str,
              "examples": list,   # 真实样例值（用于 WHERE 条件的枚举值匹配）
            }

    失败兜底：
        - repository 为 None → 返回 []
        - 表不存在 / 仓库抛错 → 返回 []
        - examples 是 JSON 字符串 → 转 list（防御）
    """
    # 1. 缓存优先：避免重复查询
    if use_cache and table_name in _TABLE_COLUMNS_CACHE:
        return _TABLE_COLUMNS_CACHE[table_name]

    # 2. 没有 repository：返回空 list（容错）
    if resolver is None or resolver.meta_mysql_repository is None:
        return []

    # 3. 调仓库查字段
    try:
        columns = await resolver.meta_mysql_repository.get_columns_by_table_id(table_name)
    except Exception:
        # 仓库层失败（DB 异常等）兜底为空，不让链路崩
        return []

    # 4. 标准化为 dict list（避开 dataclass 序列化问题）
    result: list[dict[str, Any]] = []
    for col in columns:
        examples = getattr(col, "examples", None)
        # examples 可能是 list 也可能是 str（数据库返回差异）
        if isinstance(examples, str):
            import json

            try:
                examples = json.loads(examples)
            except (json.JSONDecodeError, TypeError):
                examples = []
        result.append({
            "name": getattr(col, "name", None),
            "type": getattr(col, "type", None),
            "role": getattr(col, "role", None),
            "description": getattr(col, "description", None),
            "examples": examples or [],
        })

    # 5. 写缓存
    if use_cache:
        _TABLE_COLUMNS_CACHE[table_name] = result

    return result


def clear_cache() -> None:
    """清缓存（测试用）"""
    _TABLE_COLUMNS_CACHE.clear()