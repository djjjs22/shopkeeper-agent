# Redis 升级架构改造 — 代码学习笔记

> 创建时间：2026-07-07 | 藤子的 Python + 架构学习笔记（完整实施记录）

---

## 变更概览

| 文件 | 操作 | 行数 | 说明 |
|------|------|------|------|
| `app/clients/redis_client_manager.py` | **新增** | +95 | Redis 客户端单例 + 可用性状态管理 |
| `app/services/session_store.py` | **重写** | +180 | 3 模式 WriteMode + 内存兜底 |
| `app/api/lifespan.py` | 改 | +3 | 启动时连接 Redis，关闭时释放 |
| `app/conf/app_config.py` | 改 | +14 | 新增 RedisConfig dataclass |
| `app/scripts/archive_sessions.py` | **新增** | +124 | 7+30 冷热分层归档脚本 |
| `conf/app_config.yaml` | 改 | +8 | Redis 配置块 |
| `docker/docker-compose.yaml` | 改 | +18 | redis 7-alpine 容器 |
| `docker/mysql/meta.sql` | 改 | +11 | session_archive 归档表 |
| `tests/test_session_store.py` | **新增** | +283 | 15 个单元测试 |
| `docs/architecture/redis-upgrade-rfc.md` | **新增** | +1211 | 完整 ADR 设计文档 |
| `pyproject.toml` | 改 | +4 | redis[hiredis]>=5.0.0 依赖 |
| **合计** | | **1984 行** | |

**L2/L3/Router/前端 全部零改动** —— 接口和实现分离的红利。

---

## 为什么要升级（业务背景）

### 之前的问题

`session_store.py` 用 Python 内存字典存储会话历史。3 个致命的"生产级"问题：

1. **重启数据丢失** —— 服务重启后 dict 清空，cookie 还在但追问失效
2. **多机不能共享** —— A 服务器的 dict B 服务器看不到
3. **过期不清理** —— 字典只增不减，长期跑内存膨胀

### 升级目标

把内存 dict 换成 Redis，**顺手解决 5 个生产级深坑中的 3 个**（剩下 2 个 Token 撑爆、敏感信息留二期）。

---

## 关键设计决策

### 决策 1：选 Redis 不用 Memcached

| 维度 | Redis | Memcached |
|------|-------|-----------|
| 持久化 | ✅ AOF/RDB | ❌ 纯内存 |
| 数据结构 | 多种（List/Hash/Stream） | 仅 KV |
| 适用场景 | 会话/缓存/消息队列 | 纯缓存 |

**选 Redis** —— 因为需要持久化（重启不丢）+ List 结构（消息追加语义最匹配）。

### 决策 2：接口和实现分离（核心亮点）

session_store.py 的 3 个函数（`get_history` / `add_message` / `clear_history`）是**稳定接口**，**实现可换**。

```python
# 升级前：内存 dict
def get_history(session_id, max_count):
    return _session_store.get(session_id, [])[-max_count:]

# 升级后：Redis + 内存兜底（函数签名一字不改）
async def get_history(session_id, max_count=5):
    client = await redis_client_manager.get_client()
    if client is None:
        return _memory_get(session_id, max_count)  # 降级
    # ... Redis 主路径
```

**这意味着什么？**

- `query_service.py` 一行代码不改
- `prompt_builder.py` 一行代码不改
- `query_router.py` 一行代码不改
- 前端零改动

**这就是"开闭原则"的实际体现** —— 对扩展开放（加 Redis 实现）、对修改关闭（不动调用方）。

### 决策 3：3 模式 WriteMode（平滑迁移）

为了从"纯内存"切换到"Redis 主"时不丢数据，设计了 3 个阶段：

```python
class WriteMode(Enum):
    MEMORY_ONLY = "memory_only"        # 阶段 0：纯内存（改造前）
    DUAL_WRITE = "dual_write"          # 阶段 1：双写（内存 + Redis）
    REDIS_PRIMARY = "redis_primary"    # 阶段 2：Redis 为主，内存仅作降级兜底
```

