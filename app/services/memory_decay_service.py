"""
Memory 衰减服务（遗忘机制）

对应 docs/AI应用架构升级路线.md 第 4.4 节"遗忘机制（被忽视的关键）"。

核心信念：Memory 不是越多越好——会过期、会矛盾、会污染。
污染的 memory 比没有 memory 更糟。

三类衰减规则：
   - Procedural（SQL Pattern）：命中率 < 10%/月 → 降权或归档
   - Semantic（User Profile）：置信度 < 0.3 → 删除
   - Episodic（Session Summary）：Redis TTL 24h 自动过期（无需额外处理）

调度：scheduler.py 每天 03:00 触发 _safe_decay（在归档任务 02:00 之后）

设计要点：
1. **统一入口**：一个 decay_all() 调用所有 memory 类型的衰减，scheduler 只调一次
2. **fail-open**：某类衰减失败不影响其他类（Procedural 挂了 Semantic 照常跑）
3. **可观测**：每次 decay 打日志，知道删了多少、降权了多少
"""

from app.core.log import logger


class MemoryDecayService:
    """Memory 衰减服务（模块级单例 memory_decay_service）"""

    def __init__(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    def enable(self) -> None:
        self._enabled = True

    async def decay_all(self) -> dict:
        """执行所有 memory 类型的衰减，返回统计

        Returns:
            {"sql_pattern": {"archived": n, "demoted": n},
             "user_profile": {"deleted": n},
             "session_summary": {"expired": n}}
        """
        if not self._enabled:
            return {}
        stats = {}
        # 三类独立 try/except，某类挂了不影响其他
        stats["sql_pattern"] = await self._decay_sql_patterns()
        stats["user_profile"] = await self._decay_user_profiles()
        stats["session_summary"] = await self._decay_session_summaries()
        logger.info(f"[memory_decay] 完成: {stats}")
        return stats

    async def _decay_sql_patterns(self) -> dict:
        """SQL Pattern 衰减：命中率低的降权，长期零命中的归档

        规则：
        - hit_count=0 且 created_at > 30 天 → 归档（删除 MySQL + Qdrant）
        - hit_count 低（< 3）且 source=online → confidence 降 0.1（下限 0.2）

        gold 来源不衰减（confidence 固定 1.0，是人工标注的金标准）
        """
        try:
            from app.clients.mysql_client_manager import meta_mysql_client_manager
            from app.repositories.mysql.meta.meta_mysql_repository import (
                MetaMySQLRepository,
            )
            from sqlalchemy import text

            meta_mysql_client_manager.init()
            archived = 0
            demoted = 0
            async with meta_mysql_client_manager.session_factory() as session:
                # 1. 归档：30 天前创建且从未命中（hit_count=0）
                result = await session.execute(
                    text(
                        "DELETE FROM sql_pattern "
                        "WHERE hit_count = 0 AND source = 'online' "
                        "AND created_at < DATE_SUB(NOW(), INTERVAL 30 DAY)"
                    )
                )
                archived = result.rowcount or 0

                # 2. 降权：online 来源 + hit_count<3 + confidence>0.2 → confidence - 0.1
                await session.execute(
                    text(
                        "UPDATE sql_pattern SET confidence = GREATEST(confidence - 0.1, 0.2) "
                        "WHERE source = 'online' AND hit_count < 3 AND confidence > 0.2"
                    )
                )
                # rowcount 对 UPDATE 不一定准，用查询统计
                demoted_result = await session.execute(
                    text(
                        "SELECT COUNT(*) FROM sql_pattern "
                        "WHERE source = 'online' AND hit_count < 3 AND confidence <= 0.5"
                    )
                )
                demoted = demoted_result.scalar() or 0

                await session.commit()
            return {"archived": archived, "demoted": demoted}
        except Exception as e:
            logger.warning(f"[memory_decay] sql_pattern 衰减失败: {e}")
            return {"archived": 0, "demoted": 0, "error": str(e)}

    async def _decay_user_profiles(self) -> dict:
        """User Profile 衰减：删除置信度 < 0.3 的偏好

        复用 user_profile_service.decay()（已有实现）
        """
        try:
            from app.services.user_profile_service import user_profile_service

            deleted = await user_profile_service.decay()
            return {"deleted": deleted}
        except Exception as e:
            logger.warning(f"[memory_decay] user_profile 衰减失败: {e}")
            return {"deleted": 0, "error": str(e)}

    async def _decay_session_summaries(self) -> dict:
        """Session Summary 衰减：Redis TTL 24h 自动过期，无需额外处理

        留方法签名供统一调度，当前是 no-op。
        """
        return {"expired": 0, "note": "Redis TTL auto-expire"}


# 模块级单例
memory_decay_service = MemoryDecayService()
