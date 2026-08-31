#!/usr/bin/env bash
# AI proposals are durable and reviewable, but never recallable before approval.
set -euo pipefail

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
EVENT="$REPO/bin/llm-wiki-event"
ENRICH="$REPO/bin/llm-wiki-enrich"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/wiki-proposals.XXXXXX")
trap 'rm -rf -- "$WORK"' EXIT
ROOT="$WORK/root"
mkdir -p "$ROOT/wiki" "$ROOT/memory/events" "$ROOT/policy"
printf '# index\n' >"$ROOT/wiki/index.md"
cp "$REPO/templates/access-policy.json" "$ROOT/policy/access.json"

record() {
  env LLM_WIKI_ROOT="$ROOT" python3 "$EVENT" "$@" --print
}
recall() {
  env LLM_WIKI_ROOT="$ROOT" python3 "$ENRICH" --agent-profile codex --query "$1" --json
}

PENDING=$(record --type decision --project alpha --source mcp:codex \
  --actor-type ai --actor-id codex --domain work --sensitivity internal \
  --epistemic-status ai-proposed --review-status pending --target-agent codex \
  'proposalmarker use a shared human-AI memory substrate')
PENDING_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$PENDING")

python3 -c '
import json,sys
event=json.load(sys.stdin)
assert event["actor"] == {"type":"ai", "id":"codex"}
assert event["epistemic_status"] == "ai-proposed"
assert event["review_status"] == "pending"
assert event["target_agents"] == ["codex"]
' <<<"$PENDING"
printf 'PASS  AI proposal is recorded with a pending governance envelope\n'

python3 -c 'import json,sys; assert json.load(sys.stdin) == []' <<<"$(recall proposalmarker)"
printf 'PASS  pending proposal is not recallable\n'

if record --type fact --actor-type ai --review-status approved bad-ai-approval >/dev/null 2>&1; then
  printf 'FAIL: AI was able to self-approve an event\n'; exit 1
fi
printf 'PASS  AI cannot self-approve through the event CLI\n'

if record --type fact --project alpha --review-status pending \
  'humanpendingmarker this is not an AI proposal' >/dev/null 2>&1; then
  printf 'FAIL: writer created a human-authored pending event\n'; exit 1
fi
if record --type fact --project alpha --actor-type human --epistemic-status ai-proposed \
  --review-status pending 'half-forged-human-ai-proposal' >/dev/null 2>&1; then
  printf 'FAIL: writer accepted human actor + ai-proposed pending\n'; exit 1
fi
if record --type fact --project alpha --actor-type ai --epistemic-status human-stated \
  --review-status pending 'half-forged-ai-human-claim' >/dev/null 2>&1; then
  printf 'FAIL: writer accepted AI actor + human-stated pending\n'; exit 1
fi
printf 'PASS  v3 writer enforces actor/epistemic/review cross-invariants\n'

