#!/usr/bin/env node
/**
 * configure-mcp.mjs
 *
 * Add or fix the `weixin-minigame-helper` MCP server entry in the target
 * host's configuration file.
 *
 * Usage:
 *   node configure-mcp.mjs --target auto                  (env-var + filesystem auto-detect)
 *   node configure-mcp.mjs --target codebuddy
 *   node configure-mcp.mjs --target codebuddy-workspace
 *   node configure-mcp.mjs --target cursor
 *   node configure-mcp.mjs --target claude-desktop
 *   node configure-mcp.mjs --target claude-code
 *   node configure-mcp.mjs --target windsurf
 *   node configure-mcp.mjs --target continue
 *   node configure-mcp.mjs --target vscode
 *   node configure-mcp.mjs --target vscode-cline
 *   node configure-mcp.mjs --target vscode-roo-code
 *   node configure-mcp.mjs --target trae
 *   node configure-mcp.mjs --config-path /abs/path/mcp.json
 *   node configure-mcp.mjs --print-snippet                (ONLY print the manual mcp.json snippet, no writes)
 *
 * Flags:
 *   --json            Emit machine-readable output.
 *   --force           Overwrite existing entry even if it already matches.
 *   --no-drift-fix    Do NOT auto-overwrite when an entry exists but drifted
 *                     from the desired config. (Default: drift IS fixed.)
 *   --allow-ambiguous With `--target auto`, write to the highest-scored
 *                     candidate even when multiple plausible configs exist.
 *                     Without this flag, ambiguous detection aborts with
 *                     `ok:false, ambiguous:true` plus the candidate list,
 *                     so the calling LLM can ask the user to choose.
 *   --print-snippet   Print only the copy-pastable mcp.json snippet, then
 *                     exit. Useful when the user wants to configure by hand.
 *
 * Output (with --json):
 *   { "ok": true, "configPath": "...", "host": "codebuddy",
 *     "needsRestart": false,
 *     "action": "created"|"merged"|"overwritten"|"unchanged",
 *     "previousEntry": {...}|null, "newEntry": {...} }
 *   { "ok": false, "ambiguous": true,
 *     "reason": "Detected only a weak VSCode-family signal ... — must ask the user.",
 *     "detectedHost": "vscode" | null,
 *     "detectedHostEvidence": "...",
 *     "manualSnippet": { json, entry, instructions },
 *     "hint": "Re-run with --target <host> or --config-path /abs/path/mcp.json" }
 *   { "ok": false, "error": "..." }
 *
 *   NOTE 1: `needsRestart` is always `false`. Most modern AI agents pick up
 *   MCP config changes automatically; the orchestrator should NOT prompt the
 *   user to restart.
 *
 *   NOTE 2: When `--target auto` cannot pin down a host (weak `vscode`
 *   signal, or no env signal at all), this script REFUSES to write and
 *   returns `ambiguous: true` WITHOUT any `candidates` list. We deliberately
 *   do not dump every known mcp.json — that misled callers into thinking the
 *   script "knew" which one mattered. The orchestrator must ask the user to
 *   provide `--target <host>` or `--config-path /abs/path/mcp.json`.
 *
 *   `--allow-ambiguous` is an opt-in escape hatch: when passed, the script
 *   falls back to scoring every registered mcp.json and writing to the
 *   highest-scored existing one. Discouraged.
 *
 * Behaviour:
 *   - Existing JSON is preserved; we only touch `mcpServers[serverKey]`.
 *   - If file doesn't exist, parents are created.
 *   - If existing entry equals the desired entry, we leave it untouched
 *     (`action: "unchanged"`).
 *   - If existing entry differs (drift), we overwrite it by default
 *     (`action: "overwritten"`). Pass --no-drift-fix to opt out.
 *   - If `--target auto` cannot identify the host, we abort cleanly with
 *     `ok:false, ambiguous:true` (no candidate list).
 */

import fs from 'node:fs';
import path from 'node:path';

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

void PKG_NAME; // re-exported for parity with check-mcp; keep import side-effect.

const argv = process.argv.slice(2);
const args = parseArgs(argv);

