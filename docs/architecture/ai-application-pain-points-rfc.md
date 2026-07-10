# AI 应用层痛点审查 RFC — Grill-me 20 刀

> **文档状态**: Draft  
> **审查日期**: 2026-07-09 / 2026-07-10  
> **审查方式**: grill-me 逐刀追问 + 代码逐行阅读（两轮审查）  
> **审查范围**: shopkeeper-agent 的 AI 应用层（意图理解、召回策略、Prompt 工程、多轮对话、结果校验、评估闭环）+ 第二轮新增（时间处理、元数据查询、N+1 查询、客户端容错、API 安全、部署工程化、测试覆盖、配置安全、前端一致性）  
> **与既有 RFC 的关系**: 既有《生产环境痛点审查 RFC》偏后端工程（SQL 循环、LLM 容错、Redis 竞态、可观测性），本 RFC 只覆盖 **AI 应用层**，两者互补，不重叠。

---

## 目录

### 第一轮（刀 1~6）：AI 应用层核心痛点
1. [刀 1：没有意图识别和 Query 改写](#刀-1没有意图识别和-query-改写所有问题走同一条链路)
2. [刀 2：召回是单跳关键词扩展，复杂问题召回必然漏](#刀-2召回是单跳关键词扩展复杂问题召回必然漏)
3. [刀 3：召回结果没有 rerank，噪声直接灌进 filter](#刀-3召回结果没有-rerank噪声直接灌进-filter)
4. [刀 4：Prompt 零示例零负例，LLM 输出无法稳定](#刀-4prompt-零示例零负例llm-输出无法稳定)
5. [刀 5：多轮对话的上下文污染](#刀-5多轮对话的上下文污染enhanced_query-直接塞进-state)
6. [刀 6：没有结果校验——SQL 执行成功 ≠ 结果正确](#刀-6没有结果校验sql-执行成功--结果正确)

### 第二轮（刀 7~20）：深度审阅新增痛点
7. [刀 7：AOV 指标定义写错了字段（Bug）](#刀-7aov-指标定义写错了字段bug)
8. [刀 8：时区陷阱 — date.today() 在 Docker 中算错一天](#刀-8时区陷阱--datetoday-在-docker-容器中算错一天)
9. [刀 9：rewrite_query.py 12 月要炸](#刀-9rewrite_querypy-12-月要炸)
10. [刀 10：API 层完全裸奔](#刀-10api-层完全裸奔)
11. [刀 11：LLM 无超时](#刀-11llm-无超时--一个慢请求拖垮全部)
12. [刀 12：respond_metadata 暴力子串匹配](#刀-12respond_metadata-暴力子串匹配)
13. [刀 13：Qdrant / ES 客户端无重连](#刀-13qdrant--es-客户端无重连)
14. [刀 14：filter 节点对 LLM 输出无 schema 校验](#刀-14filter-节点对-llm-输出无-schema-校验)
15. [刀 15：merge_retrieved_info 串行 N+1 查询](#刀-15merge_retrieved_info-串行-n1-查询)
16. [刀 16：extract_keywords 词性覆盖不足 + 无业务词典](#刀-16extract_keywords-词性覆盖不足--无业务词典)
17. [刀 17：前端"新会话"不清后端](#刀-17前端新会话不清后端)
18. [刀 18：配置明文凭证](#刀-18配置明文凭证)
19. [刀 19：start_app.py 错误路径 + 无 Dockerfile](#刀-19start_apppy-错误路径--无-dockerfile)
20. [刀 20：测试覆盖严重不足](#刀-20测试覆盖严重不足--关键节点-0-测试)

---

## 刀 1：没有意图识别和 Query 改写——所有问题走同一条链路

### 现状

`app/services/query_service.py` 第 54-82 行，所有用户输入无差别地进入同一条 12 节点 LangGraph 链路：

```python
async def query(self, query: str, session_id: str = "default"):
    history = get_history(session_id, max_count=3)
    enhanced_query = build_prompt(query, history)
    state = DataAgentState(query=enhanced_query)
    context = DataAgentContext(...)
    async for chunk in graph.astream(input=state, context=context, stream_mode="custom"):
        ...
```

`app/agent/graph.py` 第 60-89 行，图结构是**完全线性固定**的：

```
START → extract_keywords
      → recall_column / recall_value / recall_metric（并行）
      → merge_retrieved_info
      → filter_table / filter_metric（并行）
      → add_extra_context
      → generate_sql
      → validate_sql
      → (correct_sql →) run_sql → END
```

**没有分支、没有路由、没有短路。**

### 问题

#### 问题 1：闲聊和查数走同一条链路

用户输入"你好"、"谢谢"、"你是谁"时，系统仍然：

1. jieba 抽关键词 → "你好"
2. LLM 扩展字段关键词 → 可能返回 `["打招呼", "问候"]`
3. Embedding → Qdrant 检索字段 → 检不到任何字段（score 都低于 0.6）
4. 召回为空 → merge 为空 → filter 为空 → generate_sql 在空上下文下生成 SQL
5. validate_sql 失败 → correct_sql → run_sql → 500 或空结果

**浪费 3 次 LLM 调用 + 3 次 Embedding + 3 次向量检索，最后还给用户一个错误。**

#### 问题 2：元数据查询和业务查询走同一条链路

| 用户输入                 | 真实意图    | 当前链路做的事                                                      |
| -------------------- | ------- | ------------------------------------------------------------ |
| "dim_customer 有哪些字段" | 查元数据表结构 | 走完整 RAG + SQL 生成 → 可能生成 `SELECT * FROM dim_customer LIMIT 1` |
| "你能查什么表"             | 查可用表列表  | 走完整 RAG → 召回不到 → 生成空 SQL 或报错                                 |
| "GMV 是怎么算的"          | 查指标定义   | 走完整 RAG → 召回到指标但生成不出有意义的 SQL                                 |

这三类问题**不应该生成 SQL**，但当前架构强行让它们走 SQL 生成路径。

#### 问题 3：没有 Query 改写

用户输入"上个月的销售额"时，`extract_keywords` 节点用 jieba 抽出 `["上个月", "销售额"]`，但：

- "上个月" 是相对时间，**没有解析成具体日期范围**（如 2026-06-01 ~ 2026-06-30）
- 虽然 `add_extra_context` 节点会补当前日期，但 `generate_sql` 的 prompt 里只有 `date_info`，**LLM 需要自己推算"上个月是几月"**
- 用户输入"最近7天"、"本季度"、"去年同期"时，全靠 LLM 自己算，**算错就是语义错误**

#### 问题 4：没有 Query 分解

用户输入"各品类的销售额和订单数对比"时，这是一个**复合问题**：

- 子问题 1：各品类的销售额
- 子问题 2：各品类的订单数

当前链路把它当**一个问题**处理，`extract_keywords` 抽出来的关键词混合了两个度量，`recall_metric` 可能召回出"销售额"和"订单数"两个指标，但 `generate_sql` 需要**在一条 SQL 里同时算两个指标**，LLM 容易漏掉其中一个或 JOIN 条件写错。

更复杂的："华北上月销售额对比上月上月增长率"——这需要**两次查询 + 计算**，当前架构根本不支持。

### 影响面

| 场景     | 发生概率 | 后果                                |
| ------ | ---- | --------------------------------- |
| 用户闲聊   | 中    | 浪费 3+ 次 LLM 调用，返回错误，体验差           |
| 元数据查询  | 中    | 生成了无意义的 SQL，用户得不到想要的表结构信息         |
| 相对时间表达 | 高    | LLM 自己算日期，算错就是语义错误，结果对不上          |
| 复合问题   | 中    | LLM 在一条 SQL 里硬塞多个度量，容易漏指标或 JOIN 错 |

### 方案

**在 `extract_keywords` 之前加一个 `classify_and_rewrite` 节点**

```
START → classify_and_rewrite
      ├─(intent=chitchat)──────────────────→ respond_chitchat → END
      ├─(intent=metadata_query)───────────→ respond_metadata → END
      └─(intent=data_query)───────────────→ rewrite_query
                                              → extract_keywords → ...（原链路）
```

**1. 意图分类 prompt**（新增 `prompts/classify_intent.prompt`）：

```
角色：
你是一个用户意图分类器，负责判断用户输入属于以下哪一类：

1. chitchat — 闲聊、打招呼、感谢、询问 Agent 身份（"你好"、"谢谢"、"你能做什么"）
2. metadata_query — 查询数据库元数据（"有哪些表"、"dim_customer 有哪些字段"、"GMV 怎么算的"）
3. data_query — 需要查询业务数据（"华北上个月销售额"、"各品类销量"）

判断规则：
- 如果用户输入不涉及任何业务数据查询意图，归为 chitchat
- 如果用户明确询问表结构、字段、指标定义，归为 metadata_query
- 如果用户要求统计、对比、查询业务数据，归为 data_query

输出要求：
- 只输出一个单词：chitchat / metadata_query / data_query
- 不输出任何解释

用户输入：
{query}

输出：
```

**2. Query 改写 prompt**（新增 `prompts/rewrite_query.prompt`）：

```
角色：
你是一个查询改写专家，负责把用户的自然语言问题改写成更适合检索和 SQL 生成的标准化形式。

任务：
1. 解析相对时间表达（"上个月"、"最近7天"、"本季度"、"去年同期"）为具体日期范围
2. 补全省略的主语和宾语（"换成华北" → "上一个查询的指标按华南地区重新查询"）
3. 分解复合问题为多个子查询（如果需要）

当前日期：{date_info}

历史对话：
{history}

用户原始输入：
{query}

输出要求：
- 输出一个 JSON 对象
- 如果是单一问题：{{"queries": ["改写后的问题"]}}
- 如果需要分解：{{"queries": ["子问题1", "子问题2"]}}
- 不输出任何解释

输出：
```

**3. 代码改动**：

```python
# app/agent/nodes/classify_and_rewrite.py（新增）
async def classify_and_rewrite(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    query = state["query"]

    # 1. 意图分类
    intent = await _classify_intent(query)

    # 2. 如果是闲聊或元数据查询，直接短路
    if intent in ("chitchat", "metadata_query"):
        return {"intent": intent, "queries": []}

    # 3. 如果是数据查询，做 query 改写
    date_info = _get_date_info()  # 复用 add_extra_context 的逻辑
    history = state.get("history", [])
    rewritten = await _rewrite_query(query, date_info, history)

    return {"intent": intent, "queries": rewritten["queries"]}
```

```python
# graph.py 改动
graph_builder.add_node("classify_and_rewrite", classify_and_rewrite)
graph_builder.add_node("respond_chitchat", respond_chitchat)
graph_builder.add_node("respond_metadata", respond_metadata)

graph_builder.add_edge(START, "classify_and_rewrite")

graph_builder.add_conditional_edges(
    source="classify_and_rewrite",
    path=lambda state: {
        "chitchat": "respond_chitchat",
        "metadata_query": "respond_metadata",
        "data_query": "extract_keywords",
    }[state["intent"]],
    path_map={
        "respond_chitchat": "respond_chitchat",
        "respond_metadata": "respond_metadata",
        "extract_keywords": "extract_keywords",
    },
)
```

### 验收标准

- [x] 用户输入"你好"时不走 RAG 链路，直接返回闲聊响应
- [x] 用户输入"有哪些表"时返回元数据，不生成 SQL
- [x] "上个月"被解析成具体日期范围后再进入 SQL 生成
- [ ] 复合问题能被分解成多个子查询（至少识别出来）
- [ ] 正常数据查询不受影响

---

## 刀 2：召回是单跳关键词扩展，复杂问题召回必然漏

### 现状

`app/agent/nodes/recall_column.py` 第 36-49 行：

```python
# 用 LLM 把用户问法扩展成"字段语义"列表
prompt = PromptTemplate(
    template=load_prompt("extend_keywords_for_column_recall"),
    input_variables=["query"],
)
chain = prompt | llm | output_parser
result = await chain.ainvoke({"query": query})

# 原始关键词和 LLM 扩展词一起参与召回
keywords = set(keywords + result)

for keyword in keywords:
    embedding = await embedding_client.aembed_query(keyword)
    current_column_infos = await column_qdrant_repository.search(embedding)
    ...
```

`recall_value.py` 和 `recall_metric.py` 是同样的结构：**单次 LLM 扩展 → 逐关键词 Embedding → Qdrant/ES 检索 → 去重**。

### 问题

#### 问题 1：单跳扩展覆盖不了多跳语义

用户问"华北上个月销售额对比上月增长率"：

- `extract_keywords` 抽出 `["华北", "上个月", "销售额", "对比", "上月", "增长率"]`
- `recall_column` 的 LLM 扩展出 `["地区", "销售额", "增长率", "对比"]`
- 但这条问题**真正需要的字段**是：
  - `dim_region.region_name`（华北）
  - `fact_order.order_amount`（销售额）
  - `fact_order.order_date`（时间过滤）
  - 可能还需要 `dim_date` 来算"上月"和"上上月"

**问题在于**："增长率"这个概念需要**先查到指标"增长率"→ 再查增长率依赖的字段 → 再查这些字段的取值**。这是**两跳**的语义链，但当前架构只做了一跳扩展。

`merge_retrieved_info` 第 56-64 行虽然会补齐指标依赖字段：

```python
for retrieved_metric_info in retrieved_metric_infos:
    for relevant_column in retrieved_metric_info.relevant_columns:
        if relevant_column not in retrieved_column_infos_map:
            column_info = await meta_mysql_repository.get_column_info_by_id(relevant_column)
            retrieved_column_infos_map[relevant_column] = column_info
```

但这**依赖于 `recall_metric` 先召回到"增长率"这个指标**——如果 `recall_metric` 的 LLM 扩展没扩展出"增长率"这个概念，后面的字段补齐就无从谈起。

#### 问题 2：关键词扩展是"盲扩展"，没有上下文约束

`extend_keywords_for_column_recall.prompt` 第 28-31 行：

```
3. 以用户问题的"度量目标"为核心生成
   - 若问题显式包含指标（如"转化率/GMV/DAU"），必须原样保留并可补充同义词
   - 若问题隐含指标（如"效果如何/增长多少/情况怎么样"），生成最可能对应的指标概念，但不得发散到无关指标
```

prompt 要求"不得发散"，但 **LLM 实际会发散**。没有负例约束它"什么不该扩展"，也没有上下文告诉它"数据库里有哪些表/字段"——它是**凭空扩展**的。

结果：

- 扩展出数据库里根本不存在的字段名 → Qdrant 检索不到（浪费 Embedding + 检索）
- 扩展出歧义词 → 检索到无关字段 → filter 节点要花一次 LLM 调用去剔除

#### 问题 3：三路召回各自为政，没有交叉验证

`recall_column`、`recall_value`、`recall_metric` 是**并行**的，互不感知：

- `recall_column` 召回到 `dim_region.region_name`，但不知道 `recall_value` 是否召回到了"华北"这个值
- `recall_metric` 召回到"GMV"指标，但不知道 `recall_column` 是否召回到了 `fact_order.order_amount`（GMV 依赖的字段）

`merge_retrieved_info` 做了**事后补齐**（第 56-64 行补指标依赖字段），但这是**单向的**（指标 → 字段），没有反向验证：

- 如果 `recall_column` 召回到了一个字段，但没有任何指标依赖它，这个字段是真的需要吗？
- 如果 `recall_value` 召回到了"华北"，但 `recall_column` 没召回到 `region_name` 字段，这个取值有用吗？

#### 问题 4：没有"召回为空"的降级

如果 `recall_column` 召回为空（所有关键词的 Embedding 检索 score 都低于 0.6）：

- `merge_retrieved_info` 拿到空列表 → `table_infos` 为空 → `filter_table` 拿到空候选 → `generate_sql` 在空上下文下生成 SQL
- **没有"召回为空"的降级策略**——比如降级到全文检索、降级到直接让 LLM 生成、或直接返回"我无法理解你的问题"

### 影响面

| 场景           | 发生概率 | 后果                            |
| ------------ | ---- | ----------------------------- |
| 多跳语义（增长率、环比） | 高    | 召回漏字段，SQL 生成缺少必要字段，JOIN 错或算不出 |
| LLM 发散扩展     | 中    | 召回到无关字段，filter 需要花 LLM 调用剔除   |
| 召回为空         | 中    | 空上下文生成 SQL，必然失败               |
| 三路召回不一致      | 高    | merge 后上下文有冗余或缺失              |

### 方案

**1. 在 `recall_column` 里加"数据库 schema 感知"的扩展**

修改 `extend_keywords_for_column_recall.prompt`，把候选表/字段名列表喂给 LLM：

```
角色：
你是一名数据表字段推断专家。

任务：
给定【用户问题】和【数据库已有的字段列表】，从中选出回答该问题可能需要的字段名。

数据库已有字段：
{available_columns}

用户问题：
{query}

输出要求：
- 仅输出 JSON 数组，元素是上述字段列表中的字段名
- 只选回答该问题可能需要的字段
- 不输出字段列表中不存在的内容
```

**好处**：LLM 不再凭空扩展，而是从真实 schema 里选，避免"幻觉字段"。

**2. 加 multi-hop 召回——指标 → 字段 → 取值链路**

```python
# recall_metric 后加一个 recall_columns_by_metric 节点
async def recall_columns_by_metric(state, runtime):
    """如果召回到了指标，按指标依赖的字段反向补充字段召回"""
    metric_infos = state["retrieved_metric_infos"]
    if not metric_infos:
        return {}

    column_qdrant_repository = runtime.context["column_qdrant_repository"]
    embedding_client = runtime.context["embedding_client"]

    # 用指标名和别名做向量检索，补充召回指标依赖的字段
    existing_column_ids = {c.id for c in state["retrieved_column_infos"]}
    new_column_infos = []
    for metric in metric_infos:
        for keyword in [metric.name] + metric.alias:
            embedding = await embedding_client.aembed_query(keyword)
            results = await column_qdrant_repository.search(embedding)
            for col in results:
                if col.id not in existing_column_ids:
                    # 只补充指标 relevant_columns 里声明的字段
                    if col.id in metric.relevant_columns:
                        new_column_infos.append(col)
                        existing_column_ids.add(col.id)

    return {"retrieved_column_infos": list(state["retrieved_column_infos"]) + new_column_infos}
```

**3. 加"召回为空"降级**

```python
# merge_retrieved_info 里加判断
if not retrieved_column_infos and not retrieved_metric_infos:
    writer({"type": "warning", "message": "未能理解您的问题，请换个问法试试"})
    return {"table_infos": [], "metric_infos": [], "_abort": True}
```

在 graph 里加条件边：`_abort=True` 时直接跳到 END，不走后续 filter/generate_sql。

### 验收标准

- [ ] LLM 扩展不再生成数据库里不存在的字段名
- [ ] "增长率"等多跳概念能通过指标反向补齐依赖字段
- [ ] 召回为空时直接返回友好提示，不走空上下文 SQL 生成
- [ ] 召回结果数量不再包含明显无关的字段

---

## 刀 3：召回结果没有 rerank，噪声直接灌进 filter

### 现状

`app/repositories/qdrant/column_qdrant_repository.py` 第 53-64 行：

```python
async def search(
    self, embedding: list[float], score_threshold: float = 0.6, limit: int = 20
) -> list[ColumnInfo]:
    result = await self.client.query_points(
        collection_name=self.collection_name,
        query=embedding,
        limit=limit,
        score_threshold=score_threshold,
    )
    return [ColumnInfo(**point.payload) for point in result.points]
```

`app/repositories/es/value_es_repository.py` 第 61-75 行：

```python
async def search(
    self, keyword: str, score_threshold: float = 0.6, limit: int = 20
) -> list[ValueInfo]:
    resp = await self.client.search(
        index=self.index_name,
        query={"match": {"value": keyword}},
        size=limit,
        min_score=score_threshold,
    )
    return [ValueInfo(**hit["_source"]) for hit in resp["hits"]["hits"]]
```

**Qdrant 和 ES 都用双塔模型（Embedding）做召回，没有 cross-encoder rerank。**

`recall_column` 节点对每个关键词都检索一次，多个关键词的结果**只做了 id 去重**（第 60-61 行），**没有按综合相关度排序**：

```python
for keyword in keywords:
    embedding = await embedding_client.aembed_query(keyword)
    current_column_infos = await column_qdrant_repository.search(embedding)
    for column_info in current_column_infos:
        if column_info.id not in column_info_map:
            column_info_map[column_info.id] = column_info
```

**先到先得**——哪个关键词先检索到某个字段，那个字段的 score 就被保留了，后续关键词即使检索到同一个字段且 score 更高，也不会更新。

### 问题

#### 问题 1：召回结果噪声多（阈值本身合理，但缺少精筛层）

> **修正说明**：`score_threshold=0.6` 对 bge-large-zh-v1.5 是社区验证过的合理阈值，设计理念是"高召回优先，宁可多召回也不漏，交给后续 filter 精选"——这个取舍是正确的。本节的痛点不是阈值本身，而是**召回阶段缺少精筛层**，导致噪声直接灌进 filter。

Embedding 向量检索（双塔）是**粗召回**，特点是快但精度低：

- `score_threshold=0.6` 是 bge-large-zh 的社区合理阈值（保留）
- `limit=20` 也是合理配置（保留）
- 但 0.6 以上的字段**仍然可能包含噪声**——粗召回的固有特性

实际场景：用户问"华北上个月销售额"，`recall_column` 可能召回到：

- `fact_order.order_amount`（score=0.82，相关 ✅）
- `dim_region.region_name`（score=0.75，相关 ✅）
- `fact_order.order_status`（score=0.63，不相关 ❌ 但过了阈值）
- `dim_customer.customer_name`（score=0.61，不相关 ❌ 但过了阈值）

这 20 个字段全部灌进 `filter_table` 的 prompt，LLM 要从里面选——**噪声多，token 浪费，容易漏选或误选**。

> **设计取舍说明**：高召回策略（低阈值 + 多关键词扩展）保证了不漏字段，代价是召回结果多 + 执行时间长。这是正确的取舍——漏召回不可恢复，多召回可以靠 filter 剔除。优化方向应聚焦于"如何在不降低召回率的前提下减少执行时间"，而不是提高阈值。

#### 问题 2：多关键词召回没有融合排序

`recall_column` 对 N 个关键词分别检索，每个返回 top-20，去重后可能有 30-50 个字段。这些字段的**排序是随机的**（取决于哪个关键词先检索到），没有按"被多少个关键词命中"或"最高 score"来排序。

结果：`filter_table` 的 prompt 里，**真正相关的字段可能排在列表末尾，不相关的排在前面**。LLM 的注意力对位置敏感（lost-in-the-middle 问题），排在中间的字段容易被忽略。

#### 问题 3：filter 节点用 LLM 做 rerank，但代价高且不稳

`filter_table` 和 `filter_metric` 用 LLM 做选择，本质上是**用 LLM 当 cross-encoder**。但：

- 一次 LLM 调用 ~1-3s，延迟高
- LLM 的选择不稳定（temperature=0 也不完全稳定）
- 候选多时（30+ 个字段），prompt 长，LLM 容易漏选

**正确的做法**：用轻量 cross-encoder（如 bge-reranker）先做一轮 rerank，把 top-20 缩到 top-5，再让 LLM 精选。

### 影响面

| 场景          | 发生概率 | 后果                         |
| ----------- | ---- | -------------------------- |
| 召回噪声多       | 高    | filter prompt 长，LLM 漏选关键字段 |
| 多关键词结果无融合排序 | 高    | 相关字段排在后面，LLM 注意力丢失         |
| 阈值 0.6 太低   | 中    | 不相关字段混入，filter 误选          |
| 阈值 0.6 太高   | 中    | 相关字段被过滤，召回漏                |

### 方案

**1. 加 cross-encoder rerank 节点**

```
recall_column → rerank_column → merge_retrieved_info
recall_value  → rerank_value  ↗
recall_metric → rerank_metric ↗
```

```python
# app/agent/nodes/rerank_column.py（新增）
from sentence_transformers import CrossEncoder

# 加载轻量 rerank 模型（本地推理，不占 LLM 调用配额）
_reranker = CrossEncoder("BAAI/bge-reranker-base")

async def rerank_column(state, runtime):
    query = state["query"]
    column_infos = state["retrieved_column_infos"]

    # 用原始 query（不是 enhanced_query）和每个字段的 name+description 做相关性打分
    pairs = [(query, f"{c.name} {c.description}") for c in column_infos]
    scores = _reranker.predict(pairs)

    # 按 score 降序排序，取 top-5
    ranked = sorted(zip(column_infos, scores), key=lambda x: x[1], reverse=True)
    top_k = 5
    reranked_column_infos = [c for c, s in ranked[:top_k] if s > 0.3]

    return {"retrieved_column_infos": reranked_column_infos}
```

**2. 多关键词召回融合排序（短期改动，不加新依赖）**

```python
# recall_column.py 改动：用 score 融合，而不是先到先得
column_score_map: dict[str, float] = {}  # column_id -> max_score
column_info_map: dict[str, ColumnInfo] = {}

for keyword in keywords:
    embedding = await embedding_client.aembed_query(keyword)
    # Qdrant search 需要返回 score
    results = await column_qdrant_repository.search_with_score(embedding)
    for col, score in results:
        if col.id not in column_score_map or score > column_score_map[col.id]:
            column_score_map[col.id] = score
            column_info_map[col.id] = col

# 按 score 降序排序
ranked_ids = sorted(column_score_map.keys(), key=lambda x: column_score_map[x], reverse=True)
retrieved_column_infos = [column_info_map[cid] for cid in ranked_ids]
```

**3. 阈值可配置化**

```yaml
# conf/app_config.yaml
qdrant:
  embedding_size: 1024
  recall_score_threshold: 0.5    # 召回阶段放宽（多召回，交给 rerank 筛）
  rerank_score_threshold: 0.3    # rerank 阶段收紧
  recall_limit: 20
  rerank_top_k: 5
```

### 验收标准

- [ ] 召回结果经过 rerank 后，相关字段排在前面
- [ ] 多关键词召回按最高 score 融合排序，不是先到先得
- [ ] 阈值可在配置文件里调整，不用改代码
- [ ] filter 节点收到的候选数量从 20+ 降到 5-8
- [ ] filter 节点 LLM 漏选率下降（用 eval 数据集验证）

### 召回耗时优化（用户反馈的真实痛点）

> **背景**：用户反馈"召回阶段执行时间非常长"——当前 `recall_column` 对 N 个关键词**串行循环**，每个关键词都要 `aembed_query` → `qdrant.search`，8 个关键词 = 8 次串行往返，耗时 3-5 秒。三路召回（column + value + metric）各自串行，总耗时可能 10-15 秒。

以下 4 个方案按改动量从小到大排列，可叠加使用：

**方案 1：Embedding 批量化（最小改动，立即生效）**

当前代码对每个关键词单独调 `aembed_query`，但 HuggingFace TEI 支持**批量 Embedding**：

```python
# recall_column.py 当前代码（串行）
for keyword in keywords:
    embedding = await embedding_client.aembed_query(keyword)  # 一次一个
    current_column_infos = await column_qdrant_repository.search(embedding)

# 改成批量
embeddings = await embedding_client.aembed_documents(list(keywords))  # 一次批量
for embedding in embeddings:
    current_column_infos = await column_qdrant_repository.search(embedding)
```

**效果**：8 个关键词的 Embedding 从 8 次 HTTP 往返 → 1 次。预计节省 1-2 秒。

**前置条件**：确认 `embedding_client` 是否支持 `aembed_documents`（TEI 原生支持批量）。

**方案 2：Qdrant 批量检索（配合方案 1）**

Qdrant 支持 `query_batch_points`（批量向量检索），不需要逐个搜：

```python
# 一次提交 8 个向量，Qdrant 内部并行检索
embeddings = await embedding_client.aembed_documents(list(keywords))
# Qdrant 批量检索
batch_results = await self.client.query_batch_points(
    collection_name=self.collection_name,
    queries=embeddings,           # 8 个向量一起搜
    limit=20,
    score_threshold=0.6,
)
# batch_results 是 list[list[ScoredPoint]]，每个查询对应一组结果
```

**效果**：8 次 Qdrant HTTP 往返 → 1 次。预计再节省 1-2 秒。

**前置条件**：`column_qdrant_repository` 需要新增 `batch_search` 方法。

**方案 3：asyncio.gather 并行化三路召回 + 关键词循环**

当前三路召回（column / value / metric）虽然图层面是并行的（`extract_keywords → recall_column / recall_value / recall_metric`），但每一路内部的关键词循环是串行的。用 `asyncio.gather` 并行化：

```python
# recall_column.py 改动
async def _search_one_keyword(keyword, embedding_client, column_qdrant_repository):
    """单个关键词的 Embedding + 检索"""
    embedding = await embedding_client.aembed_query(keyword)
    return await column_qdrant_repository.search(embedding)

# 并行执行所有关键词的检索
tasks = [_search_one_keyword(kw, embedding_client, column_qdrant_repository) for kw in keywords]
all_results = await asyncio.gather(*tasks, return_exceptions=True)

for result in all_results:
    if isinstance(result, Exception):
        logger.warning(f"关键词检索失败: {result}")
        continue
    for column_info in result:
        if column_info.id not in column_info_map:
            column_info_map[column_info.id] = column_info
```

**效果**：8 个关键词的 Embedding + 检索全部并行，耗时从 8 × RTT → 1 × RTT。预计再节省 2-3 秒。

**注意**：如果同时用方案 1+2（批量），则方案 3 不需要——批量已经把串行消除了。方案 3 是"不改 embedding_client 和 repository 的情况下最快的改法"。

**方案 4：LLM 关键词扩展前置缓存（长期优化）**

当前每次查询都调 LLM 扩展关键词（3 路 × 1 次 = 3 次 LLM 调用），如果同一个 session 的追问"换成华北"只改了地区值，关键词扩展结果和上一轮几乎一样。

```python
# 按 session_id + query 做缓存
cache_key = f"keyword_expand:{session_id}:{hash(query)}"
cached = await redis_client.get(cache_key)
if cached:
    return json.loads(cached)

# ... LLM 扩展 ...
await redis_client.setex(cache_key, 300, json.dumps(result))  # 缓存 5 分钟
```

**效果**：追问场景下跳过 3 次 LLM 调用，预计节省 3-5 秒。

**前置条件**：recall 节点需要拿到 `session_id`（当前不在 state 里，需要从 query_service 透传）。

### 优化效果预估

| 方案 | 改动量 | 预计节省 | 累计耗时 |
|------|--------|---------|---------|
| 当前 | - | - | 10-15s |
| 方案 1（批量 Embedding） | 小 | -1~2s | 8-13s |
| 方案 1+2（批量 Qdrant） | 小 | -1~2s | 6-11s |
| 方案 1+2+3（或仅 3） | 中 | -2~3s | 4-8s |
| 方案 1+2+4（+缓存） | 中 | -3~5s | 3-6s |
| 全部叠加 | 中 | -5~8s | **2-5s** |

---

## 刀 4：Prompt 零示例零负例，LLM 输出无法稳定

### 现状

`prompts/generate_sql.prompt` 全文：

```
【角色】
你是一个资深的数据库专家和数据分析师。你的任务是根据提供的【上下文信息】，将用户的自然语言查询转换为语法正确、性能优化的 SQL 语句。

【上下文信息】
可用数据表信息如下：
{table_infos}
...

【任务要求】
1. ⛔ 安全红线：你只能生成 SELECT 查询语句...
2. 仅允许使用数据表信息中真实存在的表与字段名称...
...

用户查询如下：
{query}

输出：
```

**7 个 prompt 文件，没有一个包含 few-shot 示例，没有一个包含负例。**

### 问题

#### 问题 1：generate_sql 零示例，LLM 靠猜

`generate_sql.prompt` 只给了角色 + 上下文 + 规则，**没有一条"输入→输出"示例**。

LLM 生成 SQL 时，它不知道：

- 期望的 SQL 风格是什么（大写关键字？小写表名？是否用别名？）
- 聚合字段怎么命名（`SUM(order_amount) AS 销售额` 还是 `AS total_amount` 还是 `AS gmv`？）
- JOIN 风格（`LEFT JOIN` 还是 `INNER JOIN`？`ON` 条件怎么写？）
- 日期过滤风格（`WHERE date BETWEEN '2026-06-01' AND '2026-06-30'` 还是 `WHERE date >= '2026-06-01'`？）

**结果**：每次生成的 SQL 风格不一致，`correct_sql` 修正时也风格不统一，`run_sql` 执行后结果列名不稳定，前端展示混乱。

#### 问题 2：correct_sql 没有"修正示例"，LLM 修歪

`correct_sql.prompt` 要求"最小必要修改"，但**没有给出"什么是最小修改"的示例**。

LLM 收到错误信息后，可能：

- 把整条 SQL 重写（不是最小修改）
- 改了语义（从 SUM 改成 COUNT）
- 加了不必要的子查询

**没有示例约束，LLM 的"最小修改"和人类理解的"最小修改"不一致。**

#### 问题 3：filter prompt 没有"返回空"的负例

`filter_table_info.prompt` 第 9 行：

```
- 若问题不需要使用任何指标，应返回空结果
```

但**没有给出"返回空"的示例**。LLM 不确定时倾向于"选点什么"而不是"返回空"——因为训练数据里"返回空"的样本少。

`filter_metric_info.prompt` 第 43-44 行虽然有示例二（返回空数组），但只给了一个，且场景太简单（"在职实习生有哪些"→ `[]`）。

实际复杂场景："上个月华北的销售额和订单数对比" → 指标应该选"GMV"和"订单数"，还是只选"GMV"？**没有这种边界 case 示例**。

#### 问题 4：关键词扩展 prompt 的示例和实际业务不匹配

`extend_keywords_for_column_recall.prompt` 的示例：

```
用户问题：
最近三个月在职实习生的转正情况如何？

输出：
[
  "员工身份类型",
  "员工在职状态",
  "转正状态",
  "转正日期",
  "入职日期",
  "统计日期"
]
```

**但实际业务是电商**（fact_order, dim_customer, dim_product, dim_region）——示例是 HR 场景的。LLM 看到这个示例，可能被误导去扩展 HR 相关字段。

#### 问题 5：没有 prompt 版本管理

7 个 prompt 是 `.prompt` 文本文件，**没有版本号**。改了 prompt 后：

- 无法知道这次改动的效果（没有 A/B 对比）
- 无法回滚到上一个版本（没有 git history 以外的版本记录）
- 无法知道当前 prompt 的"准确率"是多少（没有 baseline）

### 影响面

| 场景              | 发生概率 | 后果              |
| --------------- | ---- | --------------- |
| SQL 风格不一致       | 高    | 结果列名混乱，前端展示不稳定  |
| correct_sql 改语义 | 中    | SQL 执行成功但结果错误   |
| filter 不返回空     | 中    | 冗余字段进入 SQL 生成   |
| 示例和业务不匹配        | 高    | LLM 被误导扩展无关字段   |
| 改 prompt 无法对比   | 高    | 优化靠玄学，不知道变好还是变差 |

### 方案

**1. 给 generate_sql.prompt 加 few-shot 示例（电商业务相关）**

```
【示例 1：单表聚合】
用户问题：华北的销售额
上下文：
- 表：fact_order（字段：order_amount, order_date, region_id）
- 表：dim_region（字段：region_id, region_name）
SQL：
SELECT SUM(fact_order.order_amount) AS 销售额
FROM fact_order
JOIN dim_region ON fact_order.region_id = dim_region.region_id
WHERE dim_region.region_name = '华北'

【示例 2：多表分组】
用户问题：各品类的销量
上下文：
- 表：fact_order（字段：order_quantity, product_id）
- 表：dim_product（字段：product_id, category_name）
SQL：
SELECT dim_product.category_name AS 品类,
       SUM(fact_order.order_quantity) AS 销量
FROM fact_order
JOIN dim_product ON fact_order.product_id = dim_product.product_id
GROUP BY dim_product.category_name

【示例 3：时间过滤】
用户问题：最近7天的销量
上下文：
- 表：fact_order（字段：order_quantity, order_date）
- 当前日期：2026-07-09
SQL：
SELECT SUM(fact_order.order_quantity) AS 销量
FROM fact_order
WHERE fact_order.order_date >= DATE_SUB('2026-07-09', INTERVAL 7 DAY)
```

**2. 给 correct_sql.prompt 加修正示例**

```
【示例】
原 SQL：
SELECT SUM(order_amount) FROM fact_order JOIN dim_region ON region_id = region_id WHERE region_name = '华北'
错误信息：Column 'region_id' in on clause is ambiguous
修正后 SQL：
SELECT SUM(fact_order.order_amount) AS 销售额
FROM fact_order
JOIN dim_region ON fact_order.region_id = dim_region.region_id
WHERE dim_region.region_name = '华北'
说明：只修正了 ambiguous 列名问题，保留了原始的聚合和过滤语义。
```

**3. 把关键词扩展的示例改成电商场景**

```
示例：
用户问题：
华北上个月的销售额是多少？

输出：
[
  "地区名称",
  "订单金额",
  "订单日期"
]
```

**4. 加 prompt 版本管理**

```python
# app/prompt/prompt_loader.py 改动
def load_prompt(name: str, version: str = "v1") -> str:
    """加载指定版本的 prompt"""
    path = Path(__file__).parent.parent.parent / "prompts" / f"{name}.{version}.prompt"
    if not path.exists():
        # 兼容：fallback 到无版本号文件
        path = Path(__file__).parent.parent.parent / "prompts" / f"{name}.prompt"
    return path.read_text(encoding="utf-8")
```

```yaml
# conf/app_config.yaml
prompt:
  versions:
    generate_sql: v2          # 用 v2 版本（加了 few-shot 的）
    correct_sql: v2
    extend_keywords_for_column_recall: v2
```

### 验收标准

- [ ] generate_sql prompt 包含至少 3 个电商场景的 few-shot 示例
- [ ] correct_sql prompt 包含至少 1 个修正示例
- [ ] 关键词扩展的示例是电商场景，不是 HR 场景
- [ ] prompt 支持版本管理，改了能回滚
- [ ] 用 eval 数据集跑一次，加了 few-shot 后 SQL 生成成功率提升

---

## 刀 5：多轮对话的上下文污染——enhanced_query 直接塞进 state

### 现状

`app/services/query_service.py` 第 63-72 行：

```python
# ⭐ L1 检索：拿历史对话
history = get_history(session_id, max_count=3)

# ⭐ L3 拼接：把历史 + 当前问题 拼成结构化 Prompt
enhanced_query = build_prompt(query, history)

state = DataAgentState(query=enhanced_query)
```

`app/services/prompt_builder.py` 第 52-73 行的 `build_prompt`：

```python
def build_prompt(query: str, history: list) -> str:
    history_text = format_history(history)
    if is_followup_query(query):
        task_hint = "这是一个追问，请结合历史对话理解用户真正想查询的内容。"
    else:
        task_hint = "这是一个新问题。"
    prompt = f"""
【对话历史】
{history_text}

【当前问题】
{query}

【任务类型】
{task_hint}
"""
    return prompt
```

**这个拼接后的字符串直接作为 `state["query"]` 传给整个 LangGraph 链路。**

### 问题

#### 问题 1：extract_keywords 对拼接字符串做 jieba 分词，历史对话的词混入关键词

`extract_keywords` 节点第 24 行：

```python
query = state["query"]  # 这是 enhanced_query，不是用户原始问题
keywords = jieba.analyse.extract_tags(query, allowPOS=allow_pos)
```

如果 `state["query"]` 是：

```
【对话历史】
用户:上个月华东的销售额是多少
助手:[{'地区': '华东', '销售额': 120000}]

【当前问题】
换成华北呢

【任务类型】
这是一个追问，请结合历史对话理解用户真正想查询的内容。
```

jieba 会抽出：`["对话历史", "上个月", "华东", "销售额", "助手", "地区", "当前问题", "任务类型", "追问", "华北", "历史对话"]`

**历史对话里的"华东"、"上个月"、"销售额"混入了关键词列表**，导致：

- `recall_column` 用这些词去 Qdrant 检索 → 召回到"华东"相关的字段（但当前问题问的是"华北"）
- `recall_value` 用这些词去 ES 检索 → 召回到"华东"这个值（但当前问题问的是"华北"）
- `recall_metric` 用这些词去检索 → 召回到"销售额"指标（这个碰巧是对的）

**结果是召回结果被历史话题污染**——用户问"换成华北"，但召回结果里混进了"华东"相关的内容。

#### 问题 2：recall 节点用污染的 query 做 LLM 扩展

`recall_column` 第 46 行：

```python
result = await chain.ainvoke({"query": query})  # query 是 enhanced_query
```

LLM 收到的是：

```
【对话历史】用户:上个月华东的销售额是多少 助手:xxx 【当前问题】换成华北呢 【任务类型】这是一个追问...
```

LLM 扩展关键词时，会被历史对话里的"华东"、"上个月"带偏，可能扩展出：

```json
["华东地区", "华北地区", "上月销售额", "本月销售额"]
```

这些扩展词进一步污染召回。

#### 问题 3：generate_sql 的 prompt 里 query 是拼接字符串

`generate_sql` 节点第 33 行：

```python
query = state["query"]  # enhanced_query
```

prompt 里 `用户查询如下：{query}` 实际是：

```
用户查询如下：
【对话历史】
用户:上个月华东的销售额是多少
助手:[{'地区': '华东', '销售额': 120000}]

【当前问题】
换成华北呢

【任务类型】
这是一个追问，请结合历史对话理解用户真正想查询的内容。
```

**问题**：

- LLM 需要自己从这段拼接文本里提取"真正的问题是什么"——这是在做 query understanding，但 prompt 没有明确要求它做这件事
- 如果 LLM 理解错了（比如以为用户问的是"华东"），生成的 SQL 就是错的
- 拼接文本里的 JSON 格式（`[{'地区': '华东', '销售额': 120000}]`）可能被 LLM 当成要执行的指令

#### 问题 4：is_followup_query 的误判加剧了污染

`is_followup_query` 用关键词匹配（这在既有 RFC 痛点 6 已经说了），但**即使修好了**，问题依然存在：

假设 `is_followup_query` 正确判断"换成华北呢"是追问，`build_prompt` 生成 enhanced_query。但 enhanced_query 这个**整体字符串**被传给所有节点，**每个节点都受到污染**。

**根因**：`build_prompt` 不应该把拼接后的字符串作为 `state["query"]`，而应该：

- `state["query"]` 只放用户当前问题
- `state["history"]` 单独存历史对话
- 需要历史的节点（generate_sql, correct_sql）自己从 state 里取

### 影响面

| 场景                    | 发生概率     | 后果                                 |
| --------------------- | -------- | ---------------------------------- |
| 历史话题混入关键词             | 高（追问时必现） | 召回到上一轮的字段/取值，污染上下文                 |
| LLM 扩展被历史带偏           | 高        | 扩展出历史话题相关的关键词                      |
| SQL 生成 LLM 需要自己解析拼接文本 | 高        | 理解错了就是语义错误                         |
| 新问题也被污染               | 中        | 即使不是追问，enhanced_query 仍带【对话历史】等元数据 |

### 方案

**把 history 从 query 里拆出来，单独存 state**

```python
# state.py 改动
class DataAgentState(TypedDict):
    query: str           # ← 只放用户当前问题，纯净的
    history: list        # ← 新增，单独存历史对话
    intent: str          # ← 新增，配合刀 1 的意图识别
    keywords: list[str]
    ...
```

```python
# query_service.py 改动
async def query(self, query: str, session_id: str = "default"):
    history = await get_history(session_id, max_count=3)

    state = DataAgentState(
        query=query,           # ← 纯净的当前问题
        history=history,      # ← 历史单独存
    )
    ...
```

```python
# extract_keywords.py 改动
query = state["query"]  # 纯净的，不带历史
keywords = jieba.analyse.extract_tags(query, allowPOS=allow_pos)
```

```python
# recall_column.py / recall_value.py / recall_metric.py 改动
query = state["query"]  # 纯净的
```

```python
# generate_sql.py 改动：需要历史时才取
query = state["query"]
history = state.get("history", [])

# 在 prompt 里单独渲染历史
prompt = PromptTemplate(
    template=load_prompt("generate_sql"),
    input_variables=["table_infos", "metric_infos", "date_info", "db_info", "query", "history"],
)
result = await chain.ainvoke({
    ...
    "query": query,          # 纯净的当前问题
    "history": format_history(history) if history else "无",
})
```

```python
# generate_sql.prompt 改动
【对话历史】
{history}

【当前问题】
{query}

【上下文信息】
可用数据表信息如下：
{table_infos}
...
```

```python
# prompt_builder.py 的 build_prompt 不再需要
# 历史和 query 在 state 里分开存，节点自己决定要不要用历史
```

### 验收标准

- [ ] `state["query"]` 只包含用户当前问题，不含【对话历史】等元数据
- [ ] `extract_keywords` 分词结果不含历史对话的词
- [ ] `recall_*` 节点的 LLM 扩展只基于当前问题
- [ ] `generate_sql` 的 prompt 里历史和当前问题分开渲染
- [ ] 追问"换成华北"时，召回结果不混入"华东"相关内容

---

## 刀 6：没有结果校验——SQL 执行成功 ≠ 结果正确

### 现状

`app/agent/nodes/run_sql.py` 第 234-270 行：

```python
result = await dw_mysql_repository.run(sql)

if len(result) == 0:
    query = state["query"]
    warning_msg = f"查询'{query}'返回0行数据，可能是查询条件过于严格或者筛选条件有误"
    writer({"type": "warning", "message": warning_msg})

writer({"type": "progress", "step": step, "status": "success"})
writer({"type": "result", "data": result})
```

**唯一的"校验"是行数是否为 0。** SQL 执行成功就直接返回结果给用户。

### 问题

#### 问题 1：SQL 语法正确 ≠ 语义正确

LLM 可能生成以下"语法对、语义错"的 SQL：

| 用户问题    | LLM 生成的 SQL                                                                  | 问题                                        |
| ------- | ---------------------------------------------------------------------------- | ----------------------------------------- |
| "华北销售额" | `SELECT COUNT(*) FROM fact_order JOIN dim_region ... WHERE region_name='华北'` | 用了 COUNT 而不是 SUM                          |
| "各品类销量" | `SELECT category_name, SUM(order_amount) FROM ...`                           | 用了 order_amount（金额）而不是 order_quantity（数量） |
| "上月销售额" | `SELECT SUM(order_amount) FROM fact_order WHERE order_date >= '2026-07-01'`  | 日期算错（本月而不是上月）                             |
| "华北销售额" | `SELECT SUM(order_amount) FROM fact_order WHERE region_name='华东'`            | WHERE 条件写错地区                              |

**这些 SQL 都能执行成功，但结果是错的。用户看到的是一个数字，不知道它是错的。**

#### 问题 2：没有结果合理性检查

- 销售额返回负数？→ 可能是退款字段混入，但没人检查
- 销售额返回 999999999？→ 可能是 JOIN 条件缺失导致笛卡尔积，但没人检查
- 订单数返回 0 但表里有数据？→ 可能是 WHERE 条件过严，但只在 run_sql 里 warning 了一下

#### 问题 3：没有"SQL vs 历史成功案例"的对照

如果有一个历史成功 SQL 库（"这个问题以前生成过正确的 SQL"），可以对照当前生成的 SQL 是否结构相似。但当前架构没有这个能力——每次都是 LLM 从零生成。

#### 问题 4：没有"结果 vs 用户预期"的反馈闭环

用户问"华北上个月销售额"，SQL 返回 `120000`。用户可能：

- 觉得对：关闭对话
- 觉得不对：换种问法再问

但系统**不记录"用户是否满意"**，无法收集 bad case 来优化。没有反馈按钮，没有"这个结果对吗"的判断。

#### 问题 5：run_sql 的空结果 warning 太弱

```python
if len(result) == 0:
    writer({"type": "warning", "message": warning_msg})
```

只是在前端显示一个 warning，**仍然继续返回"成功"状态和空结果**。用户看到的是"执行成功 ✅"和一个空表格——**混淆了"查询成功但无数据"和"查询失败"**。

### 影响面

| 场景           | 发生概率 | 后果                 |
| ------------ | ---- | ------------------ |
| SUM 写成 COUNT | 中    | 结果数字类型对但语义错        |
| WHERE 条件写错地区 | 中    | 查到了别的地区的数据         |
| 日期算错         | 高    | 查到了错误时间段的数据        |
| JOIN 条件缺失    | 中    | 笛卡尔积，结果数字异常大       |
| 空结果          | 中    | 用户以为没数据，实际是 SQL 错了 |

### 方案

**1. 结果合理性校验（规则引擎）**

```python
# app/agent/nodes/validate_result.py（新增节点，在 run_sql 之后）
async def validate_result(state, runtime):
    """校验 SQL 执行结果的合理性"""
    result = state.get("query_result", [])
    query = state["query"]
    sql = state["sql"]

    issues = []

    # 1. 空结果
    if len(result) == 0:
        issues.append({"level": "warning", "type": "empty_result", "message": "查询返回0行数据"})

    # 2. 结果行数异常多（可能笛卡尔积）
    if len(result) > 10000:
        issues.append({"level": "warning", "type": "too_many_rows", "message": f"返回{len(result)}行，可能存在JOIN条件缺失"})

    # 3. 数值字段异常（负数、过大）
    for row in result[:10]:  # 只检查前10行
        for key, value in row.items():
            if isinstance(value, (int, float)):
                if value < 0:
                    issues.append({"level": "warning", "type": "negative_value", "message": f"字段{key}存在负值{value}"})
                if value > 100000000:  # 超过1亿
                    issues.append({"level": "warning", "type": "abnormal_value", "message": f"字段{key}值{value}异常大"})

    # 4. SQL 语义检查（用 LLM 做轻量校验）
    if not issues:  # 规则检查通过后，再用 LLM 做一次语义校验
        semantic_check = await _check_semantic(query, sql, result[:3])
        if semantic_check.get("suspicious"):
            issues.append({"level": "warning", "type": "semantic_suspicious", "message": semantic_check["reason"]})

    return {"validation_issues": issues}
```

**2. SQL 语义校验 prompt**（轻量，只传 3 行结果）

```
角色：
你是一个 SQL 结果审核员。判断以下 SQL 的执行结果是否符合用户问题的语义。

用户问题：{query}
SQL：{sql}
结果（前3行）：{result}

检查要点：
1. 聚合函数是否正确（SUM vs COUNT vs AVG）
2. WHERE 条件的值是否和用户问题一致
3. 时间范围是否正确
4. 结果数量是否合理

输出：
- 如果结果合理：{{"suspicious": false}}
- 如果可疑：{{"suspicious": true, "reason": "具体原因"}}
```

**3. 图结构改动**

```
run_sql → validate_result → END
```

**4. 空结果时返回 error 而不是 success**

```python
# run_sql.py 改动
if len(result) == 0:
    writer({"type": "progress", "step": step, "status": "warning"})
    writer({"type": "result", "data": result, "warning": "查询返回0行数据，可能是条件有误"})
    return  # 不再标记为 success
```

**5. （长期）加反馈闭环**

```python
# app/api/routes/feedback.py（新增）
@router.post("/api/feedback")
async def submit_feedback(request: Request):
    """用户对查询结果的反馈"""
    data = await request.json()
    # 存到 feedback 表：session_id, query, sql, result, feedback(thumbs_up/down)
    ...
```

前端在结果展示后加 👍👎 按钮，反馈数据用于：

- 收集 bad case
- 优化 prompt（用 bad case 做 few-shot 负例）
- 评估系统真实准确率

### 验收标准

- [ ] SQL 执行后有结果合理性校验节点
- [ ] 空结果时返回 warning 而不是 success
- [ ] 结果行数异常多时能检测到（笛卡尔积防护）
- [ ] 数值异常（负数、过大）时能检测到
- [ ] （长期）用户可对结果反馈 👍/👎
- [ ] bad case 被收集用于优化 prompt

---

## 优先级排序

| 优先级 | 刀                  | 改动量                                              | 影响面                 |
| --- | ------------------ | ------------------------------------------------ | ------------------- |
| P0  | 5. 上下文污染           | 小（state.py + query_service.py + 3 个节点取 query 改动） | 防止追问时召回污染，影响所有多轮对话  |
| P0  | 1. 意图识别            | 中（新增 classify_and_rewrite 节点 + 2 个 prompt）       | 防止闲聊/元数据查询走 SQL 链路  |
| P1  | 4. Prompt few-shot | 中（改 7 个 prompt + prompt_loader 版本管理）             | 提升 SQL 生成稳定性        |
| P1  | 2. 多跳召回            | 中（改 recall prompt + 新增 recall_columns_by_metric） | 提升复杂问题召回率           |
| P2  | 6. 结果校验            | 中（新增 validate_result 节点 + prompt）                | 防止语义错误的 SQL 被当成正确结果 |
| P2  | 3. Rerank          | 大（新增 rerank 节点 + cross-encoder 依赖）               | 提升召回精度，减少 filter 噪声 |

---

## 总结

这 6 刀和既有 RFC 的 6 刀**完全不重叠**——既有 RFC 修的是"后端工程的坑"（容错、并发、可观测性），本 RFC 修的是"AI 应用的坑"（意图理解、召回策略、Prompt 工程、上下文管理、结果校验）。

两者关系：

- **既有 RFC 是"让系统不崩"**（工程健壮性）
- **本 RFC 是"让系统答对"**（AI 准确性）

建议先修本 RFC 的 P0（刀 5 上下文污染 + 刀 1 意图识别），因为它们影响**每一次多轮对话**——不修的话，追问场景下召回和生成都会被污染，后端再健壮也救不回来。

每个改动都应配套写 eval 用例，用 `tests/eval_data.py` 的 20 条测试集验证效果变化。

---

# 第二轮 Grill-me 审查 — 新增 14 刀（刀 7~20）

> **审查日期**: 2026-07-10
> **审查方式**: 第二轮 grill-me 逐刀追问 + 代码逐行深度审阅
> **审查范围**: 意图分类异常策略、时间处理、元数据查询、N+1 查询、客户端容错、API 安全、部署工程化、测试覆盖、配置安全、前端一致性

---

## 刀 7：AOV 指标定义写错了字段（Bug）

### 现状

`conf/meta_config.yaml` 第 172-176 行：

```yaml
- name: AOV
  description: 全称Average Order Value，表示所有订单的成交金额平均值。
  relevant_columns:
    - fact_order.order_quantity   # ← 错：数量
```

AOV = 总成交金额 / 总订单数，应该依赖 `fact_order.order_amount`，不是 `order_quantity`。

### 影响

- `recall_metric` 召回 AOV → `merge` 补齐 `order_quantity` → `generate_sql` 生成 `AVG(order_quantity)`
- 用户问"平均订单金额"，拿到的是"平均订单件数"

### 处理

用户确认：**是 bug，直接改**。

### 验收

- [ ] AOV 的 `relevant_columns` 改成 `fact_order.order_amount`

---

## 刀 8：时区陷阱 — date.today() 在 Docker 容器中算错一天

### 现状

`add_extra_context.py` 第 27 行 和 `rewrite_query.py` 第 51 行：

```python
today = date.today()  # 取容器系统时区
```

Docker 容器默认 UTC，北京凌晨 0-8 点时 `date.today()` 仍是前一天。

### 影响

| 真实时间（北京） | 容器内日期 | 用户问"今天" | LLM 拿到的日期 |
|---|---|---|---|
| 7月10日 01:00 | 7月9日 | "今天的销售额" | 2026-07-09 |
| 1月1日 01:00 | 12月31日 | "今年的GMV" | 2025-12-31 |

### 处理

用户确认：**Docker 加 TZ=Asia/Shanghai**。代码层面不改。

### 验收

- [ ] docker-compose.yaml 所有服务加 `environment: TZ=Asia/Shanghai`

---

## 刀 9：rewrite_query.py 12 月要炸

### 现状

`rewrite_query.py` 第 59-62 行：

```python
if today.month == 12:
    end = date(today.year, today.month - 1, 31)  # 11月31日 → ValueError
```

11 月只有 30 天，`date(2026, 11, 31)` 直接抛异常。`except` 会吞掉异常用原始 query 继续，但"上一个自然月"没被解析成日期范围。

### 处理

用户确认：**修复 12 月 bug**。12 月分支用"下月第 1 天减 1 天"统一逻辑。

### 修复代码

```python
if today.month == 12:
    start = date(today.year, 11, 1)
    end = date(today.year, 12, 1) - timedelta(days=1)  # 11月30日
```

### 验收

- [ ] 12 月时"上一个自然月"解析为 11月1日~11月30日

---

## 刀 10：API 层完全裸奔

### 现状

1. `QuerySchema` 的 `query: str` 无 `max_length` 限制
2. 无 IP 速率限制
3. 无认证/鉴权
4. CORS `allow_origins=["*"]` + `allow_credentials=True`

### 处理

用户确认：**加 max_length + 简单 IP 限流**。

### 方案

```python
# query_schema.py
class QuerySchema(BaseModel):
    query: str = Field(max_length=500, description="用户查询文本")

# main.py 或 middleware
from slowapi import Limiter
from slowapi.util import get_remote_address
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

# query_router.py
@router.post("/api/query")
@limiter.limit("10/minute")
async def query(request: Request, body: QuerySchema):
    ...
```

### 验收

- [ ] 超过 500 字符的请求被拒绝（422）
- [ ] 单 IP 超过 10 次/分钟被限流（429）

---

## 刀 11：LLM 无超时 — 一个慢请求拖垮全部

### 现状

`app/agent/llm.py` 初始化 LLM 时无 `request_timeout`。DeepSeek API 慢响应时 async 事件循环全部阻塞。

### 处理

用户确认：**加 request_timeout=30s**。

### 方案

```python
llm = init_chat_model(
    model=model_name,
    model_provider="openai",
    api_key=api_key,
    base_url=base_url,
    temperature=0,
    request_timeout=30,   # 新增
    max_tokens=2000,      # 新增
)
```

### 验收

- [ ] LLM 调用超过 30s 自动超时并抛异常
- [ ] 链路中 LLM 超时时正确降级/报错给用户

---

## 刀 12：respond_metadata 暴力子串匹配

### 现状

`respond_metadata.py` 遍历表名做 `name in query` 子串匹配。问题：
1. 多表查询只命中第 1 个
2. 表名互相是子串时命中错误的表
3. 指标名常见词误命中

### 处理

用户确认：**改成 jieba 分词后匹配**。

### 方案

```python
import jieba

def _extract_table_name(query, table_names):
    words = set(jieba.cut(query))
    matched = [name for name in table_names if name in words]
    return matched[0] if matched else None
```

### 验收

- [ ] "dim_product 和 dim_customer 有哪些字段" 能识别多个表名
- [ ] "我要看数据的实际情况" 不误命中指标名

---

## 刀 13：Qdrant / ES 客户端无重连

### 现状

客户端只创建一次，容器重启后连接池失效，无自动重连。

### 处理

用户确认：**在 recall 节点 catch 后重试 1 次**。

### 方案

```python
# recall_column.py 示例
try:
    results = await repository.search(embedding)
except Exception as e:
    logger.warning(f"Qdrant 检索失败，重试一次: {e}")
    await asyncio.sleep(1)  # 短暂等待
    results = await repository.search(embedding)  # 重试一次
```

### 验收

- [ ] Qdrant 重启后，recall 节点第 2 次检索能恢复

---

## 刀 14：filter 节点对 LLM 输出无 schema 校验

### 现状

`filter_table.py` 假设 LLM 输出 dict，`filter_metric.py` 假设输出 list。LLM 输出 null/不同结构时 TypeError 崩掉链路。

### 处理

用户确认：**加 isinstance 检查 + 降级**。

### 方案

```python
# filter_table.py
if not isinstance(result, dict) or not result:
    # LLM 输出格式不符合预期，降级：直接返回所有候选
    writer({"type": "warning", "message": "filter_table LLM 输出格式异常，降级返回全候选"})
    return {"table_infos": table_infos}

# filter_metric.py
if not isinstance(result, list):
    writer({"type": "warning", "message": "filter_metric LLM 输出格式异常，降级返回空"})
    return {"metric_infos": []}
```

### 验收

- [ ] LLM 输出 null 时不崩，降级返回原始候选
- [ ] LLM 输出错误结构时有 warning 日志

---

## 刀 15：merge_retrieved_info 串行 N+1 查询

### 现状

为每个指标依赖字段、每个字段取值、每张表的主外键都**逐个**查数据库。20+ 次串行 DB 往返。

### 处理

用户确认：**改成批量 IN 查询**。

### 方案

```python
# 不再逐个查
# for col_id in missing_column_ids:
#     col = await meta_mysql_repository.get_column_info_by_id(col_id)

# 改成批量
missing_ids = list(missing_column_ids)
columns = await meta_mysql_repository.get_column_infos_by_ids(missing_ids)
for col in columns:
    retrieved_column_infos_map[col.id] = col
```

Repository 新增批量方法：

```python
async def get_column_infos_by_ids(self, ids: list[str]) -> list[ColumnInfo]:
    stmt = select(ColumnInfoEntity).where(ColumnInfoEntity.id.in_(ids))
    result = await self.session.execute(stmt)
    return [self.mapper.to_domain(entity) for entity in result.scalars().all()]
```

### 验收

- [ ] merge 节点 DB 查询次数从 20+ 降到 3-4 次

---

## 刀 16：extract_keywords 词性覆盖不足 + 无业务词典

### 现状

1. `allowPOS` 缺 `m`（数词）和 `mq`（量词）—— "3月"、"第一季度" 中的数字被丢弃
2. 无 `jieba.load_userdict()` —— GMV/AOV/dim_product 被错误切分

### 处理

用户确认：**加业务词典 + 补数词词性**。

### 方案

```python
# extract_keywords.py
# 1. 补数词词性
allow_pos = {"n", "nr", "ns", "nt", "nz", "eng", "m", "mq", "vn"}

# 2. 加载业务词典
import jieba
jieba.initialize()
for word in ["GMV", "AOV", "dim_region", "dim_customer", "dim_product",
             "dim_date", "fact_order", "region_name", "member_level",
             "order_amount", "order_quantity", "customer_name"]:
    jieba.add_word(word)
```

### 验收

- [ ] "GMV" 被整词识别，不被切分
- [ ] "3月" 中 "3" 被保留在关键词列表

---

## 刀 17：前端"新会话"不清后端

### 现状

`App.tsx` 第 158 行 `clearConversation` 只清前端 `messages`，不通知后端清 Redis。后端历史仍在，下次发消息时 LLM 拿到旧历史。

### 处理

用户确认：**加一个清会话 API**。

### 方案

```python
# query_router.py 新增
@router.post("/api/clear-session/{session_id}")
async def clear_session(session_id: str):
    await session_store.clear_session(session_id)
    return {"success": True}
```

```typescript
// App.tsx clearConversation 改动
const clearConversation = async () => {
  const sessionId = getCookie("session_id");
  if (sessionId) {
    await fetch(`/api/clear-session/${sessionId}`, { method: "POST" });
  }
  setMessages([]);
};
```

### 验收

- [ ] 点击"新会话"后，后端 Redis 历史被清空
- [ ] 下一条消息不携带旧历史上下文

---

## 刀 18：配置明文凭证

### 现状

- `app_config.yaml` 中 DB 密码 `dili123` 明文
- `docker-compose.yaml` 中 `MYSQL_ROOT_PASSWORD: dili123` 明文

### 处理

用户确认：**改成 .env 引用**。

### 方案

```yaml
# app_config.yaml
database:
  password: ${oc.env:DB_PASSWORD}   # 改成环境变量引用
```

```yaml
# docker-compose.yaml
mysql:
  environment:
    MYSQL_ROOT_PASSWORD: ${DB_PASSWORD}  # 从 .env 读取
    TZ: Asia/Shanghai
```

```env
# .env 新增
DB_PASSWORD=dili123
```

### 验收

- [ ] git 跟踪的文件中不含明文密码

---

## 刀 19：start_app.py 错误路径 + 无 Dockerfile

### 现状

1. `start_app.py` 第 9 行指向 `d:\\shopkeeper-agent-main\\interview-simulator\\backend`（另一个项目路径）
2. docker/ 目录无应用 Dockerfile，生产部署只能裸跑

### 处理

用户确认：**删 start_app.py + 写 Dockerfile**。

### 方案

删除 `start_app.py`。

新增 `docker/Dockerfile`：

```dockerfile
FROM python:3.13-slim
WORKDIR /app
RUN pip install uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY . .
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 验收

- [ ] `start_app.py` 被删除
- [ ] `docker build -f docker/Dockerfile .` 能构建成功

---

## 刀 20：测试覆盖严重不足 — 关键节点 0 测试

### 现状

`tests/` 只有 3 个真测试（sql_safety / session_store / scheduler）。所有 agent 节点 0 测试。整条 LangGraph 链路无端到端测试。

### 处理

用户确认：**加端到端测试**。

### 方案

```python
# tests/test_e2e_agent.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.mark.asyncio
async def test_e2e_data_query_flow():
    """用 mock 数据跑整条 LangGraph 链路"""
    # mock embedding, qdrant, es, llm, mysql
    # 构造 state -> 跑 graph.astream -> 断言最终 state 包含正确的 sql 和 result

@pytest.mark.asyncio
async def test_e2e_chitchat_short_circuit():
    """闲聊意图应该短路，不走 RAG"""

@pytest.mark.asyncio
async def test_e2e_empty_recall_graceful():
    """召回为空时应该优雅降级"""

@pytest.mark.asyncio
async def test_e2e_filter_llm_bad_output():
    """filter LLM 输出异常格式时不应该崩"""
```

### 验收

- [ ] `pytest tests/test_e2e_agent.py` 通过
- [ ] 覆盖正常 data_query、闲聊、召回为空、filter 异常 4 个场景

---

## 优先级排序（全 20 刀）

| 优先级 | 刀 | 改动量 | 影响面 |
|---|---|---|---|
| **Bug** | 7. AOV 指标定义错误 | 极小（改 1 行 YAML） | 影响所有 AOV 查询 |
| **Bug** | 9. 12月解析炸掉 | 极小（改 3 行代码） | 12月所有"上月"查询 |
| P0 | 8. 时区 | 小（docker-compose 加 TZ） | 凌晨时段所有查询 |
| P0 | 11. LLM 无超时 | 小（llm.py 加 2 个参数） | 高并发时全阻塞 |
| P0 | 10. API 无限流 | 小（加 middleware + max_length） | 公网暴露即被攻击 |
| P0 | 18. 明文凭证 | 小（改 .env 引用） | 密码泄露 |
| P1 | 14. filter 无 schema 校验 | 小（2 个节点加 isinstance） | LLM 输出异常时链路崩 |
| P1 | 15. N+1 查询 | 中（repository 加批量方法） | merge 节点性能 |
| P1 | 16. extract_keywords 词典+词性 | 小（extract_keywords.py 改） | 召回准确率 |
| P1 | 12. respond_metadata 匹配 | 小（改匹配逻辑） | 元数据查询准确率 |
| P1 | 17. 前端清会话 | 小（新增 1 个 API + 前端调） | 多轮对话一致性 |
| P2 | 13. Qdrant/ES 重试 | 小（recall 节点加 retry） | 容器重启后恢复 |
| P2 | 19. 部署工程化 | 中（删脚本 + 写 Dockerfile） | 生产部署 |
| P2 | 20. 端到端测试 | 中（写 e2e test） | 回归保障 |
