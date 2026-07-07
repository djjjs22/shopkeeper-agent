# RFC v0.2 修订清单：Redis 会话架构 4 个生产深坑

> **目的**：把 grill-me 找出的生产环境问题整理成可执行的待办清单
> **文档状态**：草案 v0.2 补充
> **针对文档**：`docs/architecture/redis-upgrade-rfc.md` (v0.1)
> **优先级**：P0（必改） / P1（强烈建议） / P2（可推迟到二期）

---

## 修订项总览

| # | 问题 | 优先级 | 影响范围 | 工作量 | 关联代码 |
|---|------|--------|---------|--------|---------|
| 1 | `_memory_fallback` 没有上限 | **P0** | `session_store.py:44` | 30 分钟 | `_memory_fallback` + `_memory_add` |
| 2 | Redis 恢复后没有主动探活 | **P1** | `redis_client_manager.py:33` | 60 分钟 | `RedisClientManager.init/close/get_client` |
| 3 | 归档任务没有接入调度器 | **P1** | `app/scripts/archive_sessions.py` | 30-60 分钟 | 新增 `app/services/scheduler.py` + lifespan 注册 |
| 4 | `threading.Lock` 与 async 不兼容 | **P1** | `session_store.py:12, 45` | 15 分钟 + 测试更新 | 替换为 `asyncio.Lock` + 5 个测试 |
| 5 | 敏感信息过滤理由不充分（RFC 文本问题） | **P2** | `redis-upgrade-rfc.md:603` | 10 分钟 | 仅改 RFC 文字 |

---

## P0-1：`_memory_fallback` 必须有上限

### 现状

```python
# app/services/session_store.py:44
_memory_fallback: Dict[str, List[Dict]] = {}
_memory_lock = threading.Lock()
```

`_memory_fallback` 是降级兜底层，但：
- **无 LRU 上限**：所有 session 都累积
- **无 TTL 清理**：7 天前访问过的 session 还在内存
- **双写期永久增长**：Redis 健康时也写内存（见 §3.3 阶段 1）

### 风险场景

- **场景 A**：1 万用户连续 30 天没访问 → 1 万 × 10 条 × 500 字符 ≈ 50MB
- **场景 B**：Redis 持续健康，内存 dict 从未被使用但**始终在增长**（最隐蔽的内存泄漏）
- **场景 C**：双写期从阶段 1 切到阶段 2 后，老的内存数据**永远不释放**

### 推荐方案

**LRU 上限 + 定期清理**，二选一：

#### 方案 A：固定 LRU 上限（推荐）

```python
# 在 session_store.py 顶部
MAX_MEMORY_SESSIONS = 1000  # 内存最多保留 1000 个 session

def _memory_add(session_id: str, role: str, content: str) -> None:
    with _memory_lock:
        # LRU 淘汰：超过上限时删除最久未访问的 session
        if len(_memory_fallback) >= MAX_MEMORY_SESSIONS:
            if session_id not in _memory_fallback:
                # 删一个最旧的（Python 3.7+ dict 保持插入顺序）
                oldest = next(iter(_memory_fallback))
                _memory_fallback.pop(oldest)
                logger.debug(f"[session_store] 内存 dict 满，淘汰最旧 session: {oldest}")

        if session_id not in _memory_fallback:
            _memory_fallback[session_id] = []
        # ... 原有逻辑
```

#### 方案 B：阶段 2 之后停止写内存（更彻底）

修改 `WriteMode.REDIS_PRIMARY` 分支：

```python
# 当前：Redis 成功时也写一份到内存
await _redis_add(client, session_id, role, content)
_memory_add(session_id, role, content)  # ← 删掉这行

# 改为：Redis 成功时只写 Redis，内存仅在 Redis 失败时才写
await _redis_add(client, session_id, role, content)
```

**好处**：内存 dict 在 Redis 健康时是空的，只在故障期间才会被填满，故障恢复后下次 LRU 清理掉。

### 配套修改

- 新增 `MAX_MEMORY_SESSIONS` 到 `conf/app_config.yaml`
- 单测加一个 `test_memory_lru_eviction`
- 文档：RFC §3.1 "代价评估" 补一行"内存 dict 最多 1000 个 session"

### 验证方法

```python
# 单元测试
async def test_memory_lru_eviction():
    for i in range(1001):
        await session_store.add_message(f"session_{i}", "user", "hi")
    assert len(session_store._memory_fallback) <= 1000
```

---

## P1-2：Redis 恢复后必须主动探活

### 现状

```python
# app/clients/redis_client_manager.py:33
def init(self) -> None:
    try:
        self._client = redis_async.from_url(...)
        self._available = True  # ← 假设可用
    except Exception as e:
        self._available = False
```

`mark_available()` 只在 `get_history` / `add_message` 成功路径里被调。

### 风险场景

1. Redis 启动时连接失败 → `init` 标记 `available=False`
2. 应用跑了一段时间，`available=False` 期间业务调用失败累计 3 次
3. **Redis 实际恢复了** → 但**没有任何调用会触发探活**（除非新增的 `add_message` 第一次走成功路径）
4. **极端情况**：用户只读不写（只发 `get_history`），`available=False` 期间直接走内存 → 永远探不活
5. **更极端**：重启后 Redis 还没起好 → `init` 失败 → 永远不探活

