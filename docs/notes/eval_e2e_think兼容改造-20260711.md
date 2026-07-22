# eval_e2e 评测体系 + think 块兼容改造（2026-07-11）

> **背景**：简历 v5 要数据支撑（"端到端 SQL 生成准确率 X%"），需要建评测体系跑 baseline。
> **目标**：50 条 query 跑通 eval_e2e，拿到真准确率。
> **涉及文件**：
> - 新增 `app/core/safe_json_parser.py`
> - 修改 8 个 LangChain 节点（filter_metric / filter_table / _recall_helpers / generate_sql / correct_sql / rewrite_query / classify_intent / respond_chitchat）
> - 修改 `tests/eval_e2e.py`（state 字段名 + 算分逻辑）
> - 修改 `docker/docker-compose.yaml` + `conf/app_config.yaml`（端口 3306→3307）

---

## 一、踩到的 5 个坑（按时间顺序）

### 坑 1：MySQL 端口被本地 MySQL 占用

**症状**：
```
[+] up 5/6
 ✔ Container shopkeeper-redis Running
 ⠼ Container mysql            Starting
Error response from daemon: ports are not available:
exposing port TCP 0.0.0.0:3306 -> 127.0.0.1:0:
listen tcp 0.0.0.0:3306: bind: address already in use
```

**根因排查**：
```bash
sudo lsof -i :3306
# → mysqld 363  ... /usr/local/mysql/bin/mysqld
# → 这是 macOS Homebrew 装的本地 MySQL，不是 docker 容器
```

**解决**（**不要 kill 本地 MySQL**，会挂掉其他项目）：
1. 改 `docker/docker-compose.yaml` 第 14 行：`"3306:3306"` → `"3307:3306"`（容器内还是 3306，宿主暴露 3307）
2. 改 `conf/app_config.yaml` 第 13、22 行：`port: 3306` → `port: 3307`（meta 库和 dw 库都要改）
3. 删旧 mysql 容器（状态是 Created，没真正起）：`docker rm mysql`
4. 重新起：`docker compose up -d mysql`

**改前 vs 改后**：
| 文件 | 改前 | 改后 |
|---|---|---|
| `docker-compose.yaml` | `"3306:3306"` | `"3307:3306"` |
| `app_config.yaml` (db_meta) | `port: 3306` | `port: 3307` |
| `app_config.yaml` (db_dw) | `port: 3306` | `port: 3307` |

**验证**：
```bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
# 看到 6 容器（mysql/kibana/redis/qdrant/elasticsearch/embedding）都 Running
docker logs mysql | grep "ready for connections"
# → "ready for connections"
```

---

### 坑 2：API key 失效 401

**症状**：
```
Error code: 401 - {'type': 'error', 'error': {
    'type': 'authorized_error',
    'message': "login fail: Please carry the API secret key
                 in the 'Authorization' field of the request header (1004)"
}}
```

**根因**：`.env` 里的 `LLM_API_KEY=sk-tXap0Cck...` 失效了。

**解决**：更新 `.env` 中的 `LLM_API_KEY`（模型 MiniMax-M3）。密钥仅保存在本地环境变量中，不写入文档或 Git。

**注意**：换 key 后还要重启评测进程（LLM 客户端在进程启动时读 .env）。

---

### 坑 3：M3 模型输出 `<think>` 污染所有 parser

**症状**（filter_metric 节点）：
```
filter_metric 节点报错：
LLM 返回的 JSON 解析失败: <think>The user asks for the total customer count...
```

**症状**（generate_sql 节点）：
```
生成的SQL：<think>The user is asking for the total number of customers.
我需要查 dim_customer 表，用 COUNT 函数。</think>SELECT COUNT(*) FROM dim_customer
→ SQL 校验报语法错（多了一堆 think 块内容）
```

**根因**：M3 / DeepSeek 等模型会在 `<output>` 前输出 `<think>推理过程</think>` 块。LangChain 原生的：
- `JsonOutputParser` 直接 `json.loads(text)`，把 think 块当成 JSON → 解析失败
- `StrOutputParser` 直接返回 text 全部内容，think 块污染 SQL

