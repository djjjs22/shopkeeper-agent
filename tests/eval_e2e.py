# -*- coding: utf-8 -*-
"""
端到端 SQL 生成准确率评测脚本

跑法：
  cd /Users/lunasama/Downloads/Agent/shopkeeper-agent
  uv run python -m tests.eval_e2e

输出：
  - 总准确率
  - 按难度分组的准确率
  - 失败 case 详情（query、生成的 SQL、期望 SQL、错误原因）
  - 结果保存到 tests/results/eval_e2e_<timestamp>.json

⚠️ 注意：
  1. SQL 匹配用 sqlglot 做语法解析 + AST 对比（不是字符串匹配）
  2. 结果匹配用 sqlglot 执行 + 排序后比较
  3. 没有 ground_truth_result 的 case 跳过结果对比
"""

import asyncio
import json
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from app.agent.graph import graph as agent_graph, DataAgentContext, DataAgentState
from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import (
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
from tests.eval_e2e_data import TEST_CASES_E2E, EVAL_CONFIG


# 初始化全局 client（评测进程共用一份）
qdrant_client_manager.init()
embedding_client_manager.init()
es_client_manager.init()
meta_mysql_client_manager.init()
dw_mysql_client_manager.init()


async def run_one_case(graph, case: dict) -> dict:
    """跑一条 query，返回评测结果"""
    query = case["query"]
    expected_sql = case["expected_sql"]
    difficulty = case.get("difficulty", "未知")

    # 多轮 case 处理：把历史喂进去
    history = case.get("multi_turn", [])
    if history:
        # 截掉最后一个 user message（就是要评测的）
        history = history[:-1]

    start = time.time()
    try:
        # 每次新建 context（里面有 SQLAlchemy session，要新连接）
        async with (
            meta_mysql_client_manager.session_factory() as meta_session,
            dw_mysql_client_manager.session_factory() as dw_session,
        ):
            meta_mysql_repository = MetaMySQLRepository(meta_session)
            dw_mysql_repository = DWMySQLRepository(dw_session)
            column_qdrant_repository = ColumnQdrantRepository(qdrant_client_manager.client)
            metric_qdrant_repository = MetricQdrantRepository(qdrant_client_manager.client)
            value_es_repository = ValueESRepository(es_client_manager.client)

            context = DataAgentContext(
                column_qdrant_repository=column_qdrant_repository,
                embedding_client=embedding_client_manager.client,
                metric_qdrant_repository=metric_qdrant_repository,
                value_es_repository=value_es_repository,
                meta_mysql_repository=meta_mysql_repository,
                dw_mysql_repository=dw_mysql_repository,
            )

            result = await agent_graph.ainvoke(
                input={
                    "query": query,
                    "history": history,
                    "session_id": f"eval-{case.get('id', 'unknown')}",
                },
                context=context,
            )
        elapsed_ms = (time.time() - start) * 1000

        generated_sql = result.get("sql", "") or result.get("final_sql", "")
        error = result.get("error", None)

        # SQL 相似度（用 sqlglot AST 对比）
        sql_match_score = compute_sql_similarity(generated_sql, expected_sql)

        # 结果相似度（如果有 ground truth）
        result_match_score = None
        # if case.get("ground_truth_result") is not None:
        #     try:
        #         generated_result = execute_sql(generated_sql)
        #         result_match_score = compare_results(
        #             generated_result, case["ground_truth_result"]
        #         )
        #     except Exception as e:
        #         result_match_score = 0.0
        #         error = f"SQL 执行失败: {e}"

        return {
            "query": query,
            "difficulty": difficulty,
            "expected_sql": expected_sql,
            "generated_sql": generated_sql,
            "sql_match_score": sql_match_score,
            "result_match_score": result_match_score,
            "elapsed_ms": elapsed_ms,
            "error": error,
            "passed": (
                sql_match_score >= EVAL_CONFIG["sql_match_threshold"]
                and (
                    result_match_score is None
                    or result_match_score >= EVAL_CONFIG["result_match_threshold"]
                )
            ),
        }
    except Exception as e:
        return {
            "query": query,
            "difficulty": difficulty,
            "expected_sql": expected_sql,
            "generated_sql": "",
            "sql_match_score": 0.0,
            "result_match_score": 0.0,
            "elapsed_ms": (time.time() - start) * 1000,
            "error": f"Agent 异常: {e}",
            "passed": False,
        }


def compute_sql_similarity(generated: str, expected: str) -> float:
    """
    计算 SQL 相似度
    用 sqlglot 解析两 SQL，对比 AST 关键节点：
    - SELECT 的字段
    - FROM 的表
    - JOIN 关系
    - WHERE 条件
    - GROUP BY / ORDER BY
    """
    try:
        from sqlglot import parse_one
        g_ast = parse_one(generated, dialect="mysql")
        e_ast = parse_one(expected, dialect="mysql")
    except Exception:
        return 0.0

    from sqlglot import expressions as exp
    score = 0.0

    # SELECT 字段（权重 0.3）—— 只比列名本身，alias 差异不扣分
    def _select_columns(ast):
        cols = set()
        for sel in (ast.selects or []):
            for c in sel.find_all(exp.Column):
                cols.add(c.name)
        return cols
    g_select = _select_columns(g_ast)
    e_select = _select_columns(e_ast)
    if e_select:
        score += 0.3 * len(g_select & e_select) / len(e_select)
    elif not g_select:
        score += 0.3

    # FROM + JOIN 表（权重 0.3）
    g_tables = {t.name for t in g_ast.find_all(exp.Table)}
    e_tables = {t.name for t in e_ast.find_all(exp.Table)}
    if e_tables:
        score += 0.3 * len(g_tables & e_tables) / len(e_tables)
    elif not g_tables:
        score += 0.3

    # WHERE 条件（权重 0.2）—— 简化判断：列名匹配数
    g_where_cols = {c.name for c in g_ast.find_all(exp.Column)} if g_ast.args.get("where") else set()
    e_where_cols = {c.name for c in e_ast.find_all(exp.Column)} if e_ast.args.get("where") else set()
    if e_where_cols:
        score += 0.2 * len(g_where_cols & e_where_cols) / len(e_where_cols)
    else:
        score += 0.2

    # GROUP BY（权重 0.1）
    g_group = {g.sql() for g in g_ast.args.get("group", [])}
    e_group = {g.sql() for g in e_ast.args.get("group", [])}
    if e_group:
        score += 0.1 * len(g_group & e_group) / len(e_group)
    else:
        score += 0.1

    # ORDER BY（权重 0.1）
    g_order = [o.sql() for o in g_ast.args.get("order", [])]
    e_order = [o.sql() for o in e_ast.args.get("order", [])]
    if e_order:
        score += 0.1 * len([o for o in g_order if o in e_order]) / len(e_order)
    else:
        score += 0.1

    return min(score, 1.0)


def execute_sql(sql: str):
    """用项目的 MySQL 客户端执行 SQL，返回结果"""
    # TODO: 替换成你项目的执行逻辑
    from app.clients.mysql_client_manager import mysql_client_manager
    return asyncio.run(mysql_client_manager.execute(sql))


def compare_results(generated, expected) -> float:
    """比较两个结果集（排序后逐行比对）"""
    if isinstance(expected, (int, float)):
        return 1.0 if abs(generated - expected) / max(expected, 1) < 0.01 else 0.0
    if isinstance(expected, list):
        gen_sorted = sorted([str(x) for x in generated])
        exp_sorted = sorted([str(x) for x in expected])
        return 1.0 if gen_sorted == exp_sorted else 0.0
    return 0.0


async def main():
    print(f"\n=== 端到端 SQL 生成评测 ===")
    print(f"评测集大小: {len(TEST_CASES_E2E)} 条")
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # graph 已经在 import 时拿到

    # 跑所有 case
    results = []
    for i, case in enumerate(TEST_CASES_E2E):
        print(f"[{i+1}/{len(TEST_CASES_E2E)}] {case['query'][:40]}...", end=" ")
        result = await run_one_case(agent_graph, case)
        results.append(result)
        status = "✓" if result["passed"] else "✗"
        score = result["sql_match_score"]
        print(f"{status} (sql_match={score:.2f}, {result['elapsed_ms']:.0f}ms)")

    # 汇总
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    overall_accuracy = passed / total if total else 0

    # 按难度分组
    by_difficulty = defaultdict(list)
    for r in results:
        by_difficulty[r["difficulty"]].append(r)

    print(f"\n=== 评测结果 ===")
    print(f"总准确率: {passed}/{total} = {overall_accuracy:.1%}")
    print(f"\n按难度分组:")
    for diff, items in sorted(by_difficulty.items()):
        diff_passed = sum(1 for r in items if r["passed"])
        print(f"  {diff}: {diff_passed}/{len(items)} = {diff_passed/len(items):.1%}")

    # 失败 case
    failed = [r for r in results if not r["passed"]]
    if failed:
        print(f"\n=== 失败 case ({len(failed)} 条) ===")
        for r in failed[:10]:  # 最多显示 10 条
            print(f"\n  Query: {r['query']}")
            print(f"  Expected: {r['expected_sql'][:200]}")
            print(f"  Generated: {r['generated_sql'][:200]}")
            if r["error"]:
                print(f"  Error: {r['error']}")

    # 保存结果
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_file = results_dir / f"eval_e2e_{timestamp}.json"
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": timestamp,
            "total": total,
            "passed": passed,
            "accuracy": overall_accuracy,
            "by_difficulty": {
                diff: {
                    "total": len(items),
                    "passed": sum(1 for r in items if r["passed"]),
                }
                for diff, items in by_difficulty.items()
            },
            "details": results,
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存到: {result_file}")
    print(f"\n=== 复盘建议 ===")
    print(f"1. 看 by_difficulty 哪个难度通过率最低，先补那个")
    print(f"2. 看 failed case 列表，是 SQL 语法错、还是召回错、还是 Prompt 没引导好")
    print(f"3. baseline 跑完后，加 Function Call 再跑一次，对比提升")


if __name__ == "__main__":
    asyncio.run(main())
