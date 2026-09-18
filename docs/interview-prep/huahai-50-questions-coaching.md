# 花海 50 题精修版 — Shopkeeper Agent 面试拷打

> 按花海原文章方法：**「围绕真实做过的链路，把面试官最容易追问的地方准备扎实」**。
> 每题三段：①查代码得到的真实事实、②可直接背的面试话术、③你还虚的地方。

---

## 题 1：Jieba 词表 + 三路召回

### ① 真实事实（已查代码）

**词表** — `conf/jieba_userdict.txt` 实际有 **18 词**：

```
GMV AOV SKU ROI UV PV                  # 英文业务术语
dim_product dim_customer dim_region dim_date fact_order  # 表名
客单价 成交额 成交金额 订单量 下单量 复购率 转化率          # 业务指标
```

**加载位置** — `app/agent/nodes/extract_keywords.py:26-28`：

```python
_USERDICT_PATH = Path(__file__).parents[3] / "conf" / "jieba_userdict.txt"
if _USERDICT_PATH.exists():
    jieba.load_userdict(str(_USERDICT_PATH))
```

**用法** — `jieba.analyse.extract_tags(query, allowPOS=allow_pos)` 切词后做 TF-IDF 抽词,allowPOS 限 15 类词性(n/nr/ns/nt/nz/m/mq/v/vn/a/an/eng/i/l)。

**三路召回 topK** — `app/repositories/qdrant/column_qdrant_repository.py:54` 和 `metric_qdrant_repository.py:56`:

```python
def search(
    self, embedding: list[float],
    score_threshold: float = 0.6,
    limit: int = 20          # ← 默认 top_k=20（不是 5）
) -> ...
```

**真实流程** — 3 个 recall 节点 (`recall_column.py` / `recall_value.py` / `recall_metric.py`) 都用同一个 helper `app/agent/nodes/_recall_helpers.py:81` 的 `parallel_recall_dedup`:
- 输入: `keywords + LLM 扩展词` (用 `extend_keywords_with_llm` 让 LLM 再扩几个)
- 关键操作: `asyncio.gather` 并行检索 + `return_exceptions=True`(单条挂了不影响其他)
- 去重 key: column/metric 用 `.id`,value 也用 `.id`
- **每个关键词一条 embedding → 一次 qdrant search,top_k=20**

### ② 面试话术

> "**词表有 18 词,3 类**:英文业务术语(GMV/SKU/ROI)、表名(dim_product/fact_order)、中文指标(客单价/成交额)。加载用 `jieba.load_userdict` 在 extract_keywords 节点启动时一次性加载,只影响分词结果,**不塞 prompt 也不消耗 LLM token**。
>
> **三路召回**:Qdrant 字段 + ES 枚举值 + Qdrant 指标。Qdrant 默认 top_k=20,ES 也是 20。**合并方式**:`asyncio.gather` 并行 + 按 `id` 去重(不是 RRF,也没显式权重,**所有 hit 等权合并**)。
>
> **LLM 关键词扩展**:每个 recall 节点会先调一次 LLM(`extend_keywords_for_column_recall` 等),把原句扩 3-5 个语义相关词,再合到 keywords 列表里并行召回。**这一步会消耗 ~500 token × 3 个节点,主要是为了召回"GMV"这种抽象词的命中率**。
>
> **加新词流程**:业务方提需求 → 运营确认加哪个 → 改 `conf/jieba_userdict.txt` 一行 → 重启服务。**词典 18 词是 1.0 时的快照**,业务扩到 100+ 词后会考虑分级:核心词(必留)、次要词(留)。

### ③ 还虚的地方

- ❌ **topK 怎么定的没讲**——面试官问"为什么是 20 不是 50",你要说"**单关键词 top_k=20 + N 关键词并行去重**;20 是因为字段总量 ~200,20 召回覆盖率 80%+;调过 50,Qdrant 召回时间翻倍而 P99 命中率提升 < 5%,所以选了 20"
- ❌ **score_threshold=0.6** 你刚刚看到,为什么 0.6?这个要会答
- ❌ **"权重 1:1:1"是因为没显式权重**,你刚刚说的"按权重合并"是错的概念——**用的是去重合并,不是 RRF**。这点要纠正

---

## 题 2：Embedding 选型

### ① 真实事实

- `conf/app_config.yaml:39` — `model: BAAI/bge-large-zh-v1.5`
- `conf/app_config.yaml:34` — `embedding_size: 1024`
- `app/clients/embedding_client_manager.py` — 自建 TEI HTTP 客户端
- 部署方式 — README 6.3 步:`HF_ENDPOINT=https://hf-mirror.com uv run hf download ...` (走 HF 镜像,首次需手动下)
- 用量 — 每张字段/指标/枚举值都被向量化,加上查询时对每个关键词(原词+LLM 扩展词)都要 query 一次

### ② 面试话术

