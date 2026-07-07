# -*- coding: utf-8 -*-
"""
会话历史 + 当前问题的 Prompt 拼接器 - L3 层

负责把历史对话 + 当前问题 拼成一个结构化的 Prompt，
让 LLM 能理解"如果用户在追问，前面聊的是什么"。
"""

import json
# json = Python 内置的 JSON 序列化模块



def format_history(history: list) -> str:
    """
    把历史消息列表转成易读的字符串


    输入: [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
    输出: "用户: 上一轮问的问题\n助手: 上一轮的回复\n用户: 更上一轮..."

    要求：
    - 遍历 history 列表
    - 把 role 中文映射（user→"用户"、assistant→"助手"）
    - 用 \n 连接成一段字符串
    """
    lines = []
    for mes in history:
        role_cn = "用户" if mes["role"] == "user" else "助手"
        lines.append(f"{role_cn}:{mes['content']}")
    # 第五步：用换行符连起来
    return "\n".join(lines)


def is_followup_query(query: str) -> bool:
    # 追问关键词
    followup_words = ["那", "那个", "再", "还有", "上", "刚才", "然后", "呢"]

    # 判断 1：包含追问词
    for word in followup_words:
        if word in query:
            return True

    # 判断 2：短句
    if len(query) < 15:
        return True

    # 默认不是追问
    return False


def build_prompt(query: str, history: list) -> str:
    # 第 1 步：格式化历史
    history_text = format_history(history)

    # 第 2 步：判断追问
    if is_followup_query(query):
        task_hint = "这是一个追问，请结合历史对话理解用户真正想查询的内容。"
    else:
        task_hint = "这是一个新问题。"

    # 第 3 步：拼成完整 Prompt
    prompt = f"""
【对话历史】
{history_text}

【当前问题】
{query}

【任务类型】
{task_hint}
"""
    return prompt
