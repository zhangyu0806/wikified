"""Task-shaped synthetic-derived benchmark, never a real-user quality claim."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

BIN = Path(__file__).resolve().parent
# Deliberately include paraphrases with little lexical overlap. Misses are
# reported, not rewritten into easier queries or mistaken for ACL failures.
TOPICS = [
    ("sync", "同步冲突", "跨设备同步遇到冲突时保留双方文件，停止自动覆盖并人工合并。", ["同步冲突应该怎么处理？", "两台机器改了同一篇笔记怎么办？", "怎样避免跨设备更新覆盖我的修改？"], "workflow"),
    ("review", "人工审核", "AI 写入的提案保持 pending；人工审核前不能进入助手召回。", ["AI 记忆什么时候可以供助手使用？", "待审核提案会直接参与召回吗？", "如何把建议变成已确认记忆？"], "review"),
    ("rollback", "发布回退", "发布验证失败应恢复上一个服务版本，保留原数据与独立访问密钥。", ["新版本验证失败如何回退？", "发布出错后怎么恢复原服务？", "升级失败要不要重建整个资料库？"], "operations"),
    ("identity", "来源版本", "资料使用稳定 memory_id 和完整 SHA256 revision；内容变化后重新选择当前版本。", ["怎么保证引用仍是我看过的版本？", "笔记改名会改变稳定身份吗？", "来源版本变化以后旧引用怎么办？"], "provenance"),
    ("history", "历史架构决策", "2024 年的历史架构评审比较了数据库与 Markdown，最终选择 Markdown 和 Git 作为可移植主数据。", ["当时为什么选择 Markdown 与 Git？", "2024 年架构评审讨论过哪些存储方式？", "原来的存储决策背景是什么？"], "history"),
    ("readonly", "云端只读副本", "云端提供只读副本，本机是唯一写入端；同步为最终一致，需要核对已验证快照。", ["为什么云端不能修改原笔记？", "网页看到的内容是不是刚保存的？", "远端副本和本地主库谁负责写入？"], "replica"),
    ("capture", "会话捕获状态", "手动捕获成功不能证明自动 hook 已触发。自动捕获必须有真实完成事件证据。", ["手动测试通过代表自动捕获正常吗？", "怎样确认会话 hook 确实执行了？", "没有自动触发证据时应显示什么状态？"], "capture"),
    ("budget", "上下文预算", "先看项目简报与短摘录，需要时按固定版本读取证据块；超出上下文预算必须明确提示。", ["长篇资料怎样控制上下文预算？", "能先只给简报再按需看原文吗？", "引用太大时助手应怎么继续？"], "budget"),
]
NEGATIVE = ["下周火星港口的潮汐时刻是什么？", "量子海豚烤面包协议 zyxwvut", "谁赢得了不存在的第九十九届月球杯？"]

def run_task_suite():
    with tempfile.TemporaryDirectory(prefix="wikified-task-eval-synthetic-") as temporary:
        root = Path(temporary)
        for directory in ("policy", "notes", "memory/events"):
            (root / directory).mkdir(parents=True)
        (root / "policy/access.json").write_bytes((BIN.parent / "templates/access-policy.json").read_bytes())
        selections = {}
        def page(key, title, body, **changes):
            fields = dict(memory_id="eval:" + key, domain="work", sensitivity="internal", review_state="approved",
                epistemic_status="human-stated", target_profiles=["codex", "opencode"])
            fields.update(changes)
            path = root / "notes" / (key + ".md")
            path.write_text("---\n" + "\n".join(json.dumps(k) + ": " + json.dumps(v) for k, v in fields.items()) + "\n---\n# " + title + "\n" + body, encoding="utf-8")
            selection = dict(kind="page", memoryId="eval:" + key, revision="sha256:" + hashlib.sha256(path.read_bytes()).hexdigest())
            selections[key] = selection
            return selection
        for key, title, body, _, _ in TOPICS:
            page(key, title, body)
        page("pending", "待审核实验路线", "未审核实验方向是钴蓝飞艇路线。", review_state="pending")
        page("private", "仅本人项目", "私人项目暗号是松果铃铛。", target_profiles=["human"])
        page("large", "完整证据超预算", "合成长文。" * 4000)
        changed = page("changed", "版本变化", "合成旧版本。")
        page("changed", "版本变化", "合成新版本。")
        env = {k: v for k, v in os.environ.items() if not k.startswith("LLM_WIKI_")}
        env.update(LLM_WIKI_ROOT=str(root), PYTHONDONTWRITEBYTECODE="1")
        def cli(name, args):
            output = subprocess.run([sys.executable, "-B", str(BIN / name), *args], env=env, text=True, capture_output=True, timeout=15, check=True)
            return json.loads(output.stdout)
        questions = []
        for key, _, _, queries, category in TOPICS:
            questions.extend((query, key, category) for query in queries)
        questions.extend((query, None, "negative") for query in NEGATIVE)
        questions.extend((query, None, "negative-paraphrase") for query in ["月球杯冠军到底是谁？", "如何安装 zyxwvut 量子海豚烤箱？", "不存在的星际航班明天几点到？"])
        questions.extend((query, None, "permission") for query in ["钴蓝飞艇路线是什么？", "待审核实验方向能直接使用吗？", "实验路线的待审核内容是什么？", "松果铃铛代表哪个私人项目？", "仅本人项目的暗号是什么？", "私人项目松果铃铛详情？"])
        rows, ranks, violations = [], [], []
        for index, (query, key, category) in enumerate(questions, 1):
            results = cli("llm-wiki-enrich", ["--agent-profile", "codex", "--query", query, "--ranking", "hybrid", "--limit", "5", "--max-chars", "8000", "--json"])
            ids = [item.get("memory_id") for item in results]
            rank = ids.index("eval:" + key) + 1 if key and "eval:" + key in ids else None
            if key:
                ranks.append(rank)
            if set(ids) & {"eval:private", "eval:pending"}:
                violations.append("task-search-permission-leak")
            rows.append(dict(id=f"task-{index:02d}", question=query, category=category, expectedMemoryId="eval:" + key if key else None,
                rank=rank, returned=len(results), result="hit" if rank else "miss" if key else "abstained" if not results else "unexpected-results"))
        context_rows = []
        for profile in ("codex", "opencode"):
            for key, expected in [(key, "ready") for key, *_ in TOPICS] + [("pending", "unavailable"), ("private", "unavailable"), ("large", "too-large"), ("changed", "changed")]:
                selection = changed if key == "changed" else selections[key]
                result = cli("llm-wiki-context", ["--root", str(root), "--agent-profile", profile, "--request", json.dumps(dict(schemaVersion=1, selections=[selection]))])
                item = result["items"][0]
                passed = item["status"] == expected and (expected == "ready" or "text" not in item)
                context_rows.append(dict(profile=profile, memoryId=selection["memoryId"], expected=expected, actual=item["status"], passed=passed))
                if not passed:
                    violations.append("task-context-boundary-failed")
        unanswerable = [row for row in rows if row["expectedMemoryId"] is None]
        return dict(schemaVersion=1, fixture="synthetic-derived-task-questions", realUserLabels=False, questionCount=len(rows),
            scope="search ranking and exact-context admission; not assistant answer quality or actual new-session continuity",
            search=dict(profile="codex", ranking="hybrid", answerable=len(ranks), recallAt5=round(sum(rank is not None for rank in ranks)/len(ranks), 6),
                mrr=round(sum(1/rank if rank else 0 for rank in ranks)/len(ranks), 6),
                abstentionRate=round(sum(row["returned"] == 0 for row in unanswerable)/len(unanswerable), 6), cases=rows),
            context=dict(operation="prepare_context", queriesDoNotRankThisOperation=True, cases=context_rows,
                passed=sum(row["passed"] for row in context_rows), total=len(context_rows)), violations=sorted(set(violations)))
