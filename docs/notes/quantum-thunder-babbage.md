# shopkeeper-agent 三方向改造方案

> 范围：可观测性增强 + LLM Profile Registry + Pydantic Schema 强校验
> 状态：待批准
> 路径：D:\shopkeeper-agent

---

## 1. 目标

让项目从"能跑"到"可观测、可控、健壮"，三个方向独立可回滚。

---

## 2. 三个方向的改造方案

### 方向 1：全链路可观测性增强

**已有**：RequestID 中间件、loguru 文件+控制台、节点 SSE 进度。

**改造点**：
- 新增 `app/core/timing.py`：节点装饰器 `@timed_node`，记录 `step/duration/status/query_len`，**不读 state 内容**（避免日志爆炸）
- 新增 `app/agent/llm_callbacks.py`：langchain `BaseCallbackHandler`，记录每次 LLM 调用的 token/latency/model（不改节点代码，挂到 `init_chat_model` 的 `callbacks` 参数）
- 修改 `app/core/log.py`：加 JSON formatter，按 `LOG_FORMAT` 环境变量切换
- 可选新增 `app/metrics.py`：Prometheus Counter/Histogram + `/metrics` 端点

**文件清单**：
| 类型 | 路径 |
|---|---|
| 新增 | `app/core/timing.py`、`app/agent/llm_callbacks.py`、`app/metrics.py` |
| 修改 | `app/core/log.py` |
| 修改 | `app/agent/llm.py`（挂 callbacks）|
| 修改 | 16 个节点（加 `@timed_node` 装饰器，单行）|

**验收**：`tests/test_timing.py` + `tests/test_llm_callbacks.py`；e2e 后 `logs/app.log` 全是 JSON 行。

---

### 方向 2：LLM Profile Registry + 运行时热切换

**已有**：`app/agent/llm.py` 模块级单例 + 8 个节点 import 它。

**改造点**：
- 改 `app/agent/llm.py` 为 `LLMRegistry` 类：内部 `dict[profile_name, BaseChatModel]` + `threading.Lock` 保护 + `get(profile)` 接口；**保留旧 `llm` 单例名做默认值**，新老并存两周再删
- 改 `conf/app_config.yaml`（**env 占位，不写明文 token**）：
  ```yaml
  llm_profiles:
    cheap:                                        # 弱模型：第三方中转代理
      model_name: MiniMax2.7
      base_url: ${LLM_CHEAP_BASE_URL}             # 部署时注入，例如 https://proxy.example.com/v1
      api_key:  ${LLM_CHEAP_API_KEY}              # 第三方中转 key，从环境变量注入
      request_timeout: 15
      max_tokens: 500
    strong:                                       # 强模型：公司内部网关（保持现状）
      model_name: MiniMax-M3
      base_url: https://ml-api-gw-en.tcl.com/agi/v1
      api_key:  ${MINI_MAX_API_KEY}               # 项目原有的 env 名，保持不变
      request_timeout: 30
      max_tokens: 2000
  node_profiles:                                  # 节点 → profile 映射
    classify_intent: cheap
    respond_chitchat: cheap
    filter_table:    cheap
    filter_metric:   cheap
    extract_keywords: cheap
    generate_intent: strong
    correct_sql:     strong
    rewrite_query:   strong
  ```
- 改 `app/conf/app_config.py`：
  - 加 `LLMProfileConfig` / `LLMProfilesConfig` / `NodeProfilesConfig` 三个 dataclass
  - 加 `os.environ.expandvars()` 在 yaml 加载后跑一遍，把 `${XXX}` 替换成实际值
  - 启动时校验：所有引用的 env 变量必须存在，否则启动失败（fail-fast）
- 新增 `app/api/routers/admin_router.py`：
  - `POST /api/admin/llm-profile {node, profile}` 切换
  - `GET /api/admin/llm-profile` 查看当前映射
  - 鉴权：`X-Admin-Token` header == `ADMIN_TOKEN` 环境变量
- 修改 `app/main.py` 注册 admin_router

**8 个 import llm 的位置**（必须改）：
```
app/agent/nodes/{classify_intent, generate_intent, correct_sql,
                 rewrite_query, respond_chitchat, filter_table,
                 filter_metric}.py
app/agent/nodes/_recall_helpers.py
```

**验收**：`tests/test_llm_registry.py` + `tests/test_admin_router.py`；e2e 跑 `POST /api/admin/llm-profile` 切换后日志看 `model_name` 变了。

---

### 方向 3：Pydantic Schema 强校验

**已有**：`SafeJsonOutputParser`（regex 剥 think 块肉搏），8 个节点用。

