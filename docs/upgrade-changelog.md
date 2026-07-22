# 升级实施记录（Memory + Eval + 可观测性 + 数据飞轮）

> 对应 `docs/AI应用架构升级路线.md` 第 4/5 章的落地记录。
> 每个 Phase 完成后更新此文档，记录改动 + 验证结果 + 实际 ROI。

---

## Phase 0：基础设施（2026-07-22 完成）

### 改动
- **4 张新表**：`user_profile` / `sql_pattern` / `query_log` / `bad_case`（meta MySQL）
  - DDL 脚本：`app/scripts/init_upgrade_tables.py`（幂等，可重复跑）
  - ORM 三层：entity + model + mapper（照抄 column_info 范式）
- **LangSmith 接入**：env 配置（默认 false 零侵入），lifespan 启动日志
- **修复 bug**：`session_store.py:130` 的 `dict.popitem(last=False)` 在 Python 3.14 报 TypeError（OrderedDict 语法误用）

### 验证
- 4 张表 CRUD 真机通过（docker mysql 3307）
- session_store 测试从 17 passed/1 failed → 18 passed

---

## Phase 1：Eval 地基（2026-07-22 完成）

### 改动
- **修 6 类数据 bug**：微信支付漏 WHERE、电脑品类对应家用电器、上月日期越界（3 处）、最近7天/上周开区间、4 条多轮缺 multi_turn、第 1 条多轮设计错误（删除）
- **收紧 sqlglot AST 相似度**：
  - SELECT 加聚合函数比对（SUM vs AVG 不再假绿）
  - WHERE 加值级比对（EQ/GTE/GT/LTE/LT/BETWEEN/IS NULL/IN 全覆盖，华东 vs 华南不再假绿）
- **启用 execution match**：gold + generated 两条 SQL 都跑 DW MySQL 比对结果集（BIRD-SQL 金标准）
- **补 10 条空白场景**：同比/环比、HAVING、窗口函数、NULL、NOT IN、LIKE、UNION、同义指标、嵌套子查询、跨3轮指代
- **🔴 重大发现**：sqlglot 根本没装 → 之前所有相似度都是 0.0，eval 全是假数据。补依赖 + 装 venv

### 数据集
- 50 条 → 59 条（删 1 错误 + 补 10 新场景）

### 验证
- 8 个 anti-green 场景验证：完全匹配 1.0、SUM vs AVG 0.7、不同表 0.7
- compare_results：标量 1% 容差、多行乱序、Decimal 处理全对

### ⚠️ 待跑 baseline
完整 eval_e2e 需要 LLM API key（59 条 × 3s = ~3 分钟）。待有 key 后跑一次生成 `tests/results/baseline.json`。

---

## Phase 2：可观测性 + 飞轮信号源（2026-07-22 完成）

### 改动
- **LangSmith metadata**：`llm.py` with_config 加 `langsmith_metadata`（trace UI 可按 profile 筛 cheap/strong）
- **bad_case_collector**：模块级单例，30s 去重窗口，fire-and-forget
  - 4 处埋点：validate_sql（sql_fail）、correct_sql（sql_fail）、reviewer（review_low）、feedback 端点（user_thumb_down）
- **query_log_service**：query_service 成功后记录（session/query/success/latency）
- **feedback 端点**：`POST /api/query/feedback`（rating up/down，down → 归集 bad_case）

### 验证
- bad_case_collector 真机写入 + 去重（连续 record 3 次 → 只写 1 条）
- query_log 真机写入
- feedback 端点：down→200 recorded=True / up→200 recorded=False / 非法→422
- 现有测试 49 passed

---

## Phase 3：Memory A — SQL Pattern Bank（2026-07-22 完成）⭐⭐⭐ 最高 ROI

### 改动
- **PatternQdrantRepository**：sql_pattern_collection（UUID id 确定性生成，幂等）
- **pattern_bank_service**：
  - ingest_one：抽模板（`_extract_template`：值→占位符）+ 抽标签（`_extract_tags`：join/having/window 等 15 种）+ 双写 MySQL + Qdrant
  - retrieve_topk：query→embedding→Qdrant 召回→MySQL 取全文→按 confidence 排序
  - ingest_from_query_log：扫成功 SQL 批量入库（供 scheduler 周期调用）
- **generate_intent 注入**：节点内部召回 top-3 + `_format_sql_patterns_for_prompt` + prompt 加 `{{ sql_patterns }}` 槽位
- **build_pattern_bank 脚本**：从 gold_dataset 全量构建

