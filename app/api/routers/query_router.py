"""
问数查询接口路由

负责定义前端访问的 `/api/query` 接口，把 HTTP 请求交给 QueryService，
并把问数智能体执行过程以 SSE 形式持续返回给客户端。
路由层只处理请求体、依赖声明和响应类型，不直接创建 Repository 或执行图节点。

2026-07-20 #4 安全加固：
  - 可选 API Key 鉴权（QUERY_API_KEY 环境变量开关，未设则不鉴权，便于本地开发）
  - session_id 改用 HMAC 签名（issue_session_id / verify_session_id），防伪造
  - cookie 加 HttpOnly + SameSite=Lax（main.py 配置 secure_props）
"""

import os
from typing import Annotated

from fastapi import APIRouter, Body, Cookie, Depends, Header, HTTPException
from starlette.responses import StreamingResponse

from app.api.dependencies import get_query_service
from app.api.schemas.query_schema import QuerySchema, FeedbackSchema
from app.core.session import issue_session_id, verify_session_id
from app.services.query_service import QueryService
from app.services.session_store import clear_history
from app.services.bad_case_collector import bad_case_collector

# 当前模块只维护查询相关接口，避免后续所有 API 都挤在 main.py 中
query_router = APIRouter()


def _check_query_api_key(authorization: str | None) -> None:
    """可选 API Key 鉴权

    设置环境变量 QUERY_API_KEY 后启用；未设则跳过（本地开发不受阻）。
    前端在 Authorization: Bearer <key> 头里带 key。
    """
    expected = os.environ.get("QUERY_API_KEY", "")
    if not expected:
        return  # 未配置 → 不鉴权
    if not authorization:
        raise HTTPException(status_code=401, detail="缺少 Authorization 头")
    # 支持 "Bearer xxx" 和裸 key 两种格式
    token = authorization.removeprefix("Bearer ").strip()
    if token != expected:
        raise HTTPException(status_code=401, detail="API Key 无效")


@query_router.post("/api/query")
async def query_handler(
    # 显式声明 body 来自 HTTP body（避免 FastAPI 把 body 当成 query 参数）
    body: Annotated[QuerySchema, Body()],
    # 服务依赖：FastAPI 会调用 get_query_service，递归组装它所需的仓储和客户端
    query_service: Annotated[QueryService, Depends(get_query_service)],
    # 会话 ID 走 cookie，HMAC 签名验证（非法或缺失时发新的）
    session_id: Annotated[str | None, Cookie()] = None,
    # 可选 API Key 鉴权（QUERY_API_KEY 未设则跳过）
    authorization: Annotated[str | None, Header()] = None,
):
    """接收用户自然语言问题，并流式返回 LangGraph 工作流输出"""
    _check_query_api_key(authorization)

    # ⭐ 多轮对话支持：没有 session_id 或签名非法 → 发新的合法 session_id
    is_new_session = False
    if not session_id or not verify_session_id(session_id):
        session_id = issue_session_id()
        is_new_session = True

    # 生成 StreamingResponse
    # 2026-07-17 改造：use_multi_agent 字段控制走老 graph 还是 supervisor_graph
    query_method = (
        query_service.query_multi_agent
        if body.use_multi_agent
        else query_service.query
    )
    response = StreamingResponse(
        # 把 session_id 传给 query_service，多轮对话靠它来检索历史
        query_method(body.query, session_id=session_id),
        media_type="text/event-stream",
    )

    # 如果是新会话，把 session_id 写到 cookie 让浏览器下次带上
    # 2026-07-20（#4）：加 httponly（防 XSS 读 cookie）+ samesite=lax（防 CSRF）
    # secure 属性由 main.py 全局配置（生产用 HTTPS 时开启）
    if is_new_session:
        response.set_cookie(
            key="session_id",
            value=session_id,
            max_age=86400,
            httponly=True,
            samesite="lax",
        )

    return response


@query_router.post("/api/clear-session")
async def clear_session_handler(
    # 会话 ID 走 cookie，和 /api/query 共用同一套会话标识
    session_id: Annotated[str | None, Cookie()] = None,
    authorization: Annotated[str | None, Header()] = None,
):
    """清空当前会话的 Redis 历史，配合前端「新会话」按钮使用（刀 17）"""
    _check_query_api_key(authorization)

    if not session_id or not verify_session_id(session_id):
        return {"status": "ok", "cleared": False, "reason": "no_valid_session"}
    await clear_history(session_id)
    return {"status": "ok", "cleared": True}


@query_router.post("/api/query/feedback")
async def feedback_handler(
    body: Annotated[FeedbackSchema, Body()],
    authorization: Annotated[str | None, Header()] = None,
):
    """接收用户对查询结果的反馈（2026-07-22 飞轮升级）

    👎 是数据飞轮最准的失败信号（人工标注），自动归集到 bad_case 表，
    周期 review 后进 gold_dataset 驱动 prompt / recall 优化。

    不需要 session_id 鉴权——反馈是用户主动行为，宽松接收。
    """
    _check_query_api_key(authorization)

    # 👎 → 归集到 bad_case（fire-and-forget，不阻塞响应）
    if body.rating == "down":
        bad_case_collector.record(
            query=body.query,
            sql=body.sql or "",
            error_type="user_thumb_down",
            detail=body.comment,
            session_id=body.session_id,
        )

    return {"status": "ok", "rating": body.rating, "recorded": body.rating == "down"}
