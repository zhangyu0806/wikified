#!/usr/bin/env bash
# install.sh — install Wikified mechanisms, then explicitly configure harnesses.
#
# Normal install creates only managed tool/plugin/skill links. It never rewrites
# harness settings. Use --configure-harnesses for the safe, idempotent Claude,
# OpenCode and Grok configuration path; use --status for the capability matrix.

set -Eeuo pipefail
umask 077

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
BIN_TARGET=${LLM_WIKI_BIN_TARGET:-"$HOME/.local/bin"}
OPENCODE_PLUGIN_TARGET=${LLM_WIKI_OPENCODE_PLUGIN_TARGET:-"$HOME/.config/opencode/plugins"}
OPENCODE_LEGACY_PLUGIN_TARGET=${LLM_WIKI_OPENCODE_LEGACY_PLUGIN_TARGET:-"$HOME/.opencode/plugins"}
AGENT_SKILL_ROOT=${LLM_WIKI_AGENT_SKILL_ROOT:-"$HOME/.agents/skills"}
SKILL_FANOUT=${LLM_WIKI_SKILL_FANOUT-"$HOME/.claude/skills $HOME/.config/opencode/skills $HOME/.codex/skills"}

MODE=install
DO_INIT=0
DO_CONFIGURE=0
mode_seen=0
usage() {
  cat <<'EOF'
Usage: ./install.sh [option]
  (no option)              install managed links only
  --init                   install links and initialize the memory repository
  --configure-harnesses    install links, then configure installed Claude/OpenCode/Grok
  --status                 print the non-secret five-harness capability matrix
  --check                  verify links and installed-harness configuration
  --dry-run                print link changes without writing
EOF
}
for arg in "$@"; do
  case "$arg" in
    --init) DO_INIT=1 ;;
    --configure-harnesses) DO_CONFIGURE=1 ;;
    --status)
      (( mode_seen == 0 )) || { printf 'install.sh: only one mode option is allowed\n' >&2; exit 2; }
      MODE=status; mode_seen=1 ;;
    --check)
      (( mode_seen == 0 )) || { printf 'install.sh: only one mode option is allowed\n' >&2; exit 2; }
      MODE=check; mode_seen=1 ;;
    --dry-run)
      (( mode_seen == 0 )) || { printf 'install.sh: only one mode option is allowed\n' >&2; exit 2; }
      MODE=dryrun; mode_seen=1 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'install.sh: unknown option %s\n' "$arg" >&2; usage >&2; exit 2 ;;
  esac
done
if [[ "$MODE" != install && ( $DO_INIT -eq 1 || $DO_CONFIGURE -eq 1 ) ]]; then
  printf 'install.sh: --init/--configure-harnesses cannot be combined with %s\n' "$MODE" >&2
  exit 2
fi

# Codex Desktop on Windows and Codex CLI inside WSL can use different homes.
CODEX_HOME_DIRS=("${CODEX_HOME:-$HOME/.codex}")
WINDOWS_CODEX_HOME=${LLM_WIKI_WINDOWS_CODEX_HOME:-}
USER_HOME_FROM_PASSWD=$(getent passwd "$(id -u)" 2>/dev/null | cut -d: -f6 || true)
if [[ -z "$WINDOWS_CODEX_HOME" \
  && -n "${WSL_DISTRO_NAME:-}" \
  && "$HOME" == "$USER_HOME_FROM_PASSWD" \
  && -x /mnt/c/Windows/System32/cmd.exe \
  && $(command -v wslpath 2>/dev/null) ]]; then
  WIN_PROFILE=$(/mnt/c/Windows/System32/cmd.exe /d /c echo %USERPROFILE% 2>/dev/null | tr -d '\r' | head -n 1)
  if [[ -n "$WIN_PROFILE" ]]; then
    WINDOWS_CODEX_HOME="$(wslpath -u "$WIN_PROFILE")/.codex"
  fi
fi
if [[ -n "$WINDOWS_CODEX_HOME" ]]; then
  case "$WINDOWS_CODEX_HOME" in
    [A-Za-z]:\\*) WINDOWS_CODEX_HOME=$(wslpath -u "$WINDOWS_CODEX_HOME") ;;
  esac
  [[ "$WINDOWS_CODEX_HOME" == "${CODEX_HOME_DIRS[0]}" ]] || CODEX_HOME_DIRS+=("$WINDOWS_CODEX_HOME")
