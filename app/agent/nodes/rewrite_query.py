"""
查询改写节点

只对 data_query 意图的输入执行，在意图分类之后、关键词抽取之前。
做三件事：
  1. 省略补全：如果用户在追问，结合历史对话补全用户省略的主语和宾语
     例："换成华北" + 历史里有"华北上个月销售额" → "上一个自然月的华北销售额"
  2. 时间表达标准化：把口语化的相对时间替换为标准表达
     例："上个月" → "上一个自然月"
  3. 提取历史继承（2026-07-14 新增）：从历史里提取用户省略的实体/条件/维度
     例："这些产品在哪些省份卖得好" + 历史有 SKU1/SKU2/SKU3
     → entities=["SKU1","SKU2","SKU3"], dimensions=["省份"]

为什么不直接用 LLM 算具体日期：
  LLM 算"上个月"会跨年、2月天数出错，用 Python datetime 确定性计算不会错。
  LLM 只负责语义层面的补全和标准化，具体日期交给本节点用 date.today() 计算，
  结果落到 state['time_range'] 结构化字段（2026-07-14 改造）。

2026-07-14 关键改造：
1. time_range 拆出：state['query'] 保持原句，时间单独存结构化字段
2. inherited_from_history 新增：从历史提取三类继承信息（实体/条件/维度）
"""

import asyncio
from datetime import date, timedelta
from typing import Any

# 2026-07-11 改造：StrOutputParser → StripThinkStrParser
# 场景：改写 query（think 块会污染 query 字段，污染下游）
# 详见 app/core/safe_json_parser.py 顶部 + docs/notes/eval_e2e_think兼容改造-20260711.md
from langchain_core.output_parsers import StrOutputParser  # noqa: F401  # 保留以备回滚
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import get_llm
from app.agent.state import DataAgentState, InheritedContext, TimeRangeState
from app.core.log import logger

# 2026-07-11 改造：StrOutputParser → StripThinkStrParser
# 场景：改写 query（think 块污染 query 字段，污染下游节点）
# 详见 app/core/safe_json_parser.py 顶部 + docs/notes/eval_e2e_think兼容改造-20260711.md
# 注意：这个文件用的是 _build_strip_parser_runnable（项目自定义的可运行封装），效果同 StripThinkStrParser
from app.core.safe_json_parser import SafeJsonOutputParser, _build_strip_parser_runnable
from app.core.timing import timed_node
from app.prompt.prompt_loader import load_prompt


def _format_history_for_prompt(history: list) -> str:
    """把历史对话格式化成 prompt 可读的文本

    如果没有历史返回"无"，有历史则格式化成"用户: xxx / 助手: xxx"
    """
    if not history:
        return "无"

    lines = []
    for msg in history:
        role_cn = "用户" if msg["role"] == "user" else "助手"
        lines.append(f"{role_cn}: {msg['content']}")
    return "\n".join(lines)


