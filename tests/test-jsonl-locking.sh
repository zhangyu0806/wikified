#!/usr/bin/env bash
set -euo pipefail

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
WORK=$(mktemp -d "${TMPDIR:-/tmp}/wikified-locking.XXXXXX")
trap 'rm -rf -- "$WORK"' EXIT
ROOT="$WORK/root"
mkdir -p "$ROOT/memory/events" "$ROOT/raw/inbox"
PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf 'FAIL: %s\n' "$1"; }

printf '%s\n' '{"id":"event-a","timestamp":"2026-08-13T00:00:00Z","summary":"a"}' >"$ROOT/memory/events/2026-08.jsonl"
printf '%s\n' '{"id":"correction-a","timestamp":"2026-08-13T00:00:00Z","status":"pending","text":"a"}' >"$ROOT/raw/inbox/corrections.jsonl"

# Dedupe must wait for both append writers' locks for its entire rewrite transaction.
exec 8>"$ROOT/memory/.memory.lock"
flock 8
if timeout 0.3 "$REPO/bin/llm-wiki-dedupe-events" --root "$ROOT" >/dev/null 2>&1; then
  bad "dedupe ignored memory append lock"
else
  [[ $? -eq 124 ]] && ok "dedupe waits for memory append lock" || bad "dedupe failed for a reason other than lock wait"
fi
flock -u 8

exec 7>"$ROOT/raw/inbox/.corrections.lock"
flock 7
if timeout 0.3 "$REPO/bin/llm-wiki-correct" --root "$ROOT" --resolve correction-a >/dev/null 2>&1; then
  bad "correction resolve ignored queue lock"
else
  [[ $? -eq 124 ]] && ok "correction resolve locks read-modify-write atomically" || bad "correction resolve failed for a reason other than lock wait"
fi
flock -u 7

# Same id with different bytes is identity corruption, not a duplicate.
cat >"$ROOT/memory/events/2026-08.jsonl" <<'EOF'
{"id":"same-id","timestamp":"2026-08-13T00:00:00Z","summary":"left"}
{"id":"same-id","timestamp":"2026-08-13T00:00:01Z","summary":"right"}
EOF
BEFORE=$(sha256sum "$ROOT/memory/events/2026-08.jsonl" | cut -d' ' -f1)
if "$REPO/bin/llm-wiki-dedupe-events" --root "$ROOT" >/dev/null 2>&1; then
  bad "dedupe silently chose a conflicting same-id record"
else
  AFTER=$(sha256sum "$ROOT/memory/events/2026-08.jsonl" | cut -d' ' -f1)
  [[ "$BEFORE" == "$AFTER" ]] && ok "same-id conflict fails without rewriting data" || bad "same-id failure still rewrote data"
fi

printf '\n--- result: PASS=%s FAIL=%s ---\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
