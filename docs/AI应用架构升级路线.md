# Shopkeeper Agent — AI 应用架构升级路线

> 写给资深面试官看的项目进阶讲稿。
> 不是罗列 buzzword，是讲清「**现状痛点 → 升级方案 → ROI → 落地节奏**」四件套。
>
> 数据 + AI 复合视角，覆盖 4 大方向：**RAG / Agent / Eval / 工程**。
> 硬约束：**不换 LLM**，所有方案走「非模型层优化」（缓存 / 路由 / 并行 / 索引 / 数据飞轮）。

---

## 目录

- [0. 现状诊断 & 北极星](#0-现状诊断--北极星)
- [1. 性能与延迟优化](#1-性能与延迟优化)
- [2. RAG 召回质量升级](#2-rag-召回质量升级)
- [3. Agent 推理 & 自适应回路](#3-agent-推理--自适应回路)
- [4. Agent Memory ⭐](#4-agent-memory-)
- [5. Eval + 可观测性 + 数据飞轮 ⭐⭐](#5-eval--可观测性--数据飞轮-)
- [6. 新能力前瞻 & 路线图](#6-新能力前瞻--路线图)

---

## 0. 现状诊断 & 北极星

### 0.1 当前能力盘点

| 维度 | 现状 | 量化指标 |
|---|---|---|
| 召回 | 三路混合（Qdrant 字段/指标 + ES 枚举值 + Jieba 关键词） | 召回耗时 < 200ms，hit-rate 未系统化测量 |
| 生成 | 17 节点主图 + LLM Profile Registry（2 cheap + 9 strong） | 单 sub 端到端 ~3s |
| 校验 | 三层 SQL 安全 + EXPLAIN 预演 + 自动修正回路 | 41 个 sql_safety 单测全过 |
| 多智能体 | supervisor_graph（planner / aggregator / reviewer） | reviewer < 0.7 触发反思，max_loop=2 |
| 测试 | 19 个测试文件 / 165 个测试函数 | 单元覆盖可，**线上回归空白** |

### 0.2 三大短板（按痛感排序）

1. **延迟不可控**：用户主观感受"慢"。`~3s` 是平均值，但 p95 可能到 6-8s（强模型 retry + 自动修正回路）。**最痛，最先解决**。
2. **没有 Eval 闭环**：`tests/eval_e2e.py` 跑 5 个手写 case 全过 ≠ 系统鲁棒。改动后**无法量化收益**，等于裸跑。这是数据+AI 岗最致命的短板。
3. **无数据飞轮**：线上 bad case、用户改问、👍/👎 全部流失。每次 LLM 升级 / prompt 改动都从零开始验证，没有"经验积累"。

### 0.3 北极星指标

后续所有升级都挂在**这三个数**上，没有第四个：

```
┌─────────────────────────────────────────────────────────┐
│  NL2SQL 准确率 = 正确 SQL 数 / 总查询数                    │  ← 质量底线
│  p95 延迟                                                 │  ← 体验底线
│  周均 bad case 自动沉淀数                                  │  ← 进化速度
└─────────────────────────────────────────────────────────┘
```

**这三个数现在一个都没量。** 本文下面 6 章都是在回答"怎么把这仨量出来 + 怎么提升"。

---

## 1. 性能与延迟优化

### 1.1 现状拆解（先量，再优化）

```
用户提问
  │
  ├─ 召回 (200ms)         ┐
  │   ├ Qdrant 字段         │
  │   ├ Qdrant 指标         │ 这块已经很快，不是瓶颈
  │   └ ES 枚举值           │
  │                         ┘
  ├─ LLM 节点 (3-5s)      ← 90% 的耗时在这
  │   ├ extract_keywords
  │   ├ generate_intent
  │   ├ generate_sql       ← 大头
  │   ├ validate_sql       ← 偶尔失败
  │   └ correct_sql        ← 失败回路，每次 +3-5s
  └─ run_sql (<100ms)
```

**结论**：要降延迟只能动 LLM 节点。三条路——**少调 / 调快 / 不调**。

### 1.2 路径一：少调（削减 LLM 调用数）

| 方案 | 做法 | 预期收益 | 成本 |
|---|---|---|---|
| **Semantic Cache** | 用 Redis 存 `(query 向量, 结果)`，相似查询命中直接返回 | 命中率 20-40%，p50 砍半 | 2 天。需要 bge embedding 入 cache key |
| **意图短路** | classify_intent 后，闲聊/元数据查询完全跳过 generate 节点 | 这两条路径从 3s → 200ms | 1 天。已有路由逻辑，只是没短路 |
| **召回驱动跳过 generate_intent** | 召回置信度 > 阈值时，跳过 generate_intent 直接进 generate_sql | 单 sub 省 1 次 LLM 调用，~1s | 3 天。需要置信度信号（reranker score） |

### 1.3 路径二：调快（单次 LLM 调用更快）

| 方案 | 做法 | 预期收益 | 成本 |
|---|---|---|---|
| **Prompt 压缩** | 当前 prompt 把召回的 top-20 字段全塞进去，实际 LLM 只用 top-5。砍掉冗余 | 单次 -30% token，~0.8s | 1 天。配合 reranker 选 top-k |
| **流式返回 + SSE 透传** | 已有 SSE，但 SQL 生成完才返回。改成边生成边 stream，用户感知"在动" | 主观延迟砍半 | 1 天。前端配合 |
| **超时熔断 + 降级** | strong 节点设 8s 硬超时，超时切 cheap 重试 | p99 不再无界 | 半天。已有 retry，加 timeout 即可 |

### 1.4 路径三：不调（结果层缓存 / 预算）

| 方案 | 做法 | 预期收益 | 成本 |
|---|---|---|---|
| **SQL 结果缓存** | 同 SQL + 同时间窗（如"今天的 GMV"5 分钟内复用） | 重复查询 0ms | 2 天。SQL hash + TTL 策略 |
| **预聚合物化视图** | 高频查询（GMV/订单数按地区按月）预计算入 `dw_mv_*` 表 | 查询从秒级 → 10ms | 1 周。schema 改 + 调度任务 |
| **冷热数据分层** | 2025 H1 数据走 ClickHouse/OLAP，热数据留 MySQL | 历史查询提速 5-10× | 2 周。重，看 ROI |

### 1.5 优先级 & 路线图

```
Week 1（立刻能做，ROI 最高）
  ├ Semantic Cache          ← 预计砍 p50 40%
  ├ 意图短路                 ← 闲聊路径几乎零成本
  └ Prompt 压缩             ← 一处改动全局收益

Week 2-3（结构性优化）
  ├ SQL 结果缓存 + TTL
  └ 超时熔断（兜底 p99）

Month 2+（需要数据支撑才动）
  └ 物化视图（看高频 query 日志决定建哪些）
```

### 1.6 面试讲法

> "我做完性能优化后意识到一个事——**所有降延迟的方案都不需要换 LLM**。Semantic Cache 砍 p50、意图短路砍闲聊路径、Prompt 压缩砍 token、物化视图砍 SQL 执行。**模型不是瓶颈，工程才是**。"

---

## 2. RAG 召回质量升级

### 2.1 现状缺陷

当前三路召回（Qdrant 字段 / Qdrant 指标 / ES 枚举值）是**召回 = 给结果**，没有重排序，也没有查询理解。三个具体问题：

1. **Top-k 平权**：召回 20 个字段全塞 prompt，LLM 看到的信噪比低（实际只用 top-5）
2. **查询不解构**：用户说"统计 2025 Q1 各大区 GMV 环比"，系统直接拿整句去 embedding，没把"时间/维度/指标"拆开
3. **无结果质量信号**：召回回来什么就喂什么，无法判断"这次召回够不够好"

### 2.2 升级项（按 ROI 排序）

#### A. Reranker（bge-reranker-v2-m3） ⭐⭐⭐ 最高 ROI

```
现状：  recall → top-20 → 全塞 prompt（信噪比低）
升级：  recall → top-50 → Reranker → top-5 → 塞 prompt
```

- **为什么是最高 ROI**：reranker 是 cross-encoder，比 bi-encoder 准确率高 10-15%；TEI 已经能跑，**零新增基础设施**
- **联动收益**：prompt 变短 → token 省 → 延迟降（第 1 章 1.3 联动）
- **成本**：2-3 天。加一个 rerank 节点 + TEI 加载新模型

#### B. Query Decomposition（查询解构）

```
用户：  "对比 2025 Q1 和 Q4 各大区 GMV 环比变化"

现状：  整句 embedding → 召回模糊
升级：  decompose →
          ├ 实体: 2025 Q1, 2025 Q4
          ├ 维度: 大区
          ├ 指标: GMV, 环比
          └ 各自走召回 → 合并
```

- **为什么**：multi-agent 模式的 planner 已经做类似工作，但**单 sub 路径没做**。把 planner 的解构能力下沉到 recall 层
- **成本**：3-4 天。新增 `decompose_query` 节点，cheap 模型够用

#### C. HyDE（Hypothetical Document Embedding）

```
用户：  "查一下各地区销量前三"
HyDE：  LLM 先生成"假设答案"（假 SQL / 假字段名）→ 用它去 embedding
        → 命中"order_amount"等字段的概率提升
```

- **适用场景**：用户口语化提问（"销量" → order_amount）效果显著
- **风险**：多一次 LLM 调用（~1-2s），需要和 Semantic Cache 联动才划算
- **成本**：2 天。可作为 Reranker 后的二期

#### D. 多向量索引（Multi-Vector）

当前每个字段只有一个向量（基于字段名 + 描述）。升级为：
- 字段名向量
- 字段描述向量
- 字段示例值向量

三向量分别建索引，召回时取并集 + reranker 排序。

- **收益**：覆盖率 +5-10%，复杂查询受益
- **成本**：1 周（重建索引 + 改 build_meta_knowledge）

#### E. 召回置信度信号（为后面所有章节铺路）

```
rerank score > 0.8  →  高置信，跳过 generate_intent（联动第 1 章 1.2）
rerank score < 0.3  →  低置信，主动反问用户澄清（联动第 3 章）
```

- **价值不仅是召回层**——这是后面所有自适应决策的**信号源**
- **成本**：1 天（reranker 已经吐 score，只是没被消费）

### 2.3 优先级

```
Phase 1（必做）：A Reranker + E 置信度信号  ← 两天搞定，受益面最广
Phase 2（看数据）：B Query Decomposition    ← 如果 bad case 集中在"复杂查询"
Phase 3（可选）：C HyDE / D 多向量          ← 边际收益，看 ROI 决定
```

---

## 3. Agent 推理 & 自适应回路

### 3.1 现状：固定流程的代价

当前 17 节点主图是**静态图**——不管查询难度，所有用户都走完整链路。问题：

- 简单查询（"今天多少单"）跑完整 17 节点 = 浪费
- 难查询（"对比 Q1 和 Q4 GMV 环比"）单 sub 跑完就 END = 不够，应该升级到 multi-agent
- **决策点缺失**：现在用 `use_multi_agent=true` 这个**用户手工开关**，不是系统自己判断

### 3.2 升级方向：Adaptive Router

```
                  用户查询
                      ↓
              ┌─ 评估难度（cheap LLM 或规则）─┐
              │                               │
        简单 ↓                          复杂 ↓
     ┌────────────┐              ┌────────────────┐
     │ Fast Path  │              │ Multi-Agent    │
     │ 5 节点     │              │ planner 拆 sub │
     │ ~1s        │              │ + parallel     │
     └────────────┘              │ + aggregator   │
              │                  │ + reviewer     │
              ↓                  └────────────────┘
           直接返回                       ↓
                                  召回置信度低？
                                   ↓ Yes
                              ┌──────────────┐
                              │ Clarification│  ← 反问用户
                              │  节点        │
                              └──────────────┘
```

#### 三档路由

| 档位 | 触发条件 | 路径 | 延迟预期 |
|---|---|---|---|
| **Fast** | classify=metadata/chitchat，或召回 score > 0.8 | 5 节点（classify → recall → rerank → sql → run） | <1.5s |
| **Standard** | 默认 | 当前 17 节点 | ~3s |
| **Multi-Agent** | 含对比/拆分/多指标，或 query 长度 > 30 字 | supervisor_graph | ~5-8s |

### 3.3 Reviewer 升级：从 LLM-as-judge 到 Reference-Guided

**现状问题**：multi-agent 的 reviewer 是 LLM 评分，存在 **self-preference bias**（同款模型给自己打分偏高）。

**升级路径**：

```
现状：reviewer 看到生成的 SQL + 结果，给 0-1 分
       └ 问题：标准模糊，模型自评不可靠

升级 v1：reference-guided
       └ 给 reviewer 一组"好 SQL 的特征清单"
         (有 LIMIT / 用了聚合 / JOIN 了正确的维度表)
       └ 一项一项打勾，分数 = 命中率

升级 v2：execution-based（终极方案，见第 5 章）
       └ 不靠 LLM 判断，跑 gold SQL vs 生成 SQL，
         结果集对比 → 客观正误
```

### 3.4 自纠正回路加强

当前 `validate_sql ⇄ correct_sql` 失败后回传错误信息给 LLM。可强化：

- **错误分类**：语法错 / 字段不存在 / 表名错 / 类型不匹配，分别给不同修正 prompt
- **历史学习**：把历史 correction pair（错误 SQL → 修正 SQL）作为 few-shot，沉淀到 prompt（联动第 5 章数据飞轮）

### 3.5 面试讲法

> "Agent 不是节点越多越好，是**该走几步走几步**。我做 multi-agent 后意识到——'是否需要 multi-agent' 这件事本身应该由系统判断，不是用户开关。Adaptive Router 解决的是'**用合适的复杂度匹配问题**'，而不是'全部用最复杂的'。"

---

## 4. Agent Memory ⭐

> 面试高频考点。这一章的设计本身就是差异化——大多数项目只会说"加了 Redis session"，不会系统化讨论 memory 的类型、写入策略、遗忘机制。

### 4.1 为什么当前不算有 Memory

现在 `session_store`（Redis）只存了**对话历史**——这是 memory 最浅的一层。问题：

- 用户问完"2025 Q1 GMV"，再问"那 Q2 呢"——系统能接住对话，但**没记住"用户关心 GMV 这个指标偏好"**
- 用户连续问 3 次都加"按地区分组"——系统不会自动加上
- 业务方常用术语"动销率"，每次都得重新召回

这是 ** episodic memory（事件）有了，semantic / procedural memory（知识/流程）没有**。

### 4.2 Memory 三层模型（Cognitive Science 借鉴）

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  Short-term（工作记忆）                                       │
│  ├ 当前会话最近 5 轮                                          │
│  └ 实现已有：session_store                                    │
│                                                             │
│  Episodic（情景记忆）                                         │
│  ├ 历史会话摘录（"上周用户问过 X"）                            │
│  └ 实现缺：需要会话级 summarizer                              │
│                                                             │
│  Semantic（语义记忆 / 长期知识）                              │
│  ├ 用户偏好："总是按地区维度"                                  │
│  ├ 业务术语："动销率" = 某公式                                 │
│  └ 实现缺：需要 user_profile 表 + 自动抽取                    │
│                                                             │
│  Procedural（程序记忆 / 怎么做）                              │
│  ├ 历史成功 SQL 模板（"销量环比"对应这套写法）                  │
│  └ 实现缺：需要 sql_pattern 表 + few-shot 注入                │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 4.3 落地方案（按 ROI 排序）

#### A. Procedural Memory：SQL Pattern Bank ⭐⭐⭐ 最高 ROI

```
现状：每次生成都从零，LLM 凭"训练知识"写 SQL
升级：
   成功执行的 SQL → 抽取 (query 意图, SQL 模板) → 入库
   下次类似 query → 检索 top-3 模板 → 注入 prompt 做 few-shot

例：
   pattern_bank:
     - ("X 的销量环比", "SELECT ... LAG(...) OVER(...)")
     - ("各大区 GMV",   "SELECT region, SUM(order_amount) ...")
```

- **本质**：把"历史成功"转化为"未来 few-shot"，**这是最朴素的 memory，也是最有效的**
- **联动第 5 章数据飞轮**：飞轮沉淀的 good case 直接进 pattern bank
- **成本**：1 周。需要：成功 SQL 抽取规则 + 检索（复用 Qdrant）+ prompt 注入逻辑

#### B. Semantic Memory：User Profile ⭐⭐

```sql
-- 新表 user_profile
user_id | preference_type | content           | confidence | updated_at
--------|-----------------|-------------------|------------|--------
u_001   | preferred_dim   | region            | 0.9        | ...
u_001   | common_term     | "动销率"=某公式    | 0.7        | ...
u_001   | timezone        | Asia/Shanghai     | 1.0        | ...
```

- **抽取规则**：连续 3 次问都带"按地区" → 写入 `preferred_dim=region` 置信度 0.9
- **消费方式**：进入 generate_sql 节点的 prompt，作为默认上下文
- **成本**：1 周。需要：抽取逻辑（规则或 cheap LLM）+ 写入策略 + 遗忘机制

#### C. Episodic Memory：会话摘要 ⭐

```
现状：会话存原始 messages，超过 N 轮全塞 prompt → token 爆炸
升级：每 5 轮调一次 cheap LLM 摘要 → 替换历史
     "用户先问了 Q1 GMV，然后问 Q2，对比发现 Q2 涨了 15%"
```

- **本质**：long-context 的工程化解决方案，避免无限堆 token
- **成本**：3 天。已有 LLM 调用能力，加 summarizer 节点

### 4.4 遗忘机制（被忽视的关键）

**Memory 不是越多越好**——会过期、会矛盾、会污染。设计：

| 类型 | 遗忘策略 |
|---|---|
| Short-term | 会话结束即清（已有） |
| Episodic | 30 天衰减，N+1 次未引用即删 |
| Semantic | 矛盾时以最新为准；置信度 < 0.3 删除 |
| Procedural | 模板命中率 < 10%/月 → 降权或归档 |

### 4.5 Memory vs Fine-tuning（面试常见追问）

| 维度 | Memory（RAG-style） | Fine-tuning |
|---|---|---|
| 更新成本 | 写入即生效 | 重新训练 |
| 可解释性 | 高（看检索到啥） | 低（黑盒） |
| 适合场景 | 业务知识频繁变 / 个性化 | 模型基础能力不足 |
| 本项目选 | **✅ Memory** | ❌ 不考虑 |

**为什么本项目不 fine-tune**：(1) 不能换 LLM 的约束下 fine-tune 成本极高；(2) NL2SQL 任务更适合"模板复用"而非"风格学习"；(3) Memory 可解释，调试友好。

### 4.6 面试讲法

> "Memory 不是'加个 Redis 存对话'。我把 memory 拆成 short-term / episodic / semantic / procedural 四层，借鉴认知科学。**最高 ROI 是 procedural——SQL Pattern Bank**：把历史成功 SQL 沉淀成 few-shot 模板，每次生成时检索注入。这本质是把'用户的常用问法'和'正确 SQL 写法'对齐起来。**遗忘机制比写入更关键**——污染的 memory 比没有 memory 更糟。"

---

## 5. Eval + 可观测性 + 数据飞轮 ⭐⭐

> 这一章是数据+AI 岗的**核心差异化**。讲清楚它，等于证明你理解"AI 系统不是 demo，是工程"。

### 5.1 为什么这是当前最大短板

做完 multi-agent + profile registry 之后我意识到一个尴尬的事：**我无法量化系统到底有多好**。`tests/eval_e2e.py` 跑 5 个手写 case 全过 ≠ 系统鲁棒。每次 prompt 改动、每次召回调整，**都没有回归基线**。

这就是我开始系统研究 eval 的契机——也是这一章存在的意义：**不是吹已经做好了，是讲清"我意识到短板并设计了补齐方案"**。

### 5.2 Eval 三层金字塔

```
                    ┌──────────────────┐
                    │  Online Eval     │  线上真实流量
                    │  A/B + 用户反馈   │  + bad case 自动归集
                    └──────────────────┘
                    ┌──────────────────┐
                    │  End-to-End      │  NL → SQL → 执行 → 结果对不对
                    │  Eval            │  (BIRD-SQL 风格 execution match)
                    └──────────────────┘
                    ┌──────────────────┐
                    │  Component Eval  │  单节点：召回 hit-rate、
                    │                  │  SQL 安全、意图分类准确率
                    └──────────────────┘
```

### 5.3 Component Eval（已有，需系统化）

当前 `tests/eval_recall.py` 测召回，但只跑了几个 case。需要建：

| 组件 | 指标 | 测试集 | 工具 |
|---|---|---|---|
| 召回（三路） | hit-rate@10 / MRR | 100 条 query + 人工标注 gold 表/字段 | 现有 eval_recall 扩展 |
| 意图分类 | 准确率 / F1 | 200 条 query + 5 类标签 | 新建 |
| SQL 安全 | 拦截率 / 误杀率 | 已有 41 单测 + 注入攻击 list | 已有 |
| Reranker（升级后） | nDCG@5 | 同召回测试集 | 新建 |

**成本**：1 周。最大成本不是写代码，是**人工标注 100-200 条 gold case**。

### 5.4 End-to-End Eval（核心缺失）⭐⭐

这是 NL2SQL 系统的**金标准**。

#### 三种评估方式对比

| 方式                    | 做法                  | 优缺点                                     |
| --------------------- | ------------------- | --------------------------------------- |
| **SQL 字符串匹配**         | 生成的 SQL == gold SQL | ❌ 同义 SQL 被判错（`LEFT JOIN` vs `JOIN`）     |
| **Execution Match** ⭐ | 跑两条 SQL，对比结果集       | ✅ BIRD-SQL 标准；✅ 同义 SQL 等价；❌ 需要 gold SQL |
| **LLM-as-judge**      | 让 LLM 评分生成结果        | ❌ self-preference bias；可作为补充            |

**本项目采用 Execution Match 为主，LLM-judge 为辅**。

#### 自建 Gold Dataset 的策略

```
┌─ 接 BIRD-SQL 子集 ──────────────────────────────────┐
│  通用 NL2SQL 能力基线（schema 不同，但能验证 prompt）  │
└─────────────────────────────────────────────────────┘
                      +
┌─ 自建业务 Gold ─────────────────────────────────────┐
│  100-200 条覆盖：                                     │
│  ├ 简单聚合（"X 的总销量"）                           │
│  ├ 多表 JOIN（"各地区的客户数"）                       │
│  ├ 时间窗口（"上个月 vs 这个月"）                     │
│  ├ 排序 + LIMIT（"销量前 10"）                        │
│  └ 复杂算式（"环比/同比/复购率"）← 预期 bad case      │
└─────────────────────────────────────────────────────┘
```

- **标注成本**：100 条约 1-2 人天；可借助 LLM 辅助生成初版 SQL + 人工校对
- **存储**：`tests/gold_dataset.jsonl`，每行 `{query, gold_sql, category, difficulty}`

#### 自动回归流程

```
PR 提交 → CI 跑 eval_suite → 对比 main 分支基线
                                ├ 准确率 ↓ > 2% → 阻断合并
                                └ 准确率 ↑ → 更新基线
```

**成本**：2 周。核心是把现有 eval_e2e.py 升级成结构化、可对比、CI 集成。

### 5.5 Online Eval + 数据飞轮 ⭐⭐⭐ 最核心

**这才是数据+AI 岗的差异化叙事**——离线 eval 测已知 case，online eval 测真实分布。

```
┌─────────────── 线上查询 ───────────────┐
│                                       │
│   query → 召回 → SQL → 执行 → 结果     │
│                                       │
└───────────┬───────────────────────────┘
            ↓ 自动埋点
┌───────────────────────────────────────┐
│  Trace 日志（Langfuse）                │
│  ├ query                              │
│  ├ 每个节点的输入/输出/latency/token   │
│  ├ 召回结果                            │
│  ├ 最终 SQL                           │
│  └ 用户后续行为（改问/继续追问/离开）   │
└───────────┬───────────────────────────┘
            ↓ 信号提取
┌───────────────────────────────────────┐
│  Bad Case 自动归集                     │
│  ├ SQL 执行失败                        │
│  ├ 用户 30s 内改问同一意图             │
│  ├ reviewer 评分 < 0.5                 │
│  └ 用户主动标记 👎                     │
└───────────┬───────────────────────────┘
            ↓ 沉淀
┌───────────────────────────────────────┐
│  数据飞轮消费方                        │
│  ├ 进 Gold Dataset（人工 review 后）   │
│  ├ 进 SQL Pattern Bank（第 4 章 A）   │
│  └ 触发 Prompt 优化任务               │
└───────────────────────────────────────┘
```

#### 飞轮转起来的三个关键

1. **信号要准**：用户改问不一定是失败（可能想细化），需要组合信号（改问 + reviewer 低分 + 短停留）
2. **沉淀要闭环**：bad case 进 gold dataset → 周期性回归 → 失败模式驱动 prompt 改写 → 再回归
3. **消费要可见**：每周生成"bad case 报告"（哪类查询最常失败 / 失败模式聚类）

### 5.6 可观测性栈选型

| 维度 | 自建 | Langfuse | 决策 |
|---|---|---|---|
| Trace 链路（每节点输入输出） | 自己写 logging | 开箱即用 | **Langfuse** |
| 业务指标（NL2SQL 准确率、p95） | 自建 Prometheus | 弱 | **自建** |
| Bad case 检索 | 自己写查询 | UI 筛选 | **Langfuse** |
| A/B 实验 | 自建（重） | 不支持 | **自建**（简单版） |

**最终方案**：Langfuse 做 trace + bad case UI；Prometheus + Grafana 做业务指标趋势；A/B 用 feature flag + 数据库表自建轻量版。

### 5.7 不做 Fine-tuning 的飞轮（基于约束的妥协）

由于不能换 LLM、也不能 fine-tune，飞轮的消费方锁定在：

- ✅ Gold Dataset（人工标注 + 回归）
- ✅ SQL Pattern Bank（few-shot 注入）
- ✅ Prompt 优化（bad case 驱动版本迭代）
- ❌ SFT / DPO（条件不允许，也不必要）

**这反而是一个亮点**——证明在资源约束下，能找到工程化的飞轮路径，而不是"有钱就 fine-tune"。

### 5.8 面试讲法

> "AI 系统的成熟度不看 demo，看 eval。我的项目最大的短板是——做完 multi-agent 之后，**我无法回答'它到底有多好'**。所以我把 eval 设计成三层金字塔：Component（已有，需扩展）/ End-to-End（execution-based，缺）/ Online（数据飞轮，缺）。
>
> 数据+AI 岗最核心的不是离线 eval——是**线上 bad case 自动沉淀**。每次失败查询自动入 dataset，每周回归，失败模式驱动 prompt 改写。这是在不能 fine-tune 的约束下，唯一可持续的'系统变聪明'的路径。**飞轮转起来，比单点优化更值钱**。"

---

## 6. 新能力前瞻 & 路线图

### 6.1 新能力候选（按"差异化 × 可行性"评分）

| 能力 | 差异化 | 可行性 | 面试亮点 | 结论 |
|---|---|---|---|---|
| **Text-to-Chart** 自动可视化 | 高 | 高 | "查询完自动选图表类型" | ✅ 优先做 |
| **异常检测**（"为什么 Q2 GMV 暴跌"） | 高 | 中 | 从"问数"升级到"问洞察" | ✅ 第二批 |
| **Function Calling 扩展**（业务术语查询工具） | 中 | 高 | 已在 README 路线图 | ✅ 第二批 |
| **Multi-turn 上下文追问**（追问而不重述） | 中 | 高 | 配合第 4 章 Memory | ✅ 优先做 |
| **Scheduled Query**（"每周一发周报"） | 中 | 高 | 工程能力展示 | ⏸ 暂缓 |
| **自然语言建表 / 数据建模** | 高 | 低 | 过于前沿，超出 NL2SQL 范畴 | ❌ 不做 |
| **知识图谱融合**（业务实体关系图） | 中 | 低 | ROI 不明确 | ❌ 暂不做 |

#### 重点展开：Text-to-Chart

```
用户：  "2025 各大区 GMV"

现状：  返回表格
升级：  返回表格 + 自动选图表
        ├ 时间序列 → 折线图
        ├ 分类对比 → 柱状图
        ├ 占比 → 饼图
        └ 二维矩阵 → 热力图

技术：  结果集 schema → cheap LLM 判断"列类型 + 数据形态" → 推荐图表
```

- **联动第 4 章 Procedural Memory**：用户偏好图表类型可沉淀
- **成本**：3-5 天

#### 重点展开：异常检测

```
用户：  "为什么 Q2 GMV 暴跌？"

升级链路：
   SQL 结果 → 时序异常检测（z-score / 同比环比）
        → 命中异常 → 触发"归因子查询"
        → "Q2 华南区某 SKU X 销量降 60%，是主要原因"
```

- **本质**：从"被动问数"升级为"主动给洞察"
- **成本**：2 周。算法不难，难在归因子查询的设计

### 6.2 整体路线图（4 个阶段）

```
Phase 1：止血（Week 1-2）—— 围绕北极星"建指标"
┌────────────────────────────────────────────────────┐
│ ✅ Semantic Cache + 意图短路（第 1 章）              │
│ ✅ Reranker + 召回置信度信号（第 2 章 A/E）          │
│ ✅ Gold Dataset v1（100 条）（第 5 章）              │
│ ✅ Langfuse trace 接入（第 5 章）                    │
└────────────────────────────────────────────────────┘
   → 产出：能量出"NL2SQL 准确率 + p95"两个北极星

Phase 2：飞轮转起来（Month 1）—— 围绕"自我进化"
┌────────────────────────────────────────────────────┐
│ ✅ Bad case 自动归集（第 5 章）                      │
│ ✅ SQL Pattern Bank（第 4 章 A）⭐⭐⭐               │
│ ✅ Adaptive Router Fast/Standard/Multi（第 3 章）   │
│ ✅ Prompt 压缩 + 流式返回（第 1 章）                 │
└────────────────────────────────────────────────────┘
   → 产出：每周自动产出 bad case 报告，pattern bank 持续增长

Phase 3：能力扩展（Month 2-3）—— 围绕"超出问数"
┌────────────────────────────────────────────────────┐
│ ✅ Text-to-Chart 自动可视化                          │
│ ✅ Multi-turn 追问 + User Profile（第 4 章 B/C）    │
│ ✅ Reference-guided reviewer（第 3 章）              │
│ ✅ Query Decomposition（第 2 章 B）                  │
└────────────────────────────────────────────────────┘
   → 产出：体验差异化，从"问数工具"到"数据分析助手"

Phase 4：探索（Quarter 2）—— 看数据决定
┌────────────────────────────────────────────────────┐
│ 🔘 异常检测 + 归因                                   │
│ 🔘 物化视图预聚合                                    │
│ 🔘 Execution-based online eval                      │
│ 🔘 HyDE / 多向量索引                                │
└────────────────────────────────────────────────────┘
   → 取舍：看 Phase 1-3 的 bad case 报告决定哪个 ROI 最高
```

### 6.3 风险与开放问题

| 风险 | 应对 |
|---|---|
| Gold Dataset 标注成本 | 用 LLM 辅助生成初版 + 人工 review，控制在 2 人天内 |
| Pattern Bank 污染 | 加置信度衰减 + 命中率监控（第 4 章 4.4 遗忘机制） |
| Adaptive Router 误判 | 保留 fallback 到 Standard 路径，先 shadow run 一周对比 |
| Langfuse 部署负担 | 单容器够用；trace 采样率 10% 起步降负载 |

### 6.4 写在最后

这份升级路线的底层方法论：

1. **先量后优**：没有 eval 就没有优化方向（第 5 章是所有章节的地基）
2. **资源约束是创新的母亲**：不能换 LLM、不能 fine-tune，反而逼出了 Pattern Bank / Semantic Cache / 数据飞轮这些"工程化路径"
3. **飞轮 > 单点**：任何单次优化都有天花板，可持续的自我进化才是长期价值
4. **Memory 是新的 RAG**：procedural memory 本质上是"对历史成功经验的检索复用"，和 RAG 同源

> 面试一句话总结：
> **"这个项目的下一步不是堆 buzzword，是把 eval 闭环和 memory 沉淀做出来——让系统能自己变聪明，而不是靠我手动调 prompt。"**
