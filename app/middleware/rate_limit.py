"""
基于内存的滑动窗口限流中间件

轻量实现：单进程内存计数，按 IP 地址做分钟级频率限制
适合单实例部署的教学项目，无外部依赖
"""

import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class RateLimitMiddleware(BaseHTTPMiddleware):
    """按客户端 IP 做简单限流，超限返回 429

    Args:
        max_requests: 时间窗口内允许的最大请求数
        window_seconds: 时间窗口大小（秒）
    """

    def __init__(self, app, max_requests: int = 10, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        # ip -> list[timestamp] 记录每个 IP 的请求时间戳
        self._requests: dict[str, list[float]] = defaultdict(list)

    def _get_client_ip(self, request: Request) -> str:
        """获取客户端 IP，优先取 X-Forwarded-For（反向代理场景）"""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _cleanup_old(self, ip: str, now: float) -> None:
        """清理过期的时间戳"""
        if ip not in self._requests:
            return
        cutoff = now - self.window_seconds
        self._requests[ip] = [ts for ts in self._requests[ip] if ts > cutoff]
        if not self._requests[ip]:
            del self._requests[ip]

    async def dispatch(self, request: Request, call_next):
        # 只对 /api/query 做限流，健康检查和其他端点不限
        if request.url.path != "/api/query":
            return await call_next(request)

        ip = self._get_client_ip(request)
        now = time.time()
        self._cleanup_old(ip, now)

        current_count = len(self._requests.get(ip, []))
        if current_count >= self.max_requests:
            return JSONResponse(
                status_code=429,
                content={"detail": f"请求过于频繁，请每{self.window_seconds}秒内不超过{self.max_requests}次"},
            )

        self._requests[ip].append(now)
        return await call_next(request)