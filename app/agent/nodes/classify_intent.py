"""
意图分类节点

在链路最前端执行，判断用户输入属于哪一类意图：
  - chitchat       → 闲聊，走短路响应，不进 RAG 链路
  - metadata_query → 元数据查询，走短路响应，不生成 SQL
  - data_query     → 数据查询，走完整 RAG + SQL 生成链路

为什么需要这一步：
  原来所有输入无差别走 12 节点链路。用户说"你好"也要跑 jieba → Embedding
  → Qdrant → ES → LLM 生成 SQL，浪费 3+ 次 LLM 调用，最后返回错误。
  意图分类让闲聊和元数据查询短路，只有真正的数据查询才走完整链路。
"""

# 2026-07-11 改造：StrOutputParser → StripThinkStrParser
# 场景：意图分类（think 块会污染分类标签）
# 详见 app/core/safe_json_parser.py 顶部 + docs/notes/eval_e2e_think兼容改造-20260711.md
from langchain_core.output_parsers import StrOutputParser  # noqa: F401  # 保留以备回滚

from app.core.safe_json_parser import _build_strip_parser_runnable
from langchain_core.prompts import PromptTemplate
from app.core.timing import timed_node
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import get_llm
from app.agent.state import DataAgentState
from app.core.log import logger
from app.prompt.prompt_loader import load_prompt

# 三种合法意图，和 graph.py 的条件边 path_map 一一对应
VALID_INTENTS = ("chitchat", "metadata_query", "data_query")


@timed_node
async def classify_intent(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """对用户输入做意图分类，结果写入 state["intent"] 控制后续路由"""
    llm = get_llm("classify_intent")  # 按 node_profiles 路由

    writer = runtime.stream_writer
    step = "意图分类"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        query = state["query"]

        prompt = PromptTemplate(
            template=load_prompt("classify_intent"),
            template_format="jinja2",
            input_variables=["query"],
        )
        # 意图分类只需要纯文本输出（一个单词），用 _build_strip_parser_runnable 兼容 think 块
        chain = prompt | llm | _build_strip_parser_runnable()

        # temperature=0 的 LLM 仍然可能输出多余空格或换行，需要清洗
        result = await chain.ainvoke({"query": query})
        intent = result.strip().lower()

        # 兜底：如果 LLM 输出了无法识别的内容，默认走 data_query
        # 宁可多跑一次完整链路，也不要把真正的数据查询误判为闲聊
        if intent not in VALID_INTENTS:
            logger.warning(f"意图分类输出无法识别: {intent}，降级为 data_query")
            intent = "data_query"

        logger.info(f"意图分类结果: {intent}")

        writer({"type": "progress", "step": step, "status": "success"})
        return {"intent": intent}

    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        # 分类失败时降级为 data_query，保证不阻断链路
        return {"intent": "data_query"}
