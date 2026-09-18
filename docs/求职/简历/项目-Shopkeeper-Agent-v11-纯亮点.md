# Shopkeeper Agent

> 自然语言驱动的电商问数 AI Agent。业务方说一句"Q1 各区 GMV"，系统自动检索表/字段/指标，生成 SQL，过安全闸门后执行，3 秒出结果。
> 
> 技术栈：**LangGraph** · FastAPI · React · Qdrant · Elasticsearch · MySQL · Redis · bge-large-zh-v1.5

---

## 5 个有亮点的功能

1. **LangGraph 17 节点有向图 + 4 节点 Multi-Agent** — Send API 并行子问题（共享前置子图省 16s），评审员 < 0.7 触发反思，max_loop=2 防死循环
2. **RAG 三路互补召回** — Qdrant 向量（指标名）+ ES 全文（枚举值）+ 向量（抽象概念）覆盖三种语义空间；单 LLM 召回率 < 40% 升到能上生产
3. **SQL 5 道安全闸门 + 自动修正回路** — 关键字黑名单 → 只读白名单 → 注入检测 → EXPLAIN 预演 → 失败回 LLM 修正；**41 个单测**覆盖 4 维度（关键字 / 白名单 / 注入 / 防误杀）
4. **数据飞轮 4 处埋点 + CI 防退化** — SQL 失败 / 修正放弃 / 评审低分 / 用户 👎 自动归集；GitHub Actions 跑 59 条业务 case 评测，**基线 86.7%，退化 2% 阻断合并**
5. **多轮会话原子性 + 记忆 3 层** — Redis `rpush + ltrim + expire` 一次网络往返（断网全回滚）；Procedural 59 条 SQL 模板 + Semantic 3 次命中才注入 + Episodic 5 轮自动摘要

---

## 面试可追问

- 17 节点有向图 vs 普通 if-else 优势在哪？
- 反思回路 max_loop=2 怎么防延迟爆炸？
- 数据飞轮污染怎么防？/ 4 处埋点怎么协同？
- Redis 挂了服务怎么不挂？
- 86.7% 基线怎么定的？退化 2% 为什么不是 5% / 10%？
