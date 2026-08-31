#!/usr/bin/env bash
# Governance must be checked before candidate body reads, tokenization and ranking.
set -euo pipefail

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
ENRICH="$REPO/bin/llm-wiki-enrich"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/wikified-policy.XXXXXX")
trap 'rm -rf -- "$WORK"' EXIT
ROOT="$WORK/root"
mkdir -p "$ROOT/wiki" "$ROOT/memory/events"

# A data root is unusable until init installs an explicit policy.
if LLM_WIKI_ROOT="$ROOT" python3 "$ENRICH" --agent-profile coding --query anything --json \
    >"$WORK/missing-policy.out" 2>"$WORK/missing-policy.err"; then
  printf 'FAIL: missing policy succeeded\n' >&2; exit 1
fi
[[ ! -s "$WORK/missing-policy.out" ]]
grep -Fq 'run llm-wiki-init' "$WORK/missing-policy.err"
mkdir -p "$ROOT/policy"
cp "$REPO/templates/access-policy.json" "$ROOT/policy/access.json"

cat >"$ROOT/wiki/legacy.md" <<'EOF_LEGACY'
# Legacy page

legacypagemarker remains readable through safe migration defaults.
EOF_LEGACY

cat >"$ROOT/wiki/allowed.md" <<'EOF_ALLOWED'
---
memory_id: wiki:explicit-allowed
domain: work
sensitivity: internal
review_state: approved
target_profiles: [coding]
project: alpha
---
# Allowed page

allowedpagemarker is approved for the coding profile.
EOF_ALLOWED

cat >"$ROOT/wiki/codex.md" <<'EOF_CODEX'
---
domain: work
sensitivity: internal
review_status: approved
target_agents: [codex]
---
# Codex-only page

codexprofilemarker is visible only to the fixed Codex profile.
EOF_CODEX

cat >"$ROOT/wiki/personal.md" <<'EOF_PERSONAL'
---
domain: personal
sensitivity: internal
review_state: approved
target_profiles: [coding]
---
# Personal page

personaldeniedmarker must not enter coding candidates.
EOF_PERSONAL

cat >"$ROOT/wiki/confidential.md" <<'EOF_CONFIDENTIAL'
---
domain: work
sensitivity: confidential
review_state: approved
target_profiles: [coding]
---
# Confidential page

confidentialdeniedmarker must not enter coding candidates.
EOF_CONFIDENTIAL

cat >"$ROOT/wiki/pending.md" <<'EOF_PENDING'
---
domain: work
sensitivity: internal
review_status: pending
target_agents: [coding]
---
# Pending page

pendingdeniedmarker must not enter coding candidates.
EOF_PENDING

cat >"$ROOT/wiki/wrong-target.md" <<'EOF_TARGET'
---
domain: work
sensitivity: internal
review_state: approved
target_profiles: [opencode]
---
# Wrong target

targetdeniedmarker must not enter coding candidates.
EOF_TARGET

cat >"$ROOT/wiki/malformed.md" <<'EOF_MALFORMED'
---
domain: not-a-domain
sensitivity: internal
review_state: approved
target_profiles: [coding]
---
# Malformed governance

malformeddeniedmarker must fail closed at the item boundary.
EOF_MALFORMED

cat >"$ROOT/wiki/blank-before-frontmatter.md" <<'EOF_BLANK_FRONTMATTER'

---
domain: work
sensitivity: internal
review_state: approved
target_profiles: [coding]
---
blankfrontmattermarker must never receive legacy defaults.
EOF_BLANK_FRONTMATTER

{
printf '%s \n' '---'
cat <<'EOF_TRAILING_DELIMITER'
domain: work
sensitivity: internal
review_state: approved
target_profiles: [coding]
---
trailingdelimitermarker must never receive legacy defaults.
EOF_TRAILING_DELIMITER
} >"$ROOT/wiki/trailing-delimiter.md"

python3 - "$ROOT" <<'PY_EVENTS'
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
now = datetime.now(timezone.utc).isoformat()

def event(event_id, marker, **extra):
    row = {
        "schema_version": "llm-wiki-memory-event/v2",
        "id": event_id,
        "timestamp": now,
        "type": "fact",
        "project": "policy-test",
        "files": [],
        "concepts": [],
        "summary": marker,
        "details": "",
        "confidence": 0.8,
        "half_life_days": 90,
        "lifecycle": "active",
        "source": "test",
    }
    row.update(extra)
    return row

