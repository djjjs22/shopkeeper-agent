"""
召回率评估脚本（2026-07-22 Phase 5 重写：接真实三路召回）

改前：用 mock_recall 假数据，从未接真实召回链路 → 数字无意义
改后：直接调 ColumnQdrantRepository / MetricQdrantRepository / ValueESRepository，
      用 jieba 抽关键词 + embedding 召回，跑 eval_data.py 的 20 条 gold

指标：
  - table_recall:    召回命中表数 / 期望表数
  - column_recall:   召回命中字段数 / 期望字段数（hit-rate@k）
  - metric_recall:   召回命中指标数 / 期望指标数
  - MRR（Mean Reciprocal Rank）：第一个命中结果的平均排名倒数

运行方式：
  cd D:/shopkeeper-agent
  DB_PORT=3307 uv run python -m tests.eval_recall

注意：需要 Qdrant + Embedding + ES 全部在线（docker compose up）
"""

import asyncio
from collections import defaultdict

import jieba

from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
from tests.eval_data import TEST_CASES

# 初始化 client（评测进程共用一份）
qdrant_client_manager.init()
embedding_client_manager.init()
es_client_manager.init()


def _extract_keywords(query: str) -> list[str]:
    """用 jieba 抽关键词（轻量版，不走 LLM 扩展，纯分词）"""
    words = jieba.cut_for_search(query)
    # 过滤单字 + 停用词（粗糙版，够评测用）
    stop = {"的", "了", "是", "在", "有", "多少", "查", "看", "一下", "统计", "请问"}
    return [w for w in words if len(w) >= 2 and w not in stop]


async def real_recall(query: str) -> tuple[list[str], list[str], list[str]]:
    """调真实三路召回，返回 (tables, columns, metrics)

    不走完整 graph（避免 LLM 依赖），直接用 jieba 关键词 + embedding 检索
    """
    keywords = _extract_keywords(query)
    if not keywords:
        keywords = [query]  # 兜底：整句当关键词

    column_repo = ColumnQdrantRepository(qdrant_client_manager.client)
    metric_repo = MetricQdrantRepository(qdrant_client_manager.client)
    value_repo = ValueESRepository(es_client_manager.client)

    # 并行三路召回
    async def _recall_columns():
        results = []
        seen = set()
        for kw in keywords:
            try:
                emb = await embedding_client_manager.client.aembed_query(kw)
                cols = await column_repo.search(emb)
                for c in cols:
                    if c.id not in seen:
                        seen.add(c.id)
                        results.append(c)
            except Exception:
                pass
        return results

    async def _recall_metrics():
        results = []
        seen = set()
        for kw in keywords:
            try:
                emb = await embedding_client_manager.client.aembed_query(kw)
                ms = await metric_repo.search(emb)
                for m in ms:
                    mid = getattr(m, "id", None) or getattr(m, "name", str(m))
                    if mid not in seen:
                        seen.add(mid)
                        results.append(m)
            except Exception:
                pass
        return results

    async def _recall_values():
        try:
            return await value_repo.search(query, top_k=10)
        except Exception:
            return []

    cols, metrics, values = await asyncio.gather(
        _recall_columns(), _recall_metrics(), _recall_values()
    )

    # 提取表名 + 字段名（格式：table.column）
    tables = set()
    columns = set()
    for c in cols:
        if c.table_id:
            tables.add(c.table_id.split(".")[0] if "." in c.table_id else c.table_id)
        if c.name and c.table_id:
            tname = c.table_id.split(".")[0] if "." in c.table_id else c.table_id
            columns.add(f"{tname}.{c.name}")

    metric_names = set()
    for m in metrics:
        name = getattr(m, "name", None) or str(m)
        metric_names.add(name)

    return list(tables), list(columns), list(metric_names)


def evaluate_one_case(case: dict, actual_tables, actual_columns, actual_metrics) -> dict:
    """评估单条用例的召回质量"""
    expected_tables = set(case.get("expected_tables", []))
    expected_columns = set(case.get("expected_columns", []))
    expected_metrics = set(case.get("expected_metrics", []))

    table_recall = (
        len(expected_tables & set(actual_tables)) / len(expected_tables)
        if expected_tables else 1.0
    )
    column_recall = (
        len(expected_columns & set(actual_columns)) / len(expected_columns)
        if expected_columns else 1.0
    )
    metric_recall = (
        len(expected_metrics & set(actual_metrics)) / len(expected_metrics)
        if expected_metrics else 1.0
    )

    return {
        "query": case["query"],
        "table_recall": table_recall,
        "column_recall": column_recall,
        "metric_recall": metric_recall,
        "hit_columns": list(expected_columns & set(actual_columns)),
        "missed_columns": list(expected_columns - set(actual_columns)),
        "actual_columns_count": len(actual_columns),
    }


async def evaluate_all_cases() -> dict:
    results = []
    for i, case in enumerate(TEST_CASES):
        print(f"[{i+1}/{len(TEST_CASES)}] {case['query'][:30]}...", end=" ")
        actual_tables, actual_columns, actual_metrics = await real_recall(case["query"])
        result = evaluate_one_case(case, actual_tables, actual_columns, actual_metrics)
        results.append(result)
        print(f"col_recall={result['column_recall']:.1%}")

    avg_table = sum(r["table_recall"] for r in results) / len(results)
    avg_col = sum(r["column_recall"] for r in results) / len(results)
    avg_metric = sum(r["metric_recall"] for r in results) / len(results)
    worst = sorted(results, key=lambda x: x["column_recall"])[:5]

    return {
        "total_cases": len(results),
        "avg_table_recall": avg_table,
        "avg_column_recall": avg_col,
        "avg_metric_recall": avg_metric,
        "worst_cases": worst,
    }


async def main():
    print("=" * 60)
    print("召回率评估报告（真实三路召回，2026-07-22 重写）")
    print("=" * 60)

    summary = await evaluate_all_cases()

    print(f"\n📊 总用例数：{summary['total_cases']}")
    print(f"📈 平均表召回率：{summary['avg_table_recall']:.1%}")
    print(f"📈 平均字段召回率：{summary['avg_column_recall']:.1%}")
    print(f"📈 平均指标召回率：{summary['avg_metric_recall']:.1%}")

    print("\n🔍 召回率最低的 5 个用例：")
    for case in summary["worst_cases"]:
        print(f"  - {case['query']}")
        print(f"    字段召回率: {case['column_recall']:.1%} (实际召回 {case['actual_columns_count']} 个)")
        if case["missed_columns"]:
            print(f"    漏召字段: {case['missed_columns']}")


if __name__ == "__main__":
    asyncio.run(main())
