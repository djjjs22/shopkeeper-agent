"""
问数查询服务

负责把 API 层传入的自然语言问题转换成一次 LangGraph 工作流执行：
创建初始 State、组装 Runtime Context、消费 graph.astream 的流式输出，
并统一包装成 SSE 文本返回给路由层。

会话记忆集成（重构后）：
  history 和 query 在 state 里分开存储（刀5 修复上下文污染）：
    - state["query"]   只放用户当前问题，纯净的，不含历史拼接文本
    - state["history"] 单独存历史对话，需要历史的节点（classify_intent、
                       rewrite_query、generate_sql）自己从 state 取
  不再用 build_prompt 把历史拼进 query —— 那会让 jieba 分词和 LLM
  扩展被历史话题污染（详见 RFC 刀5）。
"""

import asyncio
import json

from langchain_huggingface import HuggingFaceEndpointEmbeddings

from app.agent.context import DataAgentContext
from app.agent.graph import graph
from app.agent.state import DataAgentState, MultiAgentState
from app.agent.supervisor_graph import supervisor_graph
from app.core.log import logger
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
from app.services.session_store import add_message, get_history

# 2026-07-22 飞轮升级：查询日志服务（成功信号源，供 Pattern Bank 消费）
from app.services.query_log_service import query_log_service
# 2026-07-22 Semantic + Episodic Memory：用户偏好抽取 + 会话摘要
from app.services.user_profile_service import user_profile_service
from app.services.session_summarizer import session_summarizer

# ─────────────────────────────────────────────────────────────────────
# 错误脱敏（2026-07-20 #1）：给前端的友好文案，避免泄露 SQL/表结构
# ─────────────────────────────────────────────────────────────────────
_SERVICE_ERROR_MAP: list[tuple[str, str]] = [
    ("timeout", "请求超时，请缩小查询范围或换更精确的条件"),
    ("connection", "服务连接异常，请稍后重试"),
    ("rate", "请求过于频繁，请稍后再试"),
    ("memory", "查询结果过大，请加更精确的筛选条件"),
]


