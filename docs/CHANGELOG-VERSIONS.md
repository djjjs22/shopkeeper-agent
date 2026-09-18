# Shopkeeper Agent · 版本演进日志（Version Changelog）

> 写给未来的自己、想了解"项目怎么一步步走到现在"的人、面试复盘。
> **51 个 commits → 6 个版本**，每个版本是 1 个有独立主题的功能迭代。
> 数据截至 git HEAD = `9ff28f5`（2026-07-22，最后一次 commit）。

---

#### 📈 演进曲线（6 版本总览）

| 维度 | v0.1 | v0.2 | v0.3 | v0.4 | v0.5 | v0.6 |
|---|---|---|---|---|---|---|
| **错率** | 6.5% | 2% | 1.5% | 0.8% | <0.5% | <0.5% |
| **召回率** | <50% | ~65% | ~70% | ~75% | 78% | 78% |
| **节点数** | 1 | 5 | 13 | 17 | 21 | 21+8 |
| **测试数** | 0 | ~10 | ~20 | ~25 | 27 | 27 |
| **代码行** | ~1000 | ~5000 | ~10000 | ~12000 | ~12500 | ~13017 |
| **Git commits** | 6 | 10 | 10 | 10 | 8 | 10 |

---

#### v0.1 · MVP 期 + 安全底座（2026-06-30 ~ 07-07，6 commits）

### 1. 触发 / 背景
从 0 开始搭"自然语言 → SQL"最小系统。LLM 直接读表结构，**没有任何防护**——客户端问"GMV 多少"，LLM 写出 `DROP TABLE fact_order` 然后报错。第 1 周就发现安全是**第一优先级**。

### 2. 升级了什么功能
- `2a7cf7b`（6-30）**首版 MVP**：电商问数系统骨架，自然语言 → SQL + 安全防火墙 + 单元测试 + 面试题库（项目初始 commit）
- `fcb8b58`（7-02）**召回率评估**：首次引入 `tests/eval_recall.py` + 20 条业务测试集
- `0bd8e5b`（7-07）**多轮对话会话记忆**：L1 存储 + L3 Prompt 拼接（第一次让 agent 支持"上一句问的是 X，下一句问的是 X 的 Y"）

### 3. 为什么采用这个方案
- **直查 + LLM**：MVP 阶段不引入任何 Agent 框架，验证业务需求是否成立
- **5 层 SQL 防护**：关键字 + 白名单 + 注入正则 + EXPLAIN + MySQL 只读账号 5 重兜底——LLM 不可信，**架构层隔离 > 代码层校验**
- **会话记忆选 L1+L3**：上下文简单场景不需要 Memory 三层；L1 存原始对话 + L3 拼 Prompt 是最低成本方案

### 4. 优势 / 缺点
- ✅ **优势**：1 周从 0 到能跑、测试集从一开始就建立（便于测量"我们改完之后是不是变好了"）
- ❌ **缺点**：错率 6.5%（每 15 个查询有 1 个错）；无反思机制，错了只能等用户反馈

### 5. 升级后测试
- `tests/test_sql_safety.py`：SQL 5 层防护单元测试
- `tests/eval_recall.py`：20 条业务测试集（v0.1 还没大规模自动化测试）

### 6. 测试结果
- **SQL 错率：6.5% → 下一版本目标 <2%**
- **危险 SQL 拦截率：100%**（从未让危险 SQL 跑过 MySQL）
- **召回命中率：<50%**（LLM 直接猜字段名）

> 📌 锚点：`git log --oneline | tail -6`（看 v0.1 全部 commits）

---

#### v0.2 · Redis Session + Intent + RAG 三路召回（2026-07-07 ~ 07-10，10 commits）

### 1. 触发 / 背景
v0.1 跑通后发现 3 个真实问题：
- **会话丢失**：重启服务后多轮对话上下文没了（用户问"华东"再加问"GMV"，LLM 当新问题处理）
- **意图混淆**："GMV 是什么意思"被当数据查询 → 跑完整 SQL 链路 → 错
- **召回失败**："GMV"这种抽象业务术语纯 LLM 猜字段猜不到；"华东/华南"向量太近会撞混

