"""
FastAPI 应用入口

负责创建 FastAPI 实例、注册生命周期、挂载路由和中间件。
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.lifespan import lifespan
from app.api.routers.query_router import query_router
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_id import RequestIDMiddleware


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例"""
    app = FastAPI(
        title="电商问数智能体",
        description="基于 LangGraph 的多步检索与 SQL 生成系统",
        version="0.1.0",
        lifespan=lifespan,
    )

    # 注册中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RateLimitMiddleware, max_requests=10, window_seconds=60)
    app.add_middleware(RequestIDMiddleware)

    # 注册路由
    app.include_router(query_router)

    # 健康检查端点
    @app.get("/health")
    async def health_check():
        return {"status": "healthy", "service": "shopkeeper-agent"}

    return app


# FastAPI 应用实例
app = create_app()