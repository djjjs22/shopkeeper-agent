# CLAUDE.md — Project Rules & Lessons

## Lessons Learned

### 2025-07-05: Skill 安装路径规则

**错误**: 安装 prompt-engineering-expert 时，将其放到了默认路径 `~/.workbuddy/skills/`。

**规则**: 本项目（shopkeeper-agent）的技能必须安装到自身的 `skills/` 目录下：
- 目标路径：`/Users/lunasama/Downloads/Agent/shopkeeper-agent/skills/`
- 每个 skill 放在单独的文件夹里，如 `skills/skill-name/`
- 如果使用 marketplace 安装器装到了默认路径，手动 copy 过来
- 绝对不要混用默认路径和项目本地路径

**为什么**: 项目要保持自包含和可移植性。所有技能文件应随项目一起走。

---

### 2026-07-20: 默认参数若只在 docstring 承诺、函数体没实现 = 埋坑

**场景**: `app/core/sql_safety.py` 的 `validate(sql, allowed_tables=None)` —— docstring 写"不传则使用类默认的 ALLOWED_TABLES"，但函数体里压根没用这个参数。`ALLOWED_TABLES` 类变量定义了 5 张表，看着像生效了，实际形同虚设。修法见 `git log` 2026-07-20 这次提交（补 `_extract_tables` + `_extract_cte_names`，并在注入检测之后做白名单比对）。

**规则**:
1. **加默认参数必须同步实现**：写 `Optional[X] = None` 的同时就要写"`None` 时用什么默认值"的实际代码，不能只在 docstring 里口头承诺。
2. **类变量定义了就要在某处被读到**：如果定义了 `ALLOWED_TABLES` 这种"看起来是配置"的类变量，必须 grep 一遍代码确认它真的被用，否则就是误导后续读者。
3. **新加的校验层要补正向 + 负向测试**：白名单这种安全功能，至少要测「合法表通过 / 非法表拦截 / 边界形态（CTE、schema 前缀、反引号、字面量）」四类，否则等于没测。
4. **测试顺序敏感**：多层校验里，注入检测必须先于表名白名单——否则 `UNION SELECT ... FROM users` 会被报成"非白名单表"而不是"注入"，错误分类错位。

---

### 2026-07-20: 改 helper 签名时，调用方一处都不能漏

**场景**: `_recall_helpers.py` 的 `expand_keywords_with_llm(prompt_name, query)` 改成 `(..., node_name)` 后，3 个 recall 节点（recall_column / recall_metric / recall_value）都要补传 `node_name`，同时 `conf/app_config.yaml` 的 `node_profiles` 也要补 3 个映射——否则 `get_llm(node_name)` 会 `KeyError`。

**规则**:
1. **改公共 helper 签名 → 列全所有调用点**：用 `grep -rn "expand_keywords_with_llm\|parallel_recall_dedup" app/` 之类命令找全，逐个确认改完。
2. **配置驱动的路由，改代码同时改配置**：节点走 `get_llm(node_name)` 时，`node_profiles` 必须有该节点的映射，否则运行时才炸（单测覆盖不到的话会漏）。
3. **既有测试 monkeypatch 失效是信号**：`_install_fake_llm` patch `m.llm = fake` 这种老风格，对走 `get_llm` 的新节点（generate_intent / 3 个 recall）已经失效——这是既有技术债，发现时记录但不顺手扩范围修，下次专门治理。

---

## Working Prompts

以下是藤子日常工作中使用的标准提示词模板，作为可复用的指令集。

### 1. 教训写入
> 每次犯错后，把根本原因和规则写进 CLAUDE.md，确保不再重犯。

### 2. 改动拷问
> 审完改动后逐条拷问我，我答不上来就阻止提 PR。

### 3. 根本原因修复
> 修 bug 必须挖到根因，不打表面补丁。修完验证一次确保真能过。

### 4. 推倒重写
> 如果当前方案不行，基于全部已知信息，重新设计优雅方案。

### 5. 先计划再动手
> 进入计划模式，只出方案，不碰一行代码。

### 6. 方案审查
> 以资深工程师视角，把开发计划审查一遍，挑出所有隐患。

### 7. 回计划模式
> 发现硬改搞不定就停下来，回到计划模式重新规划。

### 8. 代码复用检查
> 写方案前先把代码库里能复用的函数和组件摸一遍，不造重复轮子。

### 9. 回归验证
> 对比 main 分支和当前分支的行为差异，证明改动的安全性和正确性。

### 10. 老功能学习
> 做新功能前先看老功能的实现方式，对齐原有的设计风格和套路。

### 11. 理解确认
> 先听我讲一遍对代码的理解，然后追问把没吃透的地方全问出来。

### 12. 一字指令「修」
> 贴报错信息 + 只说「修」= 根因修复 + 验证，不废话。

### 13. 架构图
> 画一张架构图帮我快速理解代码库整体结构。

### 14. 进度笔记
> 给任务建笔记目录，每次提交完更新进展。

### 15. 并行执行
> 复杂任务多开 subagent 并行做。

### 16. 需求访谈
> 做新功能前先访谈我，把实现方式、交互细节、边界情况全问清楚，再动手。

### 17. 预审改动
> 审我还没提交的改动，把所有有风险的地方标出来。

### 18. 影响分析 + 存记忆
> 如果我删某个函数，告诉我哪些地方会受影响；同时把这些分析提示词存入我的记忆系统。
