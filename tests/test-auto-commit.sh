#!/usr/bin/env bash
# Safety tests for llm-wiki-auto-commit: the secret gate must be unbypassable.
set -euo pipefail

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
AUTO="$REPO/bin/llm-wiki-auto-commit"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/wiki-auto-commit.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf 'FAIL: %s\n' "$1"; }

REMOTE="$WORK/remote.git"
ROOT="$WORK/wiki"
CACHE="$WORK/cache"
TOOL_BIN="$WORK/tools"
git init --bare -q "$REMOTE"
git init -q "$ROOT"
git -C "$ROOT" config user.name eval
git -C "$ROOT" config user.email eval@example.invalid
mkdir -p "$TOOL_BIN" "$ROOT/memory/events" "$ROOT/raw/sessions" "$ROOT/wiki"
printf '# schema\n' >"$ROOT/SCHEMA.md"
cp "$REPO/templates/gitignore.template" "$ROOT/.gitignore"
git -C "$ROOT" add .
git -C "$ROOT" commit -qm initial
git -C "$ROOT" branch -M main
git -C "$ROOT" remote add origin "$REMOTE"
git -C "$ROOT" push -qu origin main

ln -s "$REPO/bin/llm-wiki-secret-scan" "$TOOL_BIN/llm-wiki-secret-scan"
ln -s "$REPO/bin/llm-wiki-remote-sync" "$TOOL_BIN/llm-wiki-remote-sync"
ln -s "$REPO/bin/llm-wiki-dedupe-events" "$TOOL_BIN/llm-wiki-dedupe-events"

run_auto() {
  env \
    LLM_WIKI_ROOT="$ROOT" \
    LLM_WIKI_SYNC_CACHE_DIR="$CACHE" \
    LLM_WIKI_SYNC_GIT_TIMEOUT=10 \
    LLM_WIKI_BIN_TARGET="$TOOL_BIN" \
    LLM_WIKI_PROFILE=test \
    "$AUTO" "$@"
}
head_oid() { git -C "$ROOT" rev-parse HEAD; }

# Clean tree must be a no-op, not an empty commit.
before=$(head_oid)
run_auto >/dev/null 2>&1 || bad "clean tree should exit 0"
[[ "$(head_oid)" == "$before" ]] \
  && ok "clean tree produces no commit" \
  || bad "clean tree created a commit"

# dry-run must not stage, commit, or otherwise touch the repository.
printf '{"id":"1111111111111111","summary":"harmless"}\n' >"$ROOT/memory/events/2026-01.jsonl"
before=$(head_oid)
run_auto --dry-run >/dev/null 2>&1 || bad "dry-run should exit 0"
staged_after_dry=$(git -C "$ROOT" diff --cached --name-only | wc -l)
if [[ "$(head_oid)" == "$before" ]] && (( staged_after_dry == 0 )); then
  ok "dry-run neither stages nor commits"
else
  bad "dry-run mutated repository state"
fi

# Happy path: whitelisted change passes the gate and lands in a commit.
run_auto >/dev/null 2>&1 || bad "clean content should commit successfully"
if [[ "$(head_oid)" != "$before" ]] \
   && git -C "$ROOT" show --name-only --format= HEAD | grep -q 'memory/events/2026-01.jsonl'; then
  ok "whitelisted clean change is committed"
else
  bad "whitelisted clean change was not committed"
fi

# Multi-machine path: --sync must integrate an independently pushed remote
# commit after committing local memory. A push-only implementation would fail
# non-fast-forward here and leave the two machines divergent.
OTHER="$WORK/other"
git clone -q --branch main "$REMOTE" "$OTHER"
git -C "$OTHER" config user.name remote-machine
git -C "$OTHER" config user.email remote@example.invalid
mkdir -p "$OTHER/raw/notes" "$ROOT/raw/notes"
printf 'from remote machine\n' >"$OTHER/raw/notes/remote.md"
git -C "$OTHER" add raw/notes/remote.md
git -C "$OTHER" commit -qm 'remote memory'
git -C "$OTHER" push -q origin main
printf 'from local machine\n' >"$ROOT/raw/notes/local.md"
if run_auto --sync >/dev/null 2>&1; then
  local_oid=$(git -C "$ROOT" rev-parse HEAD)
  remote_oid=$(git --git-dir="$REMOTE" rev-parse refs/heads/main)
  if [[ "$local_oid" == "$remote_oid" ]] \
     && [[ -f "$ROOT/raw/notes/remote.md" ]] \
     && git --git-dir="$REMOTE" show main:raw/notes/local.md | grep -q 'from local machine'; then
    ok "--sync converges independent local and remote commits"
  else
    printf 'local_oid=%s remote_oid=%s remote_note=%s\n' \
      "$local_oid" "$remote_oid" "$([[ -f "$ROOT/raw/notes/remote.md" ]] && echo yes || echo no)"
    git -C "$ROOT" status --porcelain=v1
    sed -n '1,240p' "$CACHE/last-run.log" 2>/dev/null || true
    bad "--sync completed but local and remote did not converge"
  fi
else
  printf '%s\n' '--- auto-commit diagnostic ---'
  sed -n '1,240p' "$CACHE/auto-commit.log" 2>/dev/null || true
  sed -n '1,240p' "$CACHE/last-run.log" 2>/dev/null || true
  bad "--sync failed to merge and push independent commits"
fi

