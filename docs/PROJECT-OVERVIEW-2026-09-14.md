# Shopkeeper Agent · 项目总览 + 进度时间线（2026-09-14 梳理）

> 写给未来的自己、想了解项目全貌的人、面试复盘。
> 数据截至 git HEAD = `9ff28f5`（51 commits / ~4 人天 / 2026-06 月底 ~ 2026-07-23）。
> 9-14 因项目误删，从 Codex 会话存档 + GitHub 仓库完整恢复，代码未变更。

---

## 0. 一句话

**自然语言驱动的电商问数 AI Agent**——业务方用大白话提问，系统自动检索表结构、生成 SQL、过 5 层 SQL 防护、执行查询。不是套壳 LLM，而是 LangGraph + Multi-Agent 反思 + 数据飞轮的完整 RAG 系统。

---

## 1. 当前状态（2026-07-23 快照）

| 维度 | 数据 |
|---|---|
| **代码** | 13017 行 Python / 121 文件 / 22 LangGraph 节点 / 15 prompts / 27 tests |
| **文档** | 45 篇（架构 4 + 设计决策 1 + interview-prep 3 + notes 26 + upgrade 2 + others 9）|
| **业务数据** | 7.2 万单 / 12 地区 / 120 SKU / 500 客户（2025-01 ~ 2026-07 真实业务）|
| **基础设施** | 6 个 Docker 容器：MySQL · Elasticsearch · Kibana · Qdrant · TEI Embedding · Redis |
| **Git** | 51 commits · main + feat/redis-session-store · 远程 `git@github.com:djjjs22/shopkeeper-agent.git` |

---

## 2. 当前架构（一张图）

```
   User Query
       ↓
   ┌─ Supervisor Graph (Multi-Agent, opt-in) ─────┐
   │  planner → Send API 拆 sub →                  │
   │  aggregator → reviewer（<0.7 触发反思回路）    │
   └────────────────────────────────────────────────┘
       ↓
   ┌─ Data Graph（17 节点，单 sub_query 链路）───┐
   │  classify_intent → 3 路召回                  │
   │  → merge → filter → generate_sql            │
   │  → validate_sql ⇄ correct_sql → run_sql     │
   └──────────────────────────────────────────────┘
       ↓
   ┌─ 基础设施层 ──────────────────────────────────┐
   │  MySQL（只读账号兜底）· Redis（会话）·         │
   │  LangSmith（全链路 trace）                      │
   └──────────────────────────────────────────────┘
       ↓
   ┌─ 数据飞轮（独立于基础设施，是"进化机制"）─────┐
   │  4 类失败归集 → SQL Pattern 库 →               │
   │  User Profile 5 档置信度策略 →                 │
   │  APScheduler 02:00 归档 + 03:00 衰减            │
   └──────────────────────────────────────────────┘
```

---

## 3. 5 个迭代阶段进度时间线

| # | 阶段 | 时间 | 主题 | 关键决策 | 量化 |
|---|---|---|---|---|---|
| **0** | MVP 期 | 2026-06 月底 | 套壳 LLM 直查 | 1 节点直查，无防护 | 错率 **6.5%** |
| **1** | 补安全 | 2026-07-05 ~ 07-09 | 5 层 SQL 防护 | 关键字 / 白名单 / 注入正则 / EXPLAIN / MySQL 只读账号 | 错率 **→ 2%**；危险 SQL 拦截 **100%** |
| **2** | 加 RAG | 2026-07-10 ~ 07-14 | 混合召回 | Qdrant 向量 + ES 全文 + Jieba 三路并行 + asyncio.gather | 召回命中率 **<50% → 78%** |
| **3** | Multi-Agent | 2026-07-15 ~ 07-17 | 反思自纠 | Supervisor 4 节点 + reviewer 评分 + MAX_LOOP=2 | 多 sub_query 延迟 **~30s → ~25s**；错率 **→ 0.8%** |
| **4** | 全面治理 | 2026-07-20 ~ 07-22 | Memory + Eval + 数据飞轮 | 3 层 Memory + APScheduler 03:00 衰减 + LangSmith 可观测 + 4 类失败归集 | 错率 **<0.5%**；上线 4 周维持不反弹 |

