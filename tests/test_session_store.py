# -*- coding: utf-8 -*-
"""
会话存储单元测试 — 覆盖正常/降级/边界/集成 4 类场景

运行方式：
  cd D:\shopkeeper-agent
  uv run pytest tests/test_session_store.py -v
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.clients.redis_client_manager import redis_client_manager
from app.services import session_store


@pytest.fixture(autouse=True)
def reset_state():
    """每个测试前重置状态"""
    session_store._memory_fallback.clear()
    redis_client_manager._client = None
    redis_client_manager._available = True
    redis_client_manager._fail_count = 0
    # 默认切到 REDIS_PRIMARY 模式，让测试真正走 Redis 路径
    session_store.WRITE_MODE = session_store.WriteMode.REDIS_PRIMARY
    yield
    session_store._memory_fallback.clear()


# ══════════════════════════════════════
# 正常场景：Redis 可用
# ══════════════════════════════════════
class TestRedisAvailable:
    """Redis 正常时的行为"""

    async def test_get_history_calls_redis(self):
        """Redis 可用时 get_history 调用 lrange 命令"""
        mock_client = AsyncMock()
        mock_client.lrange.return_value = [
            '{"role":"user","content":"hi"}',
            '{"role":"assistant","content":"hello"}',
        ]
        redis_client_manager._client = mock_client
        redis_client_manager._available = True

        history = await session_store.get_history("s1", max_count=5)

        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["content"] == "hello"
        mock_client.lrange.assert_called_once()

    async def test_add_message_calls_redis_pipeline(self):
        """add_message 通过 pipeline 写入 Redis"""
        # 用 MagicMock 链式 + AsyncMock 验证 execute 被调用
        pipeline_mock = MagicMock()  # pipeline() 的返回值，普通 mock
        pipeline_mock.rpush = MagicMock()
        pipeline_mock.ltrim = MagicMock()
        pipeline_mock.expire = MagicMock()
        pipeline_mock.execute = AsyncMock()

        mock_client = MagicMock()
        mock_client.pipeline = MagicMock(return_value=pipeline_mock)
        redis_client_manager._client = mock_client
        redis_client_manager._available = True

        await session_store.add_message("s1", "user", "你好")

        # 验证 pipeline 三个命令都被调用 + execute 被 await
        assert pipeline_mock.rpush.called
        assert pipeline_mock.ltrim.called
        assert pipeline_mock.expire.called
        assert pipeline_mock.execute.called

    async def test_add_message_also_writes_memory(self):
        """add_message 永远写内存（即使 Redis 成功）"""
        mock_client = AsyncMock()
        mock_client.pipeline.return_value = mock_client
        redis_client_manager._client = mock_client
        redis_client_manager._available = True

        await session_store.add_message("s1", "user", "测试")

        # 内存里有数据
        assert "s1" in session_store._memory_fallback
        assert session_store._memory_fallback["s1"][0]["content"] == "测试"

    async def test_clear_history_clears_both(self):
        """clear_history 同时清空 Redis 和内存"""
        mock_client = AsyncMock()
        redis_client_manager._client = mock_client
        redis_client_manager._available = True
        session_store._memory_fallback["s1"] = [{"role": "user", "content": "x"}]

        await session_store.clear_history("s1")

        assert "s1" not in session_store._memory_fallback
        mock_client.delete.assert_called_once()


# ══════════════════════════════════════
# 降级场景：Redis 不可用
# ══════════════════════════════════════
class TestFallback:
    """Redis 不可用时的降级行为"""

    async def test_get_history_fallback_when_marked_unavailable(self):
        """Redis 标记不可用时降级到内存"""
        redis_client_manager._available = False
        redis_client_manager._client = None

        session_store._memory_fallback["s1"] = [
            {"role": "user", "content": "test"},
        ]

        history = await session_store.get_history("s1", max_count=5)

        assert len(history) == 1
        assert history[0]["content"] == "test"

    async def test_get_history_fallback_on_redis_error(self):
        """Redis 抛异常时降级到内存"""
        mock_client = AsyncMock()
        mock_client.lrange.side_effect = ConnectionError("Redis connection refused")
        redis_client_manager._client = mock_client
        redis_client_manager._available = True

        history = await session_store.get_history("s1", max_count=5)

        # 异常时不抛错，返回空列表
        assert history == []
        # 失败计数应该增加
        assert redis_client_manager._fail_count == 1

    async def test_mark_unavailable_after_threshold(self):
        """连续失败 N 次才标记为不可用"""
        mock_client = AsyncMock()
        mock_client.lrange.side_effect = ConnectionError("fail")
        redis_client_manager._client = mock_client
        redis_client_manager._available = True

        # 连续调用 3 次（threshold 默认 3）
        for _ in range(3):
            await session_store.get_history("s1")

        # 第 3 次后应该标记不可用
        assert redis_client_manager._available is False
        assert redis_client_manager._fail_count == 3

    async def test_add_message_writes_memory_when_redis_down(self):
        """Redis 不可用时 add_message 仍能工作（写内存）"""
        redis_client_manager._available = False
        redis_client_manager._client = None

        await session_store.add_message("s1", "user", "降级测试")

        assert "s1" in session_store._memory_fallback
        assert session_store._memory_fallback["s1"][0]["content"] == "降级测试"


# ══════════════════════════════════════
# 边界场景
# ══════════════════════════════════════
class TestEdgeCases:
    """边界条件"""

    async def test_long_content_truncated_to_500(self):
        """超长内容被截断到 500 字符"""
        redis_client_manager._available = False
        redis_client_manager._client = None

        long_content = "a" * 1000
        await session_store.add_message("s1", "user", long_content)

        stored = session_store._memory_fallback["s1"][0]["content"]
        assert len(stored) == 500

    async def test_max_10_messages_in_memory(self):
        """单 session 内存里最多保留 10 条"""
        redis_client_manager._available = False
        redis_client_manager._client = None

        for i in range(20):
            await session_store.add_message("s1", "user", f"msg{i}")

        assert len(session_store._memory_fallback["s1"]) == 10

    async def test_empty_session_returns_empty_list(self):
        """查询不存在的 session 返回空列表"""
        mock_client = AsyncMock()
        mock_client.lrange.return_value = []
        redis_client_manager._client = mock_client
        redis_client_manager._available = True

        history = await session_store.get_history("nonexistent")

        assert history == []

    async def test_clear_nonexistent_session_no_error(self):
        """清空不存在的 session 不报错"""
        mock_client = AsyncMock()
        redis_client_manager._client = mock_client
        redis_client_manager._available = True

        # 不应抛出异常
        await session_store.clear_history("nonexistent")

        mock_client.delete.assert_called_once()

    async def test_concurrent_add_message_thread_safe(self):
        """并发写入内存 dict 是线程安全的"""
        import threading
        redis_client_manager._available = False
        redis_client_manager._client = None

        def add_many():
            for i in range(50):
                # 同步函数内部直接调用 _memory_add
                session_store._memory_add("s1", "user", f"msg{i}")

        threads = [threading.Thread(target=add_many) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 3 个线程各写 50 条，但内存只保留 10 条
        assert len(session_store._memory_fallback["s1"]) == 10


# ══════════════════════════════════════
# 集成测试
# ══════════════════════════════════════
class TestIntegration:
    """完整读写流程"""

    async def test_round_trip_redis_mode(self):
        """Redis 模式下完整读写循环"""
        mock_client = AsyncMock()
        written_data = []

        async def mock_rpush(key, value):
            written_data.append(value)
            return len(written_data)

        async def mock_lrange(key, start, end):
            return written_data[-5:]

        mock_client.rpush = mock_rpush
        mock_client.lrange = mock_lrange
        mock_client.pipeline.return_value = mock_client
        mock_client.ltrim = AsyncMock()
        mock_client.expire = AsyncMock()
        redis_client_manager._client = mock_client
        redis_client_manager._available = True

        # 写入 3 条
        await session_store.add_message("s1", "user", "msg1")
        await session_store.add_message("s1", "assistant", "reply1")
        await session_store.add_message("s1", "user", "msg2")

        # 读出
        history = await session_store.get_history("s1", max_count=5)

        # 内存里也有 3 条（因为 add_message 同时写内存）
        assert len(history) == 3
        assert history[0]["content"] == "msg1"

    async def test_redis_recovery_resets_fail_count(self):
        """Redis 恢复后失败计数清零"""
        mock_client = AsyncMock()
        # 第一次失败，第二次成功
        mock_client.lrange.side_effect = [ConnectionError("fail"), ["{}"]]
        redis_client_manager._client = mock_client
        redis_client_manager._available = True

        await session_store.get_history("s1")  # 失败
        await session_store.get_history("s1")  # 成功

        # 成功后失败计数清零
        assert redis_client_manager._fail_count == 0
        assert redis_client_manager._available is True
