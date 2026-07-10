# -*- coding: utf-8 -*-
"""
端到端链路测试（刀 20）

目标：
  不依赖 Qdrant / Elasticsearch / Embedding / MySQL 等任何真实服务，
  用 FakeChatModel + 内存版仓储跑通整条 LangGraph 链路，覆盖三条意图路径：

    1. data_query    —— "统计华北地区的销售总额"
       期望：classify → rewrite → recall(3路) → merge → filter(2路)
            → add_extra_context → generate_sql → validate_sql → run_sql
       断言：intent=data_query、SQL 含 SELECT/华北、result 含"销售总额"
    2. chitchat      —— "你好"
       期望：classify → respond_chitchat → END（不进 RAG 链路）
       断言：intent=chitchat、有"回复"、未生成 SQL
    3. metadata_query —— "有哪些表"
       期望：classify → respond_metadata → END
       断言：intent=metadata_query、返回表列表非空

设计要点：
  - FakeChatModel 按各节点 prompt 的「唯一标识词」识别调用方，返回结构化输出
  - 真实节点里 `from app.agent.llm import llm` 在模块级绑定了 llm，
    因此测试在运行前把 fake 同时打到 llm 模块和各 node 模块的命名空间
  - 仓储全部用内存实现，仅返回与电商 schema 一致的样例实体

运行：
  cd D:/shopkeeper-agent
  uv run pytest tests/test_e2e_graph.py -v
"""

import sys
from pathlib import Path

# 把项目根目录加入 sys.path（与 conftest 一致，保证 from app... 可用）
sys.path.insert(0, str(Path(__file__).parent.parent))

import json  # noqa: E402
import yaml  # noqa: E402
from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402

import app.agent.llm as llm_mod  # noqa: E402
from app.agent.graph import graph  # noqa: E402
from app.agent.nodes import (  # noqa: E402
    classify_intent,
    correct_sql,
    filter_metric,
    filter_table,
    generate_sql,
    recall_column,
    recall_metric,
    recall_value,
    respond_chitchat,
    rewrite_query,
)
from app.entities.column_info import ColumnInfo  # noqa: E402
from app.entities.metric_info import MetricInfo  # noqa: E402
from app.entities.table_info import TableInfo  # noqa: E402
from app.entities.value_info import ValueInfo  # noqa: E402


# ───────────────────────────────────────────────────────────────
# 1. 内存版示例数据（与电商 schema 一致）
# ───────────────────────────────────────────────────────────────

T_REGION = "t_region"
T_PRODUCT = "t_product"
T_CUSTOMER = "t_customer"
T_ORDER = "t_order"

TABLES = [
    TableInfo(id=T_REGION, name="dim_region", role="维度表", description="地区维度"),
    TableInfo(id=T_PRODUCT, name="dim_product", role="维度表", description="商品维度"),
    TableInfo(id=T_CUSTOMER, name="dim_customer", role="维度表", description="客户维度"),
    TableInfo(id=T_ORDER, name="fact_order", role="事实表", description="订单事实"),
]

