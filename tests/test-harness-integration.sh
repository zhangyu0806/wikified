#!/usr/bin/env bash
# Isolated release contract for WSL harness detection/configuration/no-clobber.
set -euo pipefail

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
WORK=$(mktemp -d "${TMPDIR:-/tmp}/wikified-harness.XXXXXX")
trap 'rm -rf -- "$WORK"' EXIT
PASS=0
pass() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }
sha() { sha256sum "$1" | awk '{print $1}'; }
count_backups() { find "$1" -type f -name '*.bak-wikified-*' 2>/dev/null | wc -l | tr -d ' '; }

install_env() {
  local home=$1 bin=$2 state=$3 cache=$4 skills=$5
  shift 5
  env HOME="$home" XDG_STATE_HOME="$state" XDG_CACHE_HOME="$cache" \
    LLM_WIKI_DISABLE_PATH_DETECTION=1 LLM_WIKI_BIN_TARGET="$bin" \
    LLM_WIKI_AGENT_SKILL_ROOT="$skills" LLM_WIKI_SKILL_FANOUT="" "$@"
}

path_detect_env() {
  local home=$1 bin=$2 state=$3 cache=$4 skills=$5 path=$6
  shift 6
  env HOME="$home" XDG_STATE_HOME="$state" XDG_CACHE_HOME="$cache" PATH="$path" \
    LLM_WIKI_DISABLE_PATH_DETECTION=0 LLM_WIKI_CURSOR_BIN="" \
    LLM_WIKI_BIN_TARGET="$bin" LLM_WIKI_AGENT_SKILL_ROOT="$skills" \
    LLM_WIKI_SKILL_FANOUT="" "$@"
}

make_stubs() {
  local home=$1
  mkdir -p "$home/.local/bin" "$home/.opencode/bin"
  cat > "$home/.local/bin/claude" <<'EOF_CLAUDE_STUB'
#!/bin/sh
set -eu
{
  printf 'claude'
  for arg in "$@"; do printf '\t[%s]' "$arg"; done
  printf '\n'
} >> "$HOME/harness-calls.log"
state="$HOME/.stub-claude-mcp"
if [ "${1:-} ${2:-} ${3:-}" = "mcp get llm-wiki" ]; then
  if [ -s "$state" ]; then
    python3 - "$state" <<'PY_CLAUDE_GET'
import pathlib, sys
parts = pathlib.Path(sys.argv[1]).read_text().rstrip('\n').split('\t')
print('llm-wiki:')
print(f'  Command: {parts[0]}')
print(f"  Args: {' '.join(parts[1:])}")
print('  Environment:')
PY_CLAUDE_GET
    exit 0
  fi
  printf 'server not found\n' >&2
  exit 1
fi
if [ "${1:-} ${2:-} ${3:-} ${4:-} ${5:-} ${6:-}" = "mcp add --scope user llm-wiki --" ]; then
  shift 6
  if [ "${LLM_WIKI_STUB_FAIL_PROFILE_ADD:-0}" = 1 ] && [ "${1:-}" = "/usr/bin/env" ]; then
    exit 7
  fi
  printf '%s' "${1:-}" > "$state"
  shift || true
  for arg in "$@"; do printf '\t%s' "$arg" >> "$state"; done
  printf '\n' >> "$state"
  exit 0
fi
if [ "${1:-} ${2:-} ${3:-} ${4:-} ${5:-}" = "mcp remove llm-wiki -s user" ]; then
  rm -f "$state"
  exit 0
fi
exit 2
EOF_CLAUDE_STUB
  cat > "$home/.local/bin/grok" <<'EOF_GROK_STUB'
#!/bin/sh
set -eu
{
  printf 'grok'
  for arg in "$@"; do printf '\t[%s]' "$arg"; done
  printf '\n'
} >> "$HOME/harness-calls.log"
state="$HOME/.stub-grok-mcp"
if [ "${1:-} ${2:-} ${3:-}" = "mcp list --json" ]; then
  if [ -s "$state" ]; then
    python3 - "$state" <<'PY_GROK_JSON'
import json, pathlib, sys
parts = pathlib.Path(sys.argv[1]).read_text().rstrip('\n').split('\t')
print(json.dumps({'servers': {'llm-wiki': {
    'command': parts[0],
    'args': parts[1:],
    'enabled': True,
    'name': 'llm-wiki',
    'scope': 'user',
}}}))
PY_GROK_JSON
  else
    printf '{"servers":{}}\n'
  fi
  exit 0
fi
if [ "${1:-} ${2:-} ${3:-} ${4:-}" = "mcp add llm-wiki --" ]; then
  shift 4
  printf '%s' "${1:-}" > "$state"
  shift || true
  for arg in "$@"; do printf '\t%s' "$arg" >> "$state"; done
  printf '\n' >> "$state"
  exit 0
fi
if [ "${1:-} ${2:-} ${3:-}" = "mcp remove llm-wiki" ]; then
  rm -f "$state"
  exit 0
fi
exit 2
EOF_GROK_STUB
  cat > "$home/.opencode/bin/opencode" <<'EOF_OPENCODE_STUB'
#!/bin/sh
exit 0
EOF_OPENCODE_STUB
  chmod 0755 "$home/.local/bin/claude" "$home/.local/bin/grok" "$home/.opencode/bin/opencode"
}

# A. Fresh install: links only, no optional harness config, and absent is not failure.
A="$WORK/absent"; AH="$A/home"; AB="$A/managed bin"; mkdir -p "$AH"
install_env "$AH" "$AB" "$A/state" "$A/cache" "$A/skills" "$REPO/install.sh" > "$A/install.out"
[[ ! -e "$AH/.claude/settings.json" && ! -e "$AH/.grok/config.toml" && ! -e "$AH/.config/opencode/opencode.json" ]] \
  || fail 'normal install wrote optional harness configuration'
[[ -L "$AH/.config/opencode/plugins/llm-wiki-recall.js" ]] || fail 'normal install missed official OpenCode plugin link'
install_env "$AH" "$AB" "$A/state" "$A/cache" "$A/skills" "$AB/llm-wiki-harness" status --strict >/dev/null
install_env "$AH" "$AB" "$A/state" "$A/cache" "$A/skills" "$REPO/install.sh" --check >/dev/null
pass 'fresh install creates managed links only and absent harnesses pass strict check'

install_env "$AH" "$AB" "$A/state" "$A/cache" "$A/skills" "$AB/llm-wiki-harness" status --json > "$A/status.json"
python3 - "$A/status.json" <<'PY_ABSENT_MATRIX'
import json, sys
with open(sys.argv[1], encoding='utf-8') as handle:
    data=json.load(handle)
assert len(data['harnesses']) == 5
assert all(item['overall']=='absent' for item in data['harnesses'])
assert all('command' not in item and 'path' not in item for item in data['harnesses'])
PY_ABSENT_MATRIX
pass 'status JSON reports a five-harness absent matrix without config values'

# A2. A user-owned primary skill path must not become the source of fanout links.
A2="$WORK/skill-fanout"; A2H="$A2/home"; A2B="$A2/bin"; A2S="$A2/skills"; A2F="$A2/fanout"
mkdir -p "$A2H" "$A2S/quick-note" "$A2F"
printf 'user-owned primary skill\n' > "$A2S/quick-note/KEEP"
set +e
install_env "$A2H" "$A2B" "$A2/state" "$A2/cache" "$A2S" \
  env LLM_WIKI_SKILL_FANOUT="$A2F" "$REPO/install.sh" > "$A2/out" 2> "$A2/err"
rc=$?
set -e
[[ $rc -eq 1 ]] || fail 'user-owned primary skill should produce a safe-stop result'
[[ -f "$A2S/quick-note/KEEP" ]] || fail 'user-owned primary skill was changed'
[[ -L "$A2F/quick-note" ]] || fail 'fanout skill link was not created'
[[ $(readlink -f "$A2F/quick-note") == $(readlink -f "$REPO/skills/quick-note") ]] \
  || fail 'fanout inherited the user-owned primary skill path'
pass 'skill fanout never propagates a preserved user-owned primary path'

