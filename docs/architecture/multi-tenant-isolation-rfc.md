# RFC: 多用户隔离 / 鉴权设计

| 字段 | 值 |
|---|---|
| 状态 | Draft |
| 作者 | Lucy (WorkBuddy) |
| 创建日期 | 2026-09-16 |
| 关联 RFC | `ai-application-pain-points-rfc.md` (7-20 安全加固) |

---

## Changelog

- **v0.1（2026-09-16）** — 初稿起草
  - 三轮审查（Clarity / Structure / Anti-patterns）已完成
  - 章节顺序调整：取舍记录（5 → 3），紧跟设计方案
  - 术语统一：`owner_id` / `user_id` / `org_id` 各表意清楚
  - 关键设计已定：行级 `owner_id` + JWT/API Key 双轨 + ContextVar 身份传递 + 兼容 7-20 的 `QUERY_API_KEY`
  - 待办：开放问题 4 项（详见 §5）

---

## 1. 背景与目标

### 1.1 现状

| 模块 | 现状 |
|---|---|
| `query_router.py` | 有 `_check_query_api_key(QUERY_API_KEY)` 单一全局 API Key（7-20 加）|
| `admin_router.py` | 有 `X-Admin-Token` Header 鉴权（7-17 加）|
| `session_id` | HMAC 签名 + cookie（防伪造）|
| 事实表 `fact_order` | **无 user_id / owner_id 字段** |
| `dim_customer` | 无 owner 字段 |
| 数据访问层 | 所有 SQL 直连 dw 库，**无过滤层** |

### 1.2 目标

- **目标 1（必须）**：单租户 → 多租户，每个用户只能看自己范围内的数据
- **目标 2（必须）**：可注册 / 登录 / 颁发凭证（JWT + API Key 两种）
- **目标 3（必须）**：不影响已有 query 链路（最小破坏）
- **目标 4（可选）**：速率限制、防滥用
- **目标 5（可选）**：审计日志（谁在什么时候查了什么）

### 1.3 非目标

- 不做 OAuth 2.0 / OIDC / SSO 集成（1 期不做）
- 不做细粒度 RBAC（按角色权限矩阵），只做"按用户范围过滤"
- 不改前端 UI（本期后端先行，前端等接口稳定再做）

---

## 2. 设计方案

### 2.1 鉴权方式：JWT + API Key 双轨

| 凭证类型 | 适用场景 | 生命周期 | 颁发方式 |
|---|---|---|---|
| **JWT** | 用户从 Web 登录后 | 短期（access 2h / refresh 7d）| `/api/auth/login` |
| **API Key** | 服务端调用、CI、SDK 集成 | 长期（可设过期）| `/api/auth/keys` 创建 |

**两者关系**：API Key 跟 JWT 是**两种独立凭证**，不互相转换。API Key 由服务端直存（DB 哈希），JWT 由客户端无状态验证。

### 2.2 隔离粒度：行级 + 显式 `owner_id`

#### Schema 改动

```sql
-- 新增字段（不删字段，向后兼容）
ALTER TABLE fact_order
  ADD COLUMN owner_id VARCHAR(64) NOT NULL DEFAULT 'org_default' AFTER order_id,
  ADD INDEX idx_owner_date (owner_id, date_id);

ALTER TABLE dim_customer
  ADD COLUMN owner_id VARCHAR(64) NOT NULL DEFAULT 'org_default' AFTER customer_id;
```

**回填策略**：
- 历史 7.2 万单的 `owner_id` 全部置为 `org_default`（公共示例数据）
- 新增用户自带 org，归属自己的数据
- 公共指标（如"全平台 GMV"）走 `org_default`

**隔离 SQL 改造**：
```python
# 改前
sql = "SELECT region_name, SUM(order_amount) FROM fact_order GROUP BY region_name"

# 改后（自动注入 owner_id 过滤）
sql = "SELECT region_name, SUM(order_amount) FROM fact_order WHERE owner_id = ? GROUP BY region_name"
```

**注入点**：`sql_safety.py` 之前新增 `RowLevelFilter` 中间件（基于 SQL 解析 + owner 列表）。

#### 为什么不改 schema 行不通

- 应用层"中间件"防漏：所有手写 SQL 都要走中间件，新增节点漏一次就**全部数据泄露**
- schema 改造一次性，**不依赖代码**——安全边界靠数据库兜底
- 面试官问"如何保证不漏过滤"：答"DB 层有 NOT NULL + 默认值，应用层出错也只看到自己的"，比"应用层过滤"硬

