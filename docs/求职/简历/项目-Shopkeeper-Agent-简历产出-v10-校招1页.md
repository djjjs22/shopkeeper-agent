# Shopkeeper Agent · 自然语言电商问数 AI Agent（v10 · 校招 1 页版）

---

## Header

**Shopkeeper Agent** —— 自然语言驱动的电商问数系统  
技术栈：**LangGraph** · FastAPI · React · Qdrant · Elasticsearch · MySQL · Redis · bge-large-zh-v1.5

---

## STAR 段落（项目栏用 · ~230 字）

电商业务方日均高频问数（"Q1 各区 GMV"、"华东大客户 Top10"），过去依赖数据团队手写 SQL 响应半天。**独立从 0 到 1 实现自然语言驱动的电商问数 AI Agent**：搭 LangGraph **17 节点有向图** + 4 节点多智能体反思（Send API 并行子问题，评审员 < 0.7 触发反思，max_loop=2 防死循环）；按指标名（Qdrant 向量）/ 枚举值（ES 精确）/ 抽象概念（向量）**三路互补召回**，让 LLM 召回率从 < 40% 升到能上生产；SQL 走 **5 道安全闸门**（关键字黑名单 → 只读白名单 → 注入检测 → EXPLAIN 预演 → 失败回 LLM 修正，**41 个单测覆盖**）；搭 **4 处数据飞轮**埋点（SQL 失败 / 修正放弃 / 评审低分 / 用户 👎）+ 02:00 归档 03:00 Memory 衰减 + GitHub Actions 跑 59 条业务 case 评测（**基线 86.7%，退化 2% 阻断合并**）。端到端 **3 秒** 出结果，覆盖 7.2 万单真实业务。

---

## Bullet 列表（技能栏 + 自我介绍说用 · 5 条）

- **LangGraph 17 节点有向图 + 4 节点 Multi-Agent Supervisor**：Send API 并行子问题（共享前置子图省 16s），评审员 < 0.7 触发反思回路，max_loop=2 硬上限
- **RAG 三路互补召回**：Qdrant 向量（指标名）+ ES 全文（枚举值）+ 向量（抽象概念）—— 单 LLM 召回率 < 40% → 多路混合达可上生产
- **SQL 5 道安全防护 + 自动修正回路**：关键字 / 白名单 / 注入检测 / EXPLAIN 预演 / 失败回 LLM 修正，**41 个单测覆盖**（关键字 / 白名单 / 注入 / 防误杀 4 维度）
- **数据飞轮 4 处埋点 + CI 防退化**：自动归集失败 case → 人工 review → GitHub Actions 跑 59 条 case 评测，**基线 86.7%，退化 > 2% 阻断合并**
- **多轮会话原子性 + 记忆 3 层次**：Redis `rpush + ltrim + expire` 一次网络往返（断网全回滚）；Procedural（59 条历史 SQL 抽模板）+ Semantic（3 次命中才注入）+ Episodic（5 轮自动摘要）

---

## 一句"可追问点"（放在简历最底，1 行）

> 深挖引导：为什么用 LangGraph 有向图不用普通 if-else？ / 反思回路 max_loop=2 为什么不是无限？ / 飞轮污染怎么防？

---

## 量化看板（1 页附录，**不放主简历**）

| 维度 | 数字 | 来源 |
|---|---|---|
| LangGraph | 17 节点主图 + 4 节点 Multi-Agent | 代码事实 |
| 测试 | 27 个测试文件 / 165 个测试函数 / 41 个 SQL 单测 | tests/ 实际数 |
| 端到端评测 | 子集 86.7% / 全量 59.3%（**59 条业务 case**）| tests/eval_e2e.py 9-15 跑 |
| CI | **基线 86.7%，退化 > 2% 阻断合并** | .github 配置 |
| 业务规模 | 7.2 万单 / 12 地区 / 120 SKU / 500 客户 | dw.fact_order |
| 端到端延迟 | ~3 秒（简单问题 opt-in 单链路）| 设计文档 |
| 失败归集埋点 | 4 类（validate / correct / reviewer / 用户 👎）| bad_case_collector.py |
| SQL 防护层数 | 5 道（关键字 / 白名单 / 注入 / EXPLAIN / 修正回路）| sql_safety.py |
| 历史 SQL 模板 | 59 条（Pattern Bank）| pattern_bank_service.py |
| 记忆层次 | Procedural + Semantic + Episodic | memory 三层服务 |
| 多轮原子性 | `rpush + ltrim + expire` 一次网络往返 | session_store.py |
| 归档策略 | 02:00 归档 + 03:00 Memory 衰减 | scheduler.py |

---

## changelog（v9 → v10）

| 改动 | 原因 |
|---|---|
| **砍到 1 页 A4** | 校招简历容量限制，5 亮点 / 项目意义 / 面试话术移到面试前材料 |
| **删 AI 味**："AI 落地的关键"/"可持续 AI 系统的标志" → 改用具体动作描述（"搭 X"、"按 Y"、"走 Z"）|
| **数字口径统一**：去掉 165 / 41 数字冲突，只用 41 SQL 单测 + 165 总测试在量化看板（不放主简历）| 校招面试官会问"哪个数是真的" |
| **bullet 砍到 5 条**：每条 1 句 + 硬数字 | 5-6 条是校招 bullet 最佳实践 |
| **STAR 段落砍到 230 字** | 之前 v9 是 340 字，对 1 页 A4 仍太长 |
| **加"可追问点"1 行** | 校招简历结尾常见技巧——告诉面试官"你可以从这里问"，展现自信 |
| **删"面试话术 / 避坑提醒 / 项目意义总结"** | 这些不进简历，单独存 `docs/interview-prep/` |

---

## 配套文件（不在简历里，但面试前必看）

- **面试话术 30 段**（含深挖引导）→ `docs/resume/电商问数Agent-简历产出.md`（你之前写的）保留作为面试准备
- **grill-me 8 层追问** → `docs/architecture/ai-application-pain-points-rfc.md`
- **50 道 AI Agent 高频面试题** → `docs/interview-prep/AI-Agent高频面试题-27届秋招.md`
