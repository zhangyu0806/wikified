# LLM Wiki Schema

这是 wiki 的运行规则。所有 LLM agent 在操作 wiki 前必须先读此文件。

## 目录结构

```
~/llm-wiki/
├── SCHEMA.md              # 本文件 — wiki 运行规则（你+LLM 共同维护）
├── Today.md               # 今日沉淀入口页（llm-wiki-refresh 自动生成）
├── raw/                   # 不可变源材料（LLM 只读，不写）
│   ├── sessions/          # OpenCode/OMO 会话摘要（自动生成）
│   ├── notes/             # 快速碎片记录（用户说“记一下：...”）
│   └── articles/          # 外部文章/信息摘要（用户提供 URL 或内容）
├── wiki/                  # LLM 维护的知识库（LLM 拥有写权限）
│   ├── index.md           # 内容目录（每次 ingest 后更新）
│   ├── log.md             # 操作日志（追加写入）
│   ├── overview.md        # 全局综述（随知识增长演化）
│   ├── projects/          # 项目页 — 每个活跃项目一个文件
│   ├── concepts/          # 概念页 — 技术概念、模式、工具
│   ├── decisions/         # 决策页 — 重要技术/架构决策记录
│   ├── tools/             # 工具页 — 使用的工具、配置、技巧
│   ├── queries/           # 查询结果 — 有价值的问答存档
│   └── dashboards/        # 自动仪表盘 — 项目、健康检查等
├── skills/                # wiki agent 的行为指南和模板
├── memory/                # agent 持久记忆（跨会话笔记）
└── graphify-out/          # Graphify 图谱输出（自动生成）
```

## 层级职责

| 层 | 目录 | 谁拥有 | 用途 |
|---|---|---|---|
| 源材料 | `raw/` | 用户/自动化 | 不可变输入，LLM 只读 |
| 知识库 | `wiki/` | LLM | 结构化、互链的 Markdown 页面 |
| 规则 | `SCHEMA.md` | 用户+LLM | 定义 wiki 结构和工作流 |
| 技能 | `skills/` | 用户 | agent 行为指南 |
| 记忆 | `memory/` | LLM | 跨会话持久笔记 |

## 页面类型

### 项目页 (`wiki/projects/{slug}.md`)

记录一个活跃项目的状态、架构、关键决策。

```yaml
---
title: 项目名称
created: 2026-05-16
updated: 2026-05-16
status: active | paused | completed
tags: [tag1, tag2]
sources: [raw/sessions/2026-05-16-xxx.md]
---
```

内容包含：项目概述、当前状态、架构要点、关键文件、待办事项、相关概念链接。

### 概念页 (`wiki/concepts/{slug}.md`)

一个技术概念、模式或工具的百科式条目。

```yaml
---
title: 概念名称
created: 2026-05-16
updated: 2026-05-16
tags: [tag1, tag2]
related: [[other-concept]]
sources: [raw/sessions/xxx.md]
---
```

内容包含：定义、工作原理、使用场景、与其他概念的关系、代码示例。

### 决策页 (`wiki/decisions/{YYYY-MM-DD}-{slug}.md`)

记录一个重要的技术或架构决策（类似 ADR）。

```yaml
---
title: 决策标题
date: 2026-05-16
status: accepted | superseded | deprecated
context: 为什么需要做这个决策
tags: [tag1, tag2]
sources: [raw/sessions/xxx.md]
---
```

内容包含：背景、考虑的选项、最终决策、理由、后果。

### 工具页 (`wiki/tools/{slug}.md`)

记录一个工具的配置、使用技巧、踩坑经验。

```yaml
---
title: 工具名称
created: 2026-05-16
updated: 2026-05-16
tags: [tag1, tag2]
---
```

内容包含：用途、安装/配置、常用命令、踩坑记录、最佳实践。

### 查询页 (`wiki/queries/{YYYY-MM-DD}-{slug}.md`)

有价值的问答存档 — 问了一个好问题，答案值得保留。

```yaml
---
title: 问题简述
date: 2026-05-16
tags: [tag1, tag2]
---
```

## 工作流

### Ingest（摄入新源材料）

当 `raw/sessions/`、`raw/notes/` 或 `raw/articles/` 中出现新文件时：

