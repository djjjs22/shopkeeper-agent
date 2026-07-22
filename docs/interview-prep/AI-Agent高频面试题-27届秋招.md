# AI 大模型应用工程师高频面试题 · 27 届秋招备战

> **目标岗位**：AI 大模型应用工程师 / Agent 工程师 / AI 数据开发
> **核心项目**：shopkeeper-agent（LangGraph 21 节点 + Multi-Agent + RAG 电商问数系统）
> **使用方法**：面试前 1 小时过速查表 + 每个主题挑 1-2 题练 STAR；遇到原题直接调对应答案
> **格式约定**：每题含「问题 → 思路要点 → 关键术语 → 关键动作 → 数据 → Trade-off」6 段

---

## 📋 20 题速查表（按主题分组）

| #  | 主题 | 高频问题 | 难度 | 含 STAR 详解 |
|----|------|----------|------|:---:|
| 1  | 项目概览 | 简单介绍一下你做的 Agent 项目？ | ⭐⭐ | ✅ |
| 2  | 项目概览 | 为什么用 LangGraph 而不是 LangChain？ | ⭐⭐⭐ | — |
| 3  | 项目概览 | 画一下你这个 Agent 的整体架构和数据流 | ⭐⭐ | — |
| 4  | 项目概览 | 这个项目最大的技术亮点是什么？ | ⭐⭐⭐ | ✅ |
| 5  | RAG 检索 | 你们 RAG 召回怎么做？为什么三路并行？ | ⭐⭐⭐ | ✅ |
| 6  | RAG 检索 | embedding 检索 vs BM25 各自优劣？怎么选型？ | ⭐⭐ | — |
| 7  | RAG 检索 | 怎么评估 RAG 召回质量？ | ⭐⭐⭐ | — |
| 8  | Prompt | 你怎么写 Prompt？模板化有什么好处？ | ⭐⭐ | — |
| 9  | Prompt | 为什么不直接让 LLM 输出 SQL，要 JSON+模板渲染？ | ⭐⭐⭐ | ✅ |
| 10 | Prompt | f-string 和 jinja2 模板的区别？你怎么选？ | ⭐⭐⭐ | — |
| 11 | Multi-Agent | 为什么需要 Multi-Agent？单 Agent 不行吗？ | ⭐⭐⭐ | ✅ |
| 12 | Multi-Agent | Planner-Aggregator-Reviewer 三角色怎么协作？ | ⭐⭐⭐ | — |
| 13 | Multi-Agent | 反思回路（reflection loop）有什么坑？怎么做保护？ | ⭐⭐⭐⭐ | — |
| 14 | 工程/SQL | SQL 安全怎么做？三层防火墙每层是什么？ | ⭐⭐⭐ | ✅ |
| 15 | 工程/SQL | LLM 生成的 SQL 准确率怎么保证？ | ⭐⭐⭐ | — |
| 16 | 工程/SQL | OOM 风险怎么处理？ | ⭐⭐ | — |
| 17 | 性能 | 延迟从 18s 降到 11s 怎么做到的？ | ⭐⭐⭐ | ✅ |
| 18 | 可观测性 | 怎么定位 Agent 线上问题？ | ⭐⭐⭐ | ✅ |
| 19 | 工程实践 | 测试覆盖度怎么做的？ | ⭐⭐ | — |
| 20 | 工程实践 | 项目里最有挑战的一个 bug 是什么？怎么排查的？ | ⭐⭐⭐⭐ | ✅ |

---

## A. 项目概览

### Q1. 简单介绍一下你做的 Agent 项目？

**思路要点（30 秒版）**：
> "我做了一个电商问数 AI Agent，业务分析师用自然语言查数据（订单、GMV、用户活跃），系统自动拆解需求 → 检索元数据 → 生成 SQL → 安全校验 → 执行 → 返回结果。技术栈 LangGraph 编排 + 多 Agent 协同 + jinja2 模板化 Prompt + 三层 SQL 防火墙。21 个 LangGraph 节点，单 query 平均 11s 端到端，复杂查询准确率 92%。"

**关键术语**：LangGraph、Multi-Agent、RAG、Context Engineering、Re-ranking、纵深防御

