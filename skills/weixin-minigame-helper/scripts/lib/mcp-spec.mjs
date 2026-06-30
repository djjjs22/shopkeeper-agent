/**
 * mcp-spec.mjs
 *
 * Single source of truth for everything that both `check-mcp.mjs` and
 * `configure-mcp.mjs` need to agree on:
 *   - Canonical MCP package name + server key.
 *   - Canonical "desired" server entry shape (command/args/env).
 *   - The full registry of known MCP host config paths
 *     (CodeBuddy / Cursor / Claude Desktop / Windsurf / Continue +
 *      VSCode-family agents: Cline, Roo Code, VSCode (1.99+ native MCP),
 *      Claude Code, Trae).
 *
 *   IMPORTANT — VSCode detection is INHERENTLY AMBIGUOUS. We never return
 *   `vscode-native` as a strong detection; the only signal we get from the
 *   environment (TERM_PROGRAM=vscode / VSCODE_PID) is shared by VSCode AND
 *   every fork (Cursor / Windsurf / CodeBuddy / VSCodium / Trae) AND every
 *   in-VSCode extension (Cline / Roo Code / GitHub Copilot Chat / ...).
 *   When only this weak signal is present, we report `host: 'vscode'`
 *   meaning "some VSCode-family host, but we cannot tell which one"; the
 *   caller MUST surface this uncertainty to the user and ask them to pick
 *   a candidate config path.
 *   - Active-host detection via environment variables / parent process.
 *   - Drift comparator (existing entry vs. desired entry).
 *
 * Pure ESM, zero deps, safe to load from any other script in this folder.
 */

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

export const PKG_NAME = '@weadmin/weixin-minigame-helper-mcp';
export const SERVER_KEY = 'weixin-minigame-helper';

/**
 * Canonical desired MCP server entry. We default to `npx`, which works
 * regardless of whether the package is installed globally — npm fetches it on
 * first use and caches it. `install-mcp.mjs` warms that cache.
 *
 * The `--prefer-online` flag ensures we always pick up the latest patch on
 * cold starts; combined with the `@latest` tag this protects against silent
 * version pinning by user-installed npx caches.
 */
export function desiredServerEntry() {
  return {
    command: 'npx',
    args: ['-y', '--prefer-online', `${PKG_NAME}@latest`],
  };
}

/**
 * Render a copy-pastable JSON snippet that the user can drop into ANY MCP host
 * config file by hand, when our auto-detection cannot pick a confident target.
 *
 * The snippet is wrapped in a top-level `mcpServers` object — this is the
 * shape supported by every MCP-aware host we know of (CodeBuddy, Cursor,
 * Windsurf, Claude Desktop, Claude Code, VSCode native, Cline, Roo Code,
 * Continue.dev, Trae). Hosts that store additional state will simply merge
 * this `mcpServers` block into their own structure.
 *
 * @returns {{ json: string, entry: { command: string, args: string[] }, instructions: string[] }}
 */
export function manualSnippet() {
  const entry = desiredServerEntry();
  const wrapped = { mcpServers: { [SERVER_KEY]: entry } };
  const json = JSON.stringify(wrapped, null, 2);
  const instructions = [
    '1) Open your AI agent\'s MCP config file (see the candidate list above, or run `check-mcp.mjs --json` again to print it).',
    '2) If the file already exists, merge the `mcpServers` block below into the existing JSON. Do NOT delete other server entries.',
    '3) If the file does NOT exist yet, create it with parent directories and paste the snippet verbatim.',
    '4) Save the file. Most modern AI agents pick up the change automatically; you do NOT need to restart unless your agent explicitly says it cached the config.',
    '5) Verify by asking the agent to call any `weixin-minigame-helper` tool (e.g. `run_game`).',
  ];
  return { json, entry, instructions };
}

/* -------------------------------------------------------------------------- */
/* Host registry                                                              */
/* -------------------------------------------------------------------------- */

function vscodeUserDir(home, platform) {
  if (platform === 'darwin') return path.join(home, 'Library', 'Application Support', 'Code', 'User');
  if (platform === 'win32') return path.join(process.env.APPDATA || path.join(home, 'AppData', 'Roaming'), 'Code', 'User');
  return path.join(home, '.config', 'Code', 'User');
}

/**
 * Build the full list of known MCP host config locations.
 * Each entry's `path` is absolute. Order matters: detection / merging walk
 * this list top-down, so put the most likely / most modern hosts first.
 *
 * @returns {Array<{ host: string, path: string, scope: 'user'|'workspace', format: 'json' }>}
 */
