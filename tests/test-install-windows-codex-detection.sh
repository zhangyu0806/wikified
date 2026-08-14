#!/usr/bin/env bash
# WSL installer must recognize existing Windows Codex wiring without mutating it.
set -euo pipefail

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
WORK=$(mktemp -d "${TMPDIR:-/tmp}/wikified-install-win.XXXXXX")
trap 'rm -rf -- "$WORK"' EXIT
FAKE_HOME="$WORK/home"
WINDOWS_CODEX_HOME="$WORK/windows-codex"
BIN="$WORK/bin"
mkdir -p "$FAKE_HOME" "$WINDOWS_CODEX_HOME"

cat > "$WINDOWS_CODEX_HOME/config.toml" <<'EOF_TOML'
[mcp_servers.llm-wiki]
command = "wsl.exe"
args = ["-d", "Ubuntu", "-e", "/home/test/.local/bin/llm-wiki-mcp"]
EOF_TOML
cp "$REPO/templates/codex/hooks.json" "$WINDOWS_CODEX_HOME/hooks.json"
cp "$REPO/templates/codex/AGENTS.recall.md" "$WINDOWS_CODEX_HOME/AGENTS.md"
BEFORE=$(sha256sum "$WINDOWS_CODEX_HOME"/*)

env \
  HOME="$FAKE_HOME" \
  XDG_CACHE_HOME="$WORK/cache" \
  LLM_WIKI_DISABLE_PATH_DETECTION=1 \
  LLM_WIKI_BIN_TARGET="$BIN" \
  LLM_WIKI_OPENCODE_PLUGIN_TARGET="$WORK/opencode-plugins" \
  LLM_WIKI_AGENT_SKILL_ROOT="$WORK/agent-skills" \
  LLM_WIKI_SKILL_FANOUT="" \
  LLM_WIKI_WINDOWS_CODEX_HOME="$WINDOWS_CODEX_HOME" \
  "$REPO/install.sh" > "$WORK/install.out"

env \
  HOME="$FAKE_HOME" \
  LLM_WIKI_DISABLE_PATH_DETECTION=1 \
  LLM_WIKI_BIN_TARGET="$BIN" \
  LLM_WIKI_REPO="$REPO" \
  LLM_WIKI_WINDOWS_CODEX_HOME="$WINDOWS_CODEX_HOME" \
  "$BIN/llm-wiki-harness" status --json > "$WORK/status.json"

python3 - "$WORK/status.json" <<'PY_STATUS'
import json,sys
items={x['harness']:x for x in json.load(open(sys.argv[1], encoding='utf-8'))['harnesses']}
codex=items['codex']
assert codex['executable']=='external-config', codex
assert codex['mcp']=='configured', codex
assert codex['lifecycle']=='configured', codex
assert codex['rules']=='configured', codex
assert codex['overall']=='configured', codex
PY_STATUS
AFTER=$(sha256sum "$WINDOWS_CODEX_HOME"/*)
[[ "$BEFORE" == "$AFTER" ]] || { printf 'FAIL: installer changed Windows Codex files\n' >&2; exit 1; }

printf 'PASS  WSL installer recognizes existing Windows Codex MCP, hook and AGENTS wiring without mutation\n'

# A configured Windows home must not mask an unverifiable WSL config symlink.
# Status is read-only and must not follow or mutate the symlink target.
mkdir -p "$FAKE_HOME/.codex" "$WORK/private"
cat > "$WORK/private/config.toml" <<'EOF_PRIVATE'
[mcp_servers.llm-wiki]
command = "/private/data-repo/llm-wiki-mcp"
EOF_PRIVATE
ln -s "$WORK/private/config.toml" "$FAKE_HOME/.codex/config.toml"
PRIVATE_BEFORE=$(sha256sum "$WORK/private/config.toml")
env \
  HOME="$FAKE_HOME" \
  LLM_WIKI_DISABLE_PATH_DETECTION=1 \
  LLM_WIKI_BIN_TARGET="$BIN" \
  LLM_WIKI_REPO="$REPO" \
  LLM_WIKI_WINDOWS_CODEX_HOME="$WINDOWS_CODEX_HOME" \
  "$BIN/llm-wiki-harness" status --json > "$WORK/status-symlink.json"
python3 - "$WORK/status-symlink.json" <<'PY_SYMLINK'
import json,sys
items={x['harness']:x for x in json.load(open(sys.argv[1], encoding='utf-8'))['harnesses']}
codex=items['codex']
assert codex['mcp']=='unverifiable', codex
assert codex['overall']=='unverifiable', codex
PY_SYMLINK
[[ -L "$FAKE_HOME/.codex/config.toml" ]] || { printf 'FAIL: Codex config symlink was replaced\n' >&2; exit 1; }
PRIVATE_AFTER=$(sha256sum "$WORK/private/config.toml")
[[ "$PRIVATE_BEFORE" == "$PRIVATE_AFTER" ]] || { printf 'FAIL: Codex symlink target was changed\n' >&2; exit 1; }

printf 'PASS  Codex status fails closed on a WSL config symlink even when Windows wiring is configured\n'