COLUMNS = [
    # dim_region
    ColumnInfo(id="c_region_id", name="region_id", type="varchar", role="primary_key", table_id=T_REGION, description="地区ID"),
    ColumnInfo(id="c_region_name", name="region_name", type="varchar", role="dimension", table_id=T_REGION, description="地区名"),
    ColumnInfo(id="c_province", name="province", type="varchar", role="dimension", table_id=T_REGION, description="省份"),
    ColumnInfo(id="c_country", name="country", type="varchar", role="dimension", table_id=T_REGION, description="国家"),
    # dim_product
    ColumnInfo(id="c_product_id", name="product_id", type="varchar", role="primary_key", table_id=T_PRODUCT, description="商品ID"),
    ColumnInfo(id="c_product_name", name="product_name", type="varchar", role="dimension", table_id=T_PRODUCT, description="商品名"),
    ColumnInfo(id="c_category", name="category", type="varchar", role="dimension", table_id=T_PRODUCT, description="品类"),
    ColumnInfo(id="c_brand", name="brand", type="varchar", role="dimension", table_id=T_PRODUCT, description="品牌"),
    # dim_customer
    ColumnInfo(id="c_customer_id", name="customer_id", type="varchar", role="primary_key", table_id=T_CUSTOMER, description="客户ID"),
    ColumnInfo(id="c_customer_name", name="customer_name", type="varchar", role="dimension", table_id=T_CUSTOMER, description="客户名"),
    ColumnInfo(id="c_gender", name="gender", type="varchar", role="dimension", table_id=T_CUSTOMER, description="性别"),
    ColumnInfo(id="c_member_level", name="member_level", type="varchar", role="dimension", table_id=T_CUSTOMER, description="会员等级"),
    # fact_order
    ColumnInfo(id="c_order_id", name="order_id", type="varchar", role="primary_key", table_id=T_ORDER, description="订单ID"),
    ColumnInfo(id="c_o_customer_id", name="customer_id", type="varchar", role="foreign_key", table_id=T_ORDER, description="客户ID"),
    ColumnInfo(id="c_o_product_id", name="product_id", type="varchar", role="foreign_key", table_id=T_ORDER, description="商品ID"),
    ColumnInfo(id="c_o_region_id", name="region_id", type="varchar", role="foreign_key", table_id=T_ORDER, description="地区ID"),
    ColumnInfo(id="c_o_date_id", name="date_id", type="varchar", role="foreign_key", table_id=T_ORDER, description="日期ID"),
    ColumnInfo(id="c_order_quantity", name="order_quantity", type="int", role="measure", table_id=T_ORDER, description="订单数量"),
    ColumnInfo(id="c_order_amount", name="order_amount", type="decimal", role="measure", table_id=T_ORDER, description="订单金额"),
]

METRICS = [
    MetricInfo(id="m_gmv", name="GMV", description="商品交易总额", relevant_columns=["fact_order.order_amount"], alias=["销售额", "销售总额"]),
    MetricInfo(id="m_aov", name="AOV", description="平均订单金额", relevant_columns=["fact_order.order_amount"], alias=["平均单价", "平均订单金额"]),
]

COLUMN_BY_ID = {c.id: c for c in COLUMNS}
TABLE_BY_ID = {t.id: t for t in TABLES}


# ───────────────────────────────────────────────────────────────
# 2. Fake 仓储
# ───────────────────────────────────────────────────────────────

class FakeEmbeddingClient:
    """Embedding 客户端桩：不真实向量化，返回定长伪向量"""

    async def aembed_query(self, text: str) -> list[float]:
        # 维度与 bge-large-zh-v1.5 一致（1024），但值固定不影响断言
        return [0.1] * 1024


class FakeColumnQdrant:
    """字段向量召回桩：返回 fact_order + dim_region 的全部字段"""

    async def search(self, embedding) -> list[ColumnInfo]:
        keep = {"t_order", "t_region"}
        return [c for c in COLUMNS if c.table_id in keep]


class FakeMetricQdrant:
    """指标向量召回桩：返回全部指标"""

    async def search(self, embedding) -> list[MetricInfo]:
        return list(METRICS)


class FakeValueES:
    """字段取值 ES 召回桩：返回「华北」取值，绑定到 region_name 字段"""

    async def search(self, keyword) -> list[ValueInfo]:
        return [ValueInfo(id="v_huabei", value="华北", column_id="c_region_name")]


class FakeMetaRepo:
    """元数据库仓储桩：按 id 批量查，避免 N+1"""

    async def get_column_infos_by_ids(self, ids):
        return [COLUMN_BY_ID[i] for i in ids if i in COLUMN_BY_ID]

    async def get_table_infos_by_ids(self, ids):
        return [TABLE_BY_ID[i] for i in ids if i in TABLE_BY_ID]

    async def get_key_columns_by_table_ids(self, table_ids):
        return [
            c
            for c in COLUMNS
            if c.table_id in set(table_ids) and c.role in ("primary_key", "foreign_key")
        ]

    async def get_all_table_infos(self):
        return list(TABLES)

    async def get_columns_by_table_id(self, table_id):
        return [c for c in COLUMNS if c.table_id == table_id]

    async def get_all_metric_infos(self):
        return list(METRICS)


class FakeDWRepo:
    """数仓仓储桩：只做安全校验与结果返回，不连真实 MySQL"""

    async def get_db_info(self):
        return {"dialect": "mysql", "version": "8.0.36"}

    async def validate(self, sql: str):
        # 安全：仅做最基础校验，不抛异常表示通过
        if not sql or not sql.strip().upper().startswith("SELECT"):
            raise ValueError("only SELECT allowed")
        return None

    async def run(self, sql: str):
        return [{"销售总额": 1234567}]