export function listHostConfigs() {
  const home = os.homedir();
  const platform = process.platform;
  const cwd = process.cwd();
  const vscUser = vscodeUserDir(home, platform);

  /** @type {Array<{ host: string, path: string, scope: 'user'|'workspace', format: 'json' }>} */
  const out = [];
  const push = (host, p, scope = 'user') => out.push({ host, path: p, scope, format: 'json' });

  // CodeBuddy IDE
  push('codebuddy', path.join(home, '.codebuddy', 'mcp.json'));
  push('codebuddy', path.join(cwd, '.codebuddy', 'mcp.json'), 'workspace');

  // Cursor
  push('cursor', path.join(home, '.cursor', 'mcp.json'));
  push('cursor', path.join(cwd, '.cursor', 'mcp.json'), 'workspace');

  // Windsurf (Codeium)
  push('windsurf', path.join(home, '.codeium', 'windsurf', 'mcp_config.json'));

  // Claude Desktop
  if (platform === 'darwin') {
    push('claude-desktop', path.join(home, 'Library', 'Application Support', 'Claude', 'claude_desktop_config.json'));
  } else if (platform === 'win32') {
    const appdata = process.env.APPDATA || path.join(home, 'AppData', 'Roaming');
    push('claude-desktop', path.join(appdata, 'Claude', 'claude_desktop_config.json'));
  } else {
    push('claude-desktop', path.join(home, '.config', 'Claude', 'claude_desktop_config.json'));
  }

  // Claude Code (CLI). Project-local `.mcp.json` takes precedence over user-level.
  push('claude-code', path.join(cwd, '.mcp.json'), 'workspace');
  push('claude-code', path.join(home, '.claude.json'));

  // Continue.dev
  push('continue', path.join(home, '.continue', 'config.json'));

  // ---- VSCode-based AI agents (extensions inside VSCode/VSCodium/Cursor) ----
  // VSCode 1.99+ native MCP support. We deliberately use the host key `vscode`
  // (not `vscode-native`): when the env-var detector reports `host: 'vscode'`
  // it ONLY means "weak VSCode-family signal, identity unknown", and the
  // candidate list below is one of MANY plausible targets the user may want.
  // The orchestrator must NEVER auto-write to these paths from a weak vscode
  // signal — it must ask the user to pick a candidate first.
  push('vscode', path.join(cwd, '.vscode', 'mcp.json'), 'workspace');
  push('vscode', path.join(vscUser, 'mcp.json'));

  // Cline (formerly Claude Dev)
  push(
    'vscode-cline',
    path.join(vscUser, 'globalStorage', 'saoudrizwan.claude-dev', 'settings', 'cline_mcp_settings.json'),
  );

  // Roo Code
  push(
    'vscode-roo-code',
    path.join(vscUser, 'globalStorage', 'rooveterinaryinc.roo-cline', 'settings', 'mcp_settings.json'),
  );

  // Trae
  push('trae', path.join(home, '.trae', 'mcp.json'));

  return out;
}

/* -------------------------------------------------------------------------- */
/* Active-host detection                                                      */
/* -------------------------------------------------------------------------- */

/**
 * Inspect the current process environment to guess which AI host is launching
 * us. This is best-effort — many hosts don't set distinctive env vars. The
 * caller should treat the result as a hint, not a guarantee.
 *
 * `strong` is true only for env vars that uniquely identify a single host.
 * The shared `TERM_PROGRAM=vscode` / `VSCODE_PID` signals are weak because
 * every VSCode fork (Cursor / Windsurf / CodeBuddy / VSCodium) sets them too.
 *
 * @returns {{ host: string|null, evidence: string|null, strong: boolean }}
 */
