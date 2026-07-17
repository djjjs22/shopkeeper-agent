# -*- coding: utf-8 -*-
"""
pydantic_parser.py
==================

**为什么有这个文件**：
把 Pydantic 强校验 + think 块兼容合并到一个 LangChain parser，
让 `prompt | llm | parser` 链式调用直接产出强类型对象。

改前（2026-07-17 前）：
- 用 `SafeJsonOutputParser`：剥 think 块 + `json.loads`，产出 dict
- dict 无类型保护，generate_sql 节点读取字段时炸 KeyError

改后：
- 用 `PydanticIntentParser(pydantic_object=QueryIntent)`
- 先剥 think 块 + 抓 ```json``` 围栏（复用 safe_parse_json 的核心逻辑）
- 再 `QueryIntent.model_validate(...)` 强校验
- 校验失败抛 OutputParserException，触发 langchain 自动 retry 机制

**与 SafeJsonOutputParser 的关系**：
- SafeJsonOutputParser：输出 dict，宽松，给"输出结构不固定"的节点用（filter_*）
- PydanticIntentParser：输出 BaseModel 实例，严格，给"schema 必须严格匹配"的节点用
- 两者并存，按场景选用
"""

from typing import Any

from langchain_core.exceptions import OutputParserException
from langchain_core.output_parsers import BaseOutputParser
from pydantic import BaseModel, ValidationError

from app.core.safe_json_parser import safe_parse_json


class PydanticIntentParser(BaseOutputParser):
    """Pydantic 强校验输出解析器

    用法：
        from app.entities.intent_schema import QueryIntent
        parser = PydanticIntentParser(pydantic_object=QueryIntent)
        chain = prompt | llm | parser
        intent: QueryIntent = await chain.ainvoke({...})  # 强类型

    错误处理：
    - LLM 输出非 JSON → OutputParserException（langchain 自动 retry）
    - JSON 不符合 schema → OutputParserException（带详细 Pydantic 错误信息）
    """

    pydantic_object: type[BaseModel]

    def parse(self, text: str) -> BaseModel:
        """把 LLM 输出文本解析为 Pydantic 模型实例

        Args:
            text: LLM 原始输出（可能含 <think>...</think> 块和 ```json``` 围栏）

        Returns:
            pydantic_object 的实例

        Raises:
            OutputParserException: 解析失败时抛出（langchain 会自动 retry）
        """
        # 第一步：剥 think 块 + 抓 json 围栏（复用 safe_parse_json 的核心逻辑）
        try:
            data: Any = safe_parse_json(text)
        except ValueError as e:
            raise OutputParserException(
                f"PydanticIntentParser: JSON 解析失败 - {e}",
                llm_output=text,
                observation=str(e),
            ) from e

        # 第二步：必须是 dict（不能是 list / str / None）
        if not isinstance(data, dict):
            raise OutputParserException(
                f"PydanticIntentParser: 期望 dict，实际 {type(data).__name__}",
                llm_output=text,
                observation=f"got {type(data).__name__}, expected dict",
            )

        # 第三步：Pydantic 强校验
        try:
            return self.pydantic_object.model_validate(data)
        except ValidationError as e:
            # 提取前 3 条错误信息，避免日志过长
            errors = e.errors()[:3]
            err_summary = "; ".join(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                for err in errors
            )
            raise OutputParserException(
                f"PydanticIntentParser: schema 校验失败 - {err_summary}",
                llm_output=text,
                observation=err_summary,
            ) from e

    @property
    def _type(self) -> str:
        return "pydantic_intent"

    def get_format_instructions(self) -> str:
        """生成 LLM prompt 用的格式说明

        用法（在 prompt 末尾追加）：
            {parser.get_format_instructions()}

        实际效果：让 LLM 知道应该输出什么 JSON 结构 + 字段类型约束。
        """
        # 简化版：用 Pydantic 的 model_json_schema() 生成 schema 描述
        # 再加上"只输出 JSON"的硬约束
        import json

        schema = self.pydantic_object.model_json_schema()
        schema_str = json.dumps(schema, ensure_ascii=False, indent=2)

        return (
            "\n【输出格式严格约束】\n"
            "1. ⛔ 只输出 JSON，不要输出任何解释、Markdown 围栏或 SQL 关键字\n"
            "2. JSON 必须符合下面的 JSON Schema（字段名、类型、必填性严格匹配）：\n"
            f"```json\n{schema_str}\n```\n"
            "3. 缺字段或不确定时，使用 schema 中的默认值（空数组 / null / 空字符串）\n"
            "4. 不要包含 schema 之外的字段（如 thinking 步骤残留会被自动忽略，但不要主动写）\n"
        )