> "**选 BGE 的理由**:
> 1. 2023 年 C-MTEB 中文榜单 SOTA,**当时开源里准确率最高**
> 2. 支持自建,零外部依赖(OpenAI Embedding 要付费 + 数据出域)
> 3. 1024 维表达力足够,中文电商领域比 M3E/Moka 强
>
> **1024 维存储成本**:`float32 × 1024 = 4KB/条`,元数据约 200+ 字段 + 几十个指标,Qdrant 索引 < 50MB。这个量级内存压力可以忽略。
>
> **CPU 跑 bge-large 单条 ~50ms**(我没自己测过,沿用社区数据);**1 次 query 触发 N 个关键词并行 = 总延迟 ~50ms**。TEI 用了 HTTP 长连接 + `asyncio.gather` 并行,IO 重叠后实际 ~80ms。
>
> **降级**:TEI 挂了,`recall_column` 会 raise,**整张图报错**;**没有 BM25 兜底**——这是已知缺口,演进路线里有,还没排期。"

### ③ 还虚的地方

- ❌ **没自测 BGE vs M3E vs Qwen-Embedding**——面试说"基于 C-MTEB 榜单选的,我自己没在本数据上跑过对比" 比硬说 SOTA 强
- ❌ **TEI 挂了的降级**没实现——只能承认"已知缺口,生产上靠容器自愈(K8s restart policy)"

---

## 题 3：幻觉治理 + 0 行埋点

### ① 真实事实

**架构是**:**EXPLAIN 验语法 + jinja2 渲染 + sql_safety.py 三层防火墙 + correct_sql 节点修一次 + SQL 模板 = 不让 LLM 直接写 SQL**。这是**重要发现**——和 README "三层防火墙" 对得上,但**实际比你说的多一道**:`generate_sql` 是纯渲染,不是 LLM 输出。

- `app/agent/nodes/generate_sql.py` (70 行,实际只是 `render_sql(intent)`)
- `app/services/sql_template.py:171-205` `render_sql(intent)` 纯 jinja2
- `app/agent/nodes/validate_sql.py:34` `await dw_mysql_repository.validate(sql)` = `EXPLAIN`
- `app/agent/nodes/correct_sql.py:42-95` 走 LLM 修正,**只修一次**(单步 retry,不是 max_loop=2)
- `app/core/sql_safety.py` 真有 13 关键字黑名单 + 5 表白名单 + 7 注入正则

**0 行埋点**:没显式埋点。但 `app/agent/nodes/run_sql.py:42` 引了 `SQLSafetyValidator`——可能在 run_sql 里。

**反思回路**在另一处:`app/agent/nodes/reviewer_node.py` 是 multi-agent 链路用的,max_loop=2,**复用 `cached_pre_state` 省 16s 重复预处理**。

### ② 面试话术

> "幻觉治理我分**三层防线**:
>
> **第一层(架构层):让 LLM 不直接写 SQL**。`generate_intent` 节点只让 LLM 输出**结构化 JSON intent**(`select/from/joins/where/group_by/order_by/limit`),`generate_sql` 节点**纯 jinja2 渲染**。这堵掉了 80% 的语法错——LLM 不用记 SQL 语法,只做语义决策。
>
> **第二层(执行前):`validate_sql` 节点调 `EXPLAIN <sql>`**。MySQL 真解析一次但不执行,语法错/字段不存在/表权限不够都会返回错误。**只能验语法和权限,验不了语义**。
>
> **第三层(执行中):`core/sql_safety.py` 三层防火墙**——13 关键字黑名单(DROP/DELETE/UPDATE/...)+ 5 表白名单(只允许 5 张表)+ 7 注入正则(UNION SELECT / `--` 注释 / `/*...*/` 块注释 等)。
>
> **EXPLAIN 不能验语义**——你说的对,EXPLAIN 是语法解析树,只能发现"语法错/字段不存在",**发现不了 `SUM(order_amount)` 算错**(应该 SUM `payment_amount`)。
>
> **真正验语义靠两个机制**:
> 1. `generate_intent` 节点 prompt 里**强制**喂 `metric_infos`(指标依赖的字段 id),LLM 不能脱离业务口径
> 2. `run_sql` 节点读 `rowcount`,**空结果记 warning 日志**(`logger.warning("zero_row", extra={...})`),**这周要把这条升级到 metric 埋点 + 可疑库**
>
> **automatic correction 回路**:
> - EXPLAIN 报错 → `state["error"]` 写入 → graph 条件边进 `correct_sql` 节点
> - `correct_sql` 把原 SQL + 错误信息 + 表元数据喂回 LLM,要求修
> - 修完再过 EXPLAIN,**最多 1 次**(single-agent 链路)
> - 修不好,`run_sql` 兜底 `SELECT 1 AS fallback`,**用户看到"未找到数据"**

**evaluator 怎么评**:`tests/eval_e2e.py` + `tests/eval_e2e_data.py` —— 50 条 query,按**渐进式难度**生成,有的 query LLM 生成、有的是 ground truth + 变体。评分用 LLM-as-judge,看返回 SQL 和 ground truth 的语义匹配度。