**关键动作**：
1. 业务定位：日均千次自然语言查数请求（真实生产数据）
2. 21 个 LangGraph 节点串成可观测执行链路
3. 多 Agent 拆分 complex query（环比增长率类）
4. 三层 SQL 防火墙 + LIMIT 兜底防 OOM

**数据**：35 个 commit / 30 篇学习笔记 / 21 节点 / 15 个 prompt 文件 / 126 个 Python 源文件 / 3764 行测试

**Trade-off**：
- 选了 LangGraph 而不是 LangChain AgentExecutor：图结构更易调试、可观测、回放
- Multi-Agent opt-in 接入：新建 `supervisor_graph.py` 不破坏老 13 节点生产链路

---

### Q4. 这个项目最大的技术亮点是什么？

**思路要点**：
> "把 LLM 干的事压缩到只做'语义层'（输出结构化 JSON），确定性工作（SQL 生成、SQL 校验、SQL 拼接）全部用代码模板 + 正则 + 白名单搞定。这样单元测试可写、改业务不动 prompt、SQL 安全可控。"

**关键术语**：LLM 角色压缩、Context Engineering、纵深防御、可观测性

**关键动作**：
1. **RFC 刀1**：让 LLM 只输出结构化 JSON intent，SQL 用 jinja2 模板渲染（RFC `ai-application-pain-points-rfc.md`）
2. **Multi-Agent**：Planner 拆 sub_queries + Aggregator 合并 + Reviewer 反思（max_loop=2 保护）
3. **SQL 纵深防御**：关键字黑名单 + SELECT 白名单 + 注入特征检测 + fetchmany 兜底
4. **可观测性**：`@timed_node` 装饰器 + 每个节点耗时日志 + LangGraph state 全字段可序列化

**数据**：
- 准确率 78% → 92%（+14pp）
- 错误率 22% → ~8%
- 181 项回归测试零新回归

**Trade-off**：
- LLM 角色压缩 vs 灵活性：选前者，因为业务对"SQL 准确性 + 可测试性"要求高于"LLM 直觉"
- Multi-Agent 接入：选 opt-in（独立 supervisor_graph），不破坏老图

---

## B. RAG 检索

### Q5. 你们 RAG 召回怎么做？为什么三路并行？

**思路要点**：
> "我们召回的目标是拿到正确的表/列/度量。三路并行对应三种语义维度：① Column 向量召回（Qdrant，按 embedding 找相似列名）② Value 文本召回（ES BM25，按关键词找值字典）③ Metric 向量召回（Qdrant，按 embedding 找相似度量）。三路并行通过 LangGraph 的 `Send` API 同时跑，结果在 merge_retrieved_info 节点合并去重。"

**关键术语**：三路并行召回、Qdrant 向量库、ES BM25、Send API、合并去重、维度继承

**关键动作**：
1. **`extract_keywords` 节点**：LLM 抽出查询关键词（含继承的实体/条件/维度）
2. **`recall_column` / `recall_value` / `recall_metric` 三个节点并行**：分别走 Qdrant(Column) + ES(Value) + Qdrant(Metric)
3. **`merge_retrieved_info` 节点**：合并三路结果，按优先级去重
4. **`filter_table` / `filter_metric` 双层过滤**：根据 query 上下文裁剪低相关召回
5. **`add_extra_context` 节点**：补充业务规则 + 同义词字典 + 时间扩展

**数据**：复杂 query 召回耗时从 4s 降到 1.8s（并行节省 60%）

**Trade-off**：
- 向量 vs 关键词：Column/Metric 用向量（语义相似），Value 用 BM25（精确匹配业务值如 "华东/iphone15"）
- 并行 vs 串行：选并行，但需要在 merge 阶段做去重和优先级判断

---

## C. Prompt Engineering

### Q9. 为什么不直接让 LLM 输出 SQL，要 JSON + 模板渲染？

**思路要点**：
> "三个原因：① 可测试：JSON intent 是结构化的，单元测试能 assert 字段；直接出 SQL 没法写确定性测试 ② 可控性：SQL 风格（缩进、关键字大小写）由模板控制，不依赖 LLM 输出；改 SQL 风格不动 prompt ③ 安全性：SQL 模板渲染后还要过防火墙校验，LLM 直接出 SQL 没法做白名单校验。"

**关键术语**：LLM 角色压缩、结构化输出、可测试性、jinja2 模板渲染

