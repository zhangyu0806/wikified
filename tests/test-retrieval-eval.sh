#!/usr/bin/env bash
# Pure offline retrieval quality gate. The evaluator creates and removes its own fixture.
set -euo pipefail

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
OUT=$(python3 "$REPO/bin/llm-wiki-eval" --json)

python3 -c '
import json, sys
report = json.load(sys.stdin)
assert report["fixture"] == "generated-temporary-no-user-data"
assert report["hybrid"]["recall_at_5"] == 1.0
assert report["hybrid"]["mrr"] == 1.0
assert report["hybrid"]["recall_at_5"] > report["legacy"]["recall_at_5"]
assert report["violations"] == []
assert all(value == "pass" for value in report["checks"].values())
' <<<"$OUT"

printf 'PASS  retrieval eval: hybrid Recall@5/MRR=1.0, all safety checks pass\n'
