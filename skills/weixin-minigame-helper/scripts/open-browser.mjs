#!/usr/bin/env node
/**
 * open-browser.mjs
 *
 * Open a preview URL through the BEST channel for the current host.
 *
 * Channels (mode):
 *   - "external"       → spawn the OS default browser (open / start / xdg-open).
 *   - "vscode-builtin" → write `.vscode/launch.json` + `.vscode/tasks.json` so
 *                        the user can open the URL in VSCode's built-in
 *                        **Simple Browser** by pressing F5 (or via Run Task).
 *                        This is the right fallback for vanilla VSCode, where
 *                        the AI host does not provide an internal `preview_url`
 *                        tool — without this, the URL would be punted to the
 *                        external browser, which defeats the point of VSCode.
 *
 * Auto-mode policy (default):
 *   - STRONG host signal (CODEBUDDY_* / CURSOR_* / WINDSURF_* / CLAUDECODE /
 *     TRAE_* / claude-desktop) → "external". Those hosts already expose their
 *     own internal browser tool to the AI; this script is the FALLBACK, so
 *     we go straight to the OS browser. (The AI should have tried the
 *     host-internal tool first.)
 *   - VANILLA VSCode (TERM_PROGRAM=vscode but NO strong fork signal) →
 *     "vscode-builtin".
 *   - Anything else → "external".
 *
 * Usage:
 *   node open-browser.mjs <url>
 *   node open-browser.mjs --mode auto|external|vscode-builtin <url>
 *   node open-browser.mjs --workspace <abs-dir> <url>      # hint dir; script
 *                                                          # will walk up to
 *                                                          # find true VSCode
 *                                                          # workspace root
 *   node open-browser.mjs --vscode-root <abs-dir> <url>    # explicit override
 *   node open-browser.mjs --json <url>
 *
 * About --workspace vs --vscode-root (CRITICAL):
 *   VSCode reads `.vscode/launch.json` ONLY from the directory the user
 *   opened with "File → Open Folder" (the workspace root). Writing
 *   `.vscode/launch.json` into a *sub*-directory (e.g. the game subdir of a
 *   monorepo) is silently ignored — F5 will do nothing.
 *
 *   AI agents typically know the GAME directory (where `game.js` lives) but
 *   not necessarily the VSCode workspace root, which can differ. To be safe,
 *   pass the game dir as `--workspace` and the script will resolve the true
 *   workspace root by walking up + checking `process.cwd()`. Use
 *   `--vscode-root` only when you're certain.
 *
 *   Resolution priority:
 *     1. --vscode-root <abs>             explicit override
 *     2. process.cwd()                   if it equals or is an ancestor of
 *                                        --workspace (AI agents launched by
 *                                        the IDE have cwd = workspace root)
 *     3. walk up from --workspace        looking for `.vscode/`, `.git/`,
 *                                        or `*.code-workspace`
 *     4. fallback                        --workspace itself (or cwd)
 *
 * Output (with --json):
 *   { "ok": true, "mode": "external", "url": "...", "command": "open" }
 *   { "ok": true, "mode": "vscode-builtin", "url": "...",
 *     "workspace": "/abs/...",                  # the chosen VSCode workspace root
 *     "workspaceHint": "/abs/.../game",         # the original --workspace arg
 *     "workspaceResolvedBy": "cwd-ancestor"     # explicit | cwd-ancestor |
 *                                               # marker:.git | marker:.vscode |
 *                                               # marker:*.code-workspace |
 *                                               # fallback
 *     "launchConfigName": "...", "taskLabel": "...",
 *     "files": [".vscode/launch.json", ".vscode/tasks.json", ...],
 *     "userInstructions": ["1) ...", "2) ..."] }
 *   { "ok": false, "error": "..." }
 *
 * Notes:
 *   - The AI agent should ALWAYS try the host's own built-in browser tool
 *     (e.g. `preview_url`) FIRST when it exists. Only fall back to this
 *     script when that tool is missing or fails.
 *   - In "vscode-builtin" mode this script DOES NOT spawn anything — opening
 *     the Simple Browser requires a user action (pressing F5 or running the
 *     task), because VSCode does not expose a way to invoke an internal
 *     command from the host shell. The AI must read `userInstructions` from
 *     the JSON output and relay them to the user.
 */

