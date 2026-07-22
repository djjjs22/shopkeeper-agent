"""
电商问数 Agent 使用的大模型实例（2026-07-17 改造：LLM Profile Registry）

集中初始化多个 OpenAI 兼容的 Chat Model（按 profile 名），供节点按需取用。

**改前**：
- 模块级单例 `llm = init_chat_model(...)`，8 个节点全部 import 这个全局变量
- 切换模型必须改 yaml + 重启服务

**改后**：
- `LLMRegistry` 类：内部 dict[profile_name, BaseChatModel]，threading.Lock 保护
- 节点通过 `get_llm(node_name)` 接口获取 model（按 node_profiles 映射）
- admin API 调用 `registry.set_profile(name)` 可热切换（重建 model 覆盖旧实例）
- 老代码 `from app.agent.llm import llm` 仍可用，llm = strong profile 默认值

**多 profile 并存设计**：
- 同进程内 cheap/strong 共存，按节点路由到不同 profile
- 不切换时各 profile 独立实例，无锁竞争（读多写少，锁只在热切换时获取）
"""

import threading
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from app.agent.llm_callbacks import LLMTimingCallback
from app.conf.app_config import app_config
from app.core.log import logger


class LLMRegistry:
    """LLM Profile 注册中心

    线程安全：所有读写都走 self._lock。
    设计为单例（模块级 _registry 变量）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._models: dict[str, BaseChatModel] = {}
        self._init_from_config()

    def _init_from_config(self) -> None:
        """从 app_config 读取所有 profile 配置，构建 model 实例"""
        profiles_cfg = (
            app_config.llm_profiles.all_profiles()
            if app_config.llm_profiles
            else {}
        )
        if not profiles_cfg:
            # 没有 llm_profiles 配置时回退到老 llm 配置（兼容现有部署）
            logger.warning(
                "llm_profiles 未配置，回退到老 llm 配置（仅注册一个 'default' profile）"
            )
            self._models["default"] = self._build_model(
                "default",
                app_config.llm.model_name,
                app_config.llm.base_url,
                app_config.llm.api_key,
                app_config.llm.request_timeout,
                app_config.llm.max_tokens,
            )
            return

        for name, cfg in profiles_cfg.items():
            self._models[name] = self._build_model(
                name,
                cfg.model_name,
                cfg.base_url,
                cfg.api_key,
                cfg.request_timeout,
                cfg.max_tokens,
            )

    def _build_model(
        self,
        profile_name: str,
        model_name: str,
        base_url: str,
        api_key: str,
        request_timeout: int,
        max_tokens: int,
    ) -> BaseChatModel:
        """构造一个 model 实例（挂 LLMTimingCallback）"""
        base = init_chat_model(
            model=model_name,
            # 兼容 OpenAI 协议的服务（MiniMax / 硅基流动 / 第三方中转）都用 openai provider
            model_provider="openai",
            base_url=base_url,
            api_key=api_key,
            temperature=0,
            request_timeout=request_timeout,
            max_tokens=max_tokens,
        )
        # with_config 返回新实例，不影响原 base
        # 2026-07-22 LangSmith 升级：metadata 多带一个 langsmith 标签字段，
        # 让 trace UI 能按 profile 筛选（cheap vs strong 调用对比）
        return base.with_config(
            {
                "callbacks": [LLMTimingCallback()],
                "metadata": {
                    "profile": profile_name,
                    # LangSmith 会把这些 key 作为可筛标签展示在 trace UI
                    "langsmith_metadata": {
                        "component": "llm",
                        "profile": profile_name,
                    },
                },
            }
        )

    def get(self, profile: str) -> BaseChatModel:
        """按 profile 名取 model 实例

        Args:
            profile: profile 名（cheap/strong/...）

        Returns:
            BaseChatModel 实例（callbacks 已挂）

        Raises:
            KeyError: profile 未注册时
        """
        with self._lock:
            if profile not in self._models:
                available = ", ".join(sorted(self._models.keys())) or "(none)"
                raise KeyError(
                    f"LLM profile '{profile}' 未注册。可用: {available}"
                )
            return self._models[profile]

    def get_by_node(self, node_name: str) -> BaseChatModel:
        """按节点名取 model（走 node_profiles 映射）

        Args:
            node_name: 节点函数名（classify_intent / generate_intent / ...）

        Raises:
            KeyError: 节点没在 node_profiles 中配置，或对应 profile 不存在
        """
        mapping = (
            app_config.node_profiles.mapping
            if app_config.node_profiles
            else {}
        )
        if node_name not in mapping:
            available = ", ".join(sorted(mapping.keys())) or "(none)"
            raise KeyError(
                f"节点 '{node_name}' 未在 node_profiles 中配置。可用节点: {available}"
            )
        return self.get(mapping[node_name])

    def rebuild_profile(self, profile_name: str) -> BaseChatModel:
        """热切换：重建指定 profile 的 model 实例（不重启服务）

        用法：admin API 调用后立即生效，新发起的 LLM 调用会用新模型。
        已经在飞的调用（旧 model 实例）继续完成，不受影响。

        Args:
            profile_name: 要重建的 profile 名

        Returns:
            新构建的 model 实例

        Raises:
            KeyError: profile 名在 llm_profiles 中不存在
            RuntimeError: 构建失败（env 占位符未解析等）
        """
        cfg = app_config.llm_profiles.get(profile_name)
        new_model = self._build_model(
            profile_name,
            cfg.model_name,
            cfg.base_url,
            cfg.api_key,
            cfg.request_timeout,
            cfg.max_tokens,
        )
        with self._lock:
            self._models[profile_name] = new_model
        return new_model

    def list_profiles(self) -> list[str]:
        """列出所有已注册的 profile 名"""
        with self._lock:
            return sorted(self._models.keys())


# ─────────────────────────────────────────────────────────────────────
# 模块级单例 + 老 API 兼容
# ─────────────────────────────────────────────────────────────────────

# 启动时构建 registry（profile 实例全部就绪）
_registry = LLMRegistry()


def get_llm(node_name: str) -> BaseChatModel:
    """节点获取 model 的标准接口（推荐）

    用法：
        from app.agent.llm import get_llm

        async def classify_intent(state, runtime):
            llm = get_llm("classify_intent")  # 按 node_profiles 自动路由
            chain = prompt | llm | parser
    """
    return _registry.get_by_node(node_name)


def get_registry() -> LLMRegistry:
    """admin API 用的 registry 访问入口（测试也用）"""
    return _registry


# ============================================================================
# DEPRECATED（2026-07-20 #21 配置统一）：
# ============================================================================
# 老代码 `from app.agent.llm import llm` 的兼容入口。**生产代码已全部迁移到
# get_llm(node_name)**（grep `from app.agent.llm import llm` 在 app/ 下零命中）。
# 这里保留只是因为：
#   1. test_llm_registry.py 显式测了"老 import 仍可用"
#   2. 简单部署场景（只配了 yaml.llm 没配 llm_profiles）的兜底
# 新代码不要再用这个全局变量，统一走 get_llm("节点名")。
# ============================================================================
try:
    llm: BaseChatModel = _registry.get("strong")
except KeyError:
    # 没有 strong profile 时退化到老配置（仅一个 default）
    llm = _registry.get("default")


if __name__ == "__main__":
    # 本地快速验证配置 + registry 是否就绪
    print("registered profiles:", _registry.list_profiles())
    print("default llm model:", llm.invoke("你好").content)