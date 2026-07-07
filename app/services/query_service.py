"""
问数查询服务

负责把 API 层传入的自然语言问题转换成一次 LangGraph 工作流执行：
创建初始 State、组装 Runtime Context、消费 graph.astream 的流式输出，
并统一包装成 SSE 文本返回给路由层。

会话记忆集成（L1+L3）：
  在 query() 入口取出该 session_id 的历史对话，
  在生成 SQL 的 Prompt 里带上历史（解决多轮对话问题），
  执行完成后把本轮对话回写到 session_store。
"""

import json

from langchain_huggingface import HuggingFaceEndpointEmbeddings

from app.agent.context import DataAgentContext
from app.agent.graph import graph
from app.agent.state import DataAgentState
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
from app.services.prompt_builder import build_prompt
# build_prompt = 把历史对话 + 当前问题 拼成结构化 Prompt 的工具
from app.services.session_store import add_message, get_history
# get_history = 拿某 session 的历史；add_message = 把本轮对话存进去


class QueryService:
    """封装一次问数查询所需的业务编排逻辑"""

    def __init__(
        self,
        meta_mysql_repository: MetaMySQLRepository,
        embedding_client: HuggingFaceEndpointEmbeddings,
        dw_mysql_repository: DWMySQLRepository,
        column_qdrant_repository: ColumnQdrantRepository,
        metric_qdrant_repository: MetricQdrantRepository,
        value_es_repository: ValueESRepository,
    ):
        # MySQL 仓储分别负责元数据补全和真实数仓环境信息读取
        self.meta_mysql_repository = meta_mysql_repository
        self.dw_mysql_repository = dw_mysql_repository

        # 召回链路依赖的向量检索、Embedding 和全文检索能力由依赖层注入
        self.embedding_client = embedding_client
        self.column_qdrant_repository = column_qdrant_repository
        self.metric_qdrant_repository = metric_qdrant_repository
        self.value_es_repository = value_es_repository

    async def query(self, query: str, session_id: str = "default"):
        """执行一次问数工作流，并逐段产出 SSE 消息

        参数：
            query: 用户当前的自然语言问题
            session_id: 会话 ID（用于多轮对话记忆）
        """

        # ⭐ L1 检索：拿历史对话
        history = get_history(session_id, max_count=3)
        # 只取最近 3 轮，太多会撑爆 token

        # ⭐ L3 拼接：把历史 + 当前问题 拼成结构化 Prompt
        enhanced_query = build_prompt(query, history)
        # 把 enhanced_query 作为新的 query 传给 State
        # LLM 看到的不只是当前问题，还有"前面聊的是什么"

        # State 只放会被图节点读写和合并的业务数据，外部工具对象不塞进 State
        state = DataAgentState(query=enhanced_query)
        # Context 保存本次图执行需要复用的外部依赖，节点通过 runtime.context 读取
        context = DataAgentContext(
            column_qdrant_repository=self.column_qdrant_repository,
            embedding_client=self.embedding_client,
            metric_qdrant_repository=self.metric_qdrant_repository,
            value_es_repository=self.value_es_repository,
            meta_mysql_repository=self.meta_mysql_repository,
            dw_mysql_repository=self.dw_mysql_repository,
        )
        try:
            # 记录最后生成的结果数据（用于回写到 session_store）
            last_result = None
            # stream_mode="custom" 对应节点内部 writer(...) 写出的进度消息
            async for chunk in graph.astream(
                input=state, context=context, stream_mode="custom"
            ):
                # ⭐ L1 存：抓取最终结果用于回写
                if isinstance(chunk, dict) and chunk.get("type") == "result":
                    last_result = chunk.get("data")
                # SSE 要求每条消息以 data: 开头，并以两个换行符结束
                # ensure_ascii=False 保留中文进度文案，default=str 兜底处理日期等非 JSON 类型
                yield f"data: {json.dumps(chunk, ensure_ascii=False, default=str)}\n\n"

            # ⭐ L1 存：把本轮对话写回 session_store
            # 只有成功拿到结果才记录（失败的不污染历史）
            if last_result is not None:
                add_message(session_id, "user", query)
                # 注意：这里存的是用户原始 query，不是 enhanced_query
                # 否则历史里全是"【对话历史】...【任务类型】..."这种元数据
                result_text = str(last_result)[:200]  # 截断防止存太大
                add_message(session_id, "assistant", result_text)

        except Exception as e:
            # 流式接口已经开始返回后不能再改 HTTP 状态码，因此把异常也包装成一条 SSE 消息
            error = {"type": "error", "message": str(e)}
            yield f"data: {json.dumps(error, ensure_ascii=False, default=str)}\n\n"
