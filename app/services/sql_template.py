# -*- coding: utf-8 -*-
"""
SQL 模板渲染器（RFC 刀1 改造：大模型角色压缩 / LLM 角色压缩）

为什么有这个文件
================
改前（2026-07-14 前）：
  `generate_sql` 节点让 LLM 一次性干两件事：
    1. 理解业务意图（哪些表、哪些条件、哪些聚合）
    2. 写 SQL 语法（SELECT 关键字、JOIN 顺序、缩进）
  这违反 "LLM 角色压缩" 原则 —— 让 LLM 干确定性工作（写 SQL 语法）会
  引入格式不一致、缩进差异、关键字大小写等不稳定因素。

改后（2026-07-14 后）：
  - LLM 节点（`generate_intent`）只输出结构化 JSON（业务意图）
  - 本文件用 jinja2 模板把 JSON 渲染成 SQL（确定性工作交给代码）
  - 好处：SQL 格式 100% 一致，缩进/换行/大小写都受控；
         改 SQL 风格只改模板不动 LLM；
         单元测试可以直接断言（已知 JSON → 已知 SQL）。

输入格式
========
本文件期望的输入是 `dict`，schema 如下：
{
  "select": [{"expr": str, "alias": str}],
  "from": str,                    # 含别名，如 "fact_order fo"
  "joins": [{"type": str, "table": str, "on": str}],
  "where": [str],
  "group_by": [str],
  "order_by": [str],
  "limit": int | None
}

字段缺失/类型错误时，本文件会**用空值兜底**而不是抛异常（链路容错）。

输出
====
- `render_sql(intent: dict) -> str`：渲染完整 SQL
- 去掉多余空行 + 合并多余空格（保证 SQL 紧凑）
"""

import logging
import re
from typing import Any

from jinja2 import Environment, StrictUndefined, Template

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# 模板字符串
# ─────────────────────────────────────────────────────────────────────
# 设计原则：
# 1. 用 jinja2 控制结构，不在模板里手写 Python 逻辑
# 2. 缩进固定 2 空格（团队规范）
# 3. 所有"是否有 X"的条件判断都用 `if X`，没有 X 就整段不输出
# 4. WHERE 用 AND 拼（不拼 OR —— LLM 不应该输出 OR 条件）
# ─────────────────────────────────────────────────────────────────────

_SQL_TEMPLATE_STR = """\
SELECT
  {%- for col in select %}
  {{ col.expr }} AS {{ col.alias }}{% if not loop.last %},{% endif %}
  {%- endfor %}
FROM {{ from_ }}
{%- for join in joins %}
{{ join.type | default('INNER') }} JOIN {{ join.table }} ON {{ join.on }}
{%- endfor %}
{%- if where %}
WHERE
  {%- for w in where %}
  ({{ w }}){% if not loop.last %} AND{% endif %}
  {%- endfor %}
{%- endif %}
{%- if group_by %}
GROUP BY {{ group_by | join(', ') }}
{%- endif %}
{%- if order_by %}
ORDER BY {{ order_by | join(', ') }}
{%- endif %}
{%- if limit is not none %}
LIMIT {{ limit }}
{%- endif %}"""


# 预编译模板（避免每次调用都重新解析）
_SQL_TEMPLATE = Template(_SQL_TEMPLATE_STR)


# ─────────────────────────────────────────────────────────────────────
# 兜底与归一化
# ─────────────────────────────────────────────────────────────────────

def _normalize_intent(intent: Any) -> dict:
    """把任何 input 归一化成 dict，字段缺失用空值兜底

    为什么不抛异常：
      - generate_intent 节点是 LLM 驱动的，输出结构可能偶发异常
      - 链路容错：渲染失败也要尽量返回一个能执行的 SQL（哪怕最差是 SELECT 1）
      - 比抛异常更适合 LangGraph 的"单节点失败不阻断整条链路"原则
    """
    if not isinstance(intent, dict):
        logger.warning(f"sql_template: intent 非 dict（实际 {type(intent)}），降级为空 intent")
        intent = {}

    def _ensure_list(v: Any) -> list:
        if v is None:
            return []
        if isinstance(v, list):
            return v
        logger.warning(f"sql_template: 期望 list 但收到 {type(v)}，降级为 []")
        return []

    def _ensure_str(v: Any, default: str = "") -> str:
        if v is None:
            return default
        if isinstance(v, str):
            return v
        return str(v)

    return {
        # select: 必须是 [{"expr", "alias"}] 形式
        "select": [
            {"expr": _ensure_str(item.get("expr")), "alias": _ensure_str(item.get("alias"))}
            for item in _ensure_list(intent.get("select"))
            if isinstance(item, dict) and item.get("expr")
        ],
        "from": _ensure_str(intent.get("from"), default="(SELECT 1) AS fallback"),
        "joins": [
            {
                "type": _ensure_str(item.get("type"), default="INNER"),
                "table": _ensure_str(item.get("table")),
                "on": _ensure_str(item.get("on")),
            }
            for item in _ensure_list(intent.get("joins"))
            if isinstance(item, dict) and item.get("table") and item.get("on")
        ],
        "where": [
            _ensure_str(w) for w in _ensure_list(intent.get("where")) if w
        ],
        "group_by": [
            _ensure_str(g) for g in _ensure_list(intent.get("group_by")) if g
        ],
        "order_by": [
            _ensure_str(o) for o in _ensure_list(intent.get("order_by")) if o
        ],
        "limit": intent.get("limit") if isinstance(intent.get("limit"), int) else None,
    }


def _collapse_whitespace(sql: str) -> str:
    """把多行压缩成单行 + 合并多余空格

    例：
        SELECT
          SUM(fo.order_amount) AS 销售额
        FROM fact_order fo
    →
        SELECT SUM(fo.order_amount) AS 销售额 FROM fact_order fo
    """
    sql = re.sub(r"\n\s*", " ", sql)
    sql = re.sub(r"\s+", " ", sql)
    return sql.strip()


# ─────────────────────────────────────────────────────────────────────
# 对外接口
# ─────────────────────────────────────────────────────────────────────

def render_sql(intent: dict) -> str:
    """把结构化 intent 渲染成 SQL 字符串

    关键点：
    - 输入是 dict，结构由 generate_intent 节点保证（但本函数容忍异常）
    - 输出是单行 SQL（去掉了模板里的换行/缩进）
    - select 为空时返回 "SELECT 1"（保证不返回空串，下游 validate_sql 不会卡住）

    Args:
        intent: dict，schema 见文件顶部 docstring

    Returns:
        str: 渲染后的 SQL，单行格式
    """
    normalized = _normalize_intent(intent)
    try:
        # 模板变量用 from_ 避免 Python 关键字冲突
        # 这里把 normalized['from'] 重命名成 from_ 再传给 jinja2
        render_kwargs = dict(normalized)
        if "from" in render_kwargs:
            render_kwargs["from_"] = render_kwargs.pop("from")
        rendered = _SQL_TEMPLATE.render(**render_kwargs)
    except Exception as e:
        # 模板渲染失败 —— 返回最保险的 SQL（至少 validate_sql 能通过）
        logger.error(f"sql_template: 模板渲染失败: {e}，返回兜底 SQL")
        return "SELECT 1 AS fallback"

    sql = _collapse_whitespace(rendered)

    # select 为空时强制返回 SELECT 1（虽然模板里有 from，但 select 才是 SQL 主句）
    if not normalized["select"]:
        logger.warning("sql_template: select 为空，返回 SELECT 1")
        sql = "SELECT 1 AS fallback"

    return sql