**关键动作**：
1. **`generate_intent` 节点**：LLM 输出 Pydantic `Intent` Schema（表、字段、聚合、过滤、分组、排序）
2. **`generate_sql` 节点**：纯渲染——JSON → jinja2 → SQL（**不再调 LLM**）
3. **`validate_sql` + `correct_sql` 节点**：SQL 防火墙 + LLM 修正确（仅修，不重写）

**数据**：从 30+ 个 SQL 风格 bug（缩进不一、大小写乱）降到 0

**Trade-off**：
- 灵活性 vs 可控性：选后者。LLM 直出 SQL 灵活但难调；模板渲染可控但要写好模板
- 性能：模板渲染比 LLM 调用快两个数量级，hot path 用模板，cold path 才用 LLM

---

## D. Multi-Agent

### Q11. 为什么需要 Multi-Agent？单 Agent 不行吗？

**思路要点**：
> "单 Agent 在简单查询上够用（'TOP 3 商品' 这种），但在复杂查询上不行——比如 '本月环比增长率'，单 LLM 要一次性输出 3 段业务 intent JSON（本月销售额 + 上月销售额 + 增长率），容易漏字段、口径错、SQL 嵌套写错。我们测下来单 Agent 在这类 query 上准确率 78%，延迟 18s。Multi-Agent 拆成 Planner → Data Agent(并行 sub) → Aggregator → Reviewer，复杂 query 准确率提到 92%，延迟降到 11s。"

**关键术语**：Multi-Agent、Sub-task 拆分、depends_on 依赖图、并行执行、反思回路

**关键动作**：
1. **Planner 节点**：LLM 拆 sub_queries，每个 SubQuery 含 `depends_on: list[int]`
2. **Pydantic 强校验**：`QueryPlan.model_validator` 强制 ids 连续、引用必须存在、≤ 5 防拆太碎
3. **Data Agent 并行**：LangGraph `Send` API 按依赖图并行调度
4. **Aggregator 合并**：LLM 合并多 sub 结果为一段话 + 1 张图
5. **Reviewer 反思回路**：confidence < 0.7 时 retry，max_loop=2 保护

**数据**：准确率 78% → 92%（+14pp），延迟 18s → 11s（-40%），错误率 22% → ~8%

**Trade-off**：
- Multi-Agent vs 单 Agent：选 Multi-Agent（针对复杂 query），简单 query 走单 Agent 路径（省成本）
- Planner 失败兜底：Planner 解析失败 → 降级为单 sub_query（不拆，走原 13 节点路径）

---

## E. 工程 / SQL 安全

### Q14. SQL 安全怎么做？三层防火墙每层是什么？

**思路要点**：
> "纵深防御思想，分四层（不是三层）：① 关键字黑名单：拦截 DROP/DELETE/UPDATE/ALTER 等写操作 + SLEEP/BENCHMARK 时间盲注 + 系统表查询 + CALL/SET ② SELECT 白名单：只允许 SELECT/WITH 开头 + 表名必须来自白名单（从 meta_db 启动加载，零代码改）③ 注入特征检测：UNION SELECT / OR '1'='1' / 块注释绕过（`UNION/**/SELECT`） ④ fetchmany 防 OOM：万一前 3 层漏了，第 4 层 `fetchmany(1000)` + `truncated` 字段兜底。"

**关键术语**：纵深防御、正则 `\b` 词边界、`re.DOTALL`、`re.IGNORECASE`、非贪婪匹配、`fetchmany` 防 OOM、异常脱敏

**关键动作**：
1. **第一层关键字黑名单**（`sql_safety.py:60-67`）：`\bDROP\b` 等危险关键字，用 `\b` 词边界防误匹配
2. **第二层 SELECT 白名单**（`sql_safety.py:153-177`）：只允许 `SELECT`/`WITH` 开头 + 表名白名单动态加载
3. **第三层注入特征**（`sql_safety.py:267-274`）：UNION SELECT / 时间盲注 / 块注释绕过
4. **第四层 fetchmany 兜底**（`dw_mysql_repository.py:79`）：`fetchmany(1000)` 替代 `fetchall()`
5. **错误脱敏**：`run_sql.py` 用 `list[tuple[str, str]]` 错误特征→友好文案映射表

