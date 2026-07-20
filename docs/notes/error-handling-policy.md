# 错误处理策略（2026-07-20 制定）

> 项目此前节点错误处理三套风格混用，本文档定约定，新节点遵循此规范。

## 核心原则：分类处理

节点按"是否可降级"分两类，错误处理策略不同：

### A 类：可降级节点（LLM 输出类）
**特征**：节点产出是"建议性"的——LLM 想给后续步骤一个参考，没有它链路也能跑（用兜底值）。
**典型节点**：`classify_intent` / `rewrite_query` / `filter_table` / `filter_metric` / `generate_intent` / `correct_sql` / `planner` / `aggregator` / `reviewer`。
**策略**：
- `except Exception as e:` 内部 `logger.error`，返回**降级默认值**
- 默认值要"无害"：`classify_intent` → `"data_query"`；`filter_table` → 保留所有候选；`generate_intent` → `{}`
- **必须给前端一个 warning**（`writer({"type": "warning", ...})`），让用户知道发生了降级
- **不要 raise**——下游节点设计了降级路径，让它接着跑

### B 类：硬依赖 IO/数据节点
**特征**：节点产出是"事实性"的——失败就是失败，没有合理兜底。
**典型节点**：`run_sql` / `extract_keywords` / `merge_retrieved_info` / `recall_column` / `recall_metric` / `recall_value` / `validate_sql` / `add_extra_context` / `respond_chitchat` / `respond_metadata`。
**策略**：
- `except Exception as e:` 内部 `logger.error` + `writer({"type": "progress", "status": "error"})`
- **raise** 让 LangGraph 框架捕获，上层 `query_service` 统一包装成 SSE error
- 给前端的 message 走 `_safe_error_message` 脱敏（见 `run_sql.py`），不要透传 `str(e)`

### 混合节点（如 `run_sql`）
`run_sql` 同时有"可降级的安全拦截"（`SQLSafetyValidator` 失败 → return + error）和"硬失败的 DB 执行"（→ raise）。
**策略**：
- 安全拦截走 A 类风格（return + 推 error event，不 raise）
- DB 执行走 B 类（raise + 上层统一处理）
- 二者用嵌套 try 区分清楚

## 字段读取约定

下游节点取上游字段时**必须用 `state.get(...)`**，不要 `state[...]`：
- A 类节点失败时返回降级值，下游能拿到合理默认
- B 类节点失败时 raise，下游根本不会执行
- 用 `state.get(key, default)` 防御 A 类节点的偶发降级

## 前端展示

| 事件类型 | UI 处理 | 触发场景 |
|---|---|---|
| `progress` (success/error/running) | StepRail 更新 | 每个节点的状态变化 |
| `warning` | 黄色提示条 | A 类降级、空结果、结果截断 |
| `result` | ResultTable 渲染 | `run_sql` 成功 |
| `result` + `truncated=true` | ResultTable + 截断提示 | 结果超过 5000 行 |
| `error` | 红色错误条 + toast | B 类 raise（脱敏后的友好文案）|

## 例外：顶层 `query_service`

`query_service.query` / `query_multi_agent` 的最外层 except 是"最后兜底"——
任何漏网的异常都包装成 SSE error event 发给前端。
**必须脱敏**：用 `_friendly_error(e)` 分类，不透传 `str(e)`。

## 测试约定

- A 类节点：测试"LLM 输出坏 JSON 时"应返回降级默认值（不 raise）
- B 类节点：测试"上游输入错时"应 raise（或 return error patch）
- run_sql 测试：覆盖"安全拦截 → return"、"DB 异常 → raise"两条路径
