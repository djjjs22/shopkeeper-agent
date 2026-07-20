"""
Embedding 客户端管理器

负责按配置初始化 Embedding 服务客户端，并为字段、指标和用户问题的向量化
提供统一访问入口
"""

import asyncio
from typing import List, Optional

import aiohttp
from langchain_core.embeddings import Embeddings

from app.conf.app_config import EmbeddingConfig, app_config


class TEIEmbeddings(Embeddings):
    """自定义 TEI (Text Embeddings Inference) 客户端，通过 HTTP 调用自托管 Embedding 服务

    2026-07-20 优化：长生命周期 aiohttp.ClientSession
    原实现每次调用都 `async with aiohttp.ClientSession()`，一次问数里 5 个关键词
    就是 10 次建连 + 10 次 TCP 握手。改后 session 在 init 时建一次，复用到 close。
    aiohttp 官方推荐：不要为每次请求新建 ClientSession。
    """

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._embed_url = f"{self.base_url}/embed"
        # 长生命周期 session，由 EmbeddingClientManager 在 lifespan 里 init/close
        self._session: Optional[aiohttp.ClientSession] = None

    def _ensure_session(self) -> aiohttp.ClientSession:
        """惰性创建 session（首次调用时建，后续复用）"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                # 适度放宽连接池上限，避免高并发下排队
                connector=aiohttp.TCPConnector(limit=32),
            )
        return self._session

    async def aclose(self):
        """释放底层 aiohttp session（lifespan 关闭时调用）"""
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """同步批量向量化（封装异步调用）"""
        return asyncio.run(self.aembed_documents(texts))

    def embed_query(self, text: str) -> List[List[float]]:
        """同步单条向量化（封装异步调用）"""
        return asyncio.run(self.aembed_query(text))

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        """异步批量向量化（一次请求处理多条文本，TEI 原生支持）"""
        session = self._ensure_session()
        async with session.post(
            self._embed_url,
            json={"inputs": texts},
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def aembed_query(self, text: str) -> List[List[float]]:
        """异步单条向量化"""
        embeddings = await self.aembed_documents([text])
        return embeddings[0]


class EmbeddingClientManager:
    """管理 Embedding 服务客户端的初始化与复用"""

    def __init__(self, config: EmbeddingConfig):
        self.client: Optional[TEIEmbeddings] = None
        self.config = config

    def _get_url(self) -> str:
        """拼接 Embedding 服务地址"""
        return f"http://{self.config.host}:{self.config.port}"

    def init(self):
        """显式初始化客户端，避免模块导入时立即建立外部连接"""
        self.client = TEIEmbeddings(base_url=self._get_url())

    async def close(self):
        """释放底层 aiohttp session（lifespan 关闭时调用）"""
        if self.client is not None:
            await self.client.aclose()


# 模块级单例，供整个项目复用同一套 Embedding 客户端管理器
embedding_client_manager = EmbeddingClientManager(app_config.embedding)


if __name__ == "__main__":
    embedding_client_manager.init()
    client = embedding_client_manager.client

    async def test():
        """执行一次最小化向量化调用，验证服务是否可用"""
        text = "What is deep learning?"
        query_result = await client.aembed_query(text)
        print(query_result[:3])

    asyncio.run(test())
