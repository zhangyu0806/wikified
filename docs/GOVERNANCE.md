# Wikified 治理与权限边界（Phase 1）

Wikified 的长期方向是让人和 AI 使用同一套**逻辑记忆基底**：人的笔记、复盘与已确认
知识，以及 AI 提出的候选记忆，都用可审计的 Markdown / JSONL 表达。逻辑统一不等于
权限混同；不同领域、Agent 和机器仍由独立的数据根、Git remote、凭据与检索策略隔离。

## 当前范围

Phase 1 先把现有编码记忆库治理好：默认只接入 `work` 领域，以 `Project` 页面和
`Decision` 页面 / event 作为记忆与执行系统之间的桥。它不会导入日记，不会建立图数据库，
不会实现完整的 Vision → Goal → OKR → Project → Task → Subtask 执行系统，也不会允许
AI 自动执行召回到的待办。

PARA 仍是内容的保存与整理方式；Vision / Goal / OKR / Project / Task / Subtask 属于执行
视图。二者可以引用同一记忆对象，但不应把任务层级复制成第二套记忆库。

## 逻辑统一，物理分区

- 编码 Agent 使用现有私有 `work` 数据仓，通常是 `~/llm-wiki`。
- `personal` 使用另一个 `LLM_WIKI_ROOT`、私有 remote、凭据和索引。不要只靠一枚
  `domain` 标签把日记与工作 Agent 放进同一个可读数据根。
- 公有 `wikified` 仓只发布机制、模板和测试，不保存任何人的记忆正文。
- 当前没有自动跨域搬运。确需把个人洞察用于工作时，由人明确新建一条 `work` 记忆，
  保留 `evidence_refs`，并指定当前 `project` 与目标 Agent。写给 `*` 的全局记忆需要再次
  人工确认，不能由 Agent 自行扩权。

`policy/access.json` 是 Wikified CLI / MCP 的应用层边界，不是操作系统沙箱。若某个 Agent
拥有任意本机 shell 和该数据根的文件读取权限，它可以绕过 MCP 直接读文件；真正的硬隔离
必须依靠独立数据根、OS 权限 / 账号和凭据，而不只是 metadata。

## 每条记忆的治理身份

| 字段 | 含义 |
|---|---|
| `memory_id` | 跨检索结果引用的稳定身份；event 为 `event:<id>`，wiki 页推荐显式填写 |
| `domain` | `work` 或 `personal` |
| `sensitivity` | `public`、`internal`、`confidential`、`restricted` |
| `epistemic_status` | 事实如何得知；v3 event 支持 `human-stated`、`ai-proposed`、`human-confirmed` 等 |
| `review_status` / `review_state` | `pending`、`approved` 或 `rejected` |
| `target_agents` / `target_profiles` | 哪些固定 Agent profile / 目标组可以收到它；`*` 表示所有经策略允许的 profile |
| `project` | 可选的稳定项目标识；用于进一步收窄，不用于扩大权限 |

event 的规范字段名是 `review_status`、`target_agents`；wiki frontmatter 的规范字段名是
`review_state`、`target_profiles`。读取器兼容这两组别名。

新 wiki 页应显式写 `memory_id`。旧页没有该字段时，读取器以相对 `wiki/` 路径的哈希生成
`wiki:<20-hex>`；内容编辑不会改变它，但移动文件会改变 fallback id，所以移动前应先补
显式 id。旧 v1/v2 event 不被批量改写：访问兼容层默认按
`work / internal / approved / ["*"]` 处理，其来源语义应视为 `legacy-imported`，而不是
伪装成已经核验的新记录。

## 策略先于内容

`llm-wiki-init` 创建 `policy/access.json`。检索器先加载并严格校验策略，再读取正文、分词或
排序；策略缺失/损坏、profile 未知、未知 event schema、v3 治理字段不完整，或疑似但格式
损坏的 frontmatter 都会拒绝访问（fail closed）。默认 profile：

| profile | 可读领域 | 最高敏感度 | 可读状态 | raw / 直接全局图 |
|---|---|---|---|---|
| `codex`、`opencode`、`claude`、`cursor`、`grok`、`coding` | `work` | `internal` | `approved` | 否 / 否 |
| `planning` | `work` | `confidential` | `approved` | 否 / 否 |
| `reflection` | `personal`、`work` | `restricted` | `approved` | 否 / 否 |
| `human` | `personal`、`work` | `restricted` | `pending`、`approved` | 是 / 是 |

具体值以数据仓内的 `policy/access.json` 为准。`llm-wiki-harness configure` 会把 Codex、
OpenCode 等服务进程绑定到各自的 `LLM_WIKI_AGENT_PROFILE` 和 `work` domain；MCP tool
参数里没有 profile 字段，prompt 不能把自己声明成 `human`。命令行的 `--agent-profile`
用于数据仓所有者的本地诊断与审核，不应暴露给不可信远端调用者。

默认策略还定义目标组：`coding` 可被 Codex、OpenCode、Claude、Cursor、Grok 与通用
`coding` profile 接收。人工新事件默认写给该组，避免把 `*` 当作隐式默认；任何显式全局
目标都必须由人传 `--confirm-global-target` 再确认。legacy v1/v2 的 `*` 只用于无损兼容，
不意味着新记录也应继续全局投递。

Codex / OpenCode 的 MCP 还固定 `LLM_WIKI_TARGET_AGENTS=codex,opencode`：任一端提出的
event 经人批准后可被两端共享。它只决定提案的目标集合，不能突破任一 profile 的
domain、sensitivity 或 review 限制。

Phase 1 不让编码 Agent 列出 `raw/`，也不让 MCP 直接遍历未分区的全局 Graphify 图。
`find_related` 暂时走同一个策略过滤后的文本检索路径；只有将来按 profile / domain 分区
图索引后，才应恢复 Agent 图遍历。

## AI 提案的人工闸门

MCP `record_event` 只会写入 `actor.type=ai`、`epistemic_status=ai-proposed`、
`review_status=pending` 的 v3 event。pending 记录会持久保存以供审计，但不会进入
Codex / OpenCode 的正常召回，也不会写入 touched-file 反向索引。

```bash
llm-wiki-review                    # 查看待审核 AI 提案
llm-wiki-event --approve <event-id> # 追加 human-confirmed/approved 修订
llm-wiki-event --reject  <event-id> # 追加 disputed/rejected 修订
```

批准和拒绝只接受当前尚未被处理的 pending event，并在持锁后再次检查，因此重复审核与
并发双审会被拒绝。两种操作都追加新 event，并通过 `supersedes` 指向原提案；历史行不原地
修改或删除。若提案还要替代其他事件，审核视图会展示这些对象，批准者必须额外传
`--confirm-supersedes`。pending 本身永不压掉已批准事实；只有人类批准的新修订能进入编码
Agent 召回。全局目标另需 `--confirm-global-target`。

## Phase 1 之后

后续阶段才考虑个人笔记 / 日记连接器、跨域晋升界面、分区图索引和更完整的执行视图。
任何扩展都应保留三个不变式：权限判断早于正文读取，AI 不能自批，召回证据不是当前任务
或行动授权。
