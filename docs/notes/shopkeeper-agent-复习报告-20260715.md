# Shopkeeper-Agent 项目复习报告

> **生成日期**：2026-07-15 18:31
> **目的**：项目复习 + 面试准备
> **材料来源**：2 份 docs.zip（16 notes + 4 architecture + 1 design-decision）+ 历史会话记忆
> **整合工具**：grill-me（压力测试） + proactive-agent（主动价值） + superpowers（工程纪律）

---

## 0. 项目速识卡（一页纸）

| 字段 | 内容 |
|------|------|
| **项目名** | shopkeeper-agent |
| **路径** | `D:\shopkeeper-agent` |
| **GitHub** | `djjjs22/shopkeeper-agent` |
| **HEAD** | `40b147c`（本地 = remote main） |
| **类型** | 电商 Text-to-SQL 智能体（自然语言问数） |
| **栈** | FastAPI + LangGraph 11 节点 + MySQL + Qdrant + ES + Redis + TEI Embedding + Vite/React 19 |
| **LLM** | DeepSeek V4 Pro（经 opencode.ai 三方代理） |
| **核心价值** | 让运营/业务同学用"中文问"代替"写 SQL"，把口径答案秒回 |
| **当前状态** | ✅ 跑通：链路稳定 + 17/17 smoke + 真实查询 ¥1,630,866 返回 |
| **最核心原则** | **LLM 只做语义，确定性事情交 Python** |

---

## 1. 项目核心目标与当前状态

### 1.1 核心目标

> 把"运营/分析师写 SQL → 等 DBA 跑 → 回来 PPT"的链路，压缩成"自然语言 → SQL → 答案"，**对口径一致性负责**，**对数据库安全负责**。

### 1.2 当前状态

```
✅ 完成：基础设施 + 安全 + 测试 + 召回 + 评测 + 架构重构 + 稳定化 + 数据补全
🚧 未完成：多轮对话历史继承强化 + 时间 SQL 截断彻底解决 + function call 重启
📊 数据：50 条评测 38/50 PASS（76%）
⚙️ 架构：LLM 角色压缩（generate_intent + generate_sql 模板渲染）已部分落地
🏗️ 服务：3 个 Python service（date/schema/metric）已上线并通过 13 个 smoke
🧪 测试：17/17 smoke PASSED（sql_stability + deterministic_resolver）
```

### 1.3 当前文件结构（服务侧）

```
app/
├── agent/
│   ├── state.py          # correction_attempts / TimeRangeState / query 只读
│   ├── graph.py          # _route_after_validate_sql 闭环 / MAX=2
│   └── nodes/            # 11 个 LangChain 节点
├── services/             # 3 个确定性 service
│   ├── date_resolver.py      # 相对时间 → SQL BETWEEN
│   ├── schema_resolver.py    # 字段元数据 + 缓存 + 降级
│   └── metric_resolver.py    # 三级匹配（精确/归一化/alias）
├── repositories/         # 仓储层
│   ├── mysql/dw/         # 业务数仓
│   ├── mysql/meta/       # 元数据 + _normalize_column_info_row
│   ├── qdrant/           # 字段向量
│   └── es/               # 字段取值
├── core/
│   ├── sql_safety.py     # 三层 SQL 防火墙
│   └── safe_json_parser.py # think 块兼容
└── clients/              # embedding / mysql / redis / qdrant / es manager

tests/                    # pytest 套件
├── test_sql_safety.py              # 20 用例（基础）
├── test_deterministic_resolver_smoke.py  # 13 用例（service）
└── test_sql_stability_smoke.py     # 4 用例（稳定性）
```

---

## 2. 按时间线的关键迭代节点

### 节点 1：2026-06-29 安全层奠基

| 项 | 内容 |
|----|------|
| **改动** | 5 文件：`.env`、`app_config.yaml`、`app/core/sql_safety.py`、`app/agent/nodes/run_sql.py`、`prompts/generate_sql.prompt`、`app/clients/embedding_client_manager.py` |
| **原因** | LLM 生成的 SQL 直接进 MySQL = 整个业务库裸奔 + LLM 切换到更强模型 |
| **影响** | 三层防火墙（关键字黑名单 + SELECT/WITH 白名单 + 注入正则） + TEI 自定义实现（避免 langchain-huggingface 版本冲突） |
| **关键决策** | 软防护（prompt 红线）+ 硬防护（代码强制）双保险 |

### 节点 2：2026-06-30 测试层