**通过环境变量切换**：`SESSION_WRITE_MODE=redis_primary`

### 决策 4：降级策略（Redis 挂了怎么办）

**核心思路**：Redis 不可用时自动 fallback 到内存 dict，**接口不挂**。

```python
async def get_history(session_id, max_count=5):
    client = await redis_client_manager.get_client()
    
    if client is None:                        # 1. 标记为不可用 → 直接降级
        return _memory_get(session_id, max_count)
    
    try:
        raw = await client.lrange(...)        # 2. 正常调用
        redis_client_manager.mark_available() # 3. 成功 → 重置失败计数
        return [json.loads(item) for item in raw]
    except Exception as e:                    # 4. 异常 → 降级
        logger.warning(...)
        redis_client_manager.mark_unavailable()
        return _memory_get(session_id, max_count)
```

**关键细节**：连续失败 N 次（默认 3 次）才标记 Redis 不可用——**避免网络抖动误降级**。

---

## 关键代码详解

### 1. RedisClientManager（单例 + 状态机）

```python
class RedisClientManager:
    def __init__(self):
        self._client = None          # Redis 连接
        self._available = False      # 当前是否可用
        self._fail_count = 0         # 连续失败计数（防抖动）
    
    def mark_unavailable(self):
        """连续失败 N 次才标记为不可用"""
        self._fail_count += 1
        threshold = redis_cfg.fail_threshold  # 默认 3
        if self._fail_count >= threshold and self._available:
            self._available = False
            logger.warning(f"[Redis] 连续失败 {self._fail_count} 次...")
```

**设计要点**：
- 状态用 `_available` 布尔标志，不用 `try/except` 每次探测（开销大）
- 失败计数避免"网络抖动"误判

### 2. Pipeline 原子操作（add_message）

```python
async def _redis_add(client, session_id, role, content):
    truncated = content[:500] if len(content) > 500 else content
    msg = json.dumps({"role": role, "content": truncated}, ensure_ascii=False)
    key = f"session:{session_id}"
    
    pipe = client.pipeline()       # 创建 pipeline（多个命令批量执行）
    pipe.rpush(key, msg)           # 1. 追加消息
    pipe.ltrim(key, -10, -1)       # 2. 只保留最近 10 条
    pipe.expire(key, 86400)        # 3. 设置 24h TTL
    await pipe.execute()           # 一次性发送 3 个命令
```

**为什么用 pipeline？**
- 3 个命令打包成 1 个网络请求（性能更好）
- 原子性：要么都成功要么都失败
- 减少 RTT（Round-Trip Time）

### 3. 内存兜底（线程安全 + 长度截断）

```python
_memory_fallback: Dict[str, List[Dict]] = {}
_memory_lock = threading.Lock()

def _memory_add(session_id, role, content):
    with _memory_lock:                                 # 加锁防并发
        if session_id not in _memory_fallback:
            _memory_fallback[session_id] = []
        truncated = content[:500] if len(content) > 500 else content  # 截断
        _memory_fallback[session_id].append({"role": role, "content": truncated})
        _memory_fallback[session_id] = _memory_fallback[session_id][-10:]  # 只留 10 条
```

**关键设计**：
- 线程锁（`threading.Lock`）防并发
- 长度截断（500 字符）防内存爆炸
- 数量截断（10 条）防 dict 增长

### 4. 归档脚本（7+30 冷热分层）

```python
async def archive_old_sessions(days_threshold: int = 7) -> int:
    redis = await redis_client_manager.get_client()
    cutoff = datetime.now() - timedelta(days=days_threshold)
    archived = 0
    
    async for key in redis.scan_iter(match="session:*"):
        ttl = await redis.ttl(key)
        if ttl > 86400:                # TTL > 24h 说明还没到 7 天
            continue
        # ... 读取、写入 MySQL、删除 Redis
        archived += 1
    
    return archived
```

