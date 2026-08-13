#!/usr/bin/env bash
# Backward-compatible temporal validity and explicit supersession tests.
set -euo pipefail

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
EVENT="$REPO/bin/llm-wiki-event"
ENRICH="$REPO/bin/llm-wiki-enrich"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/wiki-event-life.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
ROOT="$WORK/root"
mkdir -p "$ROOT/wiki" "$ROOT/memory/events"
printf '# index\n' >"$ROOT/wiki/index.md"

record() {
  env LLM_WIKI_ROOT="$ROOT" python3 "$EVENT" "$@" --print
}

OLD=$(record --type preference --project alpha 'editorchoice vim old')
OLD_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$OLD")
python3 -c '
import json,sys
event=json.load(sys.stdin)
assert event["schema_version"] == "llm-wiki-memory-event/v2"
assert event["valid_from"].endswith("+00:00")
' <<<"$OLD"
printf 'PASS  v2 event has normalized valid_from\n'

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