# A3. Claude Code 2.1.220 reports an absent server as "No MCP server named
#     \"llm-wiki\"...". It must remain an addable installed-unconfigured state.
A3="$WORK/claude-2.1.220"; A3H="$A3/home"; A3B="$A3/bin"
mkdir -p "$A3H/.local/bin"
cat > "$A3H/.local/bin/claude" <<'EOF_CLAUDE_21220_STUB'
#!/bin/sh
set -eu
{
  printf 'claude'
  for arg in "$@"; do printf '\t[%s]' "$arg"; done
  printf '\n'
} >> "$HOME/harness-calls.log"
state="$HOME/.stub-claude-mcp"
if [ "${1:-} ${2:-} ${3:-}" = "mcp get llm-wiki" ]; then
  if [ -s "$state" ]; then printf 'llm-wiki Command: %s\n' "$(cat "$state")"; exit 0; fi
  printf 'No MCP server named "llm-wiki". Run `claude mcp add` to add one.\n' >&2
  exit 1
fi
if [ "${1:-} ${2:-} ${3:-} ${4:-} ${5:-} ${6:-}" = "mcp add --scope user llm-wiki --" ]; then
  shift 6
  printf '%s' "${1:-}" > "$state"
  shift || true
  for arg in "$@"; do printf '\t%s' "$arg" >> "$state"; done
  printf '\n' >> "$state"
  exit 0
fi
exit 2
EOF_CLAUDE_21220_STUB
chmod 0755 "$A3H/.local/bin/claude"
install_env "$A3H" "$A3B" "$A3/state" "$A3/cache" "$A3/skills" "$REPO/install.sh" > "$A3/install.out"
install_env "$A3H" "$A3B" "$A3/state" "$A3/cache" "$A3/skills" \
  "$A3B/llm-wiki-harness" status --json > "$A3/status-before.json"
python3 - "$A3/status-before.json" <<'PY_CLAUDE_21220_ABSENT'
import json,sys
items={item['harness']:item for item in json.load(open(sys.argv[1], encoding='utf-8'))['harnesses']}
claude=items['claude']
assert claude['executable']=='home-local-bin', claude
assert claude['mcp']=='installed-unconfigured', claude
assert claude['overall']=='installed-unconfigured', claude
PY_CLAUDE_21220_ABSENT
pass 'Claude Code 2.1.220 named-server absence is parsed as installed-unconfigured'

install_env "$A3H" "$A3B" "$A3/state" "$A3/cache" "$A3/skills" \
  "$A3B/llm-wiki-harness" configure --harness claude > "$A3/configure-first.out"
[[ -s "$A3H/.stub-claude-mcp" ]] || fail 'Claude 2.1.220 absence did not permit MCP add'
[[ -f "$A3H/.claude/settings.json" && -f "$A3H/.claude/CLAUDE.md" ]] \
  || fail 'Claude 2.1.220 configuration missed managed hook/rules files'
install_env "$A3H" "$A3B" "$A3/state" "$A3/cache" "$A3/skills" \
  "$A3B/llm-wiki-harness" status --json > "$A3/status-after.json"
python3 - "$A3/status-after.json" <<'PY_CLAUDE_21220_CONFIGURED'
import json,sys
items={item['harness']:item for item in json.load(open(sys.argv[1], encoding='utf-8'))['harnesses']}
assert items['claude']['overall']=='configured', items['claude']
PY_CLAUDE_21220_CONFIGURED
install_env "$A3H" "$A3B" "$A3/state" "$A3/cache" "$A3/skills" \
  "$A3B/llm-wiki-harness" configure --harness claude > "$A3/configure-second.out"
[[ $(grep -Fc $'claude\t[mcp]\t[add]' "$A3H/harness-calls.log") -eq 1 ]] \
  || fail 'Claude 2.1.220 MCP add repeated after successful verification'
grep -Fq 'configuration complete: changes=0, failures=0' "$A3/configure-second.out" \
  || fail 'Claude 2.1.220 repeat configuration was not idempotent'
pass 'Claude Code 2.1.220 absence permits exact user-scope add and idempotent verification'

# A4. Upgrade exact legacy managed Skill links, then remain idempotent.
A4="$WORK/legacy-skill-upgrade"; A4H="$A4/home"; A4B="$A4/bin"; A4S="$A4/skills"
mkdir -p "$A4H" "$A4S"
for legacy in QuickNote SessionCapture WikiCompiler; do
  ln -s "$REPO/skills/$legacy" "$A4S/$legacy"
done
install_env "$A4H" "$A4B" "$A4/state" "$A4/cache" "$A4S" \
  "$REPO/install.sh" > "$A4/first.out"
for legacy in QuickNote SessionCapture WikiCompiler; do
  [[ ! -e "$A4S/$legacy" && ! -L "$A4S/$legacy" ]] \
    || fail "legacy managed Skill link survived upgrade: $legacy"
done
for current in quick-note session-capture wiki-compiler; do
  [[ -L "$A4S/$current" ]] || fail "lowercase Skill link missing after upgrade: $current"
  [[ $(readlink -f "$A4S/$current") == $(readlink -f "$REPO/skills/$current") ]] \
    || fail "lowercase Skill link points elsewhere after upgrade: $current"
done
[[ $(grep -Fc 'removed legacy managed skill symlink' "$A4/first.out") -eq 3 ]] \
  || fail 'upgrade did not report all three legacy managed Skill removals'
pass 'fresh upgrade removes only exact broken managed Skill links and installs lowercase links'

first_skill_tree=$(find "$A4S" -mindepth 1 -maxdepth 1 -printf '%f|%y|%l\n' | sort)
install_env "$A4H" "$A4B" "$A4/state" "$A4/cache" "$A4S" \
  "$REPO/install.sh" > "$A4/second.out"
second_skill_tree=$(find "$A4S" -mindepth 1 -maxdepth 1 -printf '%f|%y|%l\n' | sort)
[[ "$first_skill_tree" == "$second_skill_tree" ]] || fail 'repeat upgrade changed the Skill tree'
grep -Fq '✓ completed (changes=0).' "$A4/second.out" \
  || fail 'repeat legacy Skill upgrade was not a no-op'
pass 'legacy managed Skill migration is idempotent'

# A5. Dry-run and check diagnose exact managed legacy links without mutation.
A5="$WORK/legacy-skill-modes"; A5H="$A5/home"; A5B="$A5/bin"; A5S="$A5/skills"
mkdir -p "$A5H" "$A5S"
for legacy in QuickNote SessionCapture WikiCompiler; do
  ln -s "$REPO/skills/$legacy" "$A5S/$legacy"
done
legacy_tree_before=$(find "$A5S" -mindepth 1 -maxdepth 1 -printf '%f|%y|%l\n' | sort)
install_env "$A5H" "$A5B" "$A5/state" "$A5/cache" "$A5S" \
  "$REPO/install.sh" --dry-run > "$A5/dry-run.out"
legacy_tree_after_dry=$(find "$A5S" -mindepth 1 -maxdepth 1 -printf '%f|%y|%l\n' | sort)
[[ "$legacy_tree_before" == "$legacy_tree_after_dry" ]] || fail 'dry-run changed legacy Skill links'
for current in quick-note session-capture wiki-compiler; do
  [[ ! -e "$A5S/$current" && ! -L "$A5S/$current" ]] \
    || fail "dry-run created lowercase Skill link: $current"
done
[[ $(grep -Fc 'would remove legacy managed skill symlink' "$A5/dry-run.out") -eq 3 ]] \
  || fail 'dry-run did not report all legacy managed Skill removals'
pass 'legacy Skill dry-run reports migration and writes nothing'

set +e
install_env "$A5H" "$A5B" "$A5/state" "$A5/cache" "$A5S" \
  "$REPO/install.sh" --check > "$A5/check-before.out" 2> "$A5/check-before.err"
rc=$?
set -e
[[ $rc -eq 1 ]] || fail 'check unexpectedly accepted unmigrated legacy managed Skill links'
legacy_tree_after_check=$(find "$A5S" -mindepth 1 -maxdepth 1 -printf '%f|%y|%l\n' | sort)
[[ "$legacy_tree_before" == "$legacy_tree_after_check" ]] || fail 'check changed legacy Skill links'
[[ $(grep -Fc 'legacy managed skill symlink requires migration' "$A5/check-before.out") -eq 3 ]] \
  || fail 'check did not diagnose all legacy managed Skill links'
install_env "$A5H" "$A5B" "$A5/state" "$A5/cache" "$A5S" "$REPO/install.sh" > "$A5/install.out"
install_env "$A5H" "$A5B" "$A5/state" "$A5/cache" "$A5S" "$REPO/install.sh" --check > "$A5/check-after.out"
pass 'legacy Skill check fails read-only before migration and passes after upgrade'

