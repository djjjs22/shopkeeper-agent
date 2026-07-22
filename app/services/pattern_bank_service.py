"""
SQL Pattern Bank 服务（Procedural Memory 核心）

对应 docs/AI应用架构升级路线.md 第 4.3 A 节"SQL Pattern Bank ⭐⭐⭐ 最高 ROI"。

核心职责：
1. ingest_from_gold: 读 gold_dataset，跑通的 case 抽模板入库（source=gold, confidence=1.0）
2. ingest_from_query_log: 扫 query_log where success=1，抽模板入库（source=online, confidence=0.5）
3. retrieve_topk: 用 query embedding 召回 top-k 模板，按 confidence × hit_count 排序
4. _extract_template: 把具体值替换成占位符（'华东' → '<region_value>'，20260601 → '<date>')

双写策略：
   - MySQL sql_pattern 表：存模板全文 + 元数据 + tags（可查询、可 UPSERT）
   - Qdrant sql_pattern_collection：存 query_intent_text 的向量（语义召回）

召回消费：
   generate_intent 节点调用 retrieve_topk → 把 top-3 模板格式化成 few-shot 注入 prompt
"""

import hashlib
import re
from typing import Optional

from app.core.log import logger


# ─────────────────────────────────────────────────────────────────────
# 模板抽取：把具体值替换成占位符，让模板可复用
# ─────────────────────────────────────────────────────────────────────

# 字符串字面量：'华东' / "黄金" → <value>
_STR_LIT_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
# 日期数字：20260601 / 202606（8 位或 6 位连续数字）→ <date>
_DATE_RE = re.compile(r"\b(20\d{6}|20\d{4})\b")
# 纯数字（非日期）：1000000 / 3 → <number>（注意先替换日期再替换数字）
_NUM_RE = re.compile(r"\b\d+\b")


def _extract_template(sql: str) -> str:
    """把 SQL 里的具体值抽象成占位符，让模板可跨 query 复用

    替换顺序很重要：先字符串 → 再日期 → 再纯数字（避免日期被数字规则切碎）

    例：
        SELECT SUM(order_amount) FROM fact_order
        WHERE region_name = '华东' AND date_id >= 20260601
        →
        SELECT SUM(order_amount) FROM fact_order
        WHERE region_name = '<value>' AND date_id >= <date>
    """
    if not sql:
        return ""
    s = _STR_LIT_RE.sub("'<value>'", sql)
    s = _DATE_RE.sub("<date>", s)
    s = _NUM_RE.sub("<number>", s)
    return s


def _extract_tags(sql: str) -> list[str]:
    """从 SQL 形态抽标签（用于按形态过滤召回结果）

    标签集（小写，与 sql_pattern.tags 字段对齐）：
        join / left_join / group_by / having / order_by / limit /
        subquery / window / union / like / in / not_in / is_null /
        between / case_when / distinct
    """
    s = sql.lower()
    tags = []
    if re.search(r"\bjoin\b", s):
        tags.append("join")
    if "left join" in s:
        tags.append("left_join")
    if "group by" in s:
        tags.append("group_by")
    if "having" in s:
        tags.append("having")
    if "order by" in s:
        tags.append("order_by")
    if "limit" in s:
        tags.append("limit")
    if s.count("select") >= 2 or "from (" in s:
        tags.append("subquery")
    if "over (" in s or "over(" in s:
        tags.append("window")
    if "union" in s:
        tags.append("union")
    if "like" in s:
        tags.append("like")
    if re.search(r"\bnot\s+in\b", s):
        tags.append("not_in")
    elif re.search(r"\bin\s*\(", s):
        tags.append("in")
    if "is null" in s or "is not null" in s:
        tags.append("is_null")
    if "between" in s:
        tags.append("between")
    if "case when" in s:
        tags.append("case_when")
    if "distinct" in s:
        tags.append("distinct")
    return tags


def _pattern_id(query_intent_text: str) -> str:
    """生成稳定的 pattern id（hash(query_intent_text)，截断 32 位）"""
    return "p_" + hashlib.md5(query_intent_text.encode("utf-8")).hexdigest()[:32]


