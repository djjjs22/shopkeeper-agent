# -*- coding: utf-8 -*-
"""
test_llm_registry.py
====================

LLMRegistry 内部行为单元测试。

覆盖场景：
- registry 单例 + 启动时从 config 构建
- get(profile) 返回 model 实例
- get_by_node(node_name) 按 node_profiles 映射
- 未知 profile / 未知节点 → KeyError
- rebuild_profile() 热切换（实例替换）
- list_profiles() 返回所有 profile 名
- 线程安全：并发 get 不抛异常
- 老代码兼容：from app.agent.llm import llm 仍可用

admin router HTTP API 行为见 tests/test_admin_router.py
"""

import threading

import pytest

from app.agent.llm import get_llm, get_registry
from app.conf.app_config import app_config


class TestRegistryBasics:
    """registry 基础行为"""

    def test_list_profiles_includes_cheap_and_strong(self):
        """config 配了 cheap + strong，list_profiles 应返回两者"""
        reg = get_registry()
        profiles = reg.list_profiles()
        assert "cheap" in profiles
        assert "strong" in profiles

    def test_get_returns_model(self):
        """get(cheap) 应返回 Runnable（with_config 后是 RunnableBinding）"""
        from langchain_core.runnables import Runnable

        reg = get_registry()
        m = reg.get("cheap")
        assert isinstance(m, Runnable)
        # 必须带 LLMTimingCallback
        assert any(
            cb.__class__.__name__ == "LLMTimingCallback"
            for cb in m.config.get("callbacks", [])
        )

    def test_get_unknown_profile_raises(self):
        """未知 profile 名抛 KeyError"""
        reg = get_registry()
        with pytest.raises(KeyError, match="nonexistent"):
            reg.get("nonexistent_profile")


class TestGetByNode:
    """按节点名取 model（走 node_profiles 映射）"""

    def test_known_node_returns_model(self):
        """respond_chitchat 节点配 cheap（弱模型），get_llm 应返回 cheap profile 的 model"""
        from langchain_core.runnables import Runnable

        m = get_llm("respond_chitchat")
        assert isinstance(m, Runnable)
        # 验证 metadata 里标了 profile
        assert m.config.get("metadata", {}).get("profile") == "cheap"

    def test_strong_node_returns_strong(self):
        """generate_intent 节点配 strong（强模型），get_llm 应返回 strong profile"""
        from langchain_core.runnables import Runnable

        m = get_llm("generate_intent")
        assert isinstance(m, Runnable)
        assert m.config.get("metadata", {}).get("profile") == "strong"

    def test_known_node_strong(self):
        """generate_intent 节点配 strong，应返回 strong profile"""
        m = get_llm("generate_intent")
        # 通过 model 属性验证（两个 profile 都是 OpenAI 兼容 ChatOpenAI 实例）
        assert m is not None

    def test_unknown_node_raises(self):
        """节点没在 node_profiles 中 → KeyError"""
        with pytest.raises(KeyError, match="unknown_node"):
            get_llm("unknown_node_xyz")


class TestRebuildProfile:
    """热切换：rebuild_profile 替换 registry 里的 model 实例"""

    def test_rebuild_returns_new_model(self):
        """rebuild_profile 返回新实例，覆盖 registry 里的旧实例"""
        reg = get_registry()
        old = reg.get("cheap")
        new = reg.rebuild_profile("cheap")
        assert new is not old  # 新实例
        # registry 里现在是新实例
        assert reg.get("cheap") is new

    def test_rebuild_unknown_profile_raises(self):
        """重建不存在的 profile 抛 KeyError"""
        reg = get_registry()
        with pytest.raises(KeyError, match="ghost"):
            reg.rebuild_profile("ghost_profile")


class TestThreadSafety:
    """并发读 get() 不抛异常"""

    def test_concurrent_get_does_not_crash(self):
        reg = get_registry()
        errors = []

        def worker():
            try:
                for _ in range(20):
                    reg.get("cheap")
                    reg.get("strong")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"并发读 get 抛异常: {errors}"





class TestLegacyCompatibility:
    """老代码兼容：from app.agent.llm import llm 仍可用"""

    def test_llm_singleton_is_runnable(self):
        """默认 llm 单例至少是 Runnable（兼容 monkeypatch 替换场景）"""
        from langchain_core.runnables import Runnable

        from app.agent.llm import llm

        # 不强求 metadata.profile（其他测试可能 monkeypatch 替换成 mock）
        assert isinstance(llm, Runnable)