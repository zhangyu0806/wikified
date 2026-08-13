#!/usr/bin/env bash
# MCP stdio 传输框架回归测试。
#
# 为什么存在：MCP stdio 规范（2025-06-18）要求换行分隔 JSON（NDJSON）。
# Codex CLI 0.137.0 的 stdio 客户端只按 '\n' 定界
# （codex-rs/rmcp-client/src/local_stdio_transport.rs 用 memchr(b'\n')，
#  全仓库无 MCP 侧 Content-Length 解析），因此仅支持 LSP 风格
# Content-Length 帧的 server 会被 Codex 静默挂起——无报错、无提示。
# 本测试锁定：NDJSON 必须可用；Content-Length 作为既有客户端的兼容路径不得回退。
set -euo pipefail

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
MCP="$REPO/bin/llm-wiki-mcp"
PASS=0
FAIL=0

ok()   { PASS=$((PASS+1)); printf 'PASS  %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf 'FAIL: %s\n' "$1"; }

WORK=$(mktemp -d "${TMPDIR:-/tmp}/mcpframe.XXXXXX")
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

# stub CLI：让 tools/call 有确定性输出，且证明走的是同目录解析
STUB_BIN="$WORK/bin"
mkdir -p "$STUB_BIN"
cp "$MCP" "$STUB_BIN/llm-wiki-mcp"
for c in llm-wiki-enrich llm-wiki-health; do
  printf '#!/bin/sh\necho "STUB-OK"\n' > "$STUB_BIN/$c"
  chmod +x "$STUB_BIN/$c"
done
printf '#!/bin/sh\nlast=""\nfor arg do last=$arg; done\nif [ "$last" = "force-fail" ]; then echo "STUB-FAIL" >&2; exit 2; fi\necho "STUB-OK"\n' > "$STUB_BIN/llm-wiki-event"
chmod +x "$STUB_BIN/llm-wiki-event"

# 驱动器：按指定框架收发，输出解析到的 JSON 每行一条
cat > "$WORK/drive.js" <<'DRIVER'
'use strict';
const { spawn } = require('node:child_process');
const [, , mcpPath, framing] = process.argv;

const env = { ...process.env };
delete env.LLM_WIKI_BIN_TARGET;

const p = spawn('node', [mcpPath], { env, stdio: ['pipe', 'pipe', 'pipe'] });

const send = (obj) => {
  const body = JSON.stringify(obj);
  if (framing === 'ndjson') {
    p.stdin.write(body + '\n');
  } else {
    p.stdin.write(`Content-Length: ${Buffer.byteLength(body, 'utf8')}\r\n\r\n${body}`);
  }
};

let out = Buffer.alloc(0);
p.stdout.on('data', (d) => { out = Buffer.concat([out, d]); });
let err = '';
p.stderr.on('data', (d) => { err += d.toString(); });

send({ jsonrpc: '2.0', id: 1, method: 'initialize',
       params: { protocolVersion: '2024-11-05', capabilities: {},
                 clientInfo: { name: 'framing-test', version: '1' } } });
send({ jsonrpc: '2.0', id: 2, method: 'tools/list' });
send({ jsonrpc: '2.0', id: 3, method: 'tools/call',
       params: { name: 'search_pages', arguments: { query: 'x' } } });
send({ jsonrpc: '2.0', id: 4, method: 'tools/call',
       params: { name: 'record_event', arguments: { type: 'fact', summary: 'force-fail' } } });

setTimeout(() => {
  p.kill();
  const text = out.toString('utf8');
  const objs = [];
  // 同时能吃两种回包形态，避免驱动器本身偏向某一种
  if (/Content-Length:/i.test(text)) {
    const re = /Content-Length:\s*(\d+)\r?\n\r?\n/gi;
    let m;
    while ((m = re.exec(text)) !== null) {
      const start = m.index + m[0].length;
      const body = text.slice(start, start + Number(m[1]));
      try { objs.push(JSON.parse(body)); } catch { /* skip */ }
    }
  } else {
    for (const line of text.split('\n')) {
      const s = line.trim();
      if (!s) continue;
      try { objs.push(JSON.parse(s)); } catch { /* skip */ }
    }
  }
  for (const o of objs) console.log(JSON.stringify(o));
  if (err.trim()) console.error(err.trim());
}, 1500);
DRIVER

run_framing() {
  node "$WORK/drive.js" "$STUB_BIN/llm-wiki-mcp" "$1" 2>/dev/null || true
}

check_framing() {
  local framing=$1 label=$2 res
  res=$(run_framing "$framing")

  if [[ -z "$res" ]]; then
    bad "$label: server 无任何可解析回包（客户端会静默挂起）"
    return
  fi
  ok "$label: 收到可解析回包"

  if grep -q '"id":1' <<<"$res" && grep -q '"serverInfo"' <<<"$res"; then
    ok "$label: initialize 握手成功"
  else
    bad "$label: initialize 未返回 serverInfo"
  fi

  local n
  n=$(grep '"id":2' <<<"$res" | grep -o '"name":' | wc -l | tr -d ' ')
  if [[ "$n" == "6" ]]; then
    ok "$label: tools/list 返回 6 个工具"
  else
    bad "$label: tools/list 工具数为 $n，期望 6"
  fi

  if grep '"id":3' <<<"$res" | grep -q 'STUB-OK'; then
    ok "$label: tools/call 到达同目录 CLI"
  else
    bad "$label: tools/call 未到达同目录 CLI"
  fi

  if grep '"id":4' <<<"$res" | grep -q '"error"' \
    && grep '"id":4' <<<"$res" | grep -q 'STUB-FAIL'; then
    ok "$label: CLI 非零退出传播为 JSON-RPC error"
  else
    bad "$label: CLI 非零退出被伪装成成功 content"
  fi
}

printf '=== NDJSON（MCP 规范 + Codex 唯一支持的框架）===\n'
check_framing ndjson "NDJSON"

printf '\n=== Content-Length（既有客户端兼容路径，不得回退）===\n'
check_framing contentlength "Content-Length"

printf '\n--- 结果: PASS=%s FAIL=%s ---\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
