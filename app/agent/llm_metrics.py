# -*- coding: utf-8 -*-
"""
LLM 调用指标聚合器（2026-07-20 #22）

LLMTimingCallback 已经把每次调用的 token/duration 写到日志，但没进程内聚合。
本模块维护一个 dict[profile, Stats]，供 admin /api/admin/metrics 查询：
- 总调用次数
- 总 token（prompt + completion）
- 错误次数
- 累计耗时（用于算 avg）

设计：
- 单进程内存计数（asyncio 单线程，无需锁）
- 不存历史时序数据，只存聚合值（粒度够用，零外部依赖）
- profile 维度聚合（cheap/strong 分别统计）
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProfileStats:
    """单个 profile 的累计指标"""

    calls: int = 0
    errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_duration_ms: float = 0.0

    def to_dict(self) -> dict:
        avg_ms = self.total_duration_ms / self.calls if self.calls else 0
        return {
            "calls": self.calls,
            "errors": self.errors,
            "error_rate": (self.errors / self.calls) if self.calls else 0.0,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "avg_duration_ms": round(avg_ms, 1),
        }


class LLMMetricsCollector:
    """进程级 LLM 指标收集器（单例）"""

    def __init__(self) -> None:
        self._stats: dict[str, ProfileStats] = {}

    def record_call(
        self,
        profile: str,
        prompt_tokens: int,
        completion_tokens: int,
        duration_ms: float,
    ) -> None:
        stats = self._stats.setdefault(profile, ProfileStats())
        stats.calls += 1
        stats.prompt_tokens += prompt_tokens
        stats.completion_tokens += completion_tokens
        stats.total_duration_ms += duration_ms

    def record_error(self, profile: str, duration_ms: float) -> None:
        stats = self._stats.setdefault(profile, ProfileStats())
        stats.calls += 1
        stats.errors += 1
        stats.total_duration_ms += duration_ms

    def snapshot(self) -> dict:
        """返回所有 profile 的指标快照"""
        return {p: s.to_dict() for p, s in self._stats.items()}

    def reset(self) -> None:
        """清零（admin API 可选支持）"""
        self._stats.clear()


# 模块级单例
_metrics = LLMMetricsCollector()


def get_metrics_collector() -> LLMMetricsCollector:
    return _metrics
