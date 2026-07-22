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

        # SQL 相似度（用收紧后的 sqlglot AST 对比）
        sql_match_score = compute_sql_similarity(generated_sql, expected_sql)

        # Execution Match（BIRD-SQL 金标准）：跑 gold + generated 两条 SQL 比对结果集
        # 比 AST 相似度更准——同义 SQL（LEFT JOIN vs JOIN、子查询 vs JOIN）会被正确判等
        # 失败兜底：DB 不可用时 execution_match=None，passed 走 AST 阈值
        execution_match = None
        exec_detail = None
        try:
            async with dw_mysql_client_manager.session_factory() as dw_session:
                dw_repo = DWMySQLRepository(dw_session)
                gold_rows, _ = await dw_repo.run(expected_sql)
                gen_rows, _ = await dw_repo.run(generated_sql) if generated_sql else ([], False)
                execution_match = compare_results(gold_rows, gen_rows)
        except Exception as e:
            exec_detail = f"execution match 跳过: {e}"
            execution_match = None

        # passed 判定（优先级）：execution_match=True 直接过；否则走收紧后的 AST 阈值
        if execution_match is True:
            passed = True
        elif execution_match is False:
            # 两条 SQL 都跑通了但结果不同 → 确定错
            passed = False
        else:
            # execution_match=None（DB 不可用 / SQL 跑挂）→ fallback 到 AST
            passed = sql_match_score >= EVAL_CONFIG["sql_match_threshold"]

        return {
            "query": query,
            "difficulty": difficulty,
            "expected_sql": expected_sql,
            "generated_sql": generated_sql,
            "sql_match_score": sql_match_score,
            "execution_match": execution_match,
            "exec_detail": exec_detail,
            "elapsed_ms": elapsed_ms,
            "error": error,
            "passed": passed,
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
    计算 SQL 相似度（2026-07-22 收紧版）

    用 sqlglot 解析两 SQL，对比 AST 关键节点：
    - SELECT 的字段 + 聚合函数（权重 0.3）  ← 收紧：原来只比列名，现在 SUM/AVG/COUNT 也比
    - FROM + JOIN 的表（权重 0.3）
    - WHERE 条件的 列名 + 字面量值（权重 0.2）  ← 收紧：原来只比列名，现在 '华东'/'黄金' 等值也比
    - GROUP BY（权重 0.1）
    - ORDER BY（权重 0.1）

    改前问题（详见 docs/upgrade-changelog.md Phase 1）：
    - SELECT：SUM(amount) vs AVG(amount) 列名都是 amount，得分一样 → 假绿
    - WHERE：region='华东' vs region='华南' 列名都是 region_name，得分一样 → 假绿
    """
    try:
        from sqlglot import parse_one
        g_ast = parse_one(generated, dialect="mysql")
        e_ast = parse_one(expected, dialect="mysql")
    except Exception:
        return 0.0

    from sqlglot import expressions as exp
    score = 0.0

    # ── SELECT 字段 + 聚合函数（权重 0.3）──
    # 收紧：不只比列名，还比外层聚合函数（SUM/AVG/COUNT/MAX/MIN）
    def _select_signatures(ast):
        """每个 select 项 → (agg_func | None, column_name) 的签名集合"""
        sigs = set()
        for sel in (ast.selects or []):
            # SELECT AVG(x) AS y 被解析成 Alias(this=Avg(...))
            # 剥掉 Alias 取真正的表达式，再找聚合函数
            inner = sel.this if isinstance(sel, exp.Alias) else sel
            agg = None
            if isinstance(inner, exp.AggFunc):
                agg = type(inner).__name__.upper()  # SUM / AVG / COUNT / MAX / MIN
            else:
                # 内层可能还有嵌套聚合（罕见），兜底找一遍
                for node in inner.walk():
                    n = node[0]
                    if isinstance(n, exp.AggFunc):
                        agg = type(n).__name__.upper()
                        break
            # 找这一项里的列名（排除 alias 名）
            cols = [c.name for c in sel.find_all(exp.Column)]
            col_name = cols[0] if cols else "*"
            sigs.add((agg, col_name))
        return sigs

    g_select = _select_signatures(g_ast)
    e_select = _select_signatures(e_ast)
    if e_select:
        score += 0.3 * len(g_select & e_select) / len(e_select)
    elif not g_select:
        score += 0.3

    # ── FROM + JOIN 表（权重 0.3）──
    g_tables = {t.name for t in g_ast.find_all(exp.Table)}
    e_tables = {t.name for t in e_ast.find_all(exp.Table)}
    if e_tables:
        score += 0.3 * len(g_tables & e_tables) / len(e_tables)
    elif not g_tables:
        score += 0.3

    # ── WHERE 条件（权重 0.2）── 收紧：列名 + 字面量值都比
    def _where_signatures(ast):
        """抽 WHERE 子句里的 (column, literal_value) 对

        覆盖所有比较类条件（EQ/GTE/GT/LTE/LT/NEQ/In/Like/Between），
        不只 EQ——时间过滤（date_id >= xxx）和数值比较是最高频的 WHERE 形态。
        """
        where = ast.args.get("where")
        if where is None:
            return set()
        sigs = set()

        # 所有比较类（Binary 子类：EQ/GTE/GT/LTE/LT/NEQ/Like/...）
        for cond in where.find_all(exp.Binary):
            cols = [c.name for c in cond.find_all(exp.Column)]
            lits = [l.sql() for l in cond.find_all(exp.Literal)]
            if cols and lits:
                # 一对一绑定（简化：取第一个列 + 第一个字面量）
                sigs.add((cols[0], lits[0]))

        # IN 多值：col IN ('a','b') → 列名 + 排序后的值串
        for cond in where.find_all(exp.In):
            cols = [c.name for c in cond.find_all(exp.Column)]
            lits = [l.sql() for l in cond.find_all(exp.Literal)]
            if cols and lits:
                sigs.add((cols[0], "|".join(sorted(lits))))

        # BETWEEN：col BETWEEN a AND b → 列名 + "a~b"
        for cond in where.find_all(exp.Between):
            cols = [c.name for c in cond.find_all(exp.Column)]
            lits = [l.sql() for l in cond.find_all(exp.Literal)]
            if cols and len(lits) == 2:
                sigs.add((cols[0], f"{lits[0]}~{lits[1]}"))

        # IS NULL / IS NOT NULL：col IS NULL → 列名 + "NULL"
        for cond in where.find_all(exp.Is):
            cols = [c.name for c in cond.find_all(exp.Column)]
            for c in cols:
                sigs.add((c, "NULL"))

        return sigs

    g_where = _where_signatures(g_ast)
    e_where = _where_signatures(e_ast)
    if e_where:
        score += 0.2 * len(g_where & e_where) / len(e_where)
    else:
        # expected 没有 WHERE 字面量条件时，generated 也不该乱加
        score += 0.2 if not g_where else 0.1

    # ── GROUP BY（权重 0.1）──
    g_group = {g.sql() for g in g_ast.args.get("group", [])}
    e_group = {g.sql() for g in e_ast.args.get("group", [])}
    if e_group:
        score += 0.1 * len(g_group & e_group) / len(e_group)
    else:
        score += 0.1

    # ── ORDER BY（权重 0.1）──
    g_order = [o.sql() for o in g_ast.args.get("order", [])]
    e_order = [o.sql() for o in e_ast.args.get("order", [])]
    if e_order:
        score += 0.1 * len([o for o in g_order if o in e_order]) / len(e_order)
    else:
        score += 0.1

    return min(score, 1.0)


def compare_results(gold_rows: list[dict], gen_rows: list[dict]) -> bool:
    """比对两个结果集是否等价（execution match 的核心）

    BIRD-SQL 风格：两条 SQL 都跑一遍，比对结果集。
    策略（容错处理多行/单行/标量三种 case）：
    1. 行数不同直接 False（除非都是单行标量，走数值容差）
    2. 单行单列：标量比对，数值走 1% 容差（避免浮点精度误判）
    3. 多行：排序后逐行比对，每行的值集合需相等（不关心列顺序）

    Args:
        gold_rows: gold SQL 跑出来的结果（list[dict]）
        gen_rows: generated SQL 跑出来的结果（list[dict]）

    Returns:
        True = 结果集等价；False = 不等价
    """
    # 行数不同 → 不等价（除非两边都是空，空 == 空）
    if len(gold_rows) == 0 and len(gen_rows) == 0:
        return True
    if len(gold_rows) != len(gen_rows):
        return False

    # 单行单列（标量查询，如 SUM/AVG/COUNT）→ 走数值容差
    if len(gold_rows) == 1 and len(gen_rows) == 1:
        g = list(gold_rows[0].values())
        e = list(gen_rows[0].values())
        if len(g) == 1 and len(e) == 1:
            return _scalar_equal(g[0], e[0])

    # 多行：按值排序后逐行比对
    # 把每行转成 (列名排序后的值 tuple)，整体排序，再比对
    def _row_key(row: dict) -> tuple:
        return tuple(sorted((str(k), _to_str(v)) for k, v in row.items()))

    g_sorted = sorted(_row_key(r) for r in gold_rows)
    e_sorted = sorted(_row_key(r) for r in gen_rows)
    return g_sorted == e_sorted


def _to_str(v) -> str:
    """统一值转字符串（处理 Decimal/float/None）"""
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        # 数值统一格式化到 2 位小数，避免 100 vs 100.00 的字符串差异
        return f"{float(v):.2f}"
    return str(v)


def _scalar_equal(g, e) -> bool:
    """标量相等判断（数值 1% 容差，其他严格相等）"""
    # 都能转 float → 数值容差
    try:
        fg, fe = float(g), float(e)
        if abs(fg - fe) / max(abs(fe), 1) < 0.01:
            return True
        return False
    except (TypeError, ValueError):
        # 非数值 → 严格字符串相等
        return _to_str(g) == _to_str(e)


async def main():
    # 2026-07-22：支持 EVAL_LIMIT 环境变量控制跑多少条（快速验证/baseline 生成用）
    # 不设则全跑（59 条，约 8-10 分钟）
    import os
    limit = int(os.environ.get("EVAL_LIMIT", "0")) or len(TEST_CASES_E2E)
    cases = TEST_CASES_E2E[:limit]

    print(f"\n=== 端到端 SQL 生成评测 ===")
    print(f"评测集大小: {len(cases)} 条" + (f"（限制前 {limit} 条，全集 {len(TEST_CASES_E2E)}）" if limit < len(TEST_CASES_E2E) else ""))
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # graph 已经在 import 时拿到

    # 跑所有 case
    results = []
    for i, case in enumerate(cases):
        print(f"[{i+1}/{len(cases)}] {case['query'][:40]}...", end=" ")
        print(f"[{i+1}/{len(TEST_CASES_E2E)}] {case['query'][:40]}...", end=" ")
        result = await run_one_case(agent_graph, case)
        results.append(result)
        status = "✓" if result["passed"] else "✗"
        score = result["sql_match_score"]
        em = result.get("execution_match")
        em_tag = "" if em is None else (" [EXEC✓]" if em else " [EXEC✗]")
        print(f"{status} (sql_match={score:.2f}{em_tag}, {result['elapsed_ms']:.0f}ms)")

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

    # Execution match 覆盖率（看多少 case 真跑了 execution vs fallback 到 AST）
    exec_runs = sum(1 for r in results if r.get("execution_match") is not None)
    exec_passes = sum(1 for r in results if r.get("execution_match") is True)
    print(f"Execution match: {exec_runs}/{total} 跑了（{exec_passes} 通过）")
    if exec_runs < total:
        print(f"  ⚠ {total - exec_runs} 条 fallback 到 AST 阈值（可能 DB 不可用或 SQL 跑挂）")

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
