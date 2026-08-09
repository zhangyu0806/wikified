---
name: WikiCompiler
description: 处理 raw 源材料并更新 LLM Wiki 知识库页面。USE WHEN 新的 raw 文件需要被整合进 wiki，或用户说"compile wiki", "更新wiki", "ingest"。由 SessionCapture 自动调用。
---

# WikiCompiler

读取 `~/llm-wiki/raw/` 中的新源材料，提取实体和知识，更新 `~/llm-wiki/wiki/` 中的页面。

## 触发方式

- 由 SessionCapture skill 自动调用（传入新 raw 文件路径）
- 由 Article Ingest 流程自动调用（传入新 raw/articles/ 文件路径）
- 用户手动说 "compile wiki" / "更新wiki" / "ingest"
- 用户手动放文件到 raw/ 后说 "处理一下"
- 用户说 "存一下这篇" / "这个有用" + URL/内容

## 前置条件

**必须先读 `~/llm-wiki/SCHEMA.md`**，了解页面类型、格式约定和链接规则。

## 工作流

### Step 1: 确定待处理文件

- 如果传入了具体文件路径 → 处理该文件
- 如果没有 → 读取 `wiki/log.md` 最后一条记录的时间，找出之后新增的 raw 文件

### Step 2: 读取并分析源材料

读取 raw 文件，识别其中包含的：

| 实体类型 | 对应 wiki 目录 | 识别信号 |
|----------|---------------|----------|
| 项目 | `wiki/projects/` | 项目名、仓库、部署、架构讨论 |
| 概念 | `wiki/concepts/` | 技术概念、模式、方法论 |
| 决策 | `wiki/decisions/` | "选了X不选Y"、权衡讨论 |
| 工具 | `wiki/tools/` | 工具配置、命令、技巧 |

### Step 3: 更新或创建 wiki 页面

对每个识别出的实体：

#### 如果页面已存在：

1. 读取现有页面
2. **合并**新信息（不是覆盖！）
   - 追加新的事实、经验、代码示例
   - 更新状态信息（如项目进度）
   - 在 sources 列表中添加新的 raw 引用
   - 更新 `updated` 日期
3. 检查是否有矛盾信息 → 如有，标注 `⚠️ 矛盾` 并保留两个版本

#### 如果页面不存在：

1. 按 SCHEMA.md 中定义的模板创建新页面
2. 填入从 raw 中提取的信息
3. 添加 `[[wiki-link]]` 链接到相关页面

### Step 4: 更新 index.md

在 `wiki/index.md` 对应分类下添加新页面条目：

```markdown
- [[slug]] — 一句话描述 (YYYY-MM-DD)
```

### Step 5: 更新 overview.md（如需要）

如果新信息改变了全局理解（新项目、重大决策、方向变化），更新 overview.md。

不是每次 ingest 都需要更新 overview — 只在有实质性变化时更新。

### Step 6: 写入 log.md

在 `wiki/log.md` 顶部（`---` 之后）追加：

```markdown
- **YYYY-MM-DD HH:MM** | ingest | `raw/sessions/xxx.md` → 更新: [[page1]], [[page2]]; 新建: [[page3]]
```

### Step 7: 运行 Graphify（可选）

如果更新了 3+ 个页面，自动运行：

```bash
graphify ~/llm-wiki/wiki/ --update
```

否则提示用户可以手动运行。

### Step 8: 刷新 Obsidian 镜像（必须）

每次 ingest 完成后运行：

```bash
llm-wiki-refresh
```

这会自动生成 `Today.md`、`wiki/dashboards/projects.md`、`wiki/dashboards/health.md`，并调用 `llm-wiki-obsidian-sync` 同步到 `LLM_WIKI_MIRROR` 指定的镜像目录（未设置时跳过同步）。不要把同步作为用户需要手动执行的额外步骤。

## 合并规则

- **追加优先**：新信息追加到已有内容后面，不删除旧内容
- **时间标注**：重要更新标注日期 `(2026-05-16 更新)`
- **来源追踪**：每段新信息标注来自哪个 raw 文件
- **矛盾处理**：不要静默覆盖，标注矛盾让用户决定
- **链接维护**：新页面创建后，检查已有页面是否应该链接到它

## 页面命名规则

- 文件名全英文 kebab-case：`payment-gateway.md`, `db-migration.md`
- 中文标题放在 front-matter 的 `title` 字段
- 概念页用概念本身命名：`jwt-auth.md`, `llm-wiki-pattern.md`
- 项目页用项目名：`payment-gateway.md`, `log-collector.md`
- 决策页带日期前缀：`2026-05-16-wiki-architecture.md`

## 质量标准

- 每个页面必须有 front-matter
- 每个页面开头一句话概括
- 至少一个 `[[wiki-link]]` 链接到其他页面
- 不要生成空页面或只有标题的页面
- 不要包含敏感信息（密码、token、key）
