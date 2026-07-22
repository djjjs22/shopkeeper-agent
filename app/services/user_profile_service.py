"""
用户偏好服务（Semantic Memory）

对应 docs/AI应用架构升级路线.md 第 4.3 B 节"User Profile"。

核心职责：
1. update：从用户 query 抽取偏好信号（连续 3 次带"按地区" → preferred_dim=region）
2. get_active_preferences：读取置信度 ≥ 阈值的偏好（供 generate_intent 注入）
3. decay：遗忘机制（置信度 < 0.3 删除，Phase 5 调用）

抽取策略（规则版，不调 LLM）：
   - 偏好维度检测：query 里出现"按地区/分大区/各省份"等 → preferred_dim=region
   - 常用术语检测："动销率/复购率/客单价"等 → common_term
   - 连续命中提升置信度：首次 0.5，连续 3 次 → 0.9
   - 矛盾更新：同 type 新值覆盖旧值，置信度重置

设计要点：
1. **fire-and-forget**：update 在 query_service 成功后异步调用，不阻塞响应
2. **保守注入**：只有 confidence ≥ 0.9 才注入 prompt（避免误判污染）
3. **可解释**：偏好都是规则抽出来的，调试时能看清"为什么记了 region"
"""

import re
from typing import Optional

from app.core.log import logger


# ─────────────────────────────────────────────────────────────────────
# 偏好抽取规则（规则版，不调 LLM——便宜、可解释、可控）
# ─────────────────────────────────────────────────────────────────────

# 维度偏好：query 出现这些词 → preferred_dim 对应值
_DIM_PATTERNS = [
    # region：按地区/分地区/各地区/各大区/分大区/地区分布/每个地区
    (re.compile(r"按地区|分地区|各地区|各大区|分大区|地区分布|每个地区|各省份?"), "region"),
    # category：按品类/分品类/各品类/各大类
    (re.compile(r"按(商品)?品类|分品类|各品类|各大类|各类商品"), "category"),
    # member_level：按会员等级/分等级/各等级
    (re.compile(r"按(会员)?等级|分等级|各等级会员|各会员等级"), "member_level"),
    # month：按月/月度/各月
    (re.compile(r"按月|月度|每月|各月"), "month"),
    # payment_method：按支付方式/各支付方式
    (re.compile(r"按支付方式|各支付方式"), "payment_method"),
]

# 常用术语：query 出现这些词 → common_term（业务黑话，召回时需特殊处理）
_TERM_PATTERNS = [
    (re.compile(r"动销率"), "动销率"),
    (re.compile(r"复购率"), "复购率"),
    (re.compile(r"客单价"), "客单价"),
    (re.compile(r"GDP|GMV|营业额|流水"), "GMV"),
]

# 置信度策略
_INITIAL_CONFIDENCE = 0.5  # 首次发现某偏好
_BOOST_PER_HIT = 0.2       # 每次连续命中提升
_MAX_CONFIDENCE = 0.95     # 上限（不设 1.0 留余地）
_INJECT_THRESHOLD = 0.9    # 注入 prompt 的阈值（保守，低于此只存不消费）
_DECAY_THRESHOLD = 0.3     # 遗忘阈值（Phase 5 memory_decay_service 调用）


def _detect_preferences(query: str) -> list[tuple[str, str]]:
    """从单条 query 抽取偏好信号

    Returns:
        [(preference_type, content), ...] 命中的偏好列表
    """
    hits = []
    for pattern, dim in _DIM_PATTERNS:
        if pattern.search(query):
            hits.append(("preferred_dim", dim))
            break  # 同一 query 只记一个维度（取第一个命中）
    for pattern, term in _TERM_PATTERNS:
        if pattern.search(query):
            hits.append(("common_term", term))
            break
    return hits


def _format_preferences_for_prompt(profiles: list) -> str:
    """把用户偏好格式化成 prompt 可读文本（供 generate_intent 注入）

    Args:
        profiles: UserProfile entity 列表（仅 confidence ≥ 阈值的）

    Returns:
        格式化文本；空则返回 "无"
    """
    if not profiles:
        return "无"
    parts = []
    for p in profiles:
        if p.preference_type == "preferred_dim":
            parts.append(f"默认按【{p.content}】维度分组")
        elif p.preference_type == "common_term":
            parts.append(f"常用术语【{p.content}】")
    return "; ".join(parts) if parts else "无"


