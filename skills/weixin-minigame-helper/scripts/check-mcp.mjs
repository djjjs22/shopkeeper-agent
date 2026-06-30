#!/usr/bin/env node
/**
 * check-mcp.mjs
 *
 * Inspect environment readiness for `@weadmin/weixin-minigame-helper-mcp`:
 *   1. Node.js runtime version meets minimum requirement (>=18)
 *   2. The MCP package is installed (resolvable, global, or in npx cache)
 *   3. The MCP server is configured in the **target host's** config file(s)
 *   4. The configured entry actually MATCHES our desired entry (drift check)
 *
 * Target selection — IMPORTANT (this is the whole point of the script):
 *   The script does NOT scan every known mcp.json on disk and pretend
 *   "configured if any of them has the entry". A config in `~/.codebuddy/mcp.json`
 *   tells us nothing about whether Cursor / Cline / vanilla VSCode etc. can
 *   actually read this MCP. Instead we narrow the inspection to a single host:
 *
 *     (a) If --config-path /abs/path is given → inspect that file only.
 *     (b) If --target <host> is given → inspect that host's user+workspace files.
 *     (c) Else, env-var detection:
 *          - STRONG signal (CODEBUDDY_VERSION / CURSOR_TRACE_ID /
 *            WINDSURF_VERSION / CLAUDECODE / TRAE_HOME / claude-desktop bundle)
 *            → inspect that host's candidates only.
 *          - WEAK signal (TERM_PROGRAM=vscode / VSCODE_PID — VSCode-family but
 *            no fork-specific env var) → CANNOT decide. Return
 *            `recommendedAction: "ask-user"` with `configCandidates: []`. The
 *            orchestrator MUST ask the user to either pick `--target <host>`
 *            (vscode / vscode-cline / vscode-roo-code / cursor / codebuddy /
 *            windsurf / trae / claude-desktop / claude-code / continue / ...)
 *            or hand us an explicit `--config-path`.
 *
 *   The output `configCandidates` array therefore contains AT MOST the user
 *   and workspace files of ONE host (or one explicit path), never the full
 *   registry. Listing every known mcp.json was the previous bug — it caused
 *   the script to falsely report `configured=true` based on an entry living
 *   in a host the caller wasn't even using.
 *
 * Usage:
 *   node check-mcp.mjs --json
 *   node check-mcp.mjs --target cursor --json
 *   node check-mcp.mjs --target vscode --json
 *   node check-mcp.mjs --config-path /abs/path/mcp.json --json
 *
 * Output (with --json):
 *   {
 *     "nodeVersion": "20.10.0",
 *     "nodeMajor": 20,
 *     "nodeOk": true,
 *     "nodeMinMajor": 18,
 *
 *     "installed": boolean,
 *     "configured": boolean | null,    // null = unknown, target not decided
 *     "entryDrift": boolean,
 *
 *     "detectedHost": "codebuddy" | "vscode" | null,   // hint from env vars
 *     "detectedHostEvidence": "...",
 *     "detectedHostStrong": boolean,
 *     "targetHost": "codebuddy" | "vscode" | "custom" | null,  // host actually inspected (null when ask-user)
 *     "targetSelectionReason": "explicit-config-path"
 *                            | "explicit-target"
 *                            | "strong-env-signal"
 *                            | "weak-vscode-need-user-input"
 *                            | "no-env-signal-need-user-input",
 *     "targetHostCaveat": string | null,  // STRONG warning when the chosen target
 *                                          // is fragile (esp. `--target vscode`,
 *                                          // which ONLY means VSCode 1.99+ native
 *                                          // MCP — NOT any VSCode extension/fork).
 *                                          // The orchestrator MUST surface this and
 *                                          // re-verify its self-identity before
 *                                          // trusting `configured: true`.
 *
 *     "configCandidates": [   // ONLY for the chosen target host (max 2: user+workspace), or empty when ask-user
 *       {
 *         "host": "codebuddy",
 *         "path": "...",
 *         "scope": "user" | "workspace",
 *         "exists": true,
 *         "hasEntry": true,
 *         "entryConfig": { "command": "...", "args": [...] } | null,
 *         "drift": false,
 *         "driftReason": null,
 *         "customTransport": false,
 *         "customCommand": false,
 *         "lastModifiedMs": 1718000000000 | null
 *       }
 *     ],
 *
 *     "desiredEntry": { "command": "npx", "args": [...] },
 *
 *     "recommendedAction":
 *        "none"            // installed AND configured for the target host
 *      | "install-node"
 *      | "install"
 *      | "configure"
 *      | "configure+install"
 *      | "reconfigure"
 *      | "ask-user",       // we cannot identify the target host
 *     "recommendedTarget": "codebuddy" | null,
 *     "recommendedConfigPath": "/abs/path/mcp.json" | null,
 *
 *     "manualSnippet": { "json": "...", "entry": {...}, "instructions": [...] },
 *     "packageName": "@weadmin/weixin-minigame-helper-mcp",
 *     "serverKey": "weixin-minigame-helper"
 *   }
 *
 * NOTE: This script itself requires Node.js to run. If `node --version` already
 * fails, the AI assistant must instruct the user to install Node.js BEFORE
 * invoking this script (see SKILL.md "关于 Node 环境" note).
 */

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';