### 推荐方案

**后台协程定期探活**，每 30s `PING` 一次：

```python
# app/clients/redis_client_manager.py
import asyncio

class RedisClientManager:
    def __init__(self):
        # ... 现有字段
        self._probe_task: Optional[asyncio.Task] = None
        self._probe_interval: int = 30  # 秒
        self._lock = asyncio.Lock()  # 用于初始化 lock

    async def start(self) -> None:
        """异步初始化 + 启动探活协程"""
        # 异步版本的 init（避免 init 同步创建客户端但未验证）
        try:
            self._client = redis_async.from_url(
                redis_cfg.url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=redis_cfg.socket_timeout,
                socket_timeout=redis_cfg.socket_timeout,
                max_connections=redis_cfg.max_connections,
            )
            await self._client.ping()  # 真实验证
            self._available = True
            logger.info(f"[Redis] 连接成功: {redis_cfg.url}")
        except Exception as e:
            self._available = False
            logger.warning(f"[Redis] 启动时连接失败: {e}")

        # 启动探活协程
        if self._probe_task is None or self._probe_task.done():
            self._probe_task = asyncio.create_task(self._probe_loop())
            logger.info(f"[Redis] 探活协程已启动（间隔 {self._probe_interval}s）")

    async def _probe_loop(self) -> None:
        """后台定期 PING，发现 Redis 恢复时自动 mark_available"""
        while True:
            try:
                await asyncio.sleep(self._probe_interval)
                if self._client is not None:
                    await self._client.ping()
                    self.mark_available()  # 成功就恢复
            except asyncio.CancelledError:
                logger.info("[Redis] 探活协程已取消")
                raise
            except Exception as e:
                # ping 失败不需要做任何事——业务调用会自己 mark_unavailable
                logger.debug(f"[Redis] 探活失败: {e}")

    async def close(self) -> None:
        """关闭：取消探活 + 关闭连接"""
        if self._probe_task and not self._probe_task.done():
            self._probe_task.cancel()
            try:
                await self._probe_task
            except asyncio.CancelledError:
                pass
        if self._client is not None:
            try:
                await self._client.close()
                logger.info("[Redis] 连接已关闭")
            except Exception as e:
                logger.warning(f"[Redis] 关闭连接时异常: {e}")
```

### 配套修改

- `init()` 改名为 `start()`，改为 `async`（lifespan 里 `await` 调用）
- `lifespan.py` 里同步 `init` 改成异步 `await redis_client_manager.start()`
- 探活间隔从 `redis_cfg.probe_interval_seconds` 读（默认 30）
- 单测加一个 `test_probe_loop_recovers_redis`

### 验证方法

```bash
# 1. 启动应用（Redis 不可用）
docker compose up app  # Redis 容器不启动

# 2. 手动起 Redis
docker compose up redis -d

# 3. 30s 内观察日志，应该看到 "[Redis] 恢复可用"
tail -f logs/app.log | grep "\[Redis\]"
```

---

## P1-3：归档任务必须接入调度器

### 现状

`docs/architecture/redis-upgrade-rfc.md:386` 说"每天 02:00 异步归档"，但：

- **没有 scheduler 代码**——`app/scripts/archive_sessions.py` 只是脚本入口
- **lifespan 没注册**——FastAPI 启动时没有创建任何定时任务
- **cron / k8s CronJob 也没配置**——`docker-compose.yaml` 没有 cron 服务

### 风险场景

- 部署上线后**7 天**，Redis 内存撑满（因为没人手动跑归档）
- 值班同学："归档脚本在哪儿？""在 `scripts/` 里。""怎么跑？""不知道，没人写过"

### 推荐方案

**APScheduler**（in-process，FastAPI 同进程，**最简单**）：

```python
# app/services/scheduler.py  (新增)
"""应用内定时任务调度器"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from app.scripts.archive_sessions import archive_old_sessions

_scheduler: AsyncIOScheduler | None = None


def start_scheduler() -> None:
    """启动调度器，注册归档任务"""
    global _scheduler
    if _scheduler is not None:
        return

    _scheduler = AsyncIOScheduler()
    # 每天凌晨 02:00 执行
    _scheduler.add_job(
        archive_old_sessions,
        trigger=CronTrigger(hour=2, minute=0),
        id="archive_sessions",
        name="归档 7 天前的 session 到 MySQL",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("[scheduler] 启动成功，归档任务已注册（每天 02:00）")


def stop_scheduler() -> None:
    """关闭调度器"""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[scheduler] 已停止")
        _scheduler = None
```

```python
# app/api/lifespan.py  (修改)
from app.services.scheduler import start_scheduler, stop_scheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... 现有初始化
    await redis_client_manager.start()  # P1-2 改了
    start_scheduler()  # ← 新增
    yield
    stop_scheduler()  # ← 新增
    await redis_client_manager.close()
```

### 配套修改

