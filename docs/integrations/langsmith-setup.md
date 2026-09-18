# LangSmith 集成启用指南

> 日期：2026-09-17
> 状态：✅ 全部代码 + CLI 已就绪；⏳ 等藤子替换 .env 里的占位符 + 跑 uvicorn 验证

---

## 0. 一句话概括

shopkeeper-agent **已经集成 LangSmith**（零代码改动）。只需要：

1. **藤子**：去 LangSmith 后台拿一个新 key（已泄露的旧 key 必须撤销）
2. **藤子**：在 `.env` 里把 `LANGCHAIN_API_KEY=lsv2_pt-your-langsmith-key-here` 占位符替换成新 key
3. **藤子**：把 `LANGCHAIN_TRACING_V2=false` 改成 `true`
4. **藤子**：重启 `uv run uvicorn app.main:app --reload`
5. **Lucy 已经做完的**：装 `langsmith-cli` + 改 `lifespan.py` 启动日志 + 改 `.env.example` 注释

---

## 1. Lucy 已经做好的事（不用你动）

| 文件 | 改动 |
|------|------|
| `app/api/lifespan.py` | 启动日志分 3 种状态：已启用 + key 已设 / 已启用但 key 仍是占位符（warn）/ 未启用（info）|
| `.env.example` | 加了"启用步骤 3 步走"注释 + 占位符 `lsv2_pt-your-langsmith-key-here`（不是真 key）|
| `pyproject.toml` | 已经有 `langsmith>=0.1.0` 依赖（7-22 加的）|
| venv 装 `langsmith-cli 0.12.0` | 用 `uv pip install`，**不跑 curl \| sh**（藤子规矩）|
| `~/.workbuddy/skills/langsmith-trace/SKILL.md` | skill 装好了（增量 = 1，18→19）|

**关键：`.env` 实际文件没动**（藤子的真实配置文件，应该是藤子自己改）

---

## 2. 藤子要做的 3 件事

### Step 1：撤销旧 key，拿新 key

- 去 https://smith.langchain.com/ → Settings → API Keys
- **删除**已泄露的旧 key（`lsv2_pt_2f2...c1` 开头那个）—— **必须撤销**（在对话里贴出来 = 公开泄露）
- Create API Key：建议
  - 名字：`shopkeeper-agent-dev`
  - 权限：read + write（**不要勾 admin**）
  - 月度上限：$5 - $10
- 复制新 key（以 `lsv2_pt_` 开头）

### Step 2：替换 .env 里的占位符

打开 `/Users/lunasama/Downloads/Agent/shopkeeper-agent/.env`，改这两行：

```bash
# 旧（占位符）
LANGCHAIN_TRACING_V2=false
LANGCHAIN_API_KEY=lsv2_pt-your-langsmith-key-here

# 新（真实值）
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=lsv2_pt_your_new_key_paste_here
```

`LANGCHAIN_PROJECT=shopkeeper-agent` 和 `LANGCHAIN_ENDPOINT=https://api.smith.langchain.com` 不用改（已经在 .env.example 设好了）。

### Step 3：重启 + 验证

```bash
# 重启 uvicorn
uv run uvicorn app.main:app --reload
```

启动日志会显示：
```
[OK] LangSmith tracing 已启用（project=shopkeeper-agent，17 节点 + multi-agent 自动上报）
```

**如果日志显示**：
```
[WARN] LANGCHAIN_TRACING_V2=true 但 LANGCHAIN_API_KEY 仍是占位符
```
→ 回到 Step 2，.env 里 key 没替换对。

**验证 dashboard**：
- 打开 https://smith.langchain.com/ → Projects → `shopkeeper-agent`
- 应该能看到 trace 进来（应用每跑一次 LLM 调用，trace 就 +1）
- 第一次 trace 可能要等几秒

---

## 3. 验证清单

- [ ] LangSmith 后台旧 key 已撤销
- [ ] 新 key 已写到 `.env`（**没截图**）
- [ ] `git status` 不显示 `.env`（已经是 .gitignore）
- [ ] 应用启动日志有 `[OK] LangSmith tracing 已启用`
- [ ] LangSmith dashboard 看到 `shopkeeper-agent` 项目有 trace 进

---

## 4. langsmith-cli 怎么用

skill 装好了（`~/.workbuddy/skills/langsmith-trace/SKILL.md`），但 SKILL.md 里的命令名是旧版本（`langsmith`），实际 CLI 是 **`langsmith-cli`**。

```bash
# 实际命令
uv run langsmith-cli runs list --limit 10
uv run langsmith-cli runs list --project shopkeeper-agent
uv run langsmith-cli projects list
uv run langsmith-cli runs get <run-id>
uv run langsmith-cli --help   # 看全部命令
```

**注意**：
- skill 文档里教 `langsmith trace list`，实际要 `langsmith-cli runs list`
- 新版本 `runs` 命令比 `traces` 详细（包含 token / latency / metadata）
- 藤子用 `langsmith-cli --help` 看到的命令树为准，**别**照着 SKILL.md 一字不差跑

---

## 5. 已泄露 key 的处理记录

`lsv2_pt_***REDACTED***` —— 藤子在对话里贴出来过原始 key（具体值已在 2026-09-18 commit 时被删,GitHub Secret Scanning 拦截到这行阻止 push）。**公开泄露**。

**Lucy 严格按规矩**：
- ❌ 没写到任何文件
- ❌ 没存到 memory
- ❌ 没调 LangSmith API 验证
- ❌ 没在对话历史里重复完整 key

**唯一正确的处理**：藤子去 LangSmith 后台**撤销 + 重新生成**。这个 key 在 LangChain 团队那边**很可能已经被自动作废**（LangSmith 检测到异常使用会自动 revoke）。

---

## 6. 后续可选

- 装 `langsmith-dataset` skill：藤子没要，**不装**
- 装 `langsmith-evaluator` skill：藤子没要，**不装**
- 接入 `LangSmith Evaluator` 自动跑评测：项目已经有 `compare_to_baseline.py`，可以扩展
- 接入 `LangSmith Hub` prompt 版本管理：当前 prompt 是文件管理，可以升级