1. 读取新的 raw 文件
2. 提取关键信息：项目、概念、决策、工具、经验
3. 对每个提取的实体：
   - 如果 wiki 中已有对应页面 → **更新**（追加新信息，标注来源）
   - 如果没有 → **创建**新页面
4. 更新 `wiki/index.md`（添加新页面条目）
5. 更新 `wiki/overview.md`（如果全局理解有变化）
6. 追加操作记录到 `wiki/log.md`

### Article Ingest（外部文章摄入）

用户提供 URL 或粘贴内容时：

1. **抓取内容**：用 webfetch / ExtractWisdom / Parser / Browser 获取文章全文、完整字幕、播客转写或其他可核验的一手材料。
2. **质量门槛**：先判断材料是否足够完整可靠，再决定能否编译进 `wiki/`。
   - `quality_gate: passed_full_text`：取得完整文章正文、完整字幕、完整转写，或足够完整的一手材料；可以触发 WikiCompiler。
   - `quality_gate: partial_excerpt_only`：只取得节选、摘要、章节、简介、二手报道或少量引用；默认只能写入 `raw/articles/` 作为线索，不得创建或更新长期 `wiki/` 页面。
   - `quality_gate: partial_excerpt_only` 的例外：如果节选本身来自可核验的一手官方来源，且正文明确标注为官方节选，可以在用户明确要求后编译进 `wiki/`，但页面必须在开头标注“仅覆盖官方节选范围，不是完整 transcript / 全文”，不得把缺失部分推断成知识。
   - `quality_gate: failed_no_transcript_or_full_article`：视频/播客没有字幕、转写或完整文字版；只能写入 `raw/articles/` 作为待抓取任务，不得编译。
   - 只要无法判断完整性，默认按未通过处理；不要为了“先存进去”而把简介、目录、章节、二手摘要改写成概念页。
3. **提取要点**：仅对通过质量门槛或符合“官方节选例外”的材料提取核心观点、可操作知识、与已有知识的关联。未通过材料只记录来源、已尝试方法、缺口和下一步抓取任务。
4. **写入 raw**：存入 `raw/articles/YYYY-MM-DD-{slug}.md`，格式如下：

```markdown
---
title: 文章标题
date: YYYY-MM-DD
source_url: https://...
source_type: article | video | podcast | tweet | paper
author: 作者（如果能识别）
status: captured | needs_full_text | compiled | deferred
quality_gate: passed_full_text | partial_excerpt_only | failed_no_transcript_or_full_article
tags: [tag1, tag2]
---

## 核心观点

- 要点 1
- 要点 2

## 关键细节

详细内容...

## 与已有知识的关联

- 与 [[existing-page]] 相关：...

## 原始引用

> 值得保留的原文片段
```

5. **触发 WikiCompiler**：只有 `quality_gate: passed_full_text`，或符合“官方节选例外”的 `quality_gate: partial_excerpt_only` raw，才能编译进 wiki 对应页面。后者编译后可标记 `status: compiled`，但相关 wiki 页面必须保留节选边界；未通过质量门槛的 raw 必须保持 `status: needs_full_text` 或 `status: deferred`，并在正文开头明确写出“不得编译进长期 wiki 页面”。
6. **确认**：告诉用户存了什么、更新了哪些 wiki 页面；如果没有通过质量门槛，必须明确说明“只保存为待抓取线索，未编译进长期 wiki”。

### Note Ingest（快速碎片摄入）

用户说 `记一下：...`、`沉淀一下这个坑：...`、`这个以后要记住：...` 时：

1. **写入 raw note**：存入 `raw/notes/YYYY-MM-DD-HHMM-{slug}.md`，保持原始含义，不要过度改写。
2. **判断是否需要编译**：
   - 明确是长期事实、配置、踩坑、偏好 → 可立即编译进相关 wiki 页面。
   - 只是临时想法或待办 → 先只保存在 `raw/notes/`，等复盘时再整理。
3. **刷新派生页并同步 Obsidian**：运行 `llm-wiki-refresh`，让 note 出现在 `Today.md` 与镜像目录的 `raw/notes/`，同时更新项目仪表盘和健康检查。
4. **定期复盘 notes 队列**：运行 `llm-wiki-promote-notes` 查看哪些 quick note 值得晋升到 `wiki/` 页面。该命令默认只读 dry-run，不自动修改长期知识库。

Note 文件格式：

