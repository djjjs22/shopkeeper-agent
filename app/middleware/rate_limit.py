"""
基于内存的滑动窗口限流中间件

轻量实现：单进程内存计数，按客户端标识做分钟级频率限制
适合单实例部署的教学项目，无外部依赖

2026-07-20 (#19 安全加固)：
  - 信任 trusted_proxies 配置的代理头，避免 X-Forwarded-For 任意伪造绕过
  - admin 端点单独限流（默认 5 次/分钟，防 token 爆破）
  - 同时限 /api/query 和 /api/admin/*
"""

import os
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


def _parse_trusted_proxies() -> set[str]:
    """读 TRUSTED_PROXIES 环境变量（逗号分隔的 IP/CIDR，简化版只匹配 IP 字面值）"""
    env = os.environ.get("TRUSTED_PROXIES", "")
    if not env.strip():
        # 本地开发默认信任本机回环（vite dev server 走 127.0.0.1）
        return {"127.0.0.1", "::1"}
    return {p.strip() for p in env.split(",") if p.strip()}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """按客户端 IP 做限流，超限返回 429

    Args:
        max_requests: query 接口时间窗口内允许的最大请求数
        window_seconds: 时间窗口大小（秒）
        admin_max_requests: admin 接口的独立配额（默认 5，防爆破）
    """

    def __init__(
        self,
        app,
        max_requests: int = 10,
        window_seconds: int = 60,
        admin_max_requests: int = 5,
    ):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.admin_max_requests = admin_max_requests
        self._trusted_proxies = _parse_trusted_proxies()
        # key (path_class, ip) -> list[timestamp]
        # path_class 区分 query / admin，避免共享配额
        self._requests: dict[tuple[str, str], list[float]] = defaultdict(list)

    def _get_client_ip(self, request: Request) -> str:
        """获取客户端 IP

        2026-07-20 (#19)：仅在请求来自 trusted proxy 时才信任 X-Forwarded-For，
        避免任意客户端伪造该 header 绕过限流。
        """
        direct_ip = request.client.host if request.client else "unknown"
        # 只有直连 IP 在 trusted_proxies 里时，才信任 XFF（说明前面是真反代）
        if direct_ip in self._trusted_proxies:
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                # XFF 可以是 "client, proxy1, proxy2" 链，最左侧是真实客户端
                return forwarded.split(",")[0].strip()
        return direct_ip

    def _cleanup_old(self, key: tuple[str, str], now: float) -> None:
        """清理过期的时间戳"""
        if key not in self._requests:
            return
        cutoff = now - self.window_seconds
        self._requests[key] = [ts for ts in self._requests[key] if ts > cutoff]
        if not self._requests[key]:
            del self._requests[key]

    def _classify(self, path: str) -> str | None:
        """路径归类到限流维度，返回 None 表示不限流"""
        if path == "/api/query":
            return "query"
        if path.startswith("/api/admin/") or path == "/api/admin":
            return "admin"
        return None

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        path_class = self._classify(path)
        if path_class is None:
            # 健康检查和其他端点不限流
            return await call_next(request)

        ip = self._get_client_ip(request)
        key = (path_class, ip)
        now = time.time()
        self._cleanup_old(key, now)

        limit = (
            self.admin_max_requests if path_class == "admin" else self.max_requests
        )
        current_count = len(self._requests.get(key, []))
        if current_count >= limit:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        f"请求过于频繁，请每 {self.window_seconds} 秒内"
                        f"不超过 {limit} 次"
                    )
                },
            )

        self._requests[key].append(now)
        return await call_next(request)