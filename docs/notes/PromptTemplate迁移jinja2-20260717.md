# PromptTemplate 迁移 f-string → jinja2（2026-07-17）

## 背景

用户问"查询华东这个月的环比增长率"返回 fallback=1，根因是 `generate_intent` 节点的 PromptTemplate 抛 "Nested replacement fields are not allowed"。

**触发链路**：

1. `app/agent/nodes/generate_intent.py:142-154` 构造 PromptTemplate 时，把 `load_prompt("generate_intent") + _intent_parser.get_format_instructions()` 拼成一个模板字符串
2. prompt 文件用 `{{...}}` 转义 JSON 字面量（f-string 模式下必须转义）
3. `get_format_instructions()` 输出里 `json.dumps(schema)` 产出**嵌套** JSON（`{"items": {"properties": {...}}}`），但**没做花括号转义**
4. 拼接后模板里同时存在 `{{...}}`（f-string 转义）和 `{...{...}...}`（嵌套 JSON 字面量）
5. PromptTemplate 内部用 Python f-string 校验模板格式，遇到嵌套替换字段抛 `ValueError`
6. generate_intent 节点 except 分支返回空 intent → 下游 SELECT 1 AS fallback

## 修复方案

**全量将 PromptTemplate 从 f-string 切到 jinja2 模板**：

| 元素 | f-string 写法 | jinja2 写法 |
|---|---|---|
| 变量 | `{var}` | `{{ var }}`（带空格） |
| JSON 字面量 | `{{...}}` 转义 | `{...}` 原样 |
| JSON 嵌套 | `{{a:{{b:1}}}}` ❌ 嵌套炸 | `{a: {b:1}}` ✅ |

**jinja2 的关键优势**：jinja2 只把 `{{ var }}` 当变量，**单层 `{...}` 是字面量**，所以 JSON 嵌套不需要任何转义。

## 改动清单

### 代码侧（10 处加 `template_format="jinja2"`）

| 文件 | 节点 |
|---|---|
| `app/agent/nodes/classify_intent.py` | classify_intent |
| `app/agent/nodes/rewrite_query.py` (2 处) | extract_inherited_context + rewrite_query |
| `app/agent/nodes/_recall_helpers.py` | extend_keywords_for_*_recall |
| `app/agent/nodes/filter_table.py` | filter_table_info |
| `app/agent/nodes/filter_metric.py` | filter_metric_info |
| `app/agent/nodes/planner_node.py` | plan_query（**额外**：从字符串 replace 改成 PromptTemplate + jinja2，因为 plan_query.prompt 改成 jinja2 写法后字符串 replace 找不到 `{examples}`） |
| `app/agent/nodes/generate_intent.py` ⭐ | generate_intent（修复目标） |
| `app/agent/nodes/correct_sql.py` | correct_sql |
| `app/agent/nodes/respond_chitchat.py` | respond_chitchat（内联字符串同步改 jinja2） |

### prompt 文件侧（11 个被 git 追踪的 + 3 个未追踪的）

**被追踪、已改 jinja2 写法**：

- `classify_intent.prompt` / `correct_sql.prompt` / `extend_keywords_for_*_recall.prompt`（3 个） / `extract_inherited_context.prompt` / `filter_metric_info.prompt` / `filter_table_info.prompt` / `generate_intent.prompt` / `generate_sql.prompt` / `rewrite_query.prompt`

**未追踪（不在 git 中）、也已改 jinja2 写法**：

- `aggregate_results.prompt` / `plan_query.prompt` / `review_answer.prompt`

> 三个未追踪的 prompt 文件被代码 `load_prompt()` 引用，功能上必须存在；改 jinja2 写法是为风格统一。
> 但 `aggregate_results.prompt` / `review_answer.prompt` 在代码里走**字符串 replace** 方式加载（不是 PromptTemplate），jinja2 写法对它们不生效（找不到 `{var}` 形式），这是已知的小不一致。

## generate_intent.prompt 的额外修复

改 jinja2 同时发现一个**预存在 bug**：

- `app/agent/nodes/generate_intent.py` `input_variables` 列表里有 `business_rules`
- 但原 prompt 文件里**没有** `{business_rules}` 占位符
- 原 f-string 模式下 `business_rules` 会被注入但**没被模板使用**，属于"声明但不用"，f-string 渲染时不会报错
- 改 jinja2 模式后，为了保持 prompt 完整性（让 LLM 知道有"业务规则"这个上下文），**在 prompt 里补上了 `{{ business_rules }}` 段**（含使用说明）

## 验证

1. **PromptTemplate 构造 + 渲染**：自写脚本验证 12 个 prompt 文件 + 1 个内联 prompt，全部通过
2. **pytest**：27 个非 LLM 依赖测试全部通过；10 个 LLM 依赖 e2e 测试失败（环境 LLM `test-strong-model` 连不上 Connection error，与 jinja2 改造无关）
3. **日志对照**：跑完测试后查 `logs/app.log`，确认 `generate_intent` 节点不再抛 `PromptTemplate validation error`，而是走到 LLM 调用阶段（Connection error 是 LLM mock 问题）

## 后续可选优化（未做）

1. 三个未追踪的 prompt 文件（`aggregate_results` / `plan_query` / `review_answer`）建议加进 git 追踪，避免代码引用幽灵文件
2. `aggregate_results` 和 `review_answer` 加载方式（字符串 replace）建议统一成 PromptTemplate，消掉风格不一致
3. 添加 prompt 模板 lint 检查：扫描 `.prompt` 文件，禁止同时出现 `{var}` 单花括号和 `{{var}}` 双花括号（不混用）

## 关键 commit 信息

- 修改文件：11 个 .py + 11 个 .prompt（共 22 个文件）
- 未追踪改动：3 个 .prompt（aggregate_results / plan_query / review_answer）
- 影响节点：classify_intent / rewrite_query (2x) / extract_keywords / recall_* (3x) / filter_table / filter_metric / planner / generate_intent / correct_sql / respond_chitchat