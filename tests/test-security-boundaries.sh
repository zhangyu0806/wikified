#!/usr/bin/env bash
set -euo pipefail

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
WORK=$(mktemp -d "${TMPDIR:-/tmp}/wikified-security.XXXXXX")
trap 'rm -rf -- "$WORK"' EXIT
PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf 'FAIL: %s\n' "$1"; }

# MCP read_page: only reviewed Markdown below wiki/, with a bounded size.
ROOT="$WORK/mcp-root"
mkdir -p "$ROOT/wiki" "$ROOT/.git"
printf '# safe page\n' >"$ROOT/wiki/ok.md"
printf 'private config marker\n' >"$ROOT/.git/config"
python3 - "$ROOT/wiki/large.md" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).write_text("x" * (256 * 1024 + 1), encoding="utf-8")
PY
cat >"$WORK/mcp-drive.js" <<'JS'
const { spawn } = require('node:child_process');
const [,, server] = process.argv;
const p = spawn('node', [server], { env: process.env, stdio: ['pipe','pipe','ignore'] });
let out = '';
p.stdout.on('data', d => { out += d.toString(); });
const call = (id, path) => p.stdin.write(JSON.stringify({jsonrpc:'2.0',id,method:'tools/call',params:{name:'read_page',arguments:{path}}})+'\n');
p.stdin.write(JSON.stringify({jsonrpc:'2.0',id:1,method:'initialize',params:{protocolVersion:'2024-11-05',capabilities:{},clientInfo:{name:'security-test',version:'1'}}})+'\n');
call(2, 'wiki/ok.md');
call(3, '.git/config');
call(4, 'wiki/../.git/config');
call(5, 'wiki/large.md');
setTimeout(() => { p.kill(); process.stdout.write(out); }, 800);
JS
MCP_OUT=$(env LLM_WIKI_ROOT="$ROOT" node "$WORK/mcp-drive.js" "$REPO/bin/llm-wiki-mcp")
grep '"id":2' <<<"$MCP_OUT" | grep -q 'safe page' && ok "MCP reads reviewed wiki Markdown" || bad "MCP rejected safe wiki page"
for id in 3 4 5; do
  grep "\"id\":$id" <<<"$MCP_OUT" | grep -q '"error"' || bad "MCP unsafe read id=$id was not rejected"
done
[[ "$FAIL" -eq 0 ]] && ok "MCP rejects repository internals, traversal and oversized pages"

# Quick note must redact before deriving title, filename and body.
NOTE_ROOT="$WORK/note-root"
SYNTHETIC_TOKEN='ghp_FAKEAbCdEfGhIjKlMnOpQrStUvWxYz12'
env LLM_WIKI_ROOT="$NOTE_ROOT" "$REPO/bin/llm-wiki-note" --no-sync "token=$SYNTHETIC_TOKEN remember" >/dev/null
if grep -R -F "$SYNTHETIC_TOKEN" "$NOTE_ROOT" >/dev/null 2>&1; then
  bad "quick note leaked synthetic token"
elif grep -R -F 'REDACTED' "$NOTE_ROOT/raw/notes" >/dev/null 2>&1; then
  ok "quick note redacts title, filename source and body before write"
else
  bad "quick note did not emit a redaction marker"
fi

# Installed scanner must honor the data root instead of scanning its own toolkit repo.
SCAN_ROOT="$WORK/scan-root"
mkdir -p "$SCAN_ROOT"
SCAN_TOKEN='ghp_''AbCdEfGhIjKlMnOpQrStUvWxYz12'
printf 'token=%s\n' "$SCAN_TOKEN" >"$SCAN_ROOT/private.txt"
if env LLM_WIKI_ROOT="$SCAN_ROOT" "$REPO/bin/llm-wiki-secret-scan" --all >/dev/null 2>&1; then
  bad "scanner ignored LLM_WIKI_ROOT"
else
  ok "scanner audits the configured data root"
fi

# A copied data-repo hook fails closed when its configured toolkit disappears.
INIT_ROOT="$WORK/init-root"
env LLM_WIKI_ROOT="$INIT_ROOT" "$REPO/bin/llm-wiki-init" --git >/dev/null
git -C "$INIT_ROOT" config user.name security-test
git -C "$INIT_ROOT" config user.email security@example.invalid
git -C "$INIT_ROOT" config wikified.toolkitBin "$WORK/missing-tools"
printf 'harmless\n' >"$INIT_ROOT/new.txt"
git -C "$INIT_ROOT" add new.txt
if env PATH=/usr/bin:/bin git -C "$INIT_ROOT" commit -m should-fail >/dev/null 2>&1; then
  bad "pre-commit failed open without scanner"
else
  ok "pre-commit fails closed when scanner cannot be resolved"
fi

printf '\n--- result: PASS=%s FAIL=%s ---\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
