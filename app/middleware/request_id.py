import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.context import request_id_ctx_var


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = f"req-{uuid.uuid4().hex[:8]}"
        request_id_ctx_var.set(request_id)
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response
