# SQL 安全防护 — 设计决策与 Grill 记录

> 日期: 2026-06-29 ~ 2026-06-30 | 藤子的架构学习笔记

---

## 一、Grill 问答记录

### Q1：三层防护的顺序 — 为什么关键字拦截在第一层？

**露西的质疑**：注入检测放第一层是否更合理？毕竟注入是外部威胁。

**藤子的回答**：优先考虑内部效率。增删改查在实际生产中是最容易出现的行为，会直接导致 SQL 生成。注入安全也不可忽视，但优先级没有关键字这么高。

**结论**：关键字黑名单 → SELECT 白名单 → 注入检测，符合"尽早失败"原则，关键字匹配也最快。

### Q2：安全拦截用 `return` 还是 `raise`？

**露西的解释**（机场安检比喻）：

| 情况 | 行为 | 原因 |
|------|------|------|
| 安全拦截（如 DELETE） | `return`（静默退出） | 不是 bug，不该修——刀就不该带进来 |
| 数据库错误（如字段错误） | `raise`（向上报） | 是 bug，需要 LLM 修正 |

**藤子的反馈**：理解了。

### Q3：为什么先检查数据库再检查安全拦截？

**露西承认设计缺陷**：当前安全拦截放在 `run_sql` 节点末尾，但 `validate_sql` 的 EXPLAIN 在它前面执行——如果 SQL 本身是危险的（如 DROP TABLE），白白浪费一次数据库调用后才被拦截。

**改进方案**：安全拦截拆成独立节点 `sql_safety_check`，插入到 `generate_sql` 之后、`validate_sql` 之前：

```
旧流程: generate_sql → validate_sql → correct_sql → run_sql(安全+执行)
新流程: generate_sql → ⭐sql_safety_check → validate_sql → correct_sql → run_sql(纯执行)
```

**好处**：
- 危险 SQL 在 EXPLAIN 之前就被拦住，不浪费数据库资源
- 各节点职责单一：安全检查只管安全，执行只管执行
- `correct_sql` 修正后的 SQL 仍需经过安全检查（节点不变）

---

## 二、核心设计原则

### 2.1 双保险机制

```
软防护（Prompt 约束）  +  硬防护（代码防火墙）
         ↓                         ↓
   "安全红线"提示           SQLSafetyValidator 类
   靠 LLM 自觉              靠代码强制执行
```

Prompt 约束是"建议"，防火墙是"执法"。两者缺一不可：
- 没有 Prompt 约束 → LLM 更容易生成危险 SQL
- 没有防火墙 → Prompt 约束只是文字游戏

### 2.2 三层防护体系

```
SQL 进入
  │
  ├── 第一层：危险关键字黑名单（DROP/DELETE/UPDATE/ALTER/...）
  │     匹配方式：正则 \b 词边界
  │     拦截后：直接拒绝，不进入修正
  │
  ├── 第二层：SELECT 白名单（只允许 SELECT/WITH 开头）
  │     匹配方式：.startswith("SELECT") or .startswith("WITH")
  │     拦截后：直接拒绝
  │
  └── 第三层：SQL 注入特征检测（UNION SELECT / OR '1'='1' / -- 注释）
        匹配方式：正则 + re.IGNORECASE
        拦截后：直接拒绝
```

### 2.3 嵌套 try/except 的设计逻辑

```python
外层 try ←── 捕获数据库执行错误（可以修的 bug）
    │
    内层 try ←── 捕获安全校验错误（不该修的原则性问题）
        │
        SQLSafetyValidator.validate(sql)
        │
        ├── 通过 → 继续执行 SQL
        └── 失败 → return（优雅退出，不触发外层 except）
```

**为什么安全拦截用 `return` 而不是 `raise`？**

因为 `raise` 会让 LangGraph 认为"这个 SQL 有 bug，需要修正"，然后跳转到 `correct_sql` 节点。但危险操作（如 DELETE）不是 bug——它就不该被执行。就像安检："刀不能带进来"不是"你把刀磨钝一点"——是根本不能带。

---

## 三、已落地的代码变更

| # | 文件 | 操作 | 核心内容 |
|---|------|------|----------|
| 1 | `.env` | 修改 | API Key 更换为 opencode.ai |
| 2 | `conf/app_config.yaml` | 修改 | 模型切换 deepseek-v4-pro |
| 3 | `app/core/sql_safety.py` | **新增** | 三层防火墙类，逐行注释版 |
| 4 | `app/agent/nodes/run_sql.py` | 重写 | 集成安全校验，嵌套 try/except |
| 5 | `prompts/generate_sql.prompt` | 修改 | 安全红线约束 |
| 6 | `app/clients/embedding_client_manager.py` | 修改 | langchain API 兼容修复 |

---

## 四、待改进项（Grill 中发现）

### 4.1 ⭐ 安全拦截前置为独立节点

**当前问题**：安全校验在 `run_sql` 内，但 `validate_sql`（EXPLAIN）在它前面。

**改进**：新增 `sql_safety_check` 节点，插入 `generate_sql` → `validate_sql` 之间。

**需要改的文件**：
- 新增 `app/agent/nodes/sql_safety_check.py`
- 修改 `app/agent/graph.py`（添加节点 + 调整边）
- 简化 `app/agent/nodes/run_sql.py`（移除安全校验，回到纯执行）

### 4.2 安全拦截可考虑写回 state["error"]

如果安全拦截后不是直接 `return`，而是写入 `state["error"]` 并让 LangGraph 进入 `correct_sql`：
- 优势：给 LLM 一次"修正"机会（比如把 DELETE 改回 SELECT）
- 风险：LLM 可能只是换个写法绕过检测

**建议**：暂不实施，保持当前"直接拒绝"策略。

---

## 五、关键知识点速查

| 概念 | 一句话解释 |
|------|-----------|
| 正则 `\b` 词边界 | 匹配 `DROP` 但不匹配 `DROPDOWN` |
| `@classmethod` | 不用创建对象，直接 `类名.方法()` 调用 |
| f-string | `f"值={变量}"` 花括号里是变量 |
| `return` vs `raise` | return = 正常结束；raise = 报告异常 |
| 嵌套 try/except | 内层处理特定错误，外层处理通用错误 |
| LangGraph 条件边 | `validate_sql` → state["error"] 有值则去 `correct_sql` |
| 双保险 | Prompt 约束（软）+ 代码防火墙（硬） |