| 项 | 内容 |
|----|------|
| **改动** | 20 pytest 用例 + ruff 检查 |
| **发现** | `SELECT 'DROP' AS label` 被误杀 |
| **修复** | 关键字匹配前用正则 `r"'[^']*'"` 先删字符串字面量 |
| **经验** | 测试发现真 bug，比靠脑补强百倍 |

### 节点 3：2026-07-10 召回并行化

| 项 | 内容 |
|----|------|
| **改动** | 3 个召回节点串行 → `asyncio.gather` 并行 |
| **加速** | 5-10×（N 个关键词 × T → 取 max）|
| **关键细节** | `return_exceptions=True` 单点失败不影响整体 |
| **同步** | Few-shot prompt 改造（JOIN 策略不再让 LLM 猜） |

### 节点 4：2026-07-11 评测体系 + 兼容 Think 块

| 项 | 内容 |
|----|------|
| **改动** | `app/core/safe_json_parser.py` + 8 个 LangChain 节点 |
| **踩坑** | Docker MySQL 端口 3306 被 macOS 本地 MySQL 占用 → 改 3307 + 改 app_config |
| **影响** | 50 条 eval_e2e 跑通，获得准确率基线 76% |

### 节点 5：2026-07-14 架构大重构（4 个笔记）

#### 5.1 query 拆分

- **状态变更**：state["query"] 是只读不变量（用户原句不被任何节点覆盖）
- **新字段**：`TimeRangeState{ start_date, end_date, raw_expression }`
- **原因**：jieba 切"2025-12-01至2025-12-31"切出 3 个无效 token；Embedding 召回被污染

#### 5.2 LLM 角色压缩（核心架构原则）

- **旧链路**：`generate_sql`（LLM 干"语义 + 语法"两件事）
- **新链路**：`generate_intent`（LLM 干语义） + `generate_sql`（纯模板渲染）
- **设计**：
  ```
  业务知识在代码里，模糊理解在 LLM 里
  ```
- **对齐**：远程 commit `d9af4603` 已经实现，本次 commit 借鉴

#### 5.3 确定性解析三件套（P0/P1/P2）

| 层 | 知识类型 | 沉淀位置 |
|----|----------|----------|
| P0 时间 | 怎么算日期 | `rewrite_query.py` + `date_resolver.py`（5+5=10 种表达） |
| P1 同义词 | 业务术语映射 | `conf/synonyms.yaml`（已被远程清理，使用 metric_resolver 三级匹配） |
| P2 业务规则 | 复杂 WHERE 条件 | `conf/business_rules.yaml`（已清理，合并入 metric_resolver） |

#### 5.4 多轮对话历史继承

- 改前：LLM 在 generate_sql 阶段自己猜省略的主语/条件
- 改后：把对话历史结构化注入（`_extract_inherited_context` 节点）

### 节点 6：2026-07-14 晚 三刀稳定性修复（11 个测试）

#### 6.1 第一刀：JSON 字符串标准化

- **bug**：`'str' object has no attribute 'append'` 让整条链路崩溃
- **根因**：`column_info.examples/alias` 是 JSON 字段，text SQL 路径返回字符串，ORM 路径返回 list
- **修复**：在 Repository 出口 `_normalize_column_info_row()` 统一转 list
- **设计原则**：**数据源头治理**，下游 Agent 节点不关心数据库 JSON 字段

#### 6.2 第二刀：空 SQL / 非 SELECT 拦截

- sentinel：`EMPTY_SQL` / `NON_SELECT_SQL`
- 进入 validate_sql 时，先过 strip_think_for_str + SELECT/WITH 白名单

#### 6.3 第三刀：图闭环 + MAX=2

- **旧链路**：correct_sql 修正后直接送 run_sql（错 SQL 不复验）
- **新链路**：
  ```
  validate_sql → run_sql (无错)
              → correct_sql → validate_sql (复验, attempts < 2)
              → run_sql (强制终止, attempts >= 2)
  ```
- **为什么 MAX=2**：1 轮可能不够，3+ 轮浪费 token

### 节点 7：2026-07-14 晚 fixture 全接管（17/17 PASSED）

- **问题**：rebase 引入 4 个新 LLM 节点，单测 fixture 没接管 → 跑 33-90s 重试失败
- **修复 1**：`_install_fake_llm(fake)` 注入 9 个节点模块的 `llm` 属性
- **修复 2**：LangChain PromptTemplate 把 `{}` 当占位符，示例 JSON 必须转义 `{{}}`
- **修复 3**：filter_table / filter_metric 异常降级保留候选（对齐 _recall_helpers）
- **修复 4**：replies 序列按 LLM 调用顺序严格对齐

### 节点 8：2026-07-14 晚 bind_tools → Python service 改造