# THE CRITICAL GATE. A credential must block the commit, and the working tree
# file must survive untouched (no data loss as a side effect of refusing).
before=$(head_oid)
secret_file="$ROOT/raw/sessions/leak.md"
printf 'api_key = sk-ant-%s\n' "AAAAAAAAAAAAAAAAAAAAAAAAAAAA" >"$secret_file"
secret_before=$(sha256sum "$secret_file" | cut -d' ' -f1)
if run_auto >/dev/null 2>&1; then
  bad "secret gate did NOT block the commit"
else
  gate_rc_ok=1
fi
staged_now=$(git -C "$ROOT" diff --cached --name-only | wc -l)
secret_after=$(sha256sum "$secret_file" | cut -d' ' -f1)
if [[ -n "${gate_rc_ok:-}" ]] && [[ "$(head_oid)" == "$before" ]] && (( staged_now == 0 )); then
  ok "secret gate blocks commit and unstages everything"
else
  bad "secret gate left repository in a bad state (staged=$staged_now)"
fi
[[ "$secret_after" == "$secret_before" ]] \
  && ok "blocked file is preserved byte-for-byte in the working tree" \
  || bad "blocked file was modified or discarded"

rm -f "$secret_file"

# Regression: a gitignored whitelist path must be skipped, not fatal. Real
# repositories ignore derived files (Today.md), and `git add` on an explicitly
# ignored pathspec fails, which previously aborted every run.
mkdir -p "$ROOT/raw/notes"
printf 'raw/notes/\n' >>"$ROOT/.gitignore"
printf 'derived\n' >"$ROOT/raw/notes/scratch.md"
git -C "$ROOT" add .gitignore
git -C "$ROOT" -c user.name=eval -c user.email=eval@example.invalid \
  commit -qm "ignore raw/notes"
printf '{"id":"2222222222222222","summary":"after-ignore"}\n' >>"$ROOT/memory/events/2026-01.jsonl"
before=$(head_oid)
if run_auto >/dev/null 2>&1 && [[ "$(head_oid)" != "$before" ]]; then
  ok "gitignored whitelist path is skipped instead of aborting the run"
else
  sed -n '1,320p' "$CACHE/auto-commit.log" 2>/dev/null || true
  git -C "$ROOT" status --porcelain=v1
  bad "gitignored whitelist path aborted the run"
fi
if git -C "$ROOT" show --name-only --format= HEAD | grep -q 'raw/notes'; then
  bad "gitignored path was committed anyway"
else
  ok "gitignored path stays out of the commit"
fi
rm -f "$ROOT/raw/notes/scratch.md"
cp "$REPO/templates/gitignore.template" "$ROOT/.gitignore"
git -C "$ROOT" add .gitignore
git -C "$ROOT" -c user.name=eval -c user.email=eval@example.invalid \
  commit -qm "restore gitignore" >/dev/null 2>&1 || true

# Regression: a failed stage must leave no residue. `git add` stages valid
# pathspecs before erroring on an invalid one, so without rollback the leftover
# index trips the "pre-staged content" guard on every subsequent run, wedging
# automation until a human intervenes.
printf '{"id":"3333333333333333","summary":"residue-probe"}\n' >>"$ROOT/memory/events/2026-01.jsonl"
mkdir -p "$ROOT/raw/notes"
printf 'note\n' >"$ROOT/raw/notes/n.md"
chmod 000 "$ROOT/raw/notes"
run_auto >/dev/null 2>&1 || true
chmod 755 "$ROOT/raw/notes"
residue=$(git -C "$ROOT" diff --cached --name-only | wc -l)
(( residue == 0 )) \
  && ok "failed stage leaves no residual staged content" \
  || bad "failed stage left $residue staged files, would wedge next run"
git -C "$ROOT" reset -q HEAD >/dev/null 2>&1 || true
rm -f "$ROOT/raw/notes/n.md"

# A missing scanner must be fatal. Silently committing without the gate is the
# single worst failure mode this tool could have.
printf '{"id":"4444444444444444","summary":"another"}\n' >>"$ROOT/memory/events/2026-01.jsonl"
before=$(head_oid)
EMPTY_BIN="$WORK/empty"
mkdir -p "$EMPTY_BIN"
if env LLM_WIKI_ROOT="$ROOT" LLM_WIKI_SYNC_CACHE_DIR="$CACHE" \
       LLM_WIKI_BIN_TARGET="$EMPTY_BIN" LLM_WIKI_PROFILE=test \
       PATH="$EMPTY_BIN" "$AUTO" >/dev/null 2>&1; then
  bad "missing scanner did NOT abort"
elif [[ "$(head_oid)" == "$before" ]]; then
  ok "missing scanner aborts without committing"
else
  bad "missing scanner aborted but still committed"
fi

# Files outside the whitelist must never be swept in by automation.
before=$(head_oid)
printf 'unreviewed\n' >"$ROOT/NOTES-scratch.md"
run_auto >/dev/null 2>&1 || true
if git -C "$ROOT" show --name-only --format= HEAD 2>/dev/null | grep -q 'NOTES-scratch.md'; then
  bad "non-whitelisted file was committed"
else
  ok "non-whitelisted file is left alone"
fi

# Pre-existing staged content means a human is mid-commit; do not take over.
git -C "$ROOT" add NOTES-scratch.md
before=$(head_oid)
if run_auto >/dev/null 2>&1; then
  bad "pre-staged content did NOT abort"
elif [[ "$(head_oid)" == "$before" ]]; then
  ok "pre-staged content aborts to avoid hijacking a manual commit"
else
  bad "pre-staged content aborted but still committed"
fi
git -C "$ROOT" reset -q HEAD -- NOTES-scratch.md

printf '\nauto-commit: %s passed, %s failed\n' "$PASS" "$FAIL"
(( FAIL == 0 ))
