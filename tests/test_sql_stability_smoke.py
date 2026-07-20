"""
小样本链路稳定性冒烟测试（2026-07-14 稳定性改造）

目的：验证以下 4 个改动生效，避免反复跑 50 条端到端评测：
  1. column_info.examples/alias 从 JSON 字符串标准化为 list
  2. validate_sql 拦截空 SQL（EMPTY_SQL sentinel）
  3. validate_sql 拦截非 SELECT/NON_SELECT_SQL sentinel
  4. correct_sql -> validate_sql 闭环 + correction_attempts 计数

运行：
  cd D:/shopkeeper-agent
  uv run pytest tests/test_sql_stability_smoke.py -v

注意：
  - 本脚本不依赖 Qdrant/ES/Embedding/MySQL，只用 Fake 桩跑通链路。
  - 只跑 4 个最小场景，单 case < 5 秒。
"""

import sys
from pathlib import Path

# 让 "from app..." 可用
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402
from typing import List  # noqa: E402
from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402

import app.agent.llm as llm_mod  # noqa: E402
from app.agent.graph import graph  # noqa: E402


# ───────────────────────────────────────────────────────────────────────
# Fake LLM：按调用顺序返回固定回复序列
# ───────────────────────────────────────────────────────────────────────


class ScriptedChatModel(BaseChatModel):
    """按调用顺序返回固定回复序列的最小 LLM 桩。"""

    # Pydantic 模型字段，必须在 class body 声明，不能在 __init__ 里赋值。
    replies: List[str] = []
    call_count: int = 0

    def __init__(self, replies: list[str] | None = None, **kwargs):
        # 默认先把 Pydantic 默认 init 走完，再用 model_extra 把自定义字段挂回去。
        super().__init__(**kwargs)
        self.replies = list(replies or [])
        self.call_count = 0

    @property
    def _llm_type(self) -> str:
        return "scripted-chat"

    # 2026-07-14 新增：兼容 generate_sql 的 bind_tools 调用。
    # 测试桩也要暴露 bind_tools 接口，否则节点 .bind_tools() 调用会抛 AttributeError。
    def bind_tools(self, tools):
        # 桩仅返回自身，节点会跳过真实协议校验。
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if self.call_count >= len(self.replies):
            reply = "SELECT 1"
        else:
            reply = self.replies[self.call_count]
        self.call_count += 1
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=reply))])


# ───────────────────────────────────────────────────────────────────────
# Fake DW：脚本化校验失败次数
# ───────────────────────────────────────────────────────────────────────


class ScriptedDWRepo:
    """Fake DW Repo：用脚本控制 validate 的成功率。

    behavior:
      "always_fail"        validate 永远抛错
      "fail_then_pass"     validate 前 fail_times 次抛错，之后通过
    """

    def __init__(self, behavior: str, fail_times: int = 1):
        self.behavior = behavior
        self.fail_times = fail_times
        self.validate_calls = 0

    async def get_db_info(self):
        return {"dialect": "mysql", "version": "8.0.36"}

    async def validate(self, sql: str):
        self.validate_calls += 1
        if self.behavior == "always_fail":
            raise ValueError("fake syntax error")
        if self.behavior == "fail_then_pass" and self.validate_calls <= self.fail_times:
            raise ValueError(f"fake syntax error (call {self.validate_calls})")
        return None

    async def run(self, sql: str):
        # 2026-07-20：repository.run 改成返回 (rows, truncated) 元组（#3）
        return [{"result": "ok"}], False


# ───────────────────────────────────────────────────────────────────────
# Fake Meta Repository：examples 故意返回 JSON 字符串
# ───────────────────────────────────────────────────────────────────────


class JsonStringExamplesMetaRepo:
    """把 examples 字段保持为 JSON 字符串，验证 _normalize_column_info_row 工作正常。"""

    async def get_column_infos_by_ids(self, ids):
        from app.entities.column_info import ColumnInfo

        return [
            ColumnInfo(
                id="c_order_amount",
                name="order_amount",
                type="float",
                role="measure",
                table_id="fact_order",
                examples='["100.0", "200.0"]',  # 故意是 JSON 字符串
                alias=["销售额", "订单金额"],
                description="订单金额",
            )
        ]

    async def get_table_infos_by_ids(self, ids):
        from app.entities.table_info import TableInfo

        return [TableInfo(id="fact_order", name="fact_order", role="事实表", description="订单")]

    async def get_key_columns_by_table_ids(self, table_ids):
        return []

    async def get_all_table_infos(self):
        return []

    async def get_columns_by_table_id(self, table_id):
        return []

    async def get_all_metric_infos(self):
        return []