def v3(event_id, marker, *, actor_type="human", actor_id="test-writer",
       epistemic="human-stated", review="approved", targets=None, **extra):
    row = event(event_id, marker)
    row.update({
        "schema_version": "llm-wiki-memory-event/v3",
        "memory_id": f"event:{event_id}",
        "actor": {"type": actor_type, "id": actor_id},
        "domain": "work",
        "sensitivity": "internal",
        "epistemic_status": epistemic,
        "review_status": review,
        "target_agents": targets or ["coding"],
    })
    row.update(extra)
    return row

rows = [
    event("1111111111111111", "legacyeventmarker"),
    event("1010101010101010", "v1legacymarker", schema_version="llm-wiki-memory-event/v1"),
    event(
        "2222222222222222",
        "allowedeventmarker",
        domain="work",
        sensitivity="internal",
        review_state="approved",
        target_profiles=["coding"],
    ),
    event(
        "3333333333333333",
        "aliaseventmarker",
        domain="work",
        sensitivity="internal",
        review_status="approved",
        target_agents=["coding"],
    ),
    event(
        "4444444444444444",
        "personaleventdeniedmarker",
        domain="personal",
        sensitivity="internal",
        review_state="approved",
        target_profiles=["coding"],
    ),
    event(
        "5555555555555555",
        "pendingeventdeniedmarker",
        domain="work",
        sensitivity="internal",
        review_state="pending",
        target_profiles=["coding"],
    ),
    # A denied proposal cannot hide an approved event through supersession.
    event(
        "6666666666666666",
        "legacyeventmarker unauthorized replacement",
        domain="work",
        sensitivity="internal",
        review_state="pending",
        target_profiles=["coding"],
        supersedes=["1111111111111111"],
    ),
    # A hidden approved revision suppresses a stale visible event.
    event("7777777777777777", "hiddenrevisionmarker old visible"),
    v3(
        "8888888888888888",
        "hiddenrevisionmarker new opencode-only",
        targets=["opencode"],
        supersedes=["7777777777777777"],
    ),
    # Rejection cannot suppress an already-approved fact.
    event("9999999999999999", "rejectapprovedmarker approved target"),
    v3(
        "aaaaaaaaaaaaaaaa",
        "rejectapprovedmarker rejected revision",
        epistemic="disputed",
        review="rejected",
        supersedes=["9999999999999999"],
    ),
    # Rejection does suppress the pending proposal it reviewed.
    v3(
        "bbbbbbbbbbbbbbbb",
        "rejectpendingmarker pending target",
        actor_type="ai",
        actor_id="test-agent",
        epistemic="ai-proposed",
        review="pending",
    ),
    v3(
        "cccccccccccccccc",
        "rejectpendingmarker rejected revision",
        epistemic="disputed",
        review="rejected",
        supersedes=["bbbbbbbbbbbbbbbb"],
    ),
    # Pending proposals never suppress approved memory.
    event("dddddddddddddddd", "pendingsupersedermarker approved target"),
    v3(
        "eeeeeeeeeeeeeeee",
        "pendingsupersedermarker pending revision",
        actor_type="ai",
        actor_id="test-agent",
        epistemic="ai-proposed",
        review="pending",
        supersedes=["dddddddddddddddd"],
    ),
    v3("abababababababab", "codinggroupmarker valid v3"),
    v3(
        "cdcdcdcdcdcdcdcd",
        "maliciousv3marker actor invariant",
        actor_type="ai",
        epistemic="human-stated",
        review="approved",
    ),
    event(
        "efefefefefefefef",
        "unknownschemamarker",
        schema_version="llm-wiki-memory-event/v99",
    ),
    event(
        "f0f0f0f0f0f0f0f0",
        "incompletev3marker",
        schema_version="llm-wiki-memory-event/v3",
        memory_id="event:f0f0f0f0f0f0f0f0",
    ),
    event("1313131313131313", "crossprojectmarker approved target"),
    v3(
        "1414141414141414",
        "crossprojectmarker hidden wrong-project revision",
        targets=["opencode"],
        project="another-project",
        supersedes=["1313131313131313"],
    ),
    event("1515151515151515", "crossdomainmarker approved target"),
    v3(
        "1616161616161616",
        "crossdomainmarker hidden personal revision",
        targets=["opencode"],
        domain="personal",
        supersedes=["1515151515151515"],
    ),
]
absent_schema = event("1212121212121212", "absentschemamarker")
absent_schema.pop("schema_version")
rows.append(absent_schema)
path = root / "memory" / "events" / "policy.jsonl"
path.write_text("\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n", encoding="utf-8")
tail = event("1717171717171717", "overlongtailmarker")
(root / "memory" / "events" / "zz-overlong.jsonl").write_text(
    "{" + ("x" * (256 * 1024)) + "\n" + json.dumps(tail) + "\n",
    encoding="utf-8",
)
PY_EVENTS