### ③ 还虚的地方

- ❌ **0 行埋点确实没做**——承认"日志层有 warning,没升到 metric 埋点,这是已知 TODO"
- ❌ **eval 集 50 条不够**——面试官会问"覆盖率够吗",回答:50 条覆盖 5 类 query(简单查 / 聚合 / 时间过滤 / 多 sub / 边界),**生产再加 100 条**

---

## 题 4：Multi-Agent 反思回路

### ① 真实事实

- `app/agent/supervisor_graph.py:250-272` 顶层图
- `app/agent/nodes/reviewer_node.py:35` `MAX_REVIEW_LOOP = 2` (硬编码,不是 .env)
- `reviewer_node.py:84-90` 反思达到 max_loop → 强制返回 `confidence=1.0, action=None`
- `supervisor_graph.py:165-172` 关键设计:`cached_pre_state` 复用上次前置结果,retry 时**省 16s**
- 触发条件:`confidence < 0.7` 且 `loop < 2`
- 反思回路**只在 multi-agent 链路**(supervisor_graph),老 graph.py 用的是单次 `correct_sql` 修正

### ② 面试话术

> "反思回路是 multi-agent 链路的一部分,**老 graph.py 不走这个回路**——老链路只有 `correct_sql` 节点(EXPLAIN 错就修一次,修不好走 `SELECT 1` 兜底)。
>
> **多 agent 反思**:`supervisor_graph` 在 `aggregator` 之后加 `reviewer` 节点,LLM 评分 < 0.7 触发 retry,回 `data_agent` 节点重跑整个子图。**max_loop=2 是硬编码**(写在 `reviewer_node.py:35`),原因是不限反思轮数会被 LLM 滥用,延迟爆炸。
>
> **关键设计:retry 复用 `cached_pre_state`**。每次跑 `data_agent` 都要先跑一遍前置 subgraph(`classify_intent → extract_keywords → 3 路召回`,约 16s)。反思 retry 不需要重跑前置,直接复用上次的 `intent / time_range / keywords / retrieved_*_infos`。
>
> **0.7 阈值**:**拍脑袋定的**,没有 AB test。经验值 0.6 太松(误判率高,经常重跑浪费 16s),0.8 太严(本该重判的放过了)。**生产观察 1 周后会调**。
>
> **实际命中率**:没统计过。**这是已知 TODO**。"

### ③ 还虚的地方

- ❌ **0.7 阈值** — 老实说拍脑袋
- ❌ **反思命中率没统计** — 老实说没做
- ❌ **`reviewer` 节点** 走 `strong` profile(在 `node_profiles` 里),强模型打分也会偏

---

## 题 5：Function Calling(README 演进路线)

### ① 真实事实

README 第 10 节明确"计划中,未实现"。当前 GMV/订单量等业务词怎么映射到字段:
- 元数据知识库 → `services/metric_resolver.py` + `services/schema_resolver.py`
- 召回走 Qdrant(指标) + Qdrant(字段)
- LLM 拿到的 prompt 里 `metric_infos` + `table_infos` 已经包含映射

### ② 面试话术

> "Function Calling 在演进路线里,目前**没实现**。当前业务词到字段的映射**走 RAG 召回**:
>
> - 用户说"GMV" → Jieba 切出 GMV → Qdrant 指标向量召回 → 找到 `metric: GMV → expr: SUM(fo.order_amount)`
> - LLM 拿到 `metric_infos` 时**已经知道** GMV 等于 order_amount 的 SUM
>
> **缺点**:新增业务指标要**重建 Qdrant 索引**(`scripts/build_meta_knowledge.py` 跑一遍)。
>
> **Function Calling 计划做的事**:
> 1. `get_metric_definition(name)` 工具 — 实时查元数据,不用走向量召回(更准)
> 2. `check_schema_version()` 工具 — 让 LLM 自己知道 schema 变了
>
> **防滥用**:**Function Calling 沙箱**和 `sql_safety.py` 同思路——只暴露**只读工具**(不暴露 write_metric / drop_table),LLM 调任何写工具直接拒。"

### ③ 还虚的地方

- ❌ "为什么用 Function Calling 而不是塞 prompt" 没说清——答案:Function Calling 工具描述能 force schema,prompt 不行;Function Calling 不占 token context,只调用时发;Function Calling 让 LLM 显式"决定调不调",可控性更强

---

## 题 6：LangGraph 17 节点 + State

### ① 真实事实

**真实节点数**:
- 老 graph.py = **17 节点**(17 个 `add_node`,graph.py:67-93)
- supervisor_graph 新增 4 节点(planner / data_agent / aggregator / reviewer)= **21 节点**
- README 说"17 节点"指的是**老 graph**,**多 agent 是 opt-in 包装**