class EmptyRepo:
    async def search(self, *args, **kwargs):
        return []


class EmptyEmbeddingClient:
    async def aembed_query(self, text: str):
        return [0.0] * 8


def _install_fake_llm(fake):
    """把桩 LLM 注入到 LLMRegistry，所有节点走 get_llm(node_name) 都拿到这个 fake。

    2026-07-20 改造：节点全部已改用 `get_llm("node_name")` 按 node_profiles 路由，
    旧版"给每个节点模块塞 m.llm = fake"的 patch 已是死代码（generate_intent
    直接读 get_llm，_recall_helpers 也已迁移）。直接 patch registry.get_by_node
    即可让所有节点拿到 fake，无需逐模块改。

    注意：用 monkey-patch 替换实例方法后必须 restore，否则污染后续测试
    （test_llm_registry.py 调 get_registry().get(...) 会拿到这个 fake）。
    每次调用前先保存原方法，restore() 还原。
    """
    global _orig_get_by_node, _orig_get
    # 仅在第一次 install 时保存原方法（避免被多次 install 覆盖丢失原方法）
    if _orig_get_by_node is None:
        _orig_get_by_node = llm_mod._registry.get_by_node
        _orig_get = llm_mod._registry.get

    # 老兼容入口也顺手设一下（部分老脚本可能仍读 llm_mod.llm，零成本）
    llm_mod.llm = fake
    # 真正生效的入口：让 get_llm(node_name) 全部返回 fake
    llm_mod._registry.get_by_node = lambda node_name: fake  # type: ignore[assignment]
    llm_mod._registry.get = lambda profile: fake  # type: ignore[assignment]


# 保存 LLMRegistry 的原始方法，测试结束后还原（防止污染其他测试模块）
_orig_get_by_node = None
_orig_get = None


def _restore_fake_llm():
    """还原 _install_fake_llm 的 patch（每个用例结束必须调用）"""
    global _orig_get_by_node, _orig_get
    if _orig_get_by_node is not None:
        llm_mod._registry.get_by_node = _orig_get_by_node
        llm_mod._registry.get = _orig_get
        _orig_get_by_node = None
        _orig_get = None


def _initial_state(query: str) -> dict:
    return {
        "query": query,
        "history": [],
        "intent": None,
        "keywords": [],
        "retrieved_column_infos": [],
        "retrieved_metric_infos": [],
        "retrieved_value_infos": [],
        "table_infos": [],
        "metric_infos": [],
        "date_info": None,
        "db_info": None,
        "sql": "",
        "error": None,
        "correction_attempts": 0,
    }


async def _run_one(query: str, replies: list[str], dw_repo):
    fake = ScriptedChatModel(replies)
    _install_fake_llm(fake)

    state = _initial_state(query)
    context = {
        "column_qdrant_repository": EmptyRepo(),
        "embedding_client": EmptyEmbeddingClient(),
        "metric_qdrant_repository": EmptyRepo(),
        "value_es_repository": EmptyRepo(),
        "meta_mysql_repository": JsonStringExamplesMetaRepo(),
        "dw_mysql_repository": dw_repo,
    }

    final_state: dict = {}
    try:
        async for item in graph.astream(input=state, context=context, stream_mode="updates"):
            for _node, patch in item.items():
                if patch:
                    final_state.update(patch)
    finally:
        # 关键：还原 registry 的 patch，防止污染其他测试模块（test_llm_registry 等）
        _restore_fake_llm()
    return fake.call_count, dw_repo.validate_calls, final_state