**解决**：建 `app/core/safe_json_parser.py`，提供：
- `safe_parse_json(text)`: 剥 think + 抓 ```json``` 围栏 + regex 抓 JSON
- `SafeJsonOutputParser`: 替代 JsonOutputParser（继承 `BaseOutputParser`）
- `strip_think_for_str(text)`: 剥 think + 抓 ```sql``` 围栏
- `StripThinkStrParser`: 替代 StrOutputParser（**必须继承 BaseOutputParser**，否则 `prompt | llm | parser` chain 接不上）

**改前 vs 改后**（以 filter_metric.py 为例）：

改前：
```python
from langchain_core.output_parsers import JsonOutputParser
output_parser = JsonOutputParser()
```

改后：
```python
from app.core.safe_json_parser import SafeJsonOutputParser
# 用 SafeJsonOutputParser 兼容 M3/DeepSeek 的 <think> 块
output_parser = SafeJsonOutputParser()
```

**影响节点清单**（8 个文件）：
- `app/agent/nodes/filter_metric.py` - JsonOutputParser → SafeJsonOutputParser
- `app/agent/nodes/filter_table.py` - 同上
- `app/agent/nodes/_recall_helpers.py` - 同上
- `app/agent/nodes/generate_sql.py` - StrOutputParser → StripThinkStrParser
- `app/agent/nodes/correct_sql.py` - 同上
- `app/agent/nodes/rewrite_query.py` - 同上
- `app/agent/nodes/classify_intent.py` - 同上
- `app/agent/nodes/respond_chitchat.py` - 同上

---

### 坑 4：graph.py 需要 context 才能跑

**症状**：
```
File "/.../app/agent/nodes/recall_value.py", line 35, in <module>
    value_repo = runtime.context["value_es_repository"]
TypeError: 'NoneType' object is not subscriptable
```

**根因**：`app/agent/graph.py` 用了 LangGraph 0.2+ 的 `context_schema=DataAgentContext` 模式，**图执行必须传 `context` 参数**。评测脚本里 `agent_graph.ainvoke({...})` 没传 context。

**解决**：从 `graph.py` 末尾的官方测试代码抄上下文初始化逻辑（**这是 graph.py 自带的 reference 实现**）：

```python
qdrant_client_manager.init()
embedding_client_manager.init()
es_client_manager.init()
meta_mysql_client_manager.init()
dw_mysql_client_manager.init()

async with (
    meta_mysql_client_manager.session_factory() as meta_session,
    dw_mysql_client_manager.session_factory() as dw_session,
):
    ctx = DataAgentContext(
        column_qdrant_repository=ColumnQdrantRepository(qdrant_client_manager.client),
        embedding_client=embedding_client_manager.client,
        metric_qdrant_repository=MetricQdrantRepository(qdrant_client_manager.client),
        value_es_repository=ValueESRepository(es_client_manager.client),
        meta_mysql_repository=MetaMySQLRepository(meta_session),
        dw_mysql_repository=DWMySQLRepository(dw_session),
    )
    result = await agent_graph.ainvoke(input={...}, context=ctx)
```

---

### 坑 5：state 字段名 + sqlglot 算分双重 bug

**Bug 5a：state 字段名写错**

症状：评测脚本查 `result.get("final_sql", "")` 拿不到 SQL。

根因：我**自己编的字段名** `final_sql`，项目里实际叫 `sql`。

修复：评测脚本改 `result.get("sql", "")` 或 `result.get("final_sql", "")`。

**Bug 5b：sqlglot `find_all` API 变了**

症状：
```python
TypeError: isinstance() arg 2 must be a type, a tuple of types, or a union
```

根因：sqlglot 19 改了 API，`find_all("Table")`（字符串）不再支持，必须传 `exp.Table`（类）。

修复：
```python
from sqlglot import expressions as exp
g_tables = {t.name for t in g_ast.find_all(exp.Table)}  # ✅
g_tables = {t.name for t in g_ast.find_all("Table")}    # ❌ 报错
```

**Bug 5c：算分函数用 alias 比对导致 0 分**

症状：Generated SQL 和 Expected SQL 几乎一样，但 `sql_match=0.00`。

根因：原算分函数用 `col.sql()` 比较 SELECT 字段，**alias 命名差异让集合交集为 0**。
- Generated: `SELECT COUNT(customer_id) AS 客户数量 FROM dim_customer`
- Expected: `SELECT COUNT(customer_id) AS 客户总数 FROM dim_customer`
- `col.sql()` 返回 `COUNT(customer_id) AS 客户数量` vs `COUNT(customer_id) AS 客户总数` → 字符串不同 → 交集 0

