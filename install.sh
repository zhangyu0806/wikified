#!/usr/bin/env bash
# install.sh — 把 llm-wiki 受管工具链链接到本机各 Agent 目录
#
# 设计要点：
#   - 幂等：重复运行结果一致，已正确的链接不动
#   - 软链而非拷贝：repo 是唯一来源，pull 后立即生效，不需要重新 install
#   - --check：只校验不改动，供 remote-sync / CI 当门禁用
#   - 不覆盖非软链的既有文件：先备份，避免吞掉未纳管的本地修改
#
# 用法：
#   ./install.sh              # 安装/修复链接
#   ./install.sh --check      # 只校验（exit 1 = 有偏差）
#   ./install.sh --dry-run    # 只打印将要做的事

set -Eeuo pipefail
umask 077

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
BIN_TARGET=${LLM_WIKI_BIN_TARGET:-"$HOME/.local/bin"}
OPENCODE_PLUGIN_TARGET=${LLM_WIKI_OPENCODE_PLUGIN_TARGET:-"$HOME/.opencode/plugins"}

# Skills 走「主副本 + 分发」：repo -> ~/.agents/skills（主副本）-> 各 Agent 目录。
# 这样多个生态共享同一份 SKILL.md，不会各存一份导致分叉。
# 只对本机实际存在的 Agent 目录分发，不存在的跳过（不当作偏差）。
AGENT_SKILL_ROOT=${LLM_WIKI_AGENT_SKILL_ROOT:-"$HOME/.agents/skills"}
# 空格分隔的分发目标；设为空字符串可完全关闭分发。
SKILL_FANOUT=${LLM_WIKI_SKILL_FANOUT-"$HOME/.claude/skills $HOME/.config/opencode/skills $HOME/.codex/skills"}

MODE=install
DO_INIT=0
case "${1:-}" in
  --check)   MODE=check ;;
  --dry-run) MODE=dryrun ;;
  --init)    MODE=install; DO_INIT=1 ;;
  "")        MODE=install ;;
  -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
  *) printf 'install.sh: 未知参数 %s\n' "$1" >&2; exit 2 ;;
esac

DEVIATIONS=0
CHANGES=0
WARNINGS=0

note()  { printf '  %s\n' "$*"; }
deviate() { DEVIATIONS=$((DEVIATIONS + 1)); printf '  ✗ %s\n' "$*"; }
# 不计入退出码：用于 repo 范围外、本机可能合理缺失的东西（如非 opencode 机器没有 AGENTS.md）
warn()  { WARNINGS=$((WARNINGS + 1)); printf '  ⚠ %s\n' "$*"; }

# link_one <源绝对路径> <目标绝对路径>
link_one() {
  local src=$1 dst=$2 cur
  if [[ ! -e "$src" ]]; then
    deviate "源不存在: $src"
    return
  fi

  if [[ -L "$dst" ]]; then
    cur=$(readlink -f -- "$dst" || true)
    if [[ "$cur" == "$(readlink -f -- "$src")" ]]; then
      return  # 已正确，静默
    fi
    if [[ "$MODE" == check ]]; then
      deviate "链接指向他处: $dst -> $cur"
      return
    fi
    [[ "$MODE" == dryrun ]] && { note "would relink $dst"; return; }
    ln -sfn -- "$src" "$dst"
    note "relinked $dst"
    CHANGES=$((CHANGES + 1))
    return
  fi

  if [[ -e "$dst" ]]; then
    # 存在真实文件/目录：可能是未纳管的本地版本，先备份再替换
    if [[ "$MODE" == check ]]; then
      deviate "非软链占位（未纳管的本地副本）: $dst"
      return
    fi
    [[ "$MODE" == dryrun ]] && { note "would backup+link $dst"; return; }
    local bak="$dst.bak-preinstall-$(date +%Y%m%d-%H%M%S)"
    mv -- "$dst" "$bak"
    note "backed up $dst -> $(basename "$bak")"
    ln -sfn -- "$src" "$dst"
    note "linked $dst"
    CHANGES=$((CHANGES + 1))
    return
  fi

  if [[ "$MODE" == check ]]; then
    deviate "缺失: $dst"
    return
  fi
  [[ "$MODE" == dryrun ]] && { note "would link $dst"; return; }
  ln -sfn -- "$src" "$dst"
  note "linked $dst"
  CHANGES=$((CHANGES + 1))
}

printf 'llm-wiki install (%s)\n' "$MODE"
printf '  repo: %s\n' "$REPO"