import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';

import { detectActiveHostFromEnv } from './lib/mcp-spec.mjs';

/* -------------------------------------------------------------------------- */
/* CLI parsing                                                                */
/* -------------------------------------------------------------------------- */

const argv = process.argv.slice(2);
const args = parseArgs(argv);

function parseArgs(list) {
  const o = {
    mode: 'auto',
    workspace: null,
    vscodeRoot: null,
    json: false,
    url: null,
  };
  for (let i = 0; i < list.length; i++) {
    const a = list[i];
    if (a === '--json') o.json = true;
    else if (a === '--mode') o.mode = list[++i];
    else if (a === '--workspace') o.workspace = list[++i];
    else if (a === '--vscode-root') o.vscodeRoot = list[++i];
    else if (!a.startsWith('--')) o.url = a;
  }
  return o;
}

function emit(payload, code = 0) {
  if (args.json) {
    process.stdout.write(JSON.stringify(payload, null, 2) + '\n');
  } else if (payload.ok && payload.mode === 'external') {
    console.log(`[open-browser] external  opened ${payload.url} via ${payload.command}`);
  } else if (payload.ok && payload.mode === 'vscode-builtin') {
    console.log(`[open-browser] vscode-builtin  wrote ${payload.files.join(', ')}`);
    console.log(`[open-browser] URL: ${payload.url}`);
    console.log('[open-browser] User action required — pick ONE:');
    for (const line of payload.userInstructions) console.log(`  ${line}`);
  } else {
    console.error(`[open-browser] FAIL ${payload.error}`);
  }
  process.exit(code);
}

/* -------------------------------------------------------------------------- */
/* URL validation                                                             */
/* -------------------------------------------------------------------------- */

if (!args.url) emit({ ok: false, error: 'Missing URL argument. Usage: open-browser.mjs <url>' }, 1);

let validatedUrl;
try { validatedUrl = new URL(args.url); }
catch { emit({ ok: false, error: `Invalid URL: ${args.url}` }, 1); }

if (!['http:', 'https:'].includes(validatedUrl.protocol)) {
  emit({ ok: false, error: `Refusing non-http(s) URL: ${args.url}` }, 1);
}

/* -------------------------------------------------------------------------- */
/* Mode resolution                                                            */
/* -------------------------------------------------------------------------- */

function resolveMode() {
  if (args.mode === 'external' || args.mode === 'vscode-builtin') return args.mode;

  const env = detectActiveHostFromEnv();
  // Strong host signal → that host owns the preview UX; we are the
  // OS-fallback. Don't write VSCode files for Cursor/Windsurf/CodeBuddy etc.
  if (env.strong) return 'external';
  // Weak `vscode` signal = some VSCode-family host (could be vanilla VSCode,
  // VSCodium, or a VSCode extension whose fork didn't set a strong env var).
  // All of these can use VSCode's built-in Simple Browser via launch.json,
  // so writing the launch config is a safe default. The orchestrator should
  // STILL ask the user to confirm if it has tool-level uncertainty.
  if (env.host === 'vscode') return 'vscode-builtin';
  return 'external';
}

const MODE = resolveMode();

/* -------------------------------------------------------------------------- */
/* Mode 1 — external browser                                                  */
/* -------------------------------------------------------------------------- */

function openExternal(url) {
  let cmd, spawnArgs;
  switch (process.platform) {
    case 'darwin':
      cmd = 'open';
      spawnArgs = [url];
      break;
    case 'win32':
      cmd = 'cmd';
      // The empty "" is `start`'s title arg (otherwise URL is mistaken for a title).
      spawnArgs = ['/c', 'start', '""', url];
      break;
    default:
      cmd = 'xdg-open';
      spawnArgs = [url];
  }
  try {
    const child = spawn(cmd, spawnArgs, { detached: true, stdio: 'ignore' });
    child.unref();
    emit({ ok: true, mode: 'external', url, command: cmd });
  } catch (err) {
    emit({ ok: false, error: `Failed to spawn ${cmd}: ${err.message}` }, 1);
  }
}

