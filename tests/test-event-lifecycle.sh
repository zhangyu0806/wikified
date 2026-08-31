#!/usr/bin/env bash
# Backward-compatible temporal validity and explicit supersession tests.
set -euo pipefail

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
EVENT="$REPO/bin/llm-wiki-event"
ENRICH="$REPO/bin/llm-wiki-enrich"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/wiki-event-life.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
ROOT="$WORK/root"
mkdir -p "$ROOT/wiki" "$ROOT/memory/events" "$ROOT/policy"
printf '# index\n' >"$ROOT/wiki/index.md"
cp "$REPO/templates/access-policy.json" "$ROOT/policy/access.json"

record() {
  env LLM_WIKI_ROOT="$ROOT" python3 "$EVENT" "$@" --print
}

OLD=$(record --type preference --project alpha 'editorchoice vim old')
OLD_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$OLD")
python3 -c '
import json,sys
event=json.load(sys.stdin)
assert event["schema_version"] == "llm-wiki-memory-event/v3"
assert event["memory_id"] == "event:" + event["id"]
assert event["domain"] == "work"
assert event["sensitivity"] == "internal"
assert event["epistemic_status"] == "human-stated"
assert event["review_status"] == "approved"
assert event["target_agents"] == ["coding"]
assert event["valid_from"].endswith("+00:00")
' <<<"$OLD"
printf 'PASS  v3 event has governance envelope, scoped target, and normalized valid_from\n'

if record --type fact --target-agent '*' global-without-confirm >/dev/null 2>&1; then
  printf 'FAIL: global * target accepted without confirmation\n'; exit 1
fi
if record --type fact --target-agent all global-all-without-confirm >/dev/null 2>&1; then
  printf 'FAIL: global all target accepted without confirmation\n'; exit 1
fi
GLOBAL_STAR=$(record --type fact --target-agent '*' --confirm-global-target global-star-confirmed)
GLOBAL_ALL=$(record --type fact --target-agent all --confirm-global-target global-all-confirmed)
python3 -c '
import json,sys
assert json.loads(sys.argv[1])["target_agents"] == ["*"]
assert json.loads(sys.argv[2])["target_agents"] == ["all"]
' "$GLOBAL_STAR" "$GLOBAL_ALL"
printf 'PASS  */all targets require and preserve explicit global confirmation\n'

if record --type fact --project alpha --valid-from not-a-time badtime >/dev/null 2>&1; then
  printf 'FAIL: invalid time accepted\n'; exit 1
fi
printf 'PASS  invalid time fails closed\n'

if record --type fact --project alpha --valid-from 2030-01-02 --valid-until 2030-01-01 reversed >/dev/null 2>&1; then
  printf 'FAIL: reversed validity accepted\n'; exit 1
fi
printf 'PASS  valid_until must follow valid_from\n'

if record --type fact --project alpha --supersedes 0000000000000000 missing >/dev/null 2>&1; then
  printf 'FAIL: missing supersedes target accepted\n'; exit 1
fi
printf 'PASS  missing supersedes target fails closed\n'

OTHER=$(record --type fact --project beta other-project)
OTHER_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$OTHER")
if record --type fact --project alpha --supersedes "$OTHER_ID" cross-project >/dev/null 2>&1; then
  printf 'FAIL: cross-project supersedes accepted\n'; exit 1
fi
printf 'PASS  cross-project supersedes fails closed\n'

PERSONAL=$(record --type fact --project alpha --domain personal personal-domain-target)
PERSONAL_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$PERSONAL")
if record --type fact --project alpha --domain work --supersedes "$PERSONAL_ID" cross-domain >/dev/null 2>&1; then
  printf 'FAIL: cross-domain supersedes accepted\n'; exit 1
fi
printf 'PASS  cross-domain supersedes fails closed\n'

cat >"$ROOT/memory/events/legacy.jsonl" <<'EOF'
{"schema_version":"llm-wiki-memory-event/v2","id":"1111111111111111","timestamp":"2026-08-01T00:00:00+00:00","type":"fact","project":"alpha","summary":"legacy work target without domain","confidence":0.7,"half_life_days":90,"lifecycle":"active","valid_from":"2026-08-01T00:00:00+00:00"}
{"schema_version":"llm-wiki-memory-event/v2","id":"2222222222222222","timestamp":"2026-08-01T00:00:00+00:00","type":"fact","summary":"legacy unscoped target","confidence":0.7,"half_life_days":90,"lifecycle":"active","valid_from":"2026-08-01T00:00:00+00:00"}
EOF
record --type fact --project alpha --domain work --supersedes 1111111111111111 \
  legacy-domain-default-work >/dev/null
if record --type fact --project alpha --domain work --supersedes 2222222222222222 \
  legacy-unscoped >/dev/null 2>&1; then
  printf 'FAIL: unscoped supersedes target accepted\n'; exit 1
fi
printf 'PASS  v2 missing domain defaults to work, while missing project cannot cross scope\n'

cat >>"$ROOT/memory/events/legacy.jsonl" <<'EOF'
{"schema_version":"llm-wiki-memory-event/v2","id":"3333333333333333","timestamp":"2026-08-01T00:00:00+00:00","type":"fact","project":"alpha","summary":"first row for duplicate id","confidence":0.7,"half_life_days":90,"lifecycle":"active","valid_from":"2026-08-01T00:00:00+00:00"}
{"schema_version":"llm-wiki-memory-event/v2","id":"3333333333333333","timestamp":"2026-08-02T00:00:00+00:00","type":"fact","project":"alpha","summary":"second row for duplicate id","confidence":0.7,"half_life_days":90,"lifecycle":"active","valid_from":"2026-08-02T00:00:00+00:00"}
EOF
if record --type fact --project alpha --domain work --supersedes 3333333333333333 \
  ambiguous-duplicate-target >/dev/null 2>&1; then
  printf 'FAIL: ambiguous duplicate supersedes target accepted\n'; exit 1
fi
printf 'PASS  duplicate supersedes target IDs fail closed\n'

NEW=$(record --type preference --project alpha --supersedes "$OLD_ID" 'editorchoice helix replacement')
NEW_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$NEW")
RESULT=$(env LLM_WIKI_ROOT="$ROOT" python3 "$ENRICH" --query editorchoice --project alpha --json)
python3 -c '
import json, sys
old_id, new_id = sys.argv[1:3]
rows = json.load(sys.stdin)
titles = "\n".join(str(row.get("title", "")) for row in rows)
assert new_id in titles
assert old_id not in titles
' "$OLD_ID" "$NEW_ID" <<<"$RESULT"
printf 'PASS  current recall returns replacement and hides superseded event\n'

record --type fact --project alpha --valid-until 2000-01-01 expiredmarker >/dev/null 2>&1 && {
  printf 'FAIL: already-expired event should violate valid_until > default valid_from\n'; exit 1;
}
printf 'PASS  already-expired event cannot be recorded as current\n'

SECRET=$(record --type fact --project alpha 'token=ghp_FAKE1234567890abcdefghij1234')
python3 -c '
import json, sys
event = json.load(sys.stdin)
serialized = json.dumps(event)
assert "ghp_FAKE1234567890abcdefghij1234" not in serialized
assert "[REDACTED_GITHUB_TOKEN]" in serialized
' <<<"$SECRET"
printf 'PASS  provider credential is redacted before append and keeps an explainable marker\n'
