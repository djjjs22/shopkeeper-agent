# -*- coding: utf-8 -*-
"""
safe_json_parser.py
====================

**为什么有这个文件**：
M3 / DeepSeek 等模型会在 `<output>` 前输出 `<think>推理过程</think>` 块。
LangChain 原生的 `JsonOutputParser` 和 `StrOutputParser` 不能识别 think 块：

- `JsonOutputParser` 直接 `json.loads(text)` → 解析失败
- `StrOutputParser` 直接返回 text 全部内容 → think 块污染 SQL

**改前症状**（2026-07-11 实测）：
- filter_metric 节点：json.loads 解析失败
- generate_sql 节点：SQL 前面粘着 think 块，validate_sql 报语法错

**怎么用**（8 个节点已经替换完了）：

```python
# 解析 JSON（替代 JsonOutputParser）
from app.core.safe_json_parser import SafeJsonOutputParser
output_parser = SafeJsonOutputParser()

# 解析纯文本（替代 StrOutputParser）—— 自动剥 think 块 + 抓 ```sql``` 围栏
from app.core.safe_json_parser import StripThinkStrParser
output_parser = StripThinkStrParser()
```

**实现细节**：
1. `_strip_think(text)`: 剥 `<think>...</think>` + ```json``` 围栏
2. `_extract_json_substring(text)`: 从残留文本里抓第一个 JSON 子串（兜底）
3. `safe_parse_json(text)`: 上面两步组合
4. `SafeJsonOutputParser` / `StripThinkStrParser`: 继承 `BaseOutputParser`，
   这样可以接入 `prompt | llm | parser` 的 LangChain chain
"""

import json
import re
from typing import Any

from langchain_core.output_parsers import BaseOutputParser

# ─────────────────────────────────────────────────────────────────────
# 正则常量
# ─────────────────────────────────────────────────────────────────────

# 匹配 <think>...</think> 块（多行、忽略大小写）
# 例：<think>The user is asking for...</think>
# 改前会污染 JSON 解析，必须剥掉
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

# 匹配 ```json ... ``` 围栏
# 例：```json\n{"foo": "bar"}\n```
_JSON_FENCE = re.compile(r"```json\s*(.*?)```", re.DOTALL | re.IGNORECASE)

# 匹配 ```sql ... ``` 围栏（用于 SQL 节点）
# 例：```sql\nSELECT * FROM ...\n```
_SQL_FENCE = re.compile(r"```sql\s*(.*?)```", re.DOTALL | re.IGNORECASE)

# 匹配最外层 JSON 对象（{...}）或数组（[...]）—— 兜底用
# 用非贪婪 + 配对计数，避免截断
_JSON_OBJECT = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", re.DOTALL)
_JSON_ARRAY = re.compile(r"\[[^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*\]", re.DOTALL)


# ─────────────────────────────────────────────────────────────────────
# 核心函数
# ─────────────────────────────────────────────────────────────────────


def _strip_think(text: str) -> str:
    """
    剥 think 块 + json 围栏

    **为什么单独抽出来**：
    safe_parse_json 和 strip_think_for_str 都要剥 think 块，
    抽出来避免重复。

    处理顺序：
    1. 剥 <think>...</think>
    2. 抓 ```json ... ``` 围栏里的内容（如果存在）
    3. 都没匹配上就返回原 text（去 think 后）

    例：
        input:  '<think>xxx</think>```json\\n{"a":1}\\n```'
        output: '{"a":1}'

        input:  '<think>xxx</think>{"a":1}'
        output: '{"a":1}'
    """
    text = _THINK.sub("", text).strip()
    m = _JSON_FENCE.search(text)
    if m:
        return m.group(1).strip()
    return text


def _extract_json_substring(text: str) -> str | None:
    """
    兜底：从残留文本里抓第一个 JSON 子串

    **什么时候会走到这里**：
    LLM 输出格式不规范（既不在 json 围栏里，也不是裸 JSON）
    比如 LLM 在 JSON 前后加了一堆解释文字

    例：
        input: '这是结果：{"a": 1, "b": 2}，请查收'
        output: '{"a": 1, "b": 2}'

        input: '结果数组：[1, 2, 3]'
        output: '[1, 2, 3]'
    """
    m = _JSON_OBJECT.search(text)
    if m:
        return m.group(0)
    m = _JSON_ARRAY.search(text)
    if m:
        return m.group(0)
    return None


