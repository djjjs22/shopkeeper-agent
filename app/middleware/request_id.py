import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.context import request_id_ctx_var


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = f"req-{uuid.uuid4().hex[:8]}"
        request_id_ctx_var.set(request_id)
        # 如果客户端发的是 SSE 响应，BaseHTTPMiddleware 偶尔会破坏流式 body
        # 这里加一个 try/except 兜底，body 解析失败时不让中间件吞错
        try:
            response = await call_next(request)
        except Exception:
            raise
        response.headers["X-Request-Id"] = request_id
        return response
