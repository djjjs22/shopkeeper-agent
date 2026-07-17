"""
SQL 修正节点

负责在 SQL 校验失败后，结合原问题 原 SQL 数据库错误和完整上下文做最小必要修正
只有 validate_sql 写入错误信息时，LangGraph 才会进入这个分支
"""

import re
import yaml
from langchain_core.prompts import PromptTemplate
from app.core.timing import timed_node
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import get_llm
from app.agent.state import DataAgentState
from app.core.log import logger
# 2026-07-11 改造：StrOutputParser → StripThinkStrParser
# 场景：修正 SQL（think 污染问题同 generate_sql）
# 详见 app/core/safe_json_parser.py 顶部 + docs/notes/eval_e2e_think兼容改造-20260711.md
from app.core.safe_json_parser import StripThinkStrParser
from app.prompt.prompt_loader import load_prompt


def _is_sql_like(text: str) -> bool:
    """判断 LLM 输出是否像 SQL（防止它输出中文解释）

    改前问题（2026-07-17）：correct_sql 跑了 44 秒后输出一段中文解释
       "由于未提供数据表结构，无法确定..."，被原样写入 state["sql"]，
       下游 run_sql 的 sql_safety 拦截后给前端返回"未找到数据"。
       用户看到的不是技术问题，是没数据。
    改后：用关键字兜底判断。如果不含 SELECT/WITH/USE 等关键字，
       当成 LLM 放弃治疗，**直接抛错**走 fallback，而不是把中文塞进 sql 字段。
    """
    if not text or not isinstance(text, str):
        return False
    cleaned = text.strip().upper()
    return any(cleaned.startswith(kw) for kw in ("SELECT", "WITH", "USE", "SHOW", "DESC", "EXPLAIN"))


@timed_node
async def correct_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """根据数据库 explain 报错修正上一轮 SQL"""
    llm = get_llm("correct_sql")  # 按 node_profiles 路由

    writer = runtime.stream_writer
    step = "校正SQL"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        # 校正 SQL 仍然需要完整上下文，避免模型只根据报错修语法却改丢业务语义
        table_infos = state["table_infos"]
        metric_infos = state["metric_infos"]
        date_info = state["date_info"]
        db_info = state["db_info"]
        query = state["query"]

        # sql 是待修正的候选 SQL，error 是数据库 explain 返回的具体错误信息
        sql = state["sql"]
        error = state["error"]

        prompt = PromptTemplate(
            template=load_prompt("correct_sql"),
            template_format="jinja2",
            input_variables=[
                "table_infos",
                "metric_infos",
                "date_info",
                "db_info",
                "query",
                "sql",
                "error",
            ],
        )
        # 修正后的输出仍然是一条纯 SQL 文本，用 StripThinkStrParser 兼容 <think> 块
        from app.core.safe_json_parser import _build_strip_parser_runnable
        output_parser = _build_strip_parser_runnable()
        chain = prompt | llm | output_parser

        result = await chain.ainvoke(
            {
                # 与生成节点保持一致，用 YAML 向模型提供稳定 可读的结构化上下文
                "table_infos": yaml.dump(
                    table_infos, allow_unicode=True, sort_keys=False
                ),
                "metric_infos": yaml.dump(
                    metric_infos, allow_unicode=True, sort_keys=False
                ),
                "date_info": yaml.dump(date_info, allow_unicode=True, sort_keys=False),
                "db_info": yaml.dump(db_info, allow_unicode=True, sort_keys=False),
                "query": query,
                "sql": sql,
                "error": error,
            }
        )

        logger.info(f"校正后的SQL：{result}")

        # 2026-07-17 修复：LLM 输出非 SQL 时直接报"放弃治疗"
        # 改前：44 秒后输出中文解释，被原样写入 state["sql"]，run_sql 拦截后
        #   前端看到"未找到数据"，排查时日志里 5 个节点都"正常"返回，根因难查。
        # 改后：识别到非 SQL 输出就抛错，让 run_sql 走 SELECT 1 兜底链路
        #   并把错误信息显式传给前端。
        if not _is_sql_like(result):
            logger.error(
                f"{step}: LLM 未返回 SQL，输出前 200 字符：{str(result)[:200]!r}"
            )
            raise ValueError(
                f"correct_sql 节点 LLM 未返回 SQL（输出：{str(result)[:100]}...）"
            )

        writer({"type": "progress", "step": step, "status": "success"})
        return {"sql": result}
    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        # 2026-07-17 修复：异常时不抛（让下游 fallback），
        # 而是返回原 sql + 在 error 里追加说明，方便 run_sql 拦截时把信息传到前端
        return {
            "sql": state.get("sql", "SELECT 1 AS fallback"),
            "error": f"correct_sql 节点失败：{e}",
        }
