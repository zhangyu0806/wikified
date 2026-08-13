# wikified

> A file-based, cross-ecosystem memory layer for coding agents. Plain CLIs, agent skills,
> and an MCP server over an ordinary Markdown/JSONL git repo. No database, no vendor lock-in.
>
> **Note on language:** the code, CLI flags and command names are English, but the bundled
> skill files, the `SCHEMA.md` template and most CLI help text are in Chinese — the agent
> reads them, and that is the language the author works in. The prose below is Chinese for
> the same reason. The tooling itself is language-agnostic.

给编码 agent 用的**文件式跨生态记忆层**。底座是一个普通的 Markdown / JSONL git 仓库，
上面是一组 CLI、三个 agent skill 和一个 MCP server。没有数据库，没有厂商绑定。

核心主张：**知识是编译出来的，不是检索出来的。**
原始材料落 `raw/`，经人工审校编译进 `wiki/`，轻量事实走 `memory/events/` 的 typed event。
写入靠手动触发而非全量自动捕获——这是为了保信噪比，不是为了省事。

思路上受 Andrej Karpathy 关于 "LLM wiki" 的讨论启发，但本仓库是独立实现，
与其没有关联，也不代表其观点。

**本仓库只有机制，不含任何个人内容**——没有 `wiki/`、`raw/`、`memory/` 目录。
你的记忆库是另一个仓库（通常设为私有），由 `llm-wiki-init` 创建。

---

## 目录