def safe_parse_json(text: str) -> Any:
    """
    解析 LLM 输出为 JSON（兼容 think 块 + 围栏 + 兜底）

    **调用链**：
    1. _strip_think() 剥 think + 抓 ```json``` 围栏
    2. 尝试 json.loads(cleaned)
    3. 失败则 _extract_json_substring() 兜底
    4. 还失败就抛 ValueError，让上层 OutputParser 接住

    **为什么用 BaseException 兜底**：
    不同的 LLM 输出格式差异大（多换行、unicode、注释等），
    json.loads 报错类型不一，统一兜底处理。
    """
    cleaned = _strip_think(text or "")
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    # 兜底：抓 JSON 子串
    sub = _extract_json_substring(cleaned)
    if sub:
        try:
            return json.loads(sub)
        except Exception:
            pass
    # 还失败：抛错让上层用 OutputParserException 接
    raise ValueError(f"无法从 LLM 输出解析 JSON: {(text or '')[:200]}")


def strip_think_for_str(text: str) -> str:
    """
    用于 SQL / 闲聊等"纯文本输出"场景：
    1) 剥掉 <think>...</think> 块
    2) 如果有 ```sql ... ``` 围栏，返回围栏里内容
    3) 否则返回剥完 think 的剩余文本

    **改前症状**（2026-07-11）：
    generate_sql 节点用 StrOutputParser 拿到：
        '<think>The user asks for the total...</think>
         SELECT COUNT(*) FROM dim_customer'
    → validate_sql 报 SQL 语法错（think 块内容不是 SQL）

    **改后**：
    → 'SELECT COUNT(*) FROM dim_customer'（干净 SQL）
    """
    if not text:
        return text
    cleaned = _THINK.sub("", text).strip()
    m = _SQL_FENCE.search(cleaned)
    if m:
        return m.group(1).strip()
    return cleaned


# ─────────────────────────────────────────────────────────────────────
# LangChain Parser 封装
# ─────────────────────────────────────────────────────────────────────


class SafeJsonOutputParser(BaseOutputParser):
    """
    替代 LangChain 原生 `JsonOutputParser`，兼容 M3/DeepSeek 的 <think> 块

    **为什么继承 BaseOutputParser**：
    LangChain 的 chain 语法 `prompt | llm | parser` 要求 parser 是 Runnable
    BaseOutputParser 是 Runnable 的子类，自带 `invoke` / `ainvoke`

    **使用示例**：
        from app.core.safe_json_parser import SafeJsonOutputParser
        chain = prompt | llm | SafeJsonOutputParser()
        result = await chain.ainvoke({"query": "..."})  # 直接返回 dict/list
    """

    def parse(self, text: str) -> Any:
        return safe_parse_json(text)

    @property
    def _type(self) -> str:
        return "safe_json"


class StripThinkStrParser(BaseOutputParser):
    """
    替代 LangChain 原生 `StrOutputParser`，剥掉 think 块 + 抓 ```sql``` 围栏

    **使用场景**：
    - generate_sql：拿到干净 SQL
    - correct_sql：拿到干净 SQL
    - rewrite_query：拿到干净的改写后 query（不夹 think 文本）
    - classify_intent：拿到干净的分类标签
    - respond_chitchat：拿到干净回复
    """

    def parse(self, text: str) -> str:
        return strip_think_for_str(text)

    @property
    def _type(self) -> str:
        return "strip_think_str"


# ─────────────────────────────────────────────────────────────────────
# 兼容旧调用方（2026-07-11 之前改的代码可能用这个函数名）
# ─────────────────────────────────────────────────────────────────────


def _build_strip_parser_runnable():
    """
    **兼容函数**：旧版改造时用过这个函数名（其实没存在过），
    现在补上以免 3 个节点跑起来 NameError。

    **用法**（在 chain 里）：
        chain = prompt | llm | _build_strip_parser_runnable()

    **替代方案**：直接用 `StripThinkStrParser()`，效果完全一致：
        chain = prompt | llm | StripThinkStrParser()

    详见顶部说明 + docs/notes/eval_e2e_think兼容改造-20260711.md
    """
    return StripThinkStrParser()
