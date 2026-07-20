# MEMORY.md - 长期记忆

## 关于用户
- 姓名：未知（待补充）
- 时区：Asia/Shanghai
- 项目：shopkeeper-agent-main（电商问数AI Agent项目）

## 重要事件
### 2026-06-26
- 创建了 error-learner 技能（自动记录报错+自我成长）
- 成功运行 shopkeeper-agent-main 项目后端
- 项目依赖 Docker 服务未启动，部分功能受限
- **修复并重新启动项目**：
  - 重建了 app/main.py（原文件为空）
  - 修复了 request_id.py 中的拼写错误
  - 修复了中间件注册方式
  - 启动了 Elasticsearch 容器
- **项目现在完全运行**：
  - 后端：http://0.0.0.0:8000
  - 数据库：MySQL (运行中)
  - 向量库：Qdrant (运行中)
  - 搜索：Elasticsearch (运行中)
  - 所有依赖服务正常

## 技能库
- error-learner：五阶段自成长报错处理系统（捕获→诊断→修复→学习→成长）
- 位置：c:\Users\yuanzheng1.zhang\.trae-cn\skills\error-learner\

## 项目配置
- 后端：FastAPI + Uvicorn (http://127.0.0.1:8000)
- 数据库：MySQL (Docker)
- 向量库：Qdrant (Docker)
- 搜索：Elasticsearch (Docker)
- Embedding：需要 Docker 运行（容错跳过）

## 技术决策
- 将 asyncmy 替换为 aiomysql（避免 C++ 编译依赖）
- 实现容错启动（外部服务不可用时仍可运行）
- 配置智谱 API (glm-4-flash)
- 修复了项目中的代码错误

## 待办事项
- 启动 Docker 并运行 `docker compose up -d` ✓（已手动启动所有容器）
- 补充 USER.md 中的用户信息
- 测试完整的电商问数功能（现在可以测试 /api/query 接口）

## AI 应用层痛点 RFC（2026-07-09）
- 产出两份 RFC（都在 ~/Downloads/）：
  - `grill-me-production-pain-points-rfc.md` — 后端工程 6 刀（SQL循环/LLM容错/假评估/可观测性/Redis竞态/追问关键词匹配）
  - `grill-me-ai-application-pain-points-rfc.md` — AI 应用层 6 刀（意图识别/单跳召回/无rerank/Prompt零示例/上下文污染/无结果校验）
- **已实施刀1（意图分类+查询改写）**：commit 353f8c9，12 文件 +632/-26 行
  - 新增 4 节点：classify_intent / rewrite_query / respond_chitchat / respond_metadata
  - graph 从 12 节点变 16 节点，闲聊和元数据查询短路
  - 顺手修了刀5 核心改动（state 加 history 字段，query 变纯净，不再用 build_prompt 拼接）
  - 三条路由全部实测通过（闲聊秒回 / 元数据秒回 / 数据查询全链路通）
- **待实施**：刀2（多跳召回）→ 刀4（Prompt few-shot）→ 刀3（rerank）→ 刀6（结果校验）
- 复合问题分解（刀1 延伸）暂不做，复杂度太高（检测+编排+结果合并三层）

## 三方向改造（2026-07-17）
详见 `.workbuddy/memory/2026-07-17.md`，核心架构决策如下：

### 架构图（按层叠顺序）
```
┌─ conf/app_config.yaml ─┐    OmegaConf strict merge
│  logging/db/qdrant/... │  ───┐
│  llm_profiles:         │    │
│    cheap: MiniMax2.7   │ ───┤ (1) llm_profiles/node_profiles
│    strong: MiniMax-M3  │    │     在 merge 前 pop 出来
│  node_profiles:        │    │     单独装配（详见 app_config.py 顶部）
│    classify_intent:    │    │     ↓
│      cheap             │ ───┘
└────────────────────────┘
                  ↓
┌─ app/agent/llm.py ────────────┐
│  LLMRegistry: dict+Lock       │
│   - get(profile)              │
│   - get_by_node(node)         │  路由：node → profile → model
│   - rebuild_profile(profile)  │  热切换：替换 self._models[name]
│   - list_profiles()           │
│  get_llm(node_name)           │  ← 节点统一接口
└───────────────────────────────┘
                  ↓  with_config({"callbacks": [LLMTimingCallback()],
                  ↓               "metadata": {"profile": name}})
┌─ app/agent/llm_callbacks.py ──┐
│  BaseCallbackHandler          │
│   on_llm_start → on_llm_end   │  模型名 + 耗时 + token
│                → on_llm_error │
└───────────────────────────────┘
                  ↓
┌─ app/agent/nodes/*.py ────────┐
│  @timed_node                  │  ← 全节点装饰器
│  llm = get_llm("节点名")      │  ← 统一入口
│  chain = prompt | llm | parser│
└───────────────────────────────┘
                  ↓
┌─ app/core/pydantic_parser.py ─┐
│  PydanticIntentParser         │  schema 校验 + think 块兼容
│   1. safe_parse_json()        │  剥 think + 抓围栏
│   2. model_validate(QueryIntent)
└───────────────────────────────┘
                  ↓
┌─ app/api/routers/admin_router.py ─┐
│  GET  /api/admin/llm-profile     │  ← X-Admin-Token 鉴权
│  POST /api/admin/llm-profile     │  ← 热切换
│  GET 响应：api_key_masked        │  ← 脱敏
└──────────────────────────────────┘
```

### 关键设计决策（避坑点）

#### 1. OmegaConf strict schema 绕过（**最容易踩坑**）
- 原 AppConfig 用 `OmegaConf.structured(AppConfig)` 做 strict merge
- llm_profiles / node_profiles 的 key 是**动态**的（cheap/strong/8个节点名）
- 不能加进 dataclass 字段（每次新增 profile 要改代码）
- **解决**：merge 前先 `context.pop("llm_profiles")` + `pop("node_profiles")`，手动 `_build_*_config` 装配到 `app_config.llm_profiles` / `app_config.node_profiles`
- 代码位置：`app/conf/app_config.py` 256-275 行（顶部注释解释了 Why）

#### 2. ${VAR} env 占位符二次展开
- OmegaConf 原生只支持 `${oc.env:VAR,default}` 语法
- YAML 里希望用更标准的 `${VAR}` 形式
- 解决：merge 之后、to_object 之前跑一遍正则替换（只匹配 `[A-Z_][A-Z0-9_]*`）
- **fail-fast**：变量未注入 → 启动时 RuntimeError（避免运行时 NPE）
- 代码位置：`app/conf/app_config.py:225-251` `_expand_env_placeholders`

#### 3. callbacks.metadata 挂 profile 名（**admin API 的关键依赖**）
- 每个 model 实例挂 callbacks：`base.with_config({"callbacks": [...], "metadata": {"profile": name}})`
- 这样 callback 里能从 metadata 读到当前是哪个 profile
- admin API 切换后 callback 自动换 → 日志能区分 cheap/strong 调用
- 注意：`with_config` 返回**新实例**，不污染原 base

#### 4. LangChain on_llm_start model_name 提取
- 不同版本 ChatOpenAI 序列化结构不一致
- 新版：`serialized["kwargs"]["model_name"]`
- 旧版：`serialized["kwargs"]["model"]`
- 自定义 wrapper：可能在 metadata 里
- **三个字段都尝试**，按优先级回退

#### 5. Pydantic v2 严格校验 vs LLM 容错
- `model_config = {"extra": "ignore", "populate_by_name": True}`
- `extra="ignore"`：LLM 输出额外字段不报错（避免 thinking 残留炸 schema）
- `populate_by_name=True`：让 `from_` 字段也能直接写 `from`（Python 关键字）
- 失败 retry 1 次，仍失败降级空 intent（generate_sql 用 SELECT 1 兜底）

#### 6. timed_node 设计取舍
- **不读** state 内容（避免 query/SQL 泄漏到日志）
- 只读 state["query"] 长度作为 query_len（信息够用）
- **不替代**节点内部的 writer({"type": "progress", ...})，两者并存
- 异常时记 status="error"，原异常继续向上抛（不破坏现有 except 逻辑）

### env 变量清单（部署必填）
- `LLM_CHEAP_BASE_URL` — cheap profile base_url
- `LLM_CHEAP_API_KEY` — cheap profile api_key
- `LLM_STRONG_API_KEY` — strong profile api_key（base_url 写死在 yaml）
- `ADMIN_TOKEN` — admin API 鉴权（缺失 → admin API 503）
- `LLM_API_KEY` — 老配置 fallback（兼容老 llm 配置）
- 可选：`LOG_FORMAT=json` 切机器可读日志

### 当前 strong profile 配置
- base_url: `https://api.minimaxi.com/v1`（MiniMax 官方 OpenAI 兼容端点）
- model_name: `MiniMax-M3`
- **不是** `ml-api-gw-en.tcl.com`（那是另一个项目"龙虾"的端点）

### 测试覆盖（59 个新增 + 原有）
- `test_intent_schema.py` (12) — Pydantic schema
- `test_pydantic_parser.py` (11) — Parser 边界
- `test_timing.py` (9) — 装饰器
- `test_llm_callbacks.py` (11) — Callback 三事件
- `test_llm_registry.py` (16) — Registry + admin API 端到端
- 全量：112 通过 / 11 失败（**全部 pre-existing**，需真实 LLM key 或与本改造无关）

### 可继续做的 TODO
1. 真实 LLM key 接入后跑 `tests/eval_e2e.py` 50 条评测（验证 schema 改造无回归）
2. Prometheus metrics（plan 列了 optional，方向 1 没做）
3. admin API V2 接 OAuth + RBAC
4. 修 pre-existing 失败：session_store.popitem Python 3.14 kwargs / scheduler

## 沟通偏好
- **技术解释要用面试对话体**：用户准备面试，解释概念时用"面试官问→你答"的格式，不要用 RFC 的"现状→问题→方案"三段式
- 用"能不能跟面试官说清楚"来校准解释深度
- **数字展示风格**：原样输出不加千分位（107373，不是 107,373）、不加小数位（除非原数据有）
- **关注点**：全链路可观测性 + LLM 切换 + function call —— 这三个方向是用户最感兴趣的设计点

## PromptTemplate f-string → jinja2 迁移（2026-07-17）

**触发**：用户问"查询华东这个月的环比增长率"返回 fallback=1，日志显示 `generate_intent` 抛 `PromptTemplate validation error: Invalid format specifier in f-string template. Nested replacement fields are not allowed.`

**根因**：`generate_intent.py` 把 `load_prompt("generate_intent") + _intent_parser.get_format_instructions()` 拼成一个模板字符串。prompt 文件里 JSON 字面量用 `{{...}}` 转义，但 `get_format_instructions()` 的 `json.dumps(schema)` 输出嵌套 JSON `{...{...}...}` 没做转义，触发 f-string 嵌套检测。

**修复**：全量 10 处 PromptTemplate 加 `template_format="jinja2"`，11 个 .prompt 文件改 jinja2 写法（变量 `{{ var }}`，JSON 字面量 `{...}` 原样）。**jinja2 关键优势**：只把 `{{ var }}` 当变量，单层 `{...}` 是字面量，所以嵌套 JSON 不用任何转义。

**额外发现**：plan_query 节点原本用 `prompt_text.replace("{examples}", examples)` 字符串 replace，绕开 f-string 解析问题；改成 PromptTemplate + jinja2 后这个问题自动消失（jinja2 不解析单层花括号）。

**顺手修 bug**：`generate_intent.prompt` 里 `business_rules` 变量声明了但模板没用，改 jinja2 时补上对应占位符。

**已知遗留**：
- `aggregate_results.prompt` / `review_answer.prompt` 加载方式是字符串 replace（不是 PromptTemplate），jinja2 写法对它们**不生效**，但因为只是占位文本无 JSON 字面量冲突，运行时仍 OK
- 这三个 prompt 文件 `plan_query.prompt` / `aggregate_results.prompt` / `review_answer.prompt` **不在 git 索引**（未追踪），需要补 commit

**验证**：12 个 prompt 文件 + 1 个内联 prompt 全部 jinja2 构造+渲染通过；27 个非 LLM 依赖测试通过；10 个 LLM e2e 测试因 `test-strong-model` Connection error 失败（与改造无关）。

完整 changelog：`docs/notes/PromptTemplate迁移jinja2-20260717.md`

---

## 今日（2026-07-17）完整事件链

**4 个独立事件**（按时间顺序）：

1. **jinja2 全量迁移**（17:30-17:43）— fallback=1 bug 根因修复，10 处代码 + 14 个 .prompt
2. **端口被占 → uvicorn --reload 重启**（17:51-18:25）— 旧 PID 19884 占 8000，杀掉后启动 PID 27616
3. **multi-agent SSE result 推送 bug 修复**（18:25-18:30）— `aggregator_node.py` 三处加 `writer({"type": "result"})`
4. **Prompt 边界示例 + 数字格式约束**（18:30-18:33）— `classify_intent.prompt` 加 3 条边界示例；`aggregate_results.prompt` 加 4 条数字格式约束

**完整时间线索引 + 涉及文件清单** + **关键经验教训** 都在 `.workbuddy/memory/2026-07-17.md` 末尾的"今日总结"章节。

**关键经验**（按重要性）：
1. 前端 SSE `type="result"` 事件是消息状态切换到 done 的唯一信号
2. `--reload` 模式启动：改代码不需要手动重启
3. 重启时必须杀干净旧 uvicorn，否则报 `WinError 10013`
4. LLM 输出格式必须 prompt 强约束（千分位、单位换算等"可读性"包装是默认行为）
5. jinja2 优于 f-string：嵌套 JSON 字面量不需要任何转义
6. Prompt 边界示例的覆盖率很重要：GMV 是 data_query，环比增长率也应该是

---

## ⚠️ 前后端双进程重启（已踩坑 2 次，必看）

shopkeeper-agent 是双进程项目，**两个服务都要重启**，缺一不可：

| 服务 | 端口 | 启动命令 | 工作目录 |
|---|---|---|---|
| **后端 uvicorn** | 8000 | `.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload` | `D:\shopkeeper-agent\` |
| **前端 Vite** | 5173 | `pnpm dev --host 0.0.0.0` | `D:\shopkeeper-agent\frontend\` |

### 历史
- 2026-07-17：uvicorn 重启踩坑（端口 10013）
- **2026-07-20 又踩**：只重启了 uvicorn，前端 Vite 没重启 → 用户访问 5173 报 ERR_CONNECTION_REFUSED → 反馈"你关闭了吗"

### ⚡ 关键机制
- `run_in_background=true` 启动的 Bash 任务，**会话结束时会被自动清理**
- 清理后台任务时，启动的子进程（uvicorn / vite）一起退出
- **用户的"项目进不去了，你关闭了吗"= 服务被清理了**

### 应对（每次会话都要做）
1. **会话开始先确认两个服务都在跑**：
   ```powershell
   Get-NetTCPConnection -LocalPort 8000,5173 -State Listen
   ```
2. **启动/重启时都用 `run_in_background=true`**，避免阻塞主线程
3. **告诉用户两个 URL**：前端 `http://localhost:5173/`，后端 API 通过前端代理（`vite.config.ts` 里的 `VITE_DEV_PROXY_TARGET`，默认 `http://127.0.0.1:8000`）

---

## 绝对时间解析修复（2026-07-20 17:26 bug）

**bug**：`query="查询华东2025年1月的环比增长率"` → rewrite_query 没解析绝对时间 → `_resolve_relative_time` 只匹配相对时间 → time_range 全空 → SQL 没 WHERE 时间 → 兜底"未查到"

**修复（2 处改动）**：
- `app/agent/nodes/rewrite_query.py` `_resolve_relative_time` 新增 4 种绝对时间正则（YYYY-MM-DD / YYYY年M月 / YYYY年Q季 / YYYY年），**优先级最高**避免被相对时间"误吃"
- `prompts/rewrite_query.prompt` 加"绝对时间"段落，告诉 LLM 绝对时间保持不变（程序识别）

**验证**：9 个测试用例（2025年1月 / 2025-01 / 2025/03 / 2025年Q2 / 2025-Q1 / 2025年 / 2025-01-15 / 上月 / 最近7天）+ 27 个非 LLM pytest 全过无回归

**设计原则**：
- 相对时间 vs 绝对时间 是两个独立维度
- prompt 标准化对照表必须**穷举**所有支持的格式，未列出的 LLM 不知道怎么"标准化"