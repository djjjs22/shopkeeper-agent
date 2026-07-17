"""
应用主配置

定义 conf/app_config.yaml 在程序中的结构化配置对象
项目启动后会在这里一次性完成配置文件加载和类型化转换，其他模块只需要导入 app_config
就可以按属性方式读取日志 MySQL Qdrant Embedding Elasticsearch 和 LLM 配置
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from omegaconf import OmegaConf


@dataclass
class File:
    """文件日志配置"""

    enable: bool
    level: str
    path: str
    rotation: str
    retention: str


@dataclass
class Console:
    """控制台日志配置"""

    enable: bool
    level: str


@dataclass
class LoggingConfig:
    """日志总配置"""

    file: File
    console: Console


@dataclass
class DBConfig:
    """MySQL 连接配置"""

    host: str
    port: int
    user: str
    password: str
    database: str


@dataclass
class QdrantConfig:
    """Qdrant 连接与向量维度配置"""

    host: str
    port: int
    embedding_size: int


@dataclass
class EmbeddingConfig:
    """Embedding 服务配置"""

    host: str
    port: int
    model: str


@dataclass
class ESConfig:
    """Elasticsearch 配置"""

    host: str
    port: int
    index_name: str


@dataclass
class LLMConfig:
    """大模型调用配置"""

    model_name: str
    api_key: str
    base_url: str
    # 请求超时（秒）：防止单次慢调用阻塞整个事件循环
    request_timeout: int = 30
    # 最大输出 token 数：防止 LLM 生成超长无关内容
    max_tokens: int = 2000


@dataclass
class RedisConfig:
    """Redis 连接与降级配置"""

    url: str
    max_connections: int
    socket_timeout: float
    default_ttl_seconds: int
    key_prefix: str
    # 失败重试阈值：连续失败 N 次才标记 Redis 不可用，避免网络抖动误降级
    fail_threshold: int = 3
    # 内存 dict 降级层的最大 session 数（超过时 LRU 淘汰最旧 session）
    max_memory_sessions: int = 1000
    # 后台探活协程的间隔（秒）
    probe_interval_seconds: int = 30


# ─────────────────────────────────────────────────────────────────────
# 2026-07-17 改造：LLM Profile Registry 支持多模型切换
# ─────────────────────────────────────────────────────────────────────


@dataclass
class LLMProfileConfig:
    """单个 LLM profile 的配置

    与原 LLMConfig 结构兼容，额外带一个 profile_name 标识（方便日志/admin API 输出）。
    """

    model_name: str
    api_key: str
    base_url: str
    request_timeout: int = 30
    max_tokens: int = 2000
    profile_name: str = ""  # 注入：profile 名


@dataclass
class LLMProfilesConfig:
    """所有 LLM profile 的总配置

    形如：
        llm_profiles:
          cheap:  {model_name: ..., ...}
          strong: {model_name: ..., ...}

    字段是**直接**按 profile 名展开（cheap / strong），
    而不是嵌套 dict，方便 OmegaConf strict merge。
    """

    cheap: LLMProfileConfig | None = None
    strong: LLMProfileConfig | None = None
    # 兜底：未知 profile 名走这里（运行时由 OmegaConf 动态填充）
    profiles: dict[str, LLMProfileConfig] = field(default_factory=dict)

    def get(self, profile: str) -> LLMProfileConfig:
        """按 profile 名取配置；不存在抛 KeyError 让上层 fail-fast"""
        # 先查显式字段
        if profile == "cheap" and self.cheap is not None:
            return self.cheap
        if profile == "strong" and self.strong is not None:
            return self.strong
        # 再查 profiles dict（兼容运行时添加的 profile）
        if profile in self.profiles:
            return self.profiles[profile]
        available = self._list_profile_names()
        raise KeyError(
            f"LLM profile '{profile}' 未在 llm_profiles 中定义。"
            f"可用 profile: {available or '(none)'}"
        )

    def _list_profile_names(self) -> list[str]:
        names = []
        if self.cheap is not None:
            names.append("cheap")
        if self.strong is not None:
            names.append("strong")
        names.extend(self.profiles.keys())
        return sorted(set(names))

    def all_profiles(self) -> dict[str, LLMProfileConfig]:
        """返回所有 profile（显式字段 + dict）"""
        result: dict[str, LLMProfileConfig] = {}
        if self.cheap is not None:
            result["cheap"] = self.cheap
        if self.strong is not None:
            result["strong"] = self.strong
        result.update(self.profiles)
        return result


@dataclass
class NodeProfilesConfig:
    """节点 → profile 的映射配置

    形如：
        node_profiles:
          classify_intent: cheap
          generate_intent: strong
    """

    # 用 Mapping 父类方便运行时修改（OmegaConf 默认返回 DictConfig，要转成 dict）
    mapping: dict[str, str] = field(default_factory=dict)


@dataclass
class AppConfig:
    """项目级总配置入口"""

    logging: LoggingConfig
    db_meta: DBConfig
    db_dw: DBConfig
    qdrant: QdrantConfig
    embedding: EmbeddingConfig
    es: ESConfig
    llm: LLMConfig
    redis_cfg: RedisConfig
    # 2026-07-17 新增：profile 配置不在 strict schema 里（运行时单独装配），
    # 因为 OmegaConf strict 不接受动态 key（cheap/strong/classify_intent/...）


# 从当前文件位置回到项目根目录，再定位到 conf/app_config.yaml
project_root = Path(__file__).parents[2]
config_file = project_root / "conf" / "app_config.yaml"

# 先读取本地 .env，让 YAML 中的 ${oc.env:...} 可以解析到敏感配置
load_dotenv(project_root / ".env")


def _expand_env_placeholders(node):
    """递归展开 dict/str 里的 ${ENV_VAR} 占位符

    2026-07-17 改造：之前 YAML 用的是 OmegaConf 的 ${oc.env:VAR,default} 语法，
    profile yaml 里希望用更标准的 ${VAR} 形式，所以加这个后处理。
    只在 ${VAR} 完全大写或下划线时做替换（避免误伤 SQL 里的 ${} 字符串）。
    """
    if isinstance(node, str):
        # 匹配 ${VAR_NAME}，VAR_NAME 必须是 [A-Z_][A-Z0-9_]*
        pattern = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")

        def repl(m: re.Match[str]) -> str:
            var_name = m.group(1)
            value = os.environ.get(var_name)
            if value is None:
                raise RuntimeError(
                    f"配置占位符 ${{{var_name}}} 未在环境变量中定义。"
                    f"请在 .env 或部署环境注入该变量后重启。"
                )
            return value

        return pattern.sub(repl, node)
    elif isinstance(node, dict):
        return {k: _expand_env_placeholders(v) for k, v in node.items()}
    elif isinstance(node, list):
        return [_expand_env_placeholders(v) for v in node]
    return node


# 读取 YAML 配置内容
context = OmegaConf.load(config_file)

# ─────────────────────────────────────────────────────────────────────
# 2026-07-17 改造：OmegaConf strict schema 绕过 + env 占位符展开
# ─────────────────────────────────────────────────────────────────────
#
# **为什么绕过 OmegaConf strict schema**
# 原 AppConfig 用 OmegaConf.structured(AppConfig) 做 strict merge —— 任何不在
# dataclass 字段里的 key 都会触发 `Key 'xxx' not in 'AppConfig'` 报错。
# 但 llm_profiles / node_profiles 的 key 是**动态**的：
#   - llm_profiles: cheap / strong（以及未来可能加的 profile 名）
#   - node_profiles: classify_intent / generate_intent / ...（8+ 个节点名）
# 把它们写成 dataclass 字段意味着每次加 profile 或节点都要改代码 —— 不合理。
#
# 解决：merge 前先把这两个节点 pop 出来，用 `_build_llm_profiles_config` /
# `_build_node_profiles_config` 手动构造 dataclass 装配到 app_config 上。
# 这样保持 strict schema 不变量，同时支持动态 key。
#
# **为什么需要 ${VAR} 占位符二次展开**
# OmegaConf 原生支持 ${oc.env:VAR,default}，但语法冗长。YAML 里希望用更标准的
# ${VAR} 形式（如 api_key: ${LLM_STRONG_API_KEY}）。
# OmegaConf 在 merge 时会尝试解析 ${}，但对自定义占位符格式不识别 —— 所以这里
# 在 merge 之后、to_object 之前手动跑一遍正则替换（只匹配全大写变量名，避免误伤）。

# 1. 把动态节点 pop 出来（不进 strict schema）
llm_profiles_node = context.pop("llm_profiles", None)
node_profiles_node = context.pop("node_profiles", None)

# 2. llm_profiles 节点展开 ${VAR} 占位符 → 重建 OmegaConf 节点
if llm_profiles_node is not None:
    expanded = _expand_env_placeholders(OmegaConf.to_container(llm_profiles_node))
    llm_profiles_node = OmegaConf.create(expanded)

# 根据 AppConfig 生成结构化配置 schema（不含 llm_profiles/node_profiles，见上）
schema = OmegaConf.structured(AppConfig)

# 把配置结构和配置值合并，再转换成可以直接按属性访问的对象
app_config: AppConfig = OmegaConf.to_object(OmegaConf.merge(schema, context))


# 3. 单独装配 llm_profiles 和 node_profiles（不进 OmegaConf strict schema，见顶部说明）
# 注：占位符 ${VAR} 已在 _expand_env_placeholders 里 fail-fast（缺失即抛 RuntimeError），
# 这里不需要再额外校验。
def _build_llm_profiles_config(llm_profiles_node) -> LLMProfilesConfig:
    """从 OmegaConf 节点构建 LLMProfilesConfig"""
    profiles_dict: dict[str, LLMProfileConfig] = {}
    if llm_profiles_node is None:
        return LLMProfilesConfig(profiles=profiles_dict)

    container = OmegaConf.to_container(llm_profiles_node)
    if not isinstance(container, dict):
        return LLMProfilesConfig(profiles=profiles_dict)

    for name, cfg in container.items():
        if not isinstance(cfg, dict):
            continue
        profile_cfg = LLMProfileConfig(
            model_name=cfg.get("model_name", ""),
            base_url=cfg.get("base_url", ""),
            api_key=cfg.get("api_key", ""),
            request_timeout=cfg.get("request_timeout", 30),
            max_tokens=cfg.get("max_tokens", 2000),
            profile_name=name,
        )
        profiles_dict[name] = profile_cfg

    return LLMProfilesConfig(profiles=profiles_dict)


def _build_node_profiles_config(node_profiles_node) -> NodeProfilesConfig:
    """从 OmegaConf 节点构建 NodeProfilesConfig"""
    mapping: dict[str, str] = {}
    if node_profiles_node is None:
        return NodeProfilesConfig(mapping=mapping)

    container = OmegaConf.to_container(node_profiles_node)
    if not isinstance(container, dict):
        return NodeProfilesConfig(mapping=mapping)

    for node_name, profile_name in container.items():
        if isinstance(profile_name, str):
            mapping[node_name] = profile_name

    return NodeProfilesConfig(mapping=mapping)


app_config.llm_profiles = _build_llm_profiles_config(llm_profiles_node)
app_config.node_profiles = _build_node_profiles_config(node_profiles_node)


if __name__ == "__main__":
    # 简单测试：验证配置是否能正常读取
    print(app_config.es.host)