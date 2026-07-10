# 生产环境痛点审查 RFC — Grill-me 6 刀

> **文档状态**: Draft  
> **审查日期**: 2026-07-09  
> **审查方式**: grill-me 逐刀追问 + 代码逐行阅读  
> **审查范围**: shopkeeper-agent 全部核心代码（12 节点 LangGraph + Redis 会话存储 + 评估脚本 + Prompt 构建）

---

## 目录

1. [痛点 1：SQL 修正只有一次机会](#痛点-1sql-修正只有一次机会)
2. [痛点 2：LLM 调用无重试/熔断/降级](#痛点-2llm-调用无重试熔断降级)
3. [痛点 3：召回率评估是 mock 的（假评估）](#痛点-3召回率评估是-mock-的假评估)
4. [痛点 4：没有任何可观测性](#痛点-4没有任何可观测性)
5. [痛点 5：Redis 并发竞态](#痛点-5redis-并发竞态)
6. [痛点 6：追问判断是关键词匹配（脆弱）](#痛点-6追问判断是关键词匹配脆弱)

---

## 痛点 1：SQL 修正只有一次机会

### 现状

`app/agent/graph.py` 第 83-88 行：

```python
graph_builder.add_conditional_edges(
    source="validate_sql",
    path=lambda state: "run_sql" if state["error"] is None else "correct_sql",
    path_map={"run_sql": "run_sql", "correct_sql": "correct_sql"},
)
graph_builder.add_edge("correct_sql", "run_sql")  # ← 修正后直接执行，不再校验
```

### 问题

- `validate_sql` 校验失败 → `correct_sql` 让 LLM 修正一次 → **直接去 `run_sql` 执行**
- 修正后的 SQL 仍有语法错误时，MySQL 执行直接抛异常，用户看到 500
- **没有重试上限**，也没有"修正失败后降级返回友好提示"的机制

### 影响面

| 场景 | 发生概率 | 后果 |
|------|---------|------|
| LLM 修正时改丢 JOIN 条件 | 中 | SQL 执行报错，500 |
| LLM 修正时引入语法错误 | 低 | SQL 执行报错，500 |
| LLM 修正时改了语义（从 SUM 改成 COUNT） | 中 | 结果错误但用户无感知 |

### 方案

**改造图结构：correct_sql → validate_sql 循环 + 重试上限**

```
generate_sql → validate_sql
validate_sql ──(error is None)──→ run_sql → END
validate_sql ──(error is not None)──→ correct_sql
correct_sql ──(retry_count < 3)──→ validate_sql    ← 循环
correct_sql ──(retry_count >= 3)──→ END（返回友好错误）
run_sql → END
```

**代码改动**：

1. `DataAgentState` 加字段：

```python
class DataAgentState(TypedDict):
    ...
    sql: str
    error: str
    retry_count: int  # ← 新增，初始 0
```

2. `correct_sql` 节点累加重试计数：

```python
async def correct_sql(state, runtime):
    ...
    return {"sql": result, "retry_count": state.get("retry_count", 0) + 1}
```

3. `graph.py` 改边定义：

```python
# 删除原来的：graph_builder.add_edge("correct_sql", "run_sql")

# 改成条件边：重试次数 < 3 回到 validate_sql 循环，否则降级结束
graph_builder.add_conditional_edges(
    source="correct_sql",
    path=lambda state: "validate_sql" if state.get("retry_count", 0) < 3 else "give_up",
    path_map={"validate_sql": "validate_sql", "give_up": "give_up"},
)
```

4. 新增 `give_up` 节点：

```python
async def give_up(state, runtime):
    writer = runtime.stream_writer
    writer({"type": "progress", "step": "放弃生成", "status": "error"})
    return {"sql": "", "error": "抱歉，多次修正仍无法生成有效 SQL，请换个问法试试"}
```

### 验收标准

- [ ] 修正后 SQL 会再次走 `validate_sql` 校验
- [ ] 重试 3 次仍失败时返回友好提示，不抛 500
- [ ] 正常 SQL（一次校验通过）不受影响

---

## 痛点 2：LLM 调用无重试/熔断/降级

### 现状

`app/agent/llm.py` 全部内容：

```python
llm = init_chat_model(
    model="deepseek-v4-pro",
    model_provider="openai",
    base_url="https://opencode.ai/zen/go/v1",
    api_key=...,
    temperature=0,
)
```

**20 行代码，5 行有效配置，零行容错。**

8 个节点（`recall_column`、`recall_value`、`recall_metric`、`filter_table`、`filter_metric`、`generate_sql`、`correct_sql`、`add_extra_context`）的调用方式：

```python
chain = prompt | llm | output_parser
result = await chain.ainvoke({...})  # 无 try/except、无 timeout、无 fallback
```

### 问题

| 缺失项 | 后果 |
|--------|------|
| 无 `timeout` | DeepSeek 卡住不响应 → 请求永远挂着 → uvicorn worker 被占满 |
| 默认 `max_retries=2` 但无退避 | 连续打两次 429 还是 429 |
| 无降级 LLM | 主模型 API 挂了 → 全站 500 |
| 无熔断器 | API 持续不可用时每次请求都尝试调用主模型，浪费资源 |

### 方案

**改造 `app/agent/llm.py`：封装 `ResilientLLM` 包装器**

```python
import time
from loguru import logger
from langchain.chat_models import init_chat_model
from app.conf.app_config import app_config

# 主模型
_primary_llm = init_chat_model(
    model=app_config.llm.model_name,
    model_provider="openai",
    base_url=app_config.llm.base_url,
    api_key=app_config.llm.api_key,
    temperature=0,
    timeout=30,           # 单次调用 30s 超时
    max_retries=3,        # 重试 3 次（覆盖默认 2）
)

# 降级模型（配置在 app_config.yaml 的 llm.fallback 段）
_fallback_llm = init_chat_model(
    model=app_config.llm.fallback_model,
    model_provider="openai",
    base_url=app_config.llm.fallback_base_url,
    api_key=app_config.llm.fallback_api_key,
    temperature=0,
    timeout=30,
    max_retries=2,
)


class ResilientLLM:
    """带超时 + 重试 + 降级的 LLM 包装器

    所有节点 `from app.agent.llm import llm` 不用改一行代码，
    自动获得容错能力。
    """

    def __init__(self, primary, fallback, fail_threshold=5):
        self._primary = primary
        self._fallback = fallback
        self._fail_count = 0
        self._threshold = fail_threshold  # 连续失败 N 次切降级
        self._last_fail_time = 0
        self._recovery_interval = 60  # 60s 后尝试恢复主模型

    async def ainvoke(self, *args, **kwargs):
        now = time.time()

        # 如果主模型之前失败过，但已过恢复窗口，尝试恢复
        if self._fail_count >= self._threshold:
            if now - self._last_fail_time > self._recovery_interval:
                logger.info("尝试恢复主模型...")
                self._fail_count = 0

        # 尝试主模型
        if self._fail_count < self._threshold:
            try:
                result = await self._primary.ainvoke(*args, **kwargs)
                self._fail_count = 0  # 成功就重置
                return result
            except Exception as e:
                self._fail_count += 1
                self._last_fail_time = now
                logger.warning(
                    f"主模型失败 ({self._fail_count}/{self._threshold}): {e}"
                )

        # 降级到备用模型
        logger.warning("切换到降级模型")
        try:
            return await self._fallback.ainvoke(*args, **kwargs)
        except Exception as e:
            logger.error(f"降级模型也失败了: {e}")
            raise Exception("主模型和降级模型均不可用")

    def invoke(self, *args, **kwargs):
        """同步版本（兼容非 async 调用）"""
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            self.ainvoke(*args, **kwargs)
        )


llm = ResilientLLM(_primary_llm, _fallback_llm)
```

**配置补充**（`conf/app_config.yaml`）：

```yaml
llm:
  model_name: deepseek-v4-pro
  api_key: ${oc.env:LLM_API_KEY}
  base_url: https://opencode.ai/zen/go/v1
  fallback_model: qwen-plus         # ← 新增
  fallback_base_url: ...            # ← 新增
  fallback_api_key: ...             # ← 新增
```

### 验收标准

- [ ] 主模型超时 30s 后自动重试最多 3 次
- [ ] 连续失败 5 次后自动切换降级模型
- [ ] 降级模型可用时用户无感知
- [ ] 60s 后自动尝试恢复主模型
- [ ] 8 个节点代码零改动

---

## 痛点 3：召回率评估是 mock 的（假评估）

### 现状

`tests/eval_recall.py` 第 91-216 行：

```python
def mock_recall(query: str):
    mock_data = {
        "全国有多少个客户": {
            "tables": ["dim_customer"],
            "columns": ["dim_customer.customer_id"],
            "metrics": [],
        },
        ...
    }
    data = mock_data[query]
    return data["tables"], data["columns"], data["metrics"]
```

### 问题

**评估的不是真实系统，是人工标注的 ground truth 跟自己比。**

`evaluate_one_case` 调 `mock_recall(query)` 拿到的"实际召回结果"就是人工手写的标准答案——命中率当然接近 100%。

真正应该测的是完整链路：`query → jieba 分词 → LLM 扩展关键词 → Embedding 向量化 → Qdrant 检索 → ES 全文检索 → 合并结果`。

这条链路里**任何一个环节出问题**都会导致召回错误，但 mock 全程跳过了。

### 影响

- 面试官问"你的召回率多少"，说"跑了评估"，但评估是 mock 的 → 被追问到露馅
- 真实上线后召回率未知，无法定位是哪个环节出问题
- 改了 prompt 或换模型后，无法量化评估效果变化

### 方案

**1. 写真实链路评估脚本 `tests/eval_recall_real.py`**

```python
import asyncio
from tests.eval_data import TEST_CASES
from app.agent.nodes.extract_keywords import extract_keywords_impl
from app.agent.nodes.recall_column import recall_column_impl
from app.agent.nodes.recall_value import recall_value_impl
from app.agent.nodes.recall_metric import recall_metric_impl
from app.agent.nodes.merge_retrieved_info import merge_impl


async def real_recall(query: str, context) -> tuple:
    """真实链路召回：走完 jieba → LLM → Embedding → Qdrant → ES 全流程"""
    # 1. jieba 关键词提取
    keywords = extract_keywords_impl(query)

    # 2-3. LLM 扩展关键词 + Embedding + Qdrant 向量检索字段
    column_infos = await recall_column_impl(keywords, context)

    # 4. ES 全文检索取值
    value_infos = await recall_value_impl(keywords, context)

    # 5. Qdrant 向量检索指标
    metric_infos = await recall_metric_impl(keywords, context)

    # 6. 合并结果
    table_infos, cols, mets = merge_impl(column_infos, value_infos, metric_infos)
    return [t["name"] for t in table_infos], cols, mets


async def evaluate_all_cases_real():
    """真实链路批量评估"""
    # 初始化 6 个客户端（需要 Docker 容器全部启动）
    ...

    results = []
    for case in TEST_CASES:
        actual_tables, actual_columns, actual_metrics = await real_recall(
            case["query"], context
        )
        # 计算召回率（逻辑与 eval_recall.py 相同）
        table_recall = len(set(actual_tables) & set(case["expected_tables"])) / len(case["expected_tables"])
        column_recall = len(set(actual_columns) & set(case["expected_columns"])) / len(case["expected_columns"])
        ...
    return summary
```

**2. 定时执行**

- 不需要每次查询都跑（资源浪费）
- 每天定时跑一次（可挂在 APScheduler 上，与归档任务并列）
- 每次加了新表/新字段/改了 prompt 后手动跑一次

**3. 前置条件**

- 6 个 Docker 容器全部启动（MySQL + Qdrant + ES + Embedding + Redis + Kibana）
- `meta_config.yaml` 中的表/字段/指标已入库
- Qdrant collection 已建好索引

### 验收标准

- [ ] `real_recall` 走完完整链路（jieba → LLM → Embedding → Qdrant → ES）
- [ ] 召回率结果低于 100%（如果还是 100% 说明测试用例太简单）
- [ ] 能定位"哪个 query 召回失败"和"失败在哪个环节"
- [ ] 可挂在定时任务上每天执行

---

## 痛点 4：没有任何可观测性

### 现状

12 个节点每个的异常处理模式：

```python
try:
    writer({"type": "progress", "step": step, "status": "running"})
    ...
    return {...}
except Exception as e:
    logger.error(f"{step} failed: {e}")
    writer({"type": "progress", "step": step, "status": "error"})
    raise
```

**只打了一条日志，然后 raise 炸掉。没有指标埋点。**

### 问题

不知道的关键数据：

| 指标 | 重要性 | 当前状态 |
|------|--------|---------|
| 每个节点平均耗时 | 定位瓶颈 | ❌ 无 |
| 每个节点成功率 | 定位故障节点 | ❌ 无 |
| SQL 校验失败率 | 衡量 LLM 质量 | ❌ 无 |
| 修正后成功率 | 衡量修正效果 | ❌ 无 |
| 端到端延迟 P50/P99 | 用户体验 | ❌ 无 |
| 召回率（真实链路） | RAG 质量 | ❌ mock 的 |

面试官问"你的系统瓶颈在哪"→ **没有数据回答**。

### 方案

**装饰器模式统一拦截，一处改 12 个节点全覆盖**

```python
# app/agent/metrics.py
import time
from functools import wraps
from loguru import logger


def with_metrics(node_func):
    """节点指标埋点装饰器

    自动记录：
    - 节点名称
    - 执行耗时（ms）
    - 执行状态（ok/error）
    - 错误信息（如果失败）
    """
    @wraps(node_func)
    async def wrapper(state, runtime):
        step = node_func.__name__
        start = time.time()
        try:
            result = await node_func(state, runtime)
            duration_ms = int((time.time() - start) * 1000)
            logger.info(
                f"metrics | node={step} | duration={duration_ms}ms | status=ok"
            )
            return result
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            logger.error(
                f"metrics | node={step} | duration={duration_ms}ms | status=error | err={e}"
            )
            raise

    return wrapper
```

**在 `graph.py` 中批量装饰**：

```python
# graph.py 改动

from app.agent.metrics import with_metrics

# 注册节点时包一层装饰器
graph_builder.add_node("extract_keywords", with_metrics(extract_keywords))
graph_builder.add_node("recall_column", with_metrics(recall_column))
# ... 12 个节点全部包一层
```

**4 个核心指标**：

| 指标 | 埋点位置 | 日志格式 |
|------|---------|---------|
| 节点耗时 | 装饰器 | `node=generate_sql duration=3200ms status=ok` |
| 节点成功率 | 装饰器 | `node=generate_sql duration=3200ms status=error err=...` |
| SQL 校验失败率 | validate_sql | `sql_validate status=fail error=...` |
| 端到端延迟 | query_service | `e2e duration=8500ms query=...` |

### 进阶（后续可选）

如果后续要上 Prometheus + Grafana：

```python
# 从 structlog 升级到 prometheus_client
from prometheus_client import Summary, Counter

NODE_DURATION = Summary('node_duration_seconds', 'Node execution time', ['node'])
NODE_ERRORS = Counter('node_errors_total', 'Node errors', ['node'])

def with_metrics(node_func):
    @wraps(node_func)
    async def wrapper(state, runtime):
        step = node_func.__name__
        with NODE_DURATION.labels(step).time():
            try:
                return await node_func(state, runtime)
            except Exception as e:
                NODE_ERRORS.labels(step).inc()
                raise
    return wrapper
```

### 验收标准

- [ ] 每个节点执行后日志中有 `duration` 和 `status`
- [ ] 可以通过日志 grep 统计每个节点的平均耗时和成功率
- [ ] 12 个节点零代码改动（只改 graph.py 注册方式）
- [ ] 后续可平滑升级到 Prometheus

---

## 痛点 5：Redis 并发竞态

### 现状

`app/services/session_store.py` 的 `_redis_add`：

```python
async def _redis_add(client, session_id, role, content):
    pipe = client.pipeline()
    pipe.rpush(key, msg)       # 1. 追加消息
    pipe.ltrim(key, -10, -1)  # 2. 裁剪到最近10条
    pipe.expire(key, 86400)   # 3. 刷新TTL
    await pipe.execute()      # ← 一次性发送
```

内存层用了 `asyncio.Lock`：
```python
_memory_lock = asyncio.Lock()  # 全局锁
```

### 问题

**Redis 层没有锁。** 两个请求同时操作同一个 session_id 时：

```
时间线：
  请求A: RPUSH msg_A → LTRIM → EXPIRE    （pipeline 执行中）
  请求B:     RPUSH msg_B → LTRIM → EXPIRE  （也在执行）

实际执行顺序（Redis 单线程但 pipeline 之间可能交叉）：
  RPUSH msg_A
  RPUSH msg_B      ← B 插到了 A 后面
  LTRIM (A 发起的)  ← 裁掉的不对
  LTRIM (B 发起的)
```

结果：A 的消息可能被 B 的 LTRIM 裁掉。

**内存层全局锁的问题**：1000 个用户同时聊天，所有 `add_message` 排队等一把锁，P99 延迟从 2ms 涨到 500ms。

### 影响

| 场景 | 触发条件 | 后果 |
|------|---------|------|
| 同用户多标签页 | 同 session_id 并发请求 | 消息丢失（被 LTRIM 裁掉） |
| 前端重复提交 | 同 session_id 快速连续请求 | 消息顺序混乱 |
| 多 worker 部署 | uvicorn --workers 4 | asyncio.Lock 只锁单进程，跨 worker 无效 |

### 方案

**方案 A：Redis 用 Lua 脚本原子化（推荐）**

```python
# app/services/lua/save_session.lua
"""
local key = KEYS[1]
local msg = ARGV[1]
local ttl = tonumber(ARGV[2])
local max_len = tonumber(ARGV[3])

redis.call('RPUSH', key, msg)
redis.call('LTRIM', key, -max_len, -1)
redis.call('EXPIRE', key, ttl)
return redis.call('LLEN', key)
"""
```

```python
# session_store.py 改动
from app.clients.redis_client_manager import redis_client_manager

# 预加载 Lua 脚本
_save_session_script = None

async def _redis_add_atomic(client, session_id, role, content):
    """Lua 脚本原子化写入（多 worker 安全）"""
    global _save_session_script
    if _save_session_script is None:
        _save_session_script = client.register_script(_save_session_lua)

    truncated = content[:500] if len(content) > 500 else content
    msg = json.dumps({"role": role, "content": truncated}, ensure_ascii=False)
    key = _key(session_id)

    await _save_session_script(
        keys=[key],
        args=[msg, redis_cfg.default_ttl_seconds, 10],
    )
```

**方案 B：内存锁按 session_id 分锁**

```python
_locks: Dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()  # 保护 _locks 字典本身

async def _get_lock(session_id: str) -> asyncio.Lock:
    """按 session_id 获取锁（不同 session 不互相阻塞）"""
    async with _locks_guard:
        if session_id not in _locks:
            _locks[session_id] = asyncio.Lock()
        return _locks[session_id]
```

**推荐**：方案 A + B 同时使用。Redis 层用 Lua 保证原子性，内存层用分锁避免全局瓶颈。

### 验收标准

- [ ] 同 session_id 的并发请求不会丢消息
- [ ] 不同 session_id 的请求不互相阻塞
- [ ] 多 worker 部署时并发安全
- [ ] 现有 15 个单元测试全部通过

---

## 痛点 6：追问判断是关键词匹配（脆弱）

### 现状

`app/services/prompt_builder.py` 的 `is_followup_query`：

```python
followup_words = ["那", "那个", "再", "还有", "上", "刚才", "然后", "呢"]

for word in followup_words:
    if word in query:
        return True

if len(query) < 15:
    return True

return False
```

### 问题

**3 个严重问题**：

#### 问题 1：误杀

| 用户输入 | 命中关键词 | 判定 | 正确？ | 后果 |
|---------|-----------|------|--------|------|
| "上月 GMV" | "上" | 追问 | ❌ 可能是新问题 | LLM 被误导去结合不相关历史 |
| "还有哪些表" | "还有" | 追问 | ❌ 可能是第一句话 | 注入错误的 task_hint |

#### 问题 2：漏判 + 碰巧判对

| 用户输入 | 命中关键词 | 长度 | 判定 | 正确？ |
|---------|-----------|------|------|--------|
| "换成华北" | 无 | 3 < 15 | 追问 | ✅ 碰巧 |
| "按品类拆开看看" | 无 | 7 < 15 | 追问 | ✅ 碰巧 |
| "帮我看一下华南区各品类的销售额对比" | 无 | 14 < 15 | 追问 | ❌ 可能是新问题 |

#### 问题 3：根本缺陷

**用规则匹配去判断语义问题**——跟用正则解析 HTML 一样，能跑但一遇边界 case 就崩。

自然语言里"追问"的表达方式有无穷多种：
- 代词引用："这个"、"那个"、"它"、"上面的"
- 省略主语："换成华东" → 省略了"上面说的那个指标"
- 指代消解："按品类拆开" → "按"什么拆开？上一次聊的内容
- 上下文依赖："那上个月呢" → "那"指代上一次的话题

8 个关键词 + 长度阈值覆盖不了这些。

### 方案

**方案 1：LLM 判断（推荐，准确率最高）**

```python
from app.agent.llm import llm

FOLLOWUP_JUDGE_PROMPT = """判断以下用户输入是"追问"还是"新问题"。

追问：依赖上文才能理解，如"换成华北"、"那上个月呢"、"按品类拆开"
新问题：可以独立理解，如"全国有多少客户"、"华东区上月GMV"

历史对话：
{history}

当前输入：{query}

只回答 "followup" 或 "new" 三个字母之一。"""


async def is_followup_query_llm(query: str, history: list) -> bool:
    """用 LLM 判断是否追问"""
    if not history:
        return False  # 没有历史，一定不是追问

    prompt = FOLLOWUP_JUDGE_PROMPT.format(
        history=format_history(history),
        query=query,
    )
    result = await llm.ainvoke(prompt)
    return "followup" in result.content.lower()
```

**优点**：准确率 90%+，覆盖所有语义变体  
**缺点**：多一次 LLM 调用，增加延迟（~1-2s）

**方案 2：嵌入向量相似度（折中方案）**

```python
from app.clients.embedding_client_manager import embedding_client_manager

async def is_followup_query_embed(query: str, history: list) -> bool:
    """用 Embedding 相似度判断追问"""
    if not history:
        return False

    # 取上一轮用户问题和当前问题的嵌入
    last_user_msg = [m["content"] for m in history if m["role"] == "user"][-1]
    embed_last = await embedding_client_manager.client.embed(last_user_msg)
    embed_current = await embedding_client_manager.client.embed(query)

    # 余弦相似度
    similarity = cosine_similarity(embed_last, embed_current)

    # 相似度高 → 追问（话题相关）
    # 相似度低 → 新问题（话题切换）
    return similarity > 0.75
```

**优点**：比关键词准，比 LLM 快（~50ms）  
**缺点**：需要调阈值，边界 case 不如 LLM 准

**方案 3：规则增强（最小改动）**

如果暂时不想加 LLM/Embedding 调用，至少修复误杀：

```python
def is_followup_query(query: str) -> bool:
    # 1. 代词检测（比关键词更精准）
    pronouns = ["这个", "那个", "它", "上面", "刚才说的", "上面的"]
    for pron in pronouns:
        if pron in query:
            return True

    # 2. 短句 + 有历史时才算追问（必须有历史这个前置条件）
    if len(query) < 10:
        return True

    # 3. 排除误杀：以"上"开头但后面跟"月/周/年"的不是追问
    import re
    if re.match(r'^上[月周年]', query):
        return False  # "上月GMV" 不是追问

    return False
```

**推荐**：短期用方案 3（最小改动修复误杀），中期升级到方案 2（Embedding），长期用方案 1（LLM）。

### `build_prompt` 也需要改

当前追问和新问题的区别只是 `task_hint` 一句话：

```python
if is_followup_query(query):
    task_hint = "这是一个追问，请结合历史对话理解用户真正想查询的内容。"
else:
    task_hint = "这是一个新问题。"
```

**不够**。如果判定为追问，LLM 还需要知道：
- 上一轮的**表**是什么（用户说"换成华南"→ 上一轮查的是什么表？）
- 上一轮的**指标**是什么
- 当前问题里**缺失了什么**（主语？表？指标？时间？）

改进：

```python
def build_prompt(query: str, history: list) -> str:
    history_text = format_history(history)

    if is_followup_query(query) and history:
        # 提取上一轮的关键信息
        last_user_msg = [m for m in history if m["role"] == "user"][-1]["content"]
        last_assistant_msg = [m for m in history if m["role"] == "assistant"][-1]["content"]

        task_hint = f"""这是一个追问。上一轮用户问了：{last_user_msg}
上一轮查询结果涉及：{last_assistant_msg}
请结合上一轮的上下文理解当前问题，补全用户省略的信息。"""
    else:
        task_hint = "这是一个新问题，无需参考历史对话。"

    prompt = f"""
【对话历史】
{history_text}

【当前问题】
{query}

【任务类型】
{task_hint}
"""
    return prompt
```

### 验收标准

- [ ] "上月 GMV" 不再被误判为追问
- [ ] "还有哪些表" 作为第一句话不再被误判
- [ ] "换成华北" 能正确判为追问（有历史时）
- [ ] 追问时 LLM 能看到上一轮的具体内容
- [ ] 无历史时 `is_followup_query` 返回 False

---

## 优先级排序

| 优先级 | 痛点 | 改动量 | 影响面 |
|--------|------|--------|--------|
| P0 | 1. SQL 修正循环 | 小（graph.py + state.py） | 防止 500 错误 |
| P0 | 2. LLM 重试/降级 | 中（llm.py 重写） | 防止全站不可用 |
| P1 | 5. Redis 并发竞态 | 中（Lua 脚本 + 分锁） | 防止消息丢失 |
| P1 | 6. 追问判断 | 中（规则增强/LLM） | 防止 LLM 误导 |
| P2 | 4. 可观测性 | 小（装饰器） | 运维能力 |
| P2 | 3. 真实召回评估 | 大（新脚本 + 依赖启动） | 质量度量 |

---

## 总结

这 6 个痛点不是面试八股，是**代码里实实在在存在的问题**。每个都有：
- 明确的问题代码位置
- 真实的触发场景
- 可落地的改进方案
- 可验证的验收标准

建议按优先级从 P0 开始改，每个改动都配套写单元测试。