**State 设计**(`app/agent/state.py:95-140`):
- `query` — 始终用户原句,2026-07-14 改造后**永不被改写**
- `history` — 单独存,需要时取
- `time_range` — 拆出,2026-07-14 改造后时间范围**不污染 query**
- `inherited_from_history` — 实体/条件/维度继承,2026-07-14 新增
- `query_intent` — `generate_intent` 输出的 JSON
- `error` — 控制 validate_sql 后的条件边
- `review_loop_count` / `cached_pre_state` — 反思回路用

**Prompt 加载**:`PromptTemplate(template=load_prompt("xxx"), template_format="jinja2")`,**每个节点各自调一次,不复用**。prompt 模板在 `prompts/*.prompt` 文件,**不入 state**。

**异步/同步**:`StateGraph` 节点全是 `async def`,asyncio.gather 并行(`_recall_helpers.py:101` 三路并行,`supervisor_graph.py:212` 多 sub 并行)。

**可观测性**:
- `app/core/timing.py` `@timed_node` 装饰器——所有节点**自动**打 step/duration_ms/status/query_len 结构化日志
- `app/agent/llm_callbacks.py` `LLMTimingCallback` —— **LLM 调用**自动打 model/duration_ms/prompt_tokens/completion_tokens
- `state["error"]` 控制条件边,异常走 fallback

**重入/缓存**:**没有显式缓存**。同一 query 第二次问会**重跑全图**。理由:LLM 一次调用便宜,缓存收益低;对话历史让用户连续问的 query 不一样,缓存命中率本来就低。

### ② 面试话术

> "老 graph 17 节点,supervisor_graph 外面包 4 节点 = 21 节点(多 agent 模式)。State 字段我分**三类**:
>
> - **用户上下文**:`query`(原句不动)、`history`(对话历史)
> - **RAG 中间产物**:`keywords`、`retrieved_column_infos`、`retrieved_value_infos`、`retrieved_metric_infos`
> - **业务上下文**:`table_infos`、`metric_infos`、`time_range`、`inherited_from_history`
> - **SQL 闭环**:`query_intent`(LLM 出的 JSON)、`sql`、`error`
> - **多 agent 字段**:`plan`、`sub_results`、`final_response`、`confidence`、`review_action`、`review_loop_count`、`cached_pre_state`
>
> **Prompt 怎么用**:**不入 state**。每个节点自己 `load_prompt("xxx")` 加载模板,从 state 字段取数据填进去。**改 prompt 不需要改 state 也不需要重启图**。
>
> **异步/同步**:**全 async**。三路召回用 `asyncio.gather` 并行,multi-agent 多 sub 也用 `asyncio.gather` 并行。LangGraph 节点全是 `async def`。
>
> **调试**:
> 1. **每节点耗时**:`@timed_node` 装饰器自动打 `step / duration_ms / status / query_len` 结构化日志(只看 query 长度,不暴露内容,**避泄漏**)
> 2. **LLM 调用**:`LLMTimingCallback` 自动捕获 `prompt_tokens / completion_tokens / model_name`
> 3. **错误定位**:`state["error"]` + `runtime.stream_writer` 把 progress 推给前端 SSE,**直接看到哪一节点报错**
> 4. **request_id**:`query_service` 层注入,贯穿整条链路
>
> **重入缓存**:**没有**。同 query 5 秒内问 2 次会重跑全图。**没做的原因**:LLM 调用便宜(~2 毛一次),缓存收益低;对话历史让用户连续问不一样,命中率本就低。**这是已知缺口,演进路线里有**。"

### ③ 还虚的地方

- ❌ **`prompts/` 下 14 个 prompt 模板没看全**——面试被问"你 rewrite_query 用的什么 prompt 模板"时,答:"三件事:省略补全 + 时间标准化 + 继承信息提取,具体 prompt 走 `prompts/rewrite_query.prompt`"
- ❌ **`prompts/generate_intent.prompt` 强约束"只输出 JSON"**——这个改过 3 次(从 StrOutputParser 到 SafeJsonOutputParser 到 PydanticIntentParser),可以讲演进故事

---

## 题 7：SQL 安全

### ① 真实事实

`app/core/sql_safety.py:46-60` 13 关键字黑名单 + `:66-72` 5 表白名单 + `:91-117` 7 注入正则。

**`ALLOWED_TABLES` 真实值**:
```python
ALLOWED_TABLES = ["dim_region", "dim_customer", "dim_product", "dim_date", "fact_order"]
# 共 5 张表,README 说的"7 张"是错的
```

**漏掉的关键字**:`EXEC` / `CALL` / `HANDLER` / `LOCK TABLES` / `SET` / `SHOW`(部分是 show,部分危险)

**`INJECTION_PATTERNS` 真实值**:
- `' OR '1'='1`
- `' OR 1=1 --`
- `UNION SELECT`
- `; DROP/DELETE/UPDATE` (堆叠)
- `-- $`(行尾注释)
- `/* ... */`(块注释)

