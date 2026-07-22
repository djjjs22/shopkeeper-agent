"""
会话摘要服务（Episodic Memory）

对应 docs/AI应用架构升级路线.md 第 4.3 C 节"会话摘要"。

核心职责：
当会话历史超过 N 轮时，用 cheap LLM 把前面的对话压缩成一句摘要，
替换原始历史，避免 token 爆炸。

设计要点：
1. **触发阈值**：历史超过 _SUMMARIZE_THRESHOLD（5 轮 = 10 条消息）才触发
2. **保留近期**：摘要替换"早期历史"，但保留最近 _KEEP_RECENT（4 条）原文
   （近期对话指代最密集，压缩会丢失上下文）
3. **cheap LLM**：摘要任务简单，用 cheap profile 省 token（~1s）
4. **fail-open**：摘要失败只 warning，保留原始历史（不丢数据）
5. **摘要标记**：摘要后的首条消息 role=system，content 带 [摘要] 前缀，
   让下游节点知道这是压缩过的

例：
   原始 12 条消息（6 轮）：
     user: 查华东销售额 / assistant: 12345
     user: 华南呢 / assistant: 67890
     user: 按品类分 / assistant: ...
     user: 手机品类 / assistant: ...
     user: 上个月 / assistant: ...
     user: 黄金会员 / assistant: ...

   摘要后（保留最近 4 条 + 1 条摘要）：
     system: [摘要] 用户先查了华东/华南销售额，然后按品类维度看手机品类，
             关注上个月数据和黄金会员群体。
     user: 上个月 / assistant: ...
     user: 黄金会员 / assistant: ...
"""

from app.core.log import logger


_SUMMARIZE_THRESHOLD = 10  # 消息数超过此值触发摘要（10 条 = 5 轮）
_KEEP_RECENT = 4           # 摘要时保留最近 N 条原文（近期指代密集，不压缩）
_SUMMARY_PREFIX = "[摘要] "  # 摘要消息前缀，让下游识别

_SUMMARIZE_PROMPT = """请把下面的多轮对话压缩成一句话摘要，保留：
1. 用户查询过的主要指标（销售额/销量/客单价等）
2. 用户关注的维度（地区/品类/会员等级/时间等）
3. 用户追加的筛选条件

只输出摘要文本，不要解释，不要列表，控制在 50 字以内。

对话内容：
{history}

摘要："""


class SessionSummarizer:
    """会话摘要服务（模块级单例 session_summarizer）"""

    def __init__(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    def enable(self) -> None:
        self._enabled = True

    def should_summarize(self, history: list[dict]) -> bool:
        """判断当前历史是否需要摘要"""
        return self._enabled and len(history) > _SUMMARIZE_THRESHOLD

    async def summarize_if_needed(self, session_id: str) -> bool:
        """如果历史超长，执行摘要替换（在 add_message 后调用）

        流程：
        1. 读当前历史
        2. 不超阈值 → 跳过
        3. 超阈值 → 拆成"待摘要部分 + 近期保留部分"
        4. cheap LLM 摘要"待摘要部分"
        5. 清空历史，写入 [摘要] + 近期保留部分

        Returns:
            是否执行了摘要
        """
        if not self._enabled:
            return False
        try:
            from app.services.session_store import get_history, clear_history, add_message

            history = await get_history(session_id, max_count=20)
            if not self.should_summarize(history):
                return False

            # 拆分：早期部分待摘要，近期部分保留
            to_summarize = history[:-_KEEP_RECENT] if len(history) > _KEEP_RECENT else []
            recent = history[-_KEEP_RECENT:] if len(history) > _KEEP_RECENT else history
            if not to_summarize:
                return False

            # 格式化待摘要部分成文本
            history_text = "\n".join(
                f"{m['role']}: {m['content']}" for m in to_summarize
            )

            # 调 cheap LLM 摘要
            summary = await self._call_llm_summarize(history_text)
            if not summary:
                return False  # LLM 失败，保留原始历史

            # 替换：清空 + 写入摘要 + 近期保留
            await clear_history(session_id)
            await add_message(session_id, "system", _SUMMARY_PREFIX + summary)
            for m in recent:
                await add_message(session_id, m["role"], m["content"])

            logger.info(
                f"[session_summarizer] 摘要完成: session={session_id} "
                f"{len(history)} 条 → 1 摘要 + {len(recent)} 近期"
            )
            return True
        except Exception as e:
            logger.warning(f"[session_summarizer] 摘要失败（保留原始历史）: {e}")
            return False

    async def _call_llm_summarize(self, history_text: str) -> str:
        """调 cheap LLM 做摘要，失败返回空串"""
        try:
            from app.agent.llm import get_llm

            # 用 cheap profile（摘要任务简单，省 token）
            llm = get_llm("summarizer")
            prompt = _SUMMARIZE_PROMPT.format(history=history_text[:2000])  # 截断防超长
            result = await llm.ainvoke(prompt)
            if hasattr(result, "content"):
                result = result.content
            return str(result).strip()
        except Exception as e:
            logger.warning(f"[session_summarizer] LLM 摘要调用失败: {e}")
            return ""

    async def expire_old_summaries(self) -> int:
        """遗忘机制占位（Phase 5 实现：30 天未引用的摘要删除）

        当前 session_store 用 Redis TTL 24h 自动过期，无需额外处理。
        留方法签名供 memory_decay_service 统一调用。
        """
        return 0


# 模块级单例
session_summarizer = SessionSummarizer()
