---
name: SessionCapture
description: 捕获当前会话的关键内容并存入 LLM Wiki。USE WHEN 用户说"记录", "记住这次", "存档", "结束并记录", "save session", "capture", OR 在 InfiniteChat 结束流程中自动触发。不要用于“记一下：...”快速碎片；那应写入 raw/notes/。
---

# SessionCapture

将当前 OpenCode/OMO 会话的关键内容提取为结构化摘要，存入 `~/llm-wiki/raw/sessions/`，然后触发 WikiCompiler 更新 wiki，并自动刷新 Obsidian 阅读层。

## 触发词

- "记录" / "记住这次" / "存档"
- "结束并记录" / "save session"
- "capture"
- InfiniteChat 结束流程自动触发

不要把 `记一下：...` 当作完整会话捕获；它是 quick note 入口，应调用/等价于：

```bash
llm-wiki-note "用户要记的一句话或片段"
```

`llm-wiki-note` 会写入 `raw/notes/` 并运行 `llm-wiki-refresh`，让 Today、健康检查和 Obsidian 镜像同步更新。

## 工作流

### Step 1: 回顾当前会话

回顾本次会话的完整对话，提取：

1. **做了什么**（完成的任务、产出物）
2. **关键决策**（为什么选 A 不选 B）
3. **涉及的项目**（哪些项目/模块被讨论或修改）
4. **新学到的概念/工具/技巧**
5. **踩坑/失败经验**
6. **待办/后续动作**

### Step 2: 生成结构化摘要

写入文件：`~/llm-wiki/raw/sessions/YYYY-MM-DD-{slug}.md`

slug 规则：用 2-4 个英文单词概括主题，kebab-case。

文件格式：

```markdown
---
title: 会话主题（中文简述）
date: YYYY-MM-DD
duration: 约 X 分钟（估算）
projects: [project-slug-1, project-slug-2]
concepts: [concept-1, concept-2]
tools: [tool-1, tool-2]
tags: [tag1, tag2]
---

## 概述

一段话概括本次会话做了什么、达成了什么。

## 完成事项

- [ ] 或 [x] 列出具体完成的任务

## 关键决策

### 决策1标题
- **背景**：为什么需要做这个决策
- **选项**：考虑了哪些方案
- **结论**：最终选了什么，为什么

## 新知识

- 学到的概念、工具用法、技巧

## 踩坑记录

- 遇到的问题和解决方式

## 后续动作

- 下次需要继续做的事
```

### Step 3: 触发 WikiCompiler

摘要写入后，立即调用 WikiCompiler skill 处理新的 raw 文件：

```
skill(name="WikiCompiler", user_message="~/llm-wiki/raw/sessions/YYYY-MM-DD-slug.md")
```

### Step 4: 确认完成

WikiCompiler 完成后，立即运行标准收尾命令：

```bash
llm-wiki-refresh
```

该命令会生成 `Today.md`、项目仪表盘、健康检查，并同步到 `LLM_WIKI_MIRROR` 指定的镜像目录（未设置时跳过同步）。不要再要求用户手动运行 `llm-wiki-obsidian-sync`。

### Step 5: 确认完成

告诉用户：
- 摘要已存入 `raw/sessions/xxx.md`
- wiki 已更新哪些页面
- 已运行 `llm-wiki-refresh`，Obsidian 镜像已更新
- 建议运行 `graphify ~/llm-wiki/wiki/ --update` 更新图谱（或自动运行）

## 注意事项

- 如果会话内容很少（纯闲聊、单个小问题），可以跳过或生成极简摘要
- 不要在摘要中包含敏感信息（API key、密码、token）
- 如果一次会话涉及多个不相关主题，可以生成多个 raw 文件
- slug 不要用中文，保持文件名全英文
