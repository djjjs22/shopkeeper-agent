# -*- coding: utf-8 -*-
"""
test_admin_router.py
====================

admin router 端到端测试（FastAPI TestClient）。

覆盖场景：
- 鉴权：缺 token / 错 token → 401；缺 ADMIN_TOKEN 环境变量 → 503
- GET /api/admin/llm-profile：返回 node_profiles + profiles（api_key 已脱敏）
- POST /api/admin/llm-profile：切换节点 → profile 的映射，立即生效
- 未知节点 / 未知 profile → 400 + 友好错误信息

与 test_llm_registry.py 的区别：
- test_llm_registry.py：LLMRegistry 内部行为（单例/锁/get/rebuild/list）
- test_admin_router.py：admin_router HTTP API 行为（鉴权/响应格式/错误码）
"""

import pytest


@pytest.fixture(autouse=True)
def _reset_node_profiles():
    """每个测试用例前后重置 node_profiles 映射，避免 POST 测试污染后续用例

    设计：autouse=True 自动应用，yield 后恢复。Tests can mutate freely.
    """
    from app.conf.app_config import app_config

    original = dict(app_config.node_profiles.mapping)
    yield
    app_config.node_profiles.mapping = original


class TestAdminAuth:
    """鉴权测试：X-Admin-Token 校验"""

    def test_get_without_token_returns_401(self):
        """不带 X-Admin-Token header → 401"""
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        resp = client.get("/api/admin/llm-profile")
        assert resp.status_code == 401
        assert "X-Admin-Token" in resp.json()["detail"]

    def test_get_with_wrong_token_returns_401(self):
        """X-Admin-Token 值不对 → 401"""
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        resp = client.get(
            "/api/admin/llm-profile",
            headers={"X-Admin-Token": "wrong-token"},
        )
        assert resp.status_code == 401

    def test_post_without_token_returns_401(self):
        """POST 不带 token → 401"""
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        resp = client.post(
            "/api/admin/llm-profile",
            json={"node": "respond_chitchat", "profile": "strong"},
        )
        assert resp.status_code == 401


class TestAdminGet:
    """GET /api/admin/llm-profile 测试"""

    def test_get_with_correct_token_returns_200(self):
        """正确 token → 200 + 完整状态"""
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        resp = client.get(
            "/api/admin/llm-profile",
            headers={"X-Admin-Token": "test-admin-token"},
        )
        assert resp.status_code == 200
        data = resp.json()

        # node_profiles 应包含所有 8 个 LLM 节点
        assert "node_profiles" in data
        expected_nodes = {
            "classify_intent",
            "respond_chitchat",
            "filter_table",
            "filter_metric",
            "extract_keywords",
            "generate_intent",
            "correct_sql",
            "rewrite_query",
        }
        assert expected_nodes.issubset(set(data["node_profiles"].keys()))

        # profiles 应包含 cheap + strong
        assert "profiles" in data
        assert "cheap" in data["profiles"]
        assert "strong" in data["profiles"]

        # api_key 必须脱敏（admin 不能看到完整 key）
        for profile_name, profile_data in data["profiles"].items():
            assert "api_key" not in profile_data, (
                f"profile '{profile_name}' 暴露了原始 api_key！"
            )
            assert "api_key_masked" in profile_data

    def test_get_response_mask_format(self):
        """api_key_masked 格式：前 4 后 4，中间 ****"""
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        resp = client.get(
            "/api/admin/llm-profile",
            headers={"X-Admin-Token": "test-admin-token"},
        )
        data = resp.json()

        # conftest 注入的 test-cheap-key-not-real 长度足够
        masked = data["profiles"]["cheap"]["api_key_masked"]
        assert masked.startswith("test")
        assert masked.endswith("real")
        assert "****" in masked


class TestAdminSwitch:
    """POST /api/admin/llm-profile 测试"""

    def test_switch_profile_changes_mapping(self):
        """切换后 GET 应能看到新映射"""
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)

        # 默认 respond_chitchat = cheap，切到 strong
        resp = client.post(
            "/api/admin/llm-profile",
            headers={"X-Admin-Token": "test-admin-token"},
            json={"node": "respond_chitchat", "profile": "strong"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["node"] == "respond_chitchat"
        assert body["old_profile"] == "cheap"
        assert body["new_profile"] == "strong"

        # 验证 GET 看到新映射
        resp2 = client.get(
            "/api/admin/llm-profile",
            headers={"X-Admin-Token": "test-admin-token"},
        )
        data = resp2.json()
        assert data["node_profiles"]["respond_chitchat"] == "strong"

    def test_switch_to_same_profile_succeeds(self):
        """切到当前 profile（无变化）也是 200，old == new"""
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        # 默认 generate_intent = strong，切到 strong
        resp = client.post(
            "/api/admin/llm-profile",
            headers={"X-Admin-Token": "test-admin-token"},
            json={"node": "generate_intent", "profile": "strong"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["old_profile"] == "strong"
        assert body["new_profile"] == "strong"

    def test_switch_unknown_node_returns_400(self):
        """未知节点 → 400 + 列出可用节点"""
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        resp = client.post(
            "/api/admin/llm-profile",
            headers={"X-Admin-Token": "test-admin-token"},
            json={"node": "ghost_node", "profile": "cheap"},
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "ghost_node" in detail
        # 错误信息应包含可用节点列表
        assert "respond_chitchat" in detail or "classify_intent" in detail

    def test_switch_unknown_profile_returns_400(self):
        """未知 profile → 400"""
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        resp = client.post(
            "/api/admin/llm-profile",
            headers={"X-Admin-Token": "test-admin-token"},
            json={"node": "classify_intent", "profile": "ghost_profile"},
        )
        assert resp.status_code == 400

    def test_switch_missing_body_field_returns_422(self):
        """请求体缺字段 → 422（Pydantic 校验失败）"""
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        resp = client.post(
            "/api/admin/llm-profile",
            headers={"X-Admin-Token": "test-admin-token"},
            json={"node": "classify_intent"},  # 缺 profile
        )
        assert resp.status_code == 422


class TestAdminFallback:
    """admin_token 缺失时的兜底行为"""

    def test_admin_token_missing_returns_503(self, monkeypatch):
        """ADMIN_TOKEN 环境变量未设置 → 503（fail-secure）"""
        from fastapi.testclient import TestClient

        from app.main import app

        # 临时清空 ADMIN_TOKEN
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        # 但 app_config 模块启动时已 load 了，需要重设
        # 这里测的是 router 行为：_check_admin_token 读最新 env
        # 所以 delenv 后立即请求即可

        client = TestClient(app)
        resp = client.get("/api/admin/llm-profile")
        assert resp.status_code == 503
        assert "ADMIN_TOKEN" in resp.json()["detail"]
