# -*- coding: utf-8 -*-
"""
timing.py
=========

节点执行时长装饰器，为全链路可观测性提供"每个节点耗时多少"的基础数据。

**为什么有这个文件**：
- 改前（2026-07-17 前）：节点只在跑完时 writer progress 事件给前端 SSE，
  没有结构化的耗时日志。后端排查"哪一步慢"只能靠 timestamp 估算。
- 改后：所有节点统一打点，一条结构化日志包含 step/duration/status/query_len，
  配合 log.py 的 JSON 格式输出可直接被日志聚合工具（ELK / Loki）消费。

**关键设计**：
- 装饰器**不读** state 中具体内容（避免 prompt 模板、SQL、query 文本泄漏到日志）
- 只读 state["query"] 长度作为 query_len（信息量足够，但不暴露 query 内容）
- 装饰器**不替代**节点内部的 writer({"type": "progress", ...})，两者并存
- 异常时记 status="error"，原异常继续向上抛（不破坏现有 except 逻辑）
- 装饰器 stack 在节点的 try/except 之外，记录**整体**耗时（包括节点内部 try 兜底）

**使用方式**（在每个节点的 async def 上一行加）：

```python
from app.core.timing import timed_node

@timed_node
async def classify_intent(state, runtime):
    ...
```

装饰器自动用 `__name__` 作为 step 标签。如果想自定义：

```python
@timed_node(step="生成查询意图")
async def generate_intent(state, runtime):
    ...
```
"""

import functools
import inspect
import time
from collections.abc import Callable, Coroutine
from typing import Any, ParamSpec, TypeVar

from app.core.log import logger

P = ParamSpec("P")
R = TypeVar("R")


def _is_async_func(func: Callable[..., Any]) -> bool:
    """判断是否为 async 函数（异步生成器或协程函数都算）"""
    return inspect.iscoroutinefunction(func) or inspect.isasyncgenfunction(func)


def timed_node(
    func: Callable[P, Coroutine[Any, Any, R]] | None = None,
    *,
    step: str | None = None,
) -> Callable[P, Coroutine[Any, Any, R]]:
    """节点耗时装饰器（兼容带/不带参数两种调用方式）

    Args:
        func: 被装饰的 async 函数（直接 @timed_node 时由 Python 传入）
        step: 自定义 step 标签（默认用 func.__name__）

    Returns:
        包装后的 async 函数

    用法：
        @timed_node
        async def classify_intent(state, runtime): ...

        @timed_node(step="生成查询意图")
        async def generate_intent(state, runtime): ...
    """
    # 兼容 @timed_node（无参）和 @timed_node(step="x")（带参）两种形式
    if func is not None:
        # 形式 1：@timed_node（无参）
        return _make_wrapper(func, step=step)

    # 形式 2：@timed_node(step="x")（带参）
    def decorator(f: Callable[P, Coroutine[Any, Any, R]]) -> Callable[P, Coroutine[Any, Any, R]]:
        return _make_wrapper(f, step=step)

    return decorator


def _make_wrapper(
    func: Callable[P, Coroutine[Any, Any, R]],
    *,
    step: str | None,
) -> Callable[P, Coroutine[Any, Any, R]]:
    """实际包装函数（timed_node 拆出来，避免重复代码）"""
    label = step or func.__name__

    if not _is_async_func(func):
        raise TypeError(
            f"@timed_node 只能装饰 async 函数，{func.__name__} 不是 async"
        )

    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        # 提取 query_len（state 永远是第一个位置参数）
        query_len = -1
        if args and hasattr(args[0], "get"):
            try:
                q = args[0].get("query")
                if isinstance(q, str):
                    query_len = len(q)
            except Exception:  # noqa: BLE001
                query_len = -1

        start = time.perf_counter()
        error: BaseException | None = None
        try:
            return await func(*args, **kwargs)
        except BaseException as exc:
            # BaseException 而不是 Exception：把 CancelledError 也算进去（async 取消）
            error = exc
            raise
        finally:
            # 不论成功/失败都打 timing（finally 在 try/except 任何路径下都执行）
            duration_ms = int((time.perf_counter() - start) * 1000)
            status = "error" if error else "success"
            log_fn = logger.bind(
                step=label,
                duration_ms=duration_ms,
                status=status,
                query_len=query_len,
            )
            if error:
                log_fn.error(
                    f"node {label} failed (duration_ms={duration_ms})"
                )
            else:
                log_fn.info(
                    f"node {label} ok (duration_ms={duration_ms})"
                )

    return wrapper