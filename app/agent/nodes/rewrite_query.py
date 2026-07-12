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
  LLM 只负责语义层面的补全和标准化，具体日期交给 add_extra_context 节点
  用 date.today() 确定性生成。
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
from app.agent.state import DataAgentState
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


def _resolve_relative_time(text: str) -> str:
    """用 Python 确定性解析常见相对时间表达

    LLM 算"上个月"可能跨年/2月天数出错，这里用 datetime 精确计算。
    只处理最常见的几种表达，LLM 已经把口语表达标准化了，这里做收口。
    """
    today = date.today()

    # 上一个自然月：月份减1，跨年时年份也减1
    if "上一个自然月" in text:
        if today.month == 1:
            start = date(today.year - 1, 12, 1)
            end = date(today.year - 1, 12, 31)
        else:
            start = date(today.year, today.month - 1, 1)
            # 月末：下个月第1天减1天（统一逻辑，所有月份都适用）
            next_month_first = date(today.year, today.month, 1)
            end = next_month_first - timedelta(days=1)
        text = text.replace("上一个自然月", f"{start.strftime('%Y-%m-%d')}至{end.strftime('%Y-%m-%d')}")

    # 当前自然月：本月1号到今天
    if "当前自然月" in text:
        start = date(today.year, today.month, 1)
        text = text.replace("当前自然月", f"{start.strftime('%Y-%m-%d')}至{today.strftime('%Y-%m-%d')}")

    # 当前自然季度：季度起始月到当前
    if "当前自然季度" in text:
        quarter_start_month = (today.month - 1) // 3 * 3 + 1
        start = date(today.year, quarter_start_month, 1)
        text = text.replace("当前自然季度", f"{start.strftime('%Y-%m-%d')}至{today.strftime('%Y-%m-%d')}")

    # 上一个自然年
    if "上一个自然年" in text:
        text = text.replace("上一个自然年", f"{today.year - 1}年")

    # 去年同一时期
    if "去年同一时期" in text:
        text = text.replace("去年同一时期", f"{today.year - 1}年{today.month}月")

    return text


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
        rewritten = _resolve_relative_time(rewritten)

        logger.info(f"查询改写: {query} → {rewritten}")

        writer({"type": "progress", "step": step, "status": "success"})
        # 改写后的 query 覆盖 state["query"]，后续节点用纯净的改写后问题
        return {"query": rewritten}

    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        # 改写失败不阻断链路，用原始 query 继续
        return {}
