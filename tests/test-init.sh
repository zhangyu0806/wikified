#!/usr/bin/env bash
# llm-wiki-init 引导能力回归测试。
#
# 为什么存在：实测隔离 HOME（陌生人真实处境）下，缺 SCHEMA.md 会让
# llm-wiki-health / promote-notes / refresh 全部 rc=1，llm-wiki-dedupe-events rc=2；
# 而 note / event / correct / govern 只会各自 mkdir 自己那一个子目录，
# 没有任何工具会产出 SCHEMA.md 或 git 仓库。结果是装完即不可用。
# 本测试锁定 llm-wiki-init 必须一次性补齐这些前置，且可重复运行不破坏已有内容。
set -euo pipefail

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
INIT="$REPO/bin/llm-wiki-init"
PASS=0
FAIL=0
ok()  { PASS=$((PASS+1)); printf 'PASS  %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf 'FAIL: %s\n' "$1"; }

WORK=$(mktemp -d "${TMPDIR:-/tmp}/wikiinit.XXXXXX")
trap 'rm -rf "$WORK"' EXIT

if [[ ! -x "$INIT" ]]; then
  bad "bin/llm-wiki-init 不存在或不可执行"
  bad "manifest: 无法验证（init 缺失）"
  bad "idempotency: 无法验证（init 缺失）"
  bad "no-clobber: 无法验证（init 缺失）"
  bad "hard-fail eliminated: 无法验证（init 缺失）"
  bad "template resolution: 无法验证（init 缺失）"
  printf '\n--- 结果: PASS=%s FAIL=%s ---\n' "$PASS" "$FAIL"
  exit 1
fi

# ---------- 1. manifest ----------
H1="$WORK/h1"; mkdir -p "$H1"
if env HOME="$H1" "$INIT" --git >/dev/null 2>&1; then
  ok "init 在空 HOME 下 rc=0"
else
  bad "init 在空 HOME 下非 0 退出"
fi
R="$H1/llm-wiki"

# Tier A：消除硬失败的必需项
for p in SCHEMA.md .gitattributes .gitignore .secret-allowlist .githooks/pre-commit; do
  [[ -e "$R/$p" ]] && ok "manifest: $p" || bad "manifest: 缺 $p"
done
[[ -d "$R/.git" ]] && ok "manifest: .git 已初始化" || bad "manifest: 缺 .git"
if [[ -d "$R/.git" ]] && [[ "$(git -C "$R" config --get core.hooksPath 2>/dev/null)" == ".githooks" ]]; then
  ok "manifest: core.hooksPath=.githooks"
else
  bad "manifest: core.hooksPath 未设为 .githooks（凭据门禁不生效）"
fi
[[ -x "$R/.githooks/pre-commit" ]] && ok "manifest: pre-commit 可执行" || bad "manifest: pre-commit 不可执行"

# Tier B：消除静默降级的目录
for d in wiki wiki/context wiki/dashboards wiki/projects wiki/concepts wiki/decisions wiki/tools wiki/queries \
         raw raw/sessions raw/notes raw/articles raw/inbox memory memory/events; do
  [[ -d "$R/$d" ]] && ok "manifest: $d/" || bad "manifest: 缺目录 $d/"
done
for f in wiki/index.md wiki/context/CRITICAL_FACTS.md wiki/context/active-projects.md wiki/context/open-loops.md; do
  [[ -f "$R/$f" ]] && ok "manifest: $f" || bad "manifest: 缺 $f"
done
[[ -f "$R/raw/inbox/corrections.jsonl" ]] && ok "manifest: corrections.jsonl（使 merge=union 从首个 commit 起生效）" \
  || bad "manifest: 缺 raw/inbox/corrections.jsonl"

# ---------- 2. 硬失败已消除（实测过的四个）----------
run_rc() { local o; o=$(env HOME="$H1" timeout 20 "$@" 2>&1); printf '%s' "$?"; }
[[ "$(run_rc python3 "$REPO/bin/llm-wiki-health" --json)" == "0" ]] \
  && ok "hard-fail: health --json rc=0" || bad "hard-fail: health --json 仍非 0"
[[ "$(run_rc python3 "$REPO/bin/llm-wiki-promote-notes" --json)" == "0" ]] \
  && ok "hard-fail: promote-notes --json rc=0" || bad "hard-fail: promote-notes --json 仍非 0"
[[ "$(run_rc python3 "$REPO/bin/llm-wiki-refresh" --no-sync)" == "0" ]] \
  && ok "hard-fail: refresh --no-sync rc=0" || bad "hard-fail: refresh --no-sync 仍非 0"
[[ "$(run_rc python3 "$REPO/bin/llm-wiki-dedupe-events")" == "0" ]] \
  && ok "hard-fail: dedupe-events rc=0" || bad "hard-fail: dedupe-events 仍非 0"
if env HOME="$H1" timeout 20 bash "$REPO/bin/llm-wiki-remote-sync" --status 2>&1 | grep -q '不是 git 仓库'; then
  bad "hard-fail: remote-sync 仍报『不是 git 仓库』"
else
  ok "hard-fail: remote-sync 不再报『不是 git 仓库』"
fi

# health --json 必须是合法 JSON，不只是 rc=0
if env HOME="$H1" timeout 20 python3 "$REPO/bin/llm-wiki-health" --json 2>/dev/null | python3 -c 'import json,sys;json.load(sys.stdin)' 2>/dev/null; then
  ok "hard-fail: health --json 输出合法 JSON"
else
  bad "hard-fail: health --json 输出非合法 JSON"
fi

# ---------- 3. 幂等 ----------
# 必须在独立 HOME 上验证：上面的 hard-fail 检查会让 refresh 合法生成
# wiki/dashboards/*.md 等派生内容（未被 gitignore，属应由用户提交的 wiki 内容），
# 在同一个 HOME 上断言「worktree 干净」会把别人的正常产物记到 init 账上。
HI="$WORK/hi"; mkdir -p "$HI"
env HOME="$HI" "$INIT" --git >/dev/null 2>&1 || true
RI="$HI/llm-wiki"
[[ "$(git -C "$RI" log --oneline 2>/dev/null | wc -l)" == "1" ]] \
  && ok "idempotency: 首次 --git 产出 1 个 commit（库可同步）" \
  || bad "idempotency: 首次 --git 未产出首个 commit"

second=$(env HOME="$HI" "$INIT" --git 2>&1) || true
if [[ "$(grep -c '^created:' <<<"$second")" == "0" ]]; then
  ok "idempotency: 第二次运行零 created"
else
  bad "idempotency: 第二次运行仍有 created 行"
fi
if [[ -z "$(git -C "$RI" status --porcelain 2>/dev/null)" ]]; then
  ok "idempotency: 第二次运行后 worktree 干净"
else
  bad "idempotency: 第二次运行产生了未提交改动"
  git -C "$RI" status --porcelain | sed 's/^/        /'
fi

# ---------- 4. 不覆盖既有内容 ----------
SENTINEL="__sentinel_$(date +%s)__"
printf '\n%s\n' "$SENTINEL" >> "$R/SCHEMA.md"
env HOME="$H1" "$INIT" --git >/dev/null 2>&1 || true
grep -q "$SENTINEL" "$R/SCHEMA.md" \
  && ok "no-clobber: 用户对 SCHEMA.md 的修改被保留" \
  || bad "no-clobber: 用户修改被覆盖"

# ---------- 5. 模板解析三种布局 ----------
H2="$WORK/h2"; mkdir -p "$H2"
if env HOME="$H2" "$INIT" >/dev/null 2>&1 && [[ -f "$H2/llm-wiki/SCHEMA.md" ]]; then
  ok "template: 就地从 bin/ 运行可解析 templates"
else
  bad "template: 就地运行无法解析 templates"
fi

H3="$WORK/h3"; mkdir -p "$H3/mybin"
ln -s "$INIT" "$H3/mybin/llm-wiki-init"
if env HOME="$H3" LLM_WIKI_BIN_TARGET="$H3/mybin" "$H3/mybin/llm-wiki-init" >/dev/null 2>&1 \
   && [[ -f "$H3/llm-wiki/SCHEMA.md" ]]; then
  ok "template: 经软链 + LLM_WIKI_BIN_TARGET 可解析"
else
  bad "template: 经软链无法解析 templates"
fi

H4="$WORK/h4"; mkdir -p "$H4"
if env HOME="$H4" LLM_WIKI_TEMPLATES="$REPO/templates" "$INIT" >/dev/null 2>&1 \
   && [[ -f "$H4/llm-wiki/SCHEMA.md" ]]; then
  ok "template: LLM_WIKI_TEMPLATES 显式指定可解析"
else
  bad "template: LLM_WIKI_TEMPLATES 无效"
fi

# 模板不可解析时必须给出点名 LLM_WIKI_TEMPLATES 的可行动报错，而不是静默写残库
H5="$WORK/h5"; mkdir -p "$H5"
errout=$(env HOME="$H5" LLM_WIKI_TEMPLATES=/nonexistent "$INIT" 2>&1 || true)
if grep -q 'LLM_WIKI_TEMPLATES' <<<"$errout"; then
  ok "template: 无法解析时报错点名 LLM_WIKI_TEMPLATES"
else
  bad "template: 无法解析时未给出可行动报错"
fi

# ---------- 6. --dry-run 不落盘 ----------
H6="$WORK/h6"; mkdir -p "$H6"
env HOME="$H6" "$INIT" --dry-run >/dev/null 2>&1 || true
if [[ ! -e "$H6/llm-wiki" ]]; then
  ok "dry-run: 未创建任何内容"
else
  bad "dry-run: 竟然落盘了"
fi

printf '\n--- 结果: PASS=%s FAIL=%s ---\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