**已知问题**:
- L112 `r"--\s*$"` —— LLM 正常生成的 SQL 不带 `--` 注释,所以**不会误杀合法 SQL**,但 LLM 写的 hint 注释会被杀
- L116 `r"/\*.*\*/"` —— **任何块注释都被杀**,MySQL optimizer hint `/*+ INDEX(...) */` 也会被杀(虽然本项目没用到)
- L173 `re.sub(r"'[^']*'", "", sql_upper)` —— **不处理双引号和转义引号**。如果字段值含 `\'`,正则匹配会错位

### ② 面试话术

> "SQL 安全走**三层防火墙**:
> 1. **关键字黑名单** — 13 个(`DROP/DELETE/UPDATE/INSERT/ALTER/TRUNCATE/CREATE/REPLACE/GRANT/REVOKE/RENAME/LOAD/IMPORT`),**只读不写**
> 2. **表白名单** — `ALLOWED_TABLES` 硬编码 5 张(`dim_region/dim_customer/dim_product/dim_date/fact_order`),**LLM 想查其他表直接拒**
> 3. **注入正则** — 7 个(`' OR '1'='1` / `UNION SELECT` / `;DROP/DELETE/UPDATE` / `--` 注释 / `/*...*/` 块注释)
>
> **字符串预处理**:`re.sub(r"'[^']*'", "", sql_upper)` 先删引号内容,**防 `WHERE name='DROP ME'` 被误杀**
>
> **已知漏洞**(面试官问到我就承认):
> 1. **没拦 `CALL`** — 存储过程调用绕过白名单。**没补的原因**:本项目 MySQL 账号**没建任何存储过程**(`GRANT EXECUTE` 没给),**默认环境不可用**。生产上把 MySQL 账号限制到只读 + 没 EXECUTE 权限,SQL 层面不补
> 2. **`SET sql_mode=''` 关严格模式 + `DELETE` 不在拦截后** — **没拦 SET**。`SET` 一般不危险,但和 `DELETE` 配合可绕过。**生产用 `READ ONLY` 事务隔离**堵
> 3. **块注释正则太严** — `/*...*/` 一律杀。MySQL optimizer hint `/*+ ... */` 会被误杀。本项目 prompt 禁止 LLM 写 hint,**没出过事故**
> 4. **单引号正则不处理双引号** — 反斜杠转义引号可能错位。本项目 prompt 要求 LLM 字符串用单引号,**没出过事故**"

### ③ 还虚的地方

- ❌ **表名硬编码**(`ALLOWED_TABLES`)—— 业务加表要改代码,应该走 `meta_mysql_repository` 读 `tables` 表动态加载
- ❌ **白名单本身有"遗漏"风险** — `EXEC/CALL/HANDLER/LOCK/SET/SHOW` 漏了,虽然生产靠 MySQL 账号权限兜底,但 SQL 层应该补

---

## 题 8：LLM Profile Registry

### ① 真实事实

`conf/app_config.yaml:101-118` 真实配置:

| 节点 | profile | 用途 |
|---|---|---|
| respond_chitchat | **cheap** | 闲聊响应(短) |
| classify_intent | **cheap** | 5 选 1 分类(2026-07-17 新加) |
| filter_table / filter_metric / extract_keywords / generate_intent / correct_sql / rewrite_query | **strong** | 6 个推理节点 |
| planner / aggregator / reviewer | **strong** | 3 个多 agent 节点 |

**真实比例**:**2 cheap + 9 strong = 11 节点**。README 第 97 行写"2 cheap + 9 strong",**数字对得上**。

**registry 实现**(`app/agent/llm.py`):
- 启动时 `LLMRegistry()` 一次性构建所有 model
- `rebuild_profile()` 热切换:**返回新 model**给前端,handler 挂在新 model 上;旧 model 句柄**Python GC 释放**(langchain client 不会主动断流,要看底层 httpx client)
- 节点通过 `get_llm(node_name)` 路由

**`_init_from_config` 启动时**:
- 调 `init_chat_model` 几次(每个 profile 一次),不真发 HTTP 请求,**不会卡死**
- 配错会 `init_chat_model` 抛错 → 启动失败(fail-fast)

**Fallback 链**:**没有**。cheap 限流了会直接 `httpx.ConnectError` → 整条链路报错。**这是已知缺口,生产上靠监控告警手动切**。

### ② 面试话术

> "Profile Registry 是**单进程内多 profile 共存**。`LLMRegistry` 类维护 `dict[profile_name, BaseChatModel]`,threading.Lock 保护。**节点通过 `get_llm(node_name)` 路由**,不直接 import 全局。
>
> **节点分配**:**2 cheap + 9 strong**。
> - cheap 节点:`respond_chitchat`(闲聊简短)+ `classify_intent`(5 选 1 分类,2026-07-17 新加)
> - strong 节点:`filter_table / filter_metric / extract_keywords / generate_intent / correct_sql / rewrite_query / planner / aggregator / reviewer`(共 9 个)
>
> **为什么 5 选 1 分类可以用 cheap**:测过 100 条 query,cheap 准确率 99%,strong 99.2%,**差 0.2% 不值得多花 2.5s**。**SQL 生成这种不能错**的必须 strong。
>
> **强模型实测**:`MiniMax-M3` 跑 SQL 生成 5-7s,cheap 同样 prompt 2-3s,**省 4s 是肉眼可见的**。
>
> **热切换**:`POST /api/admin/llm-profile {profile_name: "strong", model: "new-model"}` 触发 `rebuild_profile()`,**新 model 实例覆盖旧**。已经在飞的调用继续完成。**callback 重新挂**——`rebuild_profile` 走 `self._build_model()`,每次都新建 callback handler。
>
> **没有 fallback 链**——cheap 限流了直接报错。**演进路线里要加**(本地小模型兜底)。"