def _resolve_relative_time(text: str) -> tuple[str, TimeRangeState]:
    """用 Python 确定性解析常见相对时间表达，返回 (清理后文本, 时间范围)

    改前（2026-07-14 前）：返回字符串，把 "上一个自然月" 替换成
        "2025-12-01至2025-12-31" 糊在 query 文本里，污染 jieba/Embedding 召回。
    改后（2026-07-14）：返回 (清理后文本, TimeRangeState)：
        - 文本里删掉 "上一个自然月" 这种标准表达（避免 jieba 切到）
        - 时间范围单独落到结构化字段，SQL 生成节点消费

    LLM 算"上个月"可能跨年/2月天数出错，这里用 datetime 精确计算。
    只处理最常见的几种表达，LLM 已经把口语表达标准化了，这里做收口。

    支持的模式（2026-07-14 扩展）：
    - 标准模式（来自 rewrite_query.prompt 标准化对照表）：
        上一个自然月 / 当前自然月 / 当前自然季度 / 上一个自然年 / 去年同一时期
    - 扩展模式（P0 扩展，2026-07-14）：
        最近 N 天 / 过去 N 天 / 本周 / 本月 / 今年
    """
    import re

    today = date.today()
    start_date = ""
    end_date = ""
    raw_expression = ""

    # ── 标准模式（保持原样，不重写）──

    # 上一个自然月：月份减1，跨年时年份也减1
    if "上一个自然月" in text:
        raw_expression = "上一个自然月"
        if today.month == 1:
            start = date(today.year - 1, 12, 1)
            end = date(today.year - 1, 12, 31)
        else:
            start = date(today.year, today.month - 1, 1)
            # 月末：下个月第1天减1天（统一逻辑，所有月份都适用）
            next_month_first = date(today.year, today.month, 1)
            end = next_month_first - timedelta(days=1)
        start_date = start.strftime("%Y-%m-%d")
        end_date = end.strftime("%Y-%m-%d")
        # 从 query 文本里删掉这个标准表达，不让 jieba 切到
        text = text.replace("上一个自然月", "").strip()

    # 当前自然月：本月1号到今天
    if "当前自然月" in text:
        raw_expression = raw_expression or "当前自然月"
        start = date(today.year, today.month, 1)
        start_date = start.strftime("%Y-%m-%d")
        end_date = today.strftime("%Y-%m-%d")
        text = text.replace("当前自然月", "").strip()

    # 当前自然季度：季度起始月到当前
    if "当前自然季度" in text:
        raw_expression = raw_expression or "当前自然季度"
        quarter_start_month = (today.month - 1) // 3 * 3 + 1
        start = date(today.year, quarter_start_month, 1)
        start_date = start.strftime("%Y-%m-%d")
        end_date = today.strftime("%Y-%m-%d")
        text = text.replace("当前自然季度", "").strip()

    # 上一个自然年（只标记，SQL 拼条件时再展开）
    if "上一个自然年" in text:
        raw_expression = raw_expression or "上一个自然年"
        start_date = f"{today.year - 1}-01-01"
        end_date = f"{today.year - 1}-12-31"
        text = text.replace("上一个自然年", "").strip()

    # 去年同一时期（标记，SQL 拼条件时再展开）
    if "去年同一时期" in text:
        raw_expression = raw_expression or "去年同一时期"
        start_date = f"{today.year - 1}-{today.month:02d}-01"
        next_month_first = date(today.year, today.month, 1)
        end_date = (next_month_first - timedelta(days=1)).strftime("%Y-%m-%d")
        text = text.replace("去年同一时期", "").strip()

    # ── 扩展模式（P0 2026-07-14 新增）──

    # 最近 N 天 / 过去 N 天：从今天往前推 N 天
    # 例："最近 7 天" → (today - 6, today) —— 注意是"7 天"包含今天
    #     "过去 30 天" → (today - 30, today) —— "过去"按字面算 N 天
    # 关键：用正则从 query 里抽数字，避免 LLM 改写后漏掉
    m = re.search(r"(最近|过去)\s*(\d+)\s*天", text)
    if m and not raw_expression:  # 不覆盖标准模式
        n = int(m.group(2))
        kw = m.group(1)
        raw_expression = f"{kw} {n} 天"
        end = today
        # 业务约定：
        #   "最近 N 天" = 今天 + 往前 N-1 天，共 N 天
        #     例：最近 7 天 = (今天 - 6, 今天) = 共 7 天
        #   "过去 N 天" = 往前 N 天，不含今天
        #     例：过去 30 天 = (今天 - 30, 今天) = 共 30 天
        days_back = n - 1 if kw == "最近" else n
        start = today - timedelta(days=days_back)
        start_date = start.strftime("%Y-%m-%d")
        end_date = end.strftime("%Y-%m-%d")
        text = text.replace(m.group(0), "").strip()

    # 本周：本周一到今天（按周一开始，业务约定）
    if "本周" in text and not raw_expression:
        raw_expression = "本周"
        # weekday() 返回 0=周一, 6=周日
        monday = today - timedelta(days=today.weekday())
        start_date = monday.strftime("%Y-%m-%d")
        end_date = today.strftime("%Y-%m-%d")
        text = text.replace("本周", "").strip()

    # 本月：等价于"当前自然月"，LLM 标准化后应该用"当前自然月"
    # 这里做个兜底，防止 LLM 漏标准化
    if "本月" in text and not raw_expression:
        raw_expression = "本月"
        start = date(today.year, today.month, 1)
        start_date = start.strftime("%Y-%m-%d")
        end_date = today.strftime("%Y-%m-%d")
        text = text.replace("本月", "").strip()

    # 今年：今年 1 月 1 日到今天
    if "今年" in text and not raw_expression:
        raw_expression = "今年"
        start = date(today.year, 1, 1)
        start_date = start.strftime("%Y-%m-%d")
        end_date = today.strftime("%Y-%m-%d")
        text = text.replace("今年", "").strip()

    # ── 绝对时间解析（2026-07-17 修复 P0：2025 Q1 等不被识别）──
    # 改前问题：用户问"2025 年第一季度 GMV"，rewrite_query 没把绝对时间
    # 转成 time_range，generate_intent 拿不到 time_range，LLM 自作主张拼
    # `WHERE order_date BETWEEN ...` → 字段名错（fact_order 没有 order_date，
    # 是 date_id） → correct_sql 也拿不到表元数据 → 返回中文解释 → run_sql 拦截
    #
    # 支持格式：
    # - "2025 年第一季度" / "2025 年 Q1" / "2025Q1" → 2025-01-01 ~ 2025-03-31
    # - "2025 年第二季度" / "2025 年 Q2" / "2025Q2" → 2025-04-01 ~ 2025-06-30
    # - "2025 年" / "2025年" → 2025-01-01 ~ 2025-12-31
    # - "2025 年 5 月" / "2025年5月" / "2025-05" → 2025-05-01 ~ 2025-05-31
    # - "2025-01-01 至 2025-03-31" 等日期区间直接保留
    #
    # 优先级：标准/相对 模式优先，绝对时间只在没解析到时用

    if not raw_expression:
        # 季度匹配（年 + Q1~Q4，或者"年第1季度" 也能命中）
        m = re.search(r"(\d{4})\s*年?\s*[Qq]([1-4])", text)
        if m:
            year = int(m.group(1))
            q = int(m.group(2))
            # 季度起止月
            start_month = (q - 1) * 3 + 1
            end_month = q * 3
            start = date(year, start_month, 1)
            # 季度末月最后一天：下季度首日 - 1
            if q == 4:
                end = date(year, 12, 31)
            else:
                end = date(year, end_month + 1, 1) - timedelta(days=1)
            raw_expression = f"{year}年第{q}季度"
            start_date = start.strftime("%Y-%m-%d")
            end_date = end.strftime("%Y-%m-%d")
            text = text.replace(m.group(0), "").strip()

    if not raw_expression:
        # 自然语言季度（"2025 年第一季度" / "2025年第一季度" / "2025 年第 1 季度"）
        # 关键：必须先于 "2025 年" 匹配，否则会被吞掉
        # 兼容空格：年/第/数字/季度 之间的空格都可选
        # 中文数字 一/二/三/四 也要支持
        m = re.search(r"(\d{4})\s*年?\s*第\s*([一二三四1-4])\s*季度", text)
        if m:
            year = int(m.group(1))
            q_cn = m.group(2)
            q_map = {"一": 1, "二": 2, "三": 3, "四": 4, "1": 1, "2": 2, "3": 3, "4": 4}
            q = q_map.get(q_cn)
            if q is not None:
                start_month = (q - 1) * 3 + 1
                end_month = q * 3
                start = date(year, start_month, 1)
                if q == 4:
                    end = date(year, 12, 31)
                else:
                    end = date(year, end_month + 1, 1) - timedelta(days=1)
                raw_expression = f"{year}年第{q}季度"
                start_date = start.strftime("%Y-%m-%d")
                end_date = end.strftime("%Y-%m-%d")
                text = text.replace(m.group(0), "").strip()

    if not raw_expression:
        # 单月匹配（"2025 年 5 月" / "2025-05"）
        m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", text)
        if not m:
            m = re.search(r"(\d{4})-(\d{1,2})(?!\d)", text)
        if m:
            year = int(m.group(1))
            month = int(m.group(2))
            if 1 <= month <= 12:
                start = date(year, month, 1)
                # 下个月第一天 - 1
                if month == 12:
                    end = date(year, 12, 31)
                else:
                    end = date(year, month + 1, 1) - timedelta(days=1)
                raw_expression = f"{year}年{month}月"
                start_date = start.strftime("%Y-%m-%d")
                end_date = end.strftime("%Y-%m-%d")
                text = re.sub(r"\d{4}\s*年\s*\d{1,2}\s*月", "", text).strip()
                text = re.sub(r"\d{4}-\d{1,2}(?!\d)", "", text).strip()

    if not raw_expression:
        # 整年匹配（"2025 年" / "2025年"）—— 但要避免误匹配"2025 年 5 月"（已先匹配）
        m = re.search(r"(\d{4})\s*年(?!\s*\d)", text)
        if m:
            year = int(m.group(1))
            raw_expression = f"{year}年"
            start_date = f"{year}-01-01"
            end_date = f"{year}-12-31"
            text = text.replace(m.group(0), "").strip()

    time_range = TimeRangeState(
        start_date=start_date,
        end_date=end_date,
        raw_expression=raw_expression,
    )
    return text, time_range