```markdown
---
title: 简短标题
date: YYYY-MM-DD HH:MM
source_type: quick_note
status: captured | compiled | deferred
tags: [tag1, tag2]
---

# 简短标题

## 原始记录

用户原话或最小改写后的内容。

## 初步归类

- 类型：fact | pitfall | preference | todo | idea | command
- 相关页面：[[existing-page]] 或 待定

## 编译建议

- 是否需要进入 wiki：yes | no | later
- 建议目标：projects/tools/concepts/decisions/queries
```

### 触发方式

| 用户说 | 动作 |
|--------|------|
| "存一下这篇：[URL]" | 抓取 → 提取 → raw/articles/ → WikiCompiler |
| "记一下：[内容]" | `llm-wiki-note` → raw/notes/ → llm-wiki-refresh |
| "沉淀一下这个坑：[内容]" | raw/notes/ → 优先编译到相关工具/概念页 → llm-wiki-refresh |
| "记录" / "知识沉淀" | 当前会话 → raw/sessions/ → WikiCompiler → llm-wiki-refresh |
| "这个有用：[URL/内容]" | 同上 |
| "复盘 quick notes" | `llm-wiki-promote-notes` → 输出待晋升队列 |
| "检查 wiki 健康" | `llm-wiki-health` → 输出脚本化健康检查 |
| "compile wiki" / "处理 raw" | 处理 raw/ 下所有未编译的文件 |

### Query（查询知识库）

1. 先读 `wiki/index.md` 定位相关页面
2. 读取相关页面
3. 综合回答
4. 如果答案有价值 → 存入 `wiki/queries/`

### Lint（维护健康度）

定期检查：
- 孤立页面（无入链）
- 断链（`[[target]]` 不存在）
- 过时页面（超过 30 天未更新且标记 active）
- 矛盾信息（同一主题不同页面说法冲突）

可脚本化健康检查使用：

```bash
llm-wiki-health
llm-wiki-health --json
llm-wiki-health --strict
```

`--strict` 在发现问题时返回非零退出码，适合定期任务或 CI。敏感信息检查只输出文件路径和关键词类别，不展开内容。

### Promote Notes（复盘 quick notes）

`raw/notes/` 不应无限堆积。定期运行：

```bash
llm-wiki-promote-notes
llm-wiki-promote-notes --json
```

它会按 `pitfall / preference / todo / command / decision / fact` 归类 quick notes，并建议目标页面。默认只读，不自动合并到 `wiki/`。

### Refresh（沉淀后自动刷新）

每次 `WikiCompiler` 完成 ingest 后必须运行：

```bash
llm-wiki-refresh
```

该命令是知识沉淀后的标准收尾动作，负责：

1. 生成/更新 `Today.md`：今日新增记录、今日更新页面、今日踩坑、今日决策、待复盘事项。
2. 生成/更新 `wiki/dashboards/projects.md`：按 `active / paused / completed` 汇总项目状态。
3. 生成/更新 `wiki/dashboards/health.md`：检查 30 天未更新页面、active 项目下一步、未编译 raw、断链和工具页敏感关键词。
4. 自动调用 `llm-wiki-obsidian-sync`，同步到 `LLM_WIKI_MIRROR` 指定的镜像目录（未设置时跳过）。

只有在调试生成结果、不想同步 Obsidian 时，才使用：

```bash
llm-wiki-refresh --no-sync
```

## 链接约定

- 使用 `[[wiki-link]]` 格式互链 wiki 页面
- 链接目标 = 文件名（不含 .md），如 `[[example-project]]`
- 引用 raw 源材料用相对路径：`raw/sessions/2026-05-16-xxx.md`

## 写作风格

- 中文为主，技术术语保留英文
- 简洁直接，不要废话
- 每个页面开头一句话概括
- 代码块标注语言
- 日期格式：YYYY-MM-DD

## Graphify 集成

wiki 目录配置了 Graphify 图谱构建：

```bash
# 增量更新（推荐，只处理变更文件）
graphify update ~/llm-wiki/wiki/

# 完整重建
graphify ~/llm-wiki/wiki/
```

图谱输出在 `~/llm-wiki/graphify-out/`，用于：
- 发现跨页面的隐藏关联
- 社区检测（哪些概念聚集在一起）
- 查询时提供图谱上下文
