"""
Pattern Bank 构建脚本

从 gold_dataset（tests/eval_e2e_data.py 的 TEST_CASES_E2E）全量构建 SQL 模板库。
每条 case 的 (query, expected_sql) 抽成模板，双写 MySQL + Qdrant。

运行方式：
  cd D:/shopkeeper-agent
  uv run python -m app.scripts.build_pattern_bank

设计：
  - gold 来源 confidence=1.0（最高，召回时优先注入）
  - 幂等：同 query 的 pattern_id 是 hash(query)，重复跑会 UPSERT 不重复写
  - 跑完后可看 MySQL sql_pattern 表统计

后续：
  - 周期性从 query_log 增量 ingest（pattern_bank_service.ingest_from_query_log）
  - 由 scheduler 每天 04:00 触发（Phase 5）
"""

import asyncio
import sys

from app.core.log import logger


async def build_from_gold() -> int:
    """从 TEST_CASES_E2E 全量构建 Pattern Bank

    Returns:
        成功 ingest 的条数
    """
    # 1. 初始化所有依赖 client（embedding + qdrant + mysql）
    from app.clients.embedding_client_manager import embedding_client_manager
    from app.clients.qdrant_client_manager import qdrant_client_manager
    from app.clients.mysql_client_manager import meta_mysql_client_manager

    embedding_client_manager.init()
    qdrant_client_manager.init()
    meta_mysql_client_manager.init()

    # 2. 确保 Qdrant collection 存在
    from app.repositories.qdrant.pattern_qdrant_repository import (
        PatternQdrantRepository,
    )

    pattern_repo = PatternQdrantRepository(qdrant_client_manager.client)
    await pattern_repo.ensure_collection()

    # 3. 读 gold_dataset
    #    用 importlib 避免触发 eval_e2e.py 的 client_manager init（会重复初始化）
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "eval_e2e_data",
        Path(__file__).parents[2] / "tests" / "eval_e2e_data.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    test_cases = mod.TEST_CASES_E2E

    logger.info(f"[build_pattern_bank] gold_dataset 共 {len(test_cases)} 条")

    # 4. 逐条 ingest
    from app.services.pattern_bank_service import pattern_bank_service

    count = 0
    for i, case in enumerate(test_cases):
        query = case.get("query", "")
        sql = case.get("expected_sql", "")
        if not query or not sql:
            continue
        # 多轮 case 的 query 是最后一条 user message，直接用
        pid = await pattern_bank_service.ingest_one(
            query_intent_text=query,
            sql=sql,
            source="gold",
            confidence=1.0,
        )
        if pid:
            count += 1
        if (i + 1) % 10 == 0:
            logger.info(f"[build_pattern_bank] 进度 {i+1}/{len(test_cases)}")

    # 5. 关闭 client
    await qdrant_client_manager.close()
    await embedding_client_manager.close()
    await meta_mysql_client_manager.close()

    return count


async def main_async() -> int:
    n = await build_from_gold()
    print(f"\n✓ Pattern Bank 构建完成：{n} 条 gold 模板入库")
    print("  - MySQL sql_pattern 表（存模板全文 + 元数据）")
    print("  - Qdrant sql_pattern_collection（存 query 向量）")
    print("\n下一步：generate_intent 节点会自动召回 top-3 注入 prompt")
    return 0


def main():
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