- `pyproject.toml` 加 `apscheduler>=3.10.0` 依赖
- RFC §3.2 选型说明补一行"调度器：APScheduler（in-process）"
- 风险说明：APScheduler 是 in-process，多副本部署时**每个副本都会跑**——需要加 `redis_lock` 防止重复归档（或者用 APScheduler 的 `coalesce=True`）

### 验证方法

```python
# 单元测试：验证调度器注册成功
def test_scheduler_registers_archive_job():
    start_scheduler()
    job = _scheduler.get_job("archive_sessions")
    assert job is not None
    stop_scheduler()
```

```bash
# 手动触发归档（不等 02:00）
python -m app.scripts.archive_sessions
```

---

## P1-4：`threading.Lock` → `asyncio.Lock`

### 现状

```python
# app/services/session_store.py:12, 45
import threading
_memory_lock = threading.Lock()

def _memory_add(session_id: str, role: str, content: str) -> None:
    with _memory_lock:  # ← 同步锁阻塞事件循环
        ...
```

FastAPI 是 async 框架，**事件循环被同步锁阻塞会卡住所有其他请求**。

### 风险场景

- Redis 降级期间，每个 `add_message` / `get_history` 调用都要抢 `_memory_lock`
- 假设一个请求处理时间 10ms（CPU 密集型场景），期间 1000 个并发请求 → 999 个**等待锁**
- **更严重**：高并发降级期间，整个 FastAPI 服务的 P99 延迟可能从 50ms 涨到 5000ms

### 推荐方案

```python
# app/services/session_store.py
import asyncio  # ← 替换 threading

# 进程启动时创建锁（事件循环已就绪）
_memory_lock = asyncio.Lock()


def _memory_add(session_id: str, role: str, content: str) -> None:
    # 同步函数内部不能直接 await，需要改成 async
    raise NotImplementedError("改用 _memory_add_async")


async def _memory_add_async(session_id: str, role: str, content: str) -> None:
    async with _memory_lock:
        if session_id not in _memory_fallback:
            _memory_fallback[session_id] = []
        truncated = content[:500] if len(content) > 500 else content
        _memory_fallback[session_id].append({"role": role, "content": truncated})
        _memory_fallback[session_id] = _memory_fallback[session_id][-10:]
```

### 配套修改

- **5 个测试需要更新**：`test_add_message_always_writes_memory`、`test_long_content_truncated`、`test_max_10_messages`、`test_clear_history_clears_both` 都涉及 `_memory_add` 内部逻辑
- 调用方 `add_message` / `clear_history` 是 async → 改 `await _memory_add_async(...)` 即可
- P0-1 的 LRU 淘汰要写在 `_memory_add_async` 里

### 验证方法

```python
# 单元测试：验证并发安全
async def test_concurrent_memory_writes():
    redis_client_manager._available = False
    tasks = [
        session_store.add_message("s1", "user", f"msg{i}")
        for i in range(100)
    ]
    await asyncio.gather(*tasks)
    assert len(session_store._memory_fallback["s1"]) == 10  # LTRIM 限制
```

---

## P2-5：RFC §3.4 敏感信息理由改写

### 现状

```markdown
**为什么本期不做**：
- 内部 demo 工具，用户都是可信员工
```

**问题**：理由是"用户可信"，但**应用层脱敏 ≠ 用户可信**——LLM 提供商可能记录数据、审计要求员工数据也要保护、离职员工不可控。

### 推荐改写

```markdown
**为什么本期不做**：
- 业务场景是**对内工具**（< 100 员工），相比 to C 体量小
- 真实生产脱敏需要专业 PII 工具（Microsoft Presidio 等），引入额外依赖
- 优先做"零数据丢失"（Redis 持久化），脱敏排二期
- **未来要做的话**：在 add_message 入口处加 mask_sensitive()，而不是在拼 prompt 时

**为什么"内部可信员工"不是好的理由**：
- LLM 提供商（DeepSeek/OpenAI）可能记录输入
- 等保 2.0 / GDPR 对内部数据也有要求
- 信任员工 ≠ 信任应用栈
```

---

## 修订优先级建议

按上线时间倒推：

| 时间 | 必做 | 可选 |
|------|------|------|
| **上线前** | P0-1（内存上限） | — |
| **上线后第 1 周** | P1-4（asyncio.Lock） | P1-2（探活协程） |
| **上线后第 2 周** | P1-3（归档调度器） | — |
| **二期** | P2-5（敏感信息） | — |

---

## 配套文档更新

RFC v0.2 还要补：

1. §1.1 现状补"内存 dict 无上限"为第 6 个生产级问题
2. §1.2 升级动机补"探活 / 调度器 / 锁类型"的提及
3. §1.3 范围明确**本期不接 P2-5 敏感信息**
4. §3.1 降级策略补"内存 LRU 上限"
5. §3.2 数据分层补"调度器选型"
6. §5.2 监控指标补"探活协程状态"指标

---

## 修订历史

| 版本 | 日期 | 变更 | 作者 |
|------|------|------|------|
| v0.2 草案 | 2026-07-07 | 新增 4 个生产深坑 + 修订方案 | 藤子 + grill-me |