import {
  PKG_NAME,
  SERVER_KEY,
  desiredServerEntry,
  listHostConfigs,
  detectActiveHostFromEnv,
  readJsonSafe,
  extractServerEntry,
  compareEntry,
  manualSnippet,
} from './lib/mcp-spec.mjs';

const MIN_NODE_MAJOR = 18;

/* -------------------------------------------------------------------------- */
/* CLI args                                                                   */
/* -------------------------------------------------------------------------- */

function parseArgs(list) {
  const o = { json: false, target: null, configPath: null };
  for (let i = 0; i < list.length; i++) {
    const a = list[i];
    if (a === '--json') o.json = true;
    else if (a === '--target') o.target = list[++i];
    else if (a === '--config-path') o.configPath = list[++i];
  }
  return o;
}
const args = parseArgs(process.argv.slice(2));
const wantJson = args.json;

/** Parse `process.versions.node` and check it meets minimum. */
function checkNode() {
  const raw = process.versions.node; // e.g. "20.10.0"
  const major = Number.parseInt(raw.split('.')[0], 10);
  return {
    nodeVersion: raw,
    nodeMajor: major,
    nodeOk: Number.isFinite(major) && major >= MIN_NODE_MAJOR,
    nodeMinMajor: MIN_NODE_MAJOR,
  };
}

/* -------------------------------------------------------------------------- */
/* Target resolution                                                          */
/* -------------------------------------------------------------------------- */

/**
 * Decide which mcp.json files to inspect (and only those). Returns one of:
 *   { kind: 'configs', host, configs: [{path, scope}], reason }
 *   { kind: 'ask-user', reason, hint }
 *
 * Resolution order:
 *   1. --config-path /abs/file.json  → that single file (host: 'custom')
 *   2. --target <name>               → all paths registered under that host
 *   3. STRONG env-var signal         → that host's paths
 *   4. WEAK 'vscode' signal          → ask-user (cannot disambiguate)
 *   5. No env signal at all          → ask-user
 */
