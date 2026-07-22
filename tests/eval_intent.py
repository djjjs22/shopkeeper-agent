"""
意图分类准确率评测脚本（2026-07-22 Phase 5 新建）

测 classify_intent 节点的分类准确率：
  - chitchat: 闲聊（"你好"、"谢谢"）
  - metadata_query: 元数据查询（"有哪些表"、"商品品类有哪些"）
  - data_query: 数据查询（"华东销售额"、"各品类销量"）

指标：
  - accuracy: 分类正确率
  - per_class_f1: 每类 precision / recall / F1
  - confusion_matrix: 错分分布（哪些类最容易混）

运行方式：
  cd D:/shopkeeper-agent
  DB_PORT=3307 uv run python -m tests.eval_intent

注意：需要 LLM_API_KEY（classify_intent 用 cheap profile）
"""

import asyncio
from collections import defaultdict

from app.agent.llm import get_llm
from app.core.safe_json_parser import _build_strip_parser_runnable
from langchain_core.prompts import PromptTemplate
from app.prompt.prompt_loader import load_prompt


# 意图分类评测集（200 条规模的目标先做 60 条覆盖 3 类）
# 每类 20 条，含正例 + 易混反例
INTENT_TEST_CASES = [
    # ── chitchat（20 条）──
    ("你好", "chitchat"),
    ("hello", "chitchat"),
    ("谢谢你", "chitchat"),
    ("你是谁", "chitchat"),
    ("你能做什么", "chitchat"),
    ("今天天气怎么样", "chitchat"),
    ("帮我个忙", "chitchat"),
    ("在吗", "chitchat"),
    ("嗨", "chitchat"),
    ("拜拜", "chitchat"),
    ("好的谢谢", "chitchat"),
    ("明白了", "chitchat"),
    ("请问你叫什么名字", "chitchat"),
    ("你是机器人吗", "chitchat"),
    ("辛苦了", "chitchat"),
    ("早安", "chitchat"),
    ("测试一下", "chitchat"),
    ("随便聊聊", "chitchat"),
    ("今天周几", "chitchat"),
    ("中午吃什么", "chitchat"),

    # ── metadata_query（20 条）──
    ("有哪些表", "metadata_query"),
    ("数据库里有什么数据", "metadata_query"),
    ("商品都有哪些品类", "metadata_query"),
    ("有哪些地区", "metadata_query"),
    ("有哪些支付方式", "metadata_query"),
    ("会员等级有哪些", "metadata_query"),
    ("表结构是什么样的", "metadata_query"),
    ("dim_order 表有哪些字段", "metadata_query"),
    ("有多少个维度表", "metadata_query"),
    ("给我看看所有表", "metadata_query"),
    ("有哪些指标", "metadata_query"),
    ("GMV 是什么意思", "metadata_query"),
    ("客单价怎么定义的", "metadata_query"),
    ("字段都有哪些", "metadata_query"),
    ("商品表长什么样", "metadata_query"),
    ("有哪些枚举值", "metadata_query"),
    ("告诉我表清单", "metadata_query"),
    ("数据字典", "metadata_query"),
    ("有哪些维度", "metadata_query"),
    ("日期表有哪些字段", "metadata_query"),

    # ── 边界 case：问指标定义 vs 查指标数值（最容易误判）──
    ("什么是动销率", "metadata_query"),
    ("动销率是什么意思", "metadata_query"),
    ("复购率怎么算的", "metadata_query"),
    ("GMV 是什么", "metadata_query"),

    # ── data_query（20 条）──
    ("华东销售额", "data_query"),
    ("各品类的销量", "data_query"),
    ("黄金会员的客单价", "data_query"),
    ("上个月的订单数", "data_query"),
    ("华北地区销售额排名", "data_query"),
    ("手机品类的 GMV", "data_query"),
    ("微信支付的订单总额", "data_query"),
    ("各地区的客户数量", "data_query"),
    ("最近 7 天的销量", "data_query"),
    ("黄金会员在各地区的分布", "data_query"),
    ("今年 Q1 的总销售额", "data_query"),
    ("客单价最高的 3 个地区", "data_query"),
    ("月度 GMV 趋势", "data_query"),
    ("用户复购率", "data_query"),
    ("商品动销率", "data_query"),
    ("动销率是多少", "data_query"),
    ("各支付方式的使用次数", "data_query"),
    ("统计每个会员等级的下单频次", "data_query"),
    ("2024 年全年 GMV", "data_query"),
    ("上周的日均订单数", "data_query"),
]


async def classify_one(query: str) -> str:
    """调 classify_intent 的底层 chain（不构造 runtime）"""
    llm = get_llm("classify_intent")
    prompt = PromptTemplate(
        template=load_prompt("classify_intent"),
        template_format="jinja2",
        input_variables=["query"],
    )
    chain = prompt | llm | _build_strip_parser_runnable()
    result = await chain.ainvoke({"query": query})
    intent = result.strip().lower() if isinstance(result, str) else str(result).strip().lower()

    # 与节点一致的兜底逻辑
    valid = ("chitchat", "metadata_query", "data_query")
    if intent not in valid:
        intent = "data_query"
    return intent


async def main():
    print("=" * 60)
    print("意图分类准确率评测（2026-07-22 Phase 5）")
    print("=" * 60)
    print(f"评测集: {len(INTENT_TEST_CASES)} 条 (chitchat/metadata_query/data_query 各 20)")

    results = []
    for i, (query, expected) in enumerate(INTENT_TEST_CASES):
        actual = await classify_one(query)
        correct = actual == expected
        results.append({"query": query, "expected": expected, "actual": actual, "correct": correct})
        mark = "✓" if correct else "✗"
        if not correct:
            print(f"[{i+1}] {mark} \"{query}\" 期望={expected} 实际={actual}")

    # 汇总
    total = len(results)
    correct_count = sum(1 for r in results if r["correct"])
    accuracy = correct_count / total

    print(f"\n=== 评测结果 ===")
    print(f"总准确率: {correct_count}/{total} = {accuracy:.1%}")

    # 按类别统计
    by_class = defaultdict(lambda: {"total": 0, "correct": 0, "pred_as": defaultdict(int)})
    for r in results:
        by_class[r["expected"]]["total"] += 1
        if r["correct"]:
            by_class[r["expected"]]["correct"] += 1
        by_class[r["expected"]]["pred_as"][r["actual"]] += 1

    print(f"\n按类别:")
    for cls in ("chitchat", "metadata_query", "data_query"):
        s = by_class[cls]
        acc = s["correct"] / s["total"] if s["total"] else 0
        pred_dist = dict(s["pred_as"])
        print(f"  {cls}: {s['correct']}/{s['total']} = {acc:.1%}  预测分布={pred_dist}")

    # 混淆矩阵（错分分布）
    wrong = [r for r in results if not r["correct"]]
    if wrong:
        print(f"\n=== 错分 case ({len(wrong)} 条) ===")
        confusion = defaultdict(int)
        for r in wrong:
            confusion[(r["expected"], r["actual"])] += 1
        for (exp, act), n in sorted(confusion.items()):
            print(f"  {exp} → {act}: {n} 次")


if __name__ == "__main__":
    asyncio.run(main())
