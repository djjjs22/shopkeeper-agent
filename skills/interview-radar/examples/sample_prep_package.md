# 张明 · AI 应用开发岗 备考包(示例)

> 这是用 **InterviewRadar** 跑出来的样例输出。简历是虚构人物。
> 数据采集日期:2026-06-01。源时效窗口:近 730 天。

---

## 输入(示例简历)

```
张明 · CS 本科 · 2023-2027
项目:
  P1. RAG 知识库问答系统 (LangChain + Pinecone + GPT-4)
       - 多文档切块 + Hybrid Search + Rerank
       - 引入 LLM-as-Judge 评测,准确率从 62% 提升到 81%
  P2. 学校教务查询机器人 (Spring Boot + MySQL + Redis)
       - 缓存学分查询接口,QPS 从 50 提升到 1500
  P3. 大数据课程小作业:Pandas 数据清洗

技能:Python / Java / LangChain / Pinecone / Redis / Spring Boot
方向:AI 应用开发岗
```

---

## 1. 岗位画像 · AI 应用开发岗

| 权重 | 维度 | 话题 |
|---|---|---|
| ★★★★★ | Agent 系统设计 | ReAct / Plan-and-Execute / 多 Agent / 短长期记忆 |
| ★★★★★ | RAG 工程 | 切块 / Embedding / Hybrid Search / Rerank / Query 改写 |
| ★★★★ | 协议与生态 | Function Calling / MCP / A2A |
| ★★★★ | LLM 基础 | Decoder-only / KV Cache / 长上下文 / 复读机 |
| ★★★ | 工程化 | LLM 网关 / 评测体系 / Context Engineering |

---

## 2. 简历 ↔ 岗位 Gap 分析

| 维度 | 现状 | 评级 |
|---|---|---|
| **RAG 工程深度** | P1 完整覆盖切块 / Hybrid Search / Rerank / 评测闭环 | ✅ 强 |
| **后端基本功** | P2 有 Spring Boot + Redis 缓存优化数据 | ✅ 强 |
| **Agent 设计** | 未提 | ❌ 弱 — **2 周必补** |
| **MCP / Function Calling** | 未提 | ❌ 弱 |
| **LLM 推理基础** | 无 | ⚠️ 中(可问可不问) |

**结论**:P1 是 RAG 满分答案,P2 撑后端,**Gap 集中在 Agent + 协议**。

---

## 3. 高频题(节选 10 / 实际 38)

### 3.1 RAG ★★★★★

1. RAG 召回率低怎么排查?Chunk 问题、Embedding 问题、还是排序问题?
2. Hybrid Search、Query Rewrite、Rerank 分别解决什么问题?
3. 文档切割:固定长度、语义切割、父子块,各自优劣?
4. 怎么规避幻觉?

### 3.2 Agent ★★★★★

5. ReAct、Plan-and-Execute、Reflexion 三种范式分别如何工作?
6. Agent 短期 / 长期 / 语义记忆怎么设计?
7. 从零写一个最小化 ReAct Agent(prompt 设计 + tool_call 解析)

### 3.3 协议 ★★★★

8. MCP 和 Function Calling 区别?
9. JSON Mode / Structured Outputs / Function Calling 区别?

### 3.4 工程化 ★★★

10. 如何设计 AI 应用评测体系?Golden Set / LLM-as-Judge / Trace 回放?

---

## 4. 个性化项目追问链(锚到简历,**核心价值**)

### 链 4.1 — 题 #1 #2 → P1 RAG 系统

> **预测**:
> Q1: "你 P1 的 RAG 系统准确率从 62% 提到 81%,具体是哪个环节贡献最大?"
> Q2: "做 Hybrid Search 的时候,BM25 和向量召回的权重是怎么定的?"
> Q3: "Rerank 用了什么模型?为什么不直接 prompt 让 LLM 排?"
>
> **怎么答**:
> - 拆贡献:做一张消融实验表(只 Rerank 提升 X%、只 Hybrid 提升 Y%)
> - 权重定法讲"线下小集调参 → 线上 A/B"思路
> - Rerank 模型(BGE-Reranker / Cohere)vs LLM 排序的成本/延迟差异

### 链 4.2 — 题 #4 → P1 LLM-as-Judge 评测

> **预测**:
> Q1: "你说做了 LLM-as-Judge,具体 rubric 怎么设计的?"
> Q2: "怎么避免 Judge 模型本身的 bias?"
>
> **怎么答**:
> - rubric 三维度(忠实度 / 相关性 / 完整度),每维 1-5 分
> - bias 缓解:多 Judge 投票 / 对比模式(A vs B 而不是绝对打分)

### 链 4.3 — 题 #10 → P2 教务机器人 + LLM 网关

> **预测**:
> Q1: "你 P2 把 QPS 从 50 提到 1500,Redis 怎么布的?"
> Q2: "如果让你做一个 LLM 网关,缓存层会怎么设计?"
>
> **怎么答**:
> - P2 的 Redis 是简单 key-value 缓存,讲穿透/雪崩怎么防
> - LLM 网关 cache 三层:Embedding cache(value 不变)/ Response cache(语义指纹+模型版本)/ Prompt cache(高频前缀)

---

## 5. 重点题参考思路(略,完整版每题展开)

略——实际输出会展开 5-8 题骨架答案。

---

## 6. 两周节奏

**Week 1 — 补 Agent + 协议 Gap**
- Day 1-2: ReAct / Plan-and-Execute 啃论文 + 跑 demo
- Day 3-4: MCP 官方 spec + SDK demo
- Day 5: Function Calling 工程细节
- Day 6-7: P1 RAG 全套排练

**Week 2 — 个性化追问串讲**
- Day 8-10: 上面 3 条追问链每条录音模拟
- Day 11: 后端基本功(P2 衔接)
- Day 12-13: Mock interview
- Day 14: 简历数字背熟

---

## 7. 附录 · 数据来源

| 来源 | URL | 时效 | 提供题数 |
|---|---|---|---|
| 牛客 — AI Agent 面试题汇总 | nowcoder.com/discuss/... | 2026-03 | 25 |
| JavaGuide AI 面试指南 | javaguide.cn/ai/... | 实时 | 16 |
| GitHub — hello-agents | datawhalechina/hello-agents | rolling | 56 |
| ... | | | |

---

> 💡 这只是节选样例。实际跑出来:
> - 完整题库 ~38 题(分 5 主题)
> - 每条追问链都附"怎么答"骨架
> - 每道高频题带源链接
> - 所有数据都过 730d 时效过滤
> - 输出长度通常 8-12KB Markdown
>
> 跑你自己的简历:`Claude Code` 里说 "用 InterviewRadar 跑一下: /path/to/resume.pdf, 方向:xxx"
