"""
闲聊响应节点

当意图分类判定为 chitchat 时走这条短路，不进入 RAG 链路。
直接用 LLM 生成自然回复，跳过 jieba / Embedding / Qdrant / ES / SQL 全流程。
"""

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState
from app.core.log import logger

# 闲聊不需要单独的 prompt 文件——逻辑简单，直接内联
CHITCHAT_PROMPT = """你是一个友好的电商问数助手。用户正在和你闲聊，请自然地回应。

用户说：{query}

要求：
- 简短自然，不超过两句话
- 如果用户问你能做什么，提一句你可以帮他查询业务数据（销售额、订单、品类等）
- 不要编造数据
"""


async def respond_chitchat(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """闲聊短路响应：直接用 LLM 回复，不走 RAG 链路"""

    writer = runtime.stream_writer
    step = "闲聊响应"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        query = state["query"]

        prompt = PromptTemplate(
            template=CHITCHAT_PROMPT,
            input_variables=["query"],
        )
        chain = prompt | llm | StrOutputParser()
        result = await chain.ainvoke({"query": query})

        logger.info(f"闲聊响应: {result}")

        writer({"type": "progress", "step": step, "status": "success"})
        writer({"type": "result", "data": [{"回复": result}]})

    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        writer({"type": "error", "message": str(e)})
        raise
