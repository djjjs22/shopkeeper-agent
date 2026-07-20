"""
Prompt 模板加载工具

按名称从项目根目录的 prompts 目录读取 .prompt 文件
业务节点只需要传入逻辑名称，不需要关心提示词文件的具体路径

2026-07-20 优化：加 lru_cache
================================
原实现每次调用都 `read_text()` 磁盘 IO，一次完整 data_query 至少触发 8 次
prompt 加载（rewrite_query、classify_intent、3 个 extend_keywords、filter_table、
filter_metric、generate_intent...）。这些 .prompt 文件运行期不变，缓存零风险。

如未来需要热切换 prompt（admin API 改文件立即生效），暴露 clear_prompt_cache()。
"""

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=128)
def load_prompt(name: str) -> str:
    """读取指定名称的 prompt 模板内容

    结果按 name 缓存（.prompt 文件运行期不可变）。第一次调用读盘，
    后续调用直接返回缓存。
    """

    # app/prompt/prompt_loader.py 向上两级回到项目根目录，再进入 prompts 目录
    prompt_path = Path(__file__).parents[2] / "prompts" / f"{name}.prompt"
    return prompt_path.read_text(encoding="utf-8")


def clear_prompt_cache() -> None:
    """清空 prompt 缓存（admin 改完 .prompt 文件后调用，让下次 load 重新读盘）"""
    load_prompt.cache_clear()
