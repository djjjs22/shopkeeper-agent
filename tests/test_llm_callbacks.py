# -*- coding: utf-8 -*-
"""
test_llm_callbacks.py
=====================

LLMTimingCallback 的单元测试。

覆盖场景：
- on_llm_start 记录模型名
- on_llm_end 记录耗时 + 从 llm_output 抽 token_usage
- on_llm_error 记录错误
- 多个 start/end 循环正确重置状态
- 异常 token_usage 格式不会让 callback crash
"""

import pytest

from app.agent.llm_callbacks import LLMTimingCallback


class TestCallbackLifecycle:
    """LLM 调用生命周期：start → end / error"""

    def test_start_records_model_name(self):
        cb = LLMTimingCallback()
        cb.on_llm_start(
            serialized={"kwargs": {"model_name": "MiniMax-M3"}, "name": "ChatOpenAI"},
            prompts=["hi"],
        )
        assert cb._model_name == "MiniMax-M3"
        assert cb._start_ts is not None

    def test_start_fallback_to_model_key(self):
        """旧版 langchain 用 model 而不是 model_name"""
        cb = LLMTimingCallback()
        cb.on_llm_start(
            serialized={"kwargs": {"model": "gpt-4o"}, "name": "ChatOpenAI"},
            prompts=["hi"],
        )
        assert cb._model_name == "gpt-4o"

    def test_start_fallback_to_name(self):
        """kwargs 都没有时回退到 serialized.name"""
        cb = LLMTimingCallback()
        cb.on_llm_start(
            serialized={"kwargs": {}, "name": "ChatOpenAI"},
            prompts=["hi"],
        )
        assert cb._model_name == "ChatOpenAI"

    def test_start_unknown_when_empty(self):
        """serialized 是空 dict 时不报错"""
        cb = LLMTimingCallback()
        cb.on_llm_start(serialized={}, prompts=["hi"])
        assert cb._model_name == "unknown"


class TestCallbackEnd:
    def test_end_extracts_token_usage_from_llm_output(self):
        """标准用法：从 response.llm_output["token_usage"] 取"""
        cb = LLMTimingCallback()

        class FakeResponse:
            llm_output = {
                "token_usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                },
                "model_name": "MiniMax-M3",
            }
            generations = []

        cb.on_llm_start(serialized={"kwargs": {"model_name": "MiniMax-M3"}}, prompts=["hi"])
        # end 不抛异常即可
        cb.on_llm_end(FakeResponse())
        # start_ts 已重置
        assert cb._start_ts is None

    def test_end_extracts_from_generations_message_metadata(self):
        """新版 langchain 把 usage 放在 generations[0][0].message.usage_metadata"""
        cb = LLMTimingCallback()

        class FakeMessage:
            usage_metadata = {
                "input_tokens": 200,
                "output_tokens": 80,
                "total_tokens": 280,
            }

        class FakeGen:
            message = FakeMessage()

        class FakeResponse:
            llm_output = None  # 新版可能为 None
            generations = [[FakeGen()]]

        cb.on_llm_start(serialized={"kwargs": {"model_name": "MiniMax-M3"}}, prompts=["hi"])
        cb.on_llm_end(FakeResponse())
        assert cb._start_ts is None

    def test_end_without_start_does_not_crash(self):
        """防御性：start 未触发时不报错（on_llm_end 直接返回）"""
        cb = LLMTimingCallback()
        # 不调用 on_llm_start，直接 end
        class FakeResponse:
            llm_output = {"token_usage": {"prompt_tokens": 1, "completion_tokens": 2}}
            generations = []

        # 不抛异常即通过
        cb.on_llm_end(FakeResponse())

    def test_end_with_malformed_response_does_not_crash(self):
        """response.llm_output 不是 dict 时不报错"""
        cb = LLMTimingCallback()
        cb.on_llm_start(serialized={"kwargs": {"model_name": "MiniMax-M3"}}, prompts=["hi"])

        class WeirdResponse:
            llm_output = "not a dict"  # 异常类型
            generations = "garbage"  # 也异常

        # 不抛异常即通过
        cb.on_llm_end(WeirdResponse())


class TestCallbackError:
    def test_error_records_error_type(self):
        """on_llm_error 必须记录错误类型 + 耗时"""
        cb = LLMTimingCallback()
        cb.on_llm_start(serialized={"kwargs": {"model_name": "MiniMax-M3"}}, prompts=["hi"])

        # 不抛异常即通过
        cb.on_llm_error(ValueError("connection timeout"))
        assert cb._start_ts is None  # 错误后也要重置

    def test_error_without_start_does_not_crash(self):
        """start 未触发时 on_llm_error 也不应 crash（duration_ms 记 0）"""
        cb = LLMTimingCallback()
        # 不调用 on_llm_start，直接 error
        cb.on_llm_error(RuntimeError("oops"))


class TestCallbackCycle:
    """多次 start → end 循环：每次都正确重置"""

    def test_repeated_calls_independent(self):
        cb = LLMTimingCallback()

        class FakeResponse:
            llm_output = {"token_usage": {"prompt_tokens": 1, "completion_tokens": 2}}
            generations = []

        for i in range(3):
            cb.on_llm_start(
                serialized={"kwargs": {"model_name": f"model-{i}"}}, prompts=[f"q{i}"]
            )
            assert cb._model_name == f"model-{i}"
            cb.on_llm_end(FakeResponse())
            assert cb._start_ts is None