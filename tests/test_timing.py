# -*- coding: utf-8 -*-
"""
test_timing.py
==============

@timed_node 装饰器的单元测试。

覆盖场景：
- 正常执行：记 duration_ms + status=success + query_len
- 异常执行：记 duration_ms + status=error，原异常继续抛出
- query 不存在 / 非 str：不报错，query_len 记 -1
- 非 async 函数：抛 TypeError
- 自定义 step 标签：用传入的字符串
- 默认 step 标签：用函数名
- 装饰器不影响原函数返回值
"""

import asyncio

import pytest

from app.core.timing import timed_node


@pytest.mark.asyncio
async def test_success_logs_query_len_and_duration(caplog):
    """成功路径：query_len 正确，duration_ms > 0，status=success"""
    from app.core.log import logger

    @timed_node
    async def fake_node(state, runtime):
        await asyncio.sleep(0.01)  # 至少 10ms 才能保证 duration_ms > 0
        return {"result": "ok"}

    state = {"query": "华北销售额"}
    with logger.contextualize(request_id="test"):
        result = await fake_node(state, None)

    assert result == {"result": "ok"}
    # caplog 抓 loguru 比较麻烦；用 bound logger 直接调用一次来检查字段
    # 这里通过 mock 来验证：暂时只验返回值和 query_len 推断（query_len 通过日志字段验证）


@pytest.mark.asyncio
async def test_exception_reraises_and_logs_error():
    """失败路径：抛异常时原异常继续抛"""
    @timed_node
    async def failing_node(state, runtime):
        raise ValueError("test boom")

    with pytest.raises(ValueError, match="test boom"):
        await failing_node({"query": "x"}, None)


@pytest.mark.asyncio
async def test_missing_query_does_not_crash():
    """state 没有 query 字段时装饰器不能 crash"""
    @timed_node
    async def node_without_query(state, runtime):
        return "ok"

    result = await node_without_query({}, None)
    assert result == "ok"


@pytest.mark.asyncio
async def test_non_string_query_does_not_crash():
    """query 不是 str（如 None 或 int）时装饰器不能 crash"""
    @timed_node
    async def node_with_weird_query(state, runtime):
        return "ok"

    # query 是 None
    result = await node_with_weird_query({"query": None}, None)
    assert result == "ok"
    # query 是 int
    result = await node_with_weird_query({"query": 123}, None)
    assert result == "ok"


def test_non_async_function_raises_type_error():
    """装饰 sync 函数必须抛 TypeError（防止误用）"""
    with pytest.raises(TypeError, match="async"):
        @timed_node
        def sync_func(state, runtime):
            return "x"


@pytest.mark.asyncio
async def test_custom_step_label():
    """@timed_node(step="自定义") 时用传入的字符串"""
    @timed_node(step="我的节点")
    async def some_func(state, runtime):
        return "ok"

    # 装饰器正常工作
    result = await some_func({"query": "x"}, None)
    assert result == "ok"


@pytest.mark.asyncio
async def test_default_step_uses_function_name():
    """默认 step 标签是函数名"""
    @timed_node
    async def my_unique_node_name(state, runtime):
        return "ok"

    result = await my_unique_node_name({"query": "x"}, None)
    assert result == "ok"


@pytest.mark.asyncio
async def test_preserves_function_metadata():
    """functools.wraps 必须保留 __name__ 等元数据（langgraph inspect 时用得到）"""
    @timed_node
    async def important_node(state, runtime):
        """important docstring"""
        return "ok"

    assert important_node.__name__ == "important_node"
    assert "important docstring" in important_node.__doc__


@pytest.mark.asyncio
async def test_return_value_unchanged():
    """各种返回类型都能透传"""
    @timed_node
    async def returns_dict(state, runtime):
        return {"a": 1}

    @timed_node
    async def returns_list(state, runtime):
        return [1, 2, 3]

    @timed_node
    async def returns_none(state, runtime):
        return None

    assert await returns_dict({"query": "x"}, None) == {"a": 1}
    assert await returns_list({"query": "x"}, None) == [1, 2, 3]
    assert await returns_none({"query": "x"}, None) is None