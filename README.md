# 电商问数 — 自然语言驱动的智能数据分析 Agent

> **一个让非技术人员用大白话查询数据库的 AI Agent 系统**
>
> 不是简单的"问 LLM → 返回答案"，而是一个完整的 **RAG + Agent 工作流**：
> 召回相关数据 → 推理生成 SQL → 安全校验 → 执行查询 → 修正重试。

## 一句话介绍

用户用自然语言提问（如"华东地区上个月卖了多少货"），系统自动：
1. 从元数据知识库中**检索**相关表、字段、指标
2. 交给 LLM **推理**生成 SQL
3. **校验**安全性后执行
4. 返回结构化结果

整个过程对用户完全透明——就像跟数据分析师对话一样。

---

## 为什么这是一个"AI Agent"而不是"套壳 LLM"

| 普通 LLM 调用 | 本项目的 Agent 架构 |
|---|---|
| 用户问题 → LLM → 答案 | 用户问题 → **多路召回** → **过滤筛选** → **LLM 推理** → **安全校验** → **自动修正** → 执行 |
| LLM 凭"记忆"猜表结构 | Agent 从元数据知识库**精确检索**表结构 |
| 无法验证 SQL 正确性 | **EXPLAIN 预演 + 安全防火墙 + 自动修正重试** |
| 直接执行，有安全风险 | 三层安全防护：只允许只读查询 |
| 单次调用 | **11 节点有向图**，失败自动回退重试 |

---

## AI Agent 架构设计

```
                          ┌──────────────────────────┐
                          │     用户自然语言问题        │
                          │   "华东上个月卖了多少货"    │
                          └────────────┬─────────────┘
                                       ↓
                          ┌──────────────────────────┐
                          │   ① 关键词提取 (Jieba)     │
                          │   华东 / 上个月 / 卖 / 货    │
                          └────────────┬─────────────┘
                                       ↓
            ┌──────────────────────────┼──────────────────────────┐
            ↓                          ↓                          ↓
   ┌────────────────┐       ┌────────────────┐       ┌────────────────┐
   │ ② 字段召回      │       │ ③ 取值召回      │       │ ④ 指标召回      │
   │ Qdrant 向量搜索 │       │ ES 全文搜索     │       │ Qdrant 向量搜索 │
   │ "卖了多少"→     │       │ "华东"→         │       │ "GMV"→         │
   │ order_amount   │       │ dim_region      │       │ SUM(amount)    │
   └───────┬────────┘       └───────┬────────┘       └───────┬────────┘
            ↓                          ↓                          ↓
            └──────────────────────────┼──────────────────────────┘
                                       ↓
                          ┌──────────────────────────┐
                          │ ⑤ 合并召回结果 + 过滤无关信息 │
                          │ ⑥ 补充上下文（日期/数据库环境）│
                          └────────────┬─────────────┘
                                       ↓
                          ┌──────────────────────────┐
                          │ ⑦ LLM 推理生成 SQL         │
                          │ DeepSeek-v4-pro           │
                          │ Prompt 含：表结构+字段+指标  │
                          └────────────┬─────────────┘
                                       ↓
                          ┌──────────────────────────┐
                          │ ⑧ EXPLAIN 语法校验         │
                          │ 失败 → ⑨ LLM 修正 → 再校验  │
                          └────────────┬─────────────┘
                                       ↓
                          ┌──────────────────────────┐
                          │ ⑩ SQL 安全防火墙           │
                          │ 黑名单+白名单+注入检测       │
                          └────────────┬─────────────┘
                                       ↓
                          ┌──────────────────────────┐
                          │ ⑪ 执行 SQL → 流式返回结果   │
                          └──────────────────────────┘
```

### 核心设计理念

**检索增强生成（RAG）**：LLM 不直接"猜"数据库结构。每次查询前，系统先从知识库（向量库 + 全文索引）中精确检索相关元数据，再拼接进 Prompt。这保证了 SQL 的**表名、字段名、业务口径**都来自真实数据源，而不是 LLM 的幻觉。

**混合检索（Hybrid Search）**：单一检索方式有盲区。
- **向量搜索**（Qdrant）：理解"销售额"≈"order_amount"这种语义映射。但枚举值"华东/华南"语义太近，会混淆。
- **全文搜索**（Elasticsearch + IK 分词）：精确匹配"华东"这个具体值。但搜不到"GMV"这种抽象概念。
- 两种方式互补，确保无论用户说什么表达都能精准命中。

**工作流编排（LangGraph）**：11 个节点组成有向图，不是简单的顺序调用。每个节点有明确的输入/输出状态（TypedDict），节点间按照"成功→下一步 / 失败→修正→重试"的图结构流转。比如 SQL 校验失败不会直接报错，而是把错误信息回传给 LLM 修正后再试。

**安全防御（SQL Firewall）**：LLM 的输出不可信。执行前必须过三层检查：
1. 关键字黑名单（拦截 DROP/DELETE/UPDATE/INSERT/ALTER 等 13 个危险词）
2. 只读白名单（只允许 SELECT/WITH 开头的查询）
3. SQL 注入检测（UNION SELECT / OR '1'='1' / -- 注释截断）

---

## 技术栈选型理由