function resolveInspectionTarget(detectedHost, detectedHostStrong) {
  if (args.configPath) {
    return {
      kind: 'configs',
      host: 'custom',
      configs: [{ path: path.resolve(args.configPath), scope: 'user' }],
      reason: 'explicit-config-path',
    };
  }

  if (args.target) {
    const all = listHostConfigs().filter((c) => c.host === args.target);
    if (all.length === 0) {
      return {
        kind: 'ask-user',
        reason: `unknown-target:${args.target}`,
        hint: `Unknown --target "${args.target}". Pass --config-path /abs/path/mcp.json or one of the known hosts (codebuddy, cursor, windsurf, claude-desktop, claude-code, continue, vscode, vscode-cline, vscode-roo-code, trae).`,
      };
    }
    return {
      kind: 'configs',
      host: args.target,
      configs: all.map((c) => ({ path: c.path, scope: c.scope })),
      reason: 'explicit-target',
    };
  }

  // No explicit override — fall back to env detection.
  if (detectedHost && detectedHostStrong) {
    const all = listHostConfigs().filter((c) => c.host === detectedHost);
    if (all.length > 0) {
      return {
        kind: 'configs',
        host: detectedHost,
        configs: all.map((c) => ({ path: c.path, scope: c.scope })),
        reason: 'strong-env-signal',
      };
    }
    // Strong signal but no registered paths → still ask user.
    return {
      kind: 'ask-user',
      reason: 'strong-env-signal-no-paths',
      hint: `Detected host "${detectedHost}" but no known config paths registered for it. Pass --config-path /abs/path/mcp.json.`,
    };
  }

  if (detectedHost === 'vscode' && !detectedHostStrong) {
    return {
      kind: 'ask-user',
      reason: 'weak-vscode-need-user-input',
      hint: 'Detected only a weak VSCode-family signal (TERM_PROGRAM=vscode / VSCODE_PID). This is shared by vanilla VSCode, every VSCode fork (Cursor / Windsurf / CodeBuddy / VSCodium / Trae) and every in-VSCode AI extension (Cline / Roo Code / Copilot Chat / ...). Cannot decide which mcp.json to inspect — ask the user which AI tool they are using and pass --target <host> or --config-path /abs/path/mcp.json.',
    };
  }

  return {
    kind: 'ask-user',
    reason: 'no-env-signal-need-user-input',
    hint: 'No host environment signal at all. Ask the user which AI tool they are using and pass --target <host> or --config-path /abs/path/mcp.json.',
  };
}

/* -------------------------------------------------------------------------- */
/* Config inspection                                                          */
/* -------------------------------------------------------------------------- */

const desired = desiredServerEntry();

function inspectConfigs(configs) {
  const detail = [];
  let anyConfigured = false;
  let anyDrift = false;

  for (const c of configs) {
    const exists = fs.existsSync(c.path);
    let hasEntry = false;
    let entryConfig = null;
    let drift = false;
    let driftReason = null;
    let customTransport = false;
    let customCommand = false;
    let lastModifiedMs = null;

    if (exists) {
      try {
        lastModifiedMs = fs.statSync(c.path).mtimeMs;
      } catch {
        /* ignore */
      }
      const json = readJsonSafe(c.path);
      const found = extractServerEntry(json, SERVER_KEY);
      if (found) {
        hasEntry = true;
        anyConfigured = true;
        entryConfig = found.entry;
        const cmp = compareEntry(found.entry, desired);
        drift = cmp.drift;
        driftReason = cmp.reason;
        customTransport = !!cmp.customTransport;
        customCommand = !!cmp.customCommand;
        if (drift) anyDrift = true;
      }
    }

    detail.push({
      host: c.host || null,
      path: c.path,
      scope: c.scope,
      exists,
      hasEntry,
      entryConfig,
      drift,
      driftReason,
      customTransport,
      customCommand,
      lastModifiedMs,
    });
  }

  return { detail, anyConfigured, anyDrift };
}

/* -------------------------------------------------------------------------- */
/* npm install detection                                                      */
/* -------------------------------------------------------------------------- */

function npmCmd() {
  return process.platform === 'win32' ? 'npm.cmd' : 'npm';
}

function checkInstalled() {
  // 1) require.resolve from a temp module location.
  try {
    const req = createRequire(path.join(os.homedir(), 'noop.js'));
    req.resolve(`${PKG_NAME}/package.json`);
    return true;
  } catch {
    /* fall through */
  }

  // 2) `npm root -g` + fs check.
  try {
    const stdout = execFileSync(npmCmd(), ['root', '-g'], {
      stdio: ['ignore', 'pipe', 'ignore'],
      encoding: 'utf8',
      timeout: 5000,
    }).trim();
    if (stdout && fs.existsSync(path.join(stdout, PKG_NAME, 'package.json'))) {
      return true;
    }
  } catch {
    /* ignore */
  }

  // 3) npx cache.
  const npxCache =
    process.platform === 'win32'
      ? path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), 'npm-cache', '_npx')
      : path.join(os.homedir(), '.npm', '_npx');
  if (fs.existsSync(npxCache)) {
    try {
      for (const entry of fs.readdirSync(npxCache)) {
        const pkgJson = path.join(npxCache, entry, 'node_modules', PKG_NAME, 'package.json');
        if (fs.existsSync(pkgJson)) return true;
      }
    } catch {
      /* ignore */
    }
  }

  return false;
}

