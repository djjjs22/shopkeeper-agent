#!/usr/bin/env node
/**
 * install-mcp.mjs
 *
 * Make `@weadmin/weixin-minigame-helper-mcp` resolvable on this machine
 * before the AI host tries to spawn it as an MCP server.
 *
 * Strategy (idempotent, network-aware):
 *   1. If already resolvable (npm root -g, npx cache, etc.), exit quickly.
 *   2. Otherwise prefetch via `npm install -g <pkg>@latest`.
 *      We choose `npm install -g` over `npx -y --prefer-online` because:
 *        - It guarantees a stable resolution path.
 *        - It avoids the race where MCP host tries to spawn before npx finishes downloading.
 *   3. If the user has no permission for `-g` (EACCES), fall back to a
 *      user-local prefix at `~/.weixin-minigame-helper/deps`.
 *
 * Output (with --json):
 *   { "ok": true, "method": "global"|"prefix"|"already", "resolvedPath": "..." }
 *   { "ok": false, "error": "..." }
 */

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';

const PKG_NAME = '@weadmin/weixin-minigame-helper-mcp';
const args = new Set(process.argv.slice(2));
const wantJson = args.has('--json');

function out(payload) {
  if (wantJson) {
    process.stdout.write(JSON.stringify(payload, null, 2) + '\n');
  } else if (payload.ok) {
    console.log(`[install-mcp] OK  method=${payload.method}  path=${payload.resolvedPath || '(n/a)'}`);
  } else {
    console.error(`[install-mcp] FAIL  ${payload.error}`);
  }
  process.exit(payload.ok ? 0 : 1);
}

function npmCmd() {
  return process.platform === 'win32' ? 'npm.cmd' : 'npm';
}

/** Try to locate the package after install — returns absolute path or null. */
function tryResolve() {
  try {
    const req = createRequire(path.join(os.homedir(), 'noop.js'));
    return path.dirname(req.resolve(`${PKG_NAME}/package.json`));
  } catch {
    /* not in default resolve chain */
  }
  // Check `npm root -g`.
  const r = spawnSync(npmCmd(), ['root', '-g'], { encoding: 'utf8', timeout: 5000 });
  if (r.status === 0) {
    const root = r.stdout.trim();
    const candidate = path.join(root, PKG_NAME);
    if (fs.existsSync(path.join(candidate, 'package.json'))) return candidate;
  }
  // Check user-local prefix that we may have written to.
  const userPrefix = path.join(os.homedir(), '.weixin-minigame-helper', 'deps');
  const userCandidate = path.join(userPrefix, 'lib', 'node_modules', PKG_NAME);
  if (fs.existsSync(path.join(userCandidate, 'package.json'))) return userCandidate;
  return null;
}

/** Run `npm install` with the given prefix (or global if prefix is null). */
function npmInstall(prefix) {
  const cmd = npmCmd();
  const cliArgs = ['install', '-g', `${PKG_NAME}@latest`, '--no-audit', '--no-fund'];
  const env = { ...process.env };
  if (prefix) {
    cliArgs.push('--prefix', prefix);
    // When using --prefix, npm still respects -g semantics for layout.
  }
  const result = spawnSync(cmd, cliArgs, {
    stdio: 'inherit',
    env,
    timeout: 5 * 60 * 1000,
  });
  return result.status === 0;
}

function main() {
  const pre = tryResolve();
  if (pre) {
    return out({ ok: true, method: 'already', resolvedPath: pre });
  }

  // Try global install first.
  let ok = npmInstall(null);
  if (ok) {
    const resolved = tryResolve();
    if (resolved) return out({ ok: true, method: 'global', resolvedPath: resolved });
  }

  // Fallback: user-local prefix.
  const userPrefix = path.join(os.homedir(), '.weixin-minigame-helper', 'deps');
  fs.mkdirSync(userPrefix, { recursive: true });
  ok = npmInstall(userPrefix);
  if (ok) {
    const resolved = tryResolve();
    if (resolved) return out({ ok: true, method: 'prefix', resolvedPath: resolved });
  }

  out({
    ok: false,
    error:
      `npm install of ${PKG_NAME} failed via both global and user-local prefix. ` +
      `Try manually: npm install -g ${PKG_NAME}@latest`,
  });
}

main();
