# Shopkeeper Agent — 自然语言驱动的电商问数系统

> 用户说"统计 2025 年第一季度各大区的 GMV" → 系统自动检索表结构、生成 SQL、安全校验、执行 → 返回结构化结果。
>
> 不是套壳 LLM，而是一个 **RAG + Agent 工作流**：多路召回 → 程序性上下文 → LLM 推理 → 安全校验 → 自动修正 → 反思重试。

---

## 1. 一句话介绍

**Shopkeeper Agent** 是一个电商问数系统。业务方用大白话提问，系统自动从元数据知识库检索表/字段/指标，推理生成 SQL，过三层安全校验后执行查询，按需触发自动修正与反思重试。

数据规模：**7.2 万单 / 12 个地区 / 120 SKU / 500 客户**（2025-01 ~ 2026-07）。

---

## 2. 为什么这是 AI Agent（不是套壳 LLM）

| 套壳 LLM | 本项目 |
|---|---|
| LLM 凭"记忆"猜表结构 | Agent 从元数据知识库**精确检索**表/字段/值 |
| 单次调用、无回退 | **17 节点有向图**，失败自动回退重试 |
| SQL 不可信也直接执行 | **EXPLAIN 预演 + 三层安全防火墙 + 自动修正回路** |
| 弱模型强模型混用 | **Profile Registry**：按节点路由到 cheap/strong 模型，省 token 不省能力 |
| 单 sub_query 走老路 | **Multi-Agent 模式**：planner 拆 sub → Send API 并行跑 → aggregator 合并 → reviewer 评分（< 0.7 触发反思回路） |

---

## 3. 架构

### 3.1 两层图

```
┌─────────────────────────────────────────────────────────────┐
│  Supervisor Graph（multi-agent 入口，use_multi_agent=true）  │
│                                                             │
│   planner → data_agent → aggregator → reviewer              │
│              ↓                                              │
│         (Send API 拆 sub_query，并行跑下面的 graph)            │
└─────────────┬───────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────────────────────┐
│  Data Graph（17 节点单 sub_query 链路）                      │
│                                                             │
│   classify_intent → rewrite_query ─┬→ respond_chitchat      │
│                                     ├→ respond_metadata     │
│                                     ↓                        │
│                          extract_keywords ─┐                │
│                                            ↓                 │
│   recall_column ┐  recall_value ┐  recall_metric (三路)      │
│         ↓        ↓        ↓                                │
│              merge_retrieved_info → filter → add_extra_ctx  │
│                            ↓                                │
│                     generate_intent → generate_sql          │
│                            ↓                                │
│       validate_sql ⇄ correct_sql  (失败回路)                │
│                            ↓                                │
│                          run_sql                            │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 多路召回 + 混合检索

```
                  用户问题
                     ↓
              Jieba 关键词提取
                     ↓
      ┌──────────────┼──────────────┐
      ↓              ↓              ↓
  Qdrant 向量     Elasticsearch    Qdrant 向量
  (字段名)        (枚举值)         (指标名)
  order_amount    华东/华南        GMV
      ↓              ↓              ↓
      └──────────────┼──────────────┘
                     ↓
              merge + filter
                     ↓
       add_extra_context (时间/库元信息)
                     ↓
                 LLM 推理
```

**为什么用混合检索**：
- 向量搜索理解"销售额"≈"order_amount"这种语义，但**"华东/华南"向量太近**会混淆
- 全文搜索**精确匹配**枚举值，但**搜不到"GMV"这种抽象概念**
- 两者互补——**Qdrant 管语义、ES 管精确**

### 3.3 LLM Profile Registry（按节点路由模型）

```yaml
# 两个 profile 槽位（具体模型走 .env 注入，不在 yaml 里写死）
llm_profiles:
  cheap:  { 用途: 闲聊/分类,  timeout: 15s,  max_tokens: 500 }
  strong: { 用途: SQL/纠错/改写, timeout: 30s,  max_tokens: 2000 }

node_profiles:  # 2 cheap + 9 strong
  respond_chitchat: cheap   # 闲聊简短，弱模型够用 + 省 token
  classify_intent:  cheap   # 5 选 1 分类，cheap 准确度足够，省 ~2.5s
  # 其余 9 个节点全部 strong：filter_table / filter_metric /
  # extract_keywords / generate_intent / correct_sql / rewrite_query /
  # planner / aggregator / reviewer
```

**设计意图**：不是所有节点都需要强模型。闲聊 / 5 选 1 分类这种"短答 + 路径少"的，弱模型省 token；SQL 生成 / 反思评分这种"长推理 + 高容错"的，强模型保证质量。**模型名走 `.env` 注入**（`LLM_CHEAP_MODEL_NAME` / `LLM_STRONG_MODEL_NAME`），换模型不用改 yaml。

**热切换**：`POST /api/admin/llm-profile {node, profile}` 改完立即生效，不用重启。

### 3.4 反思回路（Multi-Agent 模式）

```
reviewer 评分
  ├─ ≥ 0.7  → END
  └─ < 0.7  → 触发反思，max_loop=2
                重新跑 planner + data_agent