- [它解决什么问题](#它解决什么问题)
- [安装](#安装)
- [五分钟上手](#五分钟上手)
- [命令清单](#命令清单)
- [各 Agent 的支持程度](#各-agent-的支持程度)
- [接进 Codex](#接进-codex)
- [事件生命周期与召回评测](#事件生命周期与召回评测)
- [接进 OpenCode](#接进-opencode)
- [MCP tools](#mcp-tools)
- [记忆库结构](#记忆库结构)
- [跨机同步](#跨机同步)
- [Obsidian 阅读层](#obsidian-阅读层)
- [凭据安全](#凭据安全)
- [配置](#配置)
- [排查](#排查)
- [卸载](#卸载)
- [FAQ](#faq)
- [依赖](#依赖)
- [测试](#测试)
- [参与](#参与)

---

## 它解决什么问题

编码 agent 每次会话都从零开始。你上周踩过的坑、定过的架构、纠正过的偏好，
下次它一概不知，于是你重复解释、它重复犯错。

常见做法是把整段对话喂回上下文。问题是信噪比会崩：agent 淹在无关细节里，
反而更难抓住要点，token 也白烧。

这套工具的做法是**分层沉淀**：

```
raw/            原始材料，不可变。会话摘要、快速笔记、待处理草稿
  ↓ 人工审校后编译
wiki/           长期知识。项目页、概念页、决策记录，页面间用 [[wikilink]] 互连
memory/events/  typed event。一行一条 JSONL，记 bug / decision / preference
```

读取端同样分层：会话启动只注入极简的关键事实卡，具体细节按需召回。

---

## 安装

```bash
git clone https://github.com/zhangyu0806/wikified ~/wikified
cd ~/wikified
./install.sh --init
```

这条命令做两件事：

1. **装工具链** — 16 个 CLI 软链进 `~/.local/bin`，agent skills 分发到各 Agent 目录，
   OpenCode 召回插件软链进 `~/.opencode/plugins`
2. **`--init` 建记忆库** — 在 `~/llm-wiki` 建出目录骨架、`SCHEMA.md`、git 仓库与凭据门禁，
   并落一个首个 commit

装完立刻验证：

```bash
llm-wiki-health --json     # 应 rc=0 并输出合法 JSON
```

> **`--init` 不是可选的。** 其余命令都假定记忆库已存在。缺 `SCHEMA.md` 时
> `llm-wiki-health` / `llm-wiki-promote-notes` / `llm-wiki-refresh` 以 rc=1 退出，
> `llm-wiki-dedupe-events` rc=2，`llm-wiki-remote-sync` 缺 `.git` 会中止。
> 已有记忆库的话省掉 `--init`。

**其他安装模式**

```bash
./install.sh --dry-run   # 只打印将要做什么，不落盘
./install.sh --check     # 校验现有安装是否与本 repo 一致，可当 CI 门禁
./install.sh             # 装工具链，但不建记忆库
```

`install.sh` 是**幂等**的，重复运行零改动。它只创建软链、不复制文件，
所以 `git pull` 之后工具立即是新版，无需重装。

`~/.local/bin` 不在 `PATH` 里的话，脚本会提示。

---

## 五分钟上手

```bash
# 记一条零散事实 / 命令 / 坑
llm-wiki-note "WSL 里 cron 不可靠，改用会话启动节流"

# 记 typed event（会进按需召回）
llm-wiki-event --type decision "MCP 传输统一走 NDJSON，Codex 只认这个"
llm-wiki-event --type bug      "secret-scan 的词边界在 db_password 上失效"

# 用户纠正了你的做法 —— 进待人审队列，不直接改 wiki
llm-wiki-correct "以后结论用中文" --kind preference

# 召回
llm-wiki-enrich --session-start        # 关键事实 + 活跃项目 + 未闭环事项
llm-wiki-enrich --query "secret-scan"  # 针对具体主题的定向召回

# 离线、无用户数据地量化召回质量与安全边界
llm-wiki-eval --json

# 体检：陈旧页、孤儿页、断链、未编译的 raw
llm-wiki-health --json

# 看哪些 quick note 值得晋升进 wiki（只读建议，不自动改）
llm-wiki-promote-notes --json
```

每个命令都有 `--help`。

**典型工作流**：平时用 `note` / `event` / `correct` 随手记；定期跑 `promote-notes`
看建议，人工决定哪些值得编译进 `wiki/`；任务开始前用 `enrich` 召回。
晋升永远经人审——这是信噪比的来源。

---

## 命令清单

**写入**

| 命令 | 用途 |
|---|---|
| `llm-wiki-note` | 记零散事实 / 命令 / 坑到 `raw/notes/` |
| `llm-wiki-event` | 记 typed event（`fact` / `decision` / `bug` / `preference` …） |
| `llm-wiki-correct` | 记录用户的纠正与偏好，入待人审队列 |

**读取**

| 命令 | 用途 |
|---|---|
| `llm-wiki-enrich` | 混合词法召回。`--session-start` 取极简卡片，`--query` 定向检索 |
| `llm-wiki-eval` | 生成隔离夹具，比较 hybrid / legacy Recall@5 与 MRR，并检查安全边界 |
| `llm-wiki-health` | 结构体检：陈旧页、孤儿页、断链、未编译 raw |
| `llm-wiki-promote-notes` | 建议哪些 quick note 值得晋升（只读，不自动改） |

**维护**

| 命令 | 用途 |
|---|---|
| `llm-wiki-init` | 从零建出记忆库 |
| `llm-wiki-refresh` | 重建派生页（`Today.md`、仪表盘、健康检查） |
| `llm-wiki-govern` | 带节流的周期治理，适合挂在会话启动（命中节流即秒退） |
| `llm-wiki-dedupe-events` | 按 event id 去重 append-only jsonl |
| `llm-wiki-secret-scan` | 凭据扫描，装作 pre-commit hook |

**同步与外部**

| 命令 | 用途 |
|---|---|
| `llm-wiki-remote-sync` | 带节流的多机双向同步 |
| `llm-wiki-obsidian-sync` | 镜像成 Obsidian 可直接打开的 vault |
| `wiki-graph` | 打开 Graphify 知识图谱（需另装 `graphify`） |
| `llm-wiki-mcp` | MCP server，把上述能力暴露给任意 MCP 客户端 |

> 命令名保留 `llm-wiki-` 前缀，与仓库名 `wikified` 不同。这是有意的：
> 改前缀会打断既有安装，也会让已沉淀的知识记录失真。

---

## 各 Agent 的支持程度

| 能力 | OpenCode | Codex | Claude Code | 其他 MCP 客户端 |
|---|---|---|---|---|
| 16 个 CLI | 是 | 是 | 是 | 是（能跑 shell 即可） |
| Agent skills | 是 | 是 | 是 | 看是否支持 `SKILL.md` |
| MCP（6 个 tool） | 是 | 是 | 是 | 是 |
| **会话启动自动注入** | **是** | **是（SessionStart hook）** | 依客户端配置 | 依客户端配置 |

OpenCode 由 `chat.message` 插件注入；当前 Codex 已正式支持 `SessionStart` hook，
`templates/codex/hooks.json` 同时给出 Linux 与 Windows→WSL 命令。仍保留 MCP、
`AGENTS.md` 与手动 prompt：hook 是低成本常驻摘要，具体主题继续按需检索。

---

## 接进 Codex

### Linux / WSL 内运行 Codex CLI

```bash
# 1. 注册 MCP（注意 -- 分隔符，且用绝对路径）
codex mcp add llm-wiki -- ~/.local/bin/llm-wiki-mcp
codex mcp list                      # 确认 Status=enabled

# 2. 合并 SessionStart hook；已有 hooks.json 时不要直接覆盖
mkdir -p ~/.codex
cp templates/codex/hooks.json ~/.codex/hooks.json

# 3. 保留按需召回指令
cat templates/codex/AGENTS.recall.md >> ~/.codex/AGENTS.md
cp templates/codex/prompts/llm-wiki-recall.md ~/.codex/prompts/
```

非托管 hook 首次运行前必须在 Codex 的 `/hooks` 页面审查并信任；hook 内容变化后
hash 会变化，需要重新审查。`matcher` 覆盖 `startup / resume / clear / compact`，
所以压缩上下文后也会重新注入极简摘要。

### Windows Codex Desktop 读取 WSL 记忆库

在 Desktop 实际使用的 `%CODEX_HOME%\config.toml` 中注册 WSL MCP；不要默认它与
WSL 的 `~/.codex` 是同一个目录：

```toml
[mcp_servers.llm-wiki-wsl]
command = "wsl.exe"
args = ["-d", "Ubuntu", "--", "/home/<linux-user>/llm-wiki/bin/llm-wiki-mcp"]
startup_timeout_sec = 20
tool_timeout_sec = 60
```

把 `templates/codex/hooks.json` **合并**到同一 `%CODEX_HOME%\hooks.json`。
其中 `commandWindows` 通过默认 WSL distro 执行；默认 distro 不是记忆库所在 distro
时，在 `wsl.exe` 后加入 `-d <distro>`。保存后重启 Desktop，并在 `/hooks` 审查 hook。

`AGENTS.recall.md` 仍有价值：它告诉 agent 何时做主题检索；
`prompts/llm-wiki-recall.md` 是确定性的人工入口。hook 只负责 3500 字符以内、已脱敏、
去掉“下一步”任务语句的 session 摘要，不替代完整查询。

`install.sh` 只打印接线指引、**不覆盖**现有 `hooks.json` / `AGENTS.md` / prompts——
这些文件可能已有其他 hook 或手工规则，粗暴替换会吞掉用户配置。

Codex 用户级 skills 的首选路径就是 `~/.agents/skills`（`$CODEX_HOME/skills` 已标 deprecated），
`install.sh` 正是以它为主副本，且 Codex 会跟随软链。

### 与 Codex 原生 Memories 的边界

Codex 原生 Memories 是 `%CODEX_HOME%/memories/` 下的**生成状态**；本项目是可审查、
可 Git 同步、跨 Agent 的知识源。两者可以共存，但不要把原生 memories 目录提交到
记忆库，也不要让同一段对话被两套自动链路重复摄入。一个保守配置是：

```toml
[features]
memories = true

[memories]
disable_on_external_context = true
```

这样，使用 MCP / web 等外部上下文的聊天不会再进入 Codex 原生记忆生成；长期、
可携带知识仍由 LLM Wiki 管，人机习惯类的自动提醒可留给原生 Memories。是否开启
原生 Memories 是用户选择，不是安装脚本应暗改的全局设置。

---

## 事件生命周期与召回评测

事件 schema v2 保持 append-only，同时补上双时态里最实用的三件事：生效时间、
失效时间、替代关系。

```bash
# 未来才生效、到期后不再召回
llm-wiki-event --type fact --project billing \
  --valid-from 2026-09-01 --valid-until 2026-12-01 \
  "Q4 账单导出走新版接口"

# 用新事实替代同项目的旧事件；--supersedes 可重复
llm-wiki-event --type decision --project billing \
  --supersedes 0123456789abcdef \
  "账单主键改用 invoice_id"
```

替代目标必须存在、是 16 位小写 hex id、属于同一项目且不能形成环。旧事件不会被
改写或删除，但当前召回会隐藏它；未来事件和已过期事件也不会参与结果。事件的
`confidence` 与 `half_life_days` 只影响匹配结果的排序，不能让无关事件凭高置信度混入。

`llm-wiki-enrich` 的默认 `hybrid` 排名是纯标准库 BM25 风格词法排名，加标题/原短语
加分与 CJK 二、三元字符召回；它不声称等价于向量语义搜索，也不需要模型、网络、
数据库或 embedding 服务。用以下命令可复现实验，不读取真实记忆：

```bash
llm-wiki-eval --json
```

报告同时给出 hybrid 与保留的 `legacy` 基线 Recall@5 / MRR，并检查项目隔离、时间
衰减、替代与有效期、无关查询零上下文、凭据脱敏和有界合法 JSON。`legacy` 仅供
回归比较，不建议作为日常检索模式。

`llm-wiki-health` 现在也报告 `graph_status` / `graph_issues`：Graphify manifest 缺失、
损坏，或 wiki 输入比 manifest 更新时会明确标成 missing / invalid / stale。默认仍是
建议项；`--strict` 才把健康问题升级为非零退出码。

---

## 接进 OpenCode

```jsonc
// ~/.config/opencode/opencode.json
{
  "mcp": {
    "llm-wiki": {
      "type": "local",
      "command": ["~/.local/bin/llm-wiki-mcp"],
      "enabled": true
    }
  }
}
```

会话启动自动注入由 `plugins/llm-wiki-recall.js` 提供，`install.sh` 已软链进
`~/.opencode/plugins`。**改动插件后需重启 OpenCode 才生效。**

---

## MCP tools

`llm-wiki-mcp` 是零依赖的 Node 脚本，薄封装上面的 CLI：

| tool | 作用 |
|---|---|
| `search_pages` | 按自然语言查询检索记忆，可用 `project` 做项目隔离 |
| `read_page` | 按相对路径读某一页 |
| `find_related` | 经 Graphify 图谱找相关概念（需 `graphify`） |
| `list_recent_raw` | 列出待编译的原始材料 |
| `record_event` | 记 typed event，支持有效期与同项目 `supersedes` |
| `lint` | 跑健康检查 |

**传输层同时支持两种框架**：换行分隔 JSON（NDJSON，MCP stdio 规范，Codex 只认这个）
与 LSP 风格 `Content-Length` 帧。按入向首个非空字节自动判定，出向镜像入向。

这一点很实际：只支持 `Content-Length` 的 server 在 Codex 下会**静默挂起**
——无报错、无提示。`tests/test-mcp-framing.sh` 锁住这个不变式。

---

## 记忆库结构

```
~/llm-wiki/
├── SCHEMA.md              # 运行规则，agent 操作前必读
├── wiki/                  # 编译后的长期知识
│   ├── context/           #   会话启动注入的极简卡片
│   ├── dashboards/        #   派生仪表盘
│   └── projects|concepts|decisions|tools|queries/
├── raw/                   # 不可变源材料
│   ├── sessions|notes|articles/
│   └── inbox/             #   待人审队列（含 corrections.jsonl）
└── memory/events/         # append-only typed event（JSONL）
```

`SCHEMA.md` 是给 agent 看的操作契约，不是给人看的说明书。它规定什么该进 `raw/`、
什么该编译进 `wiki/`、页面怎么命名、链接怎么维护。`llm-wiki-init` 会放一份模板，
你应该按自己的习惯改它。

---

## 跨机同步

`llm-wiki-remote-sync` 做带节流的双向同步（默认 3 小时窗口）：

```bash
llm-wiki-remote-sync --status    # 看节流状态，不动数据
llm-wiki-remote-sync             # 到窗口才真跑，否则秒退
llm-wiki-remote-sync --force     # 忽略节流
llm-wiki-remote-sync --dry-run   # 预演
```

两个设计取舍值得说明：

**append-only 文件用 `merge=union`。** `memory/events/*.jsonl` 与
`raw/inbox/corrections.jsonl` 在 `.gitattributes` 里设了 `merge=union`，
两机各自追加时保留双方所有行，合并后由 `llm-wiki-dedupe-events` 按 event id 去重。
不这么做的话，每次跨机 pull 都要手工解 JSONL 冲突。

**绝不动未提交的改动。** 工作区脏时跳过合并并提示，不做 `reset --hard` /
`checkout -f` / `clean`——未知改动可能是另一个 agent 或你自己在编辑。
有未解决冲突时直接失败退出且**不写成功戳**，下个窗口自动重试。

---

## Obsidian 阅读层

```bash
export LLM_WIKI_MIRROR=~/Obsidian/LLM-wiki      # 或写进 config.env，见下
llm-wiki-obsidian-sync
```

把记忆库镜像成 Obsidian 能直接打开的 vault，并生成入口页。
维护仍以源库为准，镜像是只读视图。

镜像目标**没有可移植的默认值**（取决于你的 vault 放哪），解析优先级：

1. `LLM_WIKI_MIRROR` 环境变量
2. `~/.config/llm-wiki/config.env` 里的 `LLM_WIKI_MIRROR=...`
3. 都没有 → 报错并给出上述三条路，**不猜路径**

选配置文件而非 shell profile 是刻意的：cron / systemd / agent 子进程读不到 profile，
而这些恰是要用的场景。家里和公司的 vault 路径本就不同，属机器本地状态，不该跨机同步。

---

## 凭据安全

`llm-wiki-secret-scan` 作为 pre-commit hook 拦截凭据入库，两条规则：

- **厂商前缀指纹** — `sk-ant-` / `gsk_` / `ghp_` / `xoxb-` 等强信号
- **敏感键名紧邻高熵值** — `password` / `api_key` / `token` 等键名后跟高熵字符串

误报走 `.secret-allowlist`，存 fingerprint 而非明文。

排除项：占位符（`your_key_here`）、代码引用（`$VAR` / `os.environ[...]`）、
代码表达式（`re.compile(` / `list[`）、SHA-256 与 git SHA 摘要、低熵字符串。

**绝不把 token / key / password 写进 wiki、event 或 note。**
只记录「某凭据位于某处」，不记录值。

---

## 配置

| 变量 | 作用 |
|---|---|
| `LLM_WIKI_ROOT` | 记忆库位置（默认 `~/llm-wiki`） |
| `LLM_WIKI_MIRROR` | Obsidian 镜像目标，也可写进 `~/.config/llm-wiki/config.env` |
| `LLM_WIKI_BIN_TARGET` | CLI 安装位置（默认 `~/.local/bin`） |
| `LLM_WIKI_AGENT_SKILL_ROOT` | skills 主副本位置（默认 `~/.agents/skills`） |
| `LLM_WIKI_SKILL_FANOUT` | 空格分隔的分发目标；设为**空字符串**关闭分发 |
| `LLM_WIKI_OPENCODE_PLUGIN_TARGET` | OpenCode 插件位置 |
| `LLM_WIKI_TEMPLATES` | 显式指定 templates 目录 |
| `LLM_WIKI_SYNC_THROTTLE` | 同步节流窗口（秒） |
| `LLM_WIKI_GOVERN_THROTTLE` | 治理节流窗口（秒） |

skills 走「主副本 + 分发」：repo → `~/.agents/skills` → 各 Agent 目录。
这样多生态共享同一份 `SKILL.md`，不会各存一份导致分叉。
本机不存在的 Agent 目录会被跳过，且不计为偏差。

---

## 排查

**`llm-wiki-health` 报 `not an LLM Wiki root, missing SCHEMA.md`**
记忆库还没建。跑 `llm-wiki-init --git`。

**`llm-wiki-obsidian-sync` 报 `no mirror target`**
没配镜像目标。按报错给的三条路选一条，推荐写进 `~/.config/llm-wiki/config.env`。

**`install.sh --check` 报「链接指向他处」**
`--check` 校验的是「软链是否指向**本 repo**」。若你之前从别处装过同一套工具，
软链会指向那个位置，于是被报为偏差。跑 `./install.sh` 让它们指向本 repo，
或者接受现状——两处内容一致时功能不受影响。

**命令找不到**
`~/.local/bin` 不在 `PATH` 里。加进 shell 配置，或用 `LLM_WIKI_BIN_TARGET` 换位置重装。

**Codex 里 MCP 连不上、也没有任何报错**
八成是传输框架不匹配（静默挂起是其典型表现）。确认注册的路径含 NDJSON 支持：

```bash
grep -c FRAMING "$(readlink -f ~/.local/bin/llm-wiki-mcp)"    # 应 >0
bash tests/test-mcp-framing.sh                                # 应 10/10
```

**Codex 不主动召回记忆**
先确认 `SessionStart` hook 已放进当前 Codex 实际使用的 `hooks.json`，然后重启 Codex，
在 `/hooks` 审查并信任它。Windows Desktop 与 WSL CLI 可能使用不同的 Codex home，
不要只检查 WSL 的 `~/.codex`。`AGENTS.md` 召回块负责告诉 agent 何时做主题检索，
但不能代替 hook 本身：

```bash
python3 -m json.tool ~/.codex/hooks.json >/dev/null           # WSL/CLI
grep -c 'BEGIN llm-wiki-recall' ~/.codex/AGENTS.md            # 建议为 1
# Windows Desktop: 检查 %CODEX_HOME%\hooks.json，并确认 commandWindows 指向正确 distro
```

**跨机 pull 后 JSONL 有重复行**
预期行为，`merge=union` 保留双方所有行。跑 `llm-wiki-dedupe-events` 按 id 去重。

**`llm-wiki-remote-sync` 一直秒退**
命中节流窗口。`--status` 看剩余时间，`--force` 忽略节流。

---

## 卸载

`install.sh` 只创建软链、不复制文件，所以卸载就是删软链。**没有 `--uninstall` 模式**
——删除操作交给你自己确认，比脚本代劳更安全。

```bash
# 1. 删 CLI 软链
for f in ~/wikified/bin/*; do rm -f ~/.local/bin/"$(basename "$f")"; done

# 2. 删 OpenCode 插件软链
rm -f ~/.opencode/plugins/llm-wiki-recall.js

# 3. 删 skills 软链（主副本 + 各 Agent）
for s in QuickNote SessionCapture WikiCompiler; do
  rm -f ~/.agents/skills/"$s" \
        ~/.claude/skills/"$s" \
        ~/.config/opencode/skills/"$s" \
        ~/.codex/skills/"$s"
done

# 4. 注销 MCP
codex mcp remove llm-wiki
#    OpenCode 侧手工删掉 opencode.json 里的 mcp."llm-wiki" 块

# 5. 撤掉 Codex 召回块
#    手工删掉 ~/.codex/AGENTS.md 里 BEGIN/END llm-wiki-recall 之间的内容
rm -f ~/.codex/prompts/llm-wiki-recall.md
```

**你的记忆库 `~/llm-wiki` 不会被上述任何步骤删除**，那是你的数据。
确实要删的话自己 `rm -rf`——先确认已推到远端。

可选清理：`~/.config/llm-wiki/`（机器本地配置）、`~/.cache/llm-wiki-*`（节流戳与日志）。

---

## FAQ

**这和 Karpathy 的 "LLM wiki" 什么关系？**
思路受其启发，实现独立，与其没有关联。仓库不叫 `llm-wiki` 正是为了不冒领那个概念名。

**为什么仓库叫 `wikified`，命令却是 `llm-wiki-*`？**
改命令前缀会打断既有安装，也会让已沉淀的知识记录失真（那些记录写的是当时的命令名）。
名实略不一致，换来零破坏。

**必须用 Obsidian 吗？**
不用。`llm-wiki-obsidian-sync` 是可选的阅读层。记忆库本身就是普通 Markdown，
任何编辑器都能看。

**必须装 graphify 吗？**
不用。它只影响 `wiki-graph` 和 MCP 的 `find_related`，其余命令在没有它的环境下正常工作。

**能不用 MCP、只用 CLI 吗？**
可以。CLI 是完整的，MCP 只是把同样的能力换个接口暴露。

**为什么写入要手动触发，不自动捕获全部对话？**
自动全量捕获会让信噪比崩掉——agent 淹在无关细节里反而更难抓要点。
手动触发是这套设计的核心取舍，不是未实现的功能。

**多机同步会冲突吗？**
append-only 的 JSONL 走 `merge=union` 保留双方所有行，再按 event id 去重，
实测能收敛到字节一致。`wiki/` 正文是普通 Markdown，冲突按常规 git 方式解。

**记忆库应该设为私有吗？**
建议私有，它装的是你的真实知识、决策和偏好。本工具集与它是两个独立仓库。

---

## 依赖

**必需**

- **Python 3.10+** — 大部分 CLI。用了 `X | None` 联合类型语法
- **Bash** — `install.sh`、`llm-wiki-init`、`llm-wiki-govern`、`llm-wiki-remote-sync`、`wiki-graph`
- **Git** — 记忆库本身是 git 仓库；跨机同步依赖它

**按需**

- **Node.js** — 仅 `llm-wiki-mcp`（MCP server）需要。零 npm 依赖
- **`graphify`** — 仅 `wiki-graph` 与 MCP `find_related` 需要
- **`gh`** — 仅 `llm-wiki-remote-sync` 在某些克隆场景用到
- **`explorer.exe` / `wslpath`** — 仅 `wiki-graph` 在 WSL 下开浏览器用

在只有 Python / Git / Node 的环境里实测过：`llm-wiki-enrich --query` 与
`llm-wiki-health --json` 均正常。

---

## 测试

```bash
bash tests/test-init.sh             # 引导能力，44 项
bash tests/test-mcp-framing.sh      # MCP 双传输框架与失败传播，10 项
bash tests/test-mirror-optional.sh  # 可选层不得拖垮核心路径，12 项
bash tests/test-retrieval-eval.sh   # 离线检索质量与安全边界
bash tests/test-event-lifecycle.sh  # 生效/失效/替代/跨项目/脱敏
bash tests/test-remote-sync-stamp.sh # 只有完整成功同步才能写成功戳
bash tests/test-graph-freshness.sh  # Graphify manifest 新鲜度
bash tests/test-codex-hook-template.sh # SessionStart hook 模板与有界输出
```

全部使用隔离 `HOME` 或临时目录，**不碰你的真实记忆库**。召回评测夹具由脚本
临时生成，不包含用户数据，也不调用网络、模型或 embedding 服务。

后两个套件锁住的都是**静默失败**——这类缺陷不报错，只是悄悄不干活：

- `test-mcp-framing.sh` 同时验证 NDJSON 与 `Content-Length` 两条路径。
  只支持后者的 server 在 Codex 下会静默挂起；CLI 非零退出必须传播成
  JSON-RPC error，不能包装成假成功 content。
- `test-mirror-optional.sh` 验证未配镜像时核心写入仍成功、自定义
  `LLM_WIKI_BIN_TARGET` 时派生页仍刷新、`LLM_WIKI_ROOT` 被尊重。
  这三项都曾以「rc=0 但没干活」的形式存在过。
- `test-remote-sync-stamp.sh` 锁住成功戳语义：pull/push/status、脏工作区、fetch、
  dedupe 或 push 失败都不得伪装成一次完整成功同步。
- `test-codex-hook-template.sh` 验证当前官方 hook schema 形状，并确保常驻上下文
  有界、脱敏、带 canary，且不会把“下一步”变成自动任务。

---

## 参与

欢迎 issue 和 PR。几点约定：

- **行为改动要带测试。** 测试必须用隔离 `HOME` / 临时目录，不碰真实数据
- **别加数据库。** 纯文件是刻意选择：可 diff、可 grep、可手改、无迁移负担
- **别把自动化伸进 `wiki/` 正文。** 晋升经人审是信噪比的来源。工具可以*建议*
  （`llm-wiki-promote-notes` 就是），但不该代替人决定
- **绝不提交凭据。** pre-commit hook 会拦，但别依赖它兜底

---

## License

MIT — 见 [LICENSE](LICENSE)。
