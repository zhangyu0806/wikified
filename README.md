# llm-wiki-toolkit

> A file-based, cross-ecosystem memory layer for coding agents. Plain CLIs + skills + an
> MCP server over a plain Markdown/JSONL repo. No database, no vendor lock-in.
> **The toolkit's own docs, skill files and CLI help are in Chinese** — the code and CLI
> flags are English, so it is usable without Chinese, but the prose below is Chinese.

给编码 agent 用的**文件式跨生态记忆层**。底座是一个纯 Markdown / JSONL 的 git 仓库，
上面是一组 CLI、三个 skill 和一个 MCP server。没有数据库，没有厂商绑定。

核心思路：**知识是编译出来的，不是检索出来的。** 原始材料进 `raw/`，经人审编译进
`wiki/`，轻量事实走 `memory/events/` 的 typed event。手动触发保证信噪比。

本仓库**只有机制，没有任何个人内容**——不含 `wiki/`、`raw/`、`memory/`。
你的记忆库是另一个仓库（可以是私有的），由 `llm-wiki-init` 创建。

---

## 快速开始

```bash
git clone <this-repo> ~/llm-wiki-toolkit
cd ~/llm-wiki-toolkit
./install.sh --init
```

`install.sh` 把 CLI 软链进 `~/.local/bin`，把 skills 分发到各 Agent 目录；
`--init` 再建出你的记忆库 `~/llm-wiki`（含目录骨架、`SCHEMA.md`、git 仓库与凭据门禁）。

验证：

```bash
llm-wiki-health --json     # 应 rc=0 并输出合法 JSON
```

已有记忆库时省掉 `--init`。想先看会改什么：`./install.sh --dry-run`。
校验安装是否与仓库一致（可当 CI 门禁）：`./install.sh --check`。

> **没有 `--init` 就不能用。** 其余命令都假定记忆库已存在：缺 `SCHEMA.md` 时
> `llm-wiki-health` / `llm-wiki-promote-notes` / `llm-wiki-refresh` 直接失败，
> `llm-wiki-remote-sync` 缺 `.git` 也会中止。

---

## 命令清单

**日常写入**

| 命令 | 用途 |
|---|---|
| `llm-wiki-note` | 记一条零散事实 / 命令 / 坑到 `raw/notes/` |
| `llm-wiki-event` | 记 typed event（`fact` / `decision` / `bug` / `preference` …） |
| `llm-wiki-correct` | 记录用户的纠正与偏好，入待人审队列 |

**读取与召回**

| 命令 | 用途 |
|---|---|
| `llm-wiki-enrich` | 语义召回。`--session-start` 取关键事实+活跃项目，`--query` 定向检索 |
| `llm-wiki-health` | 结构体检：陈旧页、孤儿页、断链、未编译 raw |
| `llm-wiki-promote-notes` | 建议哪些 quick note 值得晋升进 `wiki/`（只读 dry-run） |

**维护**

| 命令 | 用途 |
|---|---|
| `llm-wiki-init` | 从零建出记忆库 |
| `llm-wiki-refresh` | 重建派生页（`Today.md`、仪表盘、健康检查） |
| `llm-wiki-govern` | 带节流的周期治理（会话启动时跑，命中节流即秒退） |
| `llm-wiki-dedupe-events` | 按 event id 去重 append-only jsonl |
| `llm-wiki-secret-scan` | 凭据扫描。装作 pre-commit hook 拦截凭据入库 |

**同步与外部**

| 命令 | 用途 |
|---|---|
| `llm-wiki-remote-sync` | 带节流的多机双向同步（home / office …） |
| `llm-wiki-obsidian-sync` | 镜像成 Obsidian 可直接打开的 vault |
| `wiki-graph` | 打开 Graphify 知识图谱（需另装 `graphify`） |
| `llm-wiki-mcp` | MCP server，把上面的能力暴露给任意 MCP 客户端 |

---

## 各 Agent 的支持程度

| 能力 | OpenCode | Codex | Claude Code | 其他 MCP 客户端 |
|---|---|---|---|---|
| CLI（15 个） | 是 | 是 | 是 | 是（能跑 shell 即可） |
| Skills（3 个） | 是 | 是 | 是 | 取决于是否读 `SKILL.md` |
| MCP（6 个 tool） | 是 | 是 | 是 | 是 |
| **会话启动自动注入** | **是** | 否 | 否 | 否 |