```

---

## 4. 安全设计

LLM 输出不可信。SQL 执行前**过三层**：

| 层级 | 机制 | 做法 |
|---|---|---|
| 1 | **关键字黑名单** | 拦截 DROP / DELETE / UPDATE / INSERT / ALTER 等写操作 |
| 2 | **只读白名单** | 只允许 SELECT / WITH 开头的查询 |
| 3 | **SQL 注入检测** | UNION SELECT / OR '1'='1' / `--` 注释截断 |
| 4 | **EXPLAIN 预演** | 不实际执行，MySQL 只解析语法 |
| 5 | **自动修正回路** | 语法错 → 错误信息 + 原 SQL 回传 LLM 修正 → 再 EXPLAIN |

字符串预处理：**先移除引号内容再匹配关键字**（防误杀 `WHERE name='DROP ME'`）。

---

## 5. 技术栈

| 组件 | 选择 | 为什么 |
|---|---|---|
| Agent 框架 | **LangGraph** | 有向图 + 条件分支 + 状态机，天然支持失败回退 |
| LLM | **Profile Registry（cheap / strong 两槽位）** | 按节点路由——闲聊/分类用 cheap，SQL/纠错/改写用 strong；模型名走 `.env` 注入，换模型不改 yaml |
| Embedding | **bge-large-zh-v1.5** (1024 维) | 中文 SOTA 开源模型 |
| 向量推理服务 | **TEI** (HuggingFace) | 自建，零外部依赖，CPU 也能跑 |
| 向量库 | **Qdrant** | Rust 实现性能好，单容器部署，支持过滤 |
| 全文检索 | **Elasticsearch + IK 分词** | 中文分词刚需，IK 是社区最稳的 |
| 后端 | **FastAPI** | 原生 async + Pydantic 校验 + 自动 OpenAPI |
| 前端 | **React + Vite + TypeScript** | pnpm workspace |
| 包管理 | **uv** (Rust) | 比 pip 快 10-100×，自带 venv + lockfile |
| 关键词提取 | **Jieba + 自定义词典** | `conf/jieba_userdict.txt` 兜业务术语 |

---

## 6. 快速开始

### 6.1 环境要求

- Python 3.13+
- Docker Desktop
- Node.js 22+（前端用 pnpm 10+）
- 8GB+ 内存（全栈 6 个容器）

### 6.2 启动基础设施

```bash
cd docker
docker compose up -d
# 启动：MySQL + Elasticsearch + Kibana + Qdrant + TEI Embedding + Redis
# 共 6 个容器
```

首次启动会自动建表 + 灌入 7.2 万单种子数据（`docker/mysql/dw.sql`）。

### 6.3 下载 Embedding 模型

```bash
HF_ENDPOINT=https://hf-mirror.com uv run hf download \
  BAAI/bge-large-zh-v1.5 \
  --local-dir docker/embedding/bge-large-zh-v1.5
```

### 6.4 配置 LLM

编辑 `.env`（参考 `.env.example`）—— 模型名 / base_url / api_key 都在 `.env` 里注入，不在 yaml 写死：

```env
# Cheap profile（弱模型，闲聊/分类）
LLM_CHEAP_MODEL_NAME=<cheap-model>
LLM_CHEAP_BASE_URL=<cheap-base-url>
LLM_CHEAP_API_KEY=<cheap-api-key>

# Strong profile（强模型，SQL/改写/纠错）
LLM_STRONG_MODEL_NAME=<strong-model>
LLM_STRONG_BASE_URL=<strong-base-url>
LLM_STRONG_API_KEY=<strong-api-key>

DB_PASSWORD=dili123
```

**Mac 端 MySQL 默认 3307 端口，Windows 端 3306 端口**——靠 `DB_PORT` 环境变量切换。

### 6.5 装依赖 + 构建知识库

```bash
# Python 依赖
uv sync

# 构建元数据知识库（一次性，把表/字段/指标写入 MySQL → Qdrant → ES）
uv run python -m app.scripts.build_meta_knowledge -c conf/meta_config.yaml
```

### 6.6 启动服务

```bash
# 后端（终端 1）
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

# 前端（终端 2）
cd frontend && pnpm install && pnpm dev
```

打开 `http://localhost:5173` 即可体验。

---

## 7. 项目结构

