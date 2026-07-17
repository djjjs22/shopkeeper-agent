# docs/ - 项目文档目录

> **统一文档入口**：所有项目文档集中在这里管理（团队共享、git 追踪）

## 目录结构

```
docs/
├── README.md                         # 本文件（目录索引）
├── MEMORY.md                         # 项目长期记忆（关键决策 + 设计原则）
│
├── architecture/                     # 架构 RFC（重大设计决策）
│   ├── redis-upgrade-rfc.md
│   ├── redis-upgrade-rfc-v0.2-todo.md
│   ├── grill-me-production-pain-points-rfc.md
│   └── ai-application-pain-points-rfc.md
│
├── design-decisions/                 # 单点设计决策（较小的设计选择）
│   └── (暂无)
│
├── notes/                            # 学习笔记 / 变更记录 / 实战复盘
│   ├── PromptTemplate迁移jinja2-20260717.md   # 2026-07-17 jinja2 迁移 changelog
│   ├── 召回并行化与Prompt改造-20260710.md
│   ├── eval_e2e_think兼容改造-20260711.md
│   ├── llm角色压缩+历史继承-20260714.md
│   ├── 链路稳定性修复记录-20260714.md
│   ├── 确定性服务拆分-20260714.md
│   ├── 确定性解析三件套-20260714.md
│   ├── 测试fixture接管所有LLM调用-20260714.md
│   ├── SQL安全加固-代码学习笔记.md
│   ├── SQL安全设计决策-Grill记录.md
│   ├── SQL执行流程分析.md
│   ├── Redis升级架构改造-代码学习笔记.md
│   ├── shopkeeper-agent-复习报告-20260715.md
│   ├── 校招面试题库.md
│   ├── 藤子的Python成长笔记-全记录.md
│   └── 单元测试落地记录-20260630.md
│
└── daily-notes/                      # 当日工作日志（按日期）
    └── 2026-07-17.md                 # 2026-07-17 三方向改造 + jinja2 迁移 + SSE bug + prompt 优化
```

## 文档定位

| 类型 | 目录 | 用途 | git |
|---|---|---|---|
| 长期记忆 | `docs/MEMORY.md` | 关键决策索引（AI 助手必读 + 团队参考） | ✅ |
| 架构 RFC | `docs/architecture/` | 重大设计决策的完整文档 | ✅ |
| 设计决策 | `docs/design-decisions/` | 较小的单点决策 | ✅ |
| 笔记 / 变更记录 | `docs/notes/` | 学习笔记、实战复盘、changelog | ✅ |
| 当日工作日志 | `docs/daily-notes/` | 每天的工作记录（按日期） | ❌（私有） |

## 文档命名规范

- **RFC / 决策**：`docs/architecture/<主题>-rfc.md` 或 `docs/design-decisions/<主题>.md`
- **变更记录**：`docs/notes/<主题>-YYYYMMDD.md`（按日期）
- **工作日志**：`docs/daily-notes/YYYY-MM-DD.md`（按日期）

## 与 .workbuddy/memory/ 的关系

- **`docs/`**：团队共享文档（git 追踪），给团队成员 + AI 助手 + 面试官看的
- **`.workbuddy/memory/`**：workbuddy AI 助手自动加载的精简版（不公开），保证每次会话能快速进入上下文
  - `2026-07-17.md`（daily log，完整版镜像）
  - `MEMORY.md`（精简版索引，≤3000 字符）

## 如何选择文档类型

| 你想记录的是... | 放到 |
|---|---|
| 重大架构变更、多方案对比、需要评审 | `docs/architecture/` |
| 某个具体设计的小决策（比如"为什么用 jinja2"） | `MEMORY.md` + 一行简述 |
| 一次 bug 修复的过程复盘 | `docs/notes/<主题>-YYYYMMDD.md` |
| 一天工作的完整流水（含踩坑、决策、命令） | `docs/daily-notes/YYYY-MM-DD.md` |
| 学习某个概念 / 库 / 工具的笔记 | `docs/notes/<主题>.md`（无日期） |