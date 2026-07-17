"""
表信息过滤节点

负责在合并后的候选表结构中筛选出当前问题真正需要的表和字段
这里让大模型只返回“保留哪些表和字段”的选择结果，真正的结构裁剪仍由程序完成
"""

import yaml
from langchain_core.prompts import PromptTemplate
from app.core.timing import timed_node
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import get_llm
from app.agent.state import DataAgentState, TableInfoState
from app.core.log import logger
# 2026-07-11 改造：JsonOutputParser → SafeJsonOutputParser
# 场景：过滤表信息（M3 模型输出 think 污染 JSON）
# 详见 app/core/safe_json_parser.py 顶部 + docs/notes/eval_e2e_think兼容改造-20260711.md
from app.core.safe_json_parser import SafeJsonOutputParser
from app.prompt.prompt_loader import load_prompt


@timed_node
async def filter_table(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    llm = get_llm("filter_table")  # 按 node_profiles 路由

    writer = runtime.stream_writer
    step = "过滤表信息"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        query = state["query"]
        table_infos: list[TableInfoState] = state["table_infos"]

        # table_infos 是嵌套结构，转成 YAML 后更适合放进提示词，也保留中文字段说明
        # 2026-07-17 改造：f-string → jinja2（与 generate_intent 节点同步）
        prompt = PromptTemplate(
            template=load_prompt("filter_table_info"),
            template_format="jinja2",
            input_variables=["query", "table_infos"],
        )
        # filter_table_info prompt 要求模型只输出 JSON 对象：表名 -> 字段名列表
        output_parser = SafeJsonOutputParser()
        # LCEL 管道：填充提示词 -> 调用模型 -> 解析 JSON
        chain = prompt | llm | output_parser

        result = await chain.ainvoke(
            {
                "query": query,
                "table_infos": yaml.dump(
                    table_infos, allow_unicode=True, sort_keys=False
                ),
            }
        )
        # 防御性校验：LLM 可能输出 null / list / 结构不符的 dict
        # 一旦结构异常，降级为「保留全部候选表」，避免因过滤逻辑崩溃而中断整条链路（刀 14）
        if not isinstance(result, dict):
            logger.warning(
                f"{step}: LLM 返回的表过滤结果非 dict（实际 {type(result)}），降级保留全部候选表"
            )
            writer({"type": "progress", "step": step, "status": "success"})
            return {"table_infos": table_infos}

        # 模型只负责选择，程序根据选择结果从原始 TableInfoState 中裁剪，避免模型重写复杂结构出错
        filtered_table_infos: list[TableInfoState] = []
        for table_info in table_infos:
            table_name = table_info["name"]
            # 模型只返回被选中的表名列表，未出现的表整张丢弃
            if table_name not in result:
                continue
            selected_columns = result[table_name]
            # 字段列表也可能异常，防御一下，异常时保留该表全部字段
            if not isinstance(selected_columns, list):
                logger.warning(
                    f"{step}: 表 {table_name} 的字段过滤结果非 list，保留该表全部字段"
                )
                filtered_table_infos.append(table_info)
                continue
            table_info["columns"] = [
                column_info
                for column_info in table_info["columns"]
                if column_info["name"] in selected_columns
            ]
            filtered_table_infos.append(table_info)

        logger.info(
            f"过滤后的表信息：{[filtered_table_info['name'] for filtered_table_info in filtered_table_infos]}"
        )
        writer({"type": "progress", "step": step, "status": "success"})
        return {"table_infos": filtered_table_infos}

    except Exception as e:
        # 2026-07-14 改造：JSON 解析失败时降级保留全部候选表，不让链路中断。
        # 同 filter_metric 行为对齐：链路稳定性优先，单节点异常不阻塞整体流程。
        logger.warning(f"{step} failed: {e}, 降级保留全部候选表")
        writer({"type": "progress", "step": step, "status": "success"})
        return {"table_infos": table_infos}
