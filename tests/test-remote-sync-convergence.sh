#!/usr/bin/env bash
# Three-replica convergence contract for the Git/JSONL synchronization path.
set -euo pipefail

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
SYNC="$REPO/bin/llm-wiki-remote-sync"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/wikified-sync-convergence.XXXXXX")
trap 'rm -rf -- "$WORK"' EXIT

REMOTE="$WORK/remote.git"
SEED="$WORK/seed"
TOOLS="$REPO/bin"
PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf 'FAIL: %s\n' "$1" >&2; }

git init --bare -q "$REMOTE"
git init -q "$SEED"
git -C "$SEED" config user.name seed
git -C "$SEED" config user.email seed@example.invalid
mkdir -p "$SEED/memory/events" "$SEED/raw/inbox"
cp "$REPO/templates/gitattributes.template" "$SEED/.gitattributes"
cp "$REPO/templates/gitignore.template" "$SEED/.gitignore"
printf '%s\n' '{"id":"seed","timestamp":"2026-08-14T00:00:00Z"}' \
  >"$SEED/memory/events/2026-08.jsonl"
: >"$SEED/raw/inbox/corrections.jsonl"
git -C "$SEED" add .
git -C "$SEED" commit -qm seed
git -C "$SEED" branch -M main
git -C "$SEED" remote add origin "$REMOTE"
git -C "$SEED" push -qu origin main
git --git-dir="$REMOTE" symbolic-ref HEAD refs/heads/main

for replica in a b c; do
  git clone -q "$REMOTE" "$WORK/$replica"
  git -C "$WORK/$replica" config user.name "$replica"
  git -C "$WORK/$replica" config user.email "$replica@example.invalid"
  printf '%s\n' \
    "{\"id\":\"event-$replica\",\"timestamp\":\"2026-08-14T00:00:0$((1 + $(printf '%d' "'$replica") - 97))Z\"}" \
    >>"$WORK/$replica/memory/events/2026-08.jsonl"
  git -C "$WORK/$replica" add memory/events/2026-08.jsonl
  git -C "$WORK/$replica" commit -qm "event-$replica"
done

run_sync() {
  local replica=$1 round=$2
  env PATH=/usr/bin:/bin \
    LLM_WIKI_ROOT="$WORK/$replica" \
    LLM_WIKI_SYNC_CACHE_DIR="$WORK/cache-$replica" \
    LLM_WIKI_BIN_TARGET="$TOOLS" \
    LLM_WIKI_SYNC_GIT_TIMEOUT=15 \
    "$SYNC" --force >"$WORK/$replica-$round.log" 2>&1
}

# Deliberately race the first publication attempt. At least one push may lose the
# race; that is acceptable only if later full-sync retries converge without loss.
pids=()
for replica in a b c; do
  run_sync "$replica" first &
  pids+=("$!")
done
for pid in "${pids[@]}"; do
  wait "$pid" || true
done

converged=0
for round in 1 2 3 4 5 6; do
  for replica in a b c; do
    run_sync "$replica" "retry-$round" || true
  done
  remote_oid=$(git --git-dir="$REMOTE" rev-parse refs/heads/main)
  a_oid=$(git -C "$WORK/a" rev-parse HEAD)
  b_oid=$(git -C "$WORK/b" rev-parse HEAD)
  c_oid=$(git -C "$WORK/c" rev-parse HEAD)
  if [[ "$a_oid" == "$remote_oid" && "$b_oid" == "$remote_oid" && "$c_oid" == "$remote_oid" ]]; then
    converged=1
    break
  fi
done

[[ $converged -eq 1 ]] && ok 'three replicas and remote converge to one OID after bounded retries' \
  || bad 'three replicas did not converge to the remote OID'

for replica in a b c; do
  events="$WORK/$replica/memory/events/2026-08.jsonl"
  for id in event-a event-b event-c; do
    count=$(grep -c "\"id\":\"$id\"" "$events" || true)
    [[ "$count" -eq 1 ]] || bad "$replica contains $count copies of $id"
  done
  [[ -z "$(git -C "$WORK/$replica" status --porcelain)" ]] \
    || bad "$replica has an unexpected dirty worktree after convergence"
  stamp="$WORK/cache-$replica/last-sync"
  [[ -f "$stamp" ]] && grep -Eq '^[0-9]+$' "$stamp" \
    || bad "$replica lacks a numeric complete-sync stamp"
done

if [[ $FAIL -eq 0 ]]; then
  ok 'all concurrent events survive exactly once with clean replicas and valid stamps'
fi

printf '\n--- result: PASS=%s FAIL=%s ---\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