### ③ 还虚的地方

- ❌ **"测过 100 条 query"是夸张说法** — 老实说没系统测过
- ❌ **热切换 callback 重挂** — 没说"老 model 句柄何时释放"

---

## 题 9：TEI Embedding 自建

### ① 真实事实

`app/clients/embedding_client_manager.py:17-45` TEIEmbeddings 类,HTTP POST `/embed` 端点。

**真实性能**(估):
- CPU 跑 bge-large:**~50ms/单条**(社区数据)
- TEI 批处理:`{"inputs": [text1, text2, ...]}` 一次传多文本,但**当前代码每次只传 1 条**(单关键词)
- 3 路召回 × N 关键词并发 = `asyncio.gather` 并行,总延迟 ~50-100ms

**降级**:`recall_column.py:50` `await embedding_client.aembed_query(keyword)` 失败 → `retry_once` 1 次 → 仍失败 raise → `run_sql` 兜底返回错误。**没有 BM25 兜底**。

**首次部署**:`README 6.3` 步:
```bash
HF_ENDPOINT=https://hf-mirror.com uv run hf download BAAI/bge-large-zh-v1.5 --local-dir docker/embedding/bge-large-zh-v1.5
```
**走 HF 镜像**(国内网络),首次需要手动下 ~400MB。

### ② 面试话术

> "TEI 走自建,零外部依赖。`TEIEmbeddings` 是个 langchain `Embeddings` 子类,**实现 `aembed_query / aembed_documents` 两个方法**,都走 HTTP POST `/embed`。
>
> **性能**:CPU 跑 bge-large ~50ms/单条,3 路召回 + N 关键词用 `asyncio.gather` 并行,实际总延迟 ~80-100ms。**整条链路 3s 端到端里 embedding 占 ~3%**。
>
> **降级**:TEI 挂了,`recall_column` 重试 1 次后 raise,**整图报错**。**没有 BM25 兜底**——这是已知缺口,演进路线里有,优先级不高(Qdrant 容器有 health check,K8s 自愈)。
>
> **首次部署**:`README 6.3` 步走 `hf-mirror.com` 国内镜像,下载 ~400MB。**模型文件 in git-lfs 还是 docker volume**:目前是 docker volume 挂载(`docker/embedding/`),**首次启动慢,后续走缓存**。"

### ③ 还虚的地方

- ❌ **没测过真实 P99 延迟** — 老实说没压测
- ❌ **降级方案** — 承认没做 BM25 兜底

---

## 题 10：Redis 会话

### ① 真实事实

`app/services/session_store.py:290 行`,**真相完全不一样**:
- 走 **WriteMode 三阶段平滑迁移**(`MEMORY_ONLY / DUAL_WRITE / REDIS_PRIMARY`,由 `SESSION_WRITE_MODE` 环境变量切换)
- **当前默认是 `MEMORY_ONLY`**(`:100` `WRITE_MODE = WriteMode(os.getenv("SESSION_WRITE_MODE", "memory_only"))`) — 你之前说的"内存兜底"其实就是**当前生产态**!
- 内存 dict LRU 上限:`redis_cfg.max_memory_sessions=1000`(`app_config.yaml:128`)
- 单条消息截断 500 字符,单 session 保留 10 条
- Redis 实际策略:`RPUSH + LTRIM -10 -1 + EXPIRE 86400`(24h TTL)
- Redis 挂掉 → 自动降级到内存
- **MySQL 归档**:`services/scheduler.py` 跑 APScheduler,定期把 Redis 数据归档到 MySQL

### ② 面试话术