search() {
  LLM_WIKI_ROOT="$ROOT" python3 "$ENRICH" --agent-profile "$1" --query "$2" --json --max-chars 12000
}

python3 -c '
import json, sys
rows=json.load(sys.stdin)
assert len(rows) == 1, rows
row=rows[0]
assert row["title"] == "legacy.md"
assert row["memory_id"].startswith("wiki:")
assert row["governance"]["domain"] == "work"
assert row["governance"]["sensitivity"] == "internal"
assert row["governance"]["review_state"] == "approved"
assert row["governance"]["review_status"] == "approved"
assert row["governance"]["target_profiles"] == ["*"]
assert row["governance"]["epistemic_status"] == "legacy-imported"
' <<<"$(search coding legacypagemarker)"

LEGACY_ID=$(search coding legacypagemarker | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["memory_id"])')
[[ "$LEGACY_ID" == "$(search coding legacypagemarker | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["memory_id"])')" ]]

python3 -c '
import json, sys
rows=json.load(sys.stdin)
assert len(rows) == 1 and rows[0]["memory_id"] == "event:1111111111111111", rows
assert rows[0]["governance"]["review_status"] == "approved"
assert rows[0]["governance"]["epistemic_status"] == "legacy-imported"
' <<<"$(search coding legacyeventmarker)"

python3 -c 'import json,sys; rows=json.load(sys.stdin); assert rows[0]["memory_id"] == "event:1010101010101010"' \
  <<<"$(search coding v1legacymarker)"
python3 -c 'import json,sys; rows=json.load(sys.stdin); assert rows[0]["memory_id"] == "event:1212121212121212"' \
  <<<"$(search coding absentschemamarker)"

for marker in personaldeniedmarker confidentialdeniedmarker pendingdeniedmarker targetdeniedmarker malformeddeniedmarker blankfrontmattermarker trailingdelimitermarker personaleventdeniedmarker pendingeventdeniedmarker maliciousv3marker unknownschemamarker incompletev3marker overlongtailmarker; do
  [[ "$(search coding "$marker")" == "[]" ]] || { printf 'FAIL: denied marker leaked: %s\n' "$marker" >&2; exit 1; }
done

python3 -c 'import json,sys; rows=json.load(sys.stdin); assert rows[0]["memory_id"] == "wiki:explicit-allowed"' \
  <<<"$(search coding allowedpagemarker)"
[[ "$(LLM_WIKI_ROOT="$ROOT" python3 "$ENRICH" --agent-profile coding --project beta --query allowedpagemarker --json)" == "[]" ]]
python3 -c 'import json,sys; assert json.load(sys.stdin)[0]["memory_id"] == "wiki:explicit-allowed"' \
  <<<"$(LLM_WIKI_ROOT="$ROOT" python3 "$ENRICH" --agent-profile coding --project alpha --query allowedpagemarker --json)"
MARKDOWN=$(LLM_WIKI_ROOT="$ROOT" python3 "$ENRICH" --agent-profile coding --query allowedpagemarker)
grep -Fq 'provenance: memory_id=wiki:explicit-allowed' <<<"$MARKDOWN"
grep -Fq 'epistemic_status=legacy-imported' <<<"$MARKDOWN"
grep -Fq 'review_status=approved' <<<"$MARKDOWN"
python3 -c 'import json,sys; rows=json.load(sys.stdin); assert rows[0]["memory_id"] == "event:3333333333333333"' \
  <<<"$(search coding aliaseventmarker)"
