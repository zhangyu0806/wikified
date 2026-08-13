#!/usr/bin/env bash
# Graphify freshness is advisory by default and explicit in JSON/Markdown.
set -euo pipefail

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
HEALTH="$REPO/bin/llm-wiki-health"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/wiki-graph-health.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
ROOT="$WORK/wiki-root"
mkdir -p "$ROOT/wiki" "$ROOT/graphify-out"
printf '# schema\n' >"$ROOT/SCHEMA.md"
printf '# Current page\n' >"$ROOT/wiki/current.md"

status() {
  python3 "$HEALTH" --root "$ROOT" --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["graph_status"])'
}

rm -rf "$ROOT/graphify-out"
[[ "$(status)" == missing ]]
printf 'PASS  missing graph is reported explicitly\n'

mkdir -p "$ROOT/graphify-out"
printf '# Graph report\n' >"$ROOT/graphify-out/GRAPH_REPORT.md"
python3 - "$ROOT" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
page = root / "wiki/current.md"
(root / "graphify-out/manifest.json").write_text(
    json.dumps({"current.md": {"mtime": page.stat().st_mtime, "ast_hash": "fixture"}}),
    encoding="utf-8",
)
PY
[[ "$(status)" == current ]]
printf 'PASS  matching manifest is current\n'

mkdir -p "$ROOT/wiki/graphify-out/cache"
printf '{}\n' >"$ROOT/wiki/graphify-out/cache/stat-index.json"
printf '%s\n' '---' 'updated: 2000-01-01' '---' '# Derived graph report' >"$ROOT/wiki/graphify-out/GRAPH_REPORT.md"
[[ "$(status)" == current ]]
printf 'PASS  nested Graphify output is excluded from wiki inputs\n'

python3 "$HEALTH" --root "$ROOT" --json | python3 -c '
import json, sys
data = json.load(sys.stdin)
assert not any("graphify-out" in item for item in data["stale_pages"])
'
printf 'PASS  nested Graphify Markdown is excluded from knowledge health checks\n'

python3 - "$ROOT/wiki/current.md" <<'PY'
import os, pathlib, sys
path = pathlib.Path(sys.argv[1])
stamp = path.stat().st_mtime + 5
os.utime(path, (stamp, stamp))
PY
[[ "$(status)" == stale ]]
printf 'PASS  modified wiki input makes graph stale\n'

python3 - "$ROOT" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
page = root / "wiki/current.md"
(root / "graphify-out/manifest.json").write_text(
    json.dumps({"current.md": {"mtime": page.stat().st_mtime, "ast_hash": "fixture"}}),
    encoding="utf-8",
)
(root / "wiki/new.md").write_text("# New page\n", encoding="utf-8")
PY
[[ "$(status)" == stale ]]
printf 'PASS  new wiki input makes graph stale\n'

printf 'not-json\n' >"$ROOT/graphify-out/manifest.json"
[[ "$(status)" == invalid ]]
printf 'PASS  invalid manifest is reported explicitly\n'

# Advisory by default: stale/missing signals do not make ordinary health rc non-zero.
python3 "$HEALTH" --root "$ROOT" --json >/dev/null
printf 'PASS  graph freshness remains advisory without --strict\n'