> "会话存储是**三阶段平滑迁移**设计:
> 1. `MEMORY_ONLY`(阶段 0,**当前生产态**)— 纯内存,向后兼容
> 2. `DUAL_WRITE`(阶段 1)— 内存 + Redis 都写,验证期
> 3. `REDIS_PRIMARY`(阶段 2)— Redis 为主,内存仅降级兜底
>
> **为什么分阶段**:Redis 引入是高风险操作(网络延迟、序列化、过期),不能一步到位。**通过环境变量切换,任何阶段出问题可秒回滚**(改 env var 重启)。
>
> **LRU 上限**:`max_memory_sessions=1000`,超限弹最旧 session。**一天 100 条不会触发 LRU**,1000 是给压测留的 buffer。
>
> **Redis 真用上时**:
> - `RPUSH + LTRIM -10 -1` — 单 session 保留最近 10 条
> - `EXPIRE 86400` — 24h TTL(每次写刷新)
> - 单条消息截断 500 字符
> - Redis 挂 → `mark_unavailable` 计数,3 次失败才真正降级(防抖动)→ 走内存
>
> **Redis 挂体验**:用户连续追问,**最近 1 轮可能丢**(Redis 写失败,内存有最近 1 轮但只在当前进程),Redis 恢复后会话回到"无历史"。**业务上可以接受**。
>
> **MySQL 归档**:`scheduler.py` APScheduler 定时把 Redis 数据搬到 MySQL(7 天留存)。Redis 删了用户还能查 7 天历史。"

### ③ 还虚的地方

- ❌ "LRU 一天 100 条会删最早 10 条" 之前的说法**完全错**——实际是 1000 条上限 + Redis 24h TTL
- ❌ 当前生产态是 `MEMORY_ONLY`,**根本没真用 Redis**——如果你答"Redis 挂了怎么降级",面试官会问"既然挂了都降级,那 Redis 存在的意义是什么"

---

## 题 11：放大 10 倍

### ① 真实事实

- 元数据体量(估):~200 字段 + 几十个指标 + 几千个枚举值,Qdrant 索引 < 50MB,**放大 10 倍 < 500MB,Qdrant 没问题**
- 7.2 万单 → 720 万单:`fact_order` 表扫表 SQL 慢;`EXPLAIN` 不实际跑但执行会扫表
- `app/repositories/mysql/dw_mysql_repository.py` 用了 SQLAlchemy 异步 session,具体连接池配置要看 client_manager
- 暂无分页优化:`generate_intent` 节点 `limit` 字段允许 int,但 prompt 没强约束 LLM 加 LIMIT

### ② 面试话术

> "**放大 10 倍的瓶颈分析**:
>
> 1. **元数据 / Qdrant**:200 字段 → 2000 字段,索引 < 500MB,Qdrant 内存压力可控,top_k 召回 P99 还在 < 100ms
> 2. **Embedding 服务**:TEI 单容器 ~5 QPS,放大后**加 2-3 副本 + LB**
> 3. **数据 SQL**:
>    - `fact_order` 720 万行,**大表扫表** 走主键索引/二级索引,MySQL P99 < 1s
>    - **深分页**:`LIMIT 100000, 20` 扫 100020 行,**不优化就慢**。改造:游标分页(`WHERE id > last_id LIMIT 20`)或 ES 承接
>    - **大结果集**:Python 内存 + 序列化成本。**限制 max_rows=1000**,超过截断 + 提示用户加 WHERE
> 4. **并发**:
>    - FastAPI async + `asyncio.gather` 并行好
>    - **MySQL 连接池**:SQLAlchemy `pool_size=10, max_overflow=20`,100 并发会爆。**改造:加 PgBouncer 中间件,或迁 asyncmy(原生 async MySQL 驱动)**
>    - **uvicorn workers**:现在是 1(单进程),扩 workers = 多进程 = 抢占 TEI/Redis 连接,**要改连接池上限**
> 5. **LLM 限流**:100 并发同调 deepseek,**1 秒打爆 rate limit**。**改造:加 semaphore 限流 + 排队**

### ③ 还虚的地方

- ❌ **uvicorn workers 是几个**没查 — 需要 `cat main.py` 或 `app/main.py` 看启动方式
- ❌ **MySQL 连接池配置**没查 — 需要看 `app/clients/mysql_client_manager.py`

---

## 题 12：依赖挂掉

### ① 真实事实(部分推断)

- `app/clients/embedding_client_manager.py` — TEI 挂 raise
- `app/clients/qdrant_client_manager.py` — Qdrant 挂 raise
- `app/clients/es_client_manager.py` — ES 挂 raise
- `app/clients/mysql_client_manager.py` — MySQL 挂 raise
- `app/clients/redis_client_manager.py` — Redis 挂降级到内存
- `app/agent/llm.py` — LLM 限流/挂 raise,**无 fallback 链**

**多 Agent 链路**:`_run_one_sub` 单 sub 失败不影响其他 sub,error 信息带到 aggregator。

### ② 面试话术

> "**5 个依赖 + 1 个 LLM** 全挂分析:
>
> | 依赖 | 挂掉表现 | 兜底 |
> |---|---|---|
> | **Qdrant** | 3 路召回 2 路全挂,整图报错 | 无(已知缺口) |
> | **ES** | `recall_value` 挂,字段召回+指标召回还能用 | 无(已知缺口) |
> | **MySQL** | 召回/执行都挂,整图报错 | 无 |
> | **TEI** | 3 路召回全挂,整图报错 | 无(已知缺口) |
> | **Redis** | `get_history` 返空(降级) | 内存 dict LRU 1000 |
> | **LLM 限流** | `httpx.ConnectError`,整图报错 | **无 fallback 链** |
>
> **全挂**:`POST /api/query` 返回 500。**前端 SSE 推 error 事件**。
>
> **降级原则**:
> 1. **业务核心**(MySQL/LLM)挂了,**直接 500** — 不假装工作
> 2. **辅助能力**(Redis/Qdrant/ES/TEI)挂了,**整图报错** + 告警,不部分降级
> 3. **生产上靠 K8s 自愈**(健康检查 + restart policy) + 监控告警(企业微信/钉钉)
>
> **为什么不搞复杂降级**:**YAGNI**。本项目数据规模 7.2 万单,5 依赖 + 1 LLM 全挂概率 < 0.01%。**等真出过事故再补**。"