/* -------------------------------------------------------------------------- */
/* Main                                                                       */
/* -------------------------------------------------------------------------- */

const node = checkNode();
const { host: detectedHost, evidence: detectedHostEvidence, strong: detectedHostStrong } = detectActiveHostFromEnv();
const target = resolveInspectionTarget(detectedHost, detectedHostStrong);

const installed = checkInstalled();
const snippet = manualSnippet();

/* -------------------------------------------------------------------------- */
/* Build a caveat string when the chosen target is risky.                     */
/*                                                                            */
/* The most dangerous case: the LLM blindly passes `--target vscode` because  */
/* the user's environment looks "VSCode-ish". But the registry's `vscode`     */
/* host ONLY contains files consumed by VSCode 1.99+ NATIVE MCP (built-in     */
/* Copilot Chat). Every VSCode extension (CodeBuddy / Cline / Roo Code /      */
/* etc.) and every VSCode fork (Cursor / Windsurf / Trae / VSCodium) reads    */
/* MCP from a completely different file. Reporting `configured: true` for    */
/* such a target is technically correct but operationally misleading: it      */
/* tells the LLM "everything's set up" when in fact MCP is NOT available     */
/* to the actual AI consumer running the script.                              */
/* -------------------------------------------------------------------------- */
function buildTargetHostCaveat(targetKind, targetHost, reason) {
  if (targetKind !== 'configs') return null;
  if (targetHost === 'vscode') {
    return [
      'TARGET=vscode means VSCode 1.99+ NATIVE MCP ONLY (built-in Copilot Chat).',
      'It does NOT cover any AI extension running inside VSCode (CodeBuddy / Cline /',
      'Roo Code / Continue / etc.) and does NOT cover any VSCode fork (Cursor /',
      'Windsurf / Trae / VSCodium). Each of those reads MCP from a different file.',
      'BEFORE trusting `configured: true`, the AI assistant calling this script MUST',
      'verify from its OWN system prompt that it really is vanilla VSCode native',
      'Copilot Chat. If you are any other brand (look at your system prompt!), this',
      'configuration is NOT for you — re-run with the correct --target (codebuddy /',
      'vscode-cline / vscode-roo-code / cursor / windsurf / trae / claude-code /',
      'claude-desktop / continue) or with --config-path /abs/path/mcp.json.',
    ].join(' ');
  }
  if (reason === 'explicit-target' && !detectedHostStrong) {
    return (
      `TARGET=${targetHost} was chosen explicitly while there is NO strong env-var ` +
      'signal confirming the AI host. Make sure you (the AI assistant) identified ' +
      `yourself as "${targetHost}" from your OWN system prompt — do NOT infer the ` +
      "target from the user's environment description. If unsure, drop --target and " +
      'ask the user via the SKILL\'s Step 3.1 question list.'
    );
  }
  return null;
}

let configCandidates = [];
let anyConfigured = false;
let anyDrift = false;
let recommendedAction;
let recommendedTarget = null;
let recommendedConfigPath = null;

if (target.kind === 'ask-user') {
  // We could not narrow down to a host. DO NOT inspect anything.
  recommendedAction = node.nodeOk ? 'ask-user' : 'install-node';
} else {
  // Attach host on each config we plan to inspect.
  const inspectList = target.configs.map((c) => ({ ...c, host: target.host }));
  const insp = inspectConfigs(inspectList);
  configCandidates = insp.detail;
  anyConfigured = insp.anyConfigured;
  anyDrift = insp.anyDrift;

  if (!node.nodeOk) recommendedAction = 'install-node';
  else if (anyDrift) recommendedAction = 'reconfigure';
  else if (anyConfigured && installed) recommendedAction = 'none';
  else if (!anyConfigured && !installed) recommendedAction = 'configure+install';
  else if (!installed) recommendedAction = 'install';
  else recommendedAction = 'configure';

  // Pick a single path inside the chosen host's candidates for write-back.
  // Preference: existing-with-drift (fix in place) > existing > workspace > user > first.
  const driftHit = configCandidates.find((c) => c.hasEntry && c.drift);
  const existing = configCandidates.find((c) => c.exists);
  const workspace = configCandidates.find((c) => c.scope === 'workspace');
  const pick = driftHit || existing || workspace || configCandidates[0] || null;
  if (pick) {
    recommendedTarget = target.host;
    recommendedConfigPath = pick.path;
  }
}

