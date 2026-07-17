# -*- coding: utf-8 -*-
"""
admin_schema.py
===============

admin API 的请求/响应 schema。

仅承载 admin 路由的入参/出参数据结构（其他 admin 端点未来也放这里）。
"""

from pydantic import BaseModel, Field


class LLMSwitchRequest(BaseModel):
    """`POST /api/admin/llm-profile` 请求体

    把指定节点的 profile 切换到指定 profile 名。
    例：{"node": "classify_intent", "profile": "cheap"}
    """

    node: str = Field(
        description="节点名（classify_intent/generate_intent/correct_sql/...）",
    )
    profile: str = Field(description="目标 profile 名（cheap/strong/...）")


class LLMSwitchResponse(BaseModel):
    """切换结果（同时给前端展示）"""

    node: str
    old_profile: str
    new_profile: str


class LLMStatusResponse(BaseModel):
    """`GET /api/admin/llm-profile` 响应体：当前所有节点的 profile 映射 + 各 profile 的模型配置"""

    node_profiles: dict[str, str] = Field(description="节点 → profile 的当前映射")
    profiles: dict[str, dict] = Field(description="profile → 模型配置（model_name/base_url/...）")