/* -------------------------------------------------------------------------- */
/* Mode 2 — VSCode built-in (Simple Browser via launch.json + tasks.json)     */
/* -------------------------------------------------------------------------- */

const VSC = Object.freeze({
  taskLabel: 'wmh:open-simple-browser',
  inputId: 'wmhOpenSimpleBrowserUrl',
  launchConfigName: '🎮 微信小游戏 — VSCode 内置浏览器预览',
  noopFile: 'wmh-open-preview-noop.js',
});

/**
 * Strip JSONC line/block comments and trailing commas so we can parse with
 * `JSON.parse`. Aware of strings (won't strip `//` inside `"..."`).
 *
 * @param {string} src
 * @returns {string}
 */
function stripJsonc(src) {
  let out = '';
  let i = 0;
  let inString = false;
  let stringQuote = '';
  while (i < src.length) {
    const c = src[i];
    const next = src[i + 1];
    if (inString) {
      out += c;
      if (c === '\\' && i + 1 < src.length) { out += src[i + 1]; i += 2; continue; }
      if (c === stringQuote) { inString = false; stringQuote = ''; }
      i++;
      continue;
    }
    if (c === '"' || c === '\'') { inString = true; stringQuote = c; out += c; i++; continue; }
    if (c === '/' && next === '/') {
      while (i < src.length && src[i] !== '\n') i++;
      continue;
    }
    if (c === '/' && next === '*') {
      i += 2;
      while (i < src.length && !(src[i] === '*' && src[i + 1] === '/')) i++;
      i += 2;
      continue;
    }
    out += c;
    i++;
  }
  // Strip trailing commas before } or ]
  out = out.replace(/,(\s*[}\]])/g, '$1');
  return out;
}

function readJsoncSafe(file) {
  if (!fs.existsSync(file)) return null;
  const raw = fs.readFileSync(file, 'utf8');
  try {
    return JSON.parse(stripJsonc(raw));
  } catch (err) {
    throw new Error(`Failed to parse ${file} as JSONC: ${err.message}`);
  }
}

