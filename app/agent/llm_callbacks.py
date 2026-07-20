# -*- coding: utf-8 -*-
"""
llm_callbacks.py
================

LLM 调用 callback handler，记录每次调用的 token 用量 + 耗时 + 模型名。

**为什么有这个文件**：
- 改前（2026-07-17 前）：节点代码在调用 LLM 后只 `logger.info("成功")` 一行，
  没有 token 消耗和实际延迟数据。账单对账、性能优化、限流策略都缺数据。
- 改后：langchain `BaseCallbackHandler` 自动捕获每次 invoke/ainvoke 的
  - on_llm_start → 记录开始时间 + 模型名
  - on_llm_end   → 记录耗时 + prompt_tokens / completion_tokens
  - on_llm_error → 记录失败耗时 + 错误信息

**使用方式**（在 app/agent/llm.py 挂到 model 实例上）：

```python
from app.agent.llm_callbacks import LLMTimingCallback

llm = init_chat_model(...)
llm = llm.with_config({"callbacks": [LLMTimingCallback()]})
```

**性能开销**：
BaseCallbackHandler 是 langchain 内置机制，开销 < 1ms/调用，可忽略。

**线程安全**：
每个 callback 实例绑定到一次 LLM 调用（langchain 内部串行调用 on_llm_start/on_llm_end），
不需要锁。但若同一实例被多线程共享 on_llm_start 会被覆盖——所以**每个 LLM 实例挂一个独立 handler**。
"""

import time
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from app.core.log import logger


class LLMTimingCallback(BaseCallbackHandler):
    """LLM 调用的耗时 + token 用量 callback

    每次 LLM 调用触发 on_llm_start → on_llm_end/on_llm_error，
    通过 logger.bind(...) 注入结构化字段，配合 LOG_FORMAT=json 输出机器可读日志。
    """

    def __init__(self) -> None:
        # 不用 super().__init__() —— BaseCallbackHandler.__init__ 是空操作
        self._start_ts: float | None = None
        self._model_name: str = "unknown"
        self._profile: str = "unknown"  # 2026-07-20 (#22): 用于 metrics 归属

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """LLM 调用开始：记录模型名 + 开始时间

        **model_name 提取为什么这么复杂**：
        不同 langchain 版本的 ChatOpenAI 序列化结构不一致：
        - 新版 langchain-openai：`serialized["kwargs"]["model_name"]`
        - 旧版：`serialized["kwargs"]["model"]` 或 `serialized["name"]`
        - init_chat_model 包了一层：可能用 `model_name` 也可能用 `model`
        - 还有些自定义 wrapper 在 metadata 里塞 model
        所以三个字段都尝试，按优先级回退。
        """
        self._start_ts = time.perf_counter()

        # 2026-07-20 (#22)：从 metadata 提取 profile（llm.py _build_model 挂的）
        if isinstance(metadata, dict):
            self._profile = metadata.get("profile", "unknown")

        # 防御性：serialized 可能是 None / 非 dict（部分 langchain 子类会传）
        if not isinstance(serialized, dict):
            self._model_name = "unknown"
            return

        kwargs_dict = serialized.get("kwargs", {})
        if not isinstance(kwargs_dict, dict):
            kwargs_dict = {}

        # 优先级：kwargs.model_name > kwargs.model > name（class 名）
        self._model_name = (
            kwargs_dict.get("model_name")
            or kwargs_dict.get("model")
            or serialized.get("name")
            or "unknown"
        )

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """LLM 调用结束：记录耗时 + token 用量"""
        if self._start_ts is None:
            return  # 防御性：start 未触发就不记

        duration_ms = int((time.perf_counter() - self._start_ts) * 1000)

        # 提取 token 用量
        # response.llm_output 是 dict，可能含 {"token_usage": {...}, "model_name": ...}
        # response.generations[0][0].message.usage_metadata 是新版 langchain 的位置
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        total_tokens: int | None = None

        llm_output = getattr(response, "llm_output", None)
        if isinstance(llm_output, dict):
            token_usage = llm_output.get("token_usage") or llm_output.get("usage")
            if isinstance(token_usage, dict):
                prompt_tokens = token_usage.get("prompt_tokens")
                completion_tokens = token_usage.get("completion_tokens")
                total_tokens = token_usage.get("total_tokens")

        # 新版 langchain 也可能把 usage 放在 generations[0][0].message.usage_metadata
        if prompt_tokens is None:
            try:
                gen = response.generations[0][0]
                usage_meta = getattr(gen.message, "usage_metadata", None)
                if isinstance(usage_meta, dict):
                    prompt_tokens = usage_meta.get("input_tokens")
                    completion_tokens = usage_meta.get("output_tokens")
                    total_tokens = usage_meta.get("total_tokens")
            except (AttributeError, IndexError, TypeError):
                pass

        logger.bind(
            llm_event="end",
            model=self._model_name,
            duration_ms=duration_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        ).info(
            f"llm end model={self._model_name} duration_ms={duration_ms} "
            f"prompt={prompt_tokens} completion={completion_tokens}"
        )

        # 2026-07-20 (#22)：聚合到进程级 metrics collector
        try:
            from app.agent.llm_metrics import get_metrics_collector

            get_metrics_collector().record_call(
                profile=self._profile,
                prompt_tokens=prompt_tokens or 0,
                completion_tokens=completion_tokens or 0,
                duration_ms=duration_ms,
            )
        except Exception:
            # metrics 失败不影响主链路
            pass

        # 重置状态（同一 handler 实例可能被复用）
        self._start_ts = None

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        """LLM 调用失败：记录耗时 + 错误信息"""
        duration_ms = 0
        if self._start_ts is not None:
            duration_ms = int((time.perf_counter() - self._start_ts) * 1000)

        logger.bind(
            llm_event="error",
            model=self._model_name,
            duration_ms=duration_ms,
            error_type=type(error).__name__,
        ).error(
            f"llm error model={self._model_name} duration_ms={duration_ms} "
            f"error_type={type(error).__name__}: {error}"
        )

        # 2026-07-20 (#22)：错误也记入 metrics
        try:
            from app.agent.llm_metrics import get_metrics_collector

            get_metrics_collector().record_error(
                profile=self._profile, duration_ms=duration_ms
            )
        except Exception:
            pass

        self._start_ts = None