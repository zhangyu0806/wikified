# 事件完整性与页面治理互操作

本说明对应 enrich 的有界完整事件读取和保守 frontmatter 解析。
它是引擎契约，不表示已经提供云端同步、全 YAML 支持、内容版本审核或原生客户端。

## 先计算有效历史，再限制候选

事件召回不再把前 5000 行当成全部历史。处理顺序为：

1. 有界枚举并读取事件文件集合，校验读取前后观察到的状态。
2. 校验记录、跨文件身份和已经生效的替代关系。
3. 排除已关闭、废弃或当前不可用的记录。
4. 按调用者权限、查询与候选数量过滤。

因此较后行或另一个月份中的修正仍可关闭旧记录。文件名排序不决定哪个结论有效。
相同 ID 与相同规范化 JSON 内容的副本被视为幂等重复；有效同 ID 内容冲突或有效替代环使查询失败。

| 完整读取边界 | 当前上限 |
| --- | ---: |
| 全部物理行（含空行） | 50,000 |
| 每行源字节（含换行） | 256 KiB |
| 全部 JSONL 源字节 | 16 MiB |
| JSONL 文件 | 512 |
| events 目录条目（含非 JSONL） | 1,024 |

恰好到上限且确实 EOF 可以成功；超过边界不会返回旧的部分事实。
16 MiB 指输入源字节，不是精确进程内存限制；解析后的对象和集合会占用更多内存。
此改动不增加永久索引、自动压缩、历史裁剪或数据迁移。

## 已生效的关闭关系不会隐式回滚

合法终态修订与“该修订正文是否可见”分开处理：

- 同 domain、同非空 project、时间已生效的 approved 修订可以关闭前任。
- rejected 修订仅能关闭对应 pending 提案，不能用拒绝动作抹掉已认可事实。
- pending、未来生效、无效时间区间、跨 domain/project 或双方无 project，不产生替代权限。
- 替代记录 deprecated、过期、再次被替代或对当前 profile 更窄，都不会令其祖先复活。
- 写入器同步保留终态判断；废弃批准/拒绝修订不使旧 pending 再次可审批。

可能的正确结果是“目前没有可复用结论”。恢复旧观点应是一条新的明确记录，而不是删除关闭关系。
本轮仅对齐 writer 的终态语义；writer 既有全量读取尚未采用 enrich 的资源边界。

## 不可完成时显式失败

普通事件查询遇到以下问题返回退出码 4、固定错误分类和安全建议，不输出事件前缀或伪完整的 wiki-only 候选：

| 分类 | 原因 / 处理 |
| --- | --- |
| record-count-limit / line-byte-limit / total-byte-limit / file-count-limit / directory-entry-limit | 输入超过完整性预算；先检查容量方案，不能靠忽略尾部继续 |
| snapshot-changed | 观察到追加、替换、目录/父目录变化；待写入完成后重试 |
| unreadable / unsafe-event-directory / unsafe-event-file | 读取失败、路径逃逸或不安全链接；先检查目录权限和布局 |
| malformed-jsonl / unknown-event-schema | JSON 损坏、重复 key、非有限值或无法解释的 schema；不能假设缺失记录不是撤回 |
| identity-conflict / revision-cycle | 有效历史有歧义；人工检查，禁止自动任选一个 |

已知 schema 下明确无效的治理记录仍按条拒绝，且没有授予/撤销权限；诊断仅含拒绝数量。
这不等于修复那些记录。未知 schema 和损坏 JSON 相比旧版“跳过继续”是有意收紧，升级前应在授权范围内检查兼容性。

MCP 将非零 CLI 退出作为工具错误传递，不当作正常空搜索结果。
只读页面和静态 session-start 不需要读取事件快照，不因无关事件损坏一并阻断；它们也不声称完成了事件检索。

读取器拒绝 memory/events 目录及文件的符号链接逃逸和 JSONL 硬链接；比较目录清单、父目录和文件状态。
这是“发现并发变化即拒绝”的观测机制，不是多文件原子事务，也不承诺抵抗完全控制同一 OS 用户文件系统的敌手。
读取不创建锁文件、不修改真实数据。应用层治理不是 OS 沙箱，见 [治理边界](GOVERNANCE.md)。

## Markdown 治理子集

搜索、read-page、session-start 统一先解释治理，再读取/排名允许的正文。

支持的常见输入：

- 顶层 bare、JSON 双引号、YAML 单引号键和单行字符串；BOM、LF/CRLF。
- target_profiles / target_agents 的受支持行内列表或缩进列表。
- mind2one 的受支持 block 对象，或产品输出的严格 JSON 单行对象。
- 产品 review-state 按语义映射：not-required/accepted/corrected 对应顶层 approved，pending/rejected 对应同名状态；AI 不得伪称 not-required。
- 可选 project:null；完整旧 ACL 四元组缺 ID 时仍用路径 fallback，移动后 fallback 会变，故新页面应显式 memory_id。

这是保守子集，不是通用 YAML 解析库：治理 anchor/alias/merge/tag、重复键、冲突别名、损坏/未闭合/超限前言、未知产品版本及身份/审核矛盾都会拒绝。
复杂的未知字段不被用于授权；嵌套来源中的 domain 等名称不会冒充顶层 ACL。解析器不重写文件或丢弃用户未知字段。

没有治理声明的旧文档保留原兼容行为；完整 domain/sensitivity/review/target 四维旧 ACL 保留显式权限。
只声明 1–3 维、仅有治理 ID/epistemic 而无 ACL、或显式空目标列表，均不得退回宽松旧默认。
带 mind2one 的页面要求完整顶层治理、支持的版本、稳定身份和语义一致的审核状态。

固定失败诊断不包含正文、字段值或私密路径。原有 wiki 范围、页面大小和检索覆盖限制并未由本改动全部消除。

## 验证与部署

在引擎代码检出目录运行（所有夹具都是新建的临时合成库）：

```sh
python3 tests/test-event-snapshot-integrity.py
bash tests/test-page-governance.sh
bash tests/test-event-lifecycle.sh
bash tests/test-proposal-lifecycle.sh
bash tests/test-policy-prefilter.sh
bash tests/test-security-boundaries.sh
bash tests/test-retrieval-eval.sh
bash tests/test-mcp-framing.sh
```

提交到 GitHub 与激活当前记忆工具不是同一件事。若个人命令链接到源码，合并/切换分支即可影响运行行为；先完成只读兼容检查、备份和回退安排，不自动修复真实库或重启服务。
