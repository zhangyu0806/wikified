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
- [私有数据问题如何回流公有机制仓](#私有数据问题如何回流公有机制仓)
- [安装](#安装)
- [五分钟上手](#五分钟上手)
- [本机 Web Cockpit](#本机-web-cockpit)
- [命令清单](#命令清单)
- [五个 Harness 的支持边界](#五个-harness-的支持边界)
- [接进 Codex](#接进-codex)
- [事件生命周期与召回评测](#事件生命周期与召回评测)
- [接进 Claude Code、OpenCode、Grok 与 Cursor](#接进-claude-codeopencodegrok-与-cursor)
- [共享项目 MCP 模板](#共享项目-mcp-模板)
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

## 私有数据问题如何回流公有机制仓

私有记忆库只保存个人数据，不能成为机制修复的长期分叉。凡是在私有记忆库、真实
Harness 配置或跨机同步中发现的通用问题，都应先用脱敏夹具复现，再把实现、测试和
公开文档修复提交到本仓库；私有仓只保留数据和兼容配置，不复制一份长期漂移的工具链。

详细分类、脱敏和发布门禁见
[`docs/PRIVATE_TO_PUBLIC_WORKFLOW.md`](docs/PRIVATE_TO_PUBLIC_WORKFLOW.md)。

---

## 安装

```bash
git clone https://github.com/zhangyu0806/wikified ~/wikified
cd ~/wikified
./install.sh --init
```

正常安装只做两类受管工作：

1. 把 18 个 CLI 软链到 `~/.local/bin`，把三个 lowercase kebab-case skill
   软链到 `~/.agents/skills`，并在已存在的 Agent skill 目录做可选 fanout。
2. 把 OpenCode 插件链接到当前官方全局目录
   `~/.config/opencode/plugins/llm-wiki-recall.js`。

`--init` 还会在另一个目录（默认 `~/llm-wiki`）创建私有 Markdown/JSONL + Git
记忆库。机制仓库与私有数据仓库不会互相复制插件或配置。

**正常安装绝不写 Claude/OpenCode/Grok/Codex/Cursor 的 settings、MCP、hooks 或
instruction 文件。** 安装后显式配置当前 WSL 的 Claude Code、OpenCode 和 Grok：

```bash
./install.sh --configure-harnesses
./install.sh --status
./install.sh --check
```

也可以直接使用专用命令：

```bash
llm-wiki-harness status --json
llm-wiki-harness configure --harness claude --harness opencode --harness grok
llm-wiki-harness configure --harness codex
llm-wiki-harness configure --harness cursor
```

配置器会在非交互 PATH 缺少私有 bin 时继续检查：

- Claude/Grok：`~/.local/bin/`
- OpenCode：`~/.opencode/bin/`，再检查 `~/.local/bin/`
- Codex：常见用户级 bin
- Cursor：优先 `cursor-agent` 与 Cursor 专属目录；通用名 `agent` 只有在隔离、
  最多 3 秒的 `--version` 探针明确识别为 Cursor 时才算已安装。Grok 的兼容
  `agent` 命令不会触发 Cursor 配置。

Claude、Grok、Codex 的 MCP 通过其原生 CLI 添加并回读验证；OpenCode/Cursor 的
JSON/JSONC 只做结构合并。现有文件在修改前创建恢复快照，修改后重新解析，重复运行
应为零改动。格式损坏、已有同名但不同的 `llm-wiki` 条目、用户文件、错误软链或
受管块被手改时都会安全停止；脚本不会自动删除、覆盖或重新指向它们。

其他模式：

```bash
./install.sh               # 只装受管链接
./install.sh --dry-run     # 只打印链接动作
./install.sh --status      # 只读五端能力矩阵
./install.sh --check       # 链接 + 已安装 harness 配置门禁；缺失的可选 harness 不算失败
```

`~/.local/bin` 不在 PATH 时脚本会提示，但状态检测不依赖交互 shell 的 PATH。

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
llm-wiki-enrich --session-start           # 关键事实 + 活跃项目 + 未闭环事项
llm-wiki-enrich --query "secret-scan"     # 定向召回，保留 next-action（用户主动拉取）
llm-wiki-enrich --query "secret-scan" --ambient  # 自动召回用：只留状态，剥离 next-action

# 离线、无用户数据地量化召回质量与安全边界
llm-wiki-eval --json

# 体检：陈旧页、孤儿页、断链、未编译的 raw
llm-wiki-health --json

# 看哪些 quick note 值得晋升进 wiki（只读建议，不自动改）
llm-wiki-promote-notes --json

# 复盘：一个入口看清「该审什么、堆了多久」（只读只建议）
llm-wiki-review                 # 人类可读清单，并更新 last-review 时间戳
llm-wiki-review --peek          # 只看不计入一次正式复盘
```

每个命令都有 `--help`。

**典型工作流**：平时用 `note` / `event` / `correct` 随手记；任务开始前用 `enrich` 召回；
**定期（约每周）跑一次 `llm-wiki-review`**，它把四路待审——用户纠正、晋升建议、未编译
raw、过期候选——聚合成一份清单，人工决定每项如何处置。晋升永远经人审——这是信噪比的来源。

### 复盘（review）：给人看的入口

捕获（capture）只是半个循环，复盘（review）才让它变成可用知识——这是 GTD/Zettelkasten
等所有个人知识系统共同的失败点：只写不审，库就沦为坟场。Wikified 的机制齐全（capture →
candidate → audit → **approve** → index → retrieve → expire），但 approve 这一环必须是人做。
`llm-wiki-review` 就是那个低摩擦入口，它**只读、只建议，从不自动改** wiki/events/corrections：

```bash
llm-wiki-review          # 打印复盘清单：4 个板块，每项给一个动作动词
```

清单四板块，按优先级：

1. **待处理的纠正/偏好** — `corrections.jsonl` 里 pending 的项。你纠正过 AI 或表达过偏好，
   但还没进 `CRITICAL_FACTS`/`AGENTS.md`，AI 可能还在犯。处置：
   - `llm-wiki-correct --resolve <id>` — 已晋升（你已手动编译进规则）
   - `llm-wiki-correct --resolve <id> --status rejected` — 看过决定不要
   - 两者都从 pending 移除但**留档**，供审计。
2. **晋升建议** — `promote-notes` 对 quick-note / auto-draft 的分类打分。
3. **未编译的源材料** — `raw/` 里还没编译进 `wiki/`、也没标 `status: compiled` 的文件。
4. **Event 堆积与过期候选** — 按月统计 event；列出已过 ≥2 个半衰期（置信衰减到 1/4 以下）
   的陈旧项，建议复核去留。过期不删：把 `lifecycle` 手动改 `deprecated` 或用新事实
   `--supersedes` 取代，旧的留档。

复盘节奏对标 GTD 周复盘：`review` 跑完会更新 `wiki/dashboards/.last-review` 时间戳，
下次开头显示「距上次复盘 N 天」，超过 7 天醒目提示——**不依赖你记日历**，随时能看。

---

## 本机 Web Cockpit

[Wikified Cockpit](https://github.com/zhangyu0806/wikified-cockpit) 是本项目的官方本机 Web
界面，把复盘审批、GTD 和 Markdown 阅读放进浏览器，同时继续以 `llm-wiki-*` CLI 和
普通文件为唯一事实源。

```bash
git clone https://github.com/zhangyu0806/wikified-cockpit ~/wikified-cockpit
cd ~/wikified-cockpit
./install-service.sh
```

安装后打开 `http://localhost:4177`。Cockpit 代码和 Wikified 机制一起以 MIT 许可证开源；
它只监听 `127.0.0.1`，读取你本机的 `~/llm-wiki`，不会把私有 `wiki/`、`raw/` 或
`memory/` 内容放进公开仓库。数据库不在默认位置时，用
`LLM_WIKI_ROOT=/你的/路径 ./install-service.sh` 显式指定。

---

## 命令清单

**写入与治理**

| 命令 | 用途 |
|---|---|
| `llm-wiki-note` | 记零散事实/命令/坑到 `raw/notes/` |
| `llm-wiki-event` | 记 append-only typed event |
| `llm-wiki-correct` | 记录纠正与偏好，进入待人审队列 |
| `llm-wiki-refresh` | 重建 Today、项目/健康/复盘仪表盘与可选镜像；刷新不等于 wiki 晋升 |
| `llm-wiki-govern` | 带节流的周期治理 |
| `llm-wiki-dedupe-events` | 按 event id 去重 JSONL |
| `llm-wiki-secret-scan` | 凭据扫描与 pre-commit 门禁 |

**读取与评测**

| 命令 | 用途 |
|---|---|
| `llm-wiki-enrich` | critical 会话卡或主题定向召回；`--ambient` 剥离 next-action 只留状态 |
| `llm-wiki-eval` | 离线 Recall@5/MRR 与安全不变式评测 |
| `llm-wiki-health` | 陈旧页、孤儿页、断链、未复核 raw 等体检 |
| `llm-wiki-promote-notes` | 只读晋升建议，不自动写 `wiki/` |
| `llm-wiki-review` | 复盘入口：聚合待审纠正/晋升建议/未编译 raw/过期候选，只读只建议 |

**安装、集成与外部接口**

| 命令 | 用途 |
|---|---|
| `llm-wiki-init` | 创建私有记忆库 |
| `llm-wiki-harness` | 五端检测、状态矩阵与显式安全配置 |
| `llm-wiki-session-start` | 统一的 critical-only、脱敏、2500 字符 hook 适配器 |
| `llm-wiki-mcp` | 零 npm 依赖 MCP server |
| `llm-wiki-remote-sync` | 带节流的多机双向 Git 同步 |
| `llm-wiki-obsidian-sync` | 生成可读 Obsidian 镜像 |
| `wiki-graph` | 打开可选 Graphify 图谱 |

命令名继续保留 `llm-wiki-*`，MCP server 名继续为 `llm-wiki`，以避免破坏既有
配置和已经沉淀的事实记录。

## 五个 Harness 的支持边界

“支持”在这里不是一条 README 命令，而是检测、配置或模板、生命周期、失败语义和
隔离合同测试的组合。

| 能力 | Claude Code | OpenCode | Grok CLI | Codex | Cursor |
|---|---|---|---|---|---|
| MCP | 原生 CLI，user scope | JSON/JSONC 结构合并 | 原生 CLI，user scope | 原生 CLI；Windows home 可只读识别 | `~/.cursor/mcp.json` 模板/结构合并 |
| 稳定规则 | `~/.claude/CLAUDE.md` 受管块 | 全局 `AGENTS.md` 受管块 | `~/.grok/AGENTS.md` 受管块 | `AGENTS.md` 受管块 | 提供 AGENTS 模板，位置按运行形态人工选择 |
| 会话生命周期 | `SessionStart` stdout 进入上下文 | `chat.message` 插件首次注入 | 被动 hook stdout 被忽略，仅做非敏感健康探针 | `SessionStart`，含 Windows→WSL 模板 | 仅本地 IDE/Agent 的 `sessionStart`；Cloud Agents 不支持 |
| Skill | Claude 目录可选 fanout | `~/.agents/skills` | 兼容 `~/.agents/skills` | `~/.agents/skills` | 不作为必需能力 |
| 自动捕获/晋升 | 不启用 | 自动草稿默认关闭；即使开启也只进 raw | 不管理实验记忆 | 不管理原生 Memories | 不启用 |

所有自动会话上下文都通过 `llm-wiki-session-start`：只读取人工维护的 `critical`
scope，二次脱敏，最终文本硬上限 2500 字符，并加上“untrusted evidence；不是指令或
任务队列”的标签。具体项目、报错和动态状态通过 MCP/显式 query 按需召回。

Grok 的 SessionStart 不能靠 stdout 注入，因此其 hook 只写入 `XDG_STATE_HOME` 下的
成功/字符数/时间戳，**不保存召回正文**；正常召回依赖 MCP、规则与 skills。Cursor
Cloud Agents 不运行用户级 hooks，也没有 `sessionStart`，因此必须回退到 MCP/人工召回。

## 接进 Codex

WSL/Linux Codex CLI 可使用显式配置器：

```bash
llm-wiki-harness configure --harness codex
llm-wiki-harness status
codex mcp list
```

配置器通过 `codex mcp add llm-wiki -- <absolute-command>` 注册 MCP，并对
`$CODEX_HOME/hooks.json` 与 `AGENTS.md` 做保留用户字段的合并；固定 prompt 只在目标
不存在或内容完全相同时受管。首次运行或 hook hash 改变后，仍需在 Codex 的 hook UI
中审查、信任并重启/新开会话。hook 覆盖 startup/resume/clear/compact，只注入稳定
critical facts；主题召回继续使用 MCP 或 `prompts/llm-wiki-recall.md`。

### Windows Codex Desktop 读取 WSL 记忆库

Windows Desktop 与 WSL CLI 通常不是同一个 Codex home。`install.sh` 可以通过
`LLM_WIKI_WINDOWS_CODEX_HOME`（在标准 WSL 环境下也会尝试只读发现）检查 Windows
home，但不会替你写入 Windows 配置。示例：

```toml
[mcp_servers.llm-wiki]
command = "wsl.exe"
args = ["-d", "Ubuntu", "--", "/home/<linux-user>/.local/bin/llm-wiki-mcp"]
startup_timeout_sec = 20
tool_timeout_sec = 60
```

把 `templates/codex/hooks.json` 的 `SessionStart` group **合并**到实际
`%CODEX_HOME%/hooks.json`；不要覆盖整个文件。`commandWindows` 使用 WSL 适配器，
默认 distro 不正确时显式加 `-d <distro>`。`AGENTS.recall.md` 与 prompt 同样按实际
Windows Codex home 放置，并在 `/hooks` 中审查。

Codex 原生 Memories 与 Wikified 是两套系统。本安装器不启用、清空、同步或提交
`memories/`，也不把同一会话自动送入两条摄取链；是否启用原生 Memories 由用户单独决定。

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

## 接进 Claude Code、OpenCode、Grok 与 Cursor

### Claude Code

```bash
llm-wiki-harness configure --harness claude
claude mcp get llm-wiki
```

MCP 通过 `claude mcp add --scope user llm-wiki -- <absolute-command>` 添加；配置器不直接
编辑同时承载登录/MCP 状态的 `~/.claude.json`。它只结构合并
`~/.claude/settings.json` 的 `SessionStart` hook，并在 `~/.claude/CLAUDE.md` 追加一个
带 BEGIN/END 标记的受管规则块。已有 hooks、permissions 和其他字段保留。项目级 MCP
仍可能需要逐项目信任。

### OpenCode 1.18+

当前全局插件目录是：

```text
~/.config/opencode/plugins/
```

旧的 `~/.opencode/plugins/` 只作为迁移诊断对象，不再作为发布版真源。配置器在当前
`opencode.json`/`opencode.jsonc` 的 `mcp` 对象中添加本地 server，支持注释和尾逗号，
并在全局 `AGENTS.md` 合并规则。插件会在每个 session 的第一条消息注入一次 critical
摘要，并对每条用户 prompt 做最多 2000 字符的主题召回；两者都标记为不可信证据。

自动的逐条召回走 `enrich --query <text> --ambient`：`--ambient` 会剥离召回内容里的
「下一步 / next / todo / 待办」及已完成（✅ / [x] / ~~）行，只注入**状态**，不注入可
执行意图——防止召回上下文被 Agent 当成待办队列执行（自动化召回不该带 next-action，
只有用户显式 `enrich --query <项目名>`、不带 `--ambient` 时才带出下一步）。

`LLM_WIKI_OPENCODE_AUTO_DRAFT=1` 才允许 compact 前写脱敏草稿到
`raw/inbox/auto-drafts/`。默认关闭；开启后也不会写 `wiki/`。插件状态日志只写入
`XDG_STATE_HOME/llm-wiki/harness/`，不记录 prompt 正文。

### Grok CLI

```bash
llm-wiki-harness configure --harness grok
grok mcp list --json
grok mcp doctor llm-wiki --json   # 若当前版本支持
```

MCP 使用 Grok 原生 CLI；稳定规则进入 `~/.grok/AGENTS.md`，skills 复用
`~/.agents/skills`。`~/.grok/hooks/llm-wiki.json` 的 SessionStart 只是 fail-open 的
健康探针，因为 Grok 对被动 hook stdout 不做上下文注入。

本项目不读取 `~/.grok/auth.json`，也不启用、flush、dream 或写入 Grok 的实验记忆。
若用户自行启用实验记忆，必须把它视为独立系统，避免同一 transcript 被双重摄取。

### Cursor

当本地 Cursor Agent CLI 被**身份确认**后，可使用：

```bash
llm-wiki-harness configure --harness cursor
```

`cursor-agent`、Cursor 专属 bin 目录，以及解析到
`~/.local/share/cursor-agent/` 的安装路径可作为明确证据。通用命令名 `agent`
本身不是证据：配置器只执行官方用于验证安装的 `agent --version`，使用临时
HOME/XDG/Cursor config、移除 API-key 环境变量并设置 3 秒硬超时；输出必须包含
Cursor 标识，否则按 `identity-mismatch` 或 `identity-unverifiable` 处理。
这两种状态的 `overall` 都是 `absent`，不会读取、创建或改动任何 `~/.cursor`
配置。`~/.local/bin/agent` 若解析进 `~/.grok/` 会直接拒绝。

`LLM_WIKI_CURSOR_BIN` 只覆盖候选位置，**不绕过身份校验**；它指向通用
`agent` 时仍须通过同一探针，指向 Grok 会得到
`explicit-identity-mismatch`。这允许非标准 Cursor 安装显式给出路径，同时不会
把错误产品“强制认证”为 Cursor。

身份确认后，配置器才会结构合并 `~/.cursor/mcp.json` 与本地 `hooks.json`。
仅安装 IDE、但没有可确认 Agent CLI 时，配置器不会据此猜测运行形态或自动写
用户目录；请人工审阅并合并 `templates/cursor/`。Cursor 的本地
`sessionStart` 期望
`{"additional_context": ...}`，统一适配器会输出该结构。Cursor Cloud Agents 不运行
user-level hooks，也不支持 `sessionStart`，所以云端只承诺 MCP/人工召回，不把本地能力
泛化为所有 Cursor 运行形态。`templates/cursor/AGENTS.recall.md` 是人工合并模板。

## 共享项目 MCP 模板

`templates/shared/mcp.project.json` 使用 Claude/Grok 兼容的 `mcpServers` 根对象和 stdio
`command`/`args`：

```bash
cp templates/shared/mcp.project.json .mcp.json
```

该模板使用 `sh -lc` 与 `$HOME/.local/bin/llm-wiki-mcp`，因此只承诺 WSL/Linux 项目级
可移植性，不是原生 Windows 路径。Claude/Grok 仍可能要求项目 trust/重启；OpenCode
使用自己的 `mcp` JSONC schema，Cursor 使用 `~/.cursor/mcp.json`，不能把一个文件
无条件复制到五端。对单机 WSL，用户级原生 CLI 配置通常比项目文件更确定。

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

数据流的拓扑（三条注入路径、边谓词、排除项、每层 token 预算）见
[`docs/MEMORY_PIPELINE.md`](docs/MEMORY_PIPELINE.md) 及配套的
[`docs/MEMORY_PIPELINE.mmd`](docs/MEMORY_PIPELINE.mmd) 图。

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
| `LLM_WIKI_ROOT` | 私有记忆库位置（默认 `~/llm-wiki`） |
| `LLM_WIKI_MIRROR` | Obsidian 镜像目标 |
| `LLM_WIKI_BIN_TARGET` | 受管 CLI 目录（默认 `~/.local/bin`） |
| `LLM_WIKI_AGENT_SKILL_ROOT` | skill 主目录（默认 `~/.agents/skills`） |
| `LLM_WIKI_SKILL_FANOUT` | 空格分隔的既有 Agent skill 目录；空字符串关闭 |
| `LLM_WIKI_OPENCODE_PLUGIN_TARGET` | 当前 OpenCode 插件目录（默认 `~/.config/opencode/plugins`） |
| `LLM_WIKI_OPENCODE_LEGACY_PLUGIN_TARGET` | 仅用于诊断旧 `~/.opencode/plugins` |
| `LLM_WIKI_WINDOWS_CODEX_HOME` | WSL 中只读检查的 Windows Codex home |
| `LLM_WIKI_CLAUDE_BIN` / `..._OPENCODE_BIN` / `..._GROK_BIN` / `..._CODEX_BIN` | 显式 harness 可执行文件，适合非标准安装 |
| `LLM_WIKI_CURSOR_BIN` | 覆盖 Cursor 候选位置，但不绕过通用 `agent` 的身份校验；错误产品保持 absent 且不可写配置 |
| `LLM_WIKI_OPENCODE_AUTO_DRAFT` | 设为 `1` 才允许 OpenCode 写脱敏 raw 草稿 |
| `LLM_WIKI_SYNC_THROTTLE` | Git 同步节流秒数 |
| `LLM_WIKI_GOVERN_THROTTLE` | 治理节流秒数 |

测试还使用 `LLM_WIKI_DISABLE_PATH_DETECTION=1` 隔离真实 PATH；它主要是合同测试开关，
日常无需设置。harness 专用目标路径也可通过 `llm-wiki-harness --help` 与源码中的
`LLM_WIKI_*` override 查看。

## 排查

**先看无敏感值状态矩阵**

```bash
./install.sh --status
llm-wiki-harness status --json
./install.sh --check
```

状态含义：`absent`、`installed-unconfigured`、`configured`、
`stale/wrong-link`、`unverifiable`。`--check` 只要求已安装/外部已接线的 harness 完整；
缺失的可选 harness 不算失败。

**OpenCode 当前路径存在错误软链**

脚本会保留并返回非零，不自动 `rm` 或 `ln -sfn`。先人工确认：

```bash
readlink ~/.config/opencode/plugins/llm-wiki-recall.js
readlink -f ~/.config/opencode/plugins/llm-wiki-recall.js || true
```

确认它确实不是要保留的用户文件后，再移动而不是删除：

```bash
mv ~/.config/opencode/plugins/llm-wiki-recall.js \
  ~/.config/opencode/plugins/llm-wiki-recall.js.bak-manual
./install.sh
./install.sh --configure-harnesses
```

旧 `~/.opencode/plugins/llm-wiki-recall.js` 只会被报告；清理与否由用户决定。

**配置 malformed / 同名条目冲突**

原文件保持不变，并生成 `*.bak-wikified-malformed-*` 或
`*.bak-wikified-conflict-*` 快照。修复原文件或人工迁移同名配置后重跑；不要把快照
当作自动回滚指令。

**MCP 已配置但 harness 看不到**

重启客户端，接受项目 trust，并用原生命令复核：

```bash
claude mcp get llm-wiki
opencode mcp list
grok mcp list --json
codex mcp list
```

Grok hook 没有召回正文是预期行为；它只做探针。Cursor Cloud Agent 没有
`sessionStart` 也是预期边界。

**`llm-wiki-health` 报缺 `SCHEMA.md`**：运行 `llm-wiki-init --git`。

**命令找不到**：把 `~/.local/bin` 加入 PATH，或使用绝对路径；检测器仍会检查已知私有 bin。

**Codex MCP 静默挂起**：运行 `bash tests/test-mcp-framing.sh`，确认 server 同时支持 NDJSON。

**跨机 pull 后 JSONL 重复**：运行 `llm-wiki-dedupe-events`；union merge 先保留双方追加是设计行为。

**remote sync 一直秒退**：`llm-wiki-remote-sync --status` 查看节流，必要时 `--force`。

## 卸载

没有自动卸载模式：删除与配置回退都需要用户逐项确认。机制卸载不会删除私有
`~/llm-wiki` 数据仓库。

```bash
# 1. 受管 CLI（先确认仍指向本 repo）
for f in ~/wikified/bin/*; do
  target=~/.local/bin/"$(basename "$f")"
  [[ -L "$target" && "$(readlink -f "$target")" == "$(readlink -f "$f")" ]] && rm -- "$target"
done

# 2. 当前 OpenCode 插件链接
plugin=~/.config/opencode/plugins/llm-wiki-recall.js
[[ -L "$plugin" && "$(readlink -f "$plugin")" == "$(readlink -f ~/wikified/plugins/llm-wiki-recall.js)" ]] && rm -- "$plugin"

# 3. lowercase skills
for skill in quick-note session-capture wiki-compiler; do
  rm -f ~/.agents/skills/"$skill" ~/.claude/skills/"$skill" \
    ~/.config/opencode/skills/"$skill" ~/.codex/skills/"$skill"
done
```

MCP 使用各客户端原生命令注销（例如 `claude mcp remove llm-wiki`、
`grok mcp remove llm-wiki`、`codex mcp remove llm-wiki`，以本机 `--help` 为准）。
OpenCode/Cursor 的同名结构以及 CLAUDE/AGENTS/hooks 中 BEGIN/END 受管块应人工删除，
不要整文件删除。可选清理 `XDG_STATE_HOME/llm-wiki/harness/` 的非敏感探针状态和
`~/.cache/llm-wiki-*`。

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

完整门禁：

```bash
bash -n install.sh bin/llm-wiki-remote-sync .githooks/pre-commit tests/test-*.sh
python3 -m py_compile <仓库中带 python3 shebang 的 llm-wiki 文件>
node --check bin/llm-wiki-mcp
node --check plugins/llm-wiki-recall.js
for t in tests/test-*.sh; do timeout 120 bash "$t"; done
bin/llm-wiki-secret-scan --all
```

重点套件：

| 测试 | 锁定内容 |
|---|---|
| `test-harness-integration.sh` | 私有 bin 检测、五端状态、Cursor/Grok `agent` 碰撞、显式覆盖身份门禁、installer strict/check、CLI 参数、JSONC 保真、幂等、备份、冲突、错误软链、失败隔离、skill/模板边界 |
| `test-session-start-adapters.sh` | Claude/Codex/plain/Cursor/Grok 输出结构、critical-only、二次脱敏、2500 硬上限、fail-open |
| `test-codex-hook-template.sh` | Linux 与 Windows→WSL hook 回归、信任标签、动态状态不常驻 |
| `test-opencode-plugin.sh` | 首条 critical 摘要、JIT 召回、XDG 状态日志、默认无草稿、opt-in 脱敏 raw 草稿 |
| `test-install-windows-codex-detection.sh` | WSL 对既有 Windows Codex home 的只读识别 |
| `test-mcp-framing.sh` | NDJSON 与 Content-Length，CLI 失败传播 |
| 其余 `test-*.sh` | init、事件、锁、同步、镜像、图谱、召回与安全边界 |

所有新集成测试使用临时 HOME、临时数据根和 stub harness executable；不会读取真实
配置、认证状态或记忆。它们是模拟/合同测试，不是 Claude/OpenCode/Grok/Cursor/Codex
真实登录会话 E2E，也不证明真实多机同步。

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
