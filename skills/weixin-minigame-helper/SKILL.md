---
name: weixin-minigame-helper
version: 1.0.0
description: |
  微信小游戏开发工具包（通用 Skill 版）：AI 调试、预览、运行、真机预览、上传发布微信小游戏。
  适用于任何支持 MCP（Model Context Protocol）的 AI 编程助手（CodeBuddy / Cursor / Claude Desktop / Windsurf / Continue 等）。

  ## 何时触发
  当用户正在开发微信小游戏，或用户提到"小游戏"、"游戏"相关内容时，本 Skill 生效。

  ## 触发关键词（匹配任意一个即触发）
  - **预览/运行类**：预览、帮我预览、运行、跑一下、看看效果、看下效果、试试、启动、打开游戏、运行游戏、预览游戏
  - **代码修改后自动触发**：每当为用户生成、修改、调试了小游戏代码（任何 .js/.json/.css 文件变更），代码写入完成后 → **必须**自动调用 `run_game`（首次）或再次调用 `run_game`（已运行，幂等刷新）触发预览，无需等待用户指示
  - **真机类**：真机预览、扫码体验、手机上看看
  - **上传类**：上传、发布、上传开发版、上传体验版、发布到微信
  - **截图/视觉检查类**：截图、截屏、看看画面、游戏画面、screenshot、看看游戏长什么样、画面是什么样的
  - **游戏创作/修改完成后**：当游戏代码创作或修改完成并成功运行后，如果需要确认游戏画面是否符合预期，可以调用 `capture_screenshot` 获取当前游戏画面进行视觉检查

  ## 触发后执行
  1. 代码修改后 → 自动 `run_game` + 按"档 1 host 内置 → 档 2 VSCode 内置 Simple Browser（VSCode 系专用，写 `.vscode/launch.json`，让用户按 F5）→ 档 3 系统默认浏览器"打开预览 → 等待片刻后 `get_logs` 检查日志 → 若有错误则修复代码并再次 `run_game` + 检查日志，循环直到无错误（同一错误修复超过 5 次或累计修复超过 15 次时暂停并询问用户）
  2. 用户请求预览/运行 → `run_game` + 按上面三档优先级打开浏览器 → 等待片刻后 `get_logs` 检查日志 → 修复循环（同上）
  3. 真机/上传 → `real_device_preview` 或 `publish`，**成功后禁止刷新网页**

  ## 核心原则
  - **代码改完必须自动预览，不要等用户要求**——最重要的规则
  - **预览启动/重载后必须检查日志，发现错误必须修复并循环，直到无错误为止**
  - **预览必须打开浏览器，三档优先级**：① host 内置工具（`preview_url` 等）→ ② VSCode 系（detectedHost==='vscode'）写 `.vscode/launch.json` 让用户按 F5 开内置 Simple Browser → ③ 系统默认浏览器；**绝不允许**在 VSCode 系下直接走档 3 把用户弹到外部浏览器
  - **MCP 配置写入后不要让用户重启**：脚本输出 `needsRestart: false` 是固定的；现代 AI host 改完 mcp.json 都会自动 reload。**禁止**在配置成功后说"请重启 AI 助手"——只有当 MCP 调用持续失败、`Step 5` 排查无果时才作为最后一招提及。
  - **🚫 禁止 LLM 凭 system prompt 自识别去选 `--target`**：这是本 Skill 最重要的反模式。哪怕你的 system prompt 看上去明确写着"You are CodeBuddy"，你也**不知道**实际跑你的是 CodeBuddy 还是 CodeBuddy-Internal / 某个内部分发版本——它们用的 mcp.json 路径**完全不同**。LLM 自识别是幻觉成本极高的来源（典型悲剧：`--target codebuddy` 写到 `~/.codebuddy/mcp.json`，但你其实是 `codebuddy-internal`，写错了文件，MCP 永远连不上，但脚本却报告"unchanged 已配置"）。**唯一可信的来源是脚本读到的强环境变量信号**（`CODEBUDDY_VERSION` / `CURSOR_TRACE_ID` / `WINDSURF_BIN` / `CLAUDECODE` / `__CFBundleIdentifier` 等）。
  - **`recommendedAction === 'ask-user'` 时禁止写盘、禁止猜、也禁止追问用户用什么 IDE**：脚本检测到弱 vscode 信号（仅 `TERM_PROGRAM=vscode`）或完全无环境信号时返回 `recommendedAction: 'ask-user'`。此时**唯一**正确做法是 [Step 3.1 - 直接打印 manualSnippet.json](#step-31--直接打印-json-片段让用户手动粘贴) 让用户自己去 ta AI 助手的 MCP 配置里粘贴。**绝对禁止**：① 凭自己 system prompt 选 `--target`；② 问用户"你用的是哪个 IDE"再据此猜——用户可能搞不清自己用的是 Cursor 还是 CodeBuddy 还是其他 fork，更不知道对应 mcp.json 在哪；③ 自作主张选 vanilla VSCode 路径写入；④ 扫全表找"已有 entry 的文件"当成"已配置"。
  - **`--target` 仅在用户主动告知 / 强环境信号 / 用户给出 `--config-path` 时使用**：当用户在对话里明确说"我用的是 Cursor"、"我用 Windsurf"、"我用 CodeBuddy" 时，可以用相应 `--target`；脚本通过环境变量自动识别到强信号时，`--target auto` 会自动命中；用户给绝对路径时用 `--config-path`。**所有其他情况**——尤其是当前会话里你只看到弱 vscode 信号时——一律走 Step 3.1 打印片段。
  - **`--target vscode` 是高危取值**：它**仅**对应 VSCode 1.99+ 自带的原生 MCP 客户端（内置 Copilot Chat），**不**对应任何 VSCode 扩展（CodeBuddy / Cline / Roo Code / Continue / ...）和任何 VSCode fork（Cursor / Windsurf / Trae / VSCodium）。脚本检测到这个 target 时**始终**输出 `targetHostCaveat` 警告。看到 caveat 就说明你**几乎一定写错了文件**——立即放弃，转走 Step 3.1 打印片段。
  - **MCP 不可用时禁止给"替代方案"**：当 MCP 没配好，唯一合法的两种回应：① 强环境信号 → 用 `--target auto` 直接写入；② 弱/无信号 / 用户给路径失败 → 打印 `manualSnippet` 让用户手动粘贴并**停止流程**等用户配好。**绝不允许**说"那我直接用 shell 命令帮你启动预览"、"我可以写 launch.json 让你按 F5"、"要不你看下要哪种方案" 等替代路径——本 Skill 的所有功能（`run_game` / `get_logs` / `capture_screenshot` / `real_device_preview` / `publish`）都是 **MCP-only**，没有 CLI 等价物。
  - 真机/上传后禁止刷新（二维码会消失）
  - 缺少 AppID/密钥时引导用户配置，禁止跳过
  - **首次会话先试一次 MCP 调用，调不通再按 Step 1 检查 / 配置；不需要每次会话都先跑 `node --version` 等前置检查**
allowed-tools: preview_url, mcp__weixin-minigame-helper__run_game, mcp__weixin-minigame-helper__get_logs, mcp__weixin-minigame-helper__capture_screenshot, mcp__weixin-minigame-helper__real_device_preview, mcp__weixin-minigame-helper__publish
---

# 微信小游戏 — 通用 Skill 使用指南（基于 MCP）

本 Skill 不依赖任何特定的 IDE 插件体系，仅依赖：
1. **Node.js 运行时**（≥ 18.x）。
2. 一个标准 MCP 服务（`@weadmin/weixin-minigame-helper-mcp`）。
3. 本目录下的 `scripts/` 辅助脚本（用于环境检查、安装、配置 MCP、兜底打开浏览器）。

> **AI 助手须知**：所有指令应严格按照本文件描述执行，**不要省略前置环境检查**。
> 所有"运行脚本"的操作都直接通过 shell 执行，使用脚本输出的 JSON 字段作为决策依据，**不要把脚本源码读进上下文，浪费 token**。

---

## 🚨 启动前置检查（仅当 MCP 调用失败时才需要做）

> **重要**：MCP 调用本身就是最可靠的前置检查。**不要**在每次会话开始时主动跑 `node --version` / `check-mcp.mjs` 等检查脚本——直接 [Step 0](#step-0--直接尝试调用-mcp最快路径) 试一次 MCP，能调通就完事。只有在 Step 0 失败、且本会话**还没**做过环境配置时，才往下走 Step 1 ~ Step 5 这套**逐步退化、最少重复**的诊断流程。

> **关于 Node 环境**：所有 `scripts/*.mjs` 自身依赖 Node ≥ 18。**不需要**把 `node --version` 当成独立的前置步骤跑——
> - 如果 Node 没装：`check-mcp.mjs` 等脚本一调就 `command not found`，shell 报错本身就是足够清晰的信号；此时（也只有此时）才向用户给出 Node 安装指引：
>   > "本 Skill 需要 Node.js ≥ 18。安装方式：macOS `brew install node@20`；Windows 访问 https://nodejs.org/ 下载 LTS；Linux 用包管理器或 `nvm install --lts`。装完后重启终端 / IDE 再发请求。"
> - 如果 Node 太旧：`check-mcp.mjs` 自己会返回 `nodeOk: false` + `recommendedAction: 'install-node'`，按那条分支处理即可。

### Step 0 — 直接尝试调用 MCP（最快路径）

直接尝试调用 `run_game` / `get_logs` 之类的 MCP 工具：
- **调用成功** → MCP 已就绪，跳过下面所有检查，直接执行用户请求。
- **调用失败 / 工具不存在** → 进入 Step 1。

> 一旦本会话内 Step 0 成功过一次，整个会话内不需要再做任何检查。

### Step 1 — 检查 MCP 是否已"配置 + 安装"

执行：

```bash
node "${SKILL_DIR}/scripts/check-mcp.mjs" --json
```

`SKILL_DIR` 是本 Skill 目录的绝对路径（多数 host 会以环境变量或工作目录形式提供；如果不确定，用 `dirname` 解析当前 SKILL.md 所在目录）。

脚本输出 JSON，关键字段：

| 字段 | 含义 |
|------|------|
| `nodeVersion` / `nodeOk` / `nodeMinMajor` | Node 实际版本与是否达标（双重保险） |
| `detectedHost` / `detectedHostEvidence` / `detectedHostStrong` | 通过环境变量识别出的当前 AI host。`detectedHostStrong === true` 时是强信号（如 `CODEBUDDY_VERSION` / `CURSOR_TRACE_ID` 等）；`'vscode'` 必然是弱信号（`TERM_PROGRAM=vscode` / `VSCODE_PID`，**身份不明**）；`null` 表示无任何信号 |
| `targetHost` | 脚本**实际检查的 host**。强信号时等于 `detectedHost`；显式 `--target X` 时等于 `X`；显式 `--config-path` 时为 `'custom'`；**弱 vscode / 无信号时为 `null`**（脚本拒绝猜，要求 LLM 问用户） |
| `targetSelectionReason` | 取值之一：`explicit-config-path` / `explicit-target` / `strong-env-signal` / `weak-vscode-need-user-input` / `no-env-signal-need-user-input` |
| `targetSelectionHint` | 当 `targetHost === null` 时，给 AI 助手的具体说明，可直接转述给用户 |
| **`targetHostCaveat`** | **高危警告**：非 `null` 时表示当前选的 `--target` **可能不是你这个 AI 助手该用的**。两种触发：(a) `targetHost === 'vscode'` —— `vscode` **仅**指 VSCode 1.99+ 自带的原生 MCP 客户端，**不**包含任何 VSCode 扩展（CodeBuddy / Cline / Roo Code / Continue ...）和任何 VSCode fork（Cursor / Windsurf / Trae / VSCodium）；(b) `--target` 显式指定但环境无强信号确认。**看到 caveat 时**：**直接放弃 `--target` 这条路**——不要凭 system prompt 重新自识别（典型悲剧：system prompt 写"CodeBuddy"但实际是"CodeBuddy-Internal"，写错文件后 MCP 永远连不上但 `configured` 仍报 true），不要追问用户"你用的是哪个 IDE"，**直接转 [Step 3.1 - 直接打印 manualSnippet.json](#step-31--直接打印-json-片段让用户手动粘贴)** 把片段贴给用户让 ta 自己粘配置即可 |
| `configured` | **只统计 `targetHost` 对应的那 1-2 个文件**是否已含 entry。`targetHost === null` 时为 `null`（未知，不要做"已配置"假设）。**注意**：`configured: true` + `targetHostCaveat !== null` 是个陷阱——配置存在但**很可能不是给你这个 AI 用的**，直接按 caveat 转 Step 3.1 |
| `installed` | npm 包 `@weadmin/weixin-minigame-helper-mcp` 是否已可解析 |
| `entryDrift` | 已存在的 entry 与期望的 `command/args` 是否一致 |
| `desiredEntry` | 当前期望的 MCP 启动命令（`{command, args}`），用于 diff 对照 |
| `configCandidates[]` | **只包含 `targetHost` 的候选**（最多 user + workspace 两条；显式 `--config-path` 时只有 1 条；`targetHost === null` 时为空数组）。每项含 `host` / `path` / `scope` / `exists` / `hasEntry` / `entryConfig` / `drift` / `driftReason` / `customTransport` / `customCommand` / `lastModifiedMs`。**不会再返回所有 host 的全量列表** |
| `recommendedAction` | `none` / `install-node` / `install` / `configure` / `configure+install` / `reconfigure` / **`ask-user`**（弱/无信号 → 必须走 Step 3.1 打印片段，**不要写盘也不要追问 IDE**） |
| `recommendedTarget` / `recommendedConfigPath` | 推荐 `configure-mcp.mjs` 操作的目标 host 与文件路径（`ask-user` 时均为 `null`） |
| `manualSnippet` | **手动粘贴兜底**：`{ json, entry, instructions }`。`json` 是可直接复制粘贴到任何 host 的 `mcp.json` 片段；`instructions` 是给用户的步骤说明。**`ask-user` 分支必用**——直接整段贴给用户 |

根据 `recommendedAction` 进入下面的对应分支。**只做必要的一步，不要盲目重复**。

> **`targetHostCaveat` 触发时**：直接放弃 `--target` 这条路，**不要重新自识别 / 不要追问用户用什么 IDE**。出现 caveat 几乎必然意味着你刚才用 LLM 自识别（错误）选了 `--target`，或者用户给了一个无强信号确认的 host。立即转走 [Step 3.1 - 直接打印 manualSnippet.json](#step-31--直接打印-json-片段让用户手动粘贴) 让用户自己粘配置即可——这是**绝对不会出错**的回退路径。

| recommendedAction | 操作 |
|-------------------|------|
| `none` | 配置文件层面就绪。**注意**：你能进到这一步说明 Step 0 的 MCP 调用失败过——`configured: true` 仅代表 mcp.json 里有正确的 entry，**不代表 AI host 真能 spawn 起来**。此时直接跑 [Step 4.1 verify-mcp](#step-41--真启动验证verify-mcpmjs) 真启动一次。`ok: true` → 大概率是 AI host 还没 reload mcp.json，回 Step 0 再试一次即可；`ok: false` → 看 `stage`/`hint` 排查。如果 verify 也通过、Step 0 调用却仍失败 → 说明你 `check-mcp` 看的 mcp.json **不是当前 AI host 真正读取的那一个**（典型：脚本用 `--target codebuddy` 看了 `~/.codebuddy/mcp.json`，但你实际是 `codebuddy-internal` 这种用不同路径的变体；或者 `customTransport: true` 那种 host 自家网关的 entry 又恰好挂了）。**不要再试 `--target`，转 [Step 3.1](#step-31--直接打印-json-片段让用户手动粘贴) 直接给用户片段** |
| `install-node` | Node 缺失 / 版本过低。直接告诉用户安装 Node ≥ 18（指引参见本文件顶部"关于 Node 环境"小节），停止流程 |
| `install` | Step 2 |
| `configure` | Step 3 |
| `configure+install` | 先 Step 2 再 Step 3 |
| `reconfigure` | **配置漂移**：直接走 Step 3，`configure-mcp.mjs` 默认会自动覆盖（保留用户自定义的 `env`/`cwd` 等辅助字段） |
| **`ask-user`** | **不要写盘、不要凭 system prompt 自识别选 `--target`、不要追问用户用什么 IDE**。脚本无法识别当前 AI host，唯一安全做法是 [Step 3.1](#step-31--直接打印-json-片段让用户手动粘贴)：把 `manualSnippet.json` 整段贴给用户，附常见 host mcp.json 路径参考列表，让用户**自己**找到对应文件粘贴，配置完成后告诉你，再回 Step 0。**例外**：如果用户已经在对话里明确说过"我用 Cursor / Windsurf / CodeBuddy / Trae"等具体品牌（不是 IDE 名字），可以用 `--target <host>` 重跑；其他情况一律走 3.1 |

### Step 2 — 按需安装 MCP

仅当 `installed === false` 时执行：

```bash
node "${SKILL_DIR}/scripts/install-mcp.mjs" --json
```

成功后输出 `{"ok":true,"resolvedPath":"..."}`。失败则输出 `{"ok":false,"error":"..."}`，应原样转述给用户并停止流程。

### Step 3 — 按需配置 / 修复配置 MCP

当 `recommendedAction` 为 `configure` / `configure+install` / `reconfigure` 时执行：

```bash
node "${SKILL_DIR}/scripts/configure-mcp.mjs" --target auto --json
```

`--target auto` 的探测策略**严格**遵循"宁可不写、绝不乱写"：
1. **强信号环境变量**（`CODEBUDDY_*` / `CURSOR_*` / `WINDSURF_*` / `CLAUDECODE` / `__CFBundleIdentifier` 等）→ 直接写入对应 host 的配置文件。
2. **弱 vscode 信号**（仅 `TERM_PROGRAM=vscode` / `VSCODE_PID`，没有任何 fork 专属强信号）→ **拒绝写盘**，返回 `ok:false, ambiguous:true`，由你（AI 助手）问用户。
3. **完全无环境信号** → 同样**拒绝写盘**。
4. **不再做"扫描所有 host 文件按分数取最高"的兜底**——之前那个逻辑会在 codebuddy 文件里有 entry、用户其实在用 Cursor 时误判"已配置"。

也可以指定具体 host：

```
--target codebuddy | codebuddy-workspace | cursor | cursor-workspace |
         claude-desktop | claude-code | windsurf | continue |
         vscode | vscode-cline | vscode-roo-code | trae
--config-path /abs/path/to/mcp.json    # 完全自定义路径，最高优先级
```

> **`--allow-ambiguous`** 是一个 opt-in 的逃生舱：当用户和你都不确定 host、又不想再问时，加这个 flag 会回退到"扫描全表按分数取最高"的旧逻辑写入。**不推荐**，仅在用户明确同意的情况下使用。

**关于配置漂移与覆盖：**
- 已存在的 entry **完全匹配** desired（`command` + `args` 一致）→ `action: "unchanged"`，不写盘。
- 已存在的 entry `command` 一致但 `args` 不同（典型：被旧版本号锁死、`--prefer-online` 被去掉）→ 默认自动覆盖（`action: "overwritten"`），同时**保留用户自定义的 `env` / `cwd` / `disabled` / `description` 等辅助字段**，只重置 `command` + `args`。
- 已存在的 entry **使用 url / sse / http 传输**（`customTransport: true`，例如本地开发期 `{"url": "http://127.0.0.1:43210/mcp"}`）→ **不算 drift**，绝不覆盖，视为用户主动定制。
- 已存在的 entry **使用不同的 `command`**（`customCommand: true`，例如指向本地 checkout：`node ./dist/server.js`）→ **不算 drift**，绝不覆盖。
- 不希望自动修复 args 漂移时，可加 `--no-drift-fix`，脚本会以错误退出并把 `previousEntry` 输出给你看。
- 完全不存在 entry → 创建/合并写入 entry（`action: "created"` / `"merged"`）。

> **要点**：drift 检测**只**修被锁死的旧版本号、缺失的 `--prefer-online` 这种"没人主动维护就会出问题"的情况；用户的 URL / 自定义命令一律保留。要把 URL 改回 npx，必须显式 `--force`。

成功后输出：

```json
{
  "ok": true,
  "host": "codebuddy",
  "configPath": "/Users/.../.codebuddy/mcp.json",
  "needsRestart": false,
  "action": "overwritten",
  "previousEntry": { ... } | null,
  "newEntry": { "command": "npx", "args": [...], ... }
}
```

> `needsRestart` 在脚本层已经统一固定为 `false`：现代 AI agent 改完 mcp.json 通常都会自动 reload，**绝大多数场景不需要让用户重启**。

**`--target auto` 检测到歧义时**会拒绝写盘并返回（退出码非零，**不再返回完整 candidates 列表**）：

```json
{
  "ok": false,
  "ambiguous": true,
  "reason": "Detected only a weak VSCode-family signal ... — must ask the user.",
  "detectedHost": "vscode" | null,
  "detectedHostEvidence": "TERM_PROGRAM=vscode / VSCODE_PID (weak ...)",
  "manualSnippet": { "json": "{ ... }", "entry": { ... }, "instructions": [ ... ] },
  "hint": "Re-run with --target <host> (codebuddy | cursor | windsurf | claude-desktop | claude-code | continue | vscode | vscode-cline | vscode-roo-code | trae) or --config-path /abs/path/mcp.json."
}
```

> **拿到 `ambiguous: true` 怎么办**：直接走 [Step 3.1 - 直接打印 manualSnippet.json](#step-31--直接打印-json-片段让用户手动粘贴) 把片段交给用户。**禁止**：① 凭你自己的 system prompt 选 `--target`；② 追问用户"你用的是哪个 IDE / Cline / Roo Code / vanilla VSCode"——大多数用户也分不清自己用的是哪个 fork、对应 mcp.json 在哪。直接打印片段，让用户去自己 AI 助手的"MCP 设置"里粘贴，是**唯一**不会出错的路径。

> **强制兜底：手动粘贴方案**
> 任何写盘失败、权限不足、或用户明确拒绝自动写入的情况下，都可以直接执行：
> ```bash
> node "${SKILL_DIR}/scripts/configure-mcp.mjs" --print-snippet --json
> ```
> 然后把 `manualSnippet.json` 完整呈现给用户，让用户自己粘贴到 mcp.json 即可（绝大多数 AI Agent 改完会自动 reload，无需让用户手动重启）。这是**绝不会失败**的兜底路径。

### Step 4 — 验证 MCP 已生效（**不要让用户重启**）

如果 Step 3 写入了新配置（`action !== 'unchanged'`），**先做 4.1 真启动验证**，再做 4.2 流程衔接：

#### Step 4.1 — 真启动验证（`verify-mcp.mjs`）

`check-mcp.mjs` 只检查 mcp.json 里**有没有 entry**；`install-mcp.mjs` 只确认包**能在磁盘上解析**。两者都不能保证"AI host 真正 spawn 时该 entry 真的会跑起来"——npm registry 故障、postinstall 失败、Node 版本不兼容、沙箱阻塞 stdin 等都能让一个看似正常的 entry 在握手阶段挂掉。

执行：

```bash
node "${SKILL_DIR}/scripts/verify-mcp.mjs" --json --timeout-ms 30000
```

脚本会用与 mcp.json 完全一致的 `npx -y --prefer-online @weadmin/weixin-minigame-helper-mcp@latest` 命令真正启动 MCP server，发一次 `initialize` JSON-RPC 请求，等 `result.serverInfo` 回来：

| 字段 | 含义 |
|------|------|
| `ok: true` | server 在 `durationMs` 毫秒内返回了合法 `initialize` 响应；`serverInfo` / `protocolVersion` 给出版本信息；`transport: "stdio"` 说明本服务**就是 stdio 传输**——没有端口、没有 URL |
| `ok: false` | `stage` 表示卡在哪一步：`spawn`（npx 都没起来）/ `process-exit`（包启动后立刻崩溃，看 `stderrTail`）/ `timeout`（多半网络/registry 慢，建议先跑 `install-mcp.mjs` 预热）/ `initialize-error`（包跑起来了但握手报错）/ `write-stdin`（沙箱拦了 stdin） |
| `hint` | 失败时给出的下一步建议，可直接转述给用户 |

**只有 `ok: true` 才代表配置真正可用**。`ok: false` 时不要直接转给用户调用 MCP，按 `stage` + `hint` 排查：常见的是 `timeout`，跑一次 `node install-mcp.mjs --json` 预热缓存后再 verify 一次即可。

#### Step 4.2 — 衔接到主流程

verify 通过后告诉用户：
> "已为你配置好 `@weadmin/weixin-minigame-helper-mcp` 的 MCP（已实测 `initialize` 握手成功），绝大多数 AI 助手会自动 reload mcp.json，无需手动重启。"

然后**立即回到 Step 0**，再次直接调用一次 MCP 工具（例如 `run_game`）来验证。如果调用成功 → 流程继续；如果失败 → 进入 Step 5。

> **关于"是不是要先启动 MCP 才能配地址"**——**不要**。本 MCP 是 **stdio 传输**：AI 助手在需要时用 `npx` 临时拉起进程，通过 stdin/stdout 通信，**没有自己的 HTTP 端口/URL**。你不需要预先启动任何服务、也不需要"拿到地址再来配置"。如果你在某个 host 的 mcp.json 里看到形如 `{"url":"http://127.0.0.1:43210/mcp"}` 的 entry（比如 CodeBuddy），那个 URL 是**该 host 自家的 MCP 网关**（用来把多个 stdio MCP server 聚合到一个本地 HTTP 端点），**不是**本 server 的地址；这种 `customTransport: true` 的 entry 由该 host 自己管理，`configure-mcp.mjs` 不会去覆盖。

> **重要**：脚本输出里的 `needsRestart` 字段已经固定为 `false`。**绝不允许**主动告诉用户"请重启 AI 助手"——这是过时的提法，会打断用户体验。只有当用户自己反馈"调用 MCP 工具仍失败、且 Step 5 排查无果"时，才提示"作为最后一招可以尝试重启 AI 助手"。

### Step 5 — 兜底验证 / 退化排查

如果 Step 4 验证调用 MCP 工具仍然失败：
1. `check-mcp.mjs` 重新跑一遍，**反向排查**：
   - `nodeOk === false` → 提示用户升级 Node ≥ 18（详见顶部"关于 Node 环境"小节），停止流程
   - **`targetHostCaveat !== null`** → 你之前选错了 `--target`（多半是凭 system prompt 自识别 / 用户没明示就猜的）。**不要再继续猜 `--target` 是什么**——直接转 [Step 3.1 - 直接打印 manualSnippet.json](#step-31--直接打印-json-片段让用户手动粘贴) 让用户自己粘配置；**绝不能**因为旧输出的 `configured: true` 就以为已经配好
   - `recommendedAction === 'ask-user'` → 转 [Step 3.1 - 直接打印 manualSnippet.json](#step-31--直接打印-json-片段让用户手动粘贴)；**禁止**根据 system prompt 自识别选 `--target`，**禁止**追问用户"你用的是哪个 IDE"
   - `entryDrift === true` → Step 3（重跑 `configure-mcp.mjs`，默认会覆盖修复）
   - `configured === false` → Step 3
   - `installed === false` → Step 2
2. **跑一次 `verify-mcp.mjs` 看包本身能不能起**（往往是 `check-mcp` 看着都对、但 npx 实际启动时 timeout / postinstall fail）：
   ```bash
   node "${SKILL_DIR}/scripts/verify-mcp.mjs" --json --timeout-ms 60000
   ```
   - `stage: 'timeout'` + 持续超 30s → 大概率 npm registry 不通，让用户跑 `npm config get registry` 看是不是被代理/镜像卡住
   - `stage: 'process-exit'` → 看 `stderrTail`，常见是 Node 版本太旧或缺少原生依赖编译工具
   - `stage: 'spawn'` → npx 不在 PATH，让用户检查 Node 安装
   - `ok: true` 但主流程仍然调不到 MCP → 几乎一定是 AI host 没读到（你看错了 mcp.json，例如把"codebuddy-internal"误识别成了"codebuddy"）→ **不要再猜 host**，直接转 [Step 3.1 - 直接打印 manualSnippet.json](#step-31--直接打印-json-片段让用户手动粘贴) 让用户自己处理
   - 都满足且 `targetHostCaveat` 为 `null`、但 MCP 仍连接失败 → 提示用户："MCP 已配置和安装，但启动失败，请尝试手动验证：`npx -y --prefer-online @weadmin/weixin-minigame-helper-mcp@latest`，并把错误日志发给我。如果你愿意，也可以试着重启一下 AI 助手作为最后一招。"
2. **不要陷入死循环**：同一 step 累计执行 ≥ 2 次仍失败，立即停止并向用户汇报，不要无限重试。
3. **不要给替代方案**：MCP 不可用时，不要提议"那我直接用 shell 命令帮你启动预览"等绕过路径。本 Skill 是 MCP-only，要么配好继续走 MCP，要么停下来等用户配。

---

## 🧭 关于 MCP 配置文件位置的探测策略（VSCode 系 Agent 必读）

VSCode 生态里有多个支持 MCP 的 AI agent（GitHub Copilot Chat / Cline / Roo Code / 多个第三方扩展…），它们的 mcp.json 各自分散在用户目录的不同子路径。如果用户在同一台 VSCode 里装了多个扩展，**仅靠"哪个文件存在"无法确定到底要改哪个**——而且这些 agent 都不会写专属强信号环境变量。本 Skill 采用"二态决策"：脚本要么能 100% 锁定 host、要么明确返回 `ask-user`，**绝不在中间地带瞎猜**。

### 第 0 层（已删除）— 禁止 LLM 自识别决定 `--target`

> **历史说明**：早期版本曾让 LLM 读自己 system prompt 识别品牌（"You are CodeBuddy" → `--target codebuddy`），结果发现这是**幻觉爆发的重灾区**：典型悲剧是 system prompt 写"CodeBuddy"但实际宿主是"CodeBuddy-Internal" / 内部分发版本，两者用**完全不同的 mcp.json 路径**——LLM 自识别后写到 `~/.codebuddy/mcp.json`，但实际宿主读的是另一个文件，结果是 `configure-mcp` 报告"unchanged 已配置"+`verify-mcp` 报告"握手成功"，但 MCP 工具仍然连不上。已**彻底废弃** LLM 自识别这条路。
>
> **现在的硬性规则**：
> - 强环境信号命中 → 自动写入（脚本完成）；
> - 用户在对话里**主动说**"我用 Cursor / CodeBuddy / Windsurf / Trae" → 用户给的话可信，用 `--target <host>`；
> - 用户给绝对路径 → 用 `--config-path /abs/path/mcp.json`；
> - **其他所有情况** → 走 [Step 3.1 - 直接打印 manualSnippet.json](#step-31--直接打印-json-片段让用户手动粘贴) 让用户自己处理。
>
> **绝不允许**：① 凭你自己 system prompt 选 `--target`；② 看到 `TERM_PROGRAM=vscode` 就选 `--target vscode`；③ 主动追问用户"你用的是哪个 IDE"再据此猜——大多数用户分不清 fork 与扩展，更不知道对应 mcp.json 在哪，问了也没用。

### 第 1 层 — 环境变量识别（脚本唯一的自动判定来源）

`detectActiveHostFromEnv()` 检查若干强信号环境变量：

| Host | 强信号环境变量 |
|------|----------------|
| codebuddy | `CODEBUDDY_VERSION` / `CODEBUDDY_HOME` / `CODEBUDDY_USER_DIR` |
| cursor | `CURSOR_TRACE_ID` / `CURSOR_USER` |
| windsurf | `WINDSURF_BIN` / `WINDSURF_VERSION` |
| claude-code | `CLAUDECODE=1` / `CLAUDE_CODE=1` |
| trae | `TRAE_HOME` / `TRAE_VERSION` |
| claude-desktop（macOS） | `__CFBundleIdentifier=com.anthropic.claudefordesktop` |
| **vscode（弱信号 / 不确定）** | `TERM_PROGRAM=vscode` / `VSCODE_PID` / `VSCODE_IPC_HOOK`。**不要**把它当成"vanilla VSCode"——这个信号被以下所有场景共享，单看它**完全分辨不出**到底是哪个：① 真正的 vanilla VSCode；② 任何 VSCode fork（Cursor / Windsurf / CodeBuddy / VSCodium / Trae）但其专属强信号没注入；③ 任何 VSCode 内的 AI 扩展（Cline / Roo Code / Copilot Chat / 第三方），底层 host 仍是 VSCode 但写入的 mcp.json 路径完全不同；④ **同名品牌的内部分发版本**（如 CodeBuddy-Internal）——这是 LLM 自识别最容易踩的坑 |

**判定结果（`check-mcp.mjs` / `configure-mcp.mjs --target auto` 的统一行为）：**

| 检测结果 | `targetHost` | `recommendedAction`（check-mcp） | `--target auto`（configure-mcp） |
|---------|-------------|---------------------------------|--------------------------------|
| 强信号命中（codebuddy / cursor / windsurf / claude-code / claude-desktop / trae） | 该 host | 基于该 host 的 user+workspace 配置文件判定 | 直接写入该 host 的配置文件 |
| 弱 vscode（仅 `TERM_PROGRAM=vscode` / `VSCODE_PID`） | `null` | `ask-user` | `ok:false, ambiguous:true`，**拒绝写盘**，**不返回任何候选列表** |
| 完全无环境信号 | `null` | `ask-user` | 同上，拒绝写盘 |
| 显式 `--target <host>` | `<host>` | 基于该 host 的配置文件判定 | 写入该 host |
| 显式 `--config-path /abs` | `'custom'` | 基于该文件判定 | 写入该文件 |

> **设计原则**：脚本**只看**它自己 user+workspace 那 1-2 个文件，不再扫全表。这样一来"用户的 codebuddy 文件里恰好有 entry，但用户其实在用 Cursor"这种情况不会再被误判为"已配置"。

### 第 2 层（已废弃）

> **历史说明**：早期版本的 `check-mcp.mjs` 在弱 vscode 信号下会扫描所有已知 host 配置文件并按 `score` 给出推荐目标。这个逻辑在实践中会把"恰好里面有 entry 的不相关文件"误判为"已配置"，已删除。如果你（大模型）在脑海里有"按 score 推荐"的旧印象，请丢掉——脚本现在只输出 `targetHost` 对应的 1-2 个文件，或者干脆什么都不输出，让你向用户求证。

### 第 3 层 — 直接打印 JSON 片段交给用户（当 `recommendedAction === 'ask-user'` / `ambiguous: true` / `targetHostCaveat !== null`）

**触发条件（只要满足任意一个就走这条路）**：
- `check-mcp.mjs` 返回 `recommendedAction: 'ask-user'`（即 `targetHost === null`）；或
- `configure-mcp.mjs --target auto` 返回 `ok:false, ambiguous:true`；或
- 任何脚本输出里 `targetHostCaveat !== null`（说明你或脚本之前选错了 `--target`）。

> 如果 `recommendedAction === 'configure'` / `configure+install` / `reconfigure` / `install`（即强信号已命中），**直接走 Step 2/3 即可**，不需要进入本流程。
>
> 如果 `recommendedAction === 'none'` 且 `targetHostCaveat === null` → 配置已就绪，直接 [Step 4.1 verify-mcp](#step-41--真启动验证verify-mcpmjs) 然后回 Step 0；如果 `recommendedAction === 'none'` 但 `targetHostCaveat !== null` → 配置文件里有 entry 但**很可能不是你这个 host 用的那个**，仍然走本流程让用户手动确认。

#### Step 3.1 — 直接打印 JSON 片段让用户手动粘贴

**这是 ask-user / ambiguous / caveat 的唯一正确响应**。**不要**问"你用的是哪个 IDE"，**不要**根据 system prompt 自识别选 `--target`，**不要**让用户在 1-6 个选项里挑——直接把 `manualSnippet.json` 整段贴出来，让用户自己去 ta AI 助手的 MCP 设置里粘贴。

**响应模板**（直接套这个，把 `<…>` 替换为脚本输出里的真实值）：

> 你的 AI 助手品牌我没法自动判断（环境只能看到 `TERM_PROGRAM=vscode` 这种弱信号 / 完全没信号），自动写入有写错文件的风险。下面是你需要的 MCP 配置，**请你自己**去你 AI 助手的 MCP 设置里粘贴这段：
>
> ```json
> <把脚本输出里 manualSnippet.json 的内容原样粘到这里>
> ```
>
> 大多数 AI 助手在"设置/Settings → MCP / Model Context Protocol"或"扩展菜单 → MCP servers"里能直接打开 mcp.json 编辑界面。常见 host 的 mcp.json 路径仅供你**对照查找**（不要让我猜你用哪个，**你自己最清楚**）：
>
> | AI 助手 | mcp.json 默认路径（macOS） |
> |---------|---------------------------|
> | CodeBuddy | `~/.codebuddy/mcp.json`（也可能在 IDE "设置 → MCP" 里直接编辑） |
> | Cursor | `~/.cursor/mcp.json` |
> | Windsurf | `~/.codeium/windsurf/mcp_config.json` |
> | Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` |
> | Claude Code（CLI） | `~/.claude.json` 或工作区 `.mcp.json` |
> | VSCode 1.99+ 内置 Copilot Chat | `~/Library/Application Support/Code/User/mcp.json` 或 `<workspace>/.vscode/mcp.json` |
> | Cline 扩展 | `~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json` |
> | Roo Code 扩展 | `~/Library/Application Support/Code/User/globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json` |
> | Continue 扩展 | `~/.continue/config.json` |
> | Trae | `~/.trae/mcp.json` |
>
> 如果你不确定文件路径，**优先用 IDE 自带的设置界面**（搜索 "MCP" 一般能找到）；**不要让我猜**——CodeBuddy 也有 internal 分发版用不同路径，一旦写错文件会出现"配置看着对、但 MCP 永远连不上"的暗坑。
>
> 粘完保存即可——绝大多数 AI 助手会自动 reload mcp.json，**不需要重启**。配置完后告诉我"配好了"，我再调一次 MCP 工具验证。
>
> 这是一个 **stdio MCP 服务**，由你的 AI 助手按需 `npx` 临时启动，**没有 HTTP 端口、不需要预先启动任何服务**。

> **关键铁律**：本步骤是**绝对不能跳过**的兜底路径。**绝不允许**：① 跳过 Step 3.1 直接 `--target auto` / `--target codebuddy` / `--target vscode` 等碰运气写入；② 在打印片段之前先问用户"你用什么 IDE"——直接把片段+路径表一起呈现，让用户**自己**对照路径表去找文件。

#### Step 3.2 — 用户主动告知品牌时，才允许自动写入

只有当**用户在对话里主动告诉你**"我用的是 Cursor / CodeBuddy / Windsurf / Claude Desktop / Trae"等具体 AI 助手品牌时，才允许：

```bash
node "${SKILL_DIR}/scripts/configure-mcp.mjs" --target <用户告知的品牌> --json
```

或用户提供绝对路径时：

```bash
node "${SKILL_DIR}/scripts/configure-mcp.mjs" --config-path "/abs/user-given/path.json" --json
```

**注意**：写入后仍然必须跑 [Step 4.1 verify-mcp](#step-41--真启动验证verify-mcpmjs)；如果 verify 通过但 Step 0 调 MCP 仍失败 → 用户告知的品牌也不对（少见，但确实发生过 codebuddy vs codebuddy-internal 这种），回到 Step 3.1 打印片段让用户自己处理。

#### Step 3.3 — 用户明确说"随便选一个我相信你"时（极少用）

只有用户明确表态愿意承担误改风险，才允许：

```bash
node "${SKILL_DIR}/scripts/configure-mcp.mjs" --target auto --allow-ambiguous --json
```

> **绝大多数情况下不要走 3.3**——直接 Step 3.1 打片段是用户体验最好、也最不会出错的路径。3.3 仅作为"用户主动放权时不得已的兜底"存在。

---

## 🔴 最重要的规则（必须严格遵守）

> **自动预览规则（最高优先级）：**
> 每当你为用户**生成、修改、调试**了任何小游戏相关代码（包括 `.js`、`.json`、`.css` 等文件），代码写入完成后：
> **立即调用 `run_game`** + **打开（或刷新）浏览器查看预览**。
>
> `run_game` 是幂等的：第一次调用会启动本地预览服务并返回预览 URL，后续调用只会重新打包并自动刷新已经打开的预览页。预览 URL 和端口在整个会话中保持不变，你不需要、也不应该判断游戏是否已经在运行——任何时候直接调用 `run_game` 即可。
>
> **这是自动行为，不要等用户要求！** 代码改完 → 立即调用 `run_game`，零延迟。
>
> **触发判断（宽松匹配）：**
> - 用户在开发小游戏，或工作目录有 `game.js`，或用户提到"游戏"相关内容 → 本 Skill 生效
> - 用户说了任何与"预览"、"运行"、"看看效果"相关的话 → 立即执行预览流程
> - **不需要用户精确说出"微信小游戏预览"才触发，"帮我预览"、"预览一下"、"跑起来看看"等口语表达都应触发**

### 必须打开浏览器（优先级：host 内置 → VSCode 内置 → 系统默认）

`run_game` 返回 URL 后，**必须**让用户能直接看到画面。按下面三档优先级**只走一档**，不要重复打开：

#### 档 1（首选）：当前 AI host 提供的内置浏览器工具

如果当前 AI 助手**自身的工具集**里提供了内置浏览器工具（典型名字 `preview_url` / `openPreview` / `webview.open` / `open_in_simple_browser` 等，CodeBuddy / Cursor / Claude Desktop / Windsurf / Trae 等多数现代 IDE 都有），**优先调用它**，把 URL 传给它在 IDE 内嵌 webview 中打开（同一 URL 再次调用会自动刷新预览页）。

> **🚫 关键反模式（极易踩坑）**：`preview_url` 不是 `weixin-minigame-helper` 这个 MCP server 暴露的方法！它是**你这个 AI 助手 host 自身的工具**，和 `run_game` / `get_logs` 等 MCP 工具是**两个完全不同的命名空间**。**绝不能**写成 `weixin-minigame-helper(preview_url)` 或 `mcp__weixin-minigame-helper__preview_url` 这种调用——这种调用百分之百会失败（"mcp tool not found"）。判断方法：看你当前可用的 tool 列表里**有没有一个叫 `preview_url` 的顶层工具**（与 `run_game`、`read_file` 等并列），有就直接调用它（不带任何 server 前缀），没有就**直接判定档 1 不可用、立即跳到档 2**——**不要**尝试把 `preview_url` 当 MCP 工具去调一遍试错。
>
> **`preview_url` 失败 / 不存在时禁止退化为"贴 URL 文本让用户自己开"**——这是另一个高频错误。失败必须**立即按"档 1 → 档 2 → 档 3"的优先级继续往下走**，不允许在档 1 失败后断流。

#### 档 2（VSCode 系专用）：通过 launch.json 打开 VSCode 内置 Simple Browser

**触发条件（任一成立即可）：**
- 档 1 不可用（host 没有 `preview_url` 类工具）或 档 1 调用失败；**且**
- `check-mcp.mjs` 输出 `detectedHost === 'vscode'`（**包括强信号和弱信号**——`detectedHostStrong` 不影响档 2 决策，因为所有 VSCode 系 host & fork 都支持 `simpleBrowser.show` 命令；CodeBuddy / Cursor / Windsurf 等 fork 即便档 1 失败，回落到档 2 写 `.vscode/launch.json` 仍然有效）。

> **不要等用户"确认是 VSCode 系"再写 `launch.json`**：早期版本要求 AI 先问用户"你用的是不是 VSCode 系"，这是错的——`detectedHost === 'vscode'` 已经是脚本可信判定结果，**直接写就行**；写入是**幂等的**（脚本只会合并 / 更新，不会破坏用户已有的 launch.json），即使最终用户用的是别的 host，多出几个 `.vscode/*` 文件也没有副作用。
>
> 这种情况下走"系统默认浏览器"会把用户从 IDE 里弹出去，用户体验很差。VSCode 自带 Simple Browser 内置浏览器，但**没有**提供从外部 shell 直接打开它的命令行接口，因此必须借助 `launch.json` + `tasks.json` + `${input:command}` 让用户按 F5 触发。所有 VSCode 系（含 VSCode 扩展、所有 VSCode fork）都支持这个 Simple Browser 命令，所以这条路径在 detectedHost=='vscode' 时通用。

执行：

```bash
node "${SKILL_DIR}/scripts/open-browser.mjs" --mode vscode-builtin \
  --workspace "<游戏工程绝对路径>" --json "<url>"
```

> **🚨 路径关键点（极易踩坑）**：VSCode **只**从"用户用 File → Open Folder 打开的那个根目录"读 `.vscode/launch.json`。如果脚本把文件写到了**子目录**（典型：monorepo 里的 `<repo>/games/breakout/`，但用户在 VSCode 里打开的是 `<repo>/`），F5 会**完全无反应**。
>
> 因此 `--workspace` 应该传**用户在 VSCode 里打开的工作区根目录**，**不是** `game.js` 所在的游戏目录。但 AI 多数时候不知道前者具体是哪个，所以脚本做了下面这套自动解析：
>
> 1. **`--vscode-root <abs>`**（最高优先级）：你确定时显式覆盖。
> 2. **`process.cwd()`**：当 cwd 是 `--workspace` 的祖先（含相等）时直接用 cwd——AI host 在 IDE 里启动时 cwd 通常**就是**用户打开的工作区根。
> 3. **沿 `--workspace` 向上走**：找最近一个含 `.vscode/` / `.git/` / `*.code-workspace` 标记的祖先目录。
> 4. **回退**：用 `--workspace` 自己。
>
> 实践：直接把游戏工程绝对路径传 `--workspace` 就行——脚本会自动向上走一级一级找到真正的工作区根（也就是你 cwd 通常所在的位置），并通过 JSON 输出里的 `workspace` / `workspaceHint` / `workspaceResolvedBy` 三个字段告诉你它最终选了哪个目录、是怎么选的。
>
> **念给用户听 `userInstructions` 时**，第 1 条已经包含了"确认 VSCode 当前打开的是 `<workspace>`，否则需要 File → Open Folder 切过去"的提示——一定要原样转述，因为这是用户实际能不能 F5 起来的唯一判断点。

脚本会**合并写入**（保留用户已有的 task / config）：

| 文件 | 内容 |
|------|------|
| `.vscode/launch.json` | 新增配置 `🎮 微信小游戏 — VSCode 内置浏览器预览`（type=node + preLaunchTask） |
| `.vscode/tasks.json` | 新增 task `wmh:open-simple-browser`（type=shell + `${input:wmhOpenSimpleBrowserUrl}`），新增 input（type=command，调用 `simpleBrowser.show <url>`） |
| `.vscode/wmh-open-preview-noop.js` | F5 启动需要的 noop 脚本（`process.exit(0)`） |

写入后输出：

```json
{
  "ok": true,
  "mode": "vscode-builtin",
  "url": "http://localhost:3847",
  "workspace": "/abs/path/to/repo-root",
  "workspaceHint": "/abs/path/to/repo-root/games/breakout",
  "workspaceResolvedBy": "cwd-ancestor",
  "launchConfigName": "🎮 微信小游戏 — VSCode 内置浏览器预览",
  "taskLabel": "wmh:open-simple-browser",
  "files": [".vscode/launch.json", ".vscode/tasks.json", ".vscode/wmh-open-preview-noop.js"],
  "userInstructions": [
    "1) 切到 VSCode 窗口，确认当前打开的工作区根目录是：/abs/path/to/repo-root",
    "   （如果当前打开的是其他目录，需要 File → Open Folder 切到上面这个路径，否则 F5 不会生效。）",
    "2) 按 F5 ..."
  ]
}
```

`workspaceResolvedBy` 取值与含义：

| 取值 | 含义 |
|------|------|
| `explicit` | 调用方传了 `--vscode-root`，照搬 |
| `cwd-ancestor` | `--workspace`（或 cwd）位于 `process.cwd()` 之内，cwd 即工作区根 |
| `marker:.vscode` / `marker:.git` / `marker:*.code-workspace` | 沿 `--workspace` 向上走找到的最近标记 |
| `fallback` | 没找到标记，就地用 `--workspace` 自己 |

**重要：在 VSCode 系下，脚本无法替用户按下 F5。**（VSCode 不提供从外部 shell 调用 `simpleBrowser.show` 的 CLI；这是 VSCode 自身的限制。）你（AI 助手）必须把 `userInstructions` **完整、原样**念给用户听——里面第 1 条已经包含了"确认 VSCode 当前打开的根目录是 `<workspace>`"的提示，这是 F5 能不能生效的唯一前提。

> "我已经把启动配置写到 `<workspace>/.vscode/launch.json` 里了（注意 workspace 是 VSCode 根目录，不是游戏子目录）。请确认你 VSCode 当前打开的就是这个目录；如果不是，需要 File → Open Folder 切过去，再按 **F5**（或左侧 Run and Debug 面板选择 `🎮 微信小游戏 — VSCode 内置浏览器预览`）。VSCode 自带的 Simple Browser 会在编辑器侧边打开预览页。之后代码改动会自动刷新这个内置浏览器，无需重复按 F5。"

后续在同一会话里再次 `run_game`：
- 如果 URL 不变 → **不需要**再跑 `open-browser.mjs`（用户的 Simple Browser 标签页会自动刷新）。
- 如果 URL 变了（罕见）→ 重新跑一次 `open-browser.mjs --mode vscode-builtin`，脚本会原地更新 `.vscode/tasks.json` 里的 URL，然后告诉用户重按 F5。

#### 档 3（兜底）：系统默认浏览器

档 1、档 2 都不适用（既没有 host 内置工具，又不是 VSCode 系），执行：

```bash
node "${SKILL_DIR}/scripts/open-browser.mjs" "<url>"
```

或显式指定：

```bash
node "${SKILL_DIR}/scripts/open-browser.mjs" --mode external --json "<url>"
```

该脚本会调用系统命令（macOS `open`、Windows `start`、Linux `xdg-open`）打开外部浏览器。

#### 自动模式（懒人路径）

不带 `--mode` 时（即 `--mode auto`，默认值），脚本会自己判断：

| 环境信号 | 选择的 mode |
|---------|------------|
| 强 host 信号（CODEBUDDY / CURSOR / WINDSURF / CLAUDECODE / TRAE / claude-desktop） | `external`（这些 host 应该走档 1，AI 应该已经先试过 `preview_url`；走到本脚本就是兜底，直接外部浏览器） |
| 仅 `TERM_PROGRAM=vscode` / `VSCODE_PID`（弱 VSCode 信号 — vanilla VSCode 或 VSCode 内扩展，身份不确定但底层都是 VSCode） | `vscode-builtin` |
| 其他 | `external` |

所以**最简调用就是**：

```bash
node "${SKILL_DIR}/scripts/open-browser.mjs" --workspace "<game-dir>" --json "<url>"
```

脚本输出里的 `mode` 字段告诉你它实际选了哪条；如果是 `vscode-builtin`，记得念 `userInstructions` 给用户听。

#### 绝对禁止

- ❌ **把 `preview_url` 当成 `weixin-minigame-helper` 的 MCP 工具去调**（如 `weixin-minigame-helper(preview_url)`、`mcp__weixin-minigame-helper__preview_url`）。`preview_url` 是 host 顶层工具，**不属于本 MCP server**，这种调用必然 "mcp tool not found"。
- ❌ **档 1 失败 / 不可用就直接退化为"把 URL 贴给用户让 ta 自己开"**——必须立即沿"档 1 → 档 2 → 档 3"继续往下走。仅当档 3 也失败时才允许告诉用户 URL 让 ta 手动开。
- ❌ **在 VSCode 系（detectedHost==='vscode'）下跳过档 2 直接走档 3**（用户体验极差，违反"内置优先"的原则）。看到 `detectedHost === 'vscode'` 就直接写 `.vscode/launch.json`，不要先问用户"你用的是不是 VSCode 系"。
- ❌ 仅把 URL 文本贴给用户，让用户自己点。用户没有点击的义务。
- ❌ 多档同时执行（重复打开）。三档**只走一档**——档 1 成功就不要再调档 2/档 3，档 2 写完 `launch.json` 就不要再去开外部浏览器。

### 启动/重载后的日志检查循环（必须执行）

每次调用 `run_game` 后，必须执行以下循环：
1. 等待约 2 秒让游戏初始化
2. 调用 `get_logs`（可用 `"error|warn|Error|Warning|Uncaught|TypeError|ReferenceError"` 过滤）获取日志
3. 分析日志：
   - **无错误** → 流程结束，告知用户游戏运行正常
   - **有错误** → 分析错误原因，修复代码，再次调用 `run_game`，回到步骤 1 继续循环
4. 重复上述循环，**直到日志中无错误为止**

**循环退出机制（防止无限重试）：**
在修复循环过程中，必须记录每次遇到的错误及其修复次数：
- **同一错误**（相同的错误消息或相同根因）反复尝试修复 **超过 5 次** → 暂停循环，向用户报告该错误的详细信息和已尝试的修复方案，询问用户是否需要继续修复或采取其他方案
- **不同错误累计**修复尝试 **超过 15 次** → 暂停循环，向用户汇总所有遇到的错误及修复尝试，询问用户是否需要继续进行修复检查
- 暂停时应清晰告知用户：已尝试的次数、遇到的错误列表、每个错误的修复尝试次数，以便用户做出决策
- 如果用户选择继续，则重置计数器并继续循环

> **注意**：每轮修复只需在所有文件改完后调用一次 `run_game`，不要每改一个文件就调用一次。

---

## 概述

这个 Skill 让你能够：
1. **预览小游戏** — 在本地浏览器中实时运行微信小游戏
2. **查看日志** — 获取游戏运行时的 console 输出
3. **截取游戏画面** — 截图查看游戏当前渲染画面，用于视觉检查和调试
4. **真机预览** — 生成二维码，用微信扫码在真机上测试
5. **上传开发版** — 将游戏代码上传到微信平台

所有运行时操作通过 MCP 工具完成；环境配置由本 Skill 的辅助脚本一次性完成。

---

## MCP 工具参考

| 工具 | 功能 | 关键参数 |
|------|------|----------|
| `run_game` | 启动或刷新游戏预览（幂等：首次启动，后续自动重载并刷新预览页） | `workspacePath`: 游戏目录绝对路径（必须含 `game.js`） |
| `get_logs` | 获取游戏日志 | `filter`: 可选的正则表达式过滤 |
| `capture_screenshot` | 截取游戏画面 | `format`: 图片格式（可选，默认 PNG）；`quality`: JPEG 质量 0-1（可选） |
| `real_device_preview` | 真机预览 | `workspacePath`: 游戏目录绝对路径 |
| `publish` | 上传开发版 | `workspacePath`, `version` (如 "1.0.0"), `desc` |

---

## 工作流程

### 场景一：生成/修改小游戏代码后自动预览（最常见、最重要）

这是最典型的场景。**任何时候你修改了小游戏代码，都必须自动触发 `run_game`。**

1. 直接 [Step 0](#step-0--直接尝试调用-mcp最快路径) 试 MCP；调不通再按 Step 1 ~ Step 5 处理。
2. **确保游戏目录中有 `game.js` 文件**（这是微信小游戏的入口文件）。
3. **立即调用 `run_game` 工具**，传入游戏目录的绝对路径。
4. **工具返回预览 URL**（如 `http://localhost:3847`）。
   - 同一会话中，第一次调用启动预览服务；后续调用只会重新打包并自动刷新已经打开的预览页，URL 和端口不会变化。
5. **必须打开浏览器**：按上方"必须打开浏览器（优先级：host 内置 → VSCode 内置 → 系统默认）"的三档优先级走。简化版决策树：
   - 你（AI 助手）当前可用工具里有 `preview_url` / 等价的顶层内置浏览器工具 → 直接调用它（档 1）。**注意**：`preview_url` 不是 `weixin-minigame-helper` 的 MCP 方法，**禁止**写成 `weixin-minigame-helper(preview_url)` 去试错；当前工具集里没有就直接判定档 1 不可用，跳到档 2。
   - 档 1 不可用 / 调用失败，且 `check-mcp.mjs` 输出 `detectedHost==='vscode'`（无论 `detectedHostStrong` 是 true 还是 false——所有 VSCode 系 host & fork 都支持 Simple Browser）→ `node "${SKILL_DIR}/scripts/open-browser.mjs" --mode vscode-builtin --workspace "<game-dir>" --json "<url>"`（档 2），念返回的 `userInstructions` 让用户按 F5。
   - 其他情况（非 VSCode 系，或档 2 也失败）→ `node "${SKILL_DIR}/scripts/open-browser.mjs" "<url>"`（档 3，外部浏览器）。
   - **绝对不要只把 URL 文本发给用户**，必须主动选档并执行；档 1 失败也**禁止**直接退化到"贴 URL 文本"——必须按档 2/档 3 继续。
6. **等待约 2 秒后调用 `get_logs`** 检查是否有错误日志。
7. **若发现错误** → 分析并修复代码 → 再次调用 `run_game` → 再次等待并调用 `get_logs` → 循环直到无错误。

> **记住**：不管是用户主动要求修改代码，还是你在调试过程中修改了代码，只要代码文件有变更，就必须自动调用 `run_game`。用户不需要说"帮我预览"——这是你的默认行为。

### 场景二：查看日志调试问题

当游戏运行中出现问题时：
1. 调用 `get_logs` 获取所有日志。
2. 可用 `filter` 参数过滤，如 `"error|warn"` 只看错误和警告。
3. 日志格式: `[时间戳] [级别] 消息内容`。

### 场景三：截取游戏画面 / 视觉检查

当需要查看游戏当前画面时（如用户要求截图、验证 UI 修改效果、调试视觉问题）：
1. 确保游戏已运行（先调用 `run_game`）。
2. 等待约 2 秒让游戏渲染完成。
3. 调用 `capture_screenshot` 获取游戏画面截图。
4. 分析截图内容，确认渲染是否正确。

> **提示**：支持所有游戏类型（Canvas2D、WebGL、Unity、Cocos、Three.js 等）。默认返回 PNG 格式，如需更小文件可使用 `format: "image/jpeg"`。

### 场景四：真机预览

当用户要求在真机上预览、体验、测试时：

**如果用户尚未配置 AppID 和密钥：**
1. 先确保游戏已通过 `run_game` 启动预览。
2. 告诉用户：**"请在浏览器预览页面中点击右上角 ⚙️ 按钮，在弹出的配置面板中填写你的微信 AppID 和代码上传密钥。"**
3. 引导用户获取密钥：微信公众平台 (mp.weixin.qq.com) → 管理 → 开发管理 → 开发设置 → 小程序代码上传。
4. 用户保存配置后，调用 `real_device_preview` 工具。
5. **⚠️ 禁止使用任何替代方案**（如跳过配置、使用测试账号、使用模拟数据等）。

**如果已配置好（环境变量 `WECHAT_APPID` + `WECHAT_PRIVATE_KEY_PATH`）：**
1. 直接调用 `real_device_preview` 工具。
2. 二维码会在浏览器预览页面弹窗展示。
3. 告诉用户用微信扫码。

### 场景五：上传开发版/上传体验版/发布到微信

1. 确认用户要发布的版本号和描述。
2. 调用 `publish` 工具，传入 `workspacePath`、`version`、`desc`。
3. 如果返回 `configMissing`，引导用户在预览页面配置 AppID 和密钥。
4. **⚠️ 禁止使用任何替代方案**（如跳过配置、使用测试账号、使用模拟数据等）。
5. 成功后告知用户新版本已上传，等待审核。

---

## 完整使用示例

### 示例 1：用户说"帮我做一个打砖块小游戏"

```
1. 直接 Step 0 尝试 MCP；如失败则按 check-mcp.mjs 推荐的 action 走 Step 2/3/4；
   特别注意：若 recommendedAction==='ask-user' 或 targetHostCaveat 触发，**禁止凭 system prompt 自识别选 --target、禁止追问用户用哪个 IDE**，直接按 Step 3.1 把 manualSnippet.json 整段贴给用户让 ta 自己去 AI 助手的 MCP 设置里粘贴
2. 生成游戏代码（game.js + 相关文件）
3. 立即调用 run_game 工具，传入游戏目录路径
4. 按"档 1 → 档 2 → 档 3"打开浏览器（**只走一档**；前一档失败 / 不可用必须立即继续下一档，禁止退化到"只把 URL 贴给用户"）：
   - 档 1：你的工具集里有顶层 preview_url / 等价工具就直接调用它（**注意**：preview_url 不是本 MCP server 的方法，禁止写成 `weixin-minigame-helper(preview_url)` 试错；没有就立即判定档 1 不可用）
   - 档 2：detectedHost==='vscode'（无论 detectedHostStrong）→ open-browser.mjs --mode vscode-builtin --workspace ... → 念 userInstructions 让用户按 F5
   - 档 3：其他兜底 → open-browser.mjs <url>
5. 等待约 2 秒后调用 get_logs 检查日志
6. 若发现错误 → 分析错误 → 修复代码 → 再次 run_game → 再次 get_logs → 循环直到无错误（同一错误超过 5 次或累计超过 15 次时暂停询问用户）
7. 无错误后告诉用户"游戏已生成并启动预览，运行正常，你可以在浏览器中看到效果"（如果是档 2 还要再提醒一次按 F5）
```

### 示例 2：用户说"把颜色改成红色"（代码修改后自动预览）

```
1. 修改代码文件
2. 自动调用 run_game 刷新预览（无需用户要求；预览页会自动刷新，URL 不变）
3. 等待约 2 秒后调用 get_logs 检查日志
4. 若发现错误 → 修复 → 再次 run_game → 再次 get_logs → 循环直到无错误（退出条件同上）
5. 无错误后告诉用户"代码已修改并刷新，运行正常，请查看浏览器中的效果"
```

### 示例 2.5：用户说"帮我加个得分系统"（较大代码修改后自动预览）

```
1. 修改多个代码文件（如 game.js、score.js 等）
2. 所有文件修改完成后，自动调用 run_game 刷新预览（无需用户要求）
3. 等待约 2 秒后调用 get_logs 检查日志
4. 若有错误 → 修复 → 再次 run_game → 再次 get_logs → 循环直到无错误（退出条件同上）
5. 无错误后告诉用户"得分系统已添加，运行正常，请在浏览器中查看效果"
注意：多个文件修改只需要在全部完成后触发一次 run_game，不需要每改一个文件就调用一次
```

### 示例 3：用户说"真机体验测试下"

```
1. 调用 real_device_preview，传入游戏目录路径
2. 如果返回 configMissing：
   - 引导用户在预览页面配置 AppID 和密钥
   - 禁止使用任何替代方案！
3. 如果成功，告诉用户"二维码已在预览页面弹出，请用微信扫码"
4. 禁止调用 run_game 或任何会刷新网页的操作！
```

### 示例 4：用户说"上传到微信"

```
1. 调用 publish，传入游戏目录路径
2. 如果返回 configMissing：
   - 引导用户在预览页面配置 AppID 和密钥
   - 禁止使用任何替代方案！
3. 如果成功，告诉用户"上传成功，等待审核"
4. 禁止调用 run_game 或任何会刷新网页的操作！
```

---

## 微信小游戏项目结构

一个标准的微信小游戏项目应包含：

```
game-dir/
├── game.js              # 入口文件（必须）
├── game.json            # 游戏配置
├── project.config.json  # 微信开发者工具项目配置（含 appid）
└── ... 其他资源文件
```

---

## 注意事项

- **运行环境**：Node.js ≥ 18.x；首次会话直接试 MCP 调用即可，调不通再按 Step 1 ~ Step 4 完成一次环境装配。
- 游戏目录**必须**包含 `game.js` 文件。
- 预览服务器会自动注入微信 `wx` API 兼容层，大部分小游戏 API 可在浏览器中模拟运行。
- 真机预览和发布需要微信 AppID 和代码上传密钥。
- 配置可通过浏览器预览页面的 ⚙️ 按钮完成，或通过环境变量 `WECHAT_APPID` + `WECHAT_PRIVATE_KEY_PATH`。
- 如遇到 IP 白名单问题，需在微信公众平台添加本机 IP。

---

## ⚠️ 重要约束

> **真机预览（`real_device_preview`）和上传开发版（`publish`）成功后，禁止调用 `run_game` 或任何会刷新网页的操作！**
>
> 原因：二维码会在预览页面弹窗展示，如果刷新网页，二维码会消失，用户无法扫码。
>
> **注意**：如果是正常的代码改动（非真机预览/上传开发版），则可以正常调用 `run_game` 让用户看到最新效果。

> **配置缺失时的处理原则**：
>
> 当 `real_device_preview` 或 `publish` 返回 `configMissing` 时，**必须引导用户去配置 AppID 和密钥**，**禁止使用任何替代方案**（如跳过配置、使用模拟数据、使用其他账号等）。
>
> 正确做法：
> 1. 告诉用户："请在浏览器预览页面中点击右上角 ⚙️ 按钮，在弹出的配置面板中填写你的微信 AppID 和代码上传密钥。"
> 2. 引导用户获取密钥：微信公众平台 (mp.weixin.qq.com) → 管理 → 开发管理 → 开发设置 → 小程序代码上传
> 3. 等待用户配置完成后，再重新调用相应工具
>
> **禁止的做法**：
> - ❌ 跳过配置步骤
> - ❌ 使用测试账号或模拟数据
> - ❌ 使用其他用户的 AppID
> - ❌ 尝试绕过配置要求

> **IP 白名单错误（错误码 -10008）的处理**：
>
> 当 `real_device_preview` 或 `publish` 返回 `ipWhitelistError` 时，说明本地公网 IP 不在微信公众平台的白名单中。
>
> 正确做法：
> 1. 告诉用户："你的本地公网 IP 可能已变更，需要在微信公众平台更新 IP 白名单。"
> 2. 引导用户添加 IP 白名单：微信公众平台 (mp.weixin.qq.com) → 开发管理 → 开发设置 → 小程序代码上传 → IP 白名单
> 3. 提示用户：预览页面右上角会显示当前的公网 IP，可以复制后添加到白名单中
> 4. 用户更新白名单后，重新调用相应工具

> **环境兜底原则**：
>
> 任何前置脚本（`check-mcp.mjs` / `install-mcp.mjs` / `configure-mcp.mjs` / `open-browser.mjs`）失败时，**原样转述脚本输出的 `error` 字段给用户**，并给出可手动执行的等价命令；不要伪造结果，也不要无限重试同一步骤（同一步骤累计 ≥ 2 次仍失败 → 立即停止上报）。