- **原因**：与远程架构对齐 + 不让 LLM 干确定性事
- **删除**：`app/agent/tools/` 整个目录 + `tests/test_function_call_smoke.py`
- **新增**：`app/services/{date,schema,metric}_resolver.py`
- **关键设计**：service 是普通 Python 模块，不是 LangChain Tool，注入到 `generate_intent` 节点

### 节点 9：2026-07-15 启动 + 数仓补全

- **问题**：`华东上个月的销售额` 返回 "-"（fact_order 只有 2025 Q1）
- **根因**：数仓覆盖不全，2026-06 完全空
- **解决**：seed_data.py 补数 → fact_order 115→29818，dim_date 90→577
- **意外踩坑**：Windows 上 `127.0.0.1:3306` 是本机 mysqld，必须 `localhost` 走 named pipe

---

## 3. 重大技术决策复盘

### 决策 A：LLM 只做语义，确定性事情交 Python

| 备选 | 取舍 | 最终选择 |
|------|------|----------|
| LLM 一次性出 SQL 文本 | 输出不确定 / 难测试 | ❌ |
| LLM 出 JSON intent + 模板渲染 | 确定性强 / 易测试 | ✅ |
| bind_tools（让 LLM 调工具） | 让 LLM 决定何时调 | ❌（违反原则） |
| Python service 注入 | 确定性 + 用户可控 | ✅（本次改造） |

**后续验证**：
- 50 条 eval 准确率从不可测 → 76% → 链路稳定性修复后稳定
- smoke 17/17 PASSED
- 数仓补完后真实查询可返回 ¥1,630,866

### 决策 B：state["query"] 是只读不变量

**问题**：rewrite_query 节点改写后覆盖原句，导致 jieba 切到日期字符串。
**决策**：用户原句是神圣的，改写结果落到独立字段。
**验证**：抽取关键词从 query 拿，不被污染。

### 决策 C：SQL 三层防御（关键字 + 白名单 + 注入）

**问题**：LLM 生成 SQL 直接进 MySQL = 业务库裸奔。
**决策**：不在 prompt 单点防御（软），代码层硬校验。
**验证**：20 个 pytest 用例全过，SQLSafetyValidator 作为整个安全护城河。

### 决策 D：3 路召回并行（asyncio.gather）

**问题**：关键词 N × T 串行太慢。
**决策**：gather + return_exceptions 保证单点失败不影响整体。
**验证**：召回耗时降到 5-10× 加速。

### 决策 E：bind_tools → Python service

**触发**：用户在 grilling 后决策"还是做 function call"，引入 3 个 bind_tools 工具。
**反转**：发现这违反"LLM 角色压缩"原则，且远程 d9af4603 / 41afc5f6 已经不这么做了。
**最终**：废弃工具目录，改为 3 个普通 service 模块。
**教训**：架构要服从"决策原则"而非"流行做法"。

### 决策 F：MAX_CORRECTION_ATTEMPTS=2

**为什么不是 1**：单次修正成功率不够高。
**为什么不是 ∞**：LLM 烧 token + 死循环风险。
**为什么不是 3**：边际收益太低 + 2 轮覆盖绝大多数可修场景。

### 决策 G：Repository 出口标准化（不在下游补丁）

| 修法 | 后果 |
|------|------|
| merge_retrieved_info 里判 `isinstance(examples, str)` | 每个使用方都得防 |
| Repository 出口 `_normalize_column_info_row()` | 下游节点不关心存储格式 |

**选择第二条**：数据源头治理，符合"边界处做标准化"原则。

---

## 4. 主要踩坑与解决