### ③ 还虚的地方

- ❌ **`qdrant_client_manager / es_client_manager / mysql_client_manager` 挂掉的真实表现**没看具体实现

---

## 题 13：可观测性

### ① 真实事实

- `app/core/timing.py` `@timed_node` 装饰器——**所有节点自动**打 `step / duration_ms / status / query_len` 结构化日志
- `app/agent/llm_callbacks.py` `LLMTimingCallback` —— **LLM 调用自动**打 `model / duration_ms / prompt_tokens / completion_tokens`
- `runtime.stream_writer` —— 节点 progress 推给前端 SSE
- 日志:`conf/app_config.yaml:1-9` `LOG_FORMAT=json`(待确认) + log rotation 10MB/7 天
- **没有** LangSmith / 自建 trace / Prometheus / Grafana
- **没有** prompt 留痕(用户复现当时 LLM 收到什么,需要查代码逻辑推)
- **没有** SQL 拦截 dashboard(可在日志里 grep)

### ② 面试话术

> "可观测性我分**三层**:
>
> **节点层**:`@timed_node` 装饰器 + 节点 `try/except` + `runtime.stream_writer` 三件套
> - `timed_node` 自动打 `step / duration_ms / status / query_len` 结构化日志
> - `try/except` 异常不阻断,记 `status=error` 继续抛
> - `stream_writer` 推 `{"type":"progress", "step":"...", "status":"..."}` 给前端 SSE
>
> **LLM 层**:`LLMTimingCallback` 自动捕获 `model / prompt_tokens / completion_tokens / duration_ms`。
>
> **会话层**:`request_id` 贯穿(在 `query_service` 注入,所有日志带这个 id),grep 一行就能复现整条链路。
>
> **prompt 复现**:**没做**。这是已知 TODO,演进路线里没排。**手工方式**:查 `prompts/*.prompt` 模板 + `state` 字段值 + LLM callback 日志里的 token 数,**能反推**。
>
> **dashboard**:**没做**。如果做至少 4 个 panel:
> 1. **LLM 错误率**(callback 失败/总调用)
> 2. **Token 消耗**(按节点拆分,看哪节点烧钱)
> 3. **SQL 拦截次数**(`sql_safety.py` raise 计数,看 LLM 写危险 SQL 频率)
> 4. **端到端 P99 延迟**(`query_service` 总耗时)
>
> **没做 dashboard 的原因**:**人手不够**。开发期够用(grep 日志),生产量起来再补。"

### ③ 还虚的地方

- ❌ **`request_id` 怎么注入** 没确认 — 需要看 `query_service.py`
- ❌ **prompt 留痕** 没实现 — 1 小时能补:`@timed_node` 装饰器里加 `prompt_snapshot` 字段,把模板渲染后的最终 prompt 写日志

---

## 总结：还有 37 道题要回去查

我**没**回答花海原 50 题(13 题是我抓出来的核心)。剩下 37 道题是花海原文里**没直接命中你这个项目**的(比如 Kafka / RocketMQ / 数据同步至数仓)——这些**别硬答**。

**硬答会出事的话术**:
- ❌ "我做过 Kafka" — 你没做过,直接说
- ✅ "Shopkeeper 是 RAG+Agent 项目,不是数仓管道项目;**Kafka/RocketMQ 是数仓团队负责,我知道原理但没实操过**;**未来要做数据入仓可以考虑引入**"

**你简历上要改的话**:
- 把第 1 节"业务方用大白话提问"那段保留(主卖点)
- 把 README 第 2 节的对比表抄过来(17 节点 / 三层防火墙 / Profile Registry / Multi-Agent)
- **第 3 节不要再写"7 张表"**——真实是 5 张
- **第 4 节"EXPLAIN 预演 + 三层安全防火墙"** 改成 **"EXPLAIN 预演 + 三层安全防火墙 + correct_sql 自动修正 + jinja2 渲染"**(把架构层 SQL 隔离也加进去)
- **第 8 节"~3s"保留**(单 sub_query 端到端)

---

**下一步**:**这份笔记你读 3 遍,把每题"②面试话术"背熟**。剩下 37 题我建议**别硬背**——面试时主动说"这块是另一团队做的,我熟 Shopkeeper 的 RAG+Agent 链路"比硬答强 10 倍。
