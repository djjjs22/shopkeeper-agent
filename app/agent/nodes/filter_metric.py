"""
指标信息过滤节点

负责从合并后的候选指标中筛选出当前问题真正需要的指标
过滤后的指标会进入 SQL 生成上下文，帮助模型遵循正确的业务口径
"""

import yaml
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState, MetricInfoState
from app.core.log import logger
# 2026-07-11 新增：项目自己的 parser，兼容 M3/DeepSeek 的 <think> 块
# 2026-07-11 改造：JsonOutputParser → SafeJsonOutputParser
# 场景：过滤指标信息（M3 模型会输出 <think>...</think> 块污染 JSON）
# 详见 app/core/safe_json_parser.py 顶部 + docs/notes/eval_e2e_think兼容改造-20260711.md
from app.core.safe_json_parser import SafeJsonOutputParser
from app.prompt.prompt_loader import load_prompt


async def filter_metric(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """根据用户问题裁剪候选指标上下文"""

    writer = runtime.stream_writer
    step = "过滤指标信息"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        query = state["query"]
        metric_infos: list[MetricInfoState] = state["metric_infos"]

        # metric_infos 转成 YAML 后作为候选项交给模型，模型只需要返回被选中的指标名称
        prompt = PromptTemplate(
            template=load_prompt("filter_metric_info"),
            input_variables=["query", "metric_infos"],
        )
        # filter_metric_info prompt 要求模型只输出 JSON 数组
        output_parser = SafeJsonOutputParser()  # 详见顶部注释
        # LCEL 管道：填充提示词 -> 调用模型 -> 解析 JSON
        chain = prompt | llm | output_parser

        result = await chain.ainvoke(
            {
                "query": query,
                "metric_infos": yaml.dump(
                    metric_infos, allow_unicode=True, sort_keys=False
                ),
            }
        )
        # 防御性校验：LLM 可能输出 null / dict / 非 list 结构
        # 一旦结构异常，降级为「保留全部候选指标」，避免链路中断（刀 14）
        if not isinstance(result, list):
            logger.warning(
                f"{step}: LLM 返回的指标过滤结果非 list（实际 {type(result)}），降级保留全部候选指标"
            )
            writer({"type": "progress", "step": step, "status": "success"})
            return {"metric_infos": metric_infos}

        # 用模型返回的指标名称过滤原始结构，保留描述 依赖字段 别名等完整上下文
        filtered_metric_infos = [
            metric_info for metric_info in metric_infos if metric_info["name"] in result
        ]

        logger.info(
            f"过滤后的指标信息：{[filtered_metric_info['name'] for filtered_metric_info in filtered_metric_infos]}"
        )

        writer({"type": "progress", "step": step, "status": "success"})
        return {"metric_infos": filtered_metric_infos}

    except Exception as e:
        # 2026-07-14 改造：JSON 解析失败时降级保留全部候选指标，不让链路中断。
        # 同 filter_table 行为对齐：链路稳定性优先，单节点异常不阻塞整体流程。
        logger.warning(f"{step} failed: {e}, 降级保留全部候选指标")
        writer({"type": "progress", "step": step, "status": "success"})
        return {"metric_infos": metric_infos}