fi

export LLM_WIKI_REPO="$REPO"
export LLM_WIKI_BIN_TARGET="$BIN_TARGET"
export LLM_WIKI_OPENCODE_PLUGIN_TARGET="$OPENCODE_PLUGIN_TARGET"
export LLM_WIKI_OPENCODE_LEGACY_PLUGIN_TARGET="$OPENCODE_LEGACY_PLUGIN_TARGET"
[[ -n "$WINDOWS_CODEX_HOME" ]] && export LLM_WIKI_WINDOWS_CODEX_HOME="$WINDOWS_CODEX_HOME"

HARNESS="$REPO/bin/llm-wiki-harness"
if [[ "$MODE" == status ]]; then
  exec "$HARNESS" status
fi

DEVIATIONS=0
CHANGES=0
WARNINGS=0
note() { printf '  %s\n' "$*"; }
deviate() { DEVIATIONS=$((DEVIATIONS + 1)); printf '  ✗ %s\n' "$*"; }
warn() { WARNINGS=$((WARNINGS + 1)); printf '  ⚠ %s\n' "$*"; }

# link_one SOURCE TARGET -- never replaces user-owned files or wrong symlinks.
link_one() {
  local src=$1 dst=$2 cur
  if [[ ! -e "$src" ]]; then
    deviate "source missing: $src"
    return
  fi
  if [[ -L "$dst" ]]; then
    cur=$(readlink -f -- "$dst" || true)
    if [[ "$cur" == "$(readlink -f -- "$src")" ]]; then
      return
    fi
    deviate "stale/wrong symlink left unchanged: $dst -> ${cur:-<broken>}"
    note "review it, then move it manually before rerunning: mv -- '$dst' '$dst.bak-manual'"
    return
  fi
  if [[ -e "$dst" ]]; then
    deviate "user-owned path left unchanged: $dst"
    note "review it, then move it manually before rerunning"
    return
  fi
  if [[ "$MODE" == check ]]; then
    deviate "missing: $dst"
    return
  fi
  if [[ "$MODE" == dryrun ]]; then
    note "would link $dst"
    return
  fi
  mkdir -p -- "$(dirname -- "$dst")"
  ln -s -- "$src" "$dst"
  note "linked $dst"
  CHANGES=$((CHANGES + 1))
}