export function detectActiveHostFromEnv() {
  const env = process.env;

  // Strong signals (single env var unambiguous to one host)
  if (env.CODEBUDDY_VERSION || env.CODEBUDDY_HOME || env.CODEBUDDY_USER_DIR) {
    return { host: 'codebuddy', evidence: 'CODEBUDDY_* env var', strong: true };
  }
  if (env.CURSOR_TRACE_ID || env.CURSOR_USER) {
    return { host: 'cursor', evidence: 'CURSOR_* env var', strong: true };
  }
  if (env.WINDSURF_BIN || env.WINDSURF_VERSION) {
    return { host: 'windsurf', evidence: 'WINDSURF_* env var', strong: true };
  }
  if (env.CLAUDECODE === '1' || env.CLAUDE_CODE === '1') {
    return { host: 'claude-code', evidence: 'CLAUDECODE=1', strong: true };
  }
  if (env.TRAE_HOME || env.TRAE_VERSION) {
    return { host: 'trae', evidence: 'TRAE_* env var', strong: true };
  }
  // macOS Claude Desktop sets __CFBundleIdentifier
  if (env.__CFBundleIdentifier === 'com.anthropic.claudefordesktop') {
    return { host: 'claude-desktop', evidence: '__CFBundleIdentifier=com.anthropic.claudefordesktop', strong: true };
  }

  // Weak: shared by every VSCode fork AND every VSCode extension — can't
  // identify any specific host (vanilla VSCode? Cursor without CURSOR_*?
  // CodeBuddy without CODEBUDDY_*? Cline / Roo Code / Copilot Chat
  // running inside one of the above?). The host MUST be reported as the
  // generic key `vscode` so the orchestrator knows to ask the user.
  if (env.TERM_PROGRAM === 'vscode' || env.VSCODE_PID || env.VSCODE_IPC_HOOK) {
    return {
      host: 'vscode',
      evidence: 'TERM_PROGRAM=vscode / VSCODE_PID (weak — could be vanilla VSCode, any VSCode fork, or any in-VSCode extension)',
      strong: false,
    };
  }

  return { host: null, evidence: null, strong: false };
}

/* -------------------------------------------------------------------------- */
/* Existing-entry extraction + drift detection                                */
/* -------------------------------------------------------------------------- */

export function readJsonSafe(p) {
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

/**
 * Walk a parsed config JSON and return the raw `weixin-minigame-helper` entry
 * if present, regardless of where it nests. We only check one level deep
 * besides the top-level `mcpServers`.
 *
 * @param {*} json
 * @returns {{ entry: any, container: 'root' | 'nested' } | null}
 */
export function extractServerEntry(json, key = SERVER_KEY) {
  if (!json || typeof json !== 'object') return null;
  if (json.mcpServers && typeof json.mcpServers === 'object' && key in json.mcpServers) {
    return { entry: json.mcpServers[key], container: 'root' };
  }
  for (const v of Object.values(json)) {
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      if (v.mcpServers && typeof v.mcpServers === 'object' && key in v.mcpServers) {
        return { entry: v.mcpServers[key], container: 'nested' };
      }
    }
  }
  return null;
}

/**
 * Compare an existing MCP entry against the canonical desired entry.
 *
 * Drift detection is intentionally **conservative** — we only want to fix
 * stale stdio-launcher entries that no longer match our package, NOT to
 * trample user customizations. Rules:
 *
 *   1. existing has `url` / `transport === 'sse'` / `type === 'sse'`  →
 *      user is using a custom transport (e.g. local HTTP dev server).
 *      Treat as **no drift**, surface `customTransport: true`.
 *
 *   2. existing.command differs from desired.command (e.g. `node` instead of
 *      `npx`, pointing to a local checkout) → user customization.
 *      Treat as **no drift**, surface `customCommand: true`.
 *
 *   3. Same command, but `args` differ → genuine drift (e.g. version pinned
 *      to an old release, missing `--prefer-online`). Mark as drift.
 *
 *   4. Otherwise → identical, no drift.
 *
 * User-added auxiliary fields (`env` / `cwd` / `disabled` / `description`)
 * are ignored by this comparator and preserved by `configure-mcp.mjs` when
 * it overwrites.
 *
 * @returns {{ drift: boolean, reason: string|null, customTransport?: boolean, customCommand?: boolean }}
 */
export function compareEntry(existing, desired = desiredServerEntry()) {
  if (!existing || typeof existing !== 'object') {
    return { drift: true, reason: 'existing entry is missing or malformed' };
  }
  // (1) Custom transport — leave alone.
  if (existing.url || existing.transport === 'sse' || existing.type === 'sse' || existing.type === 'http') {
    return { drift: false, reason: null, customTransport: true };
  }
  // (2) Different launcher command — user customization.
  if (existing.command !== desired.command) {
    return { drift: false, reason: null, customCommand: true };
  }
  // (3) Same command — compare args strictly.
  const a = Array.isArray(existing.args) ? existing.args : [];
  const b = desired.args;
  if (a.length !== b.length || a.some((v, i) => v !== b[i])) {
    return { drift: true, reason: `args ${JSON.stringify(a)} !== ${JSON.stringify(b)}` };
  }
  // (4) Identical.
  return { drift: false, reason: null };
}
