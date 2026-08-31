# Wikified 数据仓同步

本文件由 `llm-wiki-init` 安装到数据仓根目录。Wikified 工具仓与记忆数据仓是两条
独立的 Git 链：工具仓可以公开，数据仓应使用由数据所有者控制的私有 remote。

## 三层模型

1. **持久源数据（提交并同步）**
   - `wiki/`、经检查并脱敏的 `raw/`、`memory/events/`、`policy/`
   - `SCHEMA.md`、`SYNC.md`、`.gitattributes`、`.gitignore`
2. **可再生或机器本地状态（不提交）**
   - 仪表盘、图谱输出、锁、缓存、绝对路径索引和编辑器临时文件
   - 这些内容由各机器重新生成；同步它们只会制造冲突
3. **未经人审的敏感暂存（不提交）**
   - 例如 `raw/inbox/auto-drafts/`
   - 先人工检查、删除凭据并移入受管的 `raw/` 或 `wiki/`，再显式提交

`.gitignore` 实现后两层边界。不要为了“让所有文件都同步”而直接移除这些规则；先确认
文件属于不可再生、已审查且适合进入私有 remote 的持久数据。

## 支持的传输方式

正式支持的双向传输是私有 Git remote。不要用 Syncthing、Dropbox、OneDrive、iCloud
等文件同步工具直接双向同步活跃工作树或 `.git/`：它们不执行 `.gitignore`、
`merge=union`、JSONL 去重或 Wikified 的锁，并发写入不承诺收敛。若只用于额外备份，
应采用单向、单写者模式，并在该工具中另行排除 `.gitignore` 列出的本地态及
`raw/inbox/auto-drafts/`。

## 首台机器

```bash
llm-wiki-init --root "$HOME/llm-wiki" --git --profile "<machine-id>"
cd "$HOME/llm-wiki"
git remote add origin "<private-data-remote>"
git branch -M main
git push -u origin main
```

`<private-data-remote>` 是你自己创建并限制访问的私有仓库；不要把真实记忆推送到公开
Wikified 工具仓。remote 凭据交给 Git/SSH 凭据管理，不写进本仓文件。

## 其他机器

```bash
git clone "<private-data-remote>" "$HOME/llm-wiki"
llm-wiki-init --root "$HOME/llm-wiki" --git --profile "<machine-id>"
llm-wiki-remote-sync --status
```

`--profile` 只写机器本地缓存，不进入 Git。已有数据仓应先 clone，再对该路径运行幂等
初始化以补齐缺失模板和本机 Git hook 配置；已有 `.git` 与文件都不会被覆盖。

## 日常同步规则

```bash
git status
git add "<reviewed-paths>"
git commit -m "docs: update memory"
llm-wiki-remote-sync             # 受锁与节流保护的双向同步
# llm-wiki-remote-sync --force   # 明确需要忽略节流时
```

- 同步器只处理已经提交的变更；工作区脏、分支错误、冲突或未完成 merge/rebase 时安全停止。
- 默认使用 `main` 与 `origin/main`。首次 push 后用 `git branch -vv` 确认 upstream。
- append-only JSONL 由 `.gitattributes` 使用 `merge=union`；合并后仍应运行相应去重/检查命令。
- 同步器不会执行 `reset --hard`、`clean` 或覆盖未知改动，也不会替你创建系统定时器。
- 遇到冲突先保留现场并人工解决；不要用强制推送掩盖另一台机器的提交。
