# 微信小游戏开发助手 — 通用 Skill

> 适用于任何支持 MCP（Model Context Protocol）的 AI 编程助手：CodeBuddy、Cursor、
> Claude Desktop、Windsurf、Continue.dev 等。

本目录是一个**自包含的 Skill 包**：把整个目录拷给你的 AI 助手作为 Skill 即可使用。
不依赖任何 IDE 插件框架，仅依赖：

1. **MCP 包**：`@weadmin/weixin-minigame-helper-mcp`（npm 公开包）
2. **本目录脚本**：用于环境检查、安装、配置 MCP 与兜底打开浏览器

## 目录结构

```
.
├── SKILL.md                       # 主 Skill 文档（AI 助手读取的入口）
├── scripts/
│   ├── check-mcp.mjs              # 检查 MCP 是否已安装/已配置（输出 JSON）
│   ├── install-mcp.mjs            # 自动安装 MCP（npm install -g，失败兜底用户级 prefix）
│   ├── configure-mcp.mjs          # 写入 MCP 配置到当前 host 的 mcp.json
│   ├── verify-mcp.mjs             # 真启动一次 npx 包，发 initialize 握手验证 MCP 真能跑
│   └── open-browser.mjs           # 兜底用系统命令打开浏览器
└── README.md                      # 本文件
```

## 使用方式

### 1. 把本目录注册为 Skill

不同 AI 助手注册 Skill 的方式不同：

- **CodeBuddy IDE**：复制本目录到 `~/.codebuddy/skills/weixin-minigame-helper/`
- **Cursor / Claude Desktop**：把 `SKILL.md` 内容粘贴到对应的"自定义指令 / Custom Instructions"
- **通用做法**：在你的 AI 助手中告诉它："读取 `<absolute-path>/SKILL.md` 并按其指引行事"

### 2. 让 AI 走完前置环境检查

首次使用时，AI 会按 SKILL.md 的指引：

1. 直接尝试 MCP 工具（最快路径）。
2. 失败则跑 `check-mcp.mjs --json` 看缺什么。
3. 缺安装 → 跑 `install-mcp.mjs --json`。
4. 缺配置 → 跑 `configure-mcp.mjs --target auto --json`：
   - 强环境信号（`CODEBUDDY_*` / `CURSOR_*` / `WINDSURF_*` / `CLAUDECODE` / 等）→ 自动写入对应 host 的 mcp.json。
   - 弱 vscode 信号（仅 `TERM_PROGRAM=vscode` / `VSCODE_PID`，无法分辨是 vanilla VSCode、Cline、Roo Code 还是某个 fork）或完全无信号 → 脚本**拒绝写盘**并返回 `recommendedAction: 'ask-user'` / `ambiguous: true`，**不再返回任何候选路径列表**。AI **必须**走 SKILL.md Step 3.1：**直接把 `manualSnippet.json` 贴给用户**，附常见 host 的 mcp.json 路径表，让用户**自己**对照查找并粘贴；**禁止**让 AI 凭自己 system prompt 选 `--target`、**禁止**追问用户"你用的是哪个 IDE"——这两条都是幻觉爆发的高发场景（典型悲剧：system prompt 写"CodeBuddy"但实际是"CodeBuddy-Internal"用不同的 mcp.json 路径，结果脚本报告"unchanged 已配置"但 MCP 永远连不上）。
   - **⚠ `--target vscode` 是高危取值**——它**仅**指 VSCode 1.99+ 自带的原生 MCP 客户端（内置 Copilot Chat），**不**对应任何 VSCode 扩展（CodeBuddy / Cline / Roo Code / Continue ...）和任何 VSCode fork（Cursor / Windsurf / Trae / VSCodium）。脚本检测到这个 target 时**始终**会输出 `targetHostCaveat` 警告字段，AI 看到 caveat 必须**直接转 Step 3.1 给用户片段**，**不得**重新猜 `--target`。
5. 配置成功后**先跑 `verify-mcp.mjs --json --timeout-ms 30000` 真启动一次 MCP 包验证握手**（避免"mcp.json 里有 entry 但 npx 实际起不来"这类暗坑），通过后再让 AI host 调用 MCP 工具。绝大多数 host 会自动 reload mcp.json，**不需要让用户重启**。
6. **MCP 不可用 = 流程终止**：本 Skill 是 MCP-only，所有功能都没有 CLI 等价物。配不上就给用户 `manualSnippet` 让 ta 手动粘贴并停止流程，**不要**给"那我直接命令行帮你启动预览吧"这种替代方案。