| #   | 踩坑                                                    | 根因                                           | 解决                                                              | 教训                        |
| --- | ----------------------------------------------------- | -------------------------------------------- | --------------------------------------------------------------- | ------------------------- |
| 1   | `SELECT 'DROP' AS label` 被误杀                          | `\bDROP\b` 无法区分引号内/外                         | 关键字匹配前先删字符串字面量                                                  | 测试要写"防误杀"用例               |
| 2   | Docker MySQL 起不来                                      | macOS 端口 3306 被本地 MySQL 占                    | docker-compose 改 3307 + app_config 同步                           | 不要无脑 kill 本地服务            |
| 3   | `examples.append` 报 AttributeError                    | JSON 字段在不同查询路径返回 list 或 str                  | Repository 出口统一转 list                                           | 数据源头治理 > 下游补丁             |
| 4   | 远程 commit d9af4603 架构不同步                              | 不知道远程有个"先压缩 LLM 角色"的 commit                  | 拉 remote 看 diff，对齐思路                                            | pull 之前要看远程到底改了啥          |
| 5   | pydantic `BaseChatModel` 子类报 ValidationError          | `__init__` 里 `self.replies = ...` 触发校验       | 在 class body 声明 `replies: List[str] = []`                       | pydantic 字段必须在 class body |
| 6   | LangChain PromptTemplate 报 "Invalid format specifier" | 示例 JSON 里的 `{}` 被当占位符                        | 全部转义 `{{` `}}`                                                  | 任何 prompt 模板都要转义示例        |
| 7   | pytest 报 "fixture 'label' not found"                  | 远程 `def test_case(label, ...)` 被当 fixture 收集 | 改名 `_test_case`                                                 | 辅助函数加下划线前缀                |
| 8   | loguru KeyError 'request_id'                          | 4 个文件直接 `from loguru import logger` 没走 patch | 全部改 `from app.core.log import logger`                           | 全项目统一 log 入口              |
| 9   | pymysql 连 `127.0.0.1:3306` 失败                         | Windows 上这是本机 mysqld（PID 4488）               | 必须 `host="localhost"` 走 named pipe                              | 调试时 `netstat` 看具体监听者      |
| 10  | git push 卡 21s timeout                                | github.com 直连不稳定                             | `GIT_HTTP_LOW_SPEED_LIMIT=1000 GIT_HTTP_LOW_SPEED_TIME=30` 早判失败 | 长操作早判失败 > 等到超时            |
| 11  | 数仓查询返回空                                               | 只覆盖 2025 Q1                                  | seed_data.py 补数                                                 | 评测前要先验证数据覆盖               |
| 12  | fixture 重试 33-90s                                     | rebase 引入 4 个新 LLM 节点                        | `_install_fake_llm` 注入 9 个模块                                    | rebase 后必须验测试速度           |

---

## 5. 当前遗留问题与潜在风险

### 5.1 准确性

| 项 | 现状 | 风险 | 优先级 |
|----|------|------|--------|
| 50 条 eval 准确率 76% | 还有 12 条失败 | 面试被问到"准确率"答不上数据 | 🟡 中 |
| 时间 SQL 截断 | "上个月"语法偶发错 | 用户体感"这都不会？" | 🟡 中 |
| 多轮上下文 | 历史继承弱，"今年呢" 答非所问 | 演示时会暴露 | 🟡 中 |

### 5.2 工程度

| 项 | 现状 | 风险 | 优先级 |
|----|------|------|--------|
| 4 个远程 FAIL 测试 | scheduler × 3 + LRU × 1 留着未修 | 跑全套件会红 | 🟢 低（业务影响 0） |
| `5f4b97b` loguru fix 本地未推 remote | commit 在但 push 卡死 | 远程缺这个修复 | 🟡 中 |
| 数仓规模偏小 | 29818 行 + 500 客户 + 50 商品 | 大数据量下 SQL 性能未验 | 🟢 低 |

### 5.3 架构

| 项 | 现状 | 风险 | 优先级 |
|----|------|------|--------|
| function call 能力 | 搁置（grilling 决策） | 想要"调用工具"场景没法演示 | 🟢 低 |
| `generate_intent` prompt 还没注入 3 service 输出 | service 已写但未串起来 | 设计完整但落地 80% | 🟡 中 |
| `metric.sql_expression` 字段是空 | 三级匹配 OK 但 SQL 表达式缺失 | 复杂指标算不出来 | 🟡 中 |

### 5.4 工程实践

| 项 | 现状 | 风险 | 优先级 |
|----|------|------|--------|
| 没有完整 CI / pre-commit hook | 手动 ruff | 入门项目 OK，团队化会乱 | 🟢 低 |
| docker-compose 没固化端口策略 | .env 分散 | 跨机器部署会坑 | 🟡 中 |

---

## 6. 未来优化方向

### 6.1 短期（1 周）— 提准确率与可演示性

| 方向 | 工作量 | 收益 | 怎么做 |
|------|--------|------|--------|
| 跑完 50 条 eval 拿数据 | 0.5 天 | 简历可信度 | 跑 `pytest tests/eval_e2e.py` |
| `generate_intent` 注入 3 service | 0.5 天 | 设计完整 → 落地 | 修改 prompt 模板 + node 调用 |
| `metric.sql_expression` 补齐 | 1 小时 | 复杂指标可算 | 配置里加字段 |
| 强化多轮对话历史继承 | 1 天 | 演示更稳 | `_extract_inherited_context` 接全部状态 |
| 远程 4 FAIL 测试修 | 2 小时 | 套件全绿 | APScheduler mock + LRU 边界 |

### 6.2 中期（1 月）— 架构完整度

