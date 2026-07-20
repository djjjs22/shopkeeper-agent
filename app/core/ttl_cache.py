# -*- coding: utf-8 -*-
"""
超轻量 TTL + 容量上限缓存

为什么不直接装 cachetools：
- cachetools 不在依赖里，引入要联网装 + 改 uv.lock
- 实际只需要"按 TTL 过期 + 容量上限 LRU 淘汰"这一个数据结构，30 行就够

设计：
- OrderedDict 维护访问顺序，淘汰时弹出最旧（LRU 近似）
- 每次写入记 timestamp，读取时检查是否过期
- asyncio 单线程下无需加锁
"""

from collections import OrderedDict
from time import monotonic
from typing import Any, Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class TTLCache(Generic[K, V]):
    """带 TTL 和最大容量的缓存

    Args:
        maxsize: 最大条目数，超出时按 LRU 淘汰最旧访问的 key
        ttl:     条目存活秒数；超过即视为未命中（懒清理）
    """

    def __init__(self, maxsize: int = 512, ttl: float = 3600.0) -> None:
        self.maxsize = maxsize
        self.ttl = ttl
        self._store: OrderedDict[K, tuple[V, float]] = OrderedDict()

    def get(self, key: K, default: Any = None) -> Any:
        """返回未过期的缓存值；不存在或已过期返回 default

        default 用于区分"未命中"：调用方可以传一个哨兵（如 object()），
        然后判断 `result is sentinel` 区分"未命中"和"命中了 None"。
        """
        entry = self._store.get(key)
        if entry is None:
            return default
        value, ts = entry
        if (monotonic() - ts) > self.ttl:
            # 过期，懒删除
            self._store.pop(key, None)
            return default
        # LRU：访问后挪到末尾（最新访问）
        self._store.move_to_end(key)
        return value

    def set(self, key: K, value: V) -> None:
        """写入或更新条目；超出 maxsize 时弹出 LRU 最旧条目"""
        self._store[key] = (value, monotonic())
        self._store.move_to_end(key)
        while len(self._store) > self.maxsize:
            self._store.popitem(last=False)  # 弹出最旧

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)

    def __contains__(self, key: object) -> bool:
        # __contains__ 不该刷新 LRU 顺序；同时已过期的视为不在
        entry = self._store.get(key)  # type: ignore[arg-type]
        if entry is None:
            return False
        _, ts = entry
        if (monotonic() - ts) > self.ttl:
            return False
        return True


__all__ = ["TTLCache"]