**数据**：补 4 类盲区（SLEEP/系统表/漏拦写操作/块注释绕过），3 个测试类（test_sql_safety / test_sql_stability_smoke / test_sql_template）

**Trade-off**：
- 顺序问题（注入先 vs 关键字先）：选关键字先——生产中增删改查最高频，先拦内部
- 错误信息泄露：MySQL 异常直接 `str(e)` 推前端会暴露表结构，必须分类脱敏

---

## F. 性能 / 可观测性

### Q17. 延迟从 18s 降到 11s 怎么做到的？

**思路要点**：
> "三方面优化：① Multi-Agent 并行调度：sub-0 和 sub-1 同时跑，省 5s ② 简单 LLM 调用：拆 sub 后每个 LLM 任务变简单，平均快 2s ③ jinja2 模板化 SQL：generate_sql 节点从 LLM 改成纯渲染，hot path 不调 LLM，省 3s ④ embedding 并发：3 路召回用 `asyncio.gather` 并发。"

**关键术语**：Send API 并行、jinja2 渲染、asyncio.gather、LLM 调用简化、hot path

**关键动作**：
1. **多 Agent 并行**：sub-0/sub-1 用 `Send` API 并行，sub-2 等依赖完成
2. **LLM 简化**：每个 sub LLM 只输出"本月销售额"这种简单 intent
3. **jinja2 渲染**：generate_sql 从 LLM 改成模板，单次省 3s
4. **三路召回并发**：Qdrant + ES + Qdrant 用 `asyncio.gather`

**数据**：18s → 11s（-40%），sub-0/sub-1 并行省 5s，简单 LLM 平均快 2s

**Trade-off**：
- 并行 vs 串行：并行要处理 sub 之间的依赖（depends_on）
- 模板渲染 vs LLM 直出：选模板渲染（可控 + 快 + 可测），仅在 cold path 用 LLM

---

### Q18. 怎么定位 Agent 线上问题？

**思路要点**：
> "三层诊断：① LangGraph state 全字段可序列化：用 `logger.info(f"state keys={list(state.keys())}")` 在节点入口打印残缺 state；用 LangGraph Studio/AST 可视化执行流 ② 每个节点耗时埋点：`@timed_node` 装饰器自动打印节点耗时，定位慢节点 ③ fallback 兜底文案做反向定位：'未查到相关数据' = 所有 sub 失败，'fallback SELECT 1' = 上游 intent 生成失败——一眼定位。"

**关键术语**：state 可序列化、`@timed_node`、LangGraph Studio、兜底文案反向定位、节点耗时

**关键动作**：
1. **`@timed_node` 装饰器**：所有节点自动打点，输出耗时
2. **`logger.info(state keys=...)`**：在节点入口打印 state 字段，排查残缺 state bug
3. **兜底文案设计**：
   - generate_intent 失败 → `SELECT 1 AS fallback` → 前端显示 1 行（**反向定位上游**）
   - 所有 sub 失败 → "未查到相关数据"
   - Planner 失败 → 降级单 sub_query（兜底链路）
4. **request_id 串联日志**：每条 query 一个 req_id，日志全链路可追

**数据**：4 个真实 bug 通过这套机制定位：
- fallback=1 → 定位到 generate_intent PromptTemplate 报错
- "未查到" → 定位到 rewrite_query 绝对时间解析失败
- TypeError runtime → 定位到 _gather_sub_results 被 RunnableLambda 包装
- 错误信息泄露 → 定位到 run_sql.py 直接 str(e)

**Trade-off**：
- 全量打点 vs 抽样：选全量（生产压力可接受，日志异步刷盘）
- LangGraph Studio vs 自建：选 LangGraph Studio（开发期）+ 自建 state 打印（生产期）

---

## G. 工程实践 / Bug

### Q20. 项目里最有挑战的一个 bug 是什么？怎么排查的？

**思路要点**（挑 `multi-agent SSE result 不推送`讲最有代表性）：