const result = {
  ...node,
  installed,
  // `configured` is null when we did not inspect anything (ask-user).
  configured: target.kind === 'ask-user' ? null : anyConfigured,
  entryDrift: anyDrift,
  detectedHost,
  detectedHostEvidence,
  detectedHostStrong,
  targetHost: target.kind === 'configs' ? target.host : null,
  targetSelectionReason: target.reason,
  targetSelectionHint: target.kind === 'ask-user' ? target.hint : null,
  targetHostCaveat: buildTargetHostCaveat(
    target.kind,
    target.kind === 'configs' ? target.host : null,
    target.reason,
  ),
  configCandidates,
  desiredEntry: desired,
  recommendedAction,
  recommendedTarget,
  recommendedConfigPath,
  // Always include a copy-pastable snippet so the LLM can offer it to the
  // user whenever automatic configuration is undesired or impossible.
  manualSnippet: {
    json: snippet.json,
    entry: snippet.entry,
    instructions: snippet.instructions,
  },
  packageName: PKG_NAME,
  serverKey: SERVER_KEY,
};

if (wantJson) {
  process.stdout.write(JSON.stringify(result, null, 2) + '\n');
} else {
  console.log(`Node             : ${node.nodeVersion} (min ${MIN_NODE_MAJOR}.x, ok=${node.nodeOk})`);
  console.log(`MCP package      : ${PKG_NAME}`);
  console.log(`installed        : ${installed}`);
  console.log(`configured       : ${target.kind === 'ask-user' ? '(unknown — target host not decided)' : anyConfigured}`);
  console.log(`entryDrift       : ${anyDrift}`);
  console.log(`detectedHost     : ${detectedHost || '(unknown)'}  strong=${detectedHostStrong}  ${detectedHostEvidence ? '[' + detectedHostEvidence + ']' : ''}`);
  console.log(`targetHost       : ${result.targetHost || '(none — see hint)'}`);
  console.log(`reason           : ${target.reason}`);
  if (result.targetHostCaveat) {
    console.log('');
    console.log('!! TARGET CAVEAT !!');
    console.log('   ' + result.targetHostCaveat);
    console.log('');
  }
  console.log(`recommended      : ${recommendedAction}${recommendedTarget ? ` → ${recommendedTarget}` : ''}`);

  if (target.kind === 'ask-user') {
    console.log('\n[!] Cannot decide which mcp.json belongs to the active AI host.');
    console.log('    ' + target.hint);
    console.log('\nRe-run this script with one of:');
    console.log('  --target codebuddy | cursor | windsurf | claude-desktop | claude-code |');
    console.log('           continue | vscode | vscode-cline | vscode-roo-code | trae');
    console.log('  --config-path /absolute/path/to/mcp.json');
  } else {
    console.log(`config files for host "${target.host}" (${configCandidates.length}):`);
    for (const c of configCandidates) {
      let flag;
      if (c.hasEntry && c.drift) flag = '⚠ drift   ';
      else if (c.hasEntry && c.customTransport) flag = '~ custom-T';
      else if (c.hasEntry && c.customCommand) flag = '~ custom-C';
      else if (c.hasEntry) flag = '✓ ok      ';
      else if (c.exists) flag = '· exists  ';
      else flag = '  missing ';
      console.log(`  [${(c.scope || '').padEnd(9)}] ${flag} ${c.path}`);
      if (c.drift) console.log(`       drift: ${c.driftReason}`);
    }
  }

  if (
    target.kind === 'ask-user' ||
    recommendedAction === 'configure' ||
    recommendedAction === 'configure+install' ||
    recommendedAction === 'reconfigure'
  ) {
    console.log('\nIf you prefer to configure manually, paste the following block into your');
    console.log('agent\'s mcp.json (merging with existing `mcpServers` if present):');
    console.log('\n' + snippet.json + '\n');
    console.log('Most agents pick up new MCP entries automatically; restart the agent ONLY if it explicitly says so.');
  }
}