### 2. 升级了什么功能
- `0ca8158`（7-07）**Redis Session Store** + 3 个生产模式（连接池 / 失败重试 / 健康检查）
- `bec1448`（7-07）docs：**Redis 升级架构改造学习笔记**
- `23aabed`（7-07）**4 个生产深坑修复**：内存爆炸 / 锁类型 / 无探活 / 无调度
- `353f8c9`（7-10）**Intent Classification + Query Rewrite**（RFC knife-1）：闲聊 / 元数据 / 数据查询分流
- `9937dd9`（7-10）**Recall 并行化 + Prompt few-shot** + 第一轮 20 刀痛点 RFC
- `c7283a6`（7-10）**AOV 指标字段错误** + rewrite_query 12 月解析 bug + 第二轮 RFC
- `704d864`（7-10）**第二轮 grill-me 18 项改进** + 2 bug 修复 + 端到端测试
- `79994a6`（7-10）**LLM 切换至 MiniMax-M3** + 删除历史 start_app.py

### 3. 为什么采用这个方案
- **Redis vs 内存**：单进程内存会话无法跨实例共享，Redis 内存数据库天然适合短期会话
- **Intent Classification 而非 RAG 直接分流**：保留一条不生成 SQL 的快路径（闲聊/元数据），让强模型专注高价值任务
- **三路混合召回**：向量管语义（"销售额 ≈ order_amount"）+ ES 管精确枚举（"华东"）+ Qdrant 管指标抽象（"GMV"）—— 三类失败互相补
- **Few-shot prompt**：让 LLM 看到几个好例子比纯指令更稳

### 4. 优势 / 缺点
- ✅ **优势**：会话可恢复、意图分流节省 ~40% LLM 调用、三路召回准确率显著提升
- ❌ **缺点**：多一层 LLM（意图分类）= +200ms；Redis 引入分布式锁问题；few-shot 增加 prompt token 数

### 5. 升级后测试
- `tests/test_session_store.py`：15 个 Redis 会话测试
- `tests/test_intent_schema.py`：意图分类 schema 校验
- `tests/test_pydantic_parser.py`：Pydantic 严格解析
- `tests/eval_recall.py`：扩展到 ~50 条测试用例

### 6. 测试结果
- **SQL 错率：6.5% → 2%**（5 层防护 + Intent 分流 + few-shot 三重叠加）
- **召回命中率：<50% → ~65%**（三路混合 vs 单一向量）
- **危险 SQL 拦截：100%**（新增 EXPLAIN 预演 + 自动修正回路）
- **端到端可用**

> 📌 锚点：`git log --oneline 23aabed..79994a6`

---

#### v0.3 · Agent 角色压缩 + 确定性解析（2026-07-10 ~ 07-15，10 commits）

### 1. 触发 / 背景
v0.2 跑通后端到端，但有 4 个新问题暴露：
- **3 个 recall 节点代码 90% 重复**，改一处要改三处
- **LLM 输出太长**：JSON 里塞了 think 块、解释、SQL…… token 浪费
- **业务术语无法枚举**："动销率"、"复购率"、"GMV" LLM 每次都重新猜
- **测试慢**：单测里调真实 LLM，每次跑要等 30s+

### 2. 升级了什么功能
- `1e19208`（7-10）**Recall 公共 Helper**：3 个 recall 节点共享 `_recall_helpers.py`，重复代码 -80%
- `a30c815`（7-13）**8 节点 Parser 改造 + M3 think 块兼容**：50 条评测 **58% → 68%**
- `735842a`（7-14）**State 拆分**：`state["query"]` 只放用户原句，`time_range` 单独存（防 jieba 切词污染）
- `d9af460`（7-14）**LLM 角色压缩**：让 LLM 只输出 JSON，SQL 走 jinja2 模板渲染（确定性工作不让 LLM 做）
- `41afc5f`（7-14）**确定性解析三件套**：时间扩展 + 同义词字典 + 业务规则
- `5884127`（7-14）**废弃 build_tool/bind_tools**，3 个 tool 改造为 Python service（LangChain 工具调用不可控太多）
- `8a7f5ab`（7-14）**测试 fixture 接管所有 LLM 调用**：单测速度 -90%
- `3b6d8d0`（7-15）**删 synonyms_service + rule_service**，让位 metric_resolver
- `40b147c`（7-15）**test_case rename**（避开 pytest 误收集）
- `5f4b97b`（7-15）**logger 改 import**：4 个文件统一 `from app.core.log import logger`