async def _extract_inherited_context(llm, query: str, history: list) -> InheritedContext:
    """调用 LLM 提取历史继承（2026-07-14 新增）

    改前问题：多轮对话时用户说"这些产品"、"按门店拆一下"，LLM 生成 SQL 时
       要"猜"省略的主语/条件/维度，猜错率很高。
    改后：本函数让 LLM 显式提取三类继承（实体/条件/维度），结构化存储，
       generate_intent 节点直接消费。

    Args:
        llm: 由调用方通过 get_llm("rewrite_query") 注入的 model 实例
        query: 用户当前查询
        history: 历史对话列表

    Returns:
        InheritedContext: {entities, conditions, dimensions}
        - 任何字段提取失败时降级为空列表
    """
    history_text = _format_history_for_prompt(history)

    prompt = PromptTemplate(
        template=load_prompt("extract_inherited_context"),
        template_format="jinja2",
        input_variables=["history", "query"],
    )
    chain = prompt | llm | SafeJsonOutputParser()

    try:
        result = await chain.ainvoke({
            "history": history_text,
            "query": query,
        })
    except Exception as exc:
        logger.warning(f"[rewrite_query] 继承提取失败，使用空继承: {exc}")
        return InheritedContext(entities=[], conditions=[], dimensions=[])

    # 防御性：LLM 返回非 dict 时降级
    if not isinstance(result, dict):
        logger.warning(
            f"[rewrite_query] 继承提取返回非 dict（实际 {type(result)}），降级为空"
        )
        return InheritedContext(entities=[], conditions=[], dimensions=[])

    # 防御性：每个字段必须是 list of str
    def _normalize_list(v: Any) -> list[str]:
        if not isinstance(v, list):
            return []
        return [str(item) for item in v if item is not None]

    return InheritedContext(
        entities=_normalize_list(result.get("entities")),
        conditions=_normalize_list(result.get("conditions")),
        dimensions=_normalize_list(result.get("dimensions")),
    )