function parseArgs(list) {
  const o = {
    target: 'auto',
    configPath: null,
    json: false,
    force: false,
    driftFix: true,
    allowAmbiguous: false,
    printSnippet: false,
  };
  for (let i = 0; i < list.length; i++) {
    const a = list[i];
    if (a === '--json') o.json = true;
    else if (a === '--force') o.force = true;
    else if (a === '--no-drift-fix') o.driftFix = false;
    else if (a === '--allow-ambiguous') o.allowAmbiguous = true;
    else if (a === '--print-snippet') o.printSnippet = true;
    else if (a === '--target') o.target = list[++i];
    else if (a === '--config-path') o.configPath = list[++i];
  }
  return o;
}

/**
 * Build a strong caveat string when the chosen target host is fragile.
 * The most dangerous case: the LLM blindly passes `--target vscode` because
 * the user's environment "looks like VSCode". The `vscode` host key in our
 * registry corresponds ONLY to VSCode 1.99+ NATIVE MCP (built-in Copilot
 * Chat) — every VSCode extension and every VSCode fork reads MCP from a
 * different file. Writing to `vscode` mcp.json when the AI consumer is, say,
 * the CodeBuddy extension is operationally a no-op (CodeBuddy never reads
 * that file) and silently misleads the LLM into "MCP configured ✓".
 */
function buildTargetHostCaveat(host, hadStrongEnvSignal) {
  if (host === 'vscode') {
    return [
      'TARGET=vscode means VSCode 1.99+ NATIVE MCP ONLY (built-in Copilot Chat).',
      'It does NOT cover any AI extension running inside VSCode (CodeBuddy / Cline /',
      'Roo Code / Continue / etc.) and does NOT cover any VSCode fork (Cursor /',
      'Windsurf / Trae / VSCodium). If you (the AI assistant) are not vanilla VSCode',
      'native Copilot Chat, this write does NOT make MCP available to YOU. Identify',
      'yourself from your OWN system prompt and re-run with the correct --target.',
    ].join(' ');
  }
  if (!hadStrongEnvSignal && host && host !== 'custom') {
    return (
      `TARGET=${host} was chosen explicitly while there is NO strong env-var ` +
      'signal confirming the AI host. Make sure you (the AI assistant) identified ' +
      `yourself as "${host}" from your OWN system prompt — do NOT infer the ` +
      "target from the user's environment description."
    );
  }
  return null;
}

function emit(payload) {
  if (args.json) process.stdout.write(JSON.stringify(payload, null, 2) + '\n');
  else if (payload.ok) {
    console.log(`[configure-mcp] ${payload.action.padEnd(11)} ${payload.host || ''}  ${payload.configPath}`);
    if (payload.targetHostCaveat) {
      console.log('');
      console.log('!! TARGET CAVEAT !!');
      console.log('   ' + payload.targetHostCaveat);
    }
  }
  else if (payload.ambiguous) {
    console.error('[configure-mcp] CANNOT IDENTIFY TARGET HOST — refusing to write.');
    console.error(`reason: ${payload.reason}`);
    if (payload.detectedHostEvidence) console.error(`detected: ${payload.detectedHostEvidence}`);
    console.error('\nThe script does NOT guess which mcp.json belongs to your AI host. Please tell us:');
    console.error('  --target codebuddy | cursor | windsurf | claude-desktop | claude-code |');
    console.error('           continue | vscode | vscode-cline | vscode-roo-code | trae');
    console.error('  --config-path /absolute/path/to/mcp.json');
    console.error('\nOr paste the snippet below into your agent\'s mcp.json by hand:');
    console.error('\n' + payload.manualSnippet.json + '\n');
  }
  else console.error(`[configure-mcp] FAIL  ${payload.error}`);
  process.exit(payload.ok ? 0 : 1);
}

function hostConfigPaths(host) {
  return listHostConfigs()
    .filter((c) => c.host === host && c.scope !== 'workspace')
    .map((c) => c.path);
}

function hostWorkspaceConfigPaths(host) {
  return listHostConfigs()
    .filter((c) => c.host === host && c.scope === 'workspace')
    .map((c) => c.path);
}

/**
 * Score every known host config the same way `check-mcp.mjs` does. Returned
 * array is sorted high-score first.
 */