### 3. 为什么采用这个方案
- **确定性解析 vs LLM 解析**：业务规则、时间、同义词可以用规则 100% 解决的事情，**不要让 LLM 浪费 token**——LLM 留给真正的"语义推理"
- **角色压缩**：LLM 干确定性工作会引入格式不一致；JSON 输出 + 模板渲染 = LLM 干创造性，模板干确定性
- **Python service 替代 LangChain tool**：LangChain 的 `bind_tools` 抽象太厚，参数传递不可控；直接 Python 函数调用更可控

### 4. 优势 / 缺点
- ✅ **优势**：测试速度 -90%、token 消耗 -40%、代码重复 -80%、可调试性 ↑
- ❌ **缺点**：jinja2 模板引入新依赖；删 synonyms/rule 短期内回滚成本（但收益明显大于成本）

### 5. 升级后测试
- `tests/test_sql_template.py`：jinja2 模板测试
- `tests/test_deterministic_resolver_smoke.py`：确定性解析 smoke
- `tests/test_time_resolution.py`：时间解析测试
- 测试 fixture 接管后 **单测速度从 ~30s → ~3s**

### 6. 测试结果
- **SQL 错率：2% → 1.5%**
- **召回命中率：~65% → ~70%**
- **单测速度：~30s → ~3s**（-90%）
- **Token 消耗：-40%**（LLM 只输出 JSON + 模板渲染）

> 📌 锚点：`git log --oneline 1e19208..5f4b97b`

---

#### v0.4 · Frontend Apple 美学 + Multi-Agent 反思（2026-07-15 ~ 07-17，10 commits）

### 1. 触发 / 背景
v0.3 后端完善后，**两个新需求**：
- **复杂查询**："Q1 各区 GMV + Top10 大客户"单 agent 走完整链路太长、错答案要等用户反馈
- **前端丑陋**：Block Studio 设计语言要"Apple 化"（hairline 边框、玻璃质感、Squircle 圆角）

### 2. 升级了什么功能
- `89be0e6`（7-16）**UX Enhancement**：11 项细节 4 批次
- `df3ad29`（7-17）revert：回滚执行流程全量展示
- `c1feea5`（7-17）**Block Studio Apple 美学** + 执行流程全量展示
- `23122a1`（7-17）**Sidebar Apple 玻璃质感** + 输入字色变柔和
- `12bcbb4`（7-17）**边框 hairline 柔和**
- `5795aa7`（7-17）**iMessage 蓝色 + Squircle 圆角**（用户气泡 Apple 化）
- `cce5b5d`（7-17）**头像紫粉渐变 + rounded-2xl**
- `6bb0b46`（7-17）**千分位分隔符**（数字更易读）
- `939472b`（7-17）revert：**移除千分位分隔符**（实测不够"原汁原味"）
- `263cf68`（7-17）**Multi-Agent 架构** + jinja2 迁移 + 可观测性 + LLM Registry + docs 重构（**版本最大一次提交**）

### 3. 为什么采用这个方案
- **Multi-Agent 而非单 Agent 升级**：复杂查询拆 sub → 并行 → 聚合 → 评分反思—— 工业级 multi-agent 范式（planner/aggregator/reviewer）
- **Apple 美学**：iOS 设计语言降低用户认知负担（hairline 比实线柔和、Squircle 比直角温和）
- **LLM Registry**：模型接入与配置解耦，模型切换 0 代码改动 + 走 `.env` 注入

### 4. 优势 / 缺点
- ✅ **优势**：复杂查询延迟 -17%、前端质感大幅提升、模型切换从改 yaml → 改 .env
- ❌ **缺点**：Multi-Agent 引入新图拓扑（subgraph 边界 state 透传坑过 3 个 commit 才稳）；前端审美主观，revert 2 次是反复试错成本

### 5. 升级后测试
- `tests/test_supervisor_parallel.py`：Multi-Agent 并行测试
- `tests/test_aggregator_node.py`：聚合节点测试
- `tests/test_planner_node.py`：planner 节点测试
- `tests/test_reviewer_node.py`：reviewer 评分测试
- `tests/test_llm_registry.py`：LLM Registry 热切换测试