> 每阶段的触发 / 决策 / 产出 / 量化 / 反思见附录 §A

---

## 4. 关键能力（4 大方向）

| 方向 | 现状 |
|---|---|
| **RAG** | 3 路混合召回（Qdrant 向量 + ES 全文 + Jieba 业务词典）+ 失败归集反哺 |
| **Agent** | Multi-Agent 反思回路（MAX_LOOP=2 防延迟爆炸）+ Supervisor 4 节点 + Send API 并行 |
| **Eval** | 4 层（unit + smoke + eval_e2e + LLM-as-judge）+ baseline 对比脚本 |
| **数据飞轮** | 4 类失败归集 + SQL Pattern 库 + User Profile 5 档置信度策略 + Memory 衰减 |

> 各方向的 RFC / 设计文档见附录 §B

---

## 5. 误删与恢复（2026-08-22 → 2026-09-14）

| 日期 | 事件 |
|---|---|
| 2026-07-23 | 最后一次代码 commit（`9ff28f5`） |
| **2026-08-22 20:10** | **项目目录被误删**——只剩 `.idea/` + 空 `docker/` + PyCharm 模板 `main.py`，`.git/` 也消失 |
| 2026-09-07 | 用户在空目录里尝试启动 Codex 新会话，发现项目不可用 |
| **2026-09-14 13:45** | 用户回到目录请求梳理项目进度 |
| **2026-09-14 ~14:00** | **完整恢复**：从 Codex 会话存档提取 Write 调用 + `git@github.com:djjjs22/shopkeeper-agent.git` clone + 本地独有 4 个笔记合并 |
| 2026-09-14（现在）| 项目原位置（`~/Downloads/Agent/shopkeeper-agent/`）完整恢复，git 历史 51 commits 完整 |

---

## 附录 A：5 阶段详细复盘

参见 **`docs/掌柜成长记-0到1.md`**——每阶段的触发 / 关键决策 / 产出 / 阶段指标 / 反思都有记录。

---

## 附录 B：关键设计文档索引

| 方向 | 文档 |
|---|---|
| RAG | `docs/architecture/ai-application-pain-points-rfc.md`（20 刀 grill-me 痛点审查）|
| Agent | `docs/architecture/grill-me-production-pain-points-rfc.md`（18 项生产改进）|
| 数据飞轮 | `docs/upgrade-notes/2026-07-22-memory-eval-flywheel.md`（完整设计）|
| SQL 安全 | `docs/design-decisions/01-sql-safety.md`（5 层防护决策）|
| 升级路线 | `docs/AI应用架构升级路线.md`（现状痛点 + 升级方案 + ROI + 落地节奏，不换 LLM）|

---

## 附录 C：必读笔记（按优先级）

1. **`README.md`** — 项目主入口
2. **`docs/掌柜成长记-0到1.md`** — 5 阶段复盘
3. **`docs/architecture/ai-application-pain-points-rfc.md`** — 第一轮 grill-me 20 刀痛点
4. **`docs/architecture/grill-me-production-pain-points-rfc.md`** — 第二轮 18 项生产改进
5. **`docs/upgrade-notes/2026-07-22-memory-eval-flywheel.md`** — 数据飞轮完整设计
6. **`docs/design-decisions/01-sql-safety.md`** — SQL 5 层防护设计决策
7. **`docs/AI应用架构升级路线.md`** — 现状痛点 + 升级方案

---

## 附录 D：后续建议方向

- **8 月误删教训**：项目应建立异地备份机制（Time Machine / GitHub remote / 跨设备同步）
- **9 月恢复发现**：Codex 会话存档是隐藏的金矿——它记录所有 Write 操作，可在灾难时恢复
- **代码停止 1.5 月**：建议重跑测试、确认依赖兼容、为下一阶段演进做准备