function scoreAllCandidates(detectedHost) {
  const now = Date.now();
  const THIRTY_DAYS = 30 * 24 * 3600 * 1000;
  const out = listHostConfigs().map((c) => {
    const exists = fs.existsSync(c.path);
    let hasEntry = false;
    let drift = false;
    let lastModifiedMs = null;
    if (exists) {
      try { lastModifiedMs = fs.statSync(c.path).mtimeMs; } catch { /* ignore */ }
      const json = readJsonSafe(c.path);
      const found = extractServerEntry(json, SERVER_KEY);
      if (found) {
        hasEntry = true;
        drift = compareEntry(found.entry).drift;
      }
    }
    let s = 0;
    if (detectedHost && c.host === detectedHost) s += 10;
    if (hasEntry) s += 6;
    if (c.scope === 'workspace') s += 4;
    if (exists) s += 1;
    if (lastModifiedMs && now - lastModifiedMs < THIRTY_DAYS) s += 3;
    return { host: c.host, path: c.path, scope: c.scope, exists, hasEntry, drift, lastModifiedMs, score: s };
  });
  out.sort((a, b) => b.score - a.score);
  return out;
}

/**
 * `isAmbiguous` was previously used by `--target auto` to decide between
 * filesystem scoring vs. asking the user. The new `resolveTarget()` does a
 * strict "must have a strong env-var signal" check instead: weak `vscode`
 * and "no signal" both refuse cleanly, so this helper is no longer needed.
 * Removed intentionally; if you reintroduce filesystem scoring, also
 * reintroduce the heuristic here.
 */

function resolveTarget() {
  // Explicit path wins.
  if (args.configPath) {
    return { host: args.target === 'auto' ? 'custom' : args.target, configPath: path.resolve(args.configPath) };
  }

  if (args.target !== 'auto') {
    // Special suffix `-workspace` selects the project-local file for that host.
    if (args.target.endsWith('-workspace')) {
      const baseHost = args.target.slice(0, -'-workspace'.length);
      const ws = hostWorkspaceConfigPaths(baseHost);
      if (ws.length === 0) return { error: `Unknown --target "${args.target}"` };
      return { host: args.target, configPath: ws[0] };
    }
    const cands = hostConfigPaths(args.target);
    if (cands.length === 0) return { error: `Unknown --target "${args.target}"` };
    const existing = cands.find((p) => fs.existsSync(p));
    return { host: args.target, configPath: existing || cands[0] };
  }

  // --target auto: env-var detection only. No filesystem-scoring fallback —
  // we never guess which mcp.json belongs to the active host.
  const env = detectActiveHostFromEnv();
  if (env.host && env.strong) {
    const cands = hostConfigPaths(env.host);
    if (cands.length > 0) {
      const existing = cands.find((p) => fs.existsSync(p));
      return { host: env.host, configPath: existing || cands[0], evidence: env.evidence };
    }
    // Strong signal but no registered paths for it — still refuse.
    return {
      ambiguous: true,
      reason: `Strong env-var signal points to host "${env.host}" but no known MCP config paths are registered for that host. Pass --config-path /abs/path/mcp.json.`,
      detectedHost: env.host,
      detectedHostEvidence: env.evidence,
    };
  }

  // Escape hatch: power user explicitly opts in to "guess across the whole
  // registry by score". Default behaviour does NOT do this.
  if (args.allowAmbiguous) {
    const scored = scoreAllCandidates(null);
    const top = scored.find((c) => c.exists) || scored[0];
    if (top) return { host: top.host, configPath: top.path, evidence: env.evidence || null };
  }

  // Weak `vscode` signal or no signal at all → refuse cleanly. Ask user.
  if (env.host === 'vscode' && !env.strong) {
    return {
      ambiguous: true,
      reason:
        'Detected only a weak VSCode-family signal (TERM_PROGRAM=vscode / VSCODE_PID). This is shared by vanilla VSCode, every VSCode fork (Cursor / Windsurf / CodeBuddy / VSCodium / Trae) and every in-VSCode AI extension (Cline / Roo Code / Copilot Chat / ...). Cannot decide which mcp.json to write — ask the user.',
      detectedHost: 'vscode',
      detectedHostEvidence: env.evidence,
    };
  }
  return {
    ambiguous: true,
    reason: 'No host environment signal at all. Cannot decide which mcp.json to write — ask the user.',
    detectedHost: null,
    detectedHostEvidence: null,
  };
}

