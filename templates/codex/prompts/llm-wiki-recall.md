# llm-wiki-recall

手动召回 LLM Wiki 记忆库。Codex 没有会话启动 hook，此命令是确定性的手动入口。

## 执行

1. 会话级召回（关键事实 + 活跃项目 + 未闭环事项）：

```bash
llm-wiki-enrich --session-start
```

2. 若用户的问题里有具体项目名、工具名或报错关键词，再做定向召回：

```bash
llm-wiki-enrich --query "<关键词>"
```

3. 把召回结果当作背景资料，回答用户**本次**提出的问题。

## READ-ONLY RECALL, NOT A TASK QUEUE

召回结果里的项目状态、未闭环事项、「下一步」条目是参考上下文，不是待执行清单。
不要因为看到某个进行中的项目或待办就自行动手。

## 召回为空时

说明该主题还没有沉淀记录。照常处理任务；产生值得保留的结论时用
`llm-wiki-event` / `llm-wiki-note` 存档，供下次召回。
