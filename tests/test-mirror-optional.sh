#!/usr/bin/env bash
# 核心写入路径不得被可选镜像层拖垮。
#
# 为什么存在：llm-wiki-note 会调 llm-wiki-refresh，后者曾无条件调
# llm-wiki-obsidian-sync 且 check=True。镜像目标是可选的（取决于本机有没有
# Obsidian vault），未配置时 obsidian-sync 以 rc=1 退出，CalledProcessError
# 冒泡成 traceback，把 llm-wiki-note 一起打死——新用户的第一条命令就会撞上。
# 作者机器上 config.env 已设，这条路径永远走不到，只有隔离 HOME 能暴露。
#
# 同时锁住反向不变式：配了镜像就必须真同步，且真故障不能被静默吞掉。
set -euo pipefail

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
PASS=0
FAIL=0
ok()  { PASS=$((PASS+1)); printf 'PASS  %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf 'FAIL: %s\n' "$1"; }

WORK=$(mktemp -d "${TMPDIR:-/tmp}/mirroroptional.XXXXXX")
trap 'rm -rf "$WORK"' EXIT

HOME_DIR="$WORK/h"
mkdir -p "$HOME_DIR"
export PATH="$REPO/bin:$PATH"

run() { env HOME="$HOME_DIR" LLM_WIKI_ROOT="$HOME_DIR/llm-wiki" "$@"; }

run "$REPO/bin/llm-wiki-init" --git >/dev/null 2>&1 \
  || { bad "前置失败：llm-wiki-init 无法建库"; printf '\n--- 结果: PASS=%s FAIL=%s ---\n' "$PASS" "$FAIL"; exit 1; }

# ---------- 1. 未配镜像：核心写入必须成功 ----------
out=$(run env -u LLM_WIKI_MIRROR "$REPO/bin/llm-wiki-note" "mirror-optional 回归用例" 2>&1) && rc=0 || rc=$?
[[ "$rc" -eq 0 ]] && ok "未配镜像: llm-wiki-note rc=0" || bad "未配镜像: llm-wiki-note rc=$rc"
grep -qi 'traceback' <<<"$out" && bad "未配镜像: 抛了 traceback" || ok "未配镜像: 无 traceback"
grep -q 'skipped Obsidian mirror' <<<"$out" \
  && ok "未配镜像: 明确告知跳过而非静默" || bad "未配镜像: 未说明跳过原因"
[[ -n "$(ls -A "$HOME_DIR/llm-wiki/raw/notes" 2>/dev/null)" ]] \
  && ok "未配镜像: note 真的落盘" || bad "未配镜像: note 没写进去"

out=$(run env -u LLM_WIKI_MIRROR "$REPO/bin/llm-wiki-refresh" 2>&1) && rc=0 || rc=$?
[[ "$rc" -eq 0 ]] && ok "未配镜像: llm-wiki-refresh rc=0" || bad "未配镜像: refresh rc=$rc"

# ---------- 2. 配了镜像：必须真同步（防修复过头变成永久跳过）----------
VAULT="$WORK/vault"
mkdir -p "$HOME_DIR/.config/llm-wiki" "$VAULT"
printf 'LLM_WIKI_MIRROR=%s\n' "$VAULT" > "$HOME_DIR/.config/llm-wiki/config.env"
mkdir -p "$HOME_DIR/llm-wiki/raw/inbox/auto-drafts"
printf '# ignored auto draft\n' > "$HOME_DIR/llm-wiki/raw/inbox/auto-drafts/ignored.md"

run "$REPO/bin/llm-wiki-refresh" >/dev/null 2>&1 || true
[[ -f "$VAULT/SCHEMA.md" ]] && ok "配了镜像: 真的同步了 SCHEMA.md" || bad "配了镜像: 未同步"
[[ -d "$VAULT/wiki" ]] && ok "配了镜像: 真的同步了 wiki/" || bad "配了镜像: 缺 wiki/"
[[ -f "$VAULT/wiki/dashboards/review.md" ]] \
  && ok "配了镜像: 同步了复盘清单" || bad "配了镜像: 缺复盘清单"
grep -q 'wiki/dashboards/review' "$VAULT/LLM Wiki Mirror.md" \
  && ok "配了镜像: 入口页链接复盘清单" || bad "配了镜像: 入口页缺复盘链接"
grep -q 'raw/inbox/auto-drafts' "$VAULT/wiki/dashboards/health.md" \
  && bad "配了镜像: health 误把 ignored auto-drafts 当待编译 raw" \
  || ok "配了镜像: health 遵从 gitignore 排除 auto-drafts"

# ---------- 3. 真故障不得被吞 ----------
# 指向一个必然写不进去的路径，obsidian-sync 应真失败且 refresh 要传播出去
run env LLM_WIKI_MIRROR=/proc/definitely-not-writable "$REPO/bin/llm-wiki-refresh" >/dev/null 2>&1 && rc=0 || rc=$?
[[ "$rc" -ne 0 ]] && ok "真故障: 仍非 0 退出（rc=$rc）" || bad "真故障: 被静默吞掉了"

# ---------- 4. refresh 不得依赖调用方 PATH 才能找到同仓 sibling ----------
# Windows 通过 `wsl.exe -e /absolute/path` 直调时不加载 login shell，PATH 通常
# 不含 ~/.local/bin。refresh 必须从自身真实路径解析 obsidian-sync。
PATHLESS_VAULT="$WORK/pathless-vault"
mkdir -p "$PATHLESS_VAULT"
out=$(env HOME="$HOME_DIR" LLM_WIKI_ROOT="$HOME_DIR/llm-wiki" \
    LLM_WIKI_MIRROR="$PATHLESS_VAULT" PATH=/usr/bin:/bin \
    "$REPO/bin/llm-wiki-refresh" 2>&1) && rc=0 || rc=$?
[[ "$rc" -eq 0 ]] && ok "精简 PATH: refresh 能解析 sibling 命令" || bad "精简 PATH: refresh rc=$rc ($out)"
[[ -f "$PATHLESS_VAULT/SCHEMA.md" ]] \
  && ok "精简 PATH: 镜像真的同步" || bad "精简 PATH: 镜像未同步"

# ---------- 5. 路径解析：写死 ~/.local/bin 会导致静默不刷新 ----------
# llm-wiki-note 曾把 refresh 路径写死成 ~/.local/bin/llm-wiki-refresh，
# 自定义 LLM_WIKI_BIN_TARGET 时该路径不存在 -> 笔记落盘但派生页永不更新，
# 且 rc=0 无任何提示。这是静默的部分失败，比报错更难发现。
CUSTOM_HOME="$WORK/hcustom"
mkdir -p "$CUSTOM_HOME"
env HOME="$CUSTOM_HOME" LLM_WIKI_BIN_TARGET="$CUSTOM_HOME/custom-bin" \
    LLM_WIKI_OPENCODE_PLUGIN_TARGET="$CUSTOM_HOME/.opencode/plugins" \
    bash "$REPO/install.sh" --init >/dev/null 2>&1 || true
env HOME="$CUSTOM_HOME" LLM_WIKI_BIN_TARGET="$CUSTOM_HOME/custom-bin" \
    PATH="$CUSTOM_HOME/custom-bin:$PATH" \
    "$CUSTOM_HOME/custom-bin/llm-wiki-note" "custom bin regression" >/dev/null 2>&1 || true
[[ -f "$CUSTOM_HOME/llm-wiki/Today.md" ]] \
  && ok "自定义 BIN_TARGET: 派生页真的刷新了" || bad "自定义 BIN_TARGET: 派生页未刷新（静默失败）"

# ---------- 6. LLM_WIKI_ROOT 必须被尊重 ----------
# llm-wiki-note 曾无视 LLM_WIKI_ROOT，把笔记写进默认库 —— 写错仓库且不报错。
ALT_HOME="$WORK/halt"
mkdir -p "$ALT_HOME"
env HOME="$ALT_HOME" "$REPO/bin/llm-wiki-init" --git >/dev/null 2>&1 || true
env HOME="$ALT_HOME" LLM_WIKI_ROOT="$ALT_HOME/alt-root" "$REPO/bin/llm-wiki-init" --git >/dev/null 2>&1 || true
env HOME="$ALT_HOME" LLM_WIKI_ROOT="$ALT_HOME/alt-root" \
    "$REPO/bin/llm-wiki-note" "alt root regression" >/dev/null 2>&1 || true
[[ -n "$(ls -A "$ALT_HOME/alt-root/raw/notes" 2>/dev/null)" ]] \
  && ok "LLM_WIKI_ROOT: 写进了指定库" || bad "LLM_WIKI_ROOT: 未写进指定库"
[[ -z "$(ls -A "$ALT_HOME/llm-wiki/raw/notes" 2>/dev/null)" ]] \
  && ok "LLM_WIKI_ROOT: 未误写进默认库" || bad "LLM_WIKI_ROOT: 误写进了默认库"

# ---------- 7. --no-sync 不受影响 ----------
run "$REPO/bin/llm-wiki-refresh" --no-sync >/dev/null 2>&1 && rc=0 || rc=$?
[[ "$rc" -eq 0 ]] && ok "--no-sync rc=0" || bad "--no-sync rc=$rc"

printf '\n--- 结果: PASS=%s FAIL=%s ---\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
