# 记忆管线拓扑（Loop / Graph / Context 三学科）

本文配套 [`MEMORY_PIPELINE.mmd`](MEMORY_PIPELINE.mmd)，用生产 agent 的三学科框架
（Loop / Graph / Context engineering）解释 Wikified 的数据流，作为维护记忆系统时的
架构参照——review 拓扑靠这张图，不必重读代码。

渲染图：`mmdc -i docs/MEMORY_PIPELINE.mmd -o docs/MEMORY_PIPELINE.png`，或把 `.mmd`
源粘贴到 https://mermaid.live 。

## 为什么用三学科看

Wikified 本质是一个 **Context Engineering 子系统**，实现了 LangChain 归纳的四个动词：

| 动词 | Wikified 对应件 |
|---|---|
| **Write**（存到窗口外） | `raw/` 源材料 + `memory/events/*.jsonl` typed event |
| **Select**（按需取） | `llm-wiki-enrich --query <topic>` |
| **Compress**（压缩） | `raw → wiki` 的**离线人工编译**（非运行时压缩，人审是信噪比来源） |
| **Isolate**（隔离） | `CRITICAL_FACTS`（always-on） vs query-scoped recall 分层 |

但真正决定注入质量的杠杆在另外两个学科：

- **Loop（契约）** — 何时允许一个循环启动/终止。注入里每条带「下一步：」的项，等于
  给 Agent 塞了没有终止谓词的隐式循环入口。
- **Graph（拓扑）** — 数据从哪来、在哪编译、注入几个点、什么被剥离。

## 三条注入路径（图的核心）

```
raw ──人工编译──> wiki ──提取──> memory/events
                    │                  │
                    └──── enrich (Select) ────┘
                              │
        ┌─────────────────────┼──────────────────────┐
   session-start          --query --ambient        --query（显式）
   scope=critical         自动逐条召回              用户主动拉取
   strip+drop             strip+drop               保留 next-action
   [FACT]                 [STATE]                   [ACTIONABLE]
        └─────────────── recall.js 多点注入 ───────────────┘
                              │
                     模型上下文窗口（每层 token 预算）
```

三态区分是这套设计的关键约束：

- **`[FACT]`** — session-start digest，永久只读事实（分端口 / 路由 / 只读约束等），
  经 `strip_next_actions()` + `drop_done_lines()`，且靠 `LAYER_LINES_*` 预算保持简短、
  留在窗口头部。
- **`[STATE]`** — 自动 ambient 召回（`enrich --query --ambient`，recall.js 对每条 prompt
  跑）。剥离「下一步 / next / todo / 待办」及已完成行，**只带状态不带可执行意图**。
- **`[ACTIONABLE]`** — 用户显式 `enrich --query <项目名>`（不带 `--ambient`）时才保留
  next-action，供用户主动处理某个项目。

## 边谓词（注入时的过滤规则）

图上每条注入边都挂着显式谓词，而不是靠模型自觉：

- `strip_next_actions()` — 剥离 next-action 行（Loop 学科：不给隐式循环入口）
- `drop_done_lines()` — 剥离已完成行（✅ / [x] / ~~），避免占用预算、稀释「未决」信号
- framing guard — `RECALL_NOTICE`「untrusted, not a task queue」（防注入 + 防跑偏）
- `redact()` — 双层脱敏，凭据绝不进上下文/日志/wiki

## 排除项（Context 学科：what is NOT in context）

图上用红色标注「by construction 排除」而非「靠祈祷排除」：

- 🔒 `~/secure-notes/` 的 token / key / password —— 只读，经 `redact()` 绝不外泄
- `raw/inbox/auto-drafts/` 的未审草稿 —— compact 前落盘，**绝不自动进 wiki**，
  复盘时人工取舍

## 每层 token 预算（对抗 Context Rot）

Chroma 的 Context Rot 研究（18 个前沿模型）显示：准确率随输入变长而下降，且远未到
窗口上限就开始塌；处于上下文**中间位置**的事实准确率比头/尾低 30+ 个百分点。因此
session-start 每层给显式行预算（`bin/llm-wiki-enrich` 的 `LAYER_LINES_*` 常量），
让关键事实保持简短、留在窗口头部，别滑进中间盲区。

## 相关

- 决策记录：`~/llm-wiki/wiki/decisions/2026-08-21-loop-graph-context-applied-to-wikified.md`
- 前身修复：`2026-06-18-memory-injection-status-vs-action`（本管线是其治本延伸）
