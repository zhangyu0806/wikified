# 每日自动同步

默认关闭。开启后每天自动把记忆数据提交，并与你的私有 Git 数据仓做完整双向同步。

这套定时任务独立于具体 agent：OpenCode、Codex、Claude Code 等只要指向同一个
`LLM_WIKI_ROOT`，都会共享同步后的数据。它不会上传本公有工具仓；每个人都应该把
自己的真实记忆放在另一个私有仓库中。

## 两台机器的推荐结构

```text
GitHub 公有 wikified       工具与公开文档，每台机器正常 git pull
GitHub 私有 memory repo    个人 Markdown/JSONL 数据，自动双向同步
        ├── home:   ~/llm-wiki
        └── office: ~/llm-wiki
```

第一台机器先创建并推送私有数据仓：

```bash
git clone https://github.com/zhangyu0806/wikified ~/wikified
cd ~/wikified
./install.sh --init
git -C ~/llm-wiki remote add origin <你的私有仓库 URL>
git -C ~/llm-wiki push -u origin main
```

第二台及后续机器直接把同一个私有仓库克隆到数据根：

```bash
git clone https://github.com/zhangyu0806/wikified ~/wikified
git clone <你的私有仓库 URL> ~/llm-wiki
cd ~/wikified
./install.sh
```

不要把 token 写进 URL、配置示例、note 或 wiki；使用 Git credential helper、SSH agent
或 GitHub CLI 提供的认证。

## 为什么默认关闭

`llm-wiki-remote-sync` 刻意不自动提交，注释里写的是「提交需显式意图」。这条边界
是有意义的：自动 commit 意味着任何写进白名单目录的内容都会在无人看的情况下进入
git 历史。

`llm-wiki-auto-commit` 承担那份「显式意图」，但把风险约束回可接受范围的方式是
**强制密钥扫描门禁**，而不是取消边界。所以它需要你显式打开。

## 开启

```bash
mkdir -p ~/.config/wikified
echo enabled > ~/.config/wikified/auto-commit.enabled
mkdir -p ~/.cache/llm-wiki-sync
echo home > ~/.cache/llm-wiki-sync/profile   # 另一台可写 office
cd ~/wikified
./install.sh
```

`install.sh` 的 `[5/6]` 步骤会渲染 systemd user unit 到
`~/.config/systemd/user/` 并启用 timer。删掉开关文件即恢复默认关闭（已装的
timer 需手动 `systemctl --user disable --now llm-wiki-auto-commit.timer`）。

## 安全边界

自动提交只有在**全部**满足时才会发生：

| 门禁 | 行为 |
|---|---|
| 路径白名单 | 只暂存 `memory/events` `raw/sessions` `raw/notes` `raw/inbox` `wiki`。绝不 `git add -A` |
| 跳过派生物 | 被 `.gitignore` 排除的路径（`Today.md`、仪表盘缓存等可再生产物）静默跳过 |
| 密钥扫描 | 暂存后跑 `llm-wiki-secret-scan --staged`。不通过则全部 unstage 并 rc=1，工作区文件逐字节保持原样 |
| 扫描器必须存在 | 找不到 `llm-wiki-secret-scan` 直接 fatal。绝不「跳过扫描继续提交」 |
| 不抢人工提交 | 已有暂存内容时中止，避免把你手工组织的暂存混进自动 commit |
| 分支与状态检查 | detached HEAD、非目标分支、未完成的 merge/rebase/cherry-pick 一律拒绝 |
| 非阻塞 flock | 并发触发只有一个真干活，其余瞬退 |

`.secret-allowlist` **不在**白名单内——放宽扫描门禁必须是人工显式行为。

被门禁拦截时不会丢任何数据，只是不提交。测试
`tests/test-auto-commit.sh` 逐项验证了以上行为，包括「被拦截的文件在工作区
逐字节未变」。

## 用法

```bash
llm-wiki-auto-commit --status     # 只读：看白名单内有什么待提交
llm-wiki-auto-commit --dry-run    # 打印将要做什么，零写入
llm-wiki-auto-commit              # 扫描并提交，不推送
llm-wiki-auto-commit --push       # 只推送；兼容/单机模式
llm-wiki-auto-commit --sync       # 提交后完整 fetch/merge/dedupe/push，多机推荐
```

每日 timer 使用 `--sync`。它先完成安全自动提交，再调用 `remote-sync --force`，因此
远端已有另一台机器的提交时会先合并；直接 `--push` 不具备这项多机收敛保证。

## 接入 OpenCode、Codex 等客户端

同步任务本身不依赖客户端。每台机器安装后，再显式配置实际使用的 harness：

```bash
llm-wiki-harness configure --harness opencode
llm-wiki-harness configure --harness codex
llm-wiki-harness status --json
```

Windows Codex Desktop 与 WSL Codex CLI 通常使用不同的 Codex home。Windows Desktop
需要把 MCP 指向 WSL 的 `llm-wiki-mcp`，并合并 `templates/codex/hooks.json`；详见
README 的“接进 Codex”。同一台电脑上的 OpenCode 与 Codex 不需要互相复制数据，
它们都读写 `~/llm-wiki`。

## 排查

```bash
systemctl --user list-timers llm-wiki-auto-commit.timer
systemctl --user start llm-wiki-auto-commit.service      # 手动触发一次
journalctl --user -u llm-wiki-auto-commit.service -n 50
cat ~/.cache/llm-wiki-sync/auto-commit.log               # 逐阶段日志
```

### 代理

公有 service 模板不预设代理。需要代理时在每台机器创建本地文件：

```bash
mkdir -p ~/.config/wikified
printf '%s\n' \
  'http_proxy=http://127.0.0.1:7897' \
  'https_proxy=http://127.0.0.1:7897' \
  > ~/.config/wikified/auto-sync.env
systemctl --user restart llm-wiki-auto-commit.timer
```

把 `7897` 换成本机实际端口；不需要代理就不要创建该文件。该文件是机器本地配置，
不得放进私有记忆仓或公有工具仓。

### WSL 关机会不会漏

timer 设了 `Persistent=true`，错过的周期在 WSL/user systemd 下次可用时补跑。
`loginctl enable-linger` 只能保持 Linux 用户服务，不保证 Windows 一定在 22 点启动
WSL。必须准点运行时，应额外用 Windows 任务计划程序调用：

```text
wsl.exe -d Ubuntu -- systemctl --user start llm-wiki-auto-commit.service
```

### 没有 systemd 的环境

`install.sh` 会跳过并提示。用 cron 替代：

```cron
0 22 * * * $HOME/.local/bin/llm-wiki-auto-commit --sync >/dev/null 2>&1
```

cron 没有 `Persistent` 语义，机器关机期间错过的周期不会补跑。

## 范围

只覆盖私有数据仓（`LLM_WIKI_ROOT`，默认 `~/llm-wiki`）。公开工具链仓库
**不做**自动提交——公开仓的误入内容会直接公开，风险性质不同。

## 验收清单

每台机器都执行：

```bash
git -C ~/llm-wiki status -sb
llm-wiki-remote-sync --status
llm-wiki-auto-commit --status
llm-wiki-auto-commit --dry-run
systemctl --user is-enabled llm-wiki-auto-commit.timer
systemctl --user is-active llm-wiki-auto-commit.timer
```

再分别在两台机器新增一条无敏感信息的测试 note，手动运行
`llm-wiki-auto-commit --sync`，确认双方执行 `llm-wiki-remote-sync --force` 后都能看到
两条记录。普通 Markdown 同行冲突仍需人工解决；JSONL 追加冲突由 union merge 与
确定性去重处理。