cat >"$ROOT/memory/events/forged.jsonl" <<'EOF'
{"schema_version":"llm-wiki-memory-event/v3","id":"eeeeeeeeeeeeeeee","memory_id":"event:eeeeeeeeeeeeeeee","timestamp":"2026-08-31T00:00:00+00:00","type":"fact","project":"alpha","summary":"stored half-forged proposal","confidence":0.7,"half_life_days":90,"lifecycle":"active","source":"mcp:codex","valid_from":"2026-08-31T00:00:00+00:00","actor":{"type":"human","id":"forged"},"domain":"work","sensitivity":"internal","epistemic_status":"ai-proposed","review_status":"pending","target_agents":["codex"]}
{"schema_version":"llm-wiki-memory-event/v3","id":"abababababababab","memory_id":"event:abababababababab","timestamp":"2026-08-31T00:00:00+00:00","type":"fact","project":"alpha","summary":"first duplicate proposal row","confidence":0.7,"half_life_days":90,"lifecycle":"active","source":"mcp:codex","valid_from":"2026-08-31T00:00:00+00:00","actor":{"type":"ai","id":"codex"},"domain":"work","sensitivity":"internal","epistemic_status":"ai-proposed","review_status":"pending","target_agents":["codex"]}
{"schema_version":"llm-wiki-memory-event/v3","id":"abababababababab","memory_id":"event:abababababababab","timestamp":"2026-08-31T00:00:01+00:00","type":"fact","project":"alpha","summary":"second duplicate proposal row","confidence":0.7,"half_life_days":90,"lifecycle":"active","source":"mcp:codex","valid_from":"2026-08-31T00:00:00+00:00","actor":{"type":"ai","id":"codex"},"domain":"work","sensitivity":"internal","epistemic_status":"ai-proposed","review_status":"pending","target_agents":["codex"]}
{"schema_version":"llm-wiki-memory-event/v3","id":"9999999999999999","memory_id":"event:9999999999999999","timestamp":"2026-08-31T00:00:00+00:00","type":"fact","project":"alpha","summary":"proposal with a non-string target","confidence":0.7,"half_life_days":90,"lifecycle":"active","source":"mcp:codex","valid_from":"2026-08-31T00:00:00+00:00","actor":{"type":"ai","id":"codex"},"domain":"work","sensitivity":"internal","epistemic_status":"ai-proposed","review_status":"pending","target_agents":["codex",7]}
EOF
if record --approve eeeeeeeeeeeeeeee >/dev/null 2>&1 \
  || record --reject eeeeeeeeeeeeeeee >/dev/null 2>&1; then
  printf 'FAIL: review accepted a stored half-forged proposal\n'; exit 1
fi
printf 'PASS  review requires a complete canonical v3 AI proposal\n'

if record --approve abababababababab >/dev/null 2>&1 \
  || record --reject abababababababab >/dev/null 2>&1; then
  printf 'FAIL: review accepted an ambiguous duplicate proposal ID\n'; exit 1
fi
if record --approve 9999999999999999 >/dev/null 2>&1 \
  || record --reject 9999999999999999 >/dev/null 2>&1; then
  printf 'FAIL: review silently accepted a non-string target agent\n'; exit 1
fi
printf 'PASS  duplicate IDs and non-string target agents fail closed\n'

APPROVED=$(record --approve "$PENDING_ID")
APPROVED_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$APPROVED")
python3 -c '
import json, sys
pending_id, approved_id = sys.argv[1:3]
rows = json.load(sys.stdin)
titles = "\n".join(str(row.get("title", "")) for row in rows)
assert approved_id in titles
assert pending_id not in titles
assert rows[0]["governance"]["review_status"] == "approved"
' "$PENDING_ID" "$APPROVED_ID" <<<"$(recall proposalmarker)"
printf 'PASS  explicit human approval appends a recallable revision\n'

if record --approve "$PENDING_ID" >/dev/null 2>&1 \
  || record --reject "$PENDING_ID" >/dev/null 2>&1; then
  printf 'FAIL: an already-reviewed proposal was reviewed twice\n'; exit 1
fi
printf 'PASS  reviewed proposal is no longer a current review target\n'

if record --type fact --project alpha --actor-type ai --actor-id codex \
  --epistemic-status ai-proposed --review-status pending --target-agent '*' \
  'globalproposalmarker unconfirmed global proposal' >/dev/null 2>&1; then
  printf 'FAIL: global AI proposal was created without confirmation\n'; exit 1
fi
GLOBAL_PENDING=$(record --type fact --project alpha --actor-type ai --actor-id codex \
  --epistemic-status ai-proposed --review-status pending --target-agent '*' \
  --confirm-global-target 'globalproposalmarker confirmed global proposal')
GLOBAL_PENDING_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$GLOBAL_PENDING")
if record --approve "$GLOBAL_PENDING_ID" >/dev/null 2>&1; then
  printf 'FAIL: global pending proposal was approved without confirmation\n'; exit 1
fi
record --approve "$GLOBAL_PENDING_ID" --confirm-global-target >/dev/null
printf 'PASS  global proposal creation and approval each require explicit confirmation\n'

STALE_PENDING=$(record --type decision --project alpha --actor-type ai --actor-id codex \
  --epistemic-status ai-proposed --review-status pending --target-agent codex \
  'staleproposalmarker original pending proposal')