### 修复的 bug
1. `embed_query` 在 async 上下文崩 → 改用 `aembed_query`
2. Qdrant point id 不接受字符串 → uuid5 确定性生成
3. retrieve_topk client 未 init → lazy init

### 验证
- **59 条 gold 模板全部入库**（MySQL 59 + Qdrant 59）
- 召回语义精准：4 条测试 query 全部召回相关模板（最高 score 0.85）
- 标签抽取：join/group_by/having/subquery/is_null/order_by/limit 全识别

---

## Phase 4：Memory B/C — User Profile + 会话摘要（2026-07-22 完成）

### 改动
- **user_profile_service**（Semantic Memory）：
  - 规则抽取：5 种维度（region/category/member_level/month/payment_method）+ 4 种术语（动销率/复购率/客单价/GMV）
  - 置信度提升：首次 0.5 → 连续命中 +0.2 → 0.9 达注入阈值
  - 矛盾更新：同 type 不同 content → 重置 0.5
  - 保守注入：confidence ≥ 0.9 才进 prompt
- **session_summarizer**（Episodic Memory）：
  - 5 轮（10 条消息）触发 cheap LLM 摘要
  - 保留近 4 条原文 + 1 条 [摘要] 替换早期历史
- **generate_intent 注入**：加 `{{ user_preferences }}` 槽位

### 修复的 bug
1. 置信度不累加：循环内重读 existing 受未 commit 事务影响 → 循环前读一次 + 内存 dict 跟踪
2. "各地区"不匹配 region 规则 → 扩正则

### 验证
- 置信度提升链路：3 次连续 region → 0.5→0.7→0.9
- generate_intent 集成（sql_patterns + user_preferences 双注入）
- 现有测试 55 passed

---

## Phase 5：Eval A Component Eval + Memory D 遗忘机制（2026-07-22 完成）

### 改动
- **memory_decay_service**：统一三类衰减
  - SQL Pattern：30 天零命中归档 + online 低命中降权（gold 不动）
  - User Profile：confidence < 0.3 删除
  - Session Summary：Redis TTL 24h 自动过期（no-op）
- **scheduler 加 03:00 cron**：`_safe_memory_decay`（在归档 02:00 之后）
- **eval_recall 重写**：删 mock_recall，接真实三路召回（jieba 关键词 + Qdrant/ES）
- **eval_intent 新建**：60 条意图分类评测（chitchat/metadata_query/data_query 各 20）

### 验证
- memory_decay 真机：低置信偏好被删（deleted=1），sql_pattern gold 不动
- eval_recall jieba 关键词抽取 + real_recall 函数
- eval_intent 60 条 3 类各 20

---

## Phase 6：CI 回归门禁（2026-07-22 完成）

### 改动
- **`.github/workflows/eval.yml`**：PR 触发，跑 eval_e2e → 对比 baseline → 准确率 ↓>2% 阻断
  - 含 mysql/redis/qdrant/es services 容器
  - 依赖 GitHub Secrets 注入 LLM/DB 连接信息
- **`tests/scripts/compare_to_baseline.py`**：
  - 自动找最新 eval_e2e_*.json
  - 总准确率 + 按难度双维度对比（避免简单 case 涨掩盖复杂 case 跌）
  - 无 baseline 不阻断（首次运行）

### 验证
- 4 个场景：持平 pass、下降3% fail、上升 pass、总持平但复杂类回归 fail

---

## 总览：Memory 三层 + Eval 三层 + 飞轮闭环

```
Memory 三层（全落地）：
  Procedural (Pattern Bank)   ← Phase 3，59 条 gold 模板，召回注入 generate_intent
  Semantic (User Profile)     ← Phase 4，规则抽取 + 置信度提升
  Episodic (Session Summary)  ← Phase 4，5 轮触发压缩

Eval 三层：
  Component (eval_recall/intent) ← Phase 5，真实召回 + 意图分类
  End-to-End (eval_e2e)          ← Phase 1，execution match + 收紧 AST
  Online (数据飞轮)              ← Phase 2，bad_case + query_log + feedback

飞轮闭环：
  线上查询 → bad_case/query_log 归集 → Pattern Bank 消费 → 注入 prompt → 准确率提升
                                              ↑
                                         Memory 衰减（Phase 5，03:00 cron）

可观测性：
  LangSmith trace（Phase 2，env 配置零侵入，覆盖 17 节点 + multi-agent）
```