# ───────────────────────────────────────────────────────────────
# 3. FakeChatModel —— 按 prompt 标识词分发各节点输出
# ───────────────────────────────────────────────────────────────

def _extract_user_query(text: str) -> str:
    """从 classify_intent prompt 渲染文本里取出真实用户问题。

    prompt 末尾是「用户输入：\\n{query}」，示例文本里虽然也含「你好」
    等词，但只出现于前面的示例区；取最后一个「用户输入：」之后的
    内容即为真实 query，避免示例误匹配。
    """
    if "用户输入：" in text:
        return text.split("用户输入：")[-1].strip()
    return text.strip()


def _parse_yaml_block(text: str, marker: str) -> list:
    """从渲染后的 prompt 文本里提取 marker 之后、『输出：』之前的 YAML 块

    注意：filter_metric / filter_table 的 prompt 在「示例」区也会
    出现一次 marker（且示例是 JSON 数组，合法 YAML 但元素为字符串）。
    真实数据块总在 prompt 末尾，因此取**最后一次**出现。
    """
    if marker not in text:
        return []
    body = text.rsplit(marker, 1)[1]
    if "输出：" in body:
        body = body.split("输出：", 1)[0]
    try:
        return yaml.safe_load(body) or []
    except yaml.YAMLError:
        return []


class FakeChatModel(BaseChatModel):
    """极简 Fake LLM：识别 prompt 标识词后返回对应节点期望的结构化输出"""

    @property
    def _llm_type(self) -> str:
        return "fake-chat-model"

    def _build_reply(self, messages) -> str:
        text = "\n".join(
            m.content if isinstance(m.content, str) else str(m.content)
            for m in messages
        )

        # 闲聊响应
        if "友好的电商问数助手" in text:
            return "你好呀！我是电商问数助手，可以帮你查询销售额、订单、品类等业务数据～"

        # 意图分类
        if "用户意图分类器" in text:
            q = _extract_user_query(text)
            if q in ("你好", "谢谢", "你是谁", "你能做什么") or "你好" in q:
                return "chitchat"
            if "有哪些表" in q or "怎么算" in q or ("字段" in q and "结构" in q):
                return "metadata_query"
            return "data_query"

        # 字段语义扩展
        if "数据表字段推断专家" in text:
            return '["销售总额", "销售额", "地区", "订单金额"]'

        # 字段取值扩展
        if "业务语义解析专家" in text:
            return '["华北", "华南", "销售大区"]'

        # 指标语义扩展
        if "指标语义扩展专家" in text:
            return '["GMV", "销售额", "销售总额"]'

        # 过滤指标信息 —— 保留全部候选指标（用唯一标识词，避免被「查询规划专家」误匹配）
        if "指标筛选与查询规划专家" in text:
            metrics = _parse_yaml_block(text, "候选指标信息：")
            return json.dumps([m["name"] for m in metrics], ensure_ascii=False)

        # 过滤表信息 —— 解析 YAML 后「保留全部」返回
        # 必须用「候选表与字段集合」这个 filter_table 专属词（prompt 第2行，含「与」）
        if "候选表与字段集合" in text:
            tables = _parse_yaml_block(text, "候选表及字段信息：")
            return json.dumps(
                {
                    t["name"]: [c["name"] for c in t.get("columns", [])]
                    for t in tables
                },
                ensure_ascii=False,
            )

        # 查询改写 —— 返回原始问题（本测试 query 无相对时间表达）
        if "查询改写专家" in text:
            q = text.split("用户原始输入：")[-1].strip() if "用户原始输入：" in text else text.strip()
            return q

        # SQL 修正
        if "SQL 调试专家" in text:
            return (
                "SELECT SUM(o.order_amount) AS 销售总额 "
                "FROM fact_order o JOIN dim_region r ON o.region_id = r.region_id "
                "WHERE r.region_name = '华北'"
            )

        # SQL 生成
        if "资深的数据库专家" in text and "数据分析师" in text:
            return (
                "SELECT SUM(o.order_amount) AS 销售总额 "
                "FROM fact_order o JOIN dim_region r ON o.region_id = r.region_id "
                "WHERE r.region_name = '华北'"
            )

        return "data_query"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        reply = self._build_reply(messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=reply))])