| 组件 | 选择 | 为什么不用替代方案 |
|------|------|-------------------|
| **Agent 框架** | LangGraph | AutoGPT 太重，单 Agent 又不具备工作流回退能力。LangGraph 用有向图定义节点+边，天然支持失败重试、条件分支 |
| **LLM 模型** | DeepSeek-v4-pro | 性价比最高，SQL 生成能力强，中文理解优于 Llama 系列。通过 opencode.ai 中转调用 |
| **向量模型** | bge-large-zh-v1.5 (1024维) | 中文语义理解最好的开源模型之一。OpenAI text-embedding 要收费且中文效果不如 bge |
| **向量化服务** | TEI (HuggingFace) | 自建 Embedding 服务，不依赖第三方 API。HuggingFace TEI 是 HuggingFace 官方推出的高性能推理引擎 |
| **向量数据库** | Qdrant | Milvus 太重（需要 etcd + Pulsar），Chroma 功能太弱。Qdrant 单容器部署、Rust 实现性能好、支持过滤条件 |
| **全文检索** | Elasticsearch + IK 分词 | 中文分词是刚需，IK 是社区最成熟的方案。Solr 社区已经凉了 |
| **后端框架** | FastAPI | Flask 不支持 async，Django 太重。FastAPI 原生 async + Pydantic 验证 + 自动 OpenAPI 文档 |
| **包管理** | uv (Rust) | pip 太慢。uv 快 10-100 倍，自带虚拟环境管理和锁文件 |

---

## 快速开始

### 1. 环境要求
```bash
# Python 3.13+
# Docker Desktop
# Node.js 22+（仅前端）
```

### 2. 启动基础设施（一键）
```bash
cd docker
docker compose up -d
# 启动 MySQL + Qdrant + Elasticsearch + TEI Embedding
```

### 3. 安装 Python 依赖
```bash
uv sync
```

### 4. 下载 Embedding 模型（国内镜像加速）
```bash
HF_ENDPOINT=https://hf-mirror.com uv run hf download \
  BAAI/bge-large-zh-v1.5 \
  --local-dir docker/embedding/bge-large-zh-v1.5
```

### 5. 配置 LLM API Key
编辑 `.env`：
```env
LLM_API_KEY=your_api_key_here
```

### 6. 构建元数据知识库
```bash
uv run python -m app.scripts.build_meta_knowledge -c conf/meta_config.yaml
# 将表结构写入 MySQL → 向量写入 Qdrant → 取值写入 ES
```

### 7. 启动服务
```bash
# 后端（终端1）
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

# 前端（终端2）
cd frontend && pnpm install && pnpm dev
```

访问 http://localhost:5173 即可体验。

---

## 项目结构

```
shopkeeper-agent/
├── app/
│   ├── agent/           # 🧠 LangGraph Agent 工作流
│   │   ├── nodes/       # 11 个处理节点（召回/生成/校验/执行）
│   │   ├── graph.py     # 有向图编排（节点+边+条件分支）
│   │   ├── state.py     # Agent 状态定义（TypedDict）
│   │   └── llm.py       # LLM 客户端封装
│   ├── core/            # 🛡️ SQL 安全防火墙
│   ├── repositories/    # 仓储层（MySQL/Qdrant/ES 抽象）
│   ├── clients/         # 基础设施客户端管理
│   ├── api/             # FastAPI 路由 + SSE 流式
│   ├── services/        # 业务逻辑层
│   └── entities/        # 领域实体（pydantic）
├── prompts/             # 🎯 LLM Prompt 模板（可插拔）
├── tests/               # 🧪 单元测试（20 用例，pytest + ruff）
├── docker/              # 🐳 Docker Compose 五容器编排
├── frontend/            # 🖥️ React 聊天界面
├── docs/notes/          # 📚 学习笔记 + 面试题库
└── conf/                # ⚙️ 元数据配置（表/字段/指标定义）
```

---

## 安全特性（Agent 专属）

| 层级 | 机制 | 具体做法 |
|------|------|----------|
| 语法校验 | `EXPLAIN` 预演 | 不实际执行，MySQL 只检查语法 |
| 自动修正 | LLM 重试 | 语法错 → 把错误信息+原 SQL 回传 LLM 修正 |
| 安全防火墙 | 三层检查 | ①关键字黑名单 ②只读白名单 ③注入检测 |
| 防误杀 | 字符串预处理 | 先移除引号内容再匹配关键字 |
| 单元测试 | 20 个用例 | 覆盖正常/异常/边界/防误杀场景 |

---

## 面试准备

项目附带完整学习文档：

| 文档 | 内容 |
|------|------|
| `docs/notes/校招面试题库.md` | 42 题 8 分类，3 个核心故事 |
| `docs/notes/藤子的Python成长笔记-全记录.md` | 40 个 Python 知识点速查 |
| `docs/notes/完整代码变更档案-20260629.md` | 11 个文件逐行对比 |
| `docs/notes/SQL安全加固-代码学习笔记.md` | 安全模块逐行注释 |
| `docs/notes/SQL安全设计决策-Grill记录.md` | 设计决策与取舍 |
| `docs/notes/单元测试落地记录-20260630.md` | 测试驱动开发记录 |

---

## License

MIT