# A6. User-owned legacy files, directories and off-repo symlinks are never removed.
A6="$WORK/legacy-skill-no-clobber"; A6H="$A6/home"; A6B="$A6/bin"; A6S="$A6/skills"
mkdir -p "$A6H" "$A6S/SessionCapture" "$A6/user-target"
printf 'user QuickNote\n' > "$A6S/QuickNote"
printf 'keep directory\n' > "$A6S/SessionCapture/KEEP"
printf 'user WikiCompiler target\n' > "$A6/user-target/WikiCompiler"
ln -s "$A6/user-target/WikiCompiler" "$A6S/WikiCompiler"
set +e
install_env "$A6H" "$A6B" "$A6/state" "$A6/cache" "$A6S" \
  "$REPO/install.sh" > "$A6/install.out" 2> "$A6/install.err"
rc=$?
set -e
[[ $rc -eq 1 ]] || fail 'user-owned legacy Skill paths did not produce a safe-stop result'
[[ $(cat "$A6S/QuickNote") == 'user QuickNote' ]] || fail 'user-owned legacy Skill file changed'
[[ $(cat "$A6S/SessionCapture/KEEP") == 'keep directory' ]] || fail 'user-owned legacy Skill directory changed'
[[ -L "$A6S/WikiCompiler" && $(readlink "$A6S/WikiCompiler") == "$A6/user-target/WikiCompiler" ]] \
  || fail 'off-repo legacy Skill symlink changed'
for current in quick-note session-capture wiki-compiler; do
  [[ -L "$A6S/$current" ]] || fail "safe-stop failed to install independent lowercase Skill link: $current"
done
grep -Fq "user-owned legacy skill path left unchanged: $A6S/QuickNote" "$A6/install.out" \
  || fail 'legacy Skill file diagnosis missing'
grep -Fq "user-owned legacy skill path left unchanged: $A6S/SessionCapture" "$A6/install.out" \
  || fail 'legacy Skill directory diagnosis missing'
grep -Fq "legacy skill symlink is not managed by this repo and was left unchanged: $A6S/WikiCompiler" "$A6/install.out" \
  || fail 'off-repo legacy Skill symlink diagnosis missing'
pass 'legacy Skill no-clobber preserves and diagnoses files, directories and off-repo symlinks'

# B. Private-bin detection, safe merge, exact CLI shape and idempotency.
B="$WORK/configured"; BH="$B/home"; BB="$B/managed bin"; mkdir -p "$BH/.claude" "$BH/.config/opencode" "$BH/.grok/hooks"
make_stubs "$BH"
cat > "$BH/.claude/settings.json" <<'EOF_CLAUDE_SETTINGS'
{"permissions":{"allow":["Read"]}}
EOF_CLAUDE_SETTINGS
chmod 0600 "$BH/.claude/settings.json"
printf '# user Claude rules\n' > "$BH/.claude/CLAUDE.md"
cat > "$BH/.config/opencode/opencode.jsonc" <<'EOF_OPENCODE_JSONC'
{
  // user comment must survive
  "theme": "user-theme",
  "mcp": {
    "other": {"type": "local", "command": ["true"]},
  },
}
EOF_OPENCODE_JSONC
printf '# user OpenCode rules\n' > "$BH/.config/opencode/AGENTS.md"
cat > "$BH/.grok/hooks/existing.json" <<'EOF_GROK_EXISTING'
{"hooks":{"AfterToolUse":[{"hooks":[{"type":"command","command":"true"}]}]}}
EOF_GROK_EXISTING
printf '# user Grok rules\n' > "$BH/.grok/AGENTS.md"

install_env "$BH" "$BB" "$B/state" "$B/cache" "$B/skills" "$REPO/install.sh" --configure-harnesses > "$B/first.out" 2> "$B/first.err"
EXPECTED_MCP="$BB/llm-wiki-mcp"
printf -v EXPECTED_CLAUDE 'claude\t[mcp]\t[add]\t[--scope]\t[user]\t[llm-wiki]\t[--]\t[/usr/bin/env]\t[LLM_WIKI_AGENT_PROFILE=claude]\t[LLM_WIKI_DOMAIN=work]\t[%s]' "$EXPECTED_MCP"
printf -v EXPECTED_GROK 'grok\t[mcp]\t[add]\t[llm-wiki]\t[--]\t[/usr/bin/env]\t[LLM_WIKI_AGENT_PROFILE=grok]\t[LLM_WIKI_DOMAIN=work]\t[%s]' "$EXPECTED_MCP"
grep -Fxq "$EXPECTED_CLAUDE" "$BH/harness-calls.log" \
  || fail 'Claude MCP add argv differs from approved user-scope shape'
grep -Fxq "$EXPECTED_GROK" "$BH/harness-calls.log" \
  || fail 'Grok MCP add argv differs from approved shape'
pass 'Claude and Grok MCP mutations use exact native CLI argument shapes'

grep -Fq '// user comment must survive' "$BH/.config/opencode/opencode.jsonc" || fail 'JSONC comment was lost'
grep -Fq '"theme": "user-theme"' "$BH/.config/opencode/opencode.jsonc" || fail 'OpenCode user field was lost'
grep -Fq '"other"' "$BH/.config/opencode/opencode.jsonc" || fail 'OpenCode unrelated MCP was lost'
grep -Fq '"permissions"' "$BH/.claude/settings.json" || fail 'Claude user setting was lost'
grep -Fq '# user Claude rules' "$BH/.claude/CLAUDE.md" || fail 'Claude user instructions were lost'
grep -Fq '# user OpenCode rules' "$BH/.config/opencode/AGENTS.md" || fail 'OpenCode user instructions were lost'
grep -Fq '# user Grok rules' "$BH/.grok/AGENTS.md" || fail 'Grok user instructions were lost'
pass 'structural merge preserves JSONC comments, trailing-comma document and unrelated user content'

python3 - "$BH" "$BB" <<'PY_DYNAMIC_HOOKS'
import json, pathlib, sys
home=pathlib.Path(sys.argv[1]); managed=pathlib.Path(sys.argv[2])
claude=json.loads((home/'.claude/settings.json').read_text())
cmd=claude['hooks']['SessionStart'][0]['hooks'][0]['command']
assert str(managed/'llm-wiki-session-start') in cmd and '--format claude' in cmd and '--max-chars 2500' in cmd
assert '/usr/bin/env' in cmd and 'LLM_WIKI_AGENT_PROFILE=claude' in cmd and 'LLM_WIKI_DOMAIN=work' in cmd
grok=json.loads((home/'.grok/hooks/llm-wiki.json').read_text())
cmd=grok['hooks']['SessionStart'][0]['hooks'][0]['command']
assert str(managed/'llm-wiki-session-start') in cmd and '--format grok-probe' in cmd
assert '/usr/bin/env' in cmd and 'LLM_WIKI_AGENT_PROFILE=grok' in cmd and 'LLM_WIKI_DOMAIN=work' in cmd
PY_DYNAMIC_HOOKS
pass 'generated hooks use the actual managed bin target, not a hard-coded home path'

python3 - "$REPO/bin/llm-wiki-harness" "$BH/.config/opencode/opencode.jsonc" "$EXPECTED_MCP" <<'PY_OPENCODE_PROFILE'
import runpy,sys
scope=runpy.run_path(sys.argv[1])
obj=scope['parse_jsonc'](open(sys.argv[2], encoding='utf-8').read()).value
command=obj['mcp']['llm-wiki']['command']
assert command == [
    '/usr/bin/env',
    'LLM_WIKI_AGENT_PROFILE=opencode',
    'LLM_WIKI_DOMAIN=work',
    'LLM_WIKI_TARGET_AGENTS=codex,opencode',
    sys.argv[3],
], command
assert scope['profiled_mcp_command']('codex', sys.argv[3]) == [
    '/usr/bin/env',
    'LLM_WIKI_AGENT_PROFILE=codex',
    'LLM_WIKI_DOMAIN=work',
    'LLM_WIKI_TARGET_AGENTS=codex,opencode',
    sys.argv[3],
]
matches=scope['managed_fragment_matches']
assert matches('LLM_WIKI_AGENT_PROFILE=codex ', 'LLM_WIKI_AGENT_PROFILE=codex')
assert not matches('LLM_WIKI_AGENT_PROFILE=codex-other ', 'LLM_WIKI_AGENT_PROFILE=codex')
PY_OPENCODE_PROFILE
pass 'Codex/OpenCode MCP commands fix work profiles and shared promotion targets outside model input'