python3 -c 'import json,sys; rows=json.load(sys.stdin); assert rows and rows[0]["title"] == "codex.md"' \
  <<<"$(search codex codexprofilemarker)"
[[ "$(search coding codexprofilemarker)" == "[]" ]]

# Profiles can accept a group target without letting the event expand domain/sensitivity policy.
python3 -c 'import json,sys; rows=json.load(sys.stdin); assert rows[0]["memory_id"] == "event:abababababababab"' \
  <<<"$(search codex codinggroupmarker)"

# Supersession is resolved from all bounded metadata before profile authorization.
[[ "$(search coding hiddenrevisionmarker)" == "[]" ]]
python3 -c 'import json,sys; rows=json.load(sys.stdin); assert rows[0]["memory_id"] == "event:8888888888888888"' \
  <<<"$(search opencode hiddenrevisionmarker)"
python3 -c 'import json,sys; rows=json.load(sys.stdin); assert len(rows) == 1 and rows[0]["memory_id"] == "event:9999999999999999"' \
  <<<"$(search coding rejectapprovedmarker)"
[[ "$(search human rejectpendingmarker)" == "[]" ]]
python3 -c 'import json,sys; rows=json.load(sys.stdin); assert len(rows) == 1 and rows[0]["memory_id"] == "event:dddddddddddddddd"' \
  <<<"$(search coding pendingsupersedermarker)"
python3 -c 'import json,sys; rows=json.load(sys.stdin); assert any(row["memory_id"] == "event:1313131313131313" for row in rows)' \
  <<<"$(search coding crossprojectmarker)"
python3 -c 'import json,sys; rows=json.load(sys.stdin); assert any(row["memory_id"] == "event:1515151515151515" for row in rows)' \
  <<<"$(search coding crossdomainmarker)"

# Human review can see pending/personal items regardless of their target profile.
python3 -c 'import json,sys; rows=json.load(sys.stdin); assert rows and rows[0]["governance"]["domain"] == "personal"' \
  <<<"$(search human personaldeniedmarker)"
python3 -c 'import json,sys; rows=json.load(sys.stdin); assert rows and rows[0]["governance"]["review_status"] == "pending"' \
  <<<"$(search human pendingdeniedmarker)"

# The full-page path shares the same authorization and emits no page detail on denial.
LLM_WIKI_ROOT="$ROOT" python3 "$ENRICH" --agent-profile coding --read-page wiki/allowed.md \
  | grep -q allowedpagemarker
if LLM_WIKI_ROOT="$ROOT" python3 "$ENRICH" --agent-profile coding --project beta \
    --read-page wiki/allowed.md >"$WORK/project-denied.out" 2>/dev/null; then
  printf 'FAIL: cross-project full-page read succeeded\n' >&2; exit 1
fi
[[ ! -s "$WORK/project-denied.out" ]]
if LLM_WIKI_ROOT="$ROOT" python3 "$ENRICH" --agent-profile coding --read-page wiki/personal.md \
    >"$WORK/denied.out" 2>"$WORK/denied.err"; then
  printf 'FAIL: unauthorized full-page read succeeded\n' >&2; exit 1
fi
[[ ! -s "$WORK/denied.out" ]]
grep -Fxq 'llm-wiki-enrich: page unavailable' "$WORK/denied.err"
if LLM_WIKI_ROOT="$ROOT" python3 "$ENRICH" --agent-profile coding --read-page wiki/../memory/events/policy.jsonl \
    >"$WORK/traversal.out" 2>/dev/null; then
  printf 'FAIL: traversal full-page read succeeded\n' >&2; exit 1
fi
[[ ! -s "$WORK/traversal.out" ]]

# Governed roots and JSONL inputs cannot be replaced with symlinks.
WIKI_LINK_ROOT="$WORK/wiki-link-root"
mkdir -p "$WIKI_LINK_ROOT/policy"
cp "$REPO/templates/access-policy.json" "$WIKI_LINK_ROOT/policy/access.json"
if ln -s "$ROOT/wiki" "$WIKI_LINK_ROOT/wiki" 2>/dev/null && [[ -L "$WIKI_LINK_ROOT/wiki" ]]; then
  [[ "$(LLM_WIKI_ROOT="$WIKI_LINK_ROOT" python3 "$ENRICH" --agent-profile coding --query legacypagemarker --json)" == "[]" ]]
