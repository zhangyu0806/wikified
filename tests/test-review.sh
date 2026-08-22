#!/usr/bin/env bash
# llm-wiki-review 聚合入口 + corrections 流转测试。自建临时 fixture，不碰真实数据。
set -euo pipefail

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
REVIEW="$REPO/bin/llm-wiki-review"
CORRECT="$REPO/bin/llm-wiki-correct"
EVENT="$REPO/bin/llm-wiki-event"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/wiki-review.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
ROOT="$WORK/root"
mkdir -p "$ROOT/wiki/dashboards" "$ROOT/memory/events" "$ROOT/raw/notes" "$ROOT/raw/inbox/auto-drafts"
printf '# SCHEMA\n' >"$ROOT/SCHEMA.md"
printf '# index\n' >"$ROOT/wiki/index.md"

# --- fixtures ---
# 一条 pending correction + 一条已 resolved（不该出现在复盘）
env LLM_WIKI_ROOT="$ROOT" python3 "$CORRECT" --root "$ROOT" "以后结论用中文" --kind preference >/dev/null
env LLM_WIKI_ROOT="$ROOT" python3 "$CORRECT" --root "$ROOT" "已经处理过的旧偏好" --kind correction >/dev/null

# 未编译 raw（无 status）+ 已编译 raw（status: compiled，不该计入）
printf '# 未编译笔记\nsome content\n' >"$ROOT/raw/notes/uncompiled.md"
printf -- '---\nstatus: compiled\n---\n# 已编译\n' >"$ROOT/raw/notes/done.md"

# 一条很老的 event（valid_from 远超半衰期）→ 应成为 expire 候选
cat >"$ROOT/memory/events/2020-01.jsonl" <<'EOF'
{"schema_version":"llm-wiki-memory-event/v2","id":"aaaaaaaaaaaaaaaa","timestamp":"2020-01-01T00:00:00+00:00","type":"fact","project":"old","summary":"very old fact","confidence":0.7,"half_life_days":90,"lifecycle":"active","valid_from":"2020-01-01T00:00:00+00:00"}
EOF
# 一条新 event → 不该成为 expire 候选
cat >"$ROOT/memory/events/2999-01.jsonl" <<'EOF'
{"schema_version":"llm-wiki-memory-event/v2","id":"bbbbbbbbbbbbbbbb","timestamp":"2999-01-01T00:00:00+00:00","type":"fact","project":"new","summary":"fresh fact","confidence":0.7,"half_life_days":90,"lifecycle":"active","valid_from":"2999-01-01T00:00:00+00:00"}
EOF

# --- 1. JSON 聚合正确性 ---
OUT=$(env LLM_WIKI_ROOT="$ROOT" python3 "$REVIEW" --json --peek --root "$ROOT")
python3 -c '
import json,sys
r=json.load(sys.stdin)
assert len(r["corrections_pending"]) == 2, "corrections=%d" % len(r["corrections_pending"])
assert r["event_total"] == 2, r["event_total"]
# 老 event 应入 expire 候选，新 event 不应
ids=[e["id"] for e in r["expire_candidates"]]
assert "aaaaaaaaaaaaaaaa" in ids, "old event missing from expire candidates"
assert "bbbbbbbbbbbbbbbb" not in ids, "fresh event wrongly flagged expire"
# 未编译 raw 计入、已编译不计
paths=[x["path"] for x in r["uncompiled_raw"]]
assert any("uncompiled.md" in p for p in paths), paths
assert not any("done.md" in p for p in paths), "compiled raw wrongly counted"
' <<<"$OUT"
printf 'PASS  review 聚合：corrections/events/expire/uncompiled-raw 分类正确\n'

# --- 2. --peek 不写时间戳，正式跑写时间戳 ---
STAMP="$ROOT/wiki/dashboards/.last-review"
[ ! -f "$STAMP" ] || { printf 'FAIL: --peek 不应写时间戳\n'; exit 1; }
env LLM_WIKI_ROOT="$ROOT" python3 "$REVIEW" --root "$ROOT" >/dev/null
[ -f "$STAMP" ] || { printf 'FAIL: 正式复盘应写时间戳\n'; exit 1; }
printf 'PASS  --peek 只读；正式复盘更新 last-review 时间戳\n'

# --- 3. corrections 流转：resolve 后从 pending 移除 ---
CID=$(env LLM_WIKI_ROOT="$ROOT" python3 "$CORRECT" --root "$ROOT" --list | grep -oE '\[[0-9a-f]{12}\]' | head -1 | tr -d '[]')
env LLM_WIKI_ROOT="$ROOT" python3 "$CORRECT" --root "$ROOT" --resolve "$CID" --status rejected >/dev/null
REMAIN=$(env LLM_WIKI_ROOT="$ROOT" python3 "$REVIEW" --json --peek --root "$ROOT" | python3 -c 'import json,sys;print(len(json.load(sys.stdin)["corrections_pending"]))')
[ "$REMAIN" = "1" ] || { printf 'FAIL: resolve 后 pending 应剩 1，实际 %s\n' "$REMAIN"; exit 1; }
# rejected 行仍保留在文件里（留档，不删）
grep -q '"status": "rejected"' "$ROOT/raw/inbox/corrections.jsonl" || { printf 'FAIL: rejected 应留档\n'; exit 1; }
printf 'PASS  corrections 流转：resolve 移出 pending 且留档\n'

# --- 4. 非 Wikified 根 fail-closed ---
if env LLM_WIKI_ROOT="$WORK/nope" python3 "$REVIEW" --root "$WORK/nope" >/dev/null 2>&1; then
  printf 'FAIL: 缺 SCHEMA.md 应报错\n'; exit 1
fi
printf 'PASS  非 Wikified 数据根 fail-closed\n'

printf '\n--- result: all review tests PASS ---\n'
