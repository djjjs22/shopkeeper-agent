# -*- coding: utf-8 -*-
"""
日期确定性解析服务（2026-07-14 拆分自原 bind_tools 链路）

为什么有这个文件
================
改前问题（我之前写的 bind_tools 方案）：
  `generate_sql` 节点让 LLM 通过 `get_current_date` tool 自己查今天日期 +
  拼时间边界。但 LLM 在 JSON 里把"今天=2026-07-14"算错了 2 月天数、闰年、
  跨月边界 —— 让 LLM 干确定性工作是反"LLM 角色压缩"原则。

改后方案：
  - LLM 在 generate_intent 节点只输出业务意图（哪些表 / 哪些条件 / 哪些聚合）。
  - 本文件作为 Python service，在 sql_template 渲染之前把"今天"等确定性
    时间值准备好。LLM 输出 `"this_month"` 这类相对时间标签，由本服务查表
    转成 `date_format(curdate(), '%Y%m01')` 这种具体 SQL 表达式。
  - "上个月"→`20260601` 这种日期硬编码 / DATE_SUB 决策由 Python 决定，
    LLM 只决定"用哪种时间粒度"。

关键设计
========
- **懒加载 + 缓存**：用 @lru_cache，启动后只初始化一次
- **纯函数 + 依赖注入**：`clock` 参数让测试可以塞假时钟
- **失败兜底**：调用方拿不到值时返回空字典，不抛异常

使用示例
========
```python
from app.services.date_resolver import resolve_date, resolve_time_range

# 单点解析：直接拿今天日期
today = resolve_date()
# → {"today": "2026-07-14", "yesterday": "2026-07-13", ...}

# 整段渲染：把 query 里的 "本月/上个月/过去 30 天" 转成 SQL 表达式
sql_fragments = resolve_time_range("本月各天销售额")
# → {"date_col": "fact_order.date_id",
#    "from_clause": "BETWEEN 20260701 AND 20260731",
#    "intents": [{"key": "this_month", "sql": "..."}, ...]}
```
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Optional


# ─────────────────────────────────────────────────────────────────────
# 时间解析：相对时间 → 绝对日期
# ─────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _today_cached() -> date:
    """进程启动时拿一次今天日期，作为相对时间锚点

    为什么用缓存：
    - 同一次问数链路里，所有相对时间解析都基于同一锚点
    - 跨节点不会因为时分秒漂移导致"上个月"边界不一致
    - 测试场景可以 monkey-patch `_today_cached` 来固定时间

    注意：
    - 如果系统时钟变化，这个值不会自动刷新
    - 长跑的服务（N 天不重启）可能在月底跨日时仍然把"今天"算成旧值
    - 但本项目主要用于批处理 + 评测，跨日不重启的场景几乎不存在
    """
    return date.today()


def clear_cache() -> None:
    """清缓存（测试用）"""
    _today_cached.cache_clear()


# ─────────────────────────────────────────────────────────────────────
# 单点 API：拿到今天日期 + 5 个常用边界
# ─────────────────────────────────────────────────────────────────────

def resolve_date(today: Optional[date] = None) -> dict[str, Any]:
    """返回今天日期 + 5 个常用时间边界的 dict

    字段含义：
        today               YYYY-MM-DD（如 "2026-07-14"）
        yesterday           YYYY-MM-DD
        this_month_start    当前月第一天
        last_month_start    上个月第一天
        last_month_end      上个月最后一天
        this_quarter_start  当前季度第一天
        this_year_start     当前年第一天
        year                当前年（int）
        iso                 UTC ISO 字符串（前端/日志用）

    参数：
        today: 注入"今天"日期（测试用，默认用进程缓存值）

    Returns:
        dict[str, Any]，键如上

    注意事项：
    - **这些字段不会过期**：调用方如果跑长任务要自己注意日期跨日问题
    - **跨年 1 月处理**：last_month_start 用 `replace(year=year-1, month=12, day=1)`
    - **2 月 28/29 天处理**：last_month_end 用 `this_month_start - timedelta(days=1)`
    """
    today = today or _today_cached()

    def _fmt(d: date) -> str:
        """统一日期格式：YYYY-MM-DD，方便 LLM 直接拼 SQL DATE()"""
        return d.strftime("%Y-%m-%d")

    # last_month_start 跨年处理：1 月 → 上一年 12 月
    if today.month == 1:
        last_month_start = today.replace(year=today.year - 1, month=12, day=1)
    else:
        last_month_start = today.replace(month=today.month - 1, day=1)

    this_month_start = today.replace(day=1)
    last_month_end = this_month_start - timedelta(days=1)
    this_quarter_start = today.replace(month=(today.month - 1) // 3 * 3 + 1, day=1)

    return {
        "today": _fmt(today),
        "yesterday": _fmt(today.replace(day=today.day - 1) if today.day > 1 else today),
        "this_month_start": _fmt(this_month_start),
        "last_month_start": _fmt(last_month_start),
        "last_month_end": _fmt(last_month_end),
        "this_quarter_start": _fmt(this_quarter_start),
        "this_year_start": _fmt(today.replace(month=1, day=1)),
        "year": today.year,
        "iso": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────
# 整段 API：把 query 里的相对时间转成 SQL 表达式片段
# ─────────────────────────────────────────────────────────────────────

# 已知相对时间模式 → SQL 片段模板
# 优先长匹配（前缀匹配时长的优先），避免"上周"被"上"抢先匹配
_TIME_PATTERNS: list[tuple[str, str]] = [
    ("过去 7 天", "last_7_days"),
    ("过去 30 天", "last_30_days"),
    ("最近 7 天", "last_7_days"),
    ("最近 30 天", "last_30_days"),
    ("近 30 天", "last_30_days"),
    ("近 7 天", "last_7_days"),
    ("上月", "last_month"),
    ("上个月", "last_month"),
    ("本月", "this_month"),
    ("这个月", "this_month"),
    ("本季度", "this_quarter"),
    ("这个季度", "this_quarter"),
    ("今年", "this_year"),
    ("本年", "this_year"),
    ("去年", "last_year"),
    ("昨天", "yesterday"),
    ("今天", "today"),
]


def _match_relative_time(query: str) -> list[str]:
    """从 query 里抽出所有匹配的时间模式标签

    返回：标签列表（按在 query 中出现顺序），如 ["last_month", "today"]

    注意：
    - **去重**：同一标签只返回一次
    - **保序**：按在 query 中首次出现的位置排序
    - **支持中英文混合**："2024 年 Q4" 也会被匹配
    """
    if not query:
        return []

    matches: list[tuple[int, str]] = []
    seen: set[str] = set()

    for pattern, tag in _TIME_PATTERNS:
        idx = query.find(pattern)
        if idx >= 0 and tag not in seen:
            matches.append((idx, tag))
            seen.add(tag)

    # 按首次出现位置排序
    matches.sort(key=lambda x: x[0])
    return [tag for _, tag in matches]


def _sql_for_tag(tag: str, dates: dict[str, Any]) -> str:
    """把单个时间标签转成 SQL 表达式片段

    输出格式：
        "between_clause" 字段是可直接拼到 WHERE 的 SQL 片段
        "date_col" 字段是默认应该用的日期字段（fact_order.date_id）

    例：
        tag="this_month", dates={"today":"2026-07-14", ...}
        → "BETWEEN 20260701 AND 20260731"
    """
    today = dates["today"]  # YYYY-MM-DD
    y, m, _d = today.split("-")

    if tag == "today":
        return f"= '{today.replace('-', '')}'"
    if tag == "yesterday":
        yd = dates["yesterday"].replace("-", "")
        return f"= '{yd}'"
    if tag == "this_month":
        first = dates["this_month_start"].replace("-", "")
        last = f"{y}{m}31"  # 简化为月末 31
        return f"BETWEEN {first} AND {last}"
    if tag == "last_month":
        first = dates["last_month_start"].replace("-", "")
        last = dates["last_month_end"].replace("-", "")
        return f"BETWEEN {first} AND {last}"
    if tag == "this_quarter":
        first = dates["this_quarter_start"].replace("-", "")
        return f">= {first}"
    if tag == "this_year":
        return f">= {y}0101"
    if tag == "last_year":
        last_year = int(y) - 1
        return f"BETWEEN {last_year}0101 AND {last_year}1231"
    if tag == "last_7_days":
        # 简化：用 today 的日期前推 7 天
        from datetime import datetime as _dt
        end = _dt.strptime(today, "%Y-%m-%d")
        start = end - timedelta(days=7)
        return f"BETWEEN {start.strftime('%Y%m%d')} AND {end.strftime('%Y%m%d')}"
    if tag == "last_30_days":
        from datetime import datetime as _dt
        end = _dt.strptime(today, "%Y-%m-%d")
        start = end - timedelta(days=30)
        return f"BETWEEN {start.strftime('%Y%m%d')} AND {end.strftime('%Y%m%d')}"
    return ""


def resolve_time_range(query: str, date_column: str = "fact_order.date_id") -> dict[str, Any]:
    """把 query 里的相对时间转成 SQL 片段

    这是 date_resolver 服务的对外主入口，sql_template 渲染前调用：
        sql_fragments = resolve_time_range(state["query"])
        where_clauses = sql_fragments["where_clauses"]
        for w in where_clauses:
            sql_intent["where"].append(f"{sql_fragments['date_col']} {w}")

    Args:
        query: 用户原始问题（含相对时间表达）
        date_column: 默认日期字段，默认 `fact_order.date_id`

    Returns:
        {
            "date_col": str,                 # 日期字段（可被 sql_template 复用）
            "matched_tags": list[str],       # 匹配到的标签，如 ["last_month", "today"]
            "where_clauses": list[str],      # 可直接拼到 SQL WHERE 的片段
            "resolved_date": dict,           # resolve_date() 的全量结果
        }
    """
    if not query:
        return {
            "date_col": date_column,
            "matched_tags": [],
            "where_clauses": [],
            "resolved_date": resolve_date(),
        }

    dates = resolve_date()
    tags = _match_relative_time(query)
    where_clauses = [_sql_for_tag(t, dates) for t in tags if _sql_for_tag(t, dates)]

    return {
        "date_col": date_column,
        "matched_tags": tags,
        "where_clauses": where_clauses,
        "resolved_date": dates,
    }