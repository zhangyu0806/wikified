#!/usr/bin/env bash
# Failure-injection tests for remote-sync success-stamp semantics.
set -euo pipefail

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
SYNC="$REPO/bin/llm-wiki-remote-sync"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/wiki-remote-sync.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf 'FAIL: %s\n' "$1"; }

REMOTE="$WORK/remote.git"
ROOT="$WORK/wiki"
CACHE="$WORK/cache"
git init --bare -q "$REMOTE"
git init -q "$ROOT"
git -C "$ROOT" config user.name eval
git -C "$ROOT" config user.email eval@example.invalid
TOOL_BIN="$WORK/tools"
mkdir -p "$TOOL_BIN" "$ROOT/memory/events" "$ROOT/raw/inbox"
printf '# schema\n' >"$ROOT/SCHEMA.md"
printf '#!/bin/sh\nexit 0\n' >"$TOOL_BIN/llm-wiki-dedupe-events"
chmod +x "$TOOL_BIN/llm-wiki-dedupe-events"
git -C "$ROOT" add .
git -C "$ROOT" commit -qm initial
git -C "$ROOT" branch -M main
git -C "$ROOT" remote add origin "$REMOTE"
git -C "$ROOT" push -qu origin main

run_sync() {
  env \
    LLM_WIKI_ROOT="$ROOT" \
    LLM_WIKI_SYNC_CACHE_DIR="$CACHE" \
    LLM_WIKI_SYNC_THROTTLE=3600 \
    LLM_WIKI_SYNC_GIT_TIMEOUT=5 \
    LLM_WIKI_BIN_TARGET="$TOOL_BIN" \
    "$SYNC" "$@"
}

# Full, clean sync is the only path allowed to create a success stamp.
run_sync --force >/dev/null
if [[ -f "$CACHE/last-sync" ]] && grep -Eq '^[0-9]+$' "$CACHE/last-sync"; then
  ok "clean full sync writes a numeric success stamp"
else
  bad "clean full sync did not write success stamp"
fi

# --status must be read-only with respect to the existing stamp.
before=$(cat "$CACHE/last-sync")
run_sync --status >/dev/null
after=$(cat "$CACHE/last-sync")
[[ "$before" == "$after" ]] && ok "--status leaves success stamp unchanged" || bad "--status changed success stamp"

# Explicit one-way operations run even inside the throttle window, but never claim full-sync success.
rm -f "$CACHE/last-sync"
run_sync --pull >/dev/null
[[ ! -e "$CACHE/last-sync" ]] && ok "--pull does not write full-sync stamp" || bad "--pull wrote success stamp"
run_sync --push >/dev/null
[[ ! -e "$CACHE/last-sync" ]] && ok "--push does not write full-sync stamp" || bad "--push wrote success stamp"

# Dirty worktree is preserved and returns gracefully, but incomplete work cannot be stamped.
printf 'user work\n' >"$ROOT/dirty.txt"
run_sync --force >/dev/null 2>&1 || bad "dirty worktree should degrade without destructive failure"
[[ -f "$ROOT/dirty.txt" ]] && ok "dirty user file is preserved" || bad "dirty user file was lost"
[[ ! -e "$CACHE/last-sync" ]] && ok "dirty incomplete sync does not write stamp" || bad "dirty incomplete sync wrote stamp"
rm -f "$ROOT/dirty.txt"

# Fetch failure must be non-zero and leave no stamp.
git -C "$ROOT" remote set-url origin "$WORK/nonexistent.git"
if run_sync --force >/dev/null 2>&1; then
  bad "fetch failure returned zero"
else
  ok "fetch failure is non-zero"
fi
[[ ! -e "$CACHE/last-sync" ]] && ok "fetch failure does not write stamp" || bad "fetch failure wrote stamp"
git -C "$ROOT" remote set-url origin "$REMOTE"

# Dedupe failure is a gated phase: non-zero, no stamp.
printf '#!/bin/sh\nexit 17\n' >"$TOOL_BIN/llm-wiki-dedupe-events"
chmod +x "$TOOL_BIN/llm-wiki-dedupe-events"
if run_sync --force >/dev/null 2>&1; then
  bad "dedupe failure returned zero"
else
  ok "dedupe failure is non-zero"
fi
[[ ! -e "$CACHE/last-sync" ]] && ok "dedupe failure does not write stamp" || bad "dedupe failure wrote stamp"
printf '#!/bin/sh\nexit 0\n' >"$TOOL_BIN/llm-wiki-dedupe-events"

# A fresh stamp must not throttle an explicit push. The rejecting hook proves push was attempted.
printf 'local commit\n' >"$ROOT/local.txt"
git -C "$ROOT" add local.txt
git -C "$ROOT" commit -qm local-ahead
printf '#!/bin/sh\nexit 1\n' >"$REMOTE/hooks/pre-receive"
chmod +x "$REMOTE/hooks/pre-receive"
mkdir -p "$CACHE"
date +%s >"$CACHE/last-sync"
if run_sync --push >/dev/null 2>&1; then
  bad "explicit --push was throttled or hid a rejected push"
else
  ok "explicit --push bypasses throttle and propagates rejection"
fi
rm -f "$CACHE/last-sync"

# The same rejected push during full sync cannot advance the stamp.
if run_sync --force >/dev/null 2>&1; then
  bad "rejected full-sync push returned zero"
else
  ok "rejected full-sync push is non-zero"
fi
[[ ! -e "$CACHE/last-sync" ]] && ok "push failure does not write stamp" || bad "push failure wrote stamp"

printf '\n--- 结果: PASS=%s FAIL=%s ---\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