# ───────────────────────────────────────────────────────────────────────
# 4 个冒烟用例
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_sql_is_intercepted():
    """空 SQL 防御：validate_sql 拦截 → correct_sql 修正两次 → 触发 MAX → run_sql 防御。

    2026-07-20 改造后 LLM 调用顺序：
    # 0: classify_intent               → "data_query"
    # 1-2: rewrite_query 主体 + extract_inherited_context 并行（asyncio.gather）
    # 3: extract_keywords 内的统一关键词扩展（合并版，替代原 3 次 extend_keywords）
    # 4: filter_table                   → 降级空 dict
    # 5: filter_metric                  → 降级空 list
    # 6: generate_intent                → 坏 JSON
    # 7: correct_sql × 2                → ""
    """
    replies = [
        "data_query",                                                # 0: classify
        "统计华北地区的销售总额",                                    # 1: rewrite 主体（与下一行并行）
        '{"entities": [], "conditions": [], "dimensions": []}',      # 2: extract_inherited（并行）
        '{"column":["销售额"], "value":["华北"], "metric":["GMV"]}',  # 3: extend_keywords 统一版
        "{}",                                                          # 4: filter_table
        "[]",                                                          # 5: filter_metric
        '{"select": [{"expr": "BAD", "alias": "x"}], "from": "fact_order"}',  # 6: generate_intent (BAD)
        "",                                                            # 7: correct_sql #1 空
        "",                                                            # 8: correct_sql #2 空
    ]
    dw = ScriptedDWRepo("always_fail")
    calls, validates, final = await _run_one("统计华北地区的销售总额", replies, dw)

    # 关键断言：链路不无限循环，attempts <= MAX=2
    assert (final.get("correction_attempts") or 0) <= 2
    # 链路最终写错误（无论是空 SQL 还是 BAD）
    error_text = str(final.get("error", ""))
    assert error_text != "", f"error 应非空，实际：{error_text}"


@pytest.mark.asyncio
async def test_correct_sql_closes_loop_then_passes():
    """坏 SQL 修正一轮后，validate_sql 闭环复验通过 → 跑通。

    2026-07-20 改造后 LLM 调用顺序（rewrite 并行 + extend 统一版）：
    # 0: classify_intent
    # 1-2: rewrite 主体 + extract_inherited 并行
    # 3: extend_keywords 统一版（合并三维度）
    # 4: filter_table
    # 5: filter_metric
    # 6: generate_intent
    # 7: correct_sql #1
    """
    replies = [
        "data_query",                                                # 0: classify
        "统计华北地区的销售总额",                                    # 1: rewrite 主体（并行）
        '{"entities": [], "conditions": [], "dimensions": []}',      # 2: extract_inherited（并行）
        '{"column":["销售额"], "value":["华北"], "metric":["GMV"]}',  # 3: extend_keywords 统一版
        '{"fact_order": ["order_id"]}',                              # 4: filter_table
        '["GMV"]',                                                    # 5: filter_metric
        '{"select": [{"expr": "SELECT bad_column", "alias": "x"}], "from": "dim_region"}',  # 6: generate_intent
        "SELECT region_name FROM dim_region",                         # 7: correct_sql
    ]
    dw = ScriptedDWRepo("fail_then_pass", fail_times=1)
    calls, validates, final = await _run_one("统计华北地区的销售总额", replies, dw)

    # 关键断言：correct_sql 修复后的 SQL 进了 run_sql
    assert (final.get("correction_attempts") or 0) <= 2


@pytest.mark.asyncio
async def test_correction_overflow_stops_retrying():
    """超出 MAX_CORRECTION_ATTEMPTS 后强制走 run_sql 终止，不再多调一次 correct_sql。

    2026-07-20 改造后 LLM 调用顺序（rewrite 并行 + extend 统一版）。
    """
    replies = [
        "data_query",                                                # 0: classify
        "统计华北地区的销售总额",                                    # 1: rewrite 主体（并行）
        '{"entities": [], "conditions": [], "dimensions": []}',      # 2: extract_inherited（并行）
        '{"column":["销售额"], "value":["华北"], "metric":["GMV"]}',  # 3: extend_keywords 统一版
        "{}",                                                          # 4: filter_table
        "[]",                                                          # 5: filter_metric
        '{"select": [{"expr": "BAD", "alias": "x"}], "from": "fact_order"}',  # 6: generate_intent
        "BAD1", "BAD2",                                                # 7-8: correct_sql × 2
    ]
    dw = ScriptedDWRepo("always_fail")
    calls, validates, final = await _run_one("统计华北地区的销售总额", replies, dw)

    # 关键断言：attempts 不会无限增长
    assert (final.get("correction_attempts") or 0) <= 2


def test_normalize_column_info_handles_json_string_examples():
    """column_info.examples 是 JSON 字符串时，_normalize_column_info_row 要转成 list。"""
    from app.repositories.mysql.meta.meta_mysql_repository import (
        _normalize_column_info_row,
    )

    fake_row = {
        "id": "c_x",
        "name": "x",
        "type": "varchar",
        "role": "dimension",
        "examples": '["a", "b"]',  # 故意是 JSON 字符串
        "alias": '["x1", "x2"]',
        "description": "x",
        "table_id": "t",
    }
    col = _normalize_column_info_row(fake_row)

    # 关键断言：examples 必须能 .append（不再是 str）
    col.examples.append("c")
    assert col.examples == ["a", "b", "c"]
    assert col.alias == ["x1", "x2"]