### ⚠ 关于"端口"和"启动顺序"——读这段避免概念误区

`@weadmin/weixin-minigame-helper-mcp` 是 **stdio 传输**的 MCP server：AI 助手在需要时用 `npx` 临时拉起一个进程，通过 **stdin/stdout** 收发 JSON-RPC，**没有自己的 HTTP 端口、没有 URL、不需要预先启动任何服务**。所以**正确的启动顺序就是**：

1. `install-mcp.mjs`（让 npm 解析得到包；可选，仅为预热缓存避免首次握手 timeout）
2. `configure-mcp.mjs`（把 `npx` 命令写进对应 AI host 的 mcp.json）
3. `verify-mcp.mjs`（按 mcp.json 里那个 npx 命令真启动一次，发 `initialize` 握手验证）
4. AI host 自动 reload mcp.json，可以直接用 MCP 工具了

**不要**误以为"应该先 `npx` 启动 MCP 拿到地址，再把地址写进 mcp.json"。如果你在某个 host 的 mcp.json 里看到 `{"url":"http://127.0.0.1:43210/mcp"}` 这种 entry（CodeBuddy 是典型例子），那个 URL 是**该 host 自家的 MCP 网关**——它把多个 stdio MCP server 聚合到一个本地 HTTP 端点上——和本 server 的真实地址无关。这种 `customTransport: true` 的 entry 由该 host 自己管理，本 Skill 的 `configure-mcp.mjs` **不会**覆盖它。

下一次会话开始时，第一步就成功，跳过所有检查。

### 3. 浏览器优先级（重要）

`run_game` 返回 URL 后，AI **必须**主动打开它，按"档 1 → 档 2 → 档 3"**只走一档**；前一档失败 / 不可用必须立即继续下一档：

1. **档 1：host 顶层内置浏览器工具**（IDE 自身提供的 `preview_url` / `openPreview` 等顶层工具，**不是** `weixin-minigame-helper` 这个 MCP server 的方法——禁止写成 `weixin-minigame-helper(preview_url)` 试错；当前工具集里没有就直接判定档 1 不可用，立即跳档 2）。
2. **档 2：VSCode 系内置 Simple Browser**：当 `check-mcp.mjs` 输出 `detectedHost === 'vscode'`（无论 `detectedHostStrong` 是 true 还是 false——所有 VSCode 系 host 与 fork 都支持 `simpleBrowser.show`）→ 调用 `node scripts/open-browser.mjs --mode vscode-builtin --workspace <game-dir> --json <url>`，脚本会**自动向上找到 VSCode 工作区根**（cwd 祖先 / `.vscode` / `.git` / `*.code-workspace` 标记）并把 `.vscode/launch.json` + `tasks.json` 写到那里——`.vscode/` 必须落在用户 File → Open Folder 打开的根目录里，否则 F5 不会生效。AI 必须把返回的 `userInstructions` 完整念给用户听（第 1 条会告诉用户实际写到了哪个目录、需要在 VSCode 里打开它，然后按 F5）。
3. **档 3：系统默认浏览器**（兜底）：`node scripts/open-browser.mjs <url>`。

绝不允许：
- ❌ 只把 URL 文本贴给用户让 ta 自己开。
- ❌ 档 1 失败就直接跳过档 2 走档 3 / 退化为"贴 URL"——VSCode 系下必须先尝试写 `.vscode/launch.json`。
- ❌ 把 `preview_url` 当作本 MCP server 的方法去调用。

## 脚本设计原则

- **JSON 输出**：所有脚本都支持 `--json` 模式，方便 AI 直接消费结构化结果，**不必把脚本源码读进上下文浪费 token**。
- **幂等**：重复执行不会损坏状态。
- **逐步退化**：先尝试最快/最干净的路径，失败才退化到更重的方案；同一步骤累计失败 ≥ 2 次即停止并报告，不死循环。

## 与 WorkBuddy 版的区别

WorkBuddy 版本（`platform/workbuddy/`）依赖 CodeBuddy 的 plugin manifest（`agents/`、`commands/`、`hooks/`）。本通用 Skill 版**只保留 SKILL.md + MCP**，把 plugin 系统提供的能力（环境检查、配置注入）下放到 Skill 里的脚本，从而能跑在任何 MCP 兼容的 host 上。

构建：

```bash
npm run build:skill            # 输出到 dist/weixin-minigame-helper/
```

构建产物即一个完整可分发的 Skill 目录。