> "**症状**：multi-agent 模式问 query，前端 SSE 流走完但显示'流程已结束，后续未返回查询结果'。**根因**：`aggregator_node.py` 三处路径（单 sub / 多 sub / 异常）**全都没调 writer**，导致 SSE 流结束但没推 result 事件。**排查过程**：① 看前端 SSE 流发现只有 'planner 完成' 'data_agent 完成' 等中间事件，缺最终的 result ② 翻 `aggregator_node.py` 源码，发现三处都 `return {...}` 但没 `writer({"type": "result", ...})` ③ 对比 `respond_chitchat.py`（老节点），发现老节点是有 writer 的——是 multi-agent 新增节点遗漏。**修复**：3 处补 writer 调用。**教训**：SSE 流式场景下，每个叶子节点都必须发 result 事件，否则前端会兜底。"

**关键术语**：SSE 流式、writer 回调、叶子节点、兜底文案

**关键动作**：
1. **症状定位**：前端 SSE 流缺 result 事件
2. **源码比对**：老节点有 writer，新节点没有 → 找到差异
3. **修复 + 验证**：3 处补 writer，跑 e2e 验证 result 正确推送
4. **沉淀到文档**：MEMORY 决策 #2 永久记录

**数据**：修复后 multi-agent 模式端到端跑通，4 类 query（单 sub / 多 sub / 异常 / 反思回路）全部能正确返回

**Trade-off**：
- writer 强约束 vs 灵活：选强约束——每个叶子节点必须 writer，否则视为 bug
- SSE 流式 vs HTTP 一次性：选 SSE 流式（用户体验更好），但需要每个节点主动推事件

---

## 🎯 面试节奏建议

### 简历项目经验描述模板（5 分钟讲完）

> "**项目背景**：日均千次自然语言查数请求的电商问数 Agent。
> **架构亮点**：① 21 个 LangGraph 节点编排 RAG 工作流 ② Multi-Agent 协同（Planner-Aggregator-Reviewer） ③ jinja2 模板化 Prompt + 三层 SQL 防火墙 + fetchmany 防 OOM
> **量化成果**：环比类查询准确率 78%→92%，延迟 18s→11s，错误率 22%→8%
> **代码沉淀**：35 commit / 30 篇学习笔记 / 181 项回归测试零新回归
> **最大亮点**：让 LLM 只干语义层，确定性工作用代码模板 + 正则 + 白名单搞定"

### 高频追问应对

| 面试官可能追问 | 应对思路 | 对应题目 |
|---|---|---|
| "你们 LLM 用的是什么？" | MiniMax-M3 / MiniMax2.7，profile 切换 | — |
| "LangGraph 和 LangChain AgentExecutor 区别？" | 图结构 vs 链式 + 可观测性 + 状态管理 | Q2 |
| "为什么不直接用 GPT-4？" | 成本 + 数据安全 + 国产化合规 | — |
| "Prompt 怎么迭代的？" | Few-shot + jinja2 模板 + 边界示例 + 数字格式约束 | Q8 |
| "怎么防止 LLM 幻觉？" | 结构化输出 + 模板渲染 + SQL 防火墙 + 反思回路 | Q9 / Q13 / Q14 |
| "你们的测试覆盖度？" | 19 个测试文件 3764 行 + multi-agent 新增 30 测试 100% 过 | Q19 |
| "线上问题怎么排查？" | 节点埋点 + state 打印 + 兜底文案反向定位 + req_id 串联 | Q18 |

---

## 📚 参考资料（基于真实项目文档）

| 题目 | 对应项目文档 |
|------|--------------|
| Q1/Q3/Q4 | `README.md` + `docs/architecture/ai-application-pain-points-rfc.md` |
| Q5/Q6/Q7 | `docs/notes/召回并行化与Prompt改造-20260710.md` |
| Q9/Q10 | `docs/notes/PromptTemplate迁移jinja2-20260717.md` |
| Q11/Q12/Q13 | `docs/notes/multi-agent-改造-20260717.md` |
| Q14/Q15/Q16 | `docs/notes/SQL安全深化-LIMIT兜底与脱敏-20260720.md` |
| Q17 | `docs/notes/性能优化与并发-20260720.md` |
| Q18 | `docs/notes/error-handling-policy.md` |
| Q20 | `docs/notes/multi-agent-改造-20260717.md` + `docs/notes/20260720-改动总览.md` |

> 💡 **使用建议**：面试前 1 小时过一遍速查表，对应主题挑 1-2 题 STAR 练口头表达；遇到原题直接调对应答案，未覆盖的题目按"思路要点 + 关键术语 + 关键动作 + 数据 + Trade-off"5 段自由发挥。