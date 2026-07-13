"""
SQL 生成节点

负责根据用户问题和前面整理出的表结构 指标 日期 数据库环境生成候选 SQL。
本节点只生成 SQL，不做校验和执行，后续会交给 validate_sql 和 run_sql 继续处理。

2026-07-14 改造：消费 state['time_range'] 结构化时间字段
  改前：query 字段被 rewrite 改写过，时间是 "2025-12-01至2025-12-31" 字符串。
       LLM 容易硬编码或漏掉时间条件。
  改后：query 是用户原句，time_range 是结构化字段。
       本节点把 time_range 也塞进 prompt，让 LLM 看到显式时间范围后再写 WHERE。
"""

import yaml
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState, TimeRangeState
from app.core.log import logger
# 2026-07-11 改造：StrOutputParser → StripThinkStrParser
# 场景：生成 SQL（think 块污染 SQL，validate_sql 会报语法错）
# 详见 app/core/safe_json_parser.py 顶部 + docs/notes/eval_e2e_think兼容改造-20260711.md
from app.core.safe_json_parser import StripThinkStrParser
from app.prompt.prompt_loader import load_prompt


def _format_time_range_for_prompt(time_range: TimeRangeState) -> str:
    """把结构化 time_range 序列化成 prompt 可读的文本

    无显式时间时返回 "无"，让 LLM 知道不写 WHERE 时间条件也是合理的。
    """
    if not time_range or not time_range.get("raw_expression"):
        return "无"
    return (
        f"原始表达: {time_range['raw_expression']}\n"
        f"起止日期: {time_range['start_date']} 至 {time_range['end_date']}"
    )


async def generate_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """基于已检索和过滤的上下文生成 SQL"""

    writer = runtime.stream_writer
    step = "生成SQL"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        # 这些上下文都由前置节点准备完成，模型只在给定表 字段 指标口径范围内生成 SQL
        table_infos = state["table_infos"]
        metric_infos = state["metric_infos"]
        date_info = state["date_info"]
        db_info = state["db_info"]
        query = state["query"]
        time_range = state.get("time_range", TimeRangeState(
            start_date="", end_date="", raw_expression=""
        ))

        prompt = PromptTemplate(
            template=load_prompt("generate_sql"),
            input_variables=[
                "table_infos",
                "metric_infos",
                "date_info",
                "db_info",
                "time_range",
                "query",
            ],
        )
        # SQL 生成节点只需要纯文本 SQL。用 StripThinkStrParser 兼容 M3/DeepSeek 的 <think> 块
        from app.core.safe_json_parser import _build_strip_parser_runnable
        output_parser = _build_strip_parser_runnable()
        chain = prompt | llm | output_parser

        result = await chain.ainvoke(
            {
                # YAML 更适合放进提示词：保留嵌套结构 顺序和中文说明，方便模型理解表字段关系
                "table_infos": yaml.dump(
                    table_infos, allow_unicode=True, sort_keys=False
                ),
                "metric_infos": yaml.dump(
                    metric_infos, allow_unicode=True, sort_keys=False
                ),
                "date_info": yaml.dump(date_info, allow_unicode=True, sort_keys=False),
                "db_info": yaml.dump(db_info, allow_unicode=True, sort_keys=False),
                "time_range": _format_time_range_for_prompt(time_range),
                "query": query,
            }
        )
        logger.info(f"生成的SQL：{result}")
        writer({"type": "progress", "step": step, "status": "success"})
        return {"sql": result}

    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise
