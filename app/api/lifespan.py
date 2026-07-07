"""
FastAPI 应用生命周期管理

负责在服务启动时初始化外部客户端，在服务关闭时释放连接资源。
这些客户端是应用级资源，适合在 lifespan 中创建一次并复用，而不是每个请求
重复初始化。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import (
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.clients.redis_client_manager import redis_client_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """管理应用启动和关闭两个阶段的外部资源"""

    # 启动阶段：逐一初始化外部服务客户端，某个失败不影响整体启动
    import logging
    _log = logging.getLogger("lifespan")

    def _safe_init(name, init_fn):
        try:
            init_fn()
            _log.info("[OK] %s 初始化成功", name)
        except Exception as e:
            _log.warning("[SKIP] %s 初始化失败: %s", name, e)

    _safe_init("Qdrant", qdrant_client_manager.init)
    _safe_init("Embedding", embedding_client_manager.init)
    _safe_init("ES", es_client_manager.init)
    _safe_init("MetaMySQL", meta_mysql_client_manager.init)
    _safe_init("DWMySQL", dw_mysql_client_manager.init)
    _safe_init("Redis", redis_client_manager.init)

    yield

    async def _safe_close(name, close_fn):
        try:
            await close_fn()
            _log.info("[OK] %s 关闭成功", name)
        except Exception as e:
            _log.warning("[SKIP] %s 关闭失败: %s", name, e)

    await _safe_close("Qdrant", qdrant_client_manager.close)
    await _safe_close("ES", es_client_manager.close)
    await _safe_close("MetaMySQL", meta_mysql_client_manager.close)
    await _safe_close("DWMySQL", dw_mysql_client_manager.close)
    await _safe_close("Redis", redis_client_manager.close)
