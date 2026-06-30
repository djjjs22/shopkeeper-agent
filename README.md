# 电商问数 — 自然语言驱动的智能数据分析系统

> 让不懂 SQL 的人也能用大白话查询数据库

## 一句话介绍

用户用自然语言提问（如"华东地区上个月卖了多少货"），系统自动将问题转为 SQL、查询数据库、返回结果。整个过程对用户完全透明——就像跟数据分析师对话一样。

## 技术架构

```
用户问"华东的销售额"
      ↓
前端 (React + Vite)  ← 聊天界面
      ↓
后端 (FastAPI)  ← 接收请求，SSE 流式返回
      ↓
LangGraph 工作流  ← 编排 11 个处理节点
  ├── 关键词提取
  ├── 三路并行召回
  │   ├── 字段召回 (Qdrant 向量搜索)
  │   ├── 取值召回 (Elasticsearch 全文搜索)
  │   └── 指标召回 (Qdrant 向量搜索)
  ├── 合并 + 过滤
  ├── LLM 生成 SQL
  ├── SQL 语法校验
  ├── SQL 安全防火墙
  └── 执行 SQL → 返回结果
      ↓
MySQL (元数据 + 模拟数仓)
```

## 核心技术栈

| 层级 | 技术 | 用途 |
|------|------|------|
| 框架 | FastAPI + LangGraph | 后端接口 + AI 工作流编排 |
| 模型 | DeepSeek-v4-pro | SQL 生成与修正 |
| 向量化 | TEI + bge-large-zh-v1.5 | 文本 → 1024 维向量 |
| 向量库 | Qdrant | 字段/指标语义搜索 |
| 全文索引 | Elasticsearch + IK 分词 | 字段取值关键词搜索 |
| 数据库 | MySQL 8.0 | 元数据 + 数仓 |
| 前端 | React + Vite + Tailwind | 聊天式交互界面 |
| 测试 | pytest + ruff | 单元测试 + 代码规范 |

## 快速开始

### 1. 环境准备

```bash
# Python 3.14+
# Docker Desktop
# Node.js 22+
```

### 2. 启动基础设施

```bash
cd docker
docker compose up -d mysql qdrant elasticsearch embedding
```

### 3. 安装依赖

```bash
# Python 依赖
uv sync

# 前端依赖
cd frontend && pnpm install && cd ..
```

### 4. 下载 Embedding 模型

```bash
# 使用国内镜像加速
HF_ENDPOINT=https://hf-mirror.com uv run hf download BAAI/bge-large-zh-v1.5 --local-dir docker/embedding/bge-large-zh-v1.5
```

### 5. 配置 API Key

编辑 `.env` 文件，填入你的 LLM API Key：

```env
LLM_API_KEY=your_api_key_here
```

### 6. 构建元数据知识库

```bash
uv run python -m app.scripts.build_meta_knowledge -c conf/meta_config.yaml
```

### 7. 启动服务

```bash
# 后端
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

# 前端（新终端）
cd frontend && pnpm dev
```

访问 http://localhost:5173 开始使用。

## 项目结构

```
shopkeeper-agent/
├── app/                    # Python 后端
│   ├── agent/              # LangGraph 节点（召回、生成、校验、执行）
│   │   ├── nodes/          # 11 个处理节点
│   │   └── graph.py        # 工作流编排
│   ├── core/               # SQL 安全防火墙
│   ├── repositories/       # 数据库仓储层（MySQL/Qdrant/ES）
│   ├── clients/            # 客户端管理器（Embedding/MySQL/ES/Qdrant）
│   ├── api/                # FastAPI 路由 + 依赖注入
│   └── conf/               # 配置管理
├── tests/                  # 单元测试（20 个用例）
├── prompts/                # LLM Prompt 模板
├── docs/notes/             # 学习笔记 + 面试题库
├── docker/                 # Docker 配置
│   ├── docker-compose.yaml
│   ├── elasticsearch/      # ES + IK 分词器
│   └── mysql/              # 数据库初始化脚本
├── frontend/               # React 前端
└── conf/                   # 元数据配置（表/字段/指标定义）
```

## 安全特性

- **SQL 防火墙**：三层检查（关键字黑名单 + SELECT 白名单 + 注入检测）
- **语法校验**：EXPLAIN 预演，不实际执行
- **错误修正**：语法错误时自动调用 LLM 修正

## 学习笔记

项目附带了完整的学习文档，适合面试准备：

- `docs/notes/校招面试题库.md` — 42 题，3 个核心故事
- `docs/notes/藤子的Python成长笔记-全记录.md` — 40 个 Python 知识点
- `docs/notes/完整代码变更档案-20260629.md` — 11 个文件逐行对比

## License

MIT
