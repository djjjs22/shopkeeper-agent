## 修复两个埋的坑

### 坑 #4：recall 链路绕过 LLM 热切换

**根因**：`_recall_helpers.py:24` `from app.agent.llm import llm` 拿的是模块级固定实例（=strong profile 快照），admin API 调 `rebuild_profile` 时 `_registry._models["xxx"]` 更新了，但 `llm` 全局变量和 helper 里 chain 绑定的还是旧对象。3 个 recall 节点（recall_column / recall_metric / recall_value）全用 `expand_keywords_with_llm`，整条召回链路无法热切换。

**修法**：helper 接收 node_name 参数，内部走 `get_llm(node_name)` 按节点路由 + 实时查注册表。
- `_recall_helpers.py:expand_keywords_with_llm(prompt_name, query)` → `expand_keywords_with_llm(prompt_name, query, node_name)`
- 内部 `from app.agent.llm import llm` 改成 `from app.agent.llm import get_llm`，chain 用 `get_llm(node_name)` 动态获取
- 删掉顶部 `from app.agent.llm import llm` 这个旧 import（模块不再需要）
- 3 个调用方（`recall_column.py:43`、`recall_metric.py:43`、`recall_value.py:40`）各加一个 `node_name=` 参数，传自身函数名（如 `"recall_column"`）

**配置侧**：`conf/app_config.yaml:101` 的 `node_profiles` 补 3 个 recall 节点的 profile 映射（默认 `strong`，和当前行为一致）：
```yaml
node_profiles:
  ...
  recall_column: strong
  recall_metric: strong
  recall_value: strong
```

### 坑 #5：ALLOWED_TABLES 形同虚设（强制启用默认白名单）

**根因**：`sql_safety.py:131` `validate()` 签名有 `allowed_tables: Optional[List[str]] = None`，docstring 写"不传则使用类默认 ALLOWED_TABLES"，但函数体里压根没用这个参数。`run_sql.py:216` 调用 `SQLSafetyValidator.validate(sql)` 时也没传，白名单完全没生效。

**修法**：在 validate() 中实现表名白名单校验，逻辑放在第一层"危险关键字拦截"和第二层"SELECT/WITH 开头校验"之间（即顺序：空值 → 危险关键字 → **表名白名单** → SELECT/WITH → 注入检测）。

具体做法：
1. 默认值改 `None` 的语义：`allowed_tables = allowed_tables or cls.ALLOWED_TABLES`（落点在函数开头）
2. 仅当白名单非空时执行表名提取
3. 从 `sql_no_strings`（已移除引号字面量，防误杀）里用正则提取所有 `FROM <表>` 和 `JOIN <表>` 的表名（支持 `` `backtick` ``、`schema.table`、别名 `t1` 截断）
4. 大写化后比对白名单（大小写不敏感），任何一个不在白名单 → raise ValueError，错误信息列出不合法的表名 + 白名单
5. `run_sql.py:216` 不动（自动拿到默认白名单）

**测试**：`tests/test_sql_safety.py` 补 3-4 个用例：
- 合法表（dim_region / fact_order 等）→ 通过（用现有 `test_正常SELECT查询_应通过` 即可，不需改）
- 非法表（如 `SELECT * FROM users`）→ 拦截
- 多表 JOIN 含非法表 → 拦截
- 带 schema 前缀 + 反引号 → 正确提取并放行合法表
- 显式传 `allowed_tables=["users"]` 自定义 → `users` 放行

### 验证步骤
1. `uv run pytest tests/test_sql_safety.py -v` —— 全绿（旧 17 个用例 + 新增白名单用例）
2. `uv run ruff check app/agent/nodes/_recall_helpers.py app/agent/nodes/recall_column.py app/agent/nodes/recall_metric.py app/agent/nodes/recall_value.py app/core/sql_safety.py` —— 无 lint 错
3. `uv run python -c "from app.agent.nodes._recall_helpers import expand_keywords_with_llm; from app.core.sql_safety import SQLSafetyValidator; print('import ok')"` —— import 不报错
4. 不跑 e2e（需要 docker 起所有基础设施，超出本次修复范围）

### 文档同步
- `CLAUDE.md` 追加一条"教训写入"：白名单/默认参数若只在 docstring 承诺、函数体没实现，等于埋坑。下次加默认参数时必须同步实现并补测试。
- 不动 README（README 描述架构，这两个是内部实现细节，不影响对外说明）

### 不做的事
- 不动其他节点（filter/recall_* 的并行检索部分、reviewer 等），它们已经用 `get_llm` 了
- 不重写 `sql_safety.py` 的注释风格（虽然有过度教学注释，但属于另一个清理议题，本次不混入）
- 不补 `run_sql.py` 传 `allowed_tables` 的调用（默认值生效即可，调用方零改动是更优雅的接口）
- 不修 `prompts/` 里的 SQL 生成模板（白名单是兜底防线，prompt 层不必强约束表名）