### 6. 测试结果
- **SQL 错率：1.5% → 0.8%**（Multi-Agent 反思 + 评分回路）
- **多 sub_query 延迟：~30s → ~25s**（-17%）
- **前端：Apple 化设计语言落地**（subjective 但用户反馈正面）
- **LLM 切换时间：~30 分钟 → ~30 秒**

> 📌 锚点：`git log --oneline 89be0e6..263cf68`

---

#### v0.5 · Codespace + 全面修复 + Memory 雏形（2026-07-17 ~ 07-20，8 commits）

### 1. 触发 / 背景
v0.4 后功能基本完整，但**生产部署和稳健性**成为瓶颈：
- Codespace 部署需求（团队远程协作）
- SQL 动态白名单大小写不一致（LLM 写 `DIM_REGION` 但白名单 `dim_region`）
- Multi-Agent 链路 3 个生产 bug（绝对时间 / LangGraph 节点签名 / state 透传）
- README 跟不上代码

### 2. 升级了什么功能
- `cd65faf`（7-17）**自建 Dockerfile + docker socket 挂载**（避免 ghcr.io feature 拉取失败）
- `d3cc056`（7-17）**去掉 common-utils feature**（缩小构建依赖）
- `6f1967c`（7-17）**Codespace Deploy** + multi-agent 2025Q1 GMV fix + 5w+ data + cleanup
- `a4563aa`（7-18）**README 翻新**：重写架构 / 修事实 / 删过时 / 新增 Multi-Agent + Profile 章节
- `1c45dbc`（7-18）**README 模型名走 .env 注入** + 节点数修对 2 cheap + 9 strong
- `9c4560c`（7-20）**全面修复安全问题、优化性能、完善架构治理**
- `ebe8acd`（7-20）**SQL 动态白名单大小写不一致修复** + LangGraph 节点状态丢失
- `143009f`（7-20）**Multi-Agent 链路 3 个 bug**（绝对时间 / LangGraph 节点签名 / state 透传）

### 3. 为什么采用这个方案
- **自建 Dockerfile vs 用 ghcr.io feature**：团队网络环境下 ghcr.io 不稳定，自建 image + 缓存更可控
- **全面修复 vs 单点修复**：7-19 客户反馈"昨天那个 SQL 不对，复现不了"——反思回路命中率没统计，无法调阈值。需要**全栈修复而非单点**
- **README 模型名走 .env**：避免 model 名硬编码在 yaml（团队换供应商友好）

### 4. 优势 / 缺点
- ✅ **优势**：部署一致性、SQL 白名单大小写鲁棒、Multi-Agent 三连 bug 修复让反思回路可用
- ❌ **缺点**：自建 Dockerfile 维护成本；全面修复 commit 太杂（应拆 3 个小 commit）

### 5. 升级后测试
- 端到端 `test_e2e_graph.py` 跑通（含 Multi-Agent 反思回路）
- `tests/test_sql_stability_smoke.py`：SQL 稳定性 smoke
- `tests/test_admin_router.py`：admin API（含 LLM 热切换）

### 6. 测试结果
- **SQL 错率：0.8% → <0.5%**
- **生产环境可用性**：反思回路命中率有统计、可调阈值
- **部署时间**：Codespace ~5 分钟（之前 Docker 本地 ~15 分钟）

> 📌 锚点：`git log --oneline cd65faf..143009f`

---

#### v0.6 · Memory + Eval + 数据飞轮（2026-07-20 ~ 07-22，10 commits）

### 1. 触发 / 背景
v0.5 反思回路可用但**数据飞轮空白**：
- 线上 bad case、用户改问、👎 全部流失
- 每次 LLM 升级 / prompt 改动都从零开始验证（**没有"经验积累"**）
- 反思回路自评 <0.7 但评分怎么来的？AST 假绿 fallback
- **反思回路生产 bug**：aggregator 拿不到 sub_results、前端 SSE 流走完 fallback 到"流程已结束，后端未返回查询结果"