for skill in quick-note session-capture wiki-compiler; do
  [[ -L "$B/skills/$skill" ]] || fail "missing lowercase skill link: $skill"
  grep -Fxq "name: $skill" "$REPO/skills/$skill/SKILL.md" || fail "skill frontmatter does not match directory: $skill"
done
[[ ! -d "$REPO/skills/QuickNote" && ! -d "$REPO/skills/SessionCapture" && ! -d "$REPO/skills/WikiCompiler" ]] \
  || fail 'legacy uppercase skill directories remain'
! grep -Eqi 'automatically invoke|自动调用.*wiki-compiler|立即调用 WikiCompiler' "$REPO/skills/session-capture/SKILL.md" \
  || fail 'session capture still auto-promotes raw content'
grep -Fqi 'explicit human' "$REPO/skills/wiki-compiler/SKILL.md" || fail 'wiki compiler lacks explicit human approval gate'
pass 'skills are cross-harness discoverable and preserve raw-to-human-review-to-wiki boundary'

install_env "$BH" "$BB" "$B/state" "$B/cache" "$B/skills" "$BB/llm-wiki-harness" status --json > "$B/status.json"
python3 - "$B/status.json" <<'PY_CONFIGURED_MATRIX'
import json, sys
with open(sys.argv[1], encoding='utf-8') as handle:
    items={x['harness']:x for x in json.load(handle)['harnesses']}
for name in ('claude','opencode','grok'):
    assert items[name]['overall'] == 'configured', items[name]
assert items['opencode']['executable'] == 'private-bin'
assert items['claude']['executable'] == 'home-local-bin'
assert items['grok']['executable'] == 'home-local-bin'
assert items['codex']['overall'] == 'absent'
assert items['cursor']['overall'] == 'absent'
PY_CONFIGURED_MATRIX
pass 'non-interactive detection finds private WSL binaries and reports configured matrix'

FILES=(
  "$BH/.claude/settings.json" "$BH/.claude/CLAUDE.md"
  "$BH/.config/opencode/opencode.jsonc" "$BH/.config/opencode/AGENTS.md"
  "$BH/.grok/hooks/llm-wiki.json" "$BH/.grok/AGENTS.md"
)
before_hashes=$(for f in "${FILES[@]}"; do sha "$f"; done)
before_backups=$(count_backups "$BH")
install_env "$BH" "$BB" "$B/state" "$B/cache" "$B/skills" "$REPO/install.sh" --configure-harnesses > "$B/second.out" 2> "$B/second.err"
after_hashes=$(for f in "${FILES[@]}"; do sha "$f"; done)
after_backups=$(count_backups "$BH")
[[ "$before_hashes" == "$after_hashes" ]] || fail 'second configuration changed managed/user files'
[[ "$before_backups" == "$after_backups" ]] || fail 'second configuration created redundant backups'
[[ $(grep -Fc $'claude\t[mcp]\t[add]' "$BH/harness-calls.log") -eq 1 ]] || fail 'Claude MCP add repeated'
[[ $(grep -Fc $'grok\t[mcp]\t[add]' "$BH/harness-calls.log") -eq 1 ]] || fail 'Grok MCP add repeated'
grep -Fq 'configuration complete: changes=0, failures=0' "$B/second.out" || fail 'second configuration was not a reported no-op'
install_env "$BH" "$BB" "$B/state" "$B/cache" "$B/skills" "$REPO/install.sh" --check >/dev/null
pass 'second configuration is byte-stable, backup-stable and does not repeat MCP add'

