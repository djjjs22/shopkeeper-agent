"""
FastAPI 应用入口

负责创建 FastAPI 实例、注册生命周期、挂载路由和中间件。

2026-07-20（#4 安全加固）：
  - CORS 改成白名单（ALLOWED_ORIGINS 环境变量，逗号分隔），不再 ["*"] + credentials
  - 配合 query_router 的 cookie HttpOnly + SameSite=Lax
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.lifespan import lifespan
from app.api.routers.admin_router import admin_router
from app.api.routers.query_router import query_router
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_id import RequestIDMiddleware


def _get_cors_origins() -> list[str]:
    """读取允许的 CORS origin 列表

    优先环境变量 ALLOWED_ORIGINS（逗号分隔）。
    未设时回退到本地开发常用源（localhost:5173 / 4173 / 8000）。
    生产部署必须显式配置 ALLOWED_ORIGINS=https://your-domain.com
    """
    env = os.environ.get("ALLOWED_ORIGINS", "")
    if env.strip():
        return [o.strip() for o in env.split(",") if o.strip()]
    # 本地开发兜底：Vite 默认端口 5173，preview 4173，后端自身 8000
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://localhost:8000",
    ]


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例"""
    app = FastAPI(
        title="电商问数智能体",
        description="基于 LangGraph 的多步检索与 SQL 生成系统",
        version="0.1.0",
        lifespan=lifespan,
    )

    # 注册中间件
    # 2026-07-20（#4）：CORS 改白名单，避免 ["*"] + credentials 的规范矛盾
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_get_cors_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Admin-Token", "X-Request-Id"],
    )
    app.add_middleware(RateLimitMiddleware, max_requests=10, window_seconds=60)
    app.add_middleware(RequestIDMiddleware)

    # 注册路由
    app.include_router(query_router)
    # 2026-07-17 改造：注册 admin 路由（LLM profile 热切换等运维 API）
    app.include_router(admin_router)

    # 健康检查端点
    @app.get("/health")
    async def health_check():
        return {"status": "healthy", "service": "shopkeeper-agent"}

    return app


# FastAPI 应用实例
app = create_app()