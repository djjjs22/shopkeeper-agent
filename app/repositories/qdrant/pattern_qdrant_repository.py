"""
SQL 模板向量仓储（Procedural Memory）

管理 sql_pattern 向量集合，把"历史成功 SQL 抽成的模板"写入 Qdrant 供语义召回。

与 column/metric qdrant repository 同构：
   - ensure_collection: 按 app_config.qdrant.embedding_size 建 collection
   - upsert: 批量写 (id, embedding, payload)
   - search: 按向量相似度召回 top-k 模板

point id 设计：
   Qdrant 只接受无符号整数或 UUID，不接受任意字符串。这里用 pattern_id（MySQL 主键，
   形如 p_xxx）确定性生成 UUID（uuid.uuid5），保证：
   - 幂等：同 pattern_id 反复 upsert 不会产生重复 point
   - MySQL id 与 Qdrant id 双向可推（payload 里也存 pattern_id 做反查）

payload 结构：
   {
     "pattern_id": "p_xxx",        # MySQL sql_pattern.id（去 MySQL 取完整模板）
     "query_intent_text": "华东销售额",  # 原句（调试用）
     "source": "gold",             # gold / online
     "confidence": 1.0,
     "tags": ["join", "time_filter"]
   }
"""

import uuid

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import PointStruct
from qdrant_client.models import Distance, VectorParams

from app.conf.app_config import app_config

# uuid5 的命名空间（固定值，保证同 pattern_id 跨进程生成相同 UUID）
_PATTERN_NAMESPACE = uuid.UUID("a3f5c2e1-0000-0000-0000-000000000001")


def _to_uuid(pattern_id: str) -> str:
    """pattern_id → 确定性 UUID 字符串（幂等）"""
    return str(uuid.uuid5(_PATTERN_NAMESPACE, pattern_id))


class PatternQdrantRepository:
    """负责 SQL 模板向量集合的创建、写入和检索"""

    collection_name = "sql_pattern_collection"

    def __init__(self, client: AsyncQdrantClient):
        self.client = client

    async def ensure_collection(self):
        """确保模板向量集合存在，按配置维度初始化"""
        if not await self.client.collection_exists(self.collection_name):
            await self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=app_config.qdrant.embedding_size, distance=Distance.COSINE
                ),
            )

    async def upsert(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        payloads: list[dict],
        batch_size: int = 20,
    ):
        """分批 upsert 模板向量点

        ids 是 pattern_id（MySQL 主键，形如 p_xxx），内部转成确定性 UUID 给 Qdrant。
        """
        points: list[PointStruct] = [
            PointStruct(id=_to_uuid(_id), vector=embedding, payload=payload)
            for _id, embedding, payload in zip(ids, embeddings, payloads)
        ]
        for i in range(0, len(points), batch_size):
            await self.client.upsert(
                collection_name=self.collection_name, points=points[i : i + batch_size]
            )

    async def search(
        self, embedding: list[float], score_threshold: float = 0.5, limit: int = 5
    ) -> list[dict]:
        """按向量相似度检索模板，返回 payload 列表（含 pattern_id 供 service 去 MySQL 取全文）

        score_threshold 默认 0.5（比 column/metric 的 0.6 宽松）——
        SQL 意图相似度判断比字段名匹配更模糊，阈值太高会漏召回。
        """
        await self.ensure_collection()
        result = await self.client.query_points(
            collection_name=self.collection_name,
            query=embedding,
            limit=limit,
            score_threshold=score_threshold,
        )
        return [point.payload for point in result.points]

    async def delete_all(self):
        """重建索引前清空（drop collection 让 ensure_collection 重建）"""
        if await self.client.collection_exists(self.collection_name):
            await self.client.delete_collection(collection_name=self.collection_name)