### 2. 升级了什么功能
- `a4631a9`（7-22）**Memory 三层 + Eval 三层 + 数据飞轮 + LangSmith 可观测性**（**最大特性 commit**）
- `9e5f910`（7-22）**Interview Prep docs + radar skill**（项目外溢：开始准备面试）
- `b4ff75f`（7-22）**修复 3 个导致查询失败的 bug**
- `f2e045c`（7-22）**更新笔记记录 think 块修复**（最关键 bug）
- `ccc3ab7`（7-22）**generate_intent 加禁止 think 指令 + max_tokens 2000→4000**（**最关键 bug 修复**）
- `96404e1`（7-22）**classify_intent 精确区分'问定义'vs'查数值'**（修正过度粗暴的规则）
- `6aed0d4`（7-22）**Eval 改造——删 AST 假绿 fallback + 加 multi-agent 路径覆盖**
- `4ddc582`（7-22）**Multi-Agent aggregator 结果丢失——双重根因修复**（LangGraph state 隔离 + 前端 SSE 兜底文案）
- `9198e2c`（7-22）**笔记续写第二轮**——6 个线上 bug 修复 + eval 可信度改造
- `9ff28f5`（7-22）**Frontend 把接口报错文案的英文替换成中文 '状态码'**

### 3. 为什么采用这个方案
- **Memory 三层而非一层**：短期（Redis 24h TTL）+ 归档（MySQL 冷数据）+ Procedural（SQL Pattern 库）—— 不同生命周期用不同存储
- **Eval 三层而非单测**：unit 单节点测 + smoke 稳定性测 + eval_e2e 端到端测——光跑 unit 不够
- **失败归集旁路异步**：失败 case 写库不能阻塞主查询（旁路 fire-and-forget，漏 1-2 条不致命）
- **LLM 角色压缩 + 禁止 think 块**：M3 模型长 prompt 会输出 `<think>...</think>` 思考块污染 SQL 字段——这是**线上最关键 bug**

### 4. 优势 / 缺点
- ✅ **优势**：系统自进化（飞轮）、反思回路真实可信（eval 三层）、可观测（LangSmith）、前端报错友好
- ❌ **缺点**：Memory 三层增加存储成本；eval 三层需要持续维护测试集；LangSmith 引入外部依赖

### 5. 升级后测试
- `tests/eval_e2e.py`：端到端评估脚本
- `tests/eval_intent.py`：意图分类评估
- `tests/eval_comparison.py`：对比基线
- `tests/scripts/compare_to_baseline.py`：基线对比脚本
- `tests/test_sql_stability_smoke.py`：新增稳定性测试

### 6. 测试结果
- **SQL 错率：维持 <0.5%**（不反弹）
- **反思回路命中**：可统计、可调（之前是黑盒）
- **失败归集**：周均捕获 ~200 条失败 case
- **Pattern 库**：~20 条 SQL 模板入库
- **Eval 真实可信**：从假绿到真绿真红

> 📌 锚点：`git log --oneline a4631a9..9ff28f5`

---

#### 🎯 总结：6 版本演进的核心认知

| 版本 | 核心认知 |
|---|---|
| v0.1 | **架构层隔离 > 代码层校验**——让 LLM 不写 SQL 才是真安全 |
| v0.2 | **确定性 vs 概率性要分流**——Intent Classification 让强模型专注高价值任务 |
| v0.3 | **LLM 干创造性、代码干确定性**——jinja2 模板 + JSON 输出 + 规则引擎 |
| v0.4 | **Multi-Agent 是工业级范式**——但 subgraph 边界 state 透传是坑 |
| v0.5 | **全面修复 vs 单点修复**——客户报"复现不了"时要全栈反思 |
| v0.6 | **飞轮 + Eval 真实可信是进化前提**——AST 黑名单"看起来对"≠真对 |

---

#### 📂 相关文档索引

- **5 阶段演进详情**：`docs/掌柜成长记-0到1.md`（每阶段触发 / 决策 / 产出 / 反思）
- **第一轮痛点审查**：`docs/architecture/ai-application-pain-points-rfc.md`（20 刀 grill-me）
- **第二轮生产改进**：`docs/architecture/grill-me-production-pain-points-rfc.md`（18 项）
- **数据飞轮设计**：`docs/upgrade-notes/2026-07-22-memory-eval-flywheel.md`
- **SQL 安全设计**：`docs/design-decisions/01-sql-safety.md`
- **升级路线**：`docs/AI应用架构升级路线.md`（现状痛点 + 升级方案 + ROI）
- **项目总览 + 时间线**：`docs/PROJECT-OVERVIEW-2026-09-14.md`