# ───────────────────────────────────────────────────────────────
# 4. 测试夹具：安装 / 卸载 Fake LLM
# ───────────────────────────────────────────────────────────────

_NODE_MODULES = [
    classify_intent,
    respond_chitchat,
    generate_sql,
    correct_sql,
    filter_table,
    filter_metric,
    recall_column,
    recall_value,
    recall_metric,
    rewrite_query,
]


def _install_fake_llm(fake):
    """节点里是 `from app.agent.llm import llm` 的模块级绑定，
    必须把 fake 同时打到 llm 模块和各 node 模块的命名空间。"""
    llm_mod.llm = fake
    for mod in _NODE_MODULES:
        mod.llm = fake


def _build_context():
    return {
        "column_qdrant_repository": FakeColumnQdrant(),
        "embedding_client": FakeEmbeddingClient(),
        "metric_qdrant_repository": FakeMetricQdrant(),
        "value_es_repository": FakeValueES(),
        "meta_mysql_repository": FakeMetaRepo(),
        "dw_mysql_repository": FakeDWRepo(),
    }


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
    }


async def _run_chain(query: str):
    """跑通整图，返回 (最终 state 合并, result 事件列表)"""
    fake = FakeChatModel()
    _install_fake_llm(fake)

    final_state: dict = {}
    result_events: list = []

    async for item in graph.astream(
        input=_initial_state(query),
        context=_build_context(),
        stream_mode=["updates", "custom"],
    ):
        # 多 stream_mode 下每个 chunk 是 (mode, data) 元组
        if isinstance(item, tuple) and len(item) == 2:
            mode, chunk = item
        else:
            mode, chunk = None, item

        if mode == "updates":
            for _node, patch in chunk.items():
                if patch:
                    final_state.update(patch)
        elif mode == "custom":
            if isinstance(chunk, dict) and chunk.get("type") == "result":
                result_events.append(chunk)

    return final_state, result_events


# ───────────────────────────────────────────────────────────────
# 5. 测试用例
# ───────────────────────────────────────────────────────────────


async def test_data_query_full_pipeline():
    """data_query：完整 RAG + SQL 生成 + 执行链路跑通"""
    final, results = await _run_chain("统计华北地区的销售总额")

    # 1. 意图正确路由到 data_query
    assert final.get("intent") == "data_query", f"意图应为 data_query，实际 {final.get('intent')}"

    # 2. 生成了合法 SQL，且符合问题语义
    sql = final.get("sql", "")
    assert "SELECT" in sql.upper(), "应生成 SELECT 语句"
    assert "华北" in sql, "SQL 应含过滤条件 华北"
    assert "SUM" in sql.upper(), "GMV 指标应走 SUM 聚合"

    # 3. 结果事件返回了销售总额
    assert results, "应至少有一个 result 事件"
    payload = results[-1].get("data", [])
    assert any("销售总额" in str(row) for row in payload), "结果应含 销售总额"


async def test_chitchat_shortcut():
    """chitchat：走闲聊短路，不进 RAG 链路、不生成 SQL"""
    final, results = await _run_chain("你好")

    assert final.get("intent") == "chitchat", f"意图应为 chitchat，实际 {final.get('intent')}"
    # 闲聊路径不应生成 SQL
    assert not final.get("sql"), "闲聊不应生成 SQL"
    # 应有闲聊回复
    assert results, "应返回闲聊回复"
    payload = [row for r in results for row in r.get("data", [])]
    assert any("回复" in row for row in payload), "回复应含『回复』键"


async def test_metadata_query_shortcut():
    """metadata_query：走元数据短路，返回表列表"""
    final, results = await _run_chain("有哪些表")

    assert final.get("intent") == "metadata_query", f"意图应为 metadata_query，实际 {final.get('intent')}"
    assert results, "应返回元数据结果"
    payload = results[-1].get("data", [])
    assert len(payload) >= 1, "应至少返回一张表"
    assert any("表名" in row for row in payload), "结果应包含表名"


async def test_all_three_paths_distinct():
    """三条路径的 intent 互不串路"""
    d, _ = await _run_chain("统计华北地区的销售总额")
    c, _ = await _run_chain("你好")
    m, _ = await _run_chain("有哪些表")
    assert d["intent"] == "data_query"
    assert c["intent"] == "chitchat"
    assert m["intent"] == "metadata_query"