### 2.3 身份传递：FastAPI Depends + RuntimeContext

#### 三层注入链路

```
HTTP Request
  ↓
FastAPI Depends(get_current_user)  → 从 JWT/API Key 解析 user_id
  ↓
Request.state.user_id             → Request 作用域
  ↓
ContextVar (app_ctx.user_id)      → async-safe 全局
  ↓
DataAgentContext.user_id          → LangGraph RuntimeContext
  ↓
SQL 生成节点                       → 拼 WHERE owner_id = ?
```

**关键点**：
- `ContextVar` 替代 thread-local——LangGraph 跨节点 / 异步安全
- `RuntimeContext` 已存在（`DataAgentContext`），只需**加一个字段**
- **不污染 `DataAgentState`**——state 继续管"业务数据"，`Context` 管"运行时身份"

### 2.4 用户表设计

#### MySQL `meta` 库新增表

```sql
-- 用户
CREATE TABLE users (
  user_id VARCHAR(64) PRIMARY KEY,
  username VARCHAR(64) UNIQUE NOT NULL,
  email VARCHAR(128),
  password_hash VARCHAR(255) NOT NULL,  -- bcrypt
  org_id VARCHAR(64) NOT NULL,           -- 多用户可同 org
  status ENUM('active','suspended','deleted') DEFAULT 'active',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_org (org_id)
);

-- API Key（明文存 key_prefix，hash 存完整 key）
CREATE TABLE api_keys (
  key_id VARCHAR(64) PRIMARY KEY,
  user_id VARCHAR(64) NOT NULL,
  key_prefix VARCHAR(16) NOT NULL,        -- sk-cp-abc... 前 12 字符，用于识别
  key_hash VARCHAR(255) NOT NULL,         -- 完整 key 的 sha256
  name VARCHAR(64),                       -- 用户给 key 起名
  scopes JSON,                            -- ['query:read', 'admin:read']
  expires_at DATETIME,
  last_used_at DATETIME,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_user (user_id),
  INDEX idx_hash (key_hash)
);

-- Refresh Token（可选）
CREATE TABLE refresh_tokens (
  token_id VARCHAR(64) PRIMARY KEY,
  user_id VARCHAR(64) NOT NULL,
  token_hash VARCHAR(255) NOT NULL,
  expires_at DATETIME NOT NULL,
  revoked_at DATETIME,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_user (user_id)
);
```

**密码 / Key 安全**：
- 密码 `bcrypt` 哈希
- API Key 客户端**只看一次**（创建时返回完整 key），DB 只存 `sha256(key)`
- `key_prefix` 用于 UI 展示（"sk-cp-abc..." 让人识别）
- Key 撤销 = `revoked_at` 标记，**不删**（保留审计）

### 2.5 与现有 `_check_query_api_key` 的关系

**保留兼容路径，但改成"伪 user_id"**：

```python
# 改前（query_router.py:27）
def _check_query_api_key(authorization):
    if token != os.environ["QUERY_API_KEY"]:
        raise 401

# 改后
async def get_current_user(
    authorization: str | None = Header(None),
) -> AuthUser:
    # 路径 1: 旧的全局 QUERY_API_KEY（兼容 7-20 加的）
    if global_api_key := os.environ.get("QUERY_API_KEY"):
        if authorization and token_matches(authorization, global_api_key):
            return AuthUser(user_id="global", org_id="org_default", auth_type="legacy_api_key")
    
    # 路径 2: 新的 per-user API Key
    if authorization and key := api_key_repo.lookup(authorization):
        return AuthUser(**key.to_user())
    
    # 路径 3: JWT
    if authorization and jwt := decode_jwt(authorization):
        return AuthUser(**jwt.to_user())
    
    raise 401  # 所有路径失败
```

**好处**：
- 老的 `QUERY_API_KEY=xxx` 还能用（**不破坏现有本地开发**）
- 老的 API Key 解析为 `user_id="global"`，自动继承 `org_default` 的数据访问
- 新部署直接用 JWT 路径，旧的 env 变量可留可弃

### 2.6 速率限制（目标 4）

```python
# Redis 计数器，per-user + per-endpoint
key = f"rate_limit:{user_id}:{endpoint}:{minute_bucket}"
incr(key, ttl=60)
if count > LIMIT:
    raise 429
```

| Endpoint | 限制 |
|---|---|
| `/api/query` | 30 req/min/user |
| `/api/auth/login` | 5 req/min/IP（防爆破）|
| `/api/auth/keys` | 10 req/day/user（防滥发）|