function writeJsonAtomic(file, obj) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp.${process.pid}`;
  fs.writeFileSync(tmp, JSON.stringify(obj, null, 2) + '\n', 'utf8');
  fs.renameSync(tmp, file);
}

/**
 * Resolve the actual VSCode workspace root (where `.vscode/launch.json`
 * should live).
 *
 * VSCode reads `.vscode/launch.json` ONLY from the workspace root — the
 * directory the user opened via "File → Open Folder" or `code <dir>`. If we
 * write `.vscode/launch.json` into a SUB-directory (e.g. the game subdir of
 * a monorepo), VSCode silently ignores it and F5 does nothing. This helper
 * tries hard to land on the right directory.
 *
 * @returns {{ root: string, decidedBy: string, hint: string }}
 */
function resolveVscodeWorkspaceRoot() {
  // 1) Explicit override
  if (args.vscodeRoot) {
    return {
      root: path.resolve(args.vscodeRoot),
      decidedBy: 'explicit',
      hint: args.workspace ? path.resolve(args.workspace) : path.resolve(process.cwd()),
    };
  }

  const cwd = path.resolve(process.cwd());
  const startDir = args.workspace ? path.resolve(args.workspace) : cwd;

  // 2) cwd is the workspace root if it equals or is an ancestor of startDir.
  //    AI agents launched from inside an IDE typically have cwd = workspace
  //    root, even when the AI passes the (deeper) game dir as --workspace.
  if (cwd === startDir || isAncestor(cwd, startDir)) {
    return { root: cwd, decidedBy: 'cwd-ancestor', hint: startDir };
  }

  // 3) Walk up startDir looking for workspace markers.
  let cur = startDir;
  while (true) {
    const marker = workspaceMarker(cur);
    if (marker) return { root: cur, decidedBy: `marker:${marker}`, hint: startDir };
    const parent = path.dirname(cur);
    if (parent === cur) break;
    cur = parent;
  }

  // 4) Fallback: the start dir itself. This is fine when the game dir IS
  //    what the user opened in VSCode (most common case).
  return { root: startDir, decidedBy: 'fallback', hint: startDir };
}

/**
 * @param {string} dir
 * @returns {string | null}  The marker we found (`.git`, `.vscode`,
 *   `*.code-workspace`) or `null`.
 */
function workspaceMarker(dir) {
  try {
    if (fs.existsSync(path.join(dir, '.vscode'))) return '.vscode';
    if (fs.existsSync(path.join(dir, '.git'))) return '.git';
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const ent of entries) {
      if (ent.isFile() && ent.name.endsWith('.code-workspace')) return '*.code-workspace';
    }
  } catch {
    /* ignore EACCES / ENOENT */
  }
  return null;
}

/**
 * @param {string} maybeAncestor
 * @param {string} p
 * @returns {boolean}  true iff `maybeAncestor` is a *strict* ancestor of `p`.
 */
function isAncestor(maybeAncestor, p) {
  const a = path.resolve(maybeAncestor);
  const b = path.resolve(p);
  if (a === b) return false;
  const aWithSep = a.endsWith(path.sep) ? a : a + path.sep;
  return b.startsWith(aWithSep);
}

/**
 * Merge our task entry + input entry into an existing tasks.json shape.
 * Preserves all unrelated tasks/inputs.
 */
function mergeTasksJson(existing, url) {
  const obj = existing && typeof existing === 'object' ? { ...existing } : {};
  if (!obj.version) obj.version = '2.0.0';
  const tasks = Array.isArray(obj.tasks) ? obj.tasks.slice() : [];
  const inputs = Array.isArray(obj.inputs) ? obj.inputs.slice() : [];

  const taskEntry = {
    label: VSC.taskLabel,
    type: 'shell',
    // We only run `echo` so the task has *some* command; the real work is
    // done by VSCode resolving `${input:...}` of type=command before the
    // task runs, which invokes `simpleBrowser.show <url>` as a side effect.
    command: 'echo',
    args: [`\${input:${VSC.inputId}}`],
    presentation: { reveal: 'silent', panel: 'shared', close: true, echo: false, showReuseMessage: false },
    problemMatcher: [],
    detail: '由 weixin-minigame-helper 写入：在 VSCode 内置 Simple Browser 中打开预览 URL',
  };
  const inputEntry = {
    id: VSC.inputId,
    type: 'command',
    command: 'simpleBrowser.show',
    args: url,
  };

  const tIdx = tasks.findIndex((t) => t && t.label === VSC.taskLabel);
  if (tIdx >= 0) tasks[tIdx] = taskEntry; else tasks.push(taskEntry);
  const iIdx = inputs.findIndex((it) => it && it.id === VSC.inputId);
  if (iIdx >= 0) inputs[iIdx] = inputEntry; else inputs.push(inputEntry);

  obj.tasks = tasks;
  obj.inputs = inputs;
  return obj;
}

/**
 * Merge our launch config into an existing launch.json shape.
 * Uses a tiny no-op JS as the debug program; the real work is the
 * preLaunchTask (which opens Simple Browser).
 */
function mergeLaunchJson(existing, noopRelPath) {
  const obj = existing && typeof existing === 'object' ? { ...existing } : {};
  if (!obj.version) obj.version = '0.2.0';
  const configs = Array.isArray(obj.configurations) ? obj.configurations.slice() : [];

  const cfgEntry = {
    name: VSC.launchConfigName,
    type: 'node',
    request: 'launch',
    program: `\${workspaceFolder}/${noopRelPath}`,
    console: 'internalConsole',
    internalConsoleOptions: 'neverOpen',
    preLaunchTask: VSC.taskLabel,
    stopOnEntry: false,
    skipFiles: ['<node_internals>/**'],
  };

  const cIdx = configs.findIndex((c) => c && c.name === VSC.launchConfigName);
  if (cIdx >= 0) configs[cIdx] = cfgEntry; else configs.push(cfgEntry);

  obj.configurations = configs;
  return obj;
}

function setupVscodeBuiltin(url) {
  const { root: ws, decidedBy, hint } = resolveVscodeWorkspaceRoot();
  if (!fs.existsSync(ws) || !fs.statSync(ws).isDirectory()) {
    return emit({ ok: false, error: `Workspace dir does not exist: ${ws}` }, 1);
  }

  const vscDir = path.join(ws, '.vscode');
  const tasksPath = path.join(vscDir, 'tasks.json');
  const launchPath = path.join(vscDir, 'launch.json');
  const noopPath = path.join(vscDir, VSC.noopFile);
  const noopRel = path.posix.join('.vscode', VSC.noopFile);

  let existingTasks;
  let existingLaunch;
  try {
    existingTasks = readJsoncSafe(tasksPath);
    existingLaunch = readJsoncSafe(launchPath);
  } catch (err) {
    return emit({ ok: false, error: err.message }, 1);
  }

  const newTasks = mergeTasksJson(existingTasks, url);
  const newLaunch = mergeLaunchJson(existingLaunch, noopRel);

  writeJsonAtomic(tasksPath, newTasks);
  writeJsonAtomic(launchPath, newLaunch);

  // Tiny no-op debug program. Required because VSCode's `node` debug type
  // demands a `program`. Side-effect free; exits immediately so the F5
  // session ends the moment Simple Browser is opened.
  if (!fs.existsSync(noopPath)) {
    fs.writeFileSync(
      noopPath,
      '// Auto-generated by weixin-minigame-helper. Do NOT delete while using\n' +
      '// the VSCode built-in preview launch config — F5 would fail without it.\n' +
      'process.exit(0);\n',
      'utf8',
    );
  }

  // Note: it's important to tell the user explicitly which directory the
  // launch.json was written into AND which one VSCode must have open for F5
  // to work — these are the same directory, but if the user opened a
  // *different* folder in VSCode (e.g. the game subdir while we wrote into
  // the parent monorepo root), F5 will silently no-op. Always echo `ws`.
  const userInstructions = [
    `1) 切到 VSCode 窗口，确认当前打开的工作区根目录是：${ws}`,
    `   （如果当前打开的是其他目录，需要 File → Open Folder 切到上面这个路径，否则 F5 不会生效。）`,
    `2) 按 F5（或左侧 Run and Debug 面板）→ 选择配置「${VSC.launchConfigName}」→ 启动。`,
    `   等价方法：Cmd/Ctrl+Shift+P → Run Task → 「${VSC.taskLabel}」。`,
    '3) VSCode 内置 Simple Browser 会在编辑器侧边页打开预览 URL。',
    '4) 之后每次代码修改触发 run_game，浏览器内的预览页会自动刷新（同 URL）；',
    '   如果不慎关闭了 Simple Browser 标签页，重做一次 Step 2 即可。',
    `5) 如果想换 URL，重新跑本脚本会自动覆盖 .vscode/tasks.json 中的 args。`,
  ];

  return emit({
    ok: true,
    mode: 'vscode-builtin',
    url,
    workspace: ws,
    workspaceHint: hint,
    workspaceResolvedBy: decidedBy,
    launchConfigName: VSC.launchConfigName,
    taskLabel: VSC.taskLabel,
    files: [
      path.relative(ws, launchPath),
      path.relative(ws, tasksPath),
      path.relative(ws, noopPath),
    ],
    userInstructions,
  });
}

/* -------------------------------------------------------------------------- */
/* Dispatch                                                                   */
/* -------------------------------------------------------------------------- */

if (MODE === 'vscode-builtin') {
  setupVscodeBuiltin(args.url);
} else {
  openExternal(args.url);
}
