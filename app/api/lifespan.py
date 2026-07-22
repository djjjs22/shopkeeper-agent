"""
FastAPI 应用生命周期管理

负责在服务启动时初始化外部客户端，在服务关闭时释放连接资源。
这些客户端是应用级资源，适合在 lifespan 中创建一次并复用，而不是每个请求
重复初始化。

═══════════════════════════════════════════════════════════════════════
  核心知识点（设计决策的"为什么"）
═══════════════════════════════════════════════════════════════════════

【知识点 1：@asynccontextmanager 和 lifespan 协议】

  FastAPI 的 lifespan 参数接受一个 async context manager。
  - yield 之前的代码在服务启动时运行
  - yield 之后的代码在服务关闭时运行
  - yield 本身是"应用运行中"的占位

  ```python
  @asynccontextmanager
  async def lifespan(app):
      # 启动
      yield
      # 关闭
  ```

  这是替代 on_event("startup") / on_event("shutdown") 的新写法。

【知识点 2：降级启动原则 - 每个 manager 独立 try/except】

  Redis 挂了不能拖累 Qdrant/ES 也没起来——
  业务可能只需要 Qdrant（向量检索），不需要 Redis（会话存储）。

  _safe_init() 模式：
  - try init_fn() → 成功就 info、失败就 warning
  - 整个应用能起来 + 哪个挂了看 log 排查

  面试讲法：
  "我把 6 个外部依赖的 init 都包了 try/except，单个失败不会拖垮
  整个应用。这是 SRE 的'fail-open'原则——快速降级、保持核心可用。"

【知识点 3：同步 init + 异步 start 的混合模式】

  大部分 manager 是同步 init（跟项目风格一致）：
  - qdrant_client_manager.init() 创建客户端
  - 不需要 await 因为只创建不验证

  Redis 需要异步 start：
  - 探活协程是 asyncio.create_task()
  - 必须 await 才能确保 Task 被创建

  这种"sync init + async start"模式在测试中更容易 mock（同步部分）。

【知识点 4：关闭顺序与启动顺序相反】

  资源依赖关系：
  - Scheduler 用 Redis 客户端 → Scheduler 先关
  - Redis 用 MySQL 客户端（如果跨服务调用）→ Redis 后关
  - MySQL 是基础服务 → 最先开、最后关

  实际项目里大部分客户端无依赖关系，但保持对称是好习惯。

【知识点 5：_log 局部变量 vs 全局 logger】

  用 import logging + getLogger("lifespan") 而不是模块级 logger：
  - 跟项目其他 manager 风格一致（lifespan 是 orchestration 层）
  - 给"lifespan"单独一个 logger 名字方便 grep
  - Uvicorn 默认 logging 配置也会捕获到
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
from app.services.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    管理应用启动和关闭两个阶段的外部资源（见"知识点 1"）
    """
    # 启动阶段：逐一初始化外部服务客户端，某个失败不影响整体启动（见"知识点 2"）
    import logging
    _log = logging.getLogger("lifespan")  # 见"知识点 5"

    def _safe_init(name, init_fn):
        """同步 init 包装：失败只 warning 不抛错"""
        try:
            init_fn()
            _log.info("[OK] %s 初始化成功", name)
        except Exception as e:
            _log.warning("[SKIP] %s 初始化失败: %s", name, e)

    async def _safe_async_init(name, init_fn):
        """异步 init 包装：Redis 探活协程启动"""
        try:
            await init_fn()
            _log.info("[OK] %s 初始化成功", name)
        except Exception as e:
            _log.warning("[SKIP] %s 初始化失败: %s", name, e)

    # 同步 init 阶段（大部分客户端）
    _safe_init("Qdrant", qdrant_client_manager.init)
    _safe_init("Embedding", embedding_client_manager.init)
    _safe_init("ES", es_client_manager.init)
    _safe_init("MetaMySQL", meta_mysql_client_manager.init)
    _safe_init("DWMySQL", dw_mysql_client_manager.init)
    _safe_init("Redis", redis_client_manager.init)

    # 异步 start 阶段（见"知识点 3"）
    # Redis 探活协程是异步启动，跟同步 init 分开调用
    await _safe_async_init("Redis探活协程", redis_client_manager.start)

    # 2026-07-20 (#9)：从 meta_db 加载表名 → 注入 SQL 安全校验器
    # 让加表流程零代码改动：在 meta_config.yaml 加表 + 跑 build_meta_knowledge 后，
    # 下次启动自动识别新表，无需改 ALLOWED_TABLES 常量。
    try:
        from app.core.sql_safety import SQLSafetyValidator
        from app.repositories.mysql.meta.meta_mysql_repository import (
            MetaMySQLRepository,
        )

        async with meta_mysql_client_manager.session_factory() as session:
            meta_repo = MetaMySQLRepository(session)
            tables = await meta_repo.get_all_table_infos()
            names = [getattr(t, "name", "") for t in tables]
            SQLSafetyValidator.set_dynamic_allowed_tables(names)
            _log.info(
                "[OK] 动态表名白名单已加载: %s",
                SQLSafetyValidator.get_effective_allowed_tables(),
            )
    except Exception as e:
        _log.warning(
            "[SKIP] 动态表名白名单加载失败，退回硬编码 ALLOWED_TABLES: %s", e
        )

    # 启动应用内定时任务调度器（每天 02:00 跑归档）
    try:
        start_scheduler()
        _log.info("[OK] 调度器已启动")
    except Exception as e:
        _log.warning("[SKIP] 调度器启动失败: %s", e)

    # 2026-07-22 Eval+可观测性升级：LangSmith tracing 状态日志
    # 不做任何强制——env 设了 LangChain 自动 trace（覆盖 17 节点 + multi-agent 子图），
    # 不设则完全跳过，零侵入。这里只是启动时打一行让运维知道当前状态。
    try:
        import os

        tracing_on = os.getenv("LANGCHAIN_TRACING_V2", "false").lower() == "true"
        if tracing_on:
            project = os.getenv("LANGCHAIN_PROJECT", "shopkeeper-agent")
            _log.info(
                "[OK] LangSmith tracing 已启用（project=%s，17 节点 + multi-agent 自动上报）",
                project,
            )
        else:
            _log.info("[INFO] LangSmith tracing 未启用（设 LANGCHAIN_TRACING_V2=true 开启）")
    except Exception as e:
        _log.warning("[SKIP] LangSmith 状态检查失败: %s", e)

    yield  # ← 应用运行中

    # 关闭阶段（见"知识点 4"）
    async def _safe_close(name, close_fn):
        try:
            await close_fn()
            _log.info("[OK] %s 关闭成功", name)
        except Exception as e:
            _log.warning("[SKIP] %s 关闭失败: %s", name, e)

    # 关闭顺序与启动顺序相反
    # 1. 先关依赖（Scheduler 用 Redis → 先关 Scheduler）
    stop_scheduler()

    # 2. 再关各 manager（Redis.close 会 cancel 探活协程 + close 连接）
    await _safe_close("Qdrant", qdrant_client_manager.close)
    await _safe_close("ES", es_client_manager.close)
    await _safe_close("Embedding", embedding_client_manager.close)
    await _safe_close("MetaMySQL", meta_mysql_client_manager.close)
    await _safe_close("DWMySQL", dw_mysql_client_manager.close)
    await _safe_close("Redis", redis_client_manager.close)