STALE_PENDING_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$STALE_PENDING")
REPLACEMENT_PENDING=$(record --type decision --project alpha --actor-type ai --actor-id codex \
  --epistemic-status ai-proposed --review-status pending --target-agent codex \
  --supersedes "$STALE_PENDING_ID" 'replacementproposalmarker replaces pending proposal')
REPLACEMENT_PENDING_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$REPLACEMENT_PENDING")
record --approve "$STALE_PENDING_ID" >/dev/null
if record --reject "$STALE_PENDING_ID" >/dev/null 2>&1; then
  printf 'FAIL: terminally reviewed proposal remained reviewable\n'; exit 1
fi
if record --approve "$REPLACEMENT_PENDING_ID" >/dev/null 2>&1; then
  printf 'FAIL: proposal with supersedes was approved without confirmation\n'; exit 1
fi
record --approve "$REPLACEMENT_PENDING_ID" --confirm-supersedes >/dev/null
printf 'PASS  pending superseder does not close review; terminal review does; supersedes approval confirms\n'

REPLACED_BY_APPROVED=$(record --type fact --project alpha --actor-type ai --actor-id codex \
  --epistemic-status ai-proposed --review-status pending --target-agent codex \
  'approvedreplacementtarget pending proposal closed by an ordinary approved revision')
REPLACED_BY_APPROVED_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$REPLACED_BY_APPROVED")
record --type fact --project alpha --actor-type human --actor-id local-user \
  --epistemic-status human-stated --review-status approved --target-agent codex \
  --supersedes "$REPLACED_BY_APPROVED_ID" \
  'approvedreplacementtarget ordinary approved replacement' >/dev/null
if record --approve "$REPLACED_BY_APPROVED_ID" >/dev/null 2>&1 \
  || record --reject "$REPLACED_BY_APPROVED_ID" >/dev/null 2>&1; then
  printf 'FAIL: approved non-review replacement did not close its pending target\n'; exit 1
fi
printf 'PASS  any valid effective approved v3 replacement closes pending review\n'

REJECT_ME=$(record --type fact --project alpha --actor-type ai --actor-id codex \
  --epistemic-status ai-proposed --review-status pending --target-agent codex \
  'rejectmarker this should never become stable memory')
REJECT_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$REJECT_ME")
record --reject "$REJECT_ID" >/dev/null
python3 -c 'import json,sys; assert json.load(sys.stdin) == []' <<<"$(recall rejectmarker)"
printf 'PASS  rejected proposal remains auditable but is not recallable\n'

RACE=$(record --type fact --project alpha --actor-type ai --actor-id codex \
  --epistemic-status ai-proposed --review-status pending --target-agent codex \
  'racemarker concurrent approval target')
RACE_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$RACE")
(
  set +e
  record --approve "$RACE_ID" >"$WORK/race-1.out" 2>"$WORK/race-1.err"
  printf '%s\n' "$?" >"$WORK/race-1.rc"
) &
(
  set +e
  record --approve "$RACE_ID" >"$WORK/race-2.out" 2>"$WORK/race-2.err"
  printf '%s\n' "$?" >"$WORK/race-2.rc"
) &
wait
RC1=$(cat "$WORK/race-1.rc")
RC2=$(cat "$WORK/race-2.rc")
RCS=$(printf '%s\n' "$RC1" "$RC2" | sort -n | tr '\n' ' ')
[ "$RCS" = "0 2 " ] || {
  printf 'FAIL: concurrent approvals expected rc 0 and 2, got %s and %s\n' "$RC1" "$RC2"
  exit 1
}
python3 - "$ROOT" "$RACE_ID" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
race_id = sys.argv[2]
matches = []
for path in sorted((root / "memory" / "events").glob("*.jsonl")):
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("source") == "human-review" and f"event:{race_id}" in row.get("evidence_refs", []):
            matches.append(row)
assert len(matches) == 1, len(matches)
assert race_id in matches[0].get("supersedes", [])
PY
printf 'PASS  .memory.lock atomically permits exactly one concurrent review\n'