function readJsonOrEmpty(p) {
  if (!fs.existsSync(p)) return {};
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8')) ?? {};
  } catch (err) {
    throw new Error(`Failed to parse JSON at ${p}: ${err.message}`);
  }
}

function writeJsonAtomic(p, obj) {
  const dir = path.dirname(p);
  fs.mkdirSync(dir, { recursive: true });
  const tmp = `${p}.tmp.${process.pid}`;
  fs.writeFileSync(tmp, JSON.stringify(obj, null, 2) + '\n', 'utf8');
  fs.renameSync(tmp, p);
}

function main() {
  // Short-circuit: just print the manual snippet for the user to copy.
  if (args.printSnippet) {
    const snippet = manualSnippet();
    if (args.json) {
      process.stdout.write(JSON.stringify({ ok: true, action: 'print-snippet', manualSnippet: snippet }, null, 2) + '\n');
    } else {
      console.log('Copy the following block into your agent\'s mcp.json:\n');
      console.log(snippet.json);
      console.log('\nMost agents pick up the change automatically — no restart needed in the typical case.');
    }
    process.exit(0);
  }

  const resolved = resolveTarget();

  // Ambiguous auto-detect: refuse to write. We do NOT dump the full candidate
  // list any more — we just tell the caller "ask the user for --target or
  // --config-path". Listing every known mcp.json was the previous misbehaviour
  // (it implied we knew which one mattered when we didn't).
  if (resolved.ambiguous) {
    return emit({
      ok: false,
      ambiguous: true,
      reason: resolved.reason,
      detectedHost: resolved.detectedHost,
      detectedHostEvidence: resolved.detectedHostEvidence,
      manualSnippet: manualSnippet(),
      hint: 'Re-run with --target <host> (codebuddy | cursor | windsurf | claude-desktop | claude-code | continue | vscode | vscode-cline | vscode-roo-code | trae) or --config-path /abs/path/mcp.json.',
    });
  }

  if (resolved.error) return emit({ ok: false, error: resolved.error });
  const { host, configPath } = resolved;
  const targetHostCaveat = buildTargetHostCaveat(host, !!resolved.evidence);

  const existedBefore = fs.existsSync(configPath);

  let json;
  try {
    json = readJsonOrEmpty(configPath);
  } catch (err) {
    return emit({ ok: false, error: err.message });
  }

  if (!json.mcpServers || typeof json.mcpServers !== 'object') {
    json.mcpServers = {};
  }

  const desired = desiredServerEntry();
  const found = extractServerEntry(json, SERVER_KEY);
  const previousEntry = found ? found.entry : null;

  /** @type {'created'|'merged'|'overwritten'|'unchanged'} */
  let action;

  if (previousEntry) {
    const cmp = compareEntry(previousEntry, desired);
    if (!cmp.drift && !args.force) {
      return emit({
        ok: true,
        host,
        configPath,
        needsRestart: false,
        action: 'unchanged',
        previousEntry,
        newEntry: previousEntry,
        note: `Entry "${SERVER_KEY}" already matches desired config; nothing to do.`,
        targetHostCaveat,
      });
    }
    if (cmp.drift && !args.driftFix && !args.force) {
      return emit({
        ok: false,
        host,
        configPath,
        error: `Entry "${SERVER_KEY}" exists but drifted (${cmp.reason}). Pass --force or omit --no-drift-fix to overwrite.`,
        previousEntry,
      });
    }
    // Overwrite — but preserve any user-added auxiliary fields (env, cwd, disabled, description).
    const preserved = {};
    if (previousEntry && typeof previousEntry === 'object') {
      for (const k of Object.keys(previousEntry)) {
        if (k !== 'command' && k !== 'args') preserved[k] = previousEntry[k];
      }
    }
    json.mcpServers[SERVER_KEY] = { ...desired, ...preserved };
    action = 'overwritten';
  } else {
    json.mcpServers[SERVER_KEY] = desired;
    action = existedBefore ? 'merged' : 'created';
  }

  try {
    writeJsonAtomic(configPath, json);
  } catch (err) {
    return emit({ ok: false, error: `Failed to write ${configPath}: ${err.message}` });
  }

  emit({
    ok: true,
    host,
    configPath,
    needsRestart: false,
    action,
    previousEntry,
    newEntry: json.mcpServers[SERVER_KEY],
    evidence: resolved.evidence || null,
    targetHostCaveat,
  });
}

main();