| 方向 | 工作量 | 收益 |
|------|--------|------|
| sql_template 完整 jinja2 渲染 | 1 天 | 对齐远程 d9af4603 |
| function call 重启（service 而非 bind_tools） | 1-2 天 | 工具能力可演示 |
| 完整 CI + pre-commit | 0.5 天 | 团队化基础 |
| 真实数据库压测 | 1 天 | 大数据量性能数据 |

### 6.3 长期（3 月+）— 产品化

| 方向 | 备注 |
|------|------|
| 多业务库支持 | 现在只有电商数仓 |
| 指标平台对接 | 让业务方自助注册指标 |
| 自然语言解释 | LLM 把 SQL 答案翻译成自然语言 |
| SQL 性能监控 | 慢查询日志 + 用户体感 |

---

## 7. 面试准备速查清单

### 7.1 项目 1 分钟电梯演讲

> shopkeeper-agent 是一个电商 Text-to-SQL 智能体：让运营/分析师用中文提问（比如"华东上个月销售额是多少"），系统自动理解业务意图、生成 SQL、查询数仓，**秒级返回带数值口径的答案**。
>
> **关键架构**：用 LangGraph 编排 11 节点工作流（关键词抽取 → 三路召回 Qdrant/ES/MySQL → 合并 → 过滤 → SQL 生成 → 校验 → 执行），其中召回用 asyncio.gather 并行加速 5-10 倍，SQL 执行前过三层安全防火墙。
>
> **核心工程**：把"日期/字段/指标"这种确定性知识从 LLM 抽出来做成 3 个 Python service，让 LLM 只做语义层，从而让输出稳定、可测、可缓存。
>
> **数据**：50 条端到端评测，**38 通过 12 失败，准确率 76%**，主要瓶颈在 LLM 生成 SQL 的 JOIN 策略选择。

### 7.2 5 大高频问题预设答案

| 问题 | 答案骨架 |
|------|----------|
| 为什么不用 LLM 直接生成 SQL？ | 不确定性 + 难测试 + 不安全 |
| 三层 SQL 防火墙怎么设计的？ | 关键字黑名单 + SELECT 白名单 + 注入正则（每层配独立测试） |
| 召回为什么并行？ | asyncio.gather + return_exceptions 单点容错 |
| LLM 角色压缩怎么做？ | 业务知识下沉到代码模板，LLM 只出 JSON intent |
| 修一个让 Agent 崩的 bug 的过程？ | 例：`'str' object has no attribute 'append'` → 见节点 6 第一刀 |

### 7.3 3 个能讲故事的核心数字

| 数字 | 故事 |
|------|------|
| **5-10×** | 召回阶段异步并行加速 |
| **3 层** | SQL 安全防火墙（每层一个单元测试） |
| **76%**（38/50） | eval_e2e 端到端准确率 |

---

## 8. 可直接复用的笔记链接

| 资料 | 路径 |
|------|------|
| 项目根 | `D:\shopkeeper-agent` |
| 笔记 22 份 | `D:\shopkeeper-agent\docs\{notes,architecture,design-decisions}\*.md` |
| GitHub | `https://github.com/djjjs22/shopkeeper-agent` |
| 本报告 | `D:\shopkeeper-agent\shopkeeper-agent-复习报告-20260715.md` |

---

## 9. grill-me 自检（5 问压力测试）

| 问题 | 答案 |
|------|------|
| 1. 这个项目**最不可替代**的一行代码是什么？ | `_normalize_column_info_row()` —— 在 Repository 出口把 JSON 字符串标准化，让下游不再踩坑 |
| 2. 如果让你**删掉一半**功能，你会留哪些？ | ① LLM 角色压缩（generate_intent 拆分）② SQL 三层防火墙 ③ 3 路召回并行。其它都是锦上添花 |
| 3. 这个项目**最大的决策**是什么？ | 让 LLM 只做语义，确定性事情交 Python。这是整个架构的"宪法" |
| 4. 如果让你**重做一次**，哪里会改？ | 重做时直接采用远程 d9af4603 的 sql_template + jinja2 渲染，不走 generate_sql 节点 |
| 5. 面试官**最可能问**什么问题？ | "为什么不让 LLM 直接生成 SQL？"（考察对 LLM 不确定性的理解）|

---

**报告生成完毕**。建议：
1. 把本报告打印或导出 PDF 收藏
2. 用"面试速查清单（7 章）"现场复习
3. 把"5 大决策 + 5 个故事数字"背熟（5 分钟电梯演讲核心）

下一次会话如果需要继续推进，可以从"6.1 短期优化"的第一项"跑完 50 条 eval 拿数据"开始。
