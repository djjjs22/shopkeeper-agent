# SQL 安全加固 — 代码变更学习笔记

> 创建时间：2026-06-29 | 藤子的 Python 学习笔记

---

## 变更概览

| 文件 | 操作 | 说明 |
|------|------|------|
| `app/core/sql_safety.py` | **新增** | SQL 安全防火墙类，三层防护 |
| `app/agent/nodes/run_sql.py` | **重写** | 集成安全校验，每行详细注释 |
| `prompts/generate_sql.prompt` | **修改** | Prompt 安全约束加强 |
| `.env` | **修改** | API Key 更新为 deepseek-v4-pro |
| `conf/app_config.yaml` | **修改** | LLM 配置切换为 DeepSeek |

---

## Python 知识点速查表

代码中使用的 Python 语法及简单解释：

| 语法 | 含义 | 示例 |
|------|------|------|
| `#` | 单行注释 | `# 这是注释` |
| `"""..."""` | 多行注释/文档字符串 | `"""模块说明"""` |
| `import X` | 导入模块 X | `import re` |
| `from X import Y` | 从 X 模块导入 Y | `from typing import List` |
| `class Name:` | 定义类（对象的蓝图） | `class Dog:` |
| `def func():` | 定义函数 | `def hello():` |
| `async def` | 定义异步函数（协程） | `async def fetch():` |
| `await` | 等待异步操作完成 | `result = await db.query()` |
| `@classmethod` | 装饰器：方法可直接通过类名调用 | `SQLSafetyValidator.validate(sql)` |
| `[]` | 创建列表 | `[1, 2, 3]` |
| `{}` | 创建字典 | `{"name": "张三"}` |
| `f"..."` | 格式化字符串（f-string） | `f"分数：{score}"` |
| `r"..."` | 原始字符串（不转义 \） | `r"\n"` = 两个字符 \ 和 n |
| `try/except` | 异常处理 | `try: ... except Error: ...` |
| `raise` | 抛出异常 | `raise ValueError("错误")` |
| `return` | 函数返回值 | `return 42` |
| `is not None` | 检查变量不是空值 | `if x is not None:` |
| `[start:end]` | 切片（截取序列的一部分） | `"hello"[1:3]` = `"el"` |
| `object["key"]` | 字典取值 | `state["sql"]` |
| `变量: 类型` | 类型注解（类型提示） | `name: str = "张三"` |
| `-> 类型` | 返回值类型注解 | `-> str` 表示返回字符串 |
| `==` | 等于判断 | `a == b` |
| `and` / `or` | 逻辑与 / 逻辑或 | `a and b` |
| `not` | 逻辑非 | `not a` |

---

## 核心改动详解

### 1. SQL 防火墙 (`sql_safety.py`)

**设计思路**：在 SQL 送交数据库前设置"三道安检门"

```
SQL 进入 → [第一道门: 关键字检查] → [第二道门: SELECT白名单] → [第三道门: 注入检测] → 放行
                ↓ 拦截                      ↓ 拦截                      ↓ 拦截
            返回错误信息                 返回错误信息                 返回错误信息
```

**第一道门**：用 `re.search(r"\bDROP\b", sql)` 匹配危险关键字
- `\b` = 单词边界，防止把 "DROPDOWN" 误判为 "DROP"
- 例如 `"DROP TABLE users"` 会被拦截，`"SELECT drop_time"` 不会

**第二道门**：用 `.startswith("SELECT")` 检查 SQL 类型
- `.startswith()` = 字符串方法，检查是否以特定字符开头

**第三道门**：用多个正则表达式检测 SQL 注入特征
- `UNION SELECT`：典型注入手法
- `' OR '1'='1`：永远为真的条件
- `--`：注释截断注入

### 2. 执行节点重写 (`run_sql.py`)

**修改前**：
```python
# 旧代码：直接执行，无任何检查
sql = state["sql"]
result = await dw_mysql_repository.run(sql)
```

**修改后**：
```python
# 新代码：先清理 → 再安检 → 最后执行
sql = _clean_sql(state["sql"])          # 1. 去掉 markdown 标记
try:
    sql = SQLSafetyValidator.validate(sql)  # 2. ⭐ 三层安全检查
except ValueError as err:
    writer({"type": "error", "message": str(err)})  # 安全拦截 → 通知前端
    return                                           # 优雅终止
result = await dw_mysql_repository.run(sql)  # 3. 安全通过 → 执行
```

### 3. Prompt 约束加强 (`generate_sql.prompt`)

**修改前**（规则3）：
```
3. 生成的SQL只能用于查询，不能涉及数据写入、更新、删除等操作。
```

**修改后**（规则1，升级为安全红线）：
```
1. ⛔ 安全红线：你只能生成 SELECT 查询语句。严禁生成 INSERT、UPDATE、
   DELETE、DROP、ALTER、TRUNCATE、CREATE 等任何写操作语句。违反此规则
   将导致 SQL 被系统拦截。
```

---

## 测试结果

| # | 测试场景 | 结果 | 说明 |
|---|----------|------|------|
| 1 | 正常 `SELECT` | ✅ 放行 | 合法查询正常通过 |
| 2 | `DROP TABLE` | ✅ 拦截 | 危险操作被防火墙阻止 |
| 3 | `DELETE FROM` | ✅ 拦截 | 危险操作被防火墙阻止 |
| 4 | `UPDATE SET` | ✅ 拦截 | 危险操作被防火墙阻止 |
| 5 | `UNION SELECT` 注入 | ✅ 拦截 | 注入特征被检测 |
| 6 | 空 SQL | ✅ 拦截 | 空语句被拒绝 |