fi
EVENT_LINK_ROOT="$WORK/event-link-root"
mkdir -p "$EVENT_LINK_ROOT/policy" "$EVENT_LINK_ROOT/wiki" "$EVENT_LINK_ROOT/memory"
cp "$REPO/templates/access-policy.json" "$EVENT_LINK_ROOT/policy/access.json"
if ln -s "$ROOT/memory/events" "$EVENT_LINK_ROOT/memory/events" 2>/dev/null && [[ -L "$EVENT_LINK_ROOT/memory/events" ]]; then
  [[ "$(LLM_WIKI_ROOT="$EVENT_LINK_ROOT" python3 "$ENRICH" --agent-profile coding --query legacyeventmarker --json)" == "[]" ]]
fi
mkdir -p "$ROOT/wiki/context"
printf 'sessionstartsymlinkmarker\n' >"$ROOT/non-wiki-private.txt"
if ln -s "$ROOT/non-wiki-private.txt" "$ROOT/wiki/context/CRITICAL_FACTS.md" 2>/dev/null \
    && [[ -L "$ROOT/wiki/context/CRITICAL_FACTS.md" ]]; then
  ! LLM_WIKI_ROOT="$ROOT" python3 "$ENRICH" --agent-profile coding --session-start \
    | grep -q sessionstartsymlinkmarker
fi

# Instrument the ranker: denied pages get exactly the metadata-prefix open, never the body open.
python3 - "$ENRICH" "$WORK/prefilter-root" "$REPO/templates/access-policy.json" <<'PY_PREFILTER'
import importlib.machinery
import importlib.util
from pathlib import Path
import shutil
from unittest import mock
import sys

loader = importlib.machinery.SourceFileLoader("llm_wiki_enrich_policy_test", sys.argv[1])
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
sys.modules[loader.name] = module
loader.exec_module(module)
root = Path(sys.argv[2])
(root / "wiki").mkdir(parents=True)
(root / "policy").mkdir(parents=True)
shutil.copyfile(sys.argv[3], root / "policy" / "access.json")
(root / "wiki" / "allowed.md").write_text(
    "---\ndomain: work\nsensitivity: internal\nreview_state: approved\ntarget_profiles: [coding]\n---\nallowedbodymarker\n",
    encoding="utf-8",
)
(root / "wiki" / "denied.md").write_text(
    "---\ndomain: personal\nsensitivity: restricted\nreview_state: approved\ntarget_profiles: [coding]\n---\ndeniedbodymarker\n",
    encoding="utf-8",
)
access = module.access_context(root, "coding")
real_open = Path.open
counts = {}

def tracked(path, *args, **kwargs):
    counts[path.name] = counts.get(path.name, 0) + 1
    return real_open(path, *args, **kwargs)

with mock.patch.object(Path, "open", tracked):
    module.wiki_candidates(
        root,
        "bodymarker",
        module.token_terms(["bodymarker"]),
        module.query_term_groups(["bodymarker"]),
        access,
    )
assert counts["allowed.md"] == 2, counts
assert counts["denied.md"] == 1, counts
PY_PREFILTER

# Existing-but-invalid policy and unknown profiles fail closed with no candidate output.
mkdir -p "$ROOT/policy"
printf '{not-json\n' >"$ROOT/policy/access.json"
if LLM_WIKI_ROOT="$ROOT" python3 "$ENRICH" --agent-profile coding --query legacypagemarker --json \
    >"$WORK/invalid.out" 2>/dev/null; then
  printf 'FAIL: malformed policy succeeded\n' >&2; exit 1
fi
[[ ! -s "$WORK/invalid.out" ]]
rm "$ROOT/policy/access.json"
cp "$REPO/templates/access-policy.json" "$ROOT/policy/access.json"
if LLM_WIKI_ROOT="$ROOT" python3 "$ENRICH" --agent-profile nonexistent --query legacypagemarker --json \
    >"$WORK/unknown.out" 2>/dev/null; then
  printf 'FAIL: unknown profile succeeded\n' >&2; exit 1
fi
[[ ! -s "$WORK/unknown.out" ]]

printf 'PASS  policy prefilter, aliases, stable ids, full-page ACL and fail-closed behavior\n'
