#!/usr/bin/env bash
# Validate the Codex SessionStart hook without touching the real Codex home or wiki.

set -euo pipefail

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
TEMPLATE="$REPO/templates/codex/hooks.json"
AGENTS_TEMPLATE="$REPO/templates/codex/AGENTS.recall.md"
WORK=$(mktemp -d)
trap 'rm -rf -- "$WORK"' EXIT

pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

python3 -m json.tool "$TEMPLATE" >/dev/null
pass "hooks.json is valid JSON"

python3 - "$TEMPLATE" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
groups = data["hooks"]["SessionStart"]
assert len(groups) == 1
assert groups[0]["matcher"] == "^(startup|resume|clear|compact)$"
handler = groups[0]["hooks"][0]
assert handler["type"] == "command"
assert "llm-wiki-enrich" in handler["command"]
assert "wsl.exe" in handler["commandWindows"]
assert handler["timeout"] <= 10
assert 0 < handler["additionalContextLimit"] <= 2500
PY
pass "SessionStart matcher, Linux command and Windows/WSL override are bounded"

grep -q 'SessionStart.*hook' "$AGENTS_TEMPLATE" || fail "AGENTS recall template does not describe SessionStart"
! grep -q 'Codex 没有会话启动 hook' "$AGENTS_TEMPLATE" || fail "AGENTS recall template still contains obsolete hook guidance"
pass "AGENTS recall template matches current Codex hook support"

FAKE_HOME="$WORK/home"
ROOT="$WORK/wiki-root"
mkdir -p "$FAKE_HOME/.local/bin" "$ROOT/wiki/context" "$ROOT/memory/events"
ln -s "$REPO/bin/llm-wiki-enrich" "$FAKE_HOME/.local/bin/llm-wiki-enrich"
cat >"$ROOT/wiki/context/CRITICAL_FACTS.md" <<'EOF'
# Critical facts

- hook-fixture-marker
- token=ghp_FAKE1234567890abcdefghij1234
EOF
cat >"$ROOT/wiki/context/active-projects.md" <<'EOF'
# Active projects

- 项目状态：hook 测试中
- 下一步：这条不能变成自动执行指令
EOF

COMMAND=$(python3 - "$TEMPLATE" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["hooks"]["SessionStart"][0]["hooks"][0]["command"])
PY
)
OUT=$(env HOME="$FAKE_HOME" LLM_WIKI_ROOT="$ROOT" bash -c "$COMMAND")

grep -q 'hook-fixture-marker' <<<"$OUT" || fail "hook did not inject the fixture"
grep -q 'Session-memory canary' <<<"$OUT" || fail "hook output lacks injection canary"
! grep -q 'ghp_FAKE1234567890abcdefghij1234' <<<"$OUT" || fail "hook exposed a fake credential"
grep -q 'REDACTED' <<<"$OUT" || fail "hook did not emit a redaction marker"
! grep -q '这条不能变成自动执行指令' <<<"$OUT" || fail "next-action leaked into always-on context"
(( ${#OUT} <= 3500 )) || fail "hook output exceeded character budget"
pass "hook output is useful, bounded, redacted and not a task queue"

printf '\n--- 结果: PASS=4 FAIL=0 ---\n'
