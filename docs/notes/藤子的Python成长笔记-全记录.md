# 藤子的 Python 成长笔记 — 电商问数项目全记录

> 2026年6月29日~30日 | 从零跑通项目到安全加固到测试落地

---

## 目录

1. [项目跑通](#一项目跑通)
2. [SQL 安全加固（代码编写）](#二sql-安全加固)
3. [Grill 设计讨论](#三grill-设计讨论)
4. [单元测试落地](#四单元测试落地)
5. [代码规范整改](#五代码规范整改)
6. [Python 知识点速查](#六python-知识点速查)
7. [完整文件清单](#七完整文件清单)

---

## 一、项目跑通

### 1.1 环境搭建

```bash
# Python 依赖管理
uv sync                    # 等价于 pip install -r requirements.txt，但更快

# 前端依赖
cd frontend && pnpm install  # pnpm = 类似 npm，但更省磁盘

# Embedding 模型下载（1.3GB，用国内镜像加速）
HF_ENDPOINT=https://hf-mirror.com uv run hf download BAAI/bge-large-zh-v1.5 --local-dir docker/embedding/bge-large-zh-v1.5
```

### 1.2 Docker 服务

四个容器组成基础设施：

| 容器 | 端口 | 功能 | 类比 |
|------|------|------|------|
| MySQL | 3306 | 存元数据和模拟数仓 | 就像 Excel 表格 |
| Qdrant | 6333 | 存向量，做语义搜索 | 找"意思相近"的东西 |
| Elasticsearch | 9200 | 做全文搜索 | 像 Google 搜索关键词 |
| Embedding(TEI) | 8081 | 把文字转成向量 | 把"苹果"变成 [0.1, -0.3, ...] |

```bash
cd docker && docker compose up -d mysql qdrant elasticsearch embedding
# -d = 后台运行
# docker compose = 批量管理容器（启动/停止/重启）
```

### 1.3 修复的两个兼容性问题

**问题1：langchain-huggingface API 变更**

旧版可以写 `HuggingFaceEndpointEmbeddings(model="http://localhost:8081")`，新版 `model` 参数必须是 HuggingFace 仓库名。

修复：写了自己的 `TEIEmbeddings` 类，用 aiohttp 直连 TEI 服务。

```python
# 修改前 ❌
self.client = HuggingFaceEndpointEmbeddings(model="http://localhost:8081")

# 修改后 ✅
class TEIEmbeddings(Embeddings):      # 继承 LangChain 的 Embeddings 基类
    async def aembed_documents(self, texts):
        async with aiohttp.ClientSession() as session:      # 创建 HTTP 客户端
            async with session.post(                         # 发 POST 请求
                "http://localhost:8081/embed",
                json={"inputs": texts}                       # 请求体：要向量化的文本
            ) as resp:
                return await resp.json()                     # 返回向量结果

self.client = TEIEmbeddings(base_url="http://localhost:8081")
```

**问题2：LLM 生成的 SQL 带 Markdown 代码块**

LLM 生成 SQL 时经常包在 ````sql ... ```` 里，MySQL 不认识。

```python
def _clean_sql(sql: str) -> str:
    """清理 LLM 生成的 SQL 中的 Markdown 标记"""
    sql = re.sub(r"^```(?:sql)?\s*\n?", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\n?```\s*$", "", sql)
    return sql.strip()
```

---

## 二、SQL 安全加固

### 2.1 为什么要做安全

当前流程中，LLM 生成的 SQL **没有任何安全检查**就直接送给 MySQL 执行：

```
LLM 生成 SQL → EXPLAIN 语法检查 → 直接执行
                                    ↑
                              如果 LLM 生成 DROP TABLE，
                              这里就真的会执行！
```

### 2.2 新增文件：app/core/sql_safety.py

三层安全检查，每层职责清晰：

```
SQL 进入
  │
  ├── 第一层：危险关键字黑名单
  │    检查：DROP/DELETE/UPDATE/INSERT/ALTER/TRUNCATE/CREATE/...
  │    技术：正则 \b 词边界匹配，防止误杀
  │    例如：DROP TABLE → 拦截；is_deleted → 不拦截
  │
  ├── 第二层：SELECT/WITH 白名单
  │    检查：SQL 必须以 SELECT 或 WITH 开头
  │    技术：.startswith("SELECT")
  │
  └── 第三层：SQL 注入特征检测
       检查：UNION SELECT、OR '1'='1'、-- 注释截断
       技术：正则 + re.IGNORECASE（不区分大小写）
```

### 2.3 重写文件：app/agent/nodes/run_sql.py

核心改动——在执行 SQL 之前插入安全校验：

```python
async def run_sql(state, runtime):
    sql = _clean_sql(state["sql"])                # Step 1: 清理 Markdown
    
    try:                                           # 外层 try：捕获数据库错误
        try:                                       # 内层 try：捕获安全错误
            sql = SQLSafetyValidator.validate(sql) # Step 2: ⭐ 安全校验
        except ValueError as err:                  # 安全拦截
            writer({"type": "error", ...})          # 通知前端
            return                                  # 优雅退出，不执行 SQL
        
        result = await db.run(sql)                  # Step 3: 执行 SQL
        
    except Exception as e:                         # 数据库错误
        raise                                       # 向上抛，让 LangGraph 修正
```

### 2.4 修改文件：prompts/generate_sql.prompt

将安全约束从"建议"升级为"红线"：

```
修改前: 3. 生成的SQL只能用于查询，不能涉及数据写入、更新、删除等操作。

修改后: 1. ⛔ 安全红线：你只能生成 SELECT 查询语句。严禁生成 INSERT、
          UPDATE、DELETE、DROP 等任何写操作。违反将被系统拦截。
```

---

## 三、Grill 设计讨论

### Q1：三层防护为什么是这个顺序？

**藤子的决策**：增删改查在实际生产中最高频，关键字匹配也最快，放在第一层符合"尽早失败"原则。

### Q2：安全拦截用 return 还是 raise？

```
安全拦截 → return（静默退出）   → 类比：安检门响了，回去
数据库错误 → raise（向上报）    → 类比：引擎故障，呼叫维修
```

### Q3；安全拦截应该在 validate_sql 之前还是之后？

**发现的问题**：当前安全在 run_sql（末尾），但 EXPLAIN（validate_sql）在它前面。危险 SQL 白白浪费一次数据库调用。

**改进方案**（未实施，记录供后续）：

```
旧: generate_sql → validate_sql → correct_sql → run_sql(安全+执行)
新: generate_sql → ⭐sql_safety_check → validate_sql → correct_sql → run_sql(纯执行)
```

---

## 四、单元测试落地

### 4.1 关于安全渗透测试的讨论

**藤子**：渗透应该是网络安全专业的活，AI 岗位需要吗？

**结论**：不需要。AI/数据岗位的测试重点是单元测试 + 集成测试，不是渗透测试。

### 4.2 创建测试文件

#### tests/conftest.py — pytest 配置文件

```python
import sys
from pathlib import Path

# 把项目根目录加入 sys.path
# 这样测试文件里的 from app.core.sql_safety import ... 才能正常工作
# sys.path 是 Python 查找模块的路径列表
# Path(__file__).parent.parent = 当前文件的上两级目录（即项目根目录）
sys.path.insert(0, str(Path(__file__).parent.parent))
```

#### tests/test_sql_safety.py — 20 个单元测试

完整的测试代码，覆盖所有场景：

```python
import pytest
from app.core.sql_safety import SQLSafetyValidator


class TestSQLSafetyValidator:

    # ══════ 正向测试：合法查询应通过 ══════

    def test_正常SELECT查询_应通过(self):
        result = SQLSafetyValidator.validate(
            "SELECT region_name, SUM(order_amount) FROM fact_order"
            " JOIN dim_region ON fact_order.region_id = dim_region.region_id"
            " GROUP BY region_name"
        )
        assert "SELECT" in result
        # assert = Python 断言关键字
        # 如果后面是 True → 测试通过
        # 如果后面是 False → 测试失败，pytest 会显示红色 FAILED

    def test_WITH_CTE查询_应通过(self):
        """WITH 开头的 CTE（公用表表达式，Common Table Expression）也应该通过"""
        result = SQLSafetyValidator.validate(
            "WITH temp AS (SELECT region_id, region_name FROM dim_region)"
            " SELECT * FROM temp"
        )
        assert "WITH" in result

    # ══════ 负向测试：危险操作必须被拦截 ══════

    def test_DROP_TABLE_应被拦截(self):
        # pytest.raises(异常类型, match=错误信息关键词)
        # 作用：断言代码会抛出指定异常
        # 如果抛出 ValueError 且 message 包含 "危险关键字" → 测试通过
        # 如果没抛异常 或 抛了别的异常 → 测试失败
        with pytest.raises(ValueError, match="危险关键字"):
            SQLSafetyValidator.validate("DROP TABLE fact_order")

    def test_DELETE_应被拦截(self):
        with pytest.raises(ValueError, match="危险关键字"):
            SQLSafetyValidator.validate("DELETE FROM fact_order WHERE region_id = 1")

    def test_UPDATE_应被拦截(self):
        with pytest.raises(ValueError, match="危险关键字"):
            SQLSafetyValidator.validate("UPDATE dim_product SET brand = 'test'")

    def test_INSERT_应被拦截(self):
        with pytest.raises(ValueError, match="危险关键字"):
            SQLSafetyValidator.validate(
                "INSERT INTO dim_region VALUES (999, 'test', 'test', 'CN')"
            )

    def test_ALTER_TABLE_应被拦截(self):
        with pytest.raises(ValueError, match="危险关键字"):
            SQLSafetyValidator.validate("ALTER TABLE fact_order ADD COLUMN test INT")

    def test_TRUNCATE_应被拦截(self):
        with pytest.raises(ValueError, match="危险关键字"):
            SQLSafetyValidator.validate("TRUNCATE TABLE fact_order")

    def test_CREATE_TABLE_应被拦截(self):
        with pytest.raises(ValueError, match="危险关键字"):
            SQLSafetyValidator.validate("CREATE TABLE backup AS SELECT * FROM fact_order")

    # ══════ SQL 注入特征测试 ══════

    def test_UNION_SELECT注入_应被拦截(self):
        with pytest.raises(ValueError, match="注入特征"):
            SQLSafetyValidator.validate(
                "SELECT * FROM dim_product WHERE name = '' UNION SELECT * FROM users"
            )

    def test_OR_1等于1注入_应被拦截(self):
        with pytest.raises(ValueError, match="注入特征"):
            SQLSafetyValidator.validate(
                "SELECT * FROM dim_customer WHERE name = '' OR '1'='1'"
            )

    def test_注释截断注入_应被拦截(self):
        with pytest.raises(ValueError, match="注入特征"):
            SQLSafetyValidator.validate(
                "SELECT * FROM dim_customer WHERE id = '' OR 1=1 --"
            )

    # ══════ 边界条件 ══════

    def test_空SQL_应被拦截(self):
        with pytest.raises(ValueError, match="为空"):
            SQLSafetyValidator.validate("")

    def test_空白SQL_应被拦截(self):
        with pytest.raises(ValueError, match="为空"):
            SQLSafetyValidator.validate("   \n\t  ")

    def test_非SELECT开头_应被拦截(self):
        with pytest.raises(ValueError, match="只允许 SELECT"):
            SQLSafetyValidator.validate("SHOW TABLES")

    def test_非WITH开头_应被拦截(self):
        with pytest.raises(ValueError, match="只允许 SELECT"):
            SQLSafetyValidator.validate("EXPLAIN SELECT * FROM dim_region")

    # ══════ 防误杀测试 ══════

    def test_DROP在字段别名中_不应被误杀(self):
        """SELECT 'DROP' AS label → DROP 在引号里是数据，不是命令"""
        result = SQLSafetyValidator.validate(
            "SELECT 'DROP' AS action_label FROM dim_region"
        )
        assert "SELECT" in result

    def test_UPDATE在字段名中_不应被误杀(self):
        """updated_at 包含 UPDATE 但不应该被拦截（\b 词边界的作用）"""
        result = SQLSafetyValidator.validate(
            "SELECT updated_at FROM fact_order LIMIT 1"
        )
        assert "SELECT" in result

    def test_DELETE在字段名中_不应被误杀(self):
        """is_deleted 包含 DELETE，但 \b 只匹配完整单词，不匹配子串"""
        result = SQLSafetyValidator.validate(
            "SELECT * FROM dim_product WHERE is_deleted = 0"
        )
        assert "SELECT" in result

    def test_大小写混合DELETE_应被拦截(self):
        """DeLeTe 混合大小写也拦截（因为校验前转了 upper）"""
        with pytest.raises(ValueError, match="危险关键字"):
            SQLSafetyValidator.validate("DeLeTe FROM fact_order")
```

### 4.3 测试中发现的 Bug

**Bug**：`SELECT 'DROP' AS action_label FROM dim_region` 被误判为危险操作。

**根因**：正则 `\bDROP\b` 匹配了引号内的 DROP，但引号内是字符串数据，不是 SQL 关键字。

**修复**：在关键字匹配前，先移除所有字符串字面量：

```python
# 修复前（会误杀）
if re.search(pattern, sql_upper):     # 引号内的 DROP 也会匹配 ❌

# 修复后（不会误杀）
sql_no_strings = re.sub(              # 先用正则删掉引号内容
    r"'[^']*'",                        # 匹配 '任意非引号字符'
    "",                                # 替换为空
    sql_upper                          # 对大写的 SQL 做操作
)
if re.search(pattern, sql_no_strings): # 再在剩余部分匹配 ✅
```

**正则详解**：`r"'[^']*'"`
- `'` = 匹配开头的单引号
- `[^']` = 匹配任意一个"不是引号"的字符
- `[^']*` = 匹配零个或多个"不是引号"的字符
- `'` = 匹配结尾的单引号
- 整体 = 匹配一对引号及其内容，如 `'DROP'` → 被删除

### 4.4 运行结果

```bash
uv run pytest tests/ -v
```

```
test_正常SELECT查询_应通过 .......... PASSED
test_WITH_CTE查询_应通过 ............ PASSED
test_DROP_TABLE_应被拦截 ............ PASSED
test_DELETE_应被拦截 ................ PASSED
test_UPDATE_应被拦截 ................ PASSED
test_INSERT_应被拦截 ................ PASSED
test_ALTER_TABLE_应被拦截 ........... PASSED
test_TRUNCATE_应被拦截 .............. PASSED
test_CREATE_TABLE_应被拦截 .......... PASSED
test_UNION_SELECT注入_应被拦截 ...... PASSED
test_OR_1等于1注入_应被拦截 ......... PASSED
test_注释截断注入_应被拦截 .......... PASSED
test_空SQL_应被拦截 ................. PASSED
test_空白SQL_应被拦截 ............... PASSED
test_非SELECT开头_应被拦截 .......... PASSED
test_非WITH开头_应被拦截 ............ PASSED
test_DROP在字段别名中_不应被误杀 .... PASSED  ← 修复后通过
test_UPDATE在字段名中_不应被误杀 .... PASSED
test_DELETE在字段名中_不应被误杀 .... PASSED
test_大小写混合DELETE_应被拦截 ...... PASSED

======================== 20 passed in 0.05s ========================
```

---

## 五、代码规范整改

### 5.1 使用的工具

```bash
# ruff = Python 代码检查器（像老师批改作业）
# --fix = 自动修复能修的
uv run ruff check app/ tests/ --fix
```

### 5.2 修复的问题

| # | 类型 | 问题 | 修复方式 |
|---|------|------|----------|
| 1-3 | I001 | import 顺序不规范 | ruff --fix 自动排序 |
| 4 | F841 | tables 变量定义了但没用 | 手动删掉 |
| 5 | F401 | BaseHTTPMiddleware 导入了没用 | ruff --fix 自动删掉 |

```bash
# 最终结果
uv run ruff check app/ tests/
# → All checks passed! ✅
```

---

## 六、Python 知识点速查

按在代码中出现的顺序排列：

| # | 语法 | 是什么 | 在哪里用 |
|---|------|--------|----------|
| 1 | `import re` | 导入正则表达式模块 | sql_safety.py 做模式匹配 |
| 2 | `from X import Y` | 从模块导入特定东西 | 所有文件 |
| 3 | `class Name:` | 定义类 | SQLSafetyValidator |
| 4 | `@classmethod` | 类方法装饰器，直接 `类名.方法()` 调用 | validate() |
| 5 | `List[str]` | 类型注解：字符串列表 | FORBIDDEN_KEYWORDS |
| 6 | `Optional[X]` | 可选类型：X 或 None | allowed_tables 参数 |
| 7 | `-> str` | 返回值类型注解 | validate() 返回字符串 |
| 8 | `r"..."` | 原始字符串，\ 不转义 | 正则表达式 |
| 9 | `\b` | 正则：单词边界 | `r"\bDROP\b"` |
| 10 | `\s` | 正则：空白字符 | `r"\s*"` |
| 11 | `re.search(p, t)` | 在文本中搜索模式 | 关键字匹配 |
| 12 | `re.sub(p, r, t)` | 正则替换 | 删除字符串字面量 |
| 13 | `re.IGNORECASE` | 不区分大小写标志 | 注入检测 |
| 14 | `f"..."` | f-string，花括号里是变量 | 错误信息 |
| 15 | `[start:end]` | 切片，取字符串/列表片段 | SQL 上下文截取 |
| 16 | `.upper()` | 字符串全部转大写 | 关键字匹配前 |
| 17 | `.strip()` | 去掉首尾空白 | SQL 清理 |
| 18 | `.startswith("X")` | 检查是否以 X 开头 | SELECT 白名单 |
| 19 | `.index("X")` | 找到 X 的位置（索引） | 错误定位 |
| 20 | `max(0, x)` | 取较大值，防止负数 | 索引保护 |
| 21 | `len(x)` | 取长度 | 计算上下文范围 |
| 22 | `raise ValueError(...)` | 抛出异常 | 安全拦截 |
| 23 | `try/except` | 异常处理 | run_sql |
| 24 | `try 内嵌 try` | 嵌套异常处理 | 区分安全拦截 vs 数据库错误 |
| 25 | `return` vs `raise` | 正常返回 vs 报告异常 | 安全拦截用 return |
| 26 | `async def` | 定义异步函数 | run_sql、aembed_documents |
| 27 | `await` | 等待异步操作 | await db.run(sql) |
| 28 | `async with` | 异步上下文管理器 | aiohttp.ClientSession |
| 29 | `aiohttp` | 异步 HTTP 客户端 | 直连 TEI 服务 |
| 30 | `sys.path.insert(0, ...)` | 添加模块搜索路径 | conftest.py |
| 31 | `Path(__file__).parent` | 获取当前文件所在目录 | conftest.py |
| 32 | `pytest` | Python 测试框架 | test_sql_safety.py |
| 33 | `assert` | 断言：条件为 True 才通过 | 所有测试 |
| 34 | `pytest.raises(Type, match)` | 断言会抛出指定异常 | 负向测试 |
| 35 | `ruff check --fix` | 代码规范自动修复 | 命令行 |
| 36 | `zip(a, b, c)` | 把多个列表"拉链"在一起 | Qdrant 写入 |
| 37 | `**dict` | 字典解包传参 | `ColumnInfo(**payload)` |
| 38 | `list comprehension` | 列表推导式 | `[x for x in list]` |
| 39 | `dict comprehension` | 字典推导式 | `{row["Field"]: row["Type"] for row in rows}` |
| 40 | `| None` 类型注解 | Python 3.10+ 的联合类型 | `AsyncEngine | None` |

---

## 七、完整文件清单

### 项目中修改/新增的所有文件

```
D:\shopkeeper-agent-main\
│
├── .env                              # 修改：API Key 更换
├── conf\app_config.yaml              # 修改：LLM 模型切换
│
├── app\
│   ├── core\
│   │   └── sql_safety.py            # 新增：SQL 安全防火墙 ⭐
│   │
│   ├── agent\
│   │   └── nodes\
│   │       └── run_sql.py           # 重写：集成安全校验 ⭐
│   │
│   ├── clients\
│   │   └── embedding_client_manager.py  # 修改：API 兼容修复
│   │
│   ├── repositories\                # 现有代码（已阅读记录）
│   │   ├── mysql\
│   │   │   ├── dw\dw_mysql_repository.py     # 数据仓库 CRUD
│   │   │   └── meta\meta_mysql_repository.py # 元数据库 CRUD
│   │   ├── qdrant\
│   │   │   └── column_qdrant_repository.py   # 向量检索
│   │   └── es\
│   │       └── value_es_repository.py        # 全文检索
│   │
│   └── clients\
│       └── mysql_client_manager.py    # 现有代码：MySQL 连接管理
│
├── prompts\
│   └── generate_sql.prompt           # 修改：Prompt 安全红线 ⭐
│
├── tests\                            # 新增：测试目录 ⭐
│   ├── conftest.py                  # pytest 配置
│   └── test_sql_safety.py           # 20 个单元测试
│
└── docs\notes\                       # 新增：学习笔记 ⭐
    ├── SQL执行流程分析.md
    ├── SQL安全加固-代码学习笔记.md
    ├── SQL安全设计决策-Grill记录.md
    ├── 完整代码变更档案-20260629.md
    ├── 单元测试落地记录-20260630.md
    └── 藤子的Python成长笔记-全记录.md  ← 你正在读的这份
```
