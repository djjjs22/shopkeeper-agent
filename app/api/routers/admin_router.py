"""
admin 路由

提供运维用的内部 API：
- GET  /api/admin/llm-profile         查看当前节点 → profile 映射 + 各 profile 配置
- POST /api/admin/llm-profile         热切换指定节点到指定 profile

鉴权：所有请求必须带 `X-Admin-Token: <ADMIN_TOKEN 环境变量值>` header。

**为什么单独建一个 router**：
- 和 query_router 分离，避免运维 API 跟业务 API 混在一起
- 鉴权逻辑只在 admin 路由里加一次

**当前权限边界**：
- 只有"切换 profile"和"查看状态"两类操作
- 不能改 yaml、不能执行 SQL、不能查历史 → 后续 V2 接 OAuth + RBAC
"""

import os
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status

from app.agent.llm import get_registry
from app.api.schemas.admin_schema import (
    LLMStatusResponse,
    LLMSwitchRequest,
    LLMSwitchResponse,
)
from app.conf.app_config import app_config
from app.core.log import logger

admin_router = APIRouter()


def _check_admin_token(x_admin_token: str | None) -> None:
    """校验 admin token；不通过抛 401

    X-Admin-Token header 必须等于环境变量 ADMIN_TOKEN 的值。
    环境变量未设置时 → 全部拒绝（fail-secure 原则）。

    日志级别用 warning 而非 error：
    - 这是部署配置缺失，不是代码运行时故障
    - warning 仍会被 loguru 控制台显示，运维能看到
    - 不会触发 ELK / Prometheus 的 ERROR 报警，避免误报
    """
    expected = os.getenv("ADMIN_TOKEN")
    if not expected:
        logger.warning(
            "ADMIN_TOKEN 环境变量未设置，admin API 拒绝所有请求"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_TOKEN not configured on server",
        )
    if not x_admin_token or x_admin_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing X-Admin-Token",
        )


@admin_router.get(
    "/api/admin/llm-profile",
    response_model=LLMStatusResponse,
    summary="查看当前 LLM Profile 状态",
)
async def get_llm_profile(
    x_admin_token: Annotated[str | None, Header()] = None,
) -> LLMStatusResponse:
    _check_admin_token(x_admin_token)

    registry = get_registry()
    profiles_cfg = (
        app_config.llm_profiles.profiles
        if app_config.llm_profiles
        else {}
    )

    # 把 LLMProfileConfig 序列化成 dict（admin 看得到完整配置）
    profiles_dict: dict[str, dict] = {}
    for name, cfg in profiles_cfg.items():
        profiles_dict[name] = {
            "model_name": cfg.model_name,
            "base_url": cfg.base_url,
            # api_key 脱敏：只显示前后 4 位（admin 排查问题时快速确认 key 正确性）
            "api_key_masked": _mask_key(cfg.api_key),
            "request_timeout": cfg.request_timeout,
            "max_tokens": cfg.max_tokens,
        }

    mapping = (
        app_config.node_profiles.mapping
        if app_config.node_profiles
        else {}
    )

    return LLMStatusResponse(
        node_profiles=mapping,
        profiles=profiles_dict,
    )


@admin_router.post(
    "/api/admin/llm-profile",
    response_model=LLMSwitchResponse,
    summary="热切换节点的 LLM profile",
)
async def switch_llm_profile(
    body: LLMSwitchRequest,
    x_admin_token: Annotated[str | None, Header()] = None,
) -> LLMSwitchResponse:
    _check_admin_token(x_admin_token)

    mapping = (
        app_config.node_profiles.mapping
        if app_config.node_profiles
        else {}
    )

    # 1) 校验节点存在
    if body.node not in mapping:
        available_nodes = ", ".join(sorted(mapping.keys())) or "(none)"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown node '{body.node}'. Available: {available_nodes}",
        )

    # 2) 校验 profile 存在（registry 已经校验过配置，但提前校验给更友好错误）
    registry = get_registry()
    if body.profile not in registry.list_profiles():
        available_profiles = ", ".join(registry.list_profiles()) or "(none)"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"unknown profile '{body.profile}'. "
                f"Available: {available_profiles}"
            ),
        )

    # 3) 更新 node_profiles 映射（运行时改动 app_config）
    old_profile = mapping[body.node]
    mapping[body.node] = body.profile

    # 4) 如果目标 profile 还没 build 过（旧 registry 没这个），重新 build
    #    （实际我们的实现是 build 所有配置的 profile，所以一般不用重建）
    try:
        # 触发可能的懒构建（如果以后改成 lazy build）
        registry.get(body.profile)
    except Exception as e:
        # 回滚映射
        mapping[body.node] = old_profile
        logger.error(f"热切换失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"switch failed: {e}",
        ) from e

    logger.info(
        f"admin 热切换 LLM profile: node={body.node} "
        f"{old_profile} -> {body.profile}"
    )

    return LLMSwitchResponse(
        node=body.node,
        old_profile=old_profile,
        new_profile=body.profile,
    )


# ============================================================================
# 2026-07-20 (#22)：LLM 指标查询端点
# ============================================================================


@admin_router.get(
    "/api/admin/metrics",
    tags=["admin"],
    summary="查询 LLM 调用指标（按 profile 聚合：调用数/token/错误率/平均耗时）",
)
async def get_llm_metrics(
    x_admin_token: Annotated[str | None, Header()] = None,
):
    """返回进程启动以来的 LLM 调用聚合指标

    示例：
        curl -H "X-Admin-Token: xxx" http://localhost:8000/api/admin/metrics
    """
    _check_admin_token(x_admin_token)
    from app.agent.llm_metrics import get_metrics_collector

    return {
        "profiles": get_metrics_collector().snapshot(),
    }


@admin_router.post(
    "/api/admin/metrics/reset",
    tags=["admin"],
    summary="清零 LLM 调用指标（调试 / 重新统计时用）",
)
async def reset_llm_metrics(
    x_admin_token: Annotated[str | None, Header()] = None,
):
    """清零所有 profile 的累计指标"""
    _check_admin_token(x_admin_token)
    from app.agent.llm_metrics import get_metrics_collector

    get_metrics_collector().reset()
    return {"status": "ok", "message": "metrics cleared"}


def _mask_key(key: str) -> str:
    """API key 脱敏：保留前 4 后 4，中间用 **** 替代

    例：sk-cp-cSWY65HF-...ZmCM → sk-c****ZmCM
    """
    if not key or len(key) < 10:
        return "****"
    return f"{key[:4]}****{key[-4:]}"