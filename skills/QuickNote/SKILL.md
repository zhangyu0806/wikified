---
name: QuickNote
description: 快速记录零散事实、偏好、坑、命令和待办到 LLM Wiki 的 raw/notes/。USE WHEN 用户说“记一下：...”、“这个记住”、“沉淀一下这个坑”、“快速记录”、“note this”，且内容不是完整会话总结。
---

# QuickNote

把用户的一小段事实、偏好、坑、命令、待办或灵感写入 `~/llm-wiki/raw/notes/`，然后刷新 `Today.md`、项目仪表盘、健康检查和 Obsidian 镜像。

## Customization

**Before executing, check for user customizations at:**
`~/.claude/skills/PAI/USER/SKILLCUSTOMIZATIONS/QuickNote/`

If this directory exists, load and apply:
- `PREFERENCES.md` - User preferences and configuration
- Additional files specific to the skill

These define user-specific preferences. If the directory does not exist, proceed with skill defaults.

## 触发方式

使用本 skill，当用户表达的是“快速记一条”，例如：

- `记一下：...`
- `这个记住：...`
- `快速记录：...`
- `沉淀一下这个坑：...`
- `note this: ...`

不要把这些 quick note 当成完整 SessionCapture。只有用户明确说“知识沉淀”“记录本次会话”“结束并记录”时，才走 SessionCapture。

## 工作流

1. 提取用户要保存的正文，去掉触发前缀（如 `记一下：`）。
2. 如果正文为空，问一个精确问题：要记录什么内容？
3. 运行：

```bash
llm-wiki-note "正文"
```

4. 如果用户提供了明显标题，可运行：

```bash
llm-wiki-note --title "标题" "正文"
```

5. 默认不要加 `--no-sync`。`llm-wiki-note` 会自动运行 `llm-wiki-refresh`，刷新 Today、dashboards、health 和 Obsidian 镜像。
6. 返回简短结果：保存的 note 路径、是否已 refresh/sync。

## 内容分类提示

不用强制分类，但可以在回复里轻量标注可能归类：

- `pitfall`：踩坑、失败原因、排查经验
- `preference`：用户偏好、默认流程
- `todo`：之后要做的事
- `command`：常用命令、路径、脚本
- `fact`：事实、配置状态、环境信息

后续可由 `llm-wiki-promote-notes` 扫描 notes 并建议晋升到长期 wiki 页面。

## 质量与安全

- 不要把密码、token、API key、验证码写入 note 正文。
- 如果用户给了疑似敏感信息，只记录“某服务凭据位于某 secure-notes 文件”，不要记录值。
- 不要改写用户事实；只做必要的标题化和前缀清理。
- 不要把 quick note 合并进 `wiki/`，除非用户明确要求整理/编译。