```
shopkeeper-agent/
├── app/
│   ├── agent/                  # Agent 工作流
│   │   ├── graph.py            # 17 节点单 sub_query 主图
│   │   ├── supervisor_graph.py # Multi-Agent 顶层图（planner/aggregator/reviewer）
│   │   ├── data_subgraph.py    # 前置/后置 subgraph（按需复用）
│   │   ├── state.py            # LangGraph TypedDict 状态
│   │   ├── context.py          # Runtime Context
│   │   ├── llm.py              # LLM 客户端 + Profile Registry
│   │   ├── llm_callbacks.py    # Token/latency 监控 callback
│   │   └── nodes/              # 20 个业务节点 + 1 个 _recall_helpers 内部辅助
│   ├── api/                    # FastAPI 路由 + SSE 流式
│   │   ├── routers/            # query_router / admin_router
│   │   ├── schemas/            # Pydantic schema
│   │   ├── lifespan.py
│   │   └── dependencies.py
│   ├── services/               # 业务逻辑层
│   │   ├── query_service.py    # 单 sub_query + multi-agent 入口
│   │   ├── sql_template.py
│   │   ├── date_resolver.py
│   │   ├── metric_resolver.py
│   │   ├── schema_resolver.py
│   │   ├── session_store.py    # Redis 会话
│   │   ├── meta_knowledge_service.py
│   │   └── scheduler.py        # APScheduler 归档
│   ├── repositories/           # 仓储层
│   │   ├── mysql/              # meta + dw
│   │   ├── es/                 # value_es_repository
│   │   └── qdrant/             # column_qdrant / metric_qdrant
│   ├── clients/                # 基础设施 client manager
│   │   ├── embedding_client_manager.py
│   │   ├── es_client_manager.py
│   │   ├── mysql_client_manager.py
│   │   ├── qdrant_client_manager.py
│   │   └── redis_client_manager.py
│   ├── core/                   # 核心工具
│   │   ├── sql_safety.py       # 三层防火墙
│   │   ├── safe_json_parser.py # 兼容 <think> 块
│   │   ├── pydantic_parser.py
│   │   ├── retry.py            # 重试 + 指数退避
│   │   ├── log.py
│   │   └── timing.py
│   ├── entities/               # Pydantic 领域实体
│   ├── models/                 # SQLAlchemy ORM
│   ├── middleware/
│   ├── scripts/                # 一次性脚本
│   │   ├── build_meta_knowledge.py  # 首次部署入口
│   │   └── archive_sessions.py      # scheduler 调用
│   ├── conf/                   # 配置加载
│   └── prompt/                 # Prompt 加载器
├── conf/                       # 配置 + Jieba 词典
│   ├── app_config.yaml
│   ├── meta_config.yaml
│   └── jieba_userdict.txt
├── prompts/                    # 14 个 Prompt 模板（可插拔）
├── tests/                      # 17 个 pytest 用例 + 5 个 eval 工具
├── docker/
│   ├── docker-compose.yaml     # 6 容器编排
│   ├── mysql/                  # dw.sql (7.3MB) + gen_dw_sql.py
│   ├── elasticsearch/
│   └── embedding/              # bge-large-zh-v1.5
├── frontend/                   # React + Vite + TypeScript
├── docs/                       # RFC + 架构设计 + 笔记
├── main.py
├── pyproject.toml
└── .devcontainer/              # GitHub Codespace 部署
```

---

## 8. Multi-Agent 模式

`POST /api/query` 加 `use_multi_agent=true` 走 supervisor_graph：

```json
{
  "query": "2025 Q1 各大区 GMV",
  "use_multi_agent": true,
  "session_id": "demo-001"
}
```

流程：
1. **Planner** 拆 sub_query（多数单 sub 直通，不调 LLM）
2. 多 sub 时用 LangGraph **Send API 并行跑** data_graph
3. **Aggregator** 合并 sub 结果
4. **Reviewer** 评分（< 0.7 触发反思回路，max_loop=2）

`use_multi_agent=false`（默认）走单 sub_query 路径，**端到端平均 ~3s**（单 LLM 节点 3-5s + 召回 < 200ms）。

---

## 9. 测试

```bash
# 单元测试
uv run pytest tests/ -v

# 评估脚本
uv run python tests/eval_e2e.py       # 端到端 query 正确率
uv run python tests/eval_recall.py    # 三路召回命中率
uv run python tests/eval_comparison.py # single-agent vs multi-agent
```

**19 个测试文件 / 165 个测试函数**（参数化覆盖）：SQL 安全（关键字/白名单/注入/防误杀）/ Jieba 关键词 / 时间解析 / 评估器 / Prompt 加载 / 路由。

---

## 10. 演进路线

| 阶段 | 状态 | 描述 |
|---|---|---|
| 单 sub_query graph | ✅ | 17 节点主图，稳定生产 |
| Multi-Agent 模式 | ✅ | supervisor_graph 入口，opt-in |
| LLM Profile Registry | ✅ | 按节点路由 cheap/strong，热切换 |
| Function Calling | 🚧 计划 | 业务术语查询 + 元数据版本检查工具 |
| NL2SQL 公开 benchmark | 🚧 计划 | BIRD / Spider 子集评估 |

---

## 11. License

MIT