# ---------- 1. CLI ----------
printf '\n[1/5] CLI -> %s\n' "$BIN_TARGET"
if [[ "$MODE" == install ]]; then mkdir -p "$BIN_TARGET"; fi
shopt -s nullglob
for src in "$REPO"/bin/*; do
  [[ -f "$src" ]] || continue
  [[ "$MODE" == install ]] && chmod 0755 "$src"
  link_one "$src" "$BIN_TARGET/$(basename "$src")"
done

# ---------- 2. OpenCode 插件 ----------
printf '\n[2/5] OpenCode plugins -> %s\n' "$OPENCODE_PLUGIN_TARGET"
if [[ "$MODE" == install ]]; then mkdir -p "$OPENCODE_PLUGIN_TARGET"; fi
for src in "$REPO"/plugins/*.js; do
  [[ -f "$src" ]] || continue
  link_one "$src" "$OPENCODE_PLUGIN_TARGET/$(basename "$src")"
done

# ---------- 3. Skills：repo -> 主副本 -> 各 Agent ----------
printf '\n[3/5] Skills 主副本 -> %s\n' "$AGENT_SKILL_ROOT"
if [[ "$MODE" == install ]]; then mkdir -p "$AGENT_SKILL_ROOT"; fi
skill_names=()
for src in "$REPO"/skills/*; do
  [[ -d "$src" ]] || continue
  name=$(basename "$src")
  skill_names+=("$name")
  link_one "$src" "$AGENT_SKILL_ROOT/$name"
done

if [[ -z "${SKILL_FANOUT// /}" ]]; then
  note "分发已关闭（LLM_WIKI_SKILL_FANOUT 为空）"
else
  for dst_root in $SKILL_FANOUT; do
    if [[ ! -d "$dst_root" ]]; then
      note "跳过 $dst_root（本机无此 Agent）"
      continue
    fi
    printf '  分发 -> %s\n' "$dst_root"
    for name in "${skill_names[@]}"; do
      link_one "$AGENT_SKILL_ROOT/$name" "$dst_root/$name"
    done
  done
fi
shopt -u nullglob

# ---------- 4. git hooks ----------
printf '\n[4/5] git hooks\n'
if [[ -e "$REPO/.git" ]]; then
  want=.githooks
  cur=$(git -C "$REPO" config --get core.hooksPath 2>/dev/null || true)
  if [[ "$cur" == "$want" ]]; then
    :
  elif [[ "$MODE" == check ]]; then
    deviate "core.hooksPath 未设为 $want（当前: ${cur:-<unset>}）"
  elif [[ "$MODE" == dryrun ]]; then
    note "would set core.hooksPath=$want"
  else
    git -C "$REPO" config core.hooksPath "$want"
    note "set core.hooksPath=$want"
    CHANGES=$((CHANGES + 1))
  fi
  [[ "$MODE" == install ]] && chmod 0755 "$REPO/.githooks/"* 2>/dev/null || true
else
  note "跳过（$REPO 还不是 git 仓库）"
fi

# ---------- 5. 范围外接线 ----------
printf '\n[5/5] 范围外接线（本 repo 管不到，只报告）\n'
AGENTS_MD=${LLM_WIKI_AGENTS_MD:-"$HOME/.config/opencode/AGENTS.md"}
if [[ -f "$AGENTS_MD" ]]; then
  if grep -q 'llm-wiki-remote-sync' "$AGENTS_MD"; then
    note "AGENTS.md 已接线"
  else
    warn "AGENTS.md 未接线：$AGENTS_MD 里没有 llm-wiki-remote-sync"
    note "  → 在「会话启动必做」代码块加一行，放在 llm-wiki-govern 之后、gh-watch 之前"
    note "  → 不接线则同步永不触发（装了但不生效）"
  fi
else
  note "跳过（无 $AGENTS_MD，本机可能不用 opencode）"
fi

MCP_BIN="$BIN_TARGET/llm-wiki-mcp"
if [[ -e "$MCP_BIN" ]]; then
  mcp_registered=0
  OPENCODE_JSON="${LLM_WIKI_OPENCODE_JSON:-$HOME/.config/opencode/opencode.json}"
  CODEX_TOML="${LLM_WIKI_CODEX_TOML:-$HOME/.codex/config.toml}"
  if [[ -f "$OPENCODE_JSON" ]] && grep -q 'llm-wiki' "$OPENCODE_JSON"; then
    note "MCP 已在 opencode.json 注册"
    mcp_registered=1
  fi
  if [[ -f "$CODEX_TOML" ]] && grep -q 'llm-wiki' "$CODEX_TOML"; then
    note "MCP 已在 codex config.toml 注册"
    mcp_registered=1
  fi
  if (( ! mcp_registered )); then
    warn "MCP server 已安装但未在任何 Agent 注册：$MCP_BIN"
    note "  → 注册后任意 MCP 客户端（Codex / Cursor / Claude Code / OpenCode）都能读写这套记忆"
    note "  → Codex（官方 CLI，注意 -- 分隔符）:"
    note "      codex mcp add llm-wiki -- $MCP_BIN"
    note "  → OpenCode（opencode.json 的 mcp 段）:"
    note "      \"llm-wiki\": { \"type\": \"local\", \"command\": [\"$MCP_BIN\"], \"enabled\": true }"
  fi

  # Codex 已支持 SessionStart hook，但 hooks.json / AGENTS.md / prompts 都可能
  # 含用户自己的配置，因此只检测和打印安全合并指引，绝不整文件覆盖。
  CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"
  CODEX_HOOKS_JSON="${LLM_WIKI_CODEX_HOOKS_JSON:-$CODEX_HOME_DIR/hooks.json}"
  if [[ -f "$CODEX_HOOKS_JSON" ]] \
    && grep -q 'SessionStart' "$CODEX_HOOKS_JSON" \
    && grep -q 'llm-wiki-enrich' "$CODEX_HOOKS_JSON"; then
    note "Codex SessionStart hook 已接线"
  else
    warn "Codex 自动召回未接线：$CODEX_HOOKS_JSON 缺 LLM Wiki SessionStart hook"
    note "  → 合并 $REPO/templates/codex/hooks.json（已有 hooks 时不要覆盖）"
    note "  → Windows Desktop + WSL 使用模板里的 commandWindows；必要时加 -d <distro>"
    note "  → 重启 Codex 后用 /hooks 审查并信任精确 hook 定义"
  fi

  CODEX_AGENTS_MD="${LLM_WIKI_CODEX_AGENTS_MD:-$CODEX_HOME_DIR/AGENTS.md}"
  if [[ -d "$(dirname "$CODEX_AGENTS_MD")" ]]; then
    if [[ -f "$CODEX_AGENTS_MD" ]] && grep -q 'BEGIN llm-wiki-recall' "$CODEX_AGENTS_MD"; then
      note "Codex 召回块已接线"
    else
      warn "Codex 缺按需召回规则：$CODEX_AGENTS_MD 缺 llm-wiki-recall 块"
      note "  → cat $REPO/templates/codex/AGENTS.recall.md >> $CODEX_AGENTS_MD"
      note "  → cp $REPO/templates/codex/prompts/llm-wiki-recall.md $(dirname "$CODEX_AGENTS_MD")/prompts/"
      note "  → hook 只注入极简卡片；这条规则负责按当前任务做定向召回"
    fi
  fi
fi

PROFILE_FILE="${XDG_CACHE_HOME:-$HOME/.cache}/llm-wiki-sync/profile"
if [[ -s "$PROFILE_FILE" ]]; then
  note "profile: $(head -c 64 -- "$PROFILE_FILE" | tr -d '\n')"
else
  warn "未设 profile：$PROFILE_FILE 缺失，将回落到 hostname（日志/提交信息难读）"
  note "  → mkdir -p \"\$(dirname \"$PROFILE_FILE\")\" && echo office > \"$PROFILE_FILE\""
fi

# ---------- 结果 ----------
printf '\n'
if [[ "$MODE" == check ]]; then
  if (( DEVIATIONS )); then
    printf '✗ check 失败：%s 处偏差。跑 ./install.sh 修复。\n' "$DEVIATIONS"
    exit 1
  fi
  printf '✓ check 通过：工具链链接与 repo 一致。\n'
  (( WARNINGS )) && printf '  ⚠ 但有 %s 处范围外接线待补（见 [5/5]）。\n' "$WARNINGS"
  exit 0
fi

if (( DEVIATIONS )); then
  printf '✗ 完成但有 %s 处问题，见上。\n' "$DEVIATIONS"
  exit 1
fi

printf '✓ 完成（改动 %s 处）。\n' "$CHANGES"

# --init 必须跑在软链之后：此前 llm-wiki-init 还不在 BIN_TARGET 里。
# 这里调 repo 内的原件而非软链，避免依赖 PATH 是否已包含 BIN_TARGET。
if (( DO_INIT )); then
  printf '\n[init] 初始化数据库\n'
  if [[ -x "$REPO/bin/llm-wiki-init" ]]; then
    "$REPO/bin/llm-wiki-init" --git 2>&1 | sed 's/^/  /'
  else
    deviate "缺 $REPO/bin/llm-wiki-init，无法初始化数据库"
  fi
fi

if [[ "$MODE" == install ]]; then
  case ":$PATH:" in
    *":$BIN_TARGET:"*) ;;
    *) printf '\n注意：%s 不在 PATH 中，需加入 shell 配置。\n' "$BIN_TARGET" ;;
  esac
  printf '\n下一步：\n'
  if (( DO_INIT )); then
    printf '  1) 重启 opencode 让 recall 插件生效\n'
    printf '  2) llm-wiki-health --json  确认库健康\n'
  else
    printf '  1) llm-wiki-init --git  初始化数据库（未初始化则主命令会失败）\n'
    printf '  2) 重启 opencode 让 recall 插件生效\n'
  fi
  printf '  3) llm-wiki-remote-sync --status  查看同步节流状态\n'
  if (( WARNINGS )); then
    printf '  4) 补上 %s 处范围外接线（见 [5/5]，不补则同步不会自动触发）\n' "$WARNINGS"
  fi
fi

exit 0