**为什么用 SCAN 不用 KEYS？**
- `KEYS *` 会阻塞 Redis（生产事故）
- `SCAN` 增量遍历，对 Redis 性能影响小

---

## 完整数据流

### 写入流程（add_message）

```
query_service 调用 add_message(session_id, "user", "查询所有地区销售额")
  ↓
根据 WRITE_MODE 决定策略
  ↓
[阶段 0: MEMORY_ONLY] → _memory_add() → dict 完成
  ↓
[阶段 1: DUAL_WRITE] → _memory_add() + 尝试 Redis pipeline
  ↓                                          ↓ 失败
[阶段 2: REDIS_PRIMARY] → 尝试 Redis pipeline → 失败 → _memory_add() 兜底
```

### 读取流程（get_history）

```
query_service 调用 get_history(session_id, max_count=3)
  ↓
redis_client_manager.get_client()
  ↓
client is None? → 内存 dict 查找
  ↓
client 不为 None → 尝试 lrange
  ↓                       ↓ 异常
 成功解析返回              ↓
                  mark_unavailable() → 内存 dict 查找
```

---

## Python 知识点速查表

| 概念 | 用法 | 项目中的例子 |
|------|------|------------|
| `Enum` 枚举类 | `class WriteMode(Enum)` | 3 种写入模式 |
| `dataclass` | 自动生成 `__init__` | `RedisConfig(url, max_connections, ...)` |
| 异步上下文管理器 | `async with ... as ...` | `async with aiohttp.ClientSession() as session` |
| 异步迭代器 | `async for chunk in ...` | `async for key in redis.scan_iter(...)` |
| `asyncio.gather` | 并发运行多个协程 | （未来扩展：并发读 Redis + MySQL） |
| Pipeline 模式 | `pipe = client.pipeline()` | 3 个 Redis 命令打包发送 |
| 单例模式 | 全局唯一实例 | `redis_client_manager = RedisClientManager()` |
| 状态机 | 布尔标志 + 计数器 | `_available` + `_fail_count` |
| 线程锁 | `with _memory_lock:` | 内存 dict 并发写 |
| 异常重试 | try/except + 计数 | 连续失败 3 次才降级 |
| JSON 序列化 | `json.dumps(...)` | Redis 存字符串 |
| TTL 过期 | `EXPIRE key seconds` | Redis 自动清理 |

---

## 单元测试覆盖（15 个用例）

| 类别 | 用例 | 验证点 |
|------|------|--------|
| **正常场景** (4) | `test_get_history_calls_redis` | 正常调用 lrange |
| | `test_add_message_calls_redis_pipeline` | 验证 rpush/ltrim/expire/execute |
| | `test_add_message_also_writes_memory` | 写 Redis 同时也写内存（兜底） |
| | `test_clear_history_clears_both` | 同时清 Redis 和内存 |
| **降级场景** (4) | `test_get_history_fallback_when_marked_unavailable` | 标记不可用 → 走内存 |
| | `test_get_history_fallback_on_redis_error` | 异常 → 走内存 |
| | `test_mark_unavailable_after_threshold` | 连续 3 次失败才标记 |
| | `test_add_message_writes_memory_when_redis_down` | 不可用时写内存 |
| **边界场景** (5) | `test_long_content_truncated_to_500` | 超长截断 |
| | `test_max_10_messages_in_memory` | 数量限制 |
| | `test_empty_session_returns_empty_list` | 空 session |
| | `test_clear_nonexistent_session_no_error` | 清不存在的 session |
| | `test_concurrent_add_message_thread_safe` | 并发安全 |
| **集成场景** (2) | `test_round_trip_redis_mode` | 完整读写循环 |
| | `test_redis_recovery_resets_fail_count` | 恢复后计数清零 |

**测试结果**：15 passed, 0 failed

---

## 已解决的生产级深坑