@timed_node
async def rewrite_query(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """改写用户查询：省略补全 + 时间标准化 + 提取历史继承（2026-07-14 新增）"""
    llm = get_llm("rewrite_query")  # 按 node_profiles 路由

    writer = runtime.stream_writer
    step = "查询改写"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        query = state["query"]
        history = state.get("history", [])

        # ── 第一步 + 第二步并行（2026-07-20 优化）──
        # 第一步：省略补全 + 时间标准化（输入：query + history）
        # 第二步：提取历史继承（输入：query + history）
        # 两者输入独立，第二步不依赖第一步输出，原串行实现白白多走一个 LLM RTT。
        # 改用 asyncio.gather 并发，单次问数节省 ~500ms-2s（一个 LLM 往返）。
        # gather 默认 fail-fast：任一抛错立即抛出，进入下面统一的 except 兜底。
        prompt = PromptTemplate(
            template=load_prompt("rewrite_query"),
            template_format="jinja2",
            input_variables=["query", "history"],
        )
        chain = prompt | llm | _build_strip_parser_runnable()

        rewritten_task = chain.ainvoke({
            "query": query,
            "history": _format_history_for_prompt(history),
        })
        inherited_task = _extract_inherited_context(llm, query, history)

        rewritten, inherited = await asyncio.gather(
            rewritten_task, inherited_task
        )
        rewritten = rewritten.strip()

        # 程序确定性解析：把标准化的时间表达替换成具体日期范围
        # 这一步保证日期计算不会出错，不依赖 LLM 的数学能力
        # 2026-07-14 改造：返回 (清理后文本, TimeRangeState)
        # 文本里不再有日期字符串，时间单独落到结构化字段
        _, time_range = _resolve_relative_time(rewritten)

        logger.info(
            f"查询改写: query={query!r} time_range={dict(time_range)} "
            f"inherited={dict(inherited)}"
        )

        # ── 关键改造（2026-07-14）：state['query'] 保持原句 ──
        # 改前问题：rewritten 字符串覆盖 state["query"]，导致 jieba 和
        #   Embedding 召回时被 "2025-12-01至2025-12-31" 污染
        # 改后：query 字段保持原句，time_range 单独存结构化时间，SQL
        #   生成节点消费 time_range 拼 WHERE，召回节点看到自然语言
        writer({"type": "progress", "step": step, "status": "success"})
        # 不再覆盖 query，让后续节点（关键词抽取/召回/filter）用原句
        return {
            "time_range": time_range,
            "inherited_from_history": inherited,
        }

    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        # 改写失败不阻断链路，time_range + inherited 都用空结构
        return {
            "time_range": TimeRangeState(
                start_date="", end_date="", raw_expression=""
            ),
            "inherited_from_history": InheritedContext(
                entities=[], conditions=[], dimensions=[]
            ),
        }