# ─────────────────────────────────────────────────────────────────────
# 服务主体
# ─────────────────────────────────────────────────────────────────────


class PatternBankService:
    """SQL Pattern Bank 服务（模块级单例 pattern_bank_service）"""

    def __init__(self) -> None:
        self._enabled = True
        # 召回 top-k（注入 generate_intent prompt 的模板数）
        self._retrieve_topk = 3
        # 召回分数阈值（低于此分的模板不注入，避免噪声）
        self._score_threshold = 0.5

    def disable(self) -> None:
        self._enabled = False

    def enable(self) -> None:
        self._enabled = True

    # ─────────────────────────────────────────────────────────────────
    # Ingest（写入）
    # ─────────────────────────────────────────────────────────────────

    async def ingest_one(
        self,
        query_intent_text: str,
        sql: str,
        source: str = "online",
        confidence: Optional[float] = None,
    ) -> Optional[str]:
        """抽模板 + 双写 MySQL + Qdrant，返回 pattern_id

        Args:
            query_intent_text: 用户原句或意图文本（用于 embedding）
            sql: 成功执行的 SQL（会被抽成模板）
            source: gold / online
            confidence: 显式置信度；None 时按 source 默认（gold=1.0, online=0.5）

        Returns:
            pattern_id；失败返回 None
        """
        if not self._enabled or not query_intent_text or not sql:
            return None

        if confidence is None:
            confidence = 1.0 if source == "gold" else 0.5

        from app.entities.sql_pattern import SqlPattern

        pattern_id = _pattern_id(query_intent_text)
        template = _extract_template(sql)
        tags = _extract_tags(sql)

        pattern = SqlPattern(
            id=pattern_id,
            query_intent_text=query_intent_text,
            sql_template=template,
            source=source,
            confidence=confidence,
            hit_count=0,
            tags=tags,
        )

        # 双写：MySQL（UPSERT）+ Qdrant（upsert 向量）
        try:
            await self._upsert_mysql(pattern)
            await self._upsert_qdrant(pattern)
            logger.info(
                f"[pattern_bank] ingest 成功: id={pattern_id} source={source} "
                f"tags={tags} template={template[:60]}..."
            )
            return pattern_id
        except Exception as e:
            logger.warning(f"[pattern_bank] ingest 失败（不影响业务）: {e}")
            return None

    async def ingest_from_query_log(self, limit: int = 100) -> int:
        """扫 query_log where success=1，批量抽模板入库

        供 scheduler 周期调用（如每天 04:00）。返回成功 ingest 的条数。
        """
        if not self._enabled:
            return 0
        try:
            from app.clients.mysql_client_manager import meta_mysql_client_manager
            from app.repositories.mysql.meta.meta_mysql_repository import (
                MetaMySQLRepository,
            )

            meta_mysql_client_manager.init()
            async with meta_mysql_client_manager.session_factory() as session:
                repo = MetaMySQLRepository(session)
                logs = await repo.get_recent_successful_query_logs(limit=limit)

            # query_log 的 sql 字段目前为空（Phase 2 设计），有 sql 才 ingest
            count = 0
            for log in logs:
                if log.sql and log.sql.strip():
                    pid = await self.ingest_one(
                        query_intent_text=log.query,
                        sql=log.sql,
                        source="online",
                    )
                    if pid:
                        count += 1
            logger.info(f"[pattern_bank] 从 query_log ingest {count}/{len(logs)} 条")
            return count
        except Exception as e:
            logger.warning(f"[pattern_bank] ingest_from_query_log 失败: {e}")
            return 0

    # ─────────────────────────────────────────────────────────────────
    # Retrieve（召回 + 注入）
    # ─────────────────────────────────────────────────────────────────

    async def retrieve_topk(self, query: str) -> list[dict]:
        """召回 top-k 模板（供 generate_intent 注入 prompt）

        流程：
        1. query → embedding（复用 embedding_client_manager）
        2. Qdrant 召回 payload 列表（含 pattern_id）
        3. 去 MySQL 取完整 sql_template
        4. 按 confidence 排序（gold 优先）

        Returns:
            [{"query_intent_text": ..., "sql_template": ..., "confidence": ...}, ...]
            失败/无命中返回空列表（generate_intent 走原流程）
        """
        if not self._enabled or not query:
            return []
        try:
            from app.clients.embedding_client_manager import (
                embedding_client_manager,
            )
            from app.clients.qdrant_client_manager import qdrant_client_manager
            from app.repositories.qdrant.pattern_qdrant_repository import (
                PatternQdrantRepository,
            )

            # lazy init：脚本/测试场景下 client 可能未初始化（生产 lifespan 已 init）
            if embedding_client_manager.client is None:
                embedding_client_manager.init()
            if qdrant_client_manager.client is None:
                qdrant_client_manager.init()

            # 1. query → embedding
            # 注意：用 aembed_query（异步版），不能用 embed_query（同步版会 asyncio.run 崩）
            embedding = await embedding_client_manager.client.aembed_query(query)

            # 2. Qdrant 召回
            repo = PatternQdrantRepository(qdrant_client_manager.client)
            payloads = await repo.search(
                embedding=embedding,
                score_threshold=self._score_threshold,
                limit=self._retrieve_topk,
            )
            if not payloads:
                return []

            # 3. 去 MySQL 取完整模板
            from app.clients.mysql_client_manager import meta_mysql_client_manager
            from app.repositories.mysql.meta.meta_mysql_repository import (
                MetaMySQLRepository,
            )

            meta_mysql_client_manager.init()
            async with meta_mysql_client_manager.session_factory() as session:
                meta_repo = MetaMySQLRepository(session)
                results = []
                for p in payloads:
                    pid = p.get("pattern_id")
                    if not pid:
                        continue
                    pattern = await meta_repo.get_sql_pattern_by_id(pid)
                    if pattern:
                        results.append(
                            {
                                "query_intent_text": pattern.query_intent_text,
                                "sql_template": pattern.sql_template,
                                "confidence": pattern.confidence,
                                "source": pattern.source,
                                "tags": pattern.tags,
                            }
                        )

            # 4. 按 confidence 降序（gold 优先）
            results.sort(key=lambda x: x["confidence"], reverse=True)
            return results
        except Exception as e:
            logger.warning(f"[pattern_bank] retrieve 失败（走原流程）: {e}")
            return []

    # ─────────────────────────────────────────────────────────────────
    # 内部：双写
    # ─────────────────────────────────────────────────────────────────

    async def _upsert_mysql(self, pattern) -> None:
        from app.clients.mysql_client_manager import meta_mysql_client_manager
        from app.repositories.mysql.meta.meta_mysql_repository import (
            MetaMySQLRepository,
        )

        meta_mysql_client_manager.init()
        async with meta_mysql_client_manager.session_factory() as session:
            repo = MetaMySQLRepository(session)
            await repo.upsert_sql_pattern(pattern)
            await session.commit()

    async def _upsert_qdrant(self, pattern) -> None:
        from app.clients.embedding_client_manager import (
            embedding_client_manager,
        )
        from app.clients.qdrant_client_manager import qdrant_client_manager
        from app.repositories.qdrant.pattern_qdrant_repository import (
            PatternQdrantRepository,
        )

        # query_intent_text → embedding
        # 用 aembed_query（异步版），避免在 async 上下文调同步 embed_query 触发 asyncio.run 崩
        embedding = await embedding_client_manager.client.aembed_query(
            pattern.query_intent_text
        )

        repo = PatternQdrantRepository(qdrant_client_manager.client)
        payload = {
            "pattern_id": pattern.id,
            "query_intent_text": pattern.query_intent_text,
            "source": pattern.source,
            "confidence": pattern.confidence,
            "tags": pattern.tags,
        }
        await repo.upsert(
            ids=[pattern.id],
            embeddings=[embedding],
            payloads=[payload],
        )


# 模块级单例
pattern_bank_service = PatternBankService()