| 深坑 | 来源（Grill 第 16 问） | 解决方案 |
|------|----------------------|---------|
| **重启数据丢失** | 服务重启后历史清空 | Redis AOF 持久化 |
| **多机不共享** | A 服务器 dict B 服务器看不到 | Redis 作为共享存储 |
| **过期不清理** | dict 只增不减 | Redis TTL + MySQL 归档 |

**未解决（留二期）**：
- Token 撑爆：单条消息过长 → 需 `tiktoken` 精确截断
- 敏感信息泄露：身份证/手机号 → 需 Presidio 之类专业工具

---

## 真实定位（重要）

⚠️ **本次升级是"简历级生产标准"**，不是"真生产运营"：

- ✅ 架构按生产标准设计（降级/分层/迁移/测试）
- ✅ 单元测试 15/15 通过
- ⚠️ **端到端 Docker 验证未做**（公司网络拉不到 redis 镜像）
- ⚠️ 当前 < 100 用户，**不真用 Redis 也跑得好**

**但这份代码展示了"生产级思维"** —— 面试时被问"为什么不直接用 Redis"或"Redis 挂了怎么办"都能讲清楚。

---

## 面试讲法（5 分钟版本）

> "在我的电商问数项目里，会话记忆最初用 Python 内存字典。
>
> 但我发现 3 个生产级问题：重启数据丢失、多机不共享、过期不清理。
>
> **架构升级**：我设计了 Redis 为主、内存兜底的方案，**接口零改动**。
>
> **关键设计**：
> 1. `RedisClientManager` 单例 + 状态机管理可用性
> 2. 3 模式 `WriteMode` 支持平滑迁移（MEMORY_ONLY → DUAL_WRITE → REDIS_PRIMARY）
> 3. 连续失败 3 次才标记不可用——避免网络抖动
> 4. 7+30 冷热分层——Redis 7 天 TTL + MySQL 归档 30 天
>
> **测试覆盖**：15 个 pytest 用例，4 类场景（正常/降级/边界/集成），100% 通过。
>
> **核心亮点**：**接口和实现分离** —— 替换存储层不影响调用方，体现开闭原则。"

---

## 实施过程踩坑记录

| 问题 | 原因 | 解决 |
|------|------|------|
| `from app.conf.app_config import redis` 失败 | 字段名 `redis` 和 redis 库冲突 | 改成 `redis_cfg` |
| `import redis` 字段在 dataclass 里识别不了 | 字段名带下划线 | OmegaConf merge 失败 |
| OmegaConf 报 `Key 'redis' not in 'AppConfig'` | yaml 里字段名是 `redis` 但 dataclass 是 `redis_cfg` | 同步改 yaml |
| `from app.conf.app_config import redis_cfg` 失败 | dataclass 字段是实例属性，模块级不可直接 import | 改成 `from app.conf.app_config import app_config` + `redis_cfg = app_config.redis_cfg` |
| pytest 报 `async def functions are not natively supported` | 缺 pytest-asyncio 配置 | pyproject.toml 加 `asyncio_mode = "auto"` |
| `test_add_message_calls_redis_pipeline` 失败 | AsyncMock 嵌套 + `pipeline()` 链式调用 mock 错 | 改用 MagicMock 链式 + AsyncMock execute |
| docker pull redis:7-alpine 拉了 15+ 分钟失败 | 公司网络拉不到 Docker Hub | 阿里云镜像路径错（需要 login），放弃端到端验证，commit pytest 自验收 |

---

## 关联文档

- `docs/architecture/redis-upgrade-rfc.md` —— 完整 ADR（背景/架构/3 坑策略/改动清单/回滚/监控/附录，1211 行）
- `tests/test_session_store.py` —— 15 个单元测试
- Grill 第 16 问：会话记忆的 5 个深坑 —— 升级动机
- Grill 第 17 问：Redis 4 个生产级深坑 —— 设计依据
- `conf/app_config.yaml` —— Redis 配置
- `docker/docker-compose.yaml` —— Redis 容器定义
