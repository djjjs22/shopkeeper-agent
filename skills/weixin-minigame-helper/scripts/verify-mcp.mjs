#!/usr/bin/env node
/**
 * verify-mcp.mjs
 *
 * Actually spawn `@weadmin/weixin-minigame-helper-mcp` via the same `npx -y`
 * command we write into mcp.json, send a real MCP `initialize` JSON-RPC
 * request over its stdin, and verify it responds. This is the "does the
 * package actually run on this machine" smoke test.
 *
 * Why this exists
 * ---------------
 * `check-mcp.mjs` only checks whether `mcp.json` *contains* an entry. It
 * cannot tell whether that entry, when spawned by the AI host, will produce
 * a working MCP server. `install-mcp.mjs` only checks whether the package is
 * resolvable on disk. Neither catches:
 *   - npm registry / proxy misconfiguration that makes `npx -y` hang.
 *   - A broken postinstall on this Node version.
 *   - A globally pinned old version that no longer speaks the JSON-RPC dialect.
 *   - Sandboxes that block child-process stdin.
 *
 * What this script does
 * ---------------------
 * 1. Spawn `npx -y --prefer-online @weadmin/weixin-minigame-helper-mcp@latest`.
 * 2. Send a single MCP `initialize` request over stdin (LSP/JSON-RPC framing).
 * 3. Wait at most --timeout-ms (default 25000ms) for an `initialize` response.
 * 4. Kill the process and report the outcome.
 *
 * IMPORTANT — transport model (read this if you think MCP needs a port):
 *   This MCP server uses **stdio transport**. It has NO HTTP port and NO URL
 *   of its own. The AI host spawns the npx command on demand and talks to it
 *   over stdin/stdout. If you see entries like `{"url":"http://127.0.0.1:.../mcp"}`
 *   in some host's mcp.json, that URL belongs to that host's *internal* MCP
 *   gateway (e.g. CodeBuddy aggregates multiple stdio MCP servers behind one
 *   local HTTP endpoint). It is NOT the address of this server, and you do
 *   NOT need to "start" anything before configuring mcp.json.
 *
 * Output (with --json):
 *   { ok: true,  durationMs, serverInfo: {...}, protocolVersion }
 *   { ok: false, durationMs, stage, error, stderrTail, hint }
 *
 * Exit codes:
 *   0 — verified
 *   1 — verification failed (see `stage` and `error`)
 */

import { spawn } from 'node:child_process';

const PKG_NAME = '@weadmin/weixin-minigame-helper-mcp';

function parseArgs(list) {
  const o = { json: false, timeoutMs: 25000 };
  for (let i = 0; i < list.length; i++) {
    const a = list[i];
    if (a === '--json') o.json = true;
    else if (a === '--timeout-ms') o.timeoutMs = Number.parseInt(list[++i], 10) || 25000;
  }
  return o;
}
const args = parseArgs(process.argv.slice(2));

function emit(payload) {
  if (args.json) {
    process.stdout.write(JSON.stringify(payload, null, 2) + '\n');
  } else if (payload.ok) {
    console.log(
      `[verify-mcp] OK  duration=${payload.durationMs}ms  ` +
        `server=${payload.serverInfo?.name || '?'}@${payload.serverInfo?.version || '?'}  ` +
        `protocol=${payload.protocolVersion || '?'}`,
    );
  } else {
    console.error(`[verify-mcp] FAIL  stage=${payload.stage}  ${payload.error}`);
    if (payload.stderrTail) console.error('--- stderr tail ---\n' + payload.stderrTail);
    if (payload.hint) console.error('hint: ' + payload.hint);
  }
  process.exit(payload.ok ? 0 : 1);
}

const npxCmd = process.platform === 'win32' ? 'npx.cmd' : 'npx';
const npxArgs = ['-y', '--prefer-online', `${PKG_NAME}@latest`];

const start = Date.now();

let child;
try {
  child = spawn(npxCmd, npxArgs, {
    stdio: ['pipe', 'pipe', 'pipe'],
    env: { ...process.env, NODE_ENV: process.env.NODE_ENV || 'production' },
  });
} catch (err) {
  // emit() calls process.exit, so control will not return here.
  emit({
    ok: false,
    durationMs: Date.now() - start,
    stage: 'spawn',
    error: `Failed to spawn \`${npxCmd}\`: ${err.message}`,
    hint: 'npx not found on PATH. Make sure Node.js (>=18) and npm are installed and in PATH.',
  });
  throw err; // unreachable, satisfies TS/eslint that `child` is initialized
}

/** @type {Buffer[]} */
const stdoutChunks = [];
/** @type {Buffer[]} */
const stderrChunks = [];

let resolved = false;
let initializedResp = null;

const initRequest = {
  jsonrpc: '2.0',
  id: 1,
  method: 'initialize',
  params: {
    protocolVersion: '2024-11-05',
    capabilities: {},
    clientInfo: { name: 'weixin-minigame-helper-verify', version: '1.0.0' },
  },
};