修复：算分只比底层列名（`c.name`），不比较 alias：
```python
def _select_columns(ast):
    cols = set()
    for sel in (ast.selects or []):
        for c in sel.find_all(exp.Column):
            cols.add(c.name)  # 拿列名本身，不拿 alias
    return cols
```

**改前 vs 改后**（test case）：
| Generated | Expected | 改前分数 | 改后分数 |
|---|---|---|---|
| `SELECT COUNT(customer_id) AS 客户数量 FROM dim_customer` | `SELECT COUNT(customer_id) AS 客户总数 FROM dim_customer` | 0.00 | 1.00 |
| `SELECT SUM(order_quantity) AS 商品总数量 FROM fact_order` | `SELECT SUM(order_quantity) AS 订单商品总数量 FROM fact_order` | 0.00 | 1.00 |
| `SELECT DISTINCT region_name AS 地区 FROM dim_region ORDER BY 地区` | `SELECT region_name FROM dim_region` | 0.00 | 0.30 |

---

## 二、当前状态（2026-07-11 18:00）

### 已跑通
- ✅ Docker 5 容器全起（mysql 端口 3307）
- ✅ MySQL / Qdrant / ES 知识库就绪（5 表 + 75 条 Qdrant + 75 条 ES）
- ✅ LLM API key 换新（M3 模型）
- ✅ Think 块兼容（8 个节点 parser 全换）
- ✅ Graph context 传对
- ✅ SQL 字段名修对
- ✅ sqlglot 算分修对（实测 1.00 分数）

### 跑通验证（debug 单条 query）
```
query: 全国有多少个客户
sql: SELECT COUNT(customer_id) AS 客户数量 FROM dim_customer
validate_sql: SQL语法正确 ✅
run_sql: SQL 执行成功，返回 1 行数据 ✅
sql_match: 1.00
```

### 正在跑
- ⏳ 50 条完整评测（PID 16485，估 15-20 分钟），结果会写到 `tests/results/eval_e2e_<时间>.json`

---

## 三、关键决策记录

### 决策 1：改端口 vs kill 本地 MySQL
**改端口**。本地 MySQL 杀不得（其他项目可能用），改 docker 端口更安全。

### 决策 2：safe_json_parser 放哪
**放 `app/core/safe_json_parser.py`**，不放在节点里。8 个节点共用同一个 parser，**单一来源**。

### 决策 3：算分阈值
**0.8**（`EVAL_CONFIG["sql_match_threshold"]`）。**别用 1.0**——即使业务 SQL 完美，列名顺序、alias 风格差异都可能让分数不到 1.0。

### 决策 4：评测跑通后做什么
**先等结果，再写 v5 简历**。baseline 数据是简历能写"准确率 X%"的唯一依据。

---

## 四、遗留问题

1. **M3 模型 `filter_table` 偶尔返回 list 而不是 dict**（"LLM 返回的表过滤结果非 dict，降级保留全部候选表"）—— 需要在 prompt 里强制"必须返回 dict"
2. **filter_table / filter_metric 偶尔输出乱** —— 抓更多失败 case 看根因
3. **`result_match_score` 暂未启用**（依赖真实 SQL 执行对比）—— 短期只算 `sql_match_score`

---

## 五、怎么复用这次的修改

下次给 shopkeeper-agent 加新节点（用 LLM 解析输出）时，**直接 import 项目的 parser**：
```python
from app.core.safe_json_parser import SafeJsonOutputParser, StripThinkStrParser

# 解析 JSON
output_parser = SafeJsonOutputParser()
# 解析纯文本（含 ```sql``` 围栏）
output_parser = StripThinkStrParser()
```

跑评测时：
```bash
cd /path/to/shopkeeper-agent
.venv/bin/python -m tests.eval_e2e
# 结果在 tests/results/eval_e2e_<时间>.json
```

加新功能后想对比：
```bash
.venv/bin/python -m tests.eval_e2e
cp tests/results/eval_e2e_<最新>.json tests/results/eval_e2e_baseline.json
# 改代码 + 再跑
.venv/bin/python -m tests.eval_comparison tests/results/eval_e2e_baseline.json tests/results/eval_e2e_<新>.json "before" "after"
```