---

## 3. 取舍记录

| 决策 | 备选（及不选的理由） | 选它的理由 |
|---|---|---|
| JWT + API Key 双轨 | 纯 JWT：CI / 脚本无法用短期 token；纯 API Key：无 session 概念、刷新机制复杂 | 不同场景需要不同生命周期，**双轨反而省事**——同套用户表 |
| 行级 `owner_id` 字段 | 应用层中间件过滤：所有手写 SQL 都要走中间件，新增节点漏一次就**全部数据泄露** | DB 层兜底，**代码漏一次只影响自己** |
| ContextVar 传递身份 | state 加字段：state 混鉴权跟业务职责 | state 管业务数据，Context 管运行时身份，**职责分离** |
| `QUERY_API_KEY` 保留兼容 | 强制迁移：本地开发流程断（你 7-20 加的临时调试用 key 不能废） | 老的 key 解析为 `user_id="global"`，自动继承 `org_default` 范围，**兼容成本几乎为零** |
| 不做 OAuth 2.0 / SSO | 1 期就上 OAuth：项目还没用户，先做 OAuth 是 YAGNI | 等真的有跨系统登录需求时再做 |

---

## 4. 迁移计划

### Phase 1（**0 破坏**，1-2 天）

1. 加 `owner_id` 字段，默认 `'org_default'`
2. 加 `users` / `api_keys` 表
3. 改 `get_current_user` Depends，但**不挂到 query_router**（先放在新 `/api/auth/*` 路由测试）
4. 改 `RuntimeContext` 加 `user_id` 字段，**先不消费**

### Phase 2（**灰度**，2-3 天）

1. 把 `get_current_user` 挂到 `query_router`
2. `QUERY_API_KEY` 兼容路径返回 `user_id="global"`，`global` 用户走 `org_default` 范围
3. 加 `RowLevelFilter` 中间件：识别 `fact_order` / `dim_customer` 表的 SQL，**自动注入** `WHERE owner_id = ?`
4. 跑完整测试套件，验证 7.2 万单查询不受影响

### Phase 3（**完整**）

1. 颁发 JWT 路径上线
2. 颁发 API Key 路径上线
3. 速率限制 + 审计日志
4. README 加章节，前端配合改

---

## 5. 开放问题

- [ ] **session_id 与 user_id 关系**：现在 session_id 跟用户没绑定，登录后是否要把 user_id 写进 session？  
  倾向：是，但作为 follow-up 任务（不在本期）
- [ ] **前端登录页面**：要做最小登录页 vs 假设前端已有？  
  倾向：假设前端已有，本期只出 `/api/auth/login` 接口
- [ ] **审计日志存哪**：存 MySQL 还是单独 ES？  
  倾向：MySQL（跟 bad_case 一起），后期再迁
- [ ] **API Key 撤销后的活跃 session**：是立即失效还是延迟？  
  倾向：JWT 无解（短 token 即可），API Key 立即失效（每次请求都查 DB）

---

## 6. 风险

| 风险 | 概率 | 缓解 |
|---|---|---|
| SQL 自动注入 WHERE 漏识别（多表 JOIN / 子查询）| 中 | 黑盒测试覆盖复杂 SQL；加 dry-run 模式 |
| `owner_id` 没加 NOT NULL DEFAULT，迁移后新插入的 NULL 行泄露 | 中 | ALTER 用 `NOT NULL DEFAULT 'org_default'` |
| 7.2 万单回填时锁表 | 低 | MySQL 8 online DDL，秒级完成 |
| 老的 admin_router 的 `X-Admin-Token` 跟新 user 系统不兼容 | 低 | admin 仍走专用 token，不并入 user 系统 |

---

## 7. 测试策略

1. **单元测试**：
   - `_check_query_api_key` 三种路径（legacy / api_key / jwt）
   - `RowLevelFilter` 解析各种 SQL 形态
2. **集成测试**：
   - 创建用户 → 登录 → JWT 查询 → 验证 SQL 自动注入 owner_id
   - 不同用户查询同一表，**结果集不相交**
3. **灰度验证**：
   - 老的 7.2 万单查询（org_default）必须能跑
   - 老的 `QUERY_API_KEY=xxx` 必须还能用

---

## 8. 关联文档

- `ai-application-pain-points-rfc.md`：7-20 安全加固
- `CLAUDE.md` 项目规则
- `docs/daily-notes/2026-09-16.md`：本 RFC 起草记录