# Remove only legacy Skill symlinks whose lexical target is exactly this
# repository's retired mixed-case source path. Broken managed links cannot be
# resolved with readlink -f, so compare normalized link text without following
# symlinks. Every other file, directory, or symlink is user-owned and is left
# untouched with a safe-stop diagnosis.
migrate_legacy_skill_link() {
  local legacy_name=$1 dst raw actual expected
  dst="$AGENT_SKILL_ROOT/$legacy_name"
  expected=$(realpath -ms -- "$REPO/skills/$legacy_name")

  if [[ -L "$dst" ]]; then
    raw=$(readlink -- "$dst")
    if [[ "$raw" == /* ]]; then
      actual=$(realpath -ms -- "$raw")
    else
      actual=$(realpath -ms -- "$(dirname -- "$dst")/$raw")
    fi
    if [[ "$actual" == "$expected" ]]; then
      if [[ "$MODE" == check ]]; then
        deviate "legacy managed skill symlink requires migration: $dst -> $raw"
      elif [[ "$MODE" == dryrun ]]; then
        note "would remove legacy managed skill symlink $dst"
      else
        rm -- "$dst"
        note "removed legacy managed skill symlink $dst"
        CHANGES=$((CHANGES + 1))
      fi
      return
    fi
    deviate "legacy skill symlink is not managed by this repo and was left unchanged: $dst -> $raw"
    return
  fi
  if [[ -e "$dst" ]]; then
    deviate "user-owned legacy skill path left unchanged: $dst"
  fi
}

printf 'Wikified install (%s)\n' "$MODE"
printf '  repo: %s\n' "$REPO"

printf '\n[1/6] CLI -> %s\n' "$BIN_TARGET"
[[ "$MODE" == install ]] && mkdir -p "$BIN_TARGET"
shopt -s nullglob
for src in "$REPO"/bin/*; do
  [[ -f "$src" ]] || continue
  [[ "$MODE" == install ]] && chmod 0755 "$src"
  link_one "$src" "$BIN_TARGET/$(basename "$src")"
done

printf '\n[2/6] OpenCode plugins (official global path) -> %s\n' "$OPENCODE_PLUGIN_TARGET"
[[ "$MODE" == install ]] && mkdir -p "$OPENCODE_PLUGIN_TARGET"
for src in "$REPO"/plugins/*.js; do
  [[ -f "$src" ]] || continue
  link_one "$src" "$OPENCODE_PLUGIN_TARGET/$(basename "$src")"
done
legacy="$OPENCODE_LEGACY_PLUGIN_TARGET/llm-wiki-recall.js"
if [[ -e "$legacy" || -L "$legacy" ]]; then
  warn "legacy OpenCode plugin path detected and left untouched: $legacy"
  note "OpenCode's current global plugin path is $OPENCODE_PLUGIN_TARGET"
fi

printf '\n[3/6] Skills primary copy -> %s\n' "$AGENT_SKILL_ROOT"
[[ "$MODE" == install ]] && mkdir -p "$AGENT_SKILL_ROOT"
for legacy_name in QuickNote SessionCapture WikiCompiler; do
  migrate_legacy_skill_link "$legacy_name"
done
skill_names=()
for src in "$REPO"/skills/*; do
  [[ -d "$src" ]] || continue
  name=$(basename "$src")
  skill_names+=("$name")
  link_one "$src" "$AGENT_SKILL_ROOT/$name"
done
if [[ -z "${SKILL_FANOUT// /}" ]]; then
  note "fanout disabled (LLM_WIKI_SKILL_FANOUT is empty); Grok still discovers ~/.agents/skills"
else
  for dst_root in $SKILL_FANOUT; do
    if [[ ! -d "$dst_root" ]]; then
      note "skip $dst_root (harness directory absent)"
      continue
    fi
    printf '  fanout -> %s\n' "$dst_root"
    for name in "${skill_names[@]}"; do
      # Fan out from the published source, not through a possibly user-owned
      # primary skill path that was preserved by the no-clobber guard.
      link_one "$REPO/skills/$name" "$dst_root/$name"
    done
  done
fi
shopt -u nullglob

printf '\n[4/6] git hooks\n'
if [[ -e "$REPO/.git" ]]; then
  want=.githooks
  cur=$(git -C "$REPO" config --get core.hooksPath 2>/dev/null || true)
  if [[ "$cur" == "$want" ]]; then
    :
  elif [[ "$MODE" == check ]]; then
    deviate "core.hooksPath is not $want (current: ${cur:-<unset>})"
  elif [[ "$MODE" == dryrun ]]; then
    note "would set core.hooksPath=$want"
  else
    git -C "$REPO" config core.hooksPath "$want"
    note "set core.hooksPath=$want"
    CHANGES=$((CHANGES + 1))
  fi
  [[ "$MODE" == install ]] && chmod 0755 "$REPO/.githooks/"* 2>/dev/null || true
else
  note "skip (attachment/public archive has no Git metadata)"
fi

printf '\n[5/6] Daily auto-sync timer (opt-in)\n'
# 自动提交会写 git commit，所以默认不启用：需要一个显式开关文件。
# 这条边界是刻意的 —— 自动提交绕过了「提交需显式意图」，必须由人明确打开。
SYSTEMD_USER_DIR=${LLM_WIKI_SYSTEMD_USER_DIR:-"$HOME/.config/systemd/user"}
AUTO_COMMIT_SWITCH=${LLM_WIKI_AUTO_COMMIT_SWITCH:-"$HOME/.config/wikified/auto-commit.enabled"}
if [[ ! -d "$REPO/templates/systemd" ]]; then
  note "skip (no systemd templates in this archive)"
elif ! command -v systemctl >/dev/null 2>&1; then
  note "skip (systemctl unavailable; see docs/AUTO_SYNC.md for cron fallback)"
elif [[ ! -f "$AUTO_COMMIT_SWITCH" ]] || [[ "$(tr -d '[:space:]' <"$AUTO_COMMIT_SWITCH")" != enabled ]]; then
  note "disabled by default; to enable run:"
  note "  mkdir -p $(dirname "$AUTO_COMMIT_SWITCH") && echo enabled > $AUTO_COMMIT_SWITCH"
  note "  then re-run ./install.sh   (details: docs/AUTO_SYNC.md)"
else
  for unit in llm-wiki-auto-commit.service llm-wiki-auto-commit.timer; do
    src="$REPO/templates/systemd/$unit"
    dst="$SYSTEMD_USER_DIR/$unit"
    [[ -f "$src" ]] || continue
    # @BIN_TARGET@ 占位符替换成实际 CLI 路径，因此是生成文件而非 symlink
    rendered=$(sed "s|@BIN_TARGET@|$BIN_TARGET|g" "$src")
    if [[ -f "$dst" ]] && [[ "$(cat "$dst")" == "$rendered" ]]; then
      continue
    elif [[ "$MODE" == check ]]; then
      deviate "systemd unit out of date or missing: $dst"
    elif [[ "$MODE" == dryrun ]]; then
      note "would write $dst"
    else
      mkdir -p "$SYSTEMD_USER_DIR"
      printf '%s\n' "$rendered" >"$dst"
      chmod 0644 "$dst"
      note "wrote $dst"
      CHANGES=$((CHANGES + 1))
    fi
  done
  if [[ "$MODE" == install ]]; then
    systemctl --user daemon-reload 2>/dev/null || true
    if systemctl --user enable --now llm-wiki-auto-commit.timer 2>/dev/null; then
      note "timer enabled: $(systemctl --user list-timers --all llm-wiki-auto-commit.timer 2>/dev/null | sed -n '2p' | tr -s ' ' | cut -d' ' -f1-3)"
    else
      deviate "could not enable timer; check: systemctl --user status llm-wiki-auto-commit.timer"
    fi
  fi
fi

printf '\n[6/6] Harness capability matrix\n'
if (( DO_CONFIGURE )); then
  if "$HARNESS" configure --harness claude --harness opencode --harness grok; then
    note "explicit harness configuration completed"
  else
    deviate "harness configuration failed safely; see the exact conflict/recovery message above"
  fi
fi
if [[ "$MODE" == check ]]; then
  if ! "$HARNESS" status --strict; then
    deviate "one or more installed harnesses are unconfigured, stale/wrong, or unverifiable"
  fi
else
  "$HARNESS" status || warn "harness status could not be completed"
fi

PROFILE_FILE="${XDG_CACHE_HOME:-$HOME/.cache}/llm-wiki-sync/profile"
if [[ -s "$PROFILE_FILE" ]]; then
  note "sync profile configured"
else
  warn "sync profile is unset; hostname fallback will be used"
  note "optional: mkdir -p \"$(dirname "$PROFILE_FILE")\" && printf 'office\\n' > '$PROFILE_FILE'"
fi

printf '\n'
if [[ "$MODE" == check ]]; then
  if (( DEVIATIONS )); then
    printf '✗ check failed: %s deviation(s). No user-owned path was replaced.\n' "$DEVIATIONS"
    exit 1
  fi
  printf '✓ check passed: managed links and installed harnesses are configured.\n'
  exit 0
fi
if (( DEVIATIONS )); then
  printf '✗ completed with %s safe-stop issue(s); no conflicting path was overwritten.\n' "$DEVIATIONS"
  exit 1
fi
printf '✓ completed (changes=%s).\n' "$CHANGES"

if (( DO_INIT )); then
  printf '\n[init] initialize memory repository\n'
  "$REPO/bin/llm-wiki-init" --git 2>&1 | sed 's/^/  /'
fi

if [[ "$MODE" == install ]]; then
  case ":$PATH:" in
    *":$BIN_TARGET:"*) ;;
    *) printf '\nNote: %s is not in PATH; add it to the shell configuration.\n' "$BIN_TARGET" ;;
  esac
  printf '\nNext steps:\n'
  next_step=1
  if (( ! DO_INIT )); then
    printf '  %s) llm-wiki-init --git\n' "$next_step"
    next_step=$((next_step + 1))
  fi
  if (( ! DO_CONFIGURE )); then
    printf '  %s) ./install.sh --configure-harnesses\n' "$next_step"
    next_step=$((next_step + 1))
  fi
  printf '  %s) ./install.sh --status\n' "$next_step"
  next_step=$((next_step + 1))
  printf '  %s) restart/trust the applicable harness, then verify with its native MCP/hooks UI\n' "$next_step"
fi
exit 0
