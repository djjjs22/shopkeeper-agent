"""
FastAPI 应用入口（兼容层）

完整的应用实例在 `app.main` 中通过 `create_app()` 构建，包含 CORS / 限流 /
请求 ID 等中间件。这里统一 re-export，使 `uvicorn main:app` 与
`uvicorn app.main:app` 指向同一个完整应用，避免根目录这份副本缺失中间件。

真正运行后端请使用：`uv run uvicorn app.main:app --host 0.0.0.0 --port 8000`
"""

from app.main import app

__all__ = ["app"]