# B2. Exact configs emitted by the previous release migrate to fixed profiles.
#     Every native/file mutation is recoverable and the upgraded state is
#     idempotent. Near-matches remain covered by the conflict case below.
B2="$WORK/legacy-profile-upgrade"; B2H="$B2/home"; B2B="$B2/managed bin"
mkdir -p "$B2H/.claude" "$B2H/.config/opencode" "$B2H/.grok/hooks"
make_stubs "$B2H"
install_env "$B2H" "$B2B" "$B2/state" "$B2/cache" "$B2/skills" "$REPO/install.sh" >/dev/null
LEGACY_MCP="$B2B/llm-wiki-mcp"
LEGACY_ADAPTER="$B2B/llm-wiki-session-start"
printf '%s\n' "$LEGACY_MCP" > "$B2H/.stub-claude-mcp"
printf '%s\n' "$LEGACY_MCP" > "$B2H/.stub-grok-mcp"
cat > "$B2H/.claude/settings.json" <<EOF_LEGACY_CLAUDE
{
  "permissions": {"allow": ["Read"]},
  "hooks": {
    "SessionStart": [
      {
        "matcher": "^(startup|resume|clear|compact)$",
        "hooks": [
          {
            "type": "command",
            "command": "'$LEGACY_ADAPTER' --format claude --max-chars 2500",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
EOF_LEGACY_CLAUDE
cat > "$B2H/.config/opencode/opencode.jsonc" <<EOF_LEGACY_OPENCODE
{
  // unrelated user material must remain byte-near and semantically intact
  "theme": "legacy-user-theme",
  "mcp": {
    "other": {"type": "local", "command": ["true"]},
    "llm-wiki": {
      "type": "local",
      "command": ["$LEGACY_MCP"],
      "enabled": true,
      "timeout": 5000
    },
  },
}
EOF_LEGACY_OPENCODE
cat > "$B2H/.grok/hooks/llm-wiki.json" <<EOF_LEGACY_GROK
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "'$LEGACY_ADAPTER' --format grok-probe --max-chars 2500",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
EOF_LEGACY_GROK
claude_legacy_hash=$(sha "$B2H/.claude/settings.json")
opencode_legacy_hash=$(sha "$B2H/.config/opencode/opencode.jsonc")
grok_legacy_hash=$(sha "$B2H/.grok/hooks/llm-wiki.json")

install_env "$B2H" "$B2B" "$B2/state" "$B2/cache" "$B2/skills" \
  "$B2B/llm-wiki-harness" configure \
  --harness claude --harness opencode --harness grok > "$B2/upgrade.out" 2> "$B2/upgrade.err"

python3 - "$REPO/bin/llm-wiki-harness" "$B2H" "$B2B" <<'PY_LEGACY_UPGRADE'
import json, pathlib, runpy, sys
scope = runpy.run_path(sys.argv[1])
home = pathlib.Path(sys.argv[2])
managed = pathlib.Path(sys.argv[3])
expected = str(managed / 'llm-wiki-mcp')
adapter = str(managed / 'llm-wiki-session-start')

claude = json.loads((home / '.claude/settings.json').read_text())
claude_command = claude['hooks']['SessionStart'][0]['hooks'][0]['command']
assert adapter in claude_command
assert 'LLM_WIKI_AGENT_PROFILE=claude' in claude_command
assert 'LLM_WIKI_DOMAIN=work' in claude_command
assert claude['permissions'] == {'allow': ['Read']}

opencode_text = (home / '.config/opencode/opencode.jsonc').read_text()
opencode = scope['parse_jsonc'](opencode_text).value
assert '// unrelated user material must remain' in opencode_text
assert opencode['theme'] == 'legacy-user-theme'
assert opencode['mcp']['other'] == {'type': 'local', 'command': ['true']}
assert opencode['mcp']['llm-wiki']['command'] == scope['profiled_mcp_command']('opencode', expected)

grok = json.loads((home / '.grok/hooks/llm-wiki.json').read_text())
grok_command = grok['hooks']['SessionStart'][0]['hooks'][0]['command']
assert adapter in grok_command
assert 'LLM_WIKI_AGENT_PROFILE=grok' in grok_command
assert 'LLM_WIKI_DOMAIN=work' in grok_command

for state_name, profile in (('.stub-claude-mcp', 'claude'), ('.stub-grok-mcp', 'grok')):
    parts = (home / state_name).read_text().rstrip('\n').split('\t')
    assert parts == scope['profiled_mcp_command'](profile, expected), (profile, parts)

# Codex's previous cross-OS hook is recognized only as one exact managed
# shape; its unprofiled commandWindows member cannot satisfy current status.
template = scope['template_json'](pathlib.Path(sys.argv[1]).parents[1] / 'templates/codex/hooks.json')
current = scope['set_nested_hook_command'](template['hooks']['SessionStart'][0], 'codex', 'codex')
legacy = scope['legacy_hook_groups'](current, 'codex')
assert len(legacy) == 2
assert all('LLM_WIKI_' not in item['hooks'][0]['command'] for item in legacy)
assert all('LLM_WIKI_' not in item['hooks'][0]['commandWindows'] for item in legacy)
assert not scope['managed_hooks_match'](
    legacy[0],
    ('llm-wiki-session-start', '--format codex', '2500', *scope['profile_environment']('codex')),
)
PY_LEGACY_UPGRADE
pass 'exact legacy MCP and SessionStart shapes upgrade to fixed profiles without losing unrelated content'

for spec in \
  "$B2H/.claude/settings.json:$claude_legacy_hash" \
  "$B2H/.config/opencode/opencode.jsonc:$opencode_legacy_hash" \
  "$B2H/.grok/hooks/llm-wiki.json:$grok_legacy_hash"; do
  current=${spec%%:*}; wanted=${spec#*:}
  backup=$(find "$(dirname "$current")" -maxdepth 1 \
    -name "$(basename "$current").bak-wikified-before-merge-*" -print -quit)
  [[ -n "$backup" && $(sha "$backup") == "$wanted" ]] \
    || fail "legacy file migration backup missing/mismatched: $current"
done
recovery_count=$(find "$B2/state/llm-wiki/harness/backups" -type f -name '*-mcp-legacy-*.json' | wc -l | tr -d ' ')
[[ "$recovery_count" -eq 2 ]] || fail 'native legacy MCP migrations did not create exactly two recovery records'
python3 - "$B2/state/llm-wiki/harness/backups" "$LEGACY_MCP" <<'PY_NATIVE_RECOVERY'
import json, pathlib, sys
items = list(pathlib.Path(sys.argv[1]).glob('*-mcp-legacy-*.json'))
assert {json.loads(path.read_text())['harness'] for path in items} == {'claude', 'grok'}
for path in items:
    data = json.loads(path.read_text())
    assert data['schema_version'] == 'llm-wiki-harness-recovery/v1'
    assert data['name'] == 'llm-wiki'
    assert data['command'] == [sys.argv[2]]
PY_NATIVE_RECOVERY
grep -Fxq $'claude\t[mcp]\t[remove]\t[llm-wiki]\t[-s]\t[user]' "$B2H/harness-calls.log" \
  || fail 'Claude legacy MCP was not removed with the exact user-scope argv'
grep -Fxq $'grok\t[mcp]\t[remove]\t[llm-wiki]' "$B2H/harness-calls.log" \
  || fail 'Grok legacy MCP was not removed with the exact argv'
pass 'legacy migrations create byte backups and native recovery records before replacement'

B2_FILES=(
  "$B2H/.claude/settings.json" "$B2H/.claude/CLAUDE.md"
  "$B2H/.config/opencode/opencode.jsonc" "$B2H/.config/opencode/AGENTS.md"
  "$B2H/.grok/hooks/llm-wiki.json" "$B2H/.grok/AGENTS.md"
)
b2_hashes_before=$(for f in "${B2_FILES[@]}"; do sha "$f"; done)
b2_backups_before=$(count_backups "$B2H")
b2_calls_before=$(sha "$B2H/harness-calls.log")
install_env "$B2H" "$B2B" "$B2/state" "$B2/cache" "$B2/skills" \
  "$B2B/llm-wiki-harness" configure \
  --harness claude --harness opencode --harness grok > "$B2/second.out" 2> "$B2/second.err"
b2_hashes_after=$(for f in "${B2_FILES[@]}"; do sha "$f"; done)
[[ "$b2_hashes_before" == "$b2_hashes_after" ]] || fail 'second legacy-upgrade run changed managed/user files'
[[ "$b2_backups_before" == $(count_backups "$B2H") ]] || fail 'second legacy-upgrade run created file backups'
[[ "$recovery_count" -eq $(find "$B2/state/llm-wiki/harness/backups" -type f -name '*-mcp-legacy-*.json' | wc -l | tr -d ' ') ]] \
  || fail 'second legacy-upgrade run created native recovery records'
[[ "$b2_calls_before" != $(sha "$B2H/harness-calls.log") ]] \
  || fail 'second legacy-upgrade run did not perform read-only native verification'
[[ $(grep -Fc $'claude\t[mcp]\t[remove]' "$B2H/harness-calls.log") -eq 1 ]] \
  || fail 'Claude native legacy migration repeated'
[[ $(grep -Fc $'grok\t[mcp]\t[remove]' "$B2H/harness-calls.log") -eq 1 ]] \
  || fail 'Grok native legacy migration repeated'
grep -Fq 'configuration complete: changes=0, failures=0' "$B2/second.out" \
  || fail 'second legacy-upgrade configuration was not a no-op'
pass 'legacy-to-profile migration is byte/backup stable and native mutation is not repeated'

# B3. A failed profiled native add rolls back to the exact legacy command.
B3="$WORK/legacy-profile-rollback"; B3H="$B3/home"; B3B="$B3/bin"
mkdir -p "$B3H"
make_stubs "$B3H"
install_env "$B3H" "$B3B" "$B3/state" "$B3/cache" "$B3/skills" "$REPO/install.sh" >/dev/null
printf '%s\n' "$B3B/llm-wiki-mcp" > "$B3H/.stub-claude-mcp"
set +e
install_env "$B3H" "$B3B" "$B3/state" "$B3/cache" "$B3/skills" \
  env LLM_WIKI_STUB_FAIL_PROFILE_ADD=1 \
  "$B3B/llm-wiki-harness" configure --harness claude > "$B3/out" 2> "$B3/err"
rc=$?
set -e
[[ $rc -eq 1 ]] || fail 'failed profiled native replacement did not fail closed'
[[ $(cat "$B3H/.stub-claude-mcp") == "$B3B/llm-wiki-mcp" ]] \
  || fail 'failed profiled native replacement did not restore the legacy command'
[[ ! -e "$B3H/.claude/settings.json" && ! -e "$B3H/.claude/CLAUDE.md" ]] \
  || fail 'file configuration ran after native legacy migration failure'
[[ $(grep -Fc $'claude\t[mcp]\t[remove]' "$B3H/harness-calls.log") -eq 2 ]] \
  || fail 'native rollback did not make the expected remove/cleanup attempts'
[[ $(grep -Fc $'claude\t[mcp]\t[add]' "$B3H/harness-calls.log") -eq 2 ]] \
  || fail 'native rollback did not attempt profiled replacement then legacy restore'
[[ $(find "$B3/state/llm-wiki/harness/backups" -type f -name 'claude-mcp-legacy-*.json' | wc -l | tr -d ' ') -eq 1 ]] \
  || fail 'failed native migration did not retain one recovery record'
grep -Fq 'legacy command restored' "$B3/err" \
  || fail 'failed native migration did not report successful rollback'
pass 'failed profiled native migration restores legacy state and blocks later file mutations'

# C. Malformed JSONC is unchanged, snapshotted and reported unverifiable.
C="$WORK/malformed"; CH="$C/home"; CB="$C/bin"; mkdir -p "$CH/.opencode/bin" "$CH/.config/opencode"
printf '#!/bin/sh\nexit 0\n' > "$CH/.opencode/bin/opencode"; chmod 0755 "$CH/.opencode/bin/opencode"
install_env "$CH" "$CB" "$C/state" "$C/cache" "$C/skills" "$REPO/install.sh" >/dev/null
printf '{ "mcp": { // broken\n' > "$CH/.config/opencode/opencode.jsonc"
malformed_hash=$(sha "$CH/.config/opencode/opencode.jsonc")
set +e
install_env "$CH" "$CB" "$C/state" "$C/cache" "$C/skills" "$CB/llm-wiki-harness" configure --harness opencode > "$C/out" 2> "$C/err"
rc=$?
set -e
[[ $rc -eq 1 ]] || fail 'malformed config unexpectedly configured'
[[ $(sha "$CH/.config/opencode/opencode.jsonc") == "$malformed_hash" ]] || fail 'malformed config was modified'
backup=$(find "$CH/.config/opencode" -maxdepth 1 -name 'opencode.jsonc.bak-wikified-malformed-*' -print -quit)
[[ -n "$backup" && $(sha "$backup") == "$malformed_hash" ]] || fail 'malformed config recovery snapshot missing/mismatched'
grep -Fq 'malformed config left unchanged' "$C/err" || fail 'malformed config error lacks recovery guidance'
pass 'malformed JSONC fails closed, remains byte-identical and gets a recovery snapshot'

# D. Existing user-owned llm-wiki entry conflicts instead of being overwritten.
D="$WORK/conflict"; DH="$D/home"; DB="$D/bin"; mkdir -p "$DH/.opencode/bin" "$DH/.config/opencode"
printf '#!/bin/sh\nexit 0\n' > "$DH/.opencode/bin/opencode"; chmod 0755 "$DH/.opencode/bin/opencode"
install_env "$DH" "$DB" "$D/state" "$D/cache" "$D/skills" "$REPO/install.sh" >/dev/null
cat > "$DH/.config/opencode/opencode.json" <<EOF_CONFLICT_JSON
{"mcp":{"llm-wiki":{"type":"local","command":["$DB/llm-wiki-mcp"],"enabled":true,"timeout":5000,"owner":"user"}},"theme":"keep"}
EOF_CONFLICT_JSON
conflict_hash=$(sha "$DH/.config/opencode/opencode.json")
set +e
install_env "$DH" "$DB" "$D/state" "$D/cache" "$D/skills" "$DB/llm-wiki-harness" configure --harness opencode > "$D/out" 2> "$D/err"
rc=$?
set -e
[[ $rc -eq 1 ]] || fail 'conflicting llm-wiki entry unexpectedly overwritten'
[[ $(sha "$DH/.config/opencode/opencode.json") == "$conflict_hash" ]] || fail 'conflicting config changed'
backup=$(find "$DH/.config/opencode" -maxdepth 1 -name 'opencode.json.bak-wikified-conflict-*' -print -quit)
[[ -n "$backup" && $(sha "$backup") == "$conflict_hash" ]] || fail 'conflict recovery snapshot missing/mismatched'
grep -Fq 'existing mcp.llm-wiki differs' "$D/err" || fail 'conflict error is not specific'
pass 'user-owned MCP entry is preserved and conflict is recoverable'

# E. Current-path stale link and legacy link are diagnosed, never replaced; Grok still configures.
E="$WORK/stale"; EH="$E/home"; EB="$E/bin"; mkdir -p "$EH/.opencode/bin" "$EH/.local/bin" "$EH/.config/opencode/plugins" "$EH/.opencode/plugins" "$E/private-data"
printf '#!/bin/sh\nexit 0\n' > "$EH/.opencode/bin/opencode"; chmod 0755 "$EH/.opencode/bin/opencode"
cat > "$EH/.local/bin/grok" <<'EOF_GROK_ONLY_STUB'
#!/bin/sh
set -eu
state="$HOME/.stub-grok-mcp"
if [ "${1:-} ${2:-} ${3:-}" = "mcp list --json" ]; then
  if [ -s "$state" ]; then
    python3 - "$state" <<'PY_GROK_ONLY_JSON'
import json, pathlib, sys
parts = pathlib.Path(sys.argv[1]).read_text().rstrip('\n').split('\t')
print(json.dumps({'servers': {'llm-wiki': {
    'command': parts[0],
    'args': parts[1:],
    'enabled': True,
    'name': 'llm-wiki',
    'scope': 'user',
}}}))
PY_GROK_ONLY_JSON
  else
    printf '{"servers":{}}\n'
  fi
  exit 0
fi
if [ "${1:-} ${2:-} ${3:-} ${4:-}" = "mcp add llm-wiki --" ]; then
  shift 4
  printf '%s' "${1:-}" > "$state"
  shift || true
  for arg in "$@"; do printf '\t%s' "$arg" >> "$state"; done
  printf '\n' >> "$state"
  exit 0
fi
exit 2
EOF_GROK_ONLY_STUB
chmod 0755 "$EH/.local/bin/grok"
printf '// private data copy\n' > "$E/private-data/llm-wiki-recall.js"
ln -s "$E/private-data/llm-wiki-recall.js" "$EH/.config/opencode/plugins/llm-wiki-recall.js"
ln -s "$REPO/plugins/llm-wiki-recall.js" "$EH/.opencode/plugins/llm-wiki-recall.js"
set +e
install_env "$EH" "$EB" "$E/state" "$E/cache" "$E/skills" "$REPO/install.sh" --configure-harnesses > "$E/out" 2> "$E/err"
rc=$?
set -e
[[ $rc -eq 1 ]] || fail 'stale link should make installer return nonzero'
[[ $(readlink "$EH/.config/opencode/plugins/llm-wiki-recall.js") == "$E/private-data/llm-wiki-recall.js" ]] || fail 'stale current plugin link was replaced'
[[ $(readlink "$EH/.opencode/plugins/llm-wiki-recall.js") == "$REPO/plugins/llm-wiki-recall.js" ]] || fail 'legacy plugin link was changed'
[[ ! -e "$EH/.config/opencode/opencode.json" && ! -e "$EH/.config/opencode/AGENTS.md" ]] || fail 'OpenCode mutated before stale-link preflight failed'
[[ -s "$EH/.stub-grok-mcp" && -f "$EH/.grok/hooks/llm-wiki.json" && -f "$EH/.grok/AGENTS.md" ]] || fail 'Grok did not configure after OpenCode safe failure'
grep -Fq 'stale/wrong symlink left unchanged' "$E/out" || grep -Fq 'stale/wrong symlink left unchanged' "$E/err" || fail 'stale-link diagnosis missing'
grep -Fq 'ERROR opencode:' "$E/err" || fail 'per-harness error isolation was not reported'
grep -Fq 'OK   grok:' "$E/out" || fail 'subsequent Grok configuration did not continue'
pass 'stale current/legacy OpenCode links are non-destructive and one harness failure does not block Grok'

# F. Symlinked JSON/JSONC config is never followed or replaced.
F="$WORK/symlink-config"; FH="$F/home"; FB="$F/bin"; mkdir -p "$FH/.opencode/bin" "$FH/.config/opencode" "$F/private"
printf '#!/bin/sh\nexit 0\n' > "$FH/.opencode/bin/opencode"; chmod 0755 "$FH/.opencode/bin/opencode"
install_env "$FH" "$FB" "$F/state" "$F/cache" "$F/skills" "$REPO/install.sh" >/dev/null
printf '{"mcp":{"llm-wiki":{"type":"local","command":["%s/llm-wiki-mcp"],"enabled":true}}}\n' "$FB" > "$F/private/opencode.json"
ln -s "$F/private/opencode.json" "$FH/.config/opencode/opencode.json"
symlink_target_hash=$(sha "$F/private/opencode.json")
install_env "$FH" "$FB" "$F/state" "$F/cache" "$F/skills" "$FB/llm-wiki-harness" status --json > "$F/status.json"
python3 - "$F/status.json" <<'PY_SYMLINK_STATUS'
import json,sys
items={item['harness']:item for item in json.load(open(sys.argv[1], encoding='utf-8'))['harnesses']}
assert items['opencode']['mcp']=='unverifiable', items['opencode']
assert items['opencode']['overall']=='unverifiable', items['opencode']
PY_SYMLINK_STATUS
set +e
install_env "$FH" "$FB" "$F/state" "$F/cache" "$F/skills" "$FB/llm-wiki-harness" configure --harness opencode > "$F/out" 2> "$F/err"
rc=$?
set -e
[[ $rc -eq 1 ]] || fail 'symlinked config unexpectedly configured'
[[ -L "$FH/.config/opencode/opencode.json" ]] || fail 'symlinked config path was replaced'
[[ $(readlink "$FH/.config/opencode/opencode.json") == "$F/private/opencode.json" ]] || fail 'symlink target changed'
[[ $(sha "$F/private/opencode.json") == "$symlink_target_hash" ]] || fail 'symlink target bytes changed'
grep -Fq 'refusing to edit symlinked config' "$F/err" || fail 'symlink refusal was not explicit'
pass 'symlinked config is unverifiable, rejected and left byte-identical'

# G. A Grok-compatible generic `agent` in PATH is never classified as Cursor.
G="$WORK/path-grok-agent"; GH="$G/home"; GB="$G/bin"; GP="$G/path"
mkdir -p "$GH" "$GP"
cat > "$GP/agent" <<'EOF_PATH_GROK_AGENT'
#!/bin/sh
if [ "${1:-}" = "--version" ]; then
  printf 'grok 1.0.3 (1a29d5bc12)\n'
  exit 0
fi
exit 2
EOF_PATH_GROK_AGENT
chmod 0755 "$GP/agent"
SAFE_PATH="$GP:/usr/bin:/bin"
path_detect_env "$GH" "$GB" "$G/state" "$G/cache" "$G/skills" "$SAFE_PATH" "$REPO/install.sh" > "$G/install.out"
path_detect_env "$GH" "$GB" "$G/state" "$G/cache" "$G/skills" "$SAFE_PATH" \
  "$GB/llm-wiki-harness" status --json > "$G/status.json"
python3 - "$G/status.json" <<'PY_PATH_GROK_STATUS'
import json,sys
items={item['harness']:item for item in json.load(open(sys.argv[1], encoding='utf-8'))['harnesses']}
cursor=items['cursor']
assert cursor['overall']=='absent', cursor
assert cursor['executable']=='identity-mismatch', cursor
assert cursor['mcp']=='unverifiable' and cursor['lifecycle']=='unverifiable', cursor
assert 'not inspected or mutated' in cursor['note'], cursor
PY_PATH_GROK_STATUS
path_detect_env "$GH" "$GB" "$G/state" "$G/cache" "$G/skills" "$SAFE_PATH" \
  "$REPO/install.sh" --status > "$G/installer-status.out"
python3 - "$G/installer-status.out" <<'PY_INSTALLER_GROK_STATUS'
import sys
rows=[line.split() for line in open(sys.argv[1], encoding='utf-8') if line.startswith('cursor ')]
assert len(rows)==1, rows
assert rows[0][0]=='cursor' and rows[0][1]=='identity-mismatch' and rows[0][-1]=='absent', rows[0]
PY_INSTALLER_GROK_STATUS
path_detect_env "$GH" "$GB" "$G/state" "$G/cache" "$G/skills" "$SAFE_PATH" \
  "$GB/llm-wiki-harness" status --strict >/dev/null
path_detect_env "$GH" "$GB" "$G/state" "$G/cache" "$G/skills" "$SAFE_PATH" \
  "$REPO/install.sh" --check >/dev/null
path_detect_env "$GH" "$GB" "$G/state" "$G/cache" "$G/skills" "$SAFE_PATH" \
  "$GB/llm-wiki-harness" configure --harness cursor > "$G/configure.out"
[[ ! -e "$GH/.cursor" ]] || fail 'PATH Grok agent caused Cursor files to be created'
grep -Fq 'SKIP cursor: absent (identity-mismatch)' "$G/configure.out" \
  || fail 'PATH Grok agent was not explicitly rejected'
pass 'PATH Grok agent is identity-rejected and cannot create Cursor configuration'
pass 'installer --status/--check and harness status --strict ignore Grok agent as Cursor evidence'

# H. The exact ~/.local/bin/agent -> ~/.grok/... collision is non-mutating even
#    with PATH detection disabled; an explicit override changes location only,
#    never the Cursor identity requirement.
H="$WORK/home-grok-agent"; HH="$H/home"; HB="$H/bin"
mkdir -p "$HH/.grok/bin" "$HH/.local/bin" "$HH/.cursor"
cat > "$HH/.grok/bin/agent" <<'EOF_HOME_GROK_AGENT'
#!/bin/sh
if [ "${1:-}" = "--version" ]; then
  printf 'grok 1.0.3 (1a29d5bc12)\n'
  exit 0
fi
exit 2
EOF_HOME_GROK_AGENT
chmod 0755 "$HH/.grok/bin/agent"
ln -s "$HH/.grok/bin/agent" "$HH/.local/bin/agent"
printf 'preserve me\n' > "$HH/.cursor/keep.txt"
install_env "$HH" "$HB" "$H/state" "$H/cache" "$H/skills" "$REPO/install.sh" > "$H/install.out"
cat > "$HH/.cursor/mcp.json" <<EOF_PRIOR_CURSOR_MCP
{"mcpServers":{"llm-wiki":{"command":"sh","args":["-lc","exec $HB/llm-wiki-mcp"]}}}
EOF_PRIOR_CURSOR_MCP
cat > "$HH/.cursor/hooks.json" <<EOF_PRIOR_CURSOR_HOOKS
{"version":1,"hooks":{"sessionStart":[{"command":"$HB/llm-wiki-session-start --format cursor --max-chars 2500","timeout":10}]}}
EOF_PRIOR_CURSOR_HOOKS
before_cursor_tree=$(find "$HH/.cursor" -mindepth 1 -printf '%P|%y|%l\n' | sort)
before_cursor_hashes="$(sha "$HH/.cursor/keep.txt") $(sha "$HH/.cursor/mcp.json") $(sha "$HH/.cursor/hooks.json")"
install_env "$HH" "$HB" "$H/state" "$H/cache" "$H/skills" \
  "$HB/llm-wiki-harness" status --json > "$H/status.json"
python3 - "$H/status.json" <<'PY_HOME_GROK_STATUS'
import json,sys
items={item['harness']:item for item in json.load(open(sys.argv[1], encoding='utf-8'))['harnesses']}
cursor=items['cursor']
assert cursor['overall']=='absent', cursor
assert cursor['executable']=='identity-mismatch', cursor
assert cursor['mcp']=='unverifiable' and cursor['lifecycle']=='unverifiable', cursor
PY_HOME_GROK_STATUS
install_env "$HH" "$HB" "$H/state" "$H/cache" "$H/skills" \
  "$HB/llm-wiki-harness" configure --harness cursor > "$H/configure.out"
after_cursor_tree=$(find "$HH/.cursor" -mindepth 1 -printf '%P|%y|%l\n' | sort)
after_cursor_hashes="$(sha "$HH/.cursor/keep.txt") $(sha "$HH/.cursor/mcp.json") $(sha "$HH/.cursor/hooks.json")"
[[ "$before_cursor_tree" == "$after_cursor_tree" ]] || fail 'Grok agent symlink changed the Cursor tree'
[[ "$before_cursor_hashes" == "$after_cursor_hashes" ]] || fail 'Grok agent symlink changed a Cursor file'

install_env "$HH" "$HB" "$H/state" "$H/cache" "$H/skills" \
  env LLM_WIKI_CURSOR_BIN="$HH/.grok/bin/agent" \
  "$HB/llm-wiki-harness" status --json > "$H/explicit-status.json"
python3 - "$H/explicit-status.json" <<'PY_EXPLICIT_GROK_STATUS'
import json,sys
items={item['harness']:item for item in json.load(open(sys.argv[1], encoding='utf-8'))['harnesses']}
cursor=items['cursor']
assert cursor['overall']=='absent', cursor
assert cursor['executable']=='explicit-identity-mismatch', cursor
PY_EXPLICIT_GROK_STATUS
pass '~/.local/bin/agent resolving into ~/.grok is rejected without touching any Cursor file'
pass 'LLM_WIKI_CURSOR_BIN overrides location but cannot bless a non-Cursor generic agent'

# I. A generic PATH `agent` with an explicit Cursor identity is accepted and
#    remains byte-stable under repeat configuration.
I="$WORK/path-cursor-agent"; IH="$I/home"; IB="$I/bin"; IP="$I/path"
mkdir -p "$IH" "$IP"
cat > "$IP/agent" <<'EOF_PATH_CURSOR_AGENT'
#!/bin/sh
if [ "${1:-}" = "--version" ]; then
  printf 'Cursor Agent 2026.08.11-899851b\n'
  exit 0
fi
exit 2
EOF_PATH_CURSOR_AGENT
chmod 0755 "$IP/agent"
CURSOR_PATH="$IP:/usr/bin:/bin"
path_detect_env "$IH" "$IB" "$I/state" "$I/cache" "$I/skills" "$CURSOR_PATH" \
  "$REPO/install.sh" > "$I/install.out"
path_detect_env "$IH" "$IB" "$I/state" "$I/cache" "$I/skills" "$CURSOR_PATH" \
  "$IB/llm-wiki-harness" configure --harness cursor > "$I/first.out"
grep -Fq 'OK   cursor: changes=2' "$I/first.out" || fail 'verified PATH Cursor agent did not configure'
[[ -f "$IH/.cursor/mcp.json" && -f "$IH/.cursor/hooks.json" ]] \
  || fail 'verified PATH Cursor agent missed managed files'
first_cursor_hashes="$(sha "$IH/.cursor/mcp.json") $(sha "$IH/.cursor/hooks.json")"
path_detect_env "$IH" "$IB" "$I/state" "$I/cache" "$I/skills" "$CURSOR_PATH" \
  "$IB/llm-wiki-harness" status --json > "$I/status.json"
python3 - "$I/status.json" <<'PY_PATH_CURSOR_STATUS'
import json,sys
items={item['harness']:item for item in json.load(open(sys.argv[1], encoding='utf-8'))['harnesses']}
cursor=items['cursor']
assert cursor['overall']=='configured', cursor
assert cursor['executable']=='PATH-verified', cursor
PY_PATH_CURSOR_STATUS
path_detect_env "$IH" "$IB" "$I/state" "$I/cache" "$I/skills" "$CURSOR_PATH" \
  "$IB/llm-wiki-harness" configure --harness cursor > "$I/second.out"
second_cursor_hashes="$(sha "$IH/.cursor/mcp.json") $(sha "$IH/.cursor/hooks.json")"
[[ "$first_cursor_hashes" == "$second_cursor_hashes" ]] || fail 'repeat Cursor config changed files'
grep -Fq 'configuration complete: changes=0, failures=0' "$I/second.out" \
  || fail 'repeat verified Cursor configuration was not a no-op'
pass 'identity-confirmed generic Cursor agent configures safely and idempotently'

# J. Cursor's official installer layout is independent evidence even though the
#    primary `agent --version` output may contain only a build identifier.
J="$WORK/official-cursor-agent"; JH="$J/home"; JB="$J/bin"
JVERSION="2026.08.11-e8db854"
mkdir -p "$JH/.local/bin" "$JH/.local/share/cursor-agent/versions/$JVERSION"
cat > "$JH/.local/share/cursor-agent/versions/$JVERSION/cursor-agent" <<'EOF_OFFICIAL_CURSOR_AGENT'
#!/bin/sh
if [ "${1:-}" = "--version" ]; then
  printf '2026.08.11-e8db854\n'
  exit 0
fi
exit 2
EOF_OFFICIAL_CURSOR_AGENT
chmod 0755 "$JH/.local/share/cursor-agent/versions/$JVERSION/cursor-agent"
ln -s "$JH/.local/share/cursor-agent/versions/$JVERSION/cursor-agent" "$JH/.local/bin/agent"
install_env "$JH" "$JB" "$J/state" "$J/cache" "$J/skills" "$REPO/install.sh" > "$J/install.out"
install_env "$JH" "$JB" "$J/state" "$J/cache" "$J/skills" \
  "$JB/llm-wiki-harness" configure --harness cursor > "$J/configure.out"
install_env "$JH" "$JB" "$J/state" "$J/cache" "$J/skills" \
  "$JB/llm-wiki-harness" status --json > "$J/status.json"
python3 - "$J/status.json" <<'PY_OFFICIAL_CURSOR_STATUS'
import json,sys
items={item['harness']:item for item in json.load(open(sys.argv[1], encoding='utf-8'))['harnesses']}
cursor=items['cursor']
assert cursor['overall']=='configured', cursor
assert cursor['executable']=='home-local-bin-verified', cursor
PY_OFFICIAL_CURSOR_STATUS
pass 'official ~/.local/bin/agent -> ~/.local/share/cursor-agent layout remains a valid Cursor path'

# K. An unresponsive generic `agent` is bounded by the identity timeout and
#    cannot mutate Cursor configuration.
K="$WORK/timeout-agent"; KH="$K/home"; KB="$K/bin"; KP="$K/path"
mkdir -p "$KH" "$KP"
cat > "$KP/agent" <<'EOF_TIMEOUT_AGENT'
#!/bin/sh
if [ "${1:-}" = "--version" ]; then
  sleep 10
  exit 0
fi
exit 2
EOF_TIMEOUT_AGENT
chmod 0755 "$KP/agent"
install_env "$KH" "$KB" "$K/state" "$K/cache" "$K/skills" "$REPO/install.sh" > "$K/install.out"
TIMEOUT_PATH="$KP:/usr/bin:/bin"
path_detect_env "$KH" "$KB" "$K/state" "$K/cache" "$K/skills" "$TIMEOUT_PATH" \
  /usr/bin/timeout 5 "$KB/llm-wiki-harness" status --json > "$K/status.json"
python3 - "$K/status.json" <<'PY_TIMEOUT_STATUS'
import json,sys
items={item['harness']:item for item in json.load(open(sys.argv[1], encoding='utf-8'))['harnesses']}
cursor=items['cursor']
assert cursor['overall']=='absent', cursor
assert cursor['executable']=='identity-unverifiable', cursor
PY_TIMEOUT_STATUS
path_detect_env "$KH" "$KB" "$K/state" "$K/cache" "$K/skills" "$TIMEOUT_PATH" \
  /usr/bin/timeout 5 "$KB/llm-wiki-harness" configure --harness cursor > "$K/configure.out"
[[ ! -e "$KH/.cursor" ]] || fail 'timed-out generic agent created Cursor files'
grep -Fq 'SKIP cursor: absent (identity-unverifiable)' "$K/configure.out" \
  || fail 'timed-out generic agent was not reported as unverifiable'
pass 'generic agent identity probe is timeout-bounded and fail-closed for Cursor writes'

# L. Templates are schema-valid and explicitly document trust/native-memory boundaries.
for json_file in \
  templates/claude/settings.hooks.json templates/grok/hooks.json \
  templates/cursor/hooks.json templates/cursor/mcp.json templates/codex/hooks.json \
  templates/shared/mcp.project.json; do
  python3 -m json.tool "$REPO/$json_file" >/dev/null || fail "invalid JSON template: $json_file"
done
python3 - "$REPO/templates/shared/mcp.project.json" <<'PY_SHARED_MCP'
import json,sys
obj=json.load(open(sys.argv[1], encoding='utf-8'))
entry=obj['mcpServers']['llm-wiki']
assert entry['command']=='/usr/bin/env'
assert entry['args'][0]=='LLM_WIKI_AGENT_PROFILE=coding'
assert entry['args'][1]=='LLM_WIKI_DOMAIN=work'
assert entry['args'][2:4]==['/bin/sh', '-lc']
assert 'llm-wiki-mcp' in entry['args'][4]
PY_SHARED_MCP
python3 - "$REPO" <<'PY_PROFILED_TEMPLATES'
import json,pathlib,sys
root=pathlib.Path(sys.argv[1])
cases = {
    'claude/settings.hooks.json': ('claude', 'hooks'),
    'codex/hooks.json': ('codex', 'hooks'),
    'cursor/hooks.json': ('cursor', 'hooks'),
    'cursor/mcp.json': ('cursor', 'mcp'),
    'grok/hooks.json': ('grok', 'hooks'),
}
for relative, (profile, _kind) in cases.items():
    text=(root/'templates'/relative).read_text(encoding='utf-8')
    assert f'LLM_WIKI_AGENT_PROFILE={profile}' in text, relative
    assert 'LLM_WIKI_DOMAIN=work' in text, relative
opencode=(root/'templates/opencode/opencode.mcp.jsonc').read_text(encoding='utf-8')
assert '"/usr/bin/env"' in opencode
assert 'LLM_WIKI_AGENT_PROFILE=opencode' in opencode
assert 'LLM_WIKI_DOMAIN=work' in opencode
assert 'LLM_WIKI_TARGET_AGENTS=codex,opencode' in opencode
PY_PROFILED_TEMPLATES
grep -Fqi 'untrusted evidence' "$REPO/templates/claude/CLAUDE.recall.md" || fail 'Claude trust boundary missing'
grep -Fqi 'experimental memory' "$REPO/templates/grok/AGENTS.recall.md" || fail 'Grok native-memory boundary missing'
grep -Fqi 'Cloud Agents' "$REPO/templates/cursor/AGENTS.recall.md" || fail 'Cursor Cloud/local boundary missing'
pass 'templates are valid and encode shared-MCP, trust and native-memory boundaries'

printf '\n--- result: PASS=%s FAIL=0 ---\n' "$PASS"
