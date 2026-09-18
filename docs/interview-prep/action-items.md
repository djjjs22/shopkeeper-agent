# Shopkeeper Agent 面试准备 — 行动清单

> 配合 `huahai-50-questions-coaching.md` 使用。**按优先级排序,先去补 🚨 级**。

---

## 🚨 P0(立刻补,缺一个就死)

### 1. 修正 README "7 张表" 的错

**现状**:`README.md` 第 67-72 行 `app/core/sql_safety.py` 写**5 张表**(`dim_region / dim_customer / dim_product / dim_date / fact_order`),README 第 13/66/170 行说"7 张/7.2 万单/7.3MB dw.sql"——**"7.2 万单"对**,"7 张"**错**。

**行动**:
```bash
grep -n "7 张" /Users/lunasama/Downloads/Agent/shopkeeper-agent/README.md
# 然后把 "7 张表" 改成 "5 张表(1 事实 + 4 维)"
```

### 2. 补 README "jinja2 渲染" 介绍

**现状**:README 第 4 节"安全设计"只写"EXPLAIN + 三层防火墙 + correct_sql",**没提**`generate_sql` 已经改成 jinja2 渲染(这是关键架构变更,2026-07-14)。

**行动**:在 README 第 4 节加一行:
> | 4.5 | **LLM 输出 JSON → jinja2 渲染 SQL** | LLM 不直接写 SQL,只输出结构化 intent,SQL 由模板确定性渲染 |

### 3. 补 README 节点数说明

**现状**:README 第 22/43/232 行反复说"17 节点",supervisor_graph 新增 4 节点(planner/data_agent/aggregator/reviewer)= **21 节点**。**17 指的是老 graph,21 指的是 multi-agent 模式**,README 没讲清。

**行动**:第 3.1 节图注加:
> "**老 graph 17 节点,多 agent 模式 opt-in 后变 21 节点**(supervisor 4 + 老 17)"

---

## ⚠️ P1(一周内补,补上质量上一个台阶)

### 4. 把 prompt 留痕埋了

**现状**:用户复现"昨天那个 SQL 不对"时,**无法回查当时 LLM 收到什么 prompt**。

**行动**(估 1 小时):
- `app/core/timing.py` `_make_wrapper` 函数里加一行:把节点函数渲染后的 prompt 写日志
- 实际:每个节点自己把 `prompt_template.format(**state)` 结果 log 出去
- 或:`@timed_node` 装饰器参数化支持 `prompt_fn=`,节点传一个 `lambda: chain.first.format(**kwargs)`

### 5. 补 TEI/Redis 挂了降级

**现状**:TEI 挂 / Redis 挂 → 整图报错或丢历史。**生产上靠 K8s 自愈,代码层没降级**。

**行动**(估半天):
- TEI 挂:加 BM25 兜底召回(用 `jieba.analyse.extract_tags` 切词 + MySQL `LIKE`)
- Redis 挂:已经有内存兜底,**降级时打 warning log + metric 计数**

### 6. 补 LLM 限流降级

**现状**:deepseek 限流 → 整图 500。**无 fallback 链**。

**行动**(估 1 天):
- 在 `app/agent/llm.py` 加 fallback 链:`strong → cheap → 本地小模型(预留)`
- 用 `tenacity` 实现 retry + 指数退避
- `httpx.ConnectError` 触发 fallback,不 raise 给上层

### 7. 补反思回路命中率统计

**现状**:`reviewer_node` 触发频率没统计。**生产上不知道反思回路到底有没有用**。

**行动**(估 2 小时):
- `app/agent/nodes/reviewer_node.py` 写 metric:`reviewer_triggered_total`,按 confidence bucket 分桶
- 跑 1 周,根据数据调 0.7 阈值

---

## ❌ P2(未来补,有空再说)

### 8. Function Calling 工具集

按 README 演进路线做。估 3 天。

### 9. BIRD / Spider 公开 benchmark

估 1 周(要写 evaluator 适配器)。

### 10. MySQL 深分页优化

把 prompt 强约束加 `LIMIT 1000`,后端再加 max_rows 截断。估半天。

### 11. dashboard 4 panel

Prometheus + Grafana,估 1 周。

---

## 笔记归档位置

- 笔记主文件:`/Users/lunasama/Downloads/Agent/shopkeeper-agent/docs/interview-prep/huahai-50-questions-coaching.md`
- 行动清单:`/Users/lunasama/Downloads/Agent/shopkeeper-agent/docs/interview-prep/action-items.md`(本文件)
- 按"笔记归档规则"(2026-07-10 立):shopkeeper-agent 项目笔记放 `shopkeeper-agent/docs/`,**不再放 Downloads 根目录**

---

## 推荐阅读顺序

1. **今天**:把笔记"②面试话术"段全部读 2 遍,**别死记,先理解**
2. **明天**:挑 3 道最虚的题(题 9 / 10 / 11),各写 200 字答案
3. **这周**:补 P0 三件事(README 改 3 处)
4. **下周**:开始补 P1