**改造点**：
- 新增 `app/entities/intent_schema.py`：定义 `QueryIntent` Pydantic BaseModel（schema 来自 `sql_template.py` 顶部 docstring）
- 新增 `app/core/pydantic_parser.py`：继承 `BaseOutputParser`，**先**用 `safe_parse_json` 剥 think 块 + 抓围栏，**再**用 `Model.model_validate_json` 强校验；失败转 `OutputParserException`
- 修改 `app/agent/nodes/generate_intent.py`：`chain = prompt | llm | PydanticJSONParser(pydantic_object=QueryIntent)`；解析失败 → `retry_once` 重试 1 次 → 仍失败返回空 dict（下游 `SELECT 1` 兜底）
- 修改 `prompts/generate_intent.prompt`：末尾追加 `{{format_instructions}}`
- **不动**其他节点（`filter_*` 输出结构不固定，保留 `SafeJsonOutputParser`；`classify_intent`/`correct_sql` 等输出纯字符串，保留 `StripThinkStrParser`）

**文件清单**：
| 类型 | 路径 |
|---|---|
| 新增 | `app/entities/intent_schema.py`、`app/core/pydantic_parser.py` |
| 修改 | `app/agent/nodes/generate_intent.py` |
| 修改 | `prompts/generate_intent.prompt` |

**验收**：`tests/test_intent_schema.py` + `tests/test_pydantic_parser.py`（含 think 块污染用例）；e2e SQL 输出格式稳定。

---

## 3. 推荐实施顺序

| 序 | 方向 | 依赖 | 风险 | 工作量 |
|---|---|---|---|---|
| 1 | 方向 3 Pydantic | 无 | 低（4 文件）| 0.5 天 |
| 2 | 方向 1 可观测性 | 无 | 低（装饰器不读 state）| 1 天 |
| 3 | 方向 2 LLM 切换 | 方向 1 提供 metric 验证 | 中（8 文件）| 1.5 天 |

每个方向独立 commit，独立可回滚（`git revert`）。

---

## 4. 风险与回滚

| 风险 | 应对 |
|---|---|
| 方向 2 改 llm.py 牵涉 8 节点 | 渐进迁移：保留旧 `llm` 单例做默认值，新老并存两周 |
| 方向 3 Pydantic model 与 LLM 实际输出不匹配 | prompt 强约束（format_instructions）+ 兜底空 dict |
| 方向 1 装饰器破坏节点逻辑 | 装饰器只读 `step` 名 + `state["query"]` 长度，先在 1 节点试点 |
| admin API 鉴权弱 | 暂用 env `ADMIN_TOKEN`，V2 接 OAuth |
| loguru JSON 破坏人类可读 | `LOG_FORMAT` 环境变量开关，默认仍是人类可读 |

---

## 5. 关键背景（决策溯源）

- 用户偏好：结构化输出、表格优于长篇、中文、面试对话体
- RFC 刀1（意图分类）已实施，graph 16 节点
- 当前 LLM：强模型 = MiniMax-M3（OpenAI 兼容协议，走 `https://ml-api-gw-en.tcl.com/agi/v1`，API key 用 env `MINI_MAX_API_KEY`）
- 计划新增弱模型：MiniMax2.7（OpenAI 兼容协议，走第三方中转代理，env 占位 `LLM_CHEAP_BASE_URL` + `LLM_CHEAP_API_KEY`）
- **API Key 安全原则**：永远不写入 yaml / python 文件 / git 跟踪的任何文件，全部从环境变量注入；plan 文件同样不写明文 token
- Pydantic 依赖：未直接列在 pyproject.toml，但通过 `langchain>=1.2.15` 间接引入 → 可直接用，**无需新增依赖**
- 测试命令：`uv run pytest tests/ -v`

---

## 6. 部署时需要准备的环境变量

| 变量名 | 用途 | 必填 |
|---|---|---|
| `MINI_MAX_API_KEY` | strong profile API key（公司网关 MiniMax-M3） | 已有 |
| `LLM_CHEAP_BASE_URL` | cheap profile base_url（第三方中转，例如 `https://proxy.example.com/v1`） | 新增 |
| `LLM_CHEAP_API_KEY`  | cheap profile API key（中转代理 key） | 新增 |
| `ADMIN_TOKEN` | admin API 鉴权 header `X-Admin-Token` | 新增 |
| `LOG_FORMAT` | 可选，`json` 切换日志格式；默认人类可读 | 新增 |

启动时会 fail-fast 校验所有 `${XXX}` 占位符必须解析成功。