function tryParseStreamingJsonRpc(buf) {
  // MCP stdio uses newline-delimited JSON (NDJSON) for most servers, but some
  // implementations may use Content-Length framing (LSP-style). Try both.
  const text = buf.toString('utf8');

  // Strategy A: newline-delimited.
  const lines = text.split(/\r?\n/);
  for (const line of lines) {
    const t = line.trim();
    if (!t) continue;
    try {
      const obj = JSON.parse(t);
      if (obj && obj.id === 1 && (obj.result || obj.error)) return obj;
    } catch {
      /* not yet a complete JSON line */
    }
  }

  // Strategy B: LSP framing — find Content-Length headers.
  const re = /Content-Length:\s*(\d+)\s*\r?\n\r?\n/gi;
  let m;
  while ((m = re.exec(text))) {
    const len = Number.parseInt(m[1], 10);
    const bodyStart = m.index + m[0].length;
    if (bodyStart + len <= text.length) {
      const body = text.slice(bodyStart, bodyStart + len);
      try {
        const obj = JSON.parse(body);
        if (obj && obj.id === 1 && (obj.result || obj.error)) return obj;
      } catch {
        /* malformed body — keep scanning */
      }
    }
  }
  return null;
}

child.stdout.on('data', (chunk) => {
  stdoutChunks.push(chunk);
  if (resolved) return;
  const all = Buffer.concat(stdoutChunks);
  const resp = tryParseStreamingJsonRpc(all);
  if (resp) {
    initializedResp = resp;
    resolved = true;
    finish();
  }
});

child.stderr.on('data', (chunk) => {
  stderrChunks.push(chunk);
});

child.on('error', (err) => {
  if (resolved) return;
  resolved = true;
  emit({
    ok: false,
    durationMs: Date.now() - start,
    stage: 'spawn',
    error: `Process error: ${err.message}`,
    stderrTail: tailStderr(),
    hint:
      err.code === 'ENOENT'
        ? 'npx executable not found. Install Node.js (>=18) which ships npm/npx.'
        : null,
  });
});

child.on('exit', (code, signal) => {
  if (resolved) return;
  resolved = true;
  emit({
    ok: false,
    durationMs: Date.now() - start,
    stage: 'process-exit',
    error:
      signal != null
        ? `MCP server process killed by signal ${signal} before responding to initialize.`
        : `MCP server process exited with code ${code} before responding to initialize.`,
    stderrTail: tailStderr(),
    hint:
      'Try running `npx -y --prefer-online ' +
      PKG_NAME +
      '@latest` directly in a terminal to see the full error log.',
  });
});

const timeoutHandle = setTimeout(() => {
  if (resolved) return;
  resolved = true;
  try {
    child.kill('SIGKILL');
  } catch {
    /* ignore */
  }
  emit({
    ok: false,
    durationMs: Date.now() - start,
    stage: 'timeout',
    error: `MCP server did not respond to \`initialize\` within ${args.timeoutMs}ms.`,
    stderrTail: tailStderr(),
    hint:
      'Network or registry issue is the most common cause (npx must download the package on first use). ' +
      'Try `npm config get registry`, switch to a faster mirror, or run `install-mcp.mjs --json` first.',
  });
}, args.timeoutMs);

// Send the initialize request with both framings, since we don't know up
// front which the server prefers. Servers that read NDJSON will simply
// ignore the LSP-framed copy (and vice versa). MCP spec ALSO requires us
// to send `notifications/initialized` after a successful initialize, but
// for a smoke test we only care about the response.
function writeRequest(req) {
  const payload = JSON.stringify(req);
  // NDJSON line first.
  child.stdin.write(payload + '\n');
}

try {
  writeRequest(initRequest);
} catch (err) {
  resolved = true;
  clearTimeout(timeoutHandle);
  try {
    child.kill('SIGKILL');
  } catch {
    /* ignore */
  }
  emit({
    ok: false,
    durationMs: Date.now() - start,
    stage: 'write-stdin',
    error: `Failed to write initialize request to MCP server stdin: ${err.message}`,
    stderrTail: tailStderr(),
    hint: 'The host environment may be blocking child-process stdin.',
  });
}

function tailStderr() {
  const buf = Buffer.concat(stderrChunks).toString('utf8');
  const lines = buf.split(/\r?\n/).filter(Boolean);
  return lines.slice(-20).join('\n');
}

function finish() {
  clearTimeout(timeoutHandle);
  try {
    // Politely terminate; the server is expected to stop on stdin EOF.
    child.stdin.end();
  } catch {
    /* ignore */
  }
  setTimeout(() => {
    try {
      if (!child.killed) child.kill('SIGKILL');
    } catch {
      /* ignore */
    }
  }, 500);

  if (initializedResp.error) {
    return emit({
      ok: false,
      durationMs: Date.now() - start,
      stage: 'initialize-error',
      error:
        `MCP server returned a JSON-RPC error to initialize: ` +
        `${initializedResp.error.code} ${initializedResp.error.message}`,
      stderrTail: tailStderr(),
    });
  }

  const result = initializedResp.result || {};
  emit({
    ok: true,
    durationMs: Date.now() - start,
    serverInfo: result.serverInfo || null,
    protocolVersion: result.protocolVersion || null,
    // Note: we deliberately do NOT report a "url" or "port" — this is a
    // stdio MCP server. There IS no port to report.
    transport: 'stdio',
  });
}
