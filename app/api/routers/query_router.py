"""
问数查询接口路由

负责定义前端访问的 `/api/query` 接口，把 HTTP 请求交给 QueryService，
并把问数智能体执行过程以 SSE 形式持续返回给客户端。
路由层只处理请求体、依赖声明和响应类型，不直接创建 Repository 或执行图节点。
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Cookie, Depends
from starlette.responses import StreamingResponse

from app.api.dependencies import get_query_service
from app.api.schemas.query_schema import QuerySchema
from app.services.query_service import QueryService
from app.services.session_store import clear_history

# 当前模块只维护查询相关接口，避免后续所有 API 都挤在 main.py 中
query_router = APIRouter()


@query_router.post("/api/query")
async def query_handler(
    # 显式声明 body 来自 HTTP body（避免 FastAPI 把 body 当成 query 参数）
    body: Annotated[QuerySchema, Body()],
    # 服务依赖：FastAPI 会调用 get_query_service，递归组装它所需的仓储和客户端
    query_service: Annotated[QueryService, Depends(get_query_service)],
    # 会话 ID 走 cookie，没有就生成新的
    session_id: Annotated[str | None, Cookie()] = None,
):
    """接收用户自然语言问题，并流式返回 LangGraph 工作流输出"""

    # ⭐ 多轮对话支持：没有 session_id 就生成一个
    is_new_session = False
    if not session_id:
        session_id = str(uuid.uuid4())
        is_new_session = True

    # 生成 StreamingResponse
    response = StreamingResponse(
        # 把 session_id 传给 query_service，多轮对话靠它来检索历史
        query_service.query(body.query, session_id=session_id),
        media_type="text/event-stream",
    )

    # 如果是新会话，把 session_id 写到 cookie 让浏览器下次带上
    # max_age=86400 = 24小时，单位是秒
    if is_new_session:
        response.set_cookie(key="session_id", value=session_id, max_age=86400)

    return response


@query_router.post("/api/clear-session")
async def clear_session_handler(
    # 会话 ID 走 cookie，和 /api/query 共用同一套会话标识
    session_id: Annotated[str | None, Cookie()] = None,
):
    """清空当前会话的 Redis 历史，配合前端「新会话」按钮使用（刀 17）

    前端只清本地消息不够——session_id（cookie）不变，后端仍会读取旧历史，
    导致多轮对话上下文错位。这里显式清掉后端历史，让上下文真正重置。
    """
    if not session_id:
        return {"status": "ok", "cleared": False, "reason": "no_session"}
    await clear_history(session_id)
    return {"status": "ok", "cleared": True}
