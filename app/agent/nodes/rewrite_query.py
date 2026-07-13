"""
查询改写节点

只对 data_query 意图的输入执行，在意图分类之后、关键词抽取之前。
做两件事：
  1. 省略补全：如果用户在追问，结合历史对话补全用户省略的主语和宾语
     例："换成华北" + 历史里有"华北上个月销售额" → "上一个自然月的华北销售额"
  2. 时间表达标准化：把口语化的相对时间替换为标准表达
     例："上个月" → "上一个自然月"

为什么不直接用 LLM 算具体日期：
  LLM 算"上个月"会跨年、2月天数出错，用 Python datetime 确定性计算不会错。
  LLM 只负责语义层面的补全和标准化，具体日期交给本节点用 date.today() 计算，
  结果落到 state['time_range'] 结构化字段（2026-07-14 改造）。

2026-07-14 关键改造：
  改前：本节点把 "2025-12-01至2025-12-31华北销售额" 字符串覆盖 state['query']，
       导致 jieba 分词切到 "2025-12-01" 这种无意义 token、Embedding 召回噪声。
  改后：本节点只把时间范围落到 state['time_range']（结构化），state['query']
       保持原句不动。关键词抽取/三路召回看到的是自然语言，时间由 SQL 生成
       节点消费 time_range 拼 WHERE。
"""

from datetime import date, timedelta

# 2026-07-11 改造：StrOutputParser → StripThinkStrParser
# 场景：改写 query（think 块会污染 query 字段，污染下游）
# 详见 app/core/safe_json_parser.py 顶部 + docs/notes/eval_e2e_think兼容改造-20260711.md
from langchain_core.output_parsers import StrOutputParser  # noqa: F401  # 保留以备回滚

# 2026-07-11 改造：StrOutputParser → StripThinkStrParser
# 场景：改写 query（think 块污染 query 字段，污染下游节点）
# 详见 app/core/safe_json_parser.py 顶部 + docs/notes/eval_e2e_think兼容改造-20260711.md
# 注意：这个文件用的是 _build_strip_parser_runnable（项目自定义的可运行封装），效果同 StripThinkStrParser
from app.core.safe_json_parser import _build_strip_parser_runnable
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState, TimeRangeState
from app.core.log import logger
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
    """
    today = date.today()
    start_date = ""
    end_date = ""
    raw_expression = ""

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

    time_range = TimeRangeState(
        start_date=start_date,
        end_date=end_date,
        raw_expression=raw_expression,
    )
    return text, time_range


async def rewrite_query(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """改写用户查询：省略补全 + 时间标准化"""

    writer = runtime.stream_writer
    step = "查询改写"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        query = state["query"]
        history = state.get("history", [])

        prompt = PromptTemplate(
            template=load_prompt("rewrite_query"),
            input_variables=["query", "history"],
        )
        chain = prompt | llm | _build_strip_parser_runnable()

        # LLM 负责语义层面的补全和时间表达标准化
        rewritten = await chain.ainvoke({
            "query": query,
            "history": _format_history_for_prompt(history),
        })
        rewritten = rewritten.strip()

        # 程序确定性解析：把标准化的时间表达替换成具体日期范围
        # 这一步保证日期计算不会出错，不依赖 LLM 的数学能力
        # 2026-07-14 改造：返回 (清理后文本, TimeRangeState)
        # 文本里不再有日期字符串，时间单独落到结构化字段
        rewritten, time_range = _resolve_relative_time(rewritten)

        logger.info(f"查询改写: {query} → rewritten={rewritten!r}, time_range={time_range}")

        # ── 关键改造（2026-07-14）：state['query'] 保持原句 ──
        # 改前问题：rewritten 字符串覆盖 state["query"]，导致 jieba 和
        #   Embedding 召回时被 "2025-12-01至2025-12-31" 污染
        # 改后：query 字段保持原句，time_range 单独存结构化时间，SQL
        #   生成节点消费 time_range 拼 WHERE，召回节点看到自然语言
        writer({"type": "progress", "step": step, "status": "success"})
        # 不再覆盖 query，让后续节点（关键词抽取/召回/filter）用原句
        return {"time_range": time_range}

    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        # 改写失败不阻断链路，time_range 用空结构（SQL 生成时无时间过滤）
        return {
            "time_range": TimeRangeState(
                start_date="", end_date="", raw_expression=""
            )
        }
