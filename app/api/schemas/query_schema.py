"""
问数接口请求体定义

集中声明 API 层输入输出的数据结构，让路由函数只处理业务流程，
字段校验和 OpenAPI 文档生成交给 Pydantic 与 FastAPI 完成。

2026-07-20 (#20 Prompt Injection 防护)：query 加 validator 拦截常见注入模板
"""

import re

from pydantic import BaseModel, Field, field_validator

# 常见 prompt injection 模板（不终极，但拦 90% 业余注入）
# 命中任一即拒收。注意：业务问句里几乎不会出现这些组合。
_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        # 经典 jailbreak 词
        r"ignore\s+(previous|above|all)\s+(instructions?|prompts?)",
        r"disregard\s+(previous|above|all)",
        r"forget\s+(previous|above|all)\s+(instructions?|rules)",
        # 系统提示词标记
        r"<\s*/?\s*system\s*>",
        r"<\s*/?\s*prompt\s*>",
        # 直接指令"输出 JSON / 执行 SQL"
        r"输出\s*json.*?information_schema",
        r"忽略.{0,10}(指令|提示|规则|上下文)",
        # 显式要 LLM 越权的
        r"(tell|show|reveal|return)\s+(me\s+)?(your\s+)?(system|hidden)\s+prompt",
        r"show\s+me\s+(the\s+)?api[_\s-]?key",
    ]
]


class QuerySchema(BaseModel):
    """`/api/query` 请求体，承载用户输入的自然语言问题"""

    # 前端请求体中的 query 字段，例如 {"query": "统计华北地区销售额"}
    # max_length=500 防止超大 payload 灌进 jieba + LLM（刀 10）
    query: str = Field(max_length=500, description="用户查询文本")

    # 2026-07-17 改造：Multi-Agent 开关
    # 默认 False（保持向后兼容，走老 13 节点 graph）
    # True 时走 supervisor_graph（planner 拆 sub_query 并行执行）
    use_multi_agent: bool = Field(
        default=False,
        description="是否启用 Multi-Agent 模式（planner 拆 sub_query 并行执行）",
    )

    @field_validator("query")
    @classmethod
    def _reject_prompt_injection(cls, v: str) -> str:
        """拦截明显的 prompt injection 模板（2026-07-20 #20）

        非终极方案——LLM 自身安全 + 后端 SQL 防火墙才是纵深防御的主体，
        这里只做最外层的"明显注入模板"拦截，降低误触发 LLM 越权输出的概率。
        """
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(v):
                raise ValueError(
                    "检测到可疑指令注入，已拒绝。请用自然语言描述你想查询的数据。"
                )
        return v
