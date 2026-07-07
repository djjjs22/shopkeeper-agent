# Redis 升级 RFC：会话记忆存储层改造

> **文档状态**：草案 v0.1  
> **作者**：藤子  
> **最后更新**：2026-07-07  
> **决策记录类型**：ADR-001 (架构决策记录)

---

## TL;DR（一分钟版）

把当前基于 Python 内存字典的会话记忆存储（`session_store.py`）升级为 Redis 持久化存储。  
**核心收益**：解决 5 个生产级深坑中的 3 个（重启数据丢失、多机不共享、过期数据不清理），其余 2 个（Token 撑爆、敏感信息）作为二期任务。  
**改造范围**：1 个文件重写 + 1 个文件新增 + 3 个配置变更，**L2/L3/Router/前端零改动**。  
**预计工作量**：115 分钟（含 30 分钟单元测试 + 10 分钟降级验证）。  
**目标读者**：未来的我 + 真实团队（用作 ADR 评审）。

---

## 目录

1. [背景与目标](#1-背景与目标)
2. [架构总览](#2-架构总览)
3. [4 个深坑的应对策略](#3-4-个深坑的应对策略)
4. [代码改动清单](#4-代码改动清单)
5. [回滚方案 + 监控指标](#5-回滚方案--监控指标)
6. [附录：完整代码 + 单元测试](#6-附录完整代码--单元测试)

---

## 1. 背景与目标

### 1.1 当前现状

`session_store.py` 当前是 Python 内存字典 + 线程锁的**最简实现**，在快速验证多轮对话概念时发挥了作用，但存在 5 个生产级问题：

**代码层面**：

- 用 `_session_store: dict` 存储会话历史（`session_store.py:5`）
- 用 `threading.Lock` 保护并发写入（`session_store.py:6`）
- 提供 3 个函数式 API：`get_history` / `add_message` / `clear_history`
- **数据存在进程内存中，重启即清空**——前端 cookie 还在但追问失效
- **没有过期机制**——字典只增不减，长期运行内存膨胀
- `add_message` 内的 `truncate` 截断代码因缩进错误**实际是死代码**（`session_store.py:17-19`）
- 用 UUID 作为 session_id，存到浏览器 cookie（`query_router.py:50`）

**部署形态**：

- 单机部署（开发机 Windows + Git Bash + uv 启动）
- Docker Compose 跑 5 个服务（MySQL / Qdrant / Elasticsearch / embedding / FastAPI 后端）
- 没有负载均衡 / K8s / 多副本
- 用户量 < 100

**业务上下文**：

- 会话记忆用于"自然语言问数"系统的多轮对话支持
- 历史被拼到 LLM 的 Prompt 里，解决"那华东呢"这类追问
- LLM 调用成本敏感（每查询调 3-5 次 LLM）
- 用户大多是企业内部人员，不是 to C 业务

**项目代码现状**：

- `session_store` 没有单元测试覆盖
- 没有任何监控 / 告警机制

### 1.2 升级动机

本次升级有 4 类动机，**简历作品导向是首要驱动**：

**业务痛点**：

- **解决开发体验痛点**：服务重启后历史丢失，前端 cookie 还在但追问失效
- **解决内存膨胀**：长时间运行后字典只增不减
- **架构升级**：为未来多机部署做技术储备

**简历作品动机**：

- **面试被问"为什么不用 Redis"**：提前准备好答案
- **展示生产级架构思维**：让面试官知道"我懂生产"
- **体现"开闭原则"应用**：展示"零侵入替换存储层"的能力

**学习动机**：

- 学习 Redis 在 AI 项目里的应用模式
- 理解"接口和实现分离"的实际价值
- 学习"降级策略"等生产级概念
- 理解"冷热数据分层"等架构模式

**风险管理动机**：

- 避免面试被问住：不知道 Redis 在 AI 项目里怎么用很尴尬
- 对标 JD 要求：知乎 AI 数据合成岗要求"熟悉 RAG / 工作流 / 评测"
- 弥补项目技术债：现阶段不做，未来重写代价更大

### 1.3 范围与不在范围

**在本升级范围内**：

1. 把 `session_store` 改为 Redis 后端
2. 引入 Redis Docker 容器
3. 实现降级策略（Redis 挂了回退内存）
4. 实现平滑迁移（双写期）
5. 7+30 天的冷热数据分层（虽然现在数据量小）
6. 单元测试覆盖核心场景

**不在本升级范围内（二期任务）**：

7. Prometheus 指标集成（监控用 README 写方案，不接真监控）
8. 敏感信息过滤（`mask_sensitive` 函数）
9. Token 精确按 `tiktoken` 截断
10. Redis Cluster 分片
11. Redis 哨兵模式 / Sentinel
12. 多用户隔离的限流

### 1.4 关键设计决策

| 决策 | 选择 | 拒绝的备选 | 理由 |
|------|------|-----------|------|
| **缓存选型** | Redis | Memcached | 需要持久化（AOF/RDB）+ 多种数据结构（List/Hash/Stream） |
| **客户端库** | `redis.asyncio` | `redis` 同步版 | 项目用 FastAPI async，避免阻塞事件循环 |
| **接口风格** | 保留函数式 API | 改为类封装 | **零侵入**，调用方（query_service）零改动 |
| **客户端管理** | `RedisClientManager` 单例 | 每次新建连接 | 统一管理连接池，避免连接泄漏 |
| **降级策略** | 弱容忍（Redis 挂了回退内存） | 强一致（直接 500 报错） | 会话系统行业标配：丢历史不丢接口可用 |
| **数据存储结构** | Redis List | Redis Hash / Stream | `RPUSH` 追加 + `LTRIM` 截断，O(1) 复杂度，语义最匹配"消息队列" |
| **序列化方式** | JSON 字符串 | Pickle / MessagePack | JSON 可读 + 跨语言 + 调试方便 |
| **数据生命周期** | Redis TTL 24h + 显式归档 MySQL | 永久放 Redis | 内存成本与查询频率的折中 |
| **配置管理** | YAML 配置文件 | 环境变量 / 代码硬编码 | 与项目其他配置（`conf/app_config.yaml`）保持一致 |
| **持久化** | AOF + RDB 双开 | 只开 AOF / 只开 RDB | AOF 保数据完整性，RDB 加快启动速度 |

---

## 2. 架构总览

### 2.1 改造前架构

```
┌──────────┐
│  前端    │ (React + Vite)
│  Cookie  │ session_id=UUID
└─────┬────┘
      │ HTTP POST /api/query
      ↓
┌─────────────────────────────────────┐
│  FastAPI (uvicorn)                  │
│  ┌─────────────────────────────┐    │
│  │ query_router.py             │    │
│  │  - 读 cookie 拿 session_id  │    │
│  │  - 没 cookie 生成 UUID      │    │
│  └────────┬────────────────────┘    │
│           ↓                          │
│  ┌────────────────────────────────┐ │
│  │ query_service.py               │ │
│  │  - L1 检索 get_history()       │ │
│  │  - L3 拼接 build_prompt()      │ │
│  │  - 调用 LangGraph 工作流       │ │
│  │  - L1 存 add_message()        │ │
│  └────┬──────────────────────┬────┘ │
└───────┼──────────────────────┼──────┘
        │                      │
        ↓                      ↓
┌─────────────────┐  ┌──────────────────┐
│ session_store.py│  │ LangGraph 11 节点│
│ (内存 dict)     │  │  → MySQL 数据仓库 │
│ _session_store: │  └──────────────────┘
│   { uuid: [...] │
│   }             │
│ ⚠️ 进程内存     │
│ ⚠️ 重启即清空   │
└─────────────────┘
```

**改造前关键限制**：

- session 存储和 LangGraph 工作流**进程内耦合**——服务重启全丢
- 多副本部署不可能（每台机器一份独立 dict）
- 没有过期清理
- 跨进程 / 跨机器不共享

### 2.2 改造后架构

```
┌──────────┐
│  前端    │ (无变化)
│  Cookie  │
└─────┬────┘
      │ HTTP POST /api/query
      ↓
┌─────────────────────────────────────┐
│  FastAPI (uvicorn)                  │
│  ┌─────────────────────────────┐    │
│  │ query_router.py   (零改动)  │    │
│  └────────┬────────────────────┘    │
│           ↓                          │
│  ┌────────────────────────────────┐ │
│  │ query_service.py    (零改动)  │ │
│  └────┬──────────────────────┬────┘ │
└───────┼──────────────────────┼──────┘
        │                      │
        ↓                      ↓
┌─────────────────────────┐  ┌──────────────────┐
│ session_store.py        │  │ LangGraph 11 节点│
│ (Redis 客户端)          │  │  (零改动)        │
│                         │  └──────────────────┘
│  get_history()  ───┐    │
│  add_message()  ───┤    │
│  clear_history() ──┤    │
│                    │    │
│   ┌────────────────┘    │
│   ↓                     │
│ ┌──────────────────────┐│
│ │RedisClientManager    ││
│ │(单例, 连接池)        ││
│ └──────┬───────────────┘│
└────────┼────────────────┘
         │
         ↓
┌─────────────────────┐    ┌──────────────────┐
│ Redis 7.2           │    │ MySQL (冷数据)   │
│ ┌─────────────────┐ │    │ session_archive  │
│ │session:{uuid}   │ │    │ (30 天前归档)    │
│ │  [msg1,msg2...] │ │    └──────────────────┘
│ │  TTL 24h        │ │
│ └─────────────────┘ │
│                     │
│ AOF + RDB 双持久化  │
│ maxmemory 256MB     │
│ allkeys-lru 淘汰    │
└─────────────────────┘
         ↑                     ↓
         │                     │
┌────────┴────────────────────┴───────┐
│  降级兜底：Redis 挂了                │
│  → 自动 fallback 到内存 dict         │
│  → 报警 + 日志，不影响接口可用      │
└──────────────────────────────────────┘
```

### 2.3 关键设计：接口和实现分离

**这一设计是本改造的**灵魂**——也是简历作品的核心亮点**。

#### 当前 session_store.py 的 API（已存在）

```python
# 3 个函数，函数式调用
def get_history(session_id: str, max_count: int) -> list
def add_message(session_id: str, role: str, content: str) -> None
def clear_history(session_id: str) -> None
```

#### 改造后**API 完全不变**，只换实现

```python
# 函数签名一字不改，只把 dict 操作换成 Redis 操作
async def get_history(session_id: str, max_count: int = 5) -> list:
    raw = await client.lrange(f"session:{session_id}", -max_count, -1)
    return [json.loads(item) for item in raw]

async def add_message(session_id: str, role: str, content: str) -> None:
    msg = json.dumps({"role": role, "content": content}, ensure_ascii=False)
    await client.rpush(f"session:{session_id}", msg)
    await client.ltrim(f"session:{session_id}", -10, -1)
    await client.expire(f"session:{session_id}", 86400)

async def clear_history(session_id: str) -> None:
    await client.delete(f"session:{session_id}")
```

**改的是实现，不是接口**。**`query_service.py` 一行代码都不动**。

#### 体现的工程原则

| 原则 | 体现 |
|------|------|
| **开闭原则** | 对扩展开放（加 Redis 实现）、对修改关闭（不动 query_service） |
| **依赖倒置** | 业务层依赖"接口语义"，不依赖"具体存储" |
| **里氏替换** | 新实现完全替代旧实现，调用方无感知 |
| **接口隔离** | 3 个函数各自单一职责，没有冗余 API |

**面试讲法**：

> "我用 Python 字典 + 线程锁实现了会话记忆模块。**在设计之初就考虑了存储层可替换**——把 3 个函数（get/add/clear）作为稳定接口，把 dict 操作作为可变实现。后续要升级 Redis，只需在 session_store.py 内部重写，**L2/L3/Router/前端零改动**——这是典型的'开闭原则'应用。"

---

## 3. 5 个深坑中的 3 个应对策略（另 2 个留二期）

> 本节是 ADR 的**核心**——重点讲**本期解决的 3 个坑**。每个坑给出：场景 → 风险 → 应对方案 → 拒绝的备选 → 代价评估
> 剩余 2 个坑（Token 撑爆、敏感信息泄露）在 §3.4 简述为什么不本期做。

### 3.1 降级策略：Redis 挂了怎么办

#### 场景

- Redis 集群重启（运维 / 故障转移）
- Redis OOM（内存满了自动淘汰）
- 网络抖动导致 Redis 临时不可达
- 应用启动时 Redis 还没起来

#### 风险

如果每次 Redis 调用都直接 raise，会导致：

- 用户追问"那华东呢"突然 500 报错
- 整个问数系统对 Redis 形成强依赖
- 单点故障 = 全站不可用

#### 应对方案：双层降级（核心代码约 40 行）

```python
# app/clients/redis_client_manager.py
class RedisClientManager:
    def __init__(self):
        self._client: Optional[redis_async.Redis] = None
        self._available: bool = False
    
    async def get_client(self) -> Optional[redis_async.Redis]:
        """带健康检查的客户端获取"""
        if not self._available:
            return None  # 降级标志
        return self._client
```

```python
# app/services/session_store.py（重写后）
import asyncio

async def get_history(session_id: str, max_count: int = 5) -> list:
    """降级版获取历史"""
    client = await redis_client_manager.get_client()
    
    if client is None:
        # 降级到内存 dict
        return _memory_fallback.get(session_id, [])[-max_count:]
    
    try:
        raw = await client.lrange(f"session:{session_id}", -max_count, -1)
        return [json.loads(item) for item in raw]
    except (redis_async.ConnectionError, asyncio.TimeoutError) as e:
        # 异常时切换到降级模式
        redis_client_manager.mark_unavailable()
        logger.warning(f"Redis 不可用，降级到内存: {e}")
        return _memory_fallback.get(session_id, [])[-max_count:]
```

#### 拒绝的备选

| 备选方案 | 为什么拒绝 |
|---------|----------|
| **A. 直接 500 报错** | 用户体验差，故障传播范围大 |
| **B. 强一致重试** | Redis 挂时永远重试不成功，反而拖垮其他服务 |
| **C. 熔断器模式**（hystrix） | 对会话场景过度工程化，引入额外依赖 |
| **D. 多 Redis 实例 + 选举** | 单机 < 100 用户过度 |

#### 代价评估

| 维度 | 代价 |
|------|------|
| 实现复杂度 | 低（40 行代码） |
| 性能损失 | 降级期间有 ~10ms 内存 dict 查找延迟，可忽略 |
| 数据一致性 | 降级期间写内存 dict，**不写 Redis**——恢复后丢失 |
| 可观测性 | 需加 Prometheus 指标（监控用 README 写方案） |

---

### 3.2 数据分层：7 天热 + 30 天冷

#### 场景

- 用户问"上周聊的华东那个分析还在吗"——30 天前的历史怎么找？
- Redis 内存有限，不能无限堆积
- 但完全删除又影响长期用户

#### 风险

如果不做分层：

- 内存只增不减（虽然有 TTL，但仅 24h）
- 30 天后用户想查历史——没了
- 1 万用户 × 30 天 = 内存爆炸

#### 应对方案：分层存储 + 异步归档

```
┌──────────────────┐  写入   ┌────────────────┐
│  用户问答        │ ──────→ │  Redis (7 天)  │
│                  │         │  TTL 24h 自动转冷
└──────────────────┘         └───────┬────────┘
                                     │ 每天 02:00 异步归档
                                     ↓
                             ┌──────────────────┐
                             │  MySQL (30 天)  │
                             │  session_archive │
                             └──────────────────┘
```

**为什么不直接用 Redis 存 30 天？**

| 方案 | 内存占用 | 查询延迟 | 成本 |
|------|---------|---------|------|
| **Redis 30 天** | 1 万用户 × 30 天 × 2KB = 600MB | 1ms | 高 |
| **Redis 7 天 + MySQL 23 天** | 140MB | Redis 1ms / MySQL 50ms | 低 |

#### 核心代码：归档任务（约 30 行）

```python
# app/scripts/archive_sessions.py
import asyncio
from datetime import datetime, timedelta
from app.clients.mysql_client_manager import mysql_client_manager

async def archive_old_sessions():
    """每天 02:00 把 7 天前的 session 从 Redis 迁移到 MySQL"""
    
    # 1. 找出所有 7 天前的 session_id（通过 SCAN）
    redis = await redis_client_manager.get_client()
    cutoff = datetime.now() - timedelta(days=7)
    
    archived = 0
    async for key in redis.scan_iter(match="session:*"):
        # 2. 读出历史
        raw = await redis.lrange(key, 0, -1)
        if not raw:
            continue
        
        # 3. 写入 MySQL
        session_id = key.decode().split(":", 1)[1]
        await mysql_client_manager.execute(
            "INSERT INTO session_archive (session_id, messages, archived_at) VALUES (%s, %s, %s)",
            (session_id, json.dumps([json.loads(m) for m in raw]), datetime.now())
        )
        archived += 1
    
    logger.info(f"[归档任务] 共归档 {archived} 个 session")

# 用 APScheduler 每天 02:00 触发
# scheduler.add_job(archive_old_sessions, 'cron', hour=2)
```

#### 拒绝的备选

| 备选方案 | 为什么拒绝 |
|---------|----------|
| **A. 全部放 Redis** | < 100 用户没必要，但未来扩展不友好 |
| **B. 全部放 MySQL** | 查询慢（毫秒级 vs 微秒级），影响追问延迟 |
| **C. 放 S3 / OSS 对象存储** | 冷数据归档合适，但项目无 AWS 依赖 |
| **D. 不分层，按需查询** | 用户体验不可预期 |

#### 代价评估

| 维度 | 代价 |
|------|------|
| 实现复杂度 | 中（30 行归档 + 1 张 MySQL 表） |
| 性能损失 | 7 天前查询 ~50ms 延迟（MySQL 索引查询） |
| 数据一致性 | 异步归档有 1 天窗口，但可接受 |
| 业务可接受度 | 高——30 天已覆盖 95% 业务场景 |

---

### 3.3 平滑迁移：双写期过渡

#### 场景

- 当前系统正在用内存 dict 服务
- 切到 Redis 必须保证用户无感知
- 切换瞬间**老用户的 session_id 还在 cookie 里**

#### 风险

如果硬切换：

- 老用户 cookie 里的 session_id 在 Redis 里查不到
- 用户体验"突然失忆"
- 切换失败没有回滚路径

#### 应对方案：3 阶段迁移

```
阶段 1：双写期（1-3 天）
┌─────────────┐
│ 内存 dict   │ ← 同时写
│ (兜底)      │
└─────────────┘
       ↕
┌─────────────┐
│  Redis      │ ← 同时写
│  (主)       │
└─────────────┘
读取：优先读 Redis，Redis 异常时降级读内存

阶段 2：单写期（7-30 天）
Redis 为主，内存仅作降级兜底（写失败时才用）
不再有"双写"开销，但保留降级能力

阶段 3：清理期
物理上删除 _memory_fallback 相关代码，彻底依赖 Redis
（这一步是 PR 级工作量，独立提交）
```

#### 核心代码：带开关的双写

```python
# app/services/session_store.py
import os
from enum import Enum

class WriteMode(Enum):
    MEMORY_ONLY = "memory_only"        # 阶段 0：纯内存（改造前）
    DUAL_WRITE = "dual_write"           # 阶段 1：双写（内存 + Redis）
    REDIS_PRIMARY = "redis_primary"     # 阶段 2：Redis 为主，内存仅作降级兜底
    # 注：阶段 3（代码移除）= 物理上删掉 _memory_fallback 相关代码

# 通过环境变量切换阶段
WRITE_MODE = WriteMode(os.getenv("SESSION_WRITE_MODE", "memory_only"))

async def add_message(session_id: str, role: str, content: str) -> None:
    """根据阶段决定写哪里"""
    
    if WRITE_MODE == WriteMode.MEMORY_ONLY:
        # 阶段 0：只写内存（改造前）
        _memory_add(session_id, role, content)
    
    elif WRITE_MODE == WriteMode.DUAL_WRITE:
        # 阶段 1：双写（先内存后 Redis，Redis 失败不影响内存）
        _memory_add(session_id, role, content)
        client = await redis_client_manager.get_client()
        if client:
            try:
                await _redis_add(client, session_id, role, content)
            except Exception as e:
                logger.warning(f"[Redis] 写入失败（双写期）: {e}")
    
    elif WRITE_MODE == WriteMode.REDIS_PRIMARY:
        # 阶段 2：Redis 为主，但写失败时降级到内存
        client = await redis_client_manager.get_client()
        if client:
            try:
                await _redis_add(client, session_id, role, content)
                _memory_add(session_id, role, content)  # 内存仅作降级兜底
            except Exception as e:
                logger.warning(f"[Redis] 写入失败，降级到内存: {e}")
                _memory_add(session_id, role, content)
```

#### 拒绝的备选

| 备选方案 | 为什么拒绝 |
|---------|----------|
| **A. 一次性硬切换** | 失败无法回滚，影响范围大 |
| **B. 蓝绿部署 + DB 同步** | 单机 < 100 用户不需要蓝绿 |
| **C. 流量灰度** | 单机部署没法做流量灰度 |
| **D. 凌晨切换 + 回滚预案** | 比双写期简单，但无法"运行一段时间验证" |

#### 代价评估

| 维度 | 代价 |
|------|------|
| 实现复杂度 | 中（双写逻辑 + 模式开关 + 阶段切换文档） |
| 性能损失 | 阶段 1 期间写延迟 +2ms（双写） |
| 数据一致性 | 切换瞬间可能丢失 1-2 条消息（可接受） |
| 回滚难度 | 低——改环境变量即可 |

---

### 3.4 不解决的 2 个坑（二期任务）

明确**不做什么**和**为什么不做**——这本身就是设计决策。

#### 坑 4：Token 撑爆

**场景**：用户发了 1 万字的产品反馈 → 历史里 1 条消息 1 万字 → Prompt 撑爆

**为什么本期不做**：

- 当前 `add_message` 有 `content[:500]` 截断代码（虽然有 bug）
- 修复 bug 即可，**不用 Redis**
- 精确按 `tiktoken` 截断需要引入额外依赖，影响范围大

**二期方案**：

```python
import tiktoken
encoder = tiktoken.encoding_for_model("gpt-4")

def truncate_by_tokens(content: str, max_tokens: int = 500) -> str:
    tokens = encoder.encode(content)
    if len(tokens) > max_tokens:
        return encoder.decode(tokens[:max_tokens])
    return content
```

#### 坑 5：敏感信息泄露

**场景**：用户问"我的身份证 110..." → 存进历史 → 喂给 LLM

**为什么本期不做**：

- 内部 demo 工具，用户都是可信员工
- 正则过滤对身份证/手机号有效，但银行卡/邮箱格式众多
- 专业 PII 检测（Presidio）引入额外依赖

**二期方案**：

```python
import re

def mask_sensitive(content: str) -> str:
    content = re.sub(r'\d{17}[\dXx]', '***身份证***', content)
    content = re.sub(r'1\d{10}', '***手机号***', content)
    content = re.sub(r'\d{16,19}', '***银行卡***', content)
    return content
```

---

## 4. 代码改动清单

### 4.1 文件改动总览

| # | 文件 | 改动类型 | 行数变化 | 风险 |
|---|------|---------|---------|------|
| 1 | `app/clients/redis_client_manager.py` | **新增** | +45 | 低（独立组件） |
| 2 | `app/services/session_store.py` | **重写** | 25 → 60 | **中**（核心改动） |
| 3 | `app/api/dependencies.py` | 改 1 处 | +5 | 低（lifespan） |
| 4 | `conf/app_config.yaml` | 新增 redis 配置 | +8 | 无 |
| 5 | `docker/docker-compose.yaml` | 新增 redis 服务 | +12 | 低（容器） |
| 6 | `pyproject.toml` | 新增 `redis[hiredis]` 依赖 | +1 | 无 |
| 7 | `.env.example` | 新增 `REDIS_URL` | +1 | 无 |
| 8 | `tests/test_session_store.py` | **新增** | +120 | 无 |

**L2/L3/Router/前端/QueryService 全部零改动**——这是接口和实现分离设计的核心收益。

### 4.2 核心代码：redis_client_manager.py

```python
# app/clients/redis_client_manager.py
"""Redis 客户端管理器 - 统一管理连接池 + 健康检查"""

import redis.asyncio as redis_async
from typing import Optional
from app.conf.app_config import settings
from app.core.log import logger


class RedisClientManager:
    """Redis 客户端单例 + 降级标志"""
    
    def __init__(self):
        self._client: Optional[redis_async.Redis] = None
        self._available: bool = False
        self._fail_count: int = 0
    
    async def connect(self) -> None:
        """应用启动时建立连接"""
        try:
            self._client = redis_async.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2.0,  # 2 秒连接超时
                socket_timeout=2.0,           # 2 秒读超时
                max_connections=20,            # 连接池大小
            )
            await self._client.ping()
            self._available = True
            logger.info("[Redis] 连接成功")
        except Exception as e:
            logger.warning(f"[Redis] 启动时连接失败: {e}，将以降级模式运行")
            self._available = False
    
    async def disconnect(self) -> None:
        """应用关闭时释放连接"""
        if self._client:
            await self._client.close()
    
    async def get_client(self) -> Optional[redis_async.Redis]:
        """获取客户端（带可用性检查）"""
        if not self._available or self._client is None:
            return None
        return self._client
    
    def mark_unavailable(self) -> None:
        """标记 Redis 不可用（供降级策略调用）"""
        self._fail_count += 1
        if self._fail_count >= 3:  # 连续 3 次失败才标记
            self._available = False
            logger.warning(f"[Redis] 连续失败 {self._fail_count} 次，标记为不可用")
    
    def mark_available(self) -> None:
        """标记 Redis 恢复（健康检查成功时调用）"""
        if not self._available:
            logger.info("[Redis] 恢复可用")
        self._available = True
        self._fail_count = 0


# 全局单例
redis_client_manager = RedisClientManager()
```

### 4.3 核心代码：session_store.py（重写）

```python
# app/services/session_store.py
"""Redis 版会话存储 - L1 层（带内存降级兜底）"""

import asyncio
import json
import re
from typing import List, Dict, Optional

from app.clients.redis_client_manager import redis_client_manager
from app.core.log import logger


# ════════════════════════════════════════════════════
# 降级兜底：内存 dict
# 当 Redis 不可用时，所有读写都退化到这里
# ════════════════════════════════════════════════════
import threading
_memory_fallback: Dict[str, List[Dict]] = {}
_memory_lock = threading.Lock()


def _memory_add(session_id: str, role: str, content: str) -> None:
    """内存版添加消息"""
    with _memory_lock:
        if session_id not in _memory_fallback:
            _memory_fallback[session_id] = []
        # 单条消息截断到 500 字符（避免内存爆炸）
        truncated = content[:500] if len(content) > 500 else content
        _memory_fallback[session_id].append({"role": role, "content": truncated})
        # 只保留最近 10 条
        _memory_fallback[session_id] = _memory_fallback[session_id][-10:]


def _memory_get(session_id: str, max_count: int) -> list:
    """内存版获取历史"""
    history = _memory_fallback.get(session_id, [])
    return history[-max_count:]


# ════════════════════════════════════════════════════
# Redis 主路径
# ════════════════════════════════════════════════════
def _key(session_id: str) -> str:
    """生成 Redis key"""
    return f"session:{session_id}"


async def _redis_add(client, session_id: str, role: str, content: str) -> None:
    """Redis 版添加消息"""
    truncated = content[:500] if len(content) > 500 else content
    msg = json.dumps({"role": role, "content": truncated}, ensure_ascii=False)
    key = _key(session_id)
    
    pipe = client.pipeline()
    pipe.rpush(key, msg)
    pipe.ltrim(key, -10, -1)
    pipe.expire(key, 86400)  # 24 小时 TTL
    await pipe.execute()


async def get_history(session_id: str, max_count: int = 5) -> list:
    """获取历史（自动降级）"""
    client = await redis_client_manager.get_client()
    
    if client is None:
        return _memory_get(session_id, max_count)
    
    try:
        raw = await client.lrange(_key(session_id), -max_count, -1)
        redis_client_manager.mark_available()
        return [json.loads(item) for item in raw]
    except (asyncio.TimeoutError, Exception) as e:
        logger.warning(f"[Redis] 读取失败，降级到内存: {e}")
        redis_client_manager.mark_unavailable()
        return _memory_get(session_id, max_count)


async def add_message(session_id: str, role: str, content: str) -> None:
    """添加消息（自动降级）"""
    # 永远先写内存（保证降级期间不丢）
    _memory_add(session_id, role, content)
    
    # 尝试写 Redis
    client = await redis_client_manager.get_client()
    if client is not None:
        try:
            await _redis_add(client, session_id, role, content)
            redis_client_manager.mark_available()
        except Exception as e:
            logger.warning(f"[Redis] 写入失败: {e}")
            redis_client_manager.mark_unavailable()


async def clear_history(session_id: str) -> None:
    """清空历史"""
    with _memory_lock:
        _memory_fallback.pop(session_id, None)
    
    client = await redis_client_manager.get_client()
    if client:
        try:
            await client.delete(_key(session_id))
        except Exception:
            pass
```

### 4.4 配置变更

**`conf/app_config.yaml`** 新增：

```yaml
redis:
  url: "redis://127.0.0.1:6379/0"
  max_connections: 20
  socket_timeout: 2.0
  default_ttl_seconds: 86400
  key_prefix: "session:"
```

**`docker/docker-compose.yaml`** 新增服务：

```yaml
services:
  redis:
    image: redis:7-alpine
    container_name: shopkeeper-redis
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
    restart: unless-stopped

volumes:
  redis_data:
```

**`pyproject.toml`** 新增依赖：

```toml
[project]
dependencies = [
    # ... 现有依赖
    "redis[hiredis]>=5.0.0",  # 新增
]
```

**`.env.example`** 新增：

```bash
# Redis 配置
REDIS_URL=redis://127.0.0.1:6379/0
SESSION_WRITE_MODE=memory_only  # memory_only → dual_write → redis_only
```

**`app/api/dependencies.py`** 加 1 行：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 已有代码...
    await redis_client_manager.connect()   # ← 加这行
    yield
    # 已有代码...
    await redis_client_manager.disconnect()  # ← 加这行
```

---

## 5. 回滚方案 + 监控指标

### 5.1 回滚方案

#### 触发条件

满足以下任一条件应触发回滚：

- [ ] 升级后 P99 延迟 > 500ms（基线 200ms）
- [ ] 错误率 > 1%
- [ ] Redis 连接失败率 > 5%
- [ ] 数据丢失（Redis 切换后追问失效）
- [ ] Docker 容器无法启动

#### 回滚步骤（5 分钟内可完成）

```bash
# 步骤 1：修改环境变量，切换回纯内存模式
export SESSION_WRITE_MODE=memory_only

# 步骤 2：重启 FastAPI 服务
docker compose restart backend
# 或
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

# 步骤 3：验证服务恢复
curl http://127.0.0.1:8000/api/health
# 应返回 {"status": "ok"}
```

**回滚后效果**：

- 立即恢复到改造前状态（内存 dict）
- Redis 容器可保留（不删除），方便后续再切
- 数据一致性：切换期间的 Redis 数据**不会自动同步回内存**（这是已知的代价）

#### 备选回滚路径

如果环境变量切换无效（代码层 bug），还有**代码级回滚**：

```bash
# 1. 切换到上一版本代码
git checkout HEAD~1 -- app/services/session_store.py
git checkout HEAD~1 -- app/clients/redis_client_manager.py

# 2. 重启服务
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 5.2 监控指标（伪代码）

**本期不接 Prometheus，但记录指标采集方案**——留给二期实现。

```python
# app/core/metrics.py（伪代码）
from prometheus_client import Counter, Histogram, Gauge

# 1. Redis 操作计数器
redis_ops_total = Counter(
    "session_redis_ops_total",
    "Redis 操作总数",
    ["operation", "status"]  # operation: get/add/clear, status: success/fail
)

# 2. Redis 操作延迟直方图
redis_ops_duration = Histogram(
    "session_redis_ops_duration_seconds",
    "Redis 操作延迟",
    ["operation"]
)

# 3. 降级模式持续时间
fallback_active_seconds = Gauge(
    "session_fallback_active_seconds",
    "降级模式累计持续时间"
)

# 4. 会话总数
active_sessions = Gauge(
    "session_active_total",
    "当前活跃 session 数"
)


# 5. 关键告警规则（Alertmanager 配置示例）
# - alert: RedisHighFailureRate
#   expr: rate(session_redis_ops_total{status="fail"}[5m]) > 0.05
#   for: 2m
#   labels: { severity: warning }
#   annotations: "Redis 失败率超过 5%"
#
# - alert: FallbackSustained
#   expr: session_fallback_active_seconds > 300
#   for: 1m
#   labels: { severity: critical }
#   annotations: "降级模式持续超过 5 分钟，需要人工介入"
```

### 5.3 验收标准

**改造完成的标志**——通过下面 6 项检查才能算"完成"：

| # | 验收项 | 通过标准 | 测试方法 |
|---|--------|---------|---------|
| 1 | 容器正常启动 | `docker compose ps` 看到 redis healthy | `curl http://127.0.0.1:6379` |
| 2 | 接口正常返回 | POST /api/query 返回 SSE 流 | Swagger UI 测试 |
| 3 | 单元测试通过 | `pytest tests/test_session_store.py` 100% 通过 | 跑测试 |
| 4 | 降级场景验证 | kill Redis 容器后接口仍能用 | `docker stop redis` 后测试 |
| 5 | 数据可恢复 | 重启 Redis 后追问仍生效 | kill + restart 验证 |
| 6 | 监控指标可达 | `/metrics` 端点暴露 session_redis_ops_total | 接入 Prometheus 后验证 |

**第 4 项（降级验证）最重要**——它是本期"生产级改造"的核心证明。

---

## 6. 附录：完整代码 + 单元测试

### 6.1 session_store.py 完整代码

参见 § 4.3，约 80 行。

### 6.2 redis_client_manager.py 完整代码

参见 § 4.2，约 45 行。

### 6.3 单元测试用例

```python
# tests/test_session_store.py
"""会话存储单元测试 - 覆盖正常场景 + 降级场景 + 边界场景"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import session_store
from app.clients.redis_client_manager import redis_client_manager


@pytest.fixture(autouse=True)
def reset_state():
    """每个测试前重置内存 dict"""
    session_store._memory_fallback.clear()
    redis_client_manager._available = True
    redis_client_manager._fail_count = 0
    yield
    session_store._memory_fallback.clear()


# ══════════════════════════════════════
# 正常场景：Redis 可用
# ══════════════════════════════════════
async def test_add_and_get_history_redis():
    """Redis 可用时正常存取"""
    mock_client = AsyncMock()
    mock_client.lrange.return_value = ['{"role":"user","content":"hi"}']
    redis_client_manager._client = mock_client
    
    history = await session_store.get_history("s1", max_count=5)
    assert len(history) == 1
    assert history[0]["role"] == "user"
    mock_client.lrange.assert_called_once()


async def test_add_message_calls_redis():
    """添加消息时调用 Redis pipeline"""
    mock_client = AsyncMock()
    mock_client.pipeline.return_value = mock_client
    redis_client_manager._client = mock_client
    
    await session_store.add_message("s1", "user", "你好")
    
    # 验证 pipeline 执行了 rpush + ltrim + expire
    assert mock_client.rpush.called
    assert mock_client.ltrim.called
    assert mock_client.expire.called


# ══════════════════════════════════════
# 降级场景：Redis 不可用
# ══════════════════════════════════════
async def test_get_history_fallback_when_redis_down():
    """Redis 不可用时降级到内存"""
    redis_client_manager._available = False
    redis_client_manager._client = None
    
    # 内存 dict 里先存数据
    session_store._memory_fallback["s1"] = [
        {"role": "user", "content": "test"}
    ]
    
    history = await session_store.get_history("s1", max_count=5)
    assert len(history) == 1


async def test_get_history_fallback_on_redis_error():
    """Redis 异常时降级到内存"""
    mock_client = AsyncMock()
    mock_client.lrange.side_effect = Exception("Connection refused")
    redis_client_manager._client = mock_client
    redis_client_manager._available = True
    
    history = await session_store.get_history("s1", max_count=5)
    # 异常时不报错，返回空列表
    assert history == []


async def test_add_message_always_writes_memory():
    """添加消息永远写内存（降级兜底）"""
    redis_client_manager._available = False
    redis_client_manager._client = None
    
    await session_store.add_message("s1", "user", "降级测试")
    
    assert "s1" in session_store._memory_fallback
    assert session_store._memory_fallback["s1"][0]["content"] == "降级测试"


# ══════════════════════════════════════
# 边界场景
# ══════════════════════════════════════
async def test_long_content_truncated():
    """超长内容被截断到 500 字符"""
    redis_client_manager._available = False
    redis_client_manager._client = None
    
    long_content = "a" * 1000
    await session_store.add_message("s1", "user", long_content)
    
    stored = session_store._memory_fallback["s1"][0]["content"]
    assert len(stored) == 500


async def test_max_10_messages():
    """单 session 最多保留 10 条消息"""
    redis_client_manager._available = False
    redis_client_manager._client = None
    
    for i in range(20):
        await session_store.add_message("s1", "user", f"msg{i}")
    
    assert len(session_store._memory_fallback["s1"]) == 10


async def test_clear_history_clears_both():
    """clear_history 同时清空 Redis 和内存"""
    mock_client = AsyncMock()
    redis_client_manager._client = mock_client
    session_store._memory_fallback["s1"] = [{"role": "user", "content": "x"}]
    
    await session_store.clear_history("s1")
    
    assert "s1" not in session_store._memory_fallback
    mock_client.delete.assert_called_once()


async def test_empty_session_returns_empty_list():
    """查询不存在的 session 返回空列表"""
    mock_client = AsyncMock()
    mock_client.lrange.return_value = []
    redis_client_manager._client = mock_client
    
    history = await session_store.get_history("nonexistent")
    assert history == []


# ══════════════════════════════════════
# 集成测试：完整流程
# ══════════════════════════════════════
async def test_round_trip_redis_mode():
    """Redis 模式下的完整读写循环"""
    mock_client = AsyncMock()
    
    # 模拟 lrange 返回刚刚 rpush 的数据
    written_data = []
    async def mock_rpush(key, value):
        written_data.append(value)
        return len(written_data)
    
    async def mock_lrange(key, start, end):
        return written_data[-5:]  # 模拟返回最近 5 条
    
    mock_client.rpush = mock_rpush
    mock_client.lrange = mock_lrange
    mock_client.pipeline.return_value = mock_client
    mock_client.ltrim = AsyncMock()
    mock_client.expire = AsyncMock()
    redis_client_manager._client = mock_client
    
    # 写入 3 条
    await session_store.add_message("s1", "user", "msg1")
    await session_store.add_message("s1", "assistant", "reply1")
    await session_store.add_message("s1", "user", "msg2")
    
    # 读出
    history = await session_store.get_history("s1", max_count=5)
    assert len(history) == 3
    assert history[0]["content"] == "msg1"
```

### 6.4 docker-compose.yaml 变更

参见 § 4.4，约 12 行新增。

### 6.5 实施步骤清单（按时间顺序）

| 步骤 | 任务 | 预计时间 | 风险 |
|------|------|---------|------|
| 1 | 新增 `redis_client_manager.py` | 10 分钟 | 低 |
| 2 | 在 `dependencies.py` 加 lifespan | 5 分钟 | 低 |
| 3 | 新增 `docker-compose.yaml` redis 服务 | 5 分钟 | 低 |
| 4 | 启动 Redis 容器并验证连接 | 5 分钟 | 低 |
| 5 | 写 `test_session_store.py`（先写测试） | 30 分钟 | 中 |
| 6 | 重写 `session_store.py`（让测试通过） | 30 分钟 | **中** |
| 7 | 启动后端 + Swagger UI 验证 | 10 分钟 | 低 |
| 8 | 跑测试（`pytest`） | 5 分钟 | 低 |
| 9 | **降级场景验证**（kill Redis 看接口） | 10 分钟 | 低 |
| 10 | Git commit + push | 5 分钟 | 低 |
| **总计** | | **115 分钟** | |

**第 5 步"先写测试"是关键**——TDD 思路，避免改完代码不知道改对没。

### 6.6 上传 GitHub 前检查清单

- [ ] 删除 `.env` 中的真实连接信息
- [ ] `.env.example` 留空 `REDIS_URL=`
- [ ] 不上传 `docker_data/` 目录
- [ ] README 顶部加"运行前需启动 Redis"提示
- [ ] 检查 `git log` 描述符合 Conventional Commits 规范

---

## 修订历史

| 版本 | 日期 | 变更 | 作者 |
|------|------|------|------|
| v0.1 | 2026-07-07 | 完整初稿（6 节） | 藤子 |
