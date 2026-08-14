#!/usr/bin/env bash
set -euo pipefail

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
SYNC="$REPO/bin/llm-wiki-remote-sync"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/wikified-sync-adv.XXXXXX")
trap 'rm -rf -- "$WORK"' EXIT
PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf 'FAIL: %s\n' "$1"; }

setup_case() {
  local name=$1 base="$WORK/$1"
  ROOT="$base/root"; REMOTE="$base/remote.git"; CACHE="$base/cache"; TOOLS="$base/tools"; DEDUPE_LOG="$base/dedupe.log"
  mkdir -p "$ROOT/memory/events" "$ROOT/raw/inbox" "$TOOLS"
  git init --bare -q "$REMOTE"
  git init -q "$ROOT"
  git -C "$ROOT" config user.name sync-test
  git -C "$ROOT" config user.email sync@example.invalid
  printf '%s\n' '{"id":"seed","timestamp":"2026-08-13T00:00:00Z"}' >"$ROOT/memory/events/2026-08.jsonl"
  : >"$ROOT/raw/inbox/corrections.jsonl"
  cat >"$TOOLS/llm-wiki-dedupe-events" <<'EOF'
#!/bin/sh
printf 'called\n' >>"$DEDUPE_LOG"
exit 0
EOF
  chmod +x "$TOOLS/llm-wiki-dedupe-events"
  cp "$SYNC" "$TOOLS/llm-wiki-remote-sync"
  chmod +x "$TOOLS/llm-wiki-remote-sync"
  git -C "$ROOT" add .
  git -C "$ROOT" commit -qm initial
  git -C "$ROOT" branch -M main
  git -C "$ROOT" remote add origin "$REMOTE"
  git -C "$ROOT" push -qu origin main
}

run_sync() {
  env PATH=/usr/bin:/bin LLM_WIKI_ROOT="$ROOT" LLM_WIKI_SYNC_CACHE_DIR="$CACHE" \
    LLM_WIKI_BIN_TARGET="$TOOLS" LLM_WIKI_SYNC_GIT_TIMEOUT=5 \
    DEDUPE_LOG="$DEDUPE_LOG" "$TOOLS/llm-wiki-remote-sync" --force
}

setup_case wrong-branch
git -C "$ROOT" switch -qc topic
if run_sync >/dev/null 2>&1; then bad "wrong branch was accepted"; else ok "wrong branch fails closed"; fi
[[ ! -e "$CACHE/last-sync" ]] || bad "wrong branch wrote success stamp"

setup_case detached
git -C "$ROOT" checkout -q --detach HEAD
if run_sync >/dev/null 2>&1; then bad "detached HEAD was accepted"; else ok "detached HEAD fails closed"; fi
[[ ! -e "$CACHE/last-sync" ]] || bad "detached HEAD wrote success stamp"

setup_case missing-upstream
git -C "$ROOT" branch --unset-upstream
if run_sync >/dev/null 2>&1; then bad "missing upstream was accepted"; else ok "missing upstream fails closed"; fi
[[ ! -e "$CACHE/last-sync" ]] || bad "missing upstream wrote success stamp"

setup_case dirty
printf '%s\n' '{"id":"user-uncommitted","timestamp":"2026-08-13T00:00:01Z"}' >>"$ROOT/memory/events/2026-08.jsonl"
BEFORE_FILE=$(sha256sum "$ROOT/memory/events/2026-08.jsonl" | cut -d' ' -f1)
BEFORE_HEAD=$(git -C "$ROOT" rev-parse HEAD)
BEFORE_REMOTE=$(git --git-dir="$REMOTE" rev-parse refs/heads/main)
run_sync >/dev/null 2>&1 || bad "dirty worktree should return incomplete without destruction"
AFTER_FILE=$(sha256sum "$ROOT/memory/events/2026-08.jsonl" | cut -d' ' -f1)
AFTER_HEAD=$(git -C "$ROOT" rev-parse HEAD)
AFTER_REMOTE=$(git --git-dir="$REMOTE" rev-parse refs/heads/main)
if [[ "$BEFORE_FILE" == "$AFTER_FILE" && "$BEFORE_HEAD" == "$AFTER_HEAD" && "$BEFORE_REMOTE" == "$AFTER_REMOTE" && ! -e "$DEDUPE_LOG" ]]; then
  ok "dirty sync preserves bytes and does not dedupe, commit or push"
else
  bad "dirty sync mutated local or remote state"
fi
[[ ! -e "$CACHE/last-sync" ]] && ok "dirty sync does not stamp success" || bad "dirty sync wrote success stamp"

setup_case missing-deduper
rm "$TOOLS/llm-wiki-dedupe-events"
if run_sync >/dev/null 2>&1; then bad "missing deduper was skipped"; else ok "missing deduper fails closed"; fi
[[ ! -e "$CACHE/last-sync" ]] || bad "missing deduper wrote success stamp"

printf '\n--- result: PASS=%s FAIL=%s ---\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