def _friendly_error(exc: Exception) -> str:
    """服务层异常 → 前端友好文案（不透传 str(exc) 原文）"""
    msg = str(exc).lower()
    for pattern, friendly in _SERVICE_ERROR_MAP:
        if pattern in msg:
            return friendly
    return "处理失败，已记录日志，请换一种问法或稍后重试"


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

    async def query_multi_agent(self, query: str, session_id: str = "default"):
        """Multi-Agent 模式（2026-07-17 改造）

        跟 query() 一样 SSE 流式，但走 supervisor_graph：
        planner 拆 sub_query → data_agent 跑 N 次（Send API 并行）→
        aggregator 合并 → reviewer 评分（max_loop=2 反思回路）

        设计：
        - state["use_multi_agent"] 不写入（multi-agent 是 path-level 决策，
          不污染子 graph state）
        - 复用相同的 history / DataAgentContext 组装逻辑
        - 异常处理跟 query() 一致：包装成 SSE 错误消息

        与 query() 的区别：
        - 老 graph 是 13 节点单链；supervisor_graph 是 4 节点（planner +
          subgraph + aggregator + reviewer）
        - 多 sub_query 时并行跑多个子图；少 sub_query 时退化为近原行为
        """
        # 拿历史对话（与 query() 一致）
        history = await get_history(session_id, max_count=3)

        # 2026-07-20 (#10)：multi-agent 路径用 MultiAgentState（含 plan/sub_results 等字段）
        state = MultiAgentState(query=query, history=history)
        context = DataAgentContext(
            column_qdrant_repository=self.column_qdrant_repository,
            embedding_client=self.embedding_client,
            metric_qdrant_repository=self.metric_qdrant_repository,
            value_es_repository=self.value_es_repository,
            meta_mysql_repository=self.meta_mysql_repository,
            dw_mysql_repository=self.dw_mysql_repository,
        )
        try:
            last_result = None
            # 2026-07-22 飞轮升级：记录端到端延迟
            import time as _time
            _start_ts = _time.monotonic()
            # supervisor_graph 节点数少，progress 事件也更稀疏（planner / aggregator / reviewer 三个）
            async for chunk in supervisor_graph.astream(
                input=state, context=context, stream_mode="custom"
            ):
                if isinstance(chunk, dict) and chunk.get("type") == "result":
                    last_result = chunk.get("data")
                yield f"data: {json.dumps(chunk, ensure_ascii=False, default=str)}\n\n"

            # 2026-07-22 飞轮升级：multi-agent 路径也记 query_log（reviewer_score 可后续从 trace 补）
            _elapsed_ms = (_time.monotonic() - _start_ts) * 1000
            query_log_service.record(
                session_id=session_id, query=query, sql="",
                success=last_result is not None, latency_ms=_elapsed_ms,
            )

            if last_result is not None:
                # ⭐ 2026-07-20 优化：两次 add_message 并行（同 query() 路径）
                result_text = str(last_result)[:200]
                await asyncio.gather(
                    add_message(session_id, "user", query),
                    add_message(session_id, "assistant", result_text),
                )
                # 2026-07-22 Semantic + Episodic Memory（同 query() 路径）
                await user_profile_service.update(session_id, query)
                await session_summarizer.summarize_if_needed(session_id)

        except Exception as e:
            # 2026-07-20（#1 脱敏）：服务端记完整异常，给前端友好文案
            logger.exception(f"[query_multi_agent] 链路异常: {e}")
            query_log_service.record(
                session_id=session_id, query=query, sql="",
                success=False, latency_ms=(_time.monotonic() - _start_ts) * 1000,
            )
            error = {"type": "error", "message": _friendly_error(e)}
            yield f"data: {json.dumps(error, ensure_ascii=False, default=str)}\n\n"

    async def query(self, query: str, session_id: str = "default"):
        """执行一次问数工作流，并逐段产出 SSE 消息

        参数：
            query: 用户当前的自然语言问题
            session_id: 会话 ID（用于多轮对话记忆）
        """

        # 拿历史对话，只取最近 3 轮，太多会撑爆 token
        history = await get_history(session_id, max_count=3)

        # history 和 query 在 state 里分开存储（刀5 修复上下文污染）
        # state["query"] 只放纯净的当前问题，不再被 build_prompt 拼接
        # state["history"] 单独存历史，需要历史的节点自己取
        state = DataAgentState(query=query, history=history)
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
            # 2026-07-22 飞轮升级：记录端到端延迟（供 query_log + p95 统计）
            import time as _time
            _start_ts = _time.monotonic()
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

            # 2026-07-22 飞轮升级：记录查询日志（fire-and-forget，不阻塞响应）
            # sql 字段为空——graph 内部 state 不外泄，主要价值是 session/query/success/latency
            _elapsed_ms = (_time.monotonic() - _start_ts) * 1000
            query_log_service.record(
                session_id=session_id, query=query, sql="",
                success=last_result is not None, latency_ms=_elapsed_ms,
            )

            # ⭐ L1 存：把本轮对话写回 session_store
            # 只有成功拿到结果才记录（失败的不污染历史）
            if last_result is not None:
                # ⭐ 2026-07-20 优化：两次 add_message 并行（不同 key 写 Redis 原子，
                # 同 key 也是 RPUSH 互不冲突），消除 1 个 Redis RTT 的尾延迟
                result_text = str(last_result)[:200]  # 截断防止存太大
                # 注意：这里存的是用户原始 query，不是 enhanced_query
                # 否则历史里全是"【对话历史】...【任务类型】..."这种元数据
                await asyncio.gather(
                    add_message(session_id, "user", query),
                    add_message(session_id, "assistant", result_text),
                )
                # 2026-07-22 Semantic Memory：抽取用户偏好（fire-and-forget）
                # 连续 3 次带"按地区" → preferred_dim=region confidence=0.9
                await user_profile_service.update(session_id, query)
                # 2026-07-22 Episodic Memory：历史超 5 轮触发摘要（防 token 爆炸）
                await session_summarizer.summarize_if_needed(session_id)

        except Exception as e:
            # 流式接口已经开始返回后不能再改 HTTP 状态码，因此把异常也包装成一条 SSE 消息
            # 2026-07-20（#1 脱敏）：服务端记完整异常，给前端友好文案
            logger.exception(f"[query] 链路异常: {e}")
            # 2026-07-22 飞轮升级：异常也算一次失败查询，记 query_log
            query_log_service.record(
                session_id=session_id, query=query, sql="",
                success=False, latency_ms=(_time.monotonic() - _start_ts) * 1000,
            )
            error = {"type": "error", "message": _friendly_error(e)}
            yield f"data: {json.dumps(error, ensure_ascii=False, default=str)}\n\n"