最后一行是**真实的能力差，不是配置问题**。自动注入靠 `plugins/llm-wiki-recall.js`
挂 OpenCode 的 `chat.message` hook，其他生态没有等价 hook。它们只能由 agent
主动调 `llm-wiki-enrich` 或 MCP 的 `search_pages`。

`templates/codex/` 提供把「任务开始先召回」写进 Codex 指令的模板，
让主动召回尽可能可靠——但它仍依赖 agent 遵守指令，不等于自动注入。

---

## 接进 Codex

CLI 和 skills 由 `install.sh` 自动就位（skills 走 `~/.agents/skills` 主副本再软链
到 `~/.codex/skills`；Codex 明确 follow symlink）。剩下两步是手动的：

```bash
# 1. 注册 MCP（注意 -- 分隔符）
codex mcp add llm-wiki -- ~/.local/bin/llm-wiki-mcp

# 2. 装召回指令
cat templates/codex/AGENTS.recall.md >> ~/.codex/AGENTS.md
cp templates/codex/prompts/llm-wiki-recall.md ~/.codex/prompts/
```

`install.sh` 会检测这两项并打印上面的命令，但**不会代写**
——`config.toml` 与 `AGENTS.md` 是你自己的文件，可能已手工调过。
（skills 是新增目录项，所以自动软链；这个不对称是有意的。）

---

## MCP tools

| tool | 作用 |
|---|---|
| `search_pages` | 自然语言检索 wiki 页与 memory event |
| `read_page` | 按相对路径读单页 |
| `find_related` | 经 Graphify 图谱找相关概念（需 `graphify`） |
| `list_recent_raw` | 列出待编译的近期源材料 |
| `record_event` | 写入 typed memory event |
| `lint` | 跑 `llm-wiki-health` 报结构问题 |

传输层同时支持换行分隔 JSON（MCP stdio 规范，Codex 用这个）和
LSP 风格 `Content-Length` 帧，按入向自动判定。

---

## 环境要求

**必需**：Python 3.10+（用了 `X | None` 语法）、Bash、Git。
**MCP server 另需**：Node.js。
**可选**：`graphify`（只影响 `wiki-graph` 与 `find_related`，其余命令无它照常工作）、
`gh`（`llm-wiki-remote-sync` 的仓库操作）、WSL 的 `explorer.exe` / `wslpath`（仅 `wiki-graph` 开浏览器用）。

---

## 记忆库长什么样

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
└── memory/events/         # append-only typed event（jsonl）
```

`.gitattributes` 给 `memory/events/*.jsonl` 和 `raw/inbox/corrections.jsonl`
设了 `merge=union`：两机各自 append 时保留双方所有行，合并后由
`llm-wiki-dedupe-events` 按 event id 去重。这样跨机同步不会退化成手工解冲突。

---

## 凭据安全

`llm-wiki-secret-scan` 作为 pre-commit hook 拦截凭据入库，两条规则：
厂商前缀指纹（`sk-` / `gsk_` / `ghp_` …）与「敏感键名紧邻高熵值」。
误报走 `.secret-allowlist`，存 fingerprint 而非明文。

**绝不把 token / key / password 写进 wiki、event 或 note。**
只记录「某凭据位于某处」，不记录值。

---

## 配置

| 变量 | 作用 |
|---|---|
| `LLM_WIKI_ROOT` | 记忆库位置（默认 `~/llm-wiki`） |
| `LLM_WIKI_MIRROR` | Obsidian 镜像目标。也可写进 `~/.config/llm-wiki/config.env` |
| `LLM_WIKI_BIN_TARGET` | CLI 安装位置（默认 `~/.local/bin`） |
| `LLM_WIKI_AGENT_SKILL_ROOT` | skills 主副本位置（默认 `~/.agents/skills`） |
| `LLM_WIKI_SKILL_FANOUT` | 空格分隔的分发目标；设为空字符串关闭分发 |
| `LLM_WIKI_TEMPLATES` | 显式指定 templates 目录 |

镜像目标优先级：`LLM_WIKI_MIRROR` env > `~/.config/llm-wiki/config.env` > 可行动报错。
选配置文件而非 shell profile，因为 cron / systemd / agent 子进程读不到 profile。

---

## 测试

```bash
bash tests/test-init.sh          # 引导能力（44 项）
bash tests/test-mcp-framing.sh   # MCP 双传输框架（8 项）
```

两者都用隔离 `HOME` 和临时目录，不碰你的真实记忆库。

---

## License

MIT