class UserProfileService:
    """用户偏好服务（模块级单例 user_profile_service）"""

    def __init__(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    def enable(self) -> None:
        self._enabled = True

    async def update(self, user_id: str, query: str) -> None:
        """从 query 抽取偏好信号并更新（fire-and-forget）

        在 query_service 成功响应后调用：
            await user_profile_service.update(session_id, query)

        置信度策略：
        - 新偏好：写入 confidence=0.5
        - 已存在同 type 同 content：confidence += 0.2（上限 0.95）
        - 已存在同 type 不同 content：覆盖（新 content，confidence 重置 0.5）
        """
        if not self._enabled or not user_id or not query:
            return
        hits = _detect_preferences(query)
        if not hits:
            return
        try:
            from app.clients.mysql_client_manager import meta_mysql_client_manager
            from app.repositories.mysql.meta.meta_mysql_repository import (
                MetaMySQLRepository,
            )
            from app.repositories.mysql.meta.mappers.user_profile_mapper import (
                UserProfileMapper,
            )

            meta_mysql_client_manager.init()
            async with meta_mysql_client_manager.session_factory() as session:
                repo = MetaMySQLRepository(session)
                # 循环前读一次现有偏好，循环内用内存 dict 跟踪（避免每次 UPSERT 后
                # 重读受未 commit 事务可见性影响，导致置信度不累加）
                existing = await repo.get_user_profiles(user_id)
                current_state = {
                    p.preference_type: (p.content, p.confidence) for p in existing
                }
                for ptype, content in hits:
                    prev_content, prev_conf = current_state.get(ptype, (None, 0.0))
                    if prev_content == content:
                        # 同 type 同 content → 提升置信度
                        new_conf = min(prev_conf + _BOOST_PER_HIT, _MAX_CONFIDENCE)
                    else:
                        # 新偏好 或 同 type 不同 content → 重置置信度
                        new_conf = _INITIAL_CONFIDENCE
                    await repo.upsert_user_profile(user_id, ptype, content, new_conf)
                    # 同步内存状态（供后续 hit 看到最新值）
                    current_state[ptype] = (content, new_conf)
                await session.commit()
            logger.debug(
                f"[user_profile] update: user={user_id} hits={hits}"
            )
        except Exception as e:
            logger.warning(f"[user_profile] update 失败（不影响业务）: {e}")

    async def get_active_preferences(self, user_id: str) -> list:
        """读取置信度 ≥ 注入阈值的偏好（供 generate_intent 注入 prompt）

        保守策略：只有 confidence ≥ 0.9 才返回，避免低置信误判污染 prompt。
        """
        if not self._enabled or not user_id:
            return []
        try:
            from app.clients.mysql_client_manager import meta_mysql_client_manager
            from app.repositories.mysql.meta.meta_mysql_repository import (
                MetaMySQLRepository,
            )

            meta_mysql_client_manager.init()
            async with meta_mysql_client_manager.session_factory() as session:
                repo = MetaMySQLRepository(session)
                all_profiles = await repo.get_user_profiles(user_id)
                # 只返回高置信度偏好
                return [
                    p for p in all_profiles if p.confidence >= _INJECT_THRESHOLD
                ]
        except Exception as e:
            logger.warning(f"[user_profile] get_active 失败（走空）: {e}")
            return []

    async def decay(self) -> int:
        """遗忘机制：删除置信度 < 0.3 的偏好（Phase 5 scheduler 调用）

        Returns:
            删除条数
        """
        try:
            from app.clients.mysql_client_manager import meta_mysql_client_manager
            from app.repositories.mysql.meta.meta_mysql_repository import (
                MetaMySQLRepository,
            )

            meta_mysql_client_manager.init()
            async with meta_mysql_client_manager.session_factory() as session:
                repo = MetaMySQLRepository(session)
                n = await repo.delete_low_confidence_profiles(_DECAY_THRESHOLD)
                await session.commit()
            if n > 0:
                logger.info(f"[user_profile] decay 删除 {n} 条低置信偏好")
            return n
        except Exception as e:
            logger.warning(f"[user_profile] decay 失败: {e}")
            return 0


# 模块级单例
user_profile_service = UserProfileService()
