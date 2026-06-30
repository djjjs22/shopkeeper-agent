# SQL 执行流程分析 — 电商问数项目

> 创建时间：2026-06-29 | 藤子的学习笔记

---

## 一、什么是"直接执行 LLM 生成的 SQL"

### 整体流程

```
用户问题 → 关键词抽取 → 多路召回(字段/指标/取值)
    → 合并过滤 → LLM 生成 SQL → EXPLAIN 校验
    → (失败时) LLM 修正 SQL → 直接执行 SQL → 返回结果
```

### 关键点

LLM 生成的 SQL 文本，经过最简单的 `EXPLAIN` 语法检查后，**直接送到 MySQL 执行**。中间没有任何：
- ❌ 人工审核
- ❌ SQL 防火墙（禁止危险操作）
- ❌ 语义校验（字段/表名是否真实存在）
- ❌ 查询白名单

---

## 二、完整链路（代码级别）

### Step 1: generate_sql.py — LLM 生成 SQL

```python
# 第 47-49 行：把召回的表结构、字段、指标上下文塞进 Prompt
chain = prompt | llm | output_parser
result = await chain.ainvoke({...})
return {"sql": result}   # ← 直接存入状态，不做任何检查
```

### Step 2: validate_sql.py — EXPLAIN 语法校验

```python
# dw_mysql_repository.py 第 46-49 行
async def validate(self, sql):
    sql = f"explain {sql}"        # ← 只检查 MySQL 能否解析
    await self.session.execute(text(sql))
```

⚠️ **EXPLAIN 的局限性**：
- 只检查 SQL 语法结构是否正确
- **不检查字段名/表名是否真实存在**（语法正确但字段不存在时 EXPLAIN 会报错，但字段存在却放错表时不会）
- 不检查语义正确性（JOIN 条件是否合理）

### Step 3: correct_sql.py — LLM 修正（如果校验失败）

```python
# 把原始 SQL + 错误信息再喂给 LLM，让它重新生成
chain = prompt | llm | output_parser
result = await chain.ainvoke({"sql": sql, "error": error, ...})
return {"sql": result}
```

⚠️ 同样是 LLM 生成，没有任何安全约束。

### Step 4: run_sql.py — 直接执行

```python
# dw_mysql_repository.py 第 51-54 行
async def run(self, sql):
    result = await self.session.execute(
        text(sql)   # ← 直接执行！没有任何过滤！
    )
```

---

## 三、真实案例

### 案例1：字段放错表（2026-06-29 实测）

**问题**："查询销售额前十名的省份"

**LLM 生成**：
```sql
SELECT d.category, COUNT(f.order_id)
FROM fact_order f
JOIN dim_product d ON f.product_id = d.product_id
WHERE YEAR(d.date_id) = 2026   -- ❌ dim_product 没有 date_id 字段！
GROUP BY d.category;
```

**结果**：MySQL 报错 `Unknown column 'd.date_id'`

### 案例2：Markdown 代码块污染（已修复）

LLM 生成的 SQL 被包裹在 ````sql ... ```` 中，导致 MySQL 解析失败。

**修复**：在 `run_sql.py` 中添加了 `_clean_sql()` 函数。

---

## 四、三个核心风险

| # | 风险 | 严重程度 | 说明 |
|---|------|----------|------|
| 1 | **破坏性操作** | 🔴 高 | LLM 可能生成 `DROP TABLE`、`DELETE FROM`、`UPDATE` |
| 2 | **语义错误** | 🟡 中 | 字段放错表、JOIN 条件错，EXPLAIN 检测不到 |
| 3 | **性能杀手** | 🟡 中 | 笛卡尔积 JOIN、无 WHERE 全表扫描 |

---

## 五、安全加固方案（2026-06-29 实施）

### 新增文件：app/core/sql_safety.py
- 三层安全检查：危险关键字拦截 → SELECT 白名单 → SQL 注入检测
- 测试覆盖：DROP/DELETE/UPDATE/UNION SELECT/空SQL 全部拦截成功 ✅

### 修改文件：app/agent/nodes/run_sql.py
- 在执行前调用 SQLSafetyValidator.validate()
- 安全拦截不抛异常，流式返回具体拦截原因给前端

### 修改文件：prompts/generate_sql.prompt
- 规则1 升级为 ⛔ 安全红线，强调系统拦截后果
