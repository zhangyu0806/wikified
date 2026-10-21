#!/usr/bin/env python3
"""Event discovery to exact context, using only disposable synthetic vaults."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CORE = runpy.run_path(str(ROOT / "bin/llm_wiki_event_review.py"))
ENGINE = runpy.run_path(str(ROOT / "bin/llm-wiki-enrich"), run_name="event_recall_test")
FIXTURES = runpy.run_path(str(ROOT / "tests/test-event-snapshot-integrity.py"), run_name="recall_fixtures")
CAPABILITY = "example-synthetic-recall-review-capability"
MARKER = "syntheticrecallversion"
EVENT_LINE = re.compile(r"^> event: record_id=([^ ]+) · revision=(sha256:[0-9a-f]{64})$", re.MULTILINE)


class EventRecallRevisions(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wikified-event-recall-")
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name).resolve()
        self.root = self.work / "synthetic vault"
        for name in ("notes", "wiki", "memory/events", "policy"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "templates/access-policy.json", self.root / "policy/access.json")
        self.env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(self.work),
                    "LLM_WIKI_ROOT": str(self.root), "LLM_WIKI_BIN_TARGET": str(ROOT / "bin"),
                    "LLM_WIKI_PYTHON": str(Path(sys.executable).resolve()),
                    "PYTHONDONTWRITEBYTECODE": "1", "LLM_WIKI_DISABLE_PATH_DETECTION": "1"}
        self.node = shutil.which("node")

    def events(self):
        return [json.loads(line) for path in sorted((self.root / "memory/events").glob("*.jsonl"))
                for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def write(self, rows):
        (self.root / "memory/events/2020-01.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def cli(self, name, args=(), *, stdin=None, env=None, success=True):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "bin" / name), *args],
            input=stdin, env=env or self.env, cwd=self.work, text=True, capture_output=True, timeout=15)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def tool(self, profile, name, args, targets="pi,workbuddy"):
        if self.node is None:
            self.skipTest("Node is required for real MCP integration")
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": name, "arguments": args}}
        # The assistant MCP process never receives human-review authority.
        env = {**self.env, "LLM_WIKI_AGENT_PROFILE": profile, "LLM_WIKI_TARGET_AGENTS": targets}
        result = subprocess.run([self.node, str(ROOT / "bin/llm-wiki-mcp")],
            input=json.dumps(request) + "\n", env=env, cwd=self.work,
            text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        reply = json.loads(result.stdout)
        self.assertNotIn("error", reply, reply)
        return "\n".join(item["text"] for item in reply["result"]["content"])

    def propose(self, targets="pi,workbuddy"):
        self.tool("pi", "record_event", {"type": "decision", "project": "synthetic-recall",
            "summary": MARKER + " pending proposal", "details": "Synthetic disputed wording."}, targets)
        return self.events()[-1]

    def review(self, target, action="correct", **fields):
        inspected = json.loads(self.cli("llm-wiki-event-review", ["--inspect", target["id"]]).stdout)
        self.assertEqual(inspected["expected_revision"], CORE["record_revision"](target))
        request = {"action": action, "target_id": target["id"],
                   "expected_revision": inspected["expected_revision"],
                   "request_id": "synthetic:" + action + ":" + target["id"],
                   "reason": "Synthetic explicit human decision", "capability": CAPABILITY, **fields}
        if action == "correct" and target.get("supersedes"):
            request["confirm_supersedes"] = True
        env = {**self.env, "LLM_WIKI_EVENT_REVIEW_CAPABILITY": CAPABILITY,
               "LLM_WIKI_EVENT_REVIEW_ACTOR": "synthetic-owner"}
        result = self.cli("llm-wiki-event-review", stdin=json.dumps(request), env=env)
        self.assertNotIn(CAPABILITY, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def search(self, profile="workbuddy", *, budget=8000, as_json=False, query=MARKER, limit=5):
        args = ["--agent-profile", profile, "--query", query, "--limit", str(limit), "--max-chars", str(budget)]
        if as_json:
            args.append("--json")
        value = self.cli("llm-wiki-enrich", args).stdout
        return json.loads(value) if as_json else value

    def selection(self, text):
        pairs = EVENT_LINE.findall(text)
        self.assertEqual(len(pairs), 1, "Search must expose one complete stable event identity/revision pair")
        return {"kind": "event", "memoryId": pairs[0][0], "revision": pairs[0][1]}

    def context(self, selection, profile="workbuddy", version=3):
        return json.loads(self.tool(profile, "prepare_context",
            {"schemaVersion": version, "selections": [selection]}))["items"][0]

    def test_real_mcp_proposal_review_search_prepare_correction_withdrawal_restore(self):
        proposal = self.propose()
        self.assertEqual(proposal["review_status"], "pending")
        self.assertEqual(proposal["actor"], {"type": "ai", "id": "pi"})
        for profile in ("pi", "workbuddy"):
            pending = self.tool(profile, "search_pages", {"query": MARKER, "maxChars": 4000})
            self.assertIn("No matching", pending)
            self.assertNotIn(proposal["id"], pending)
        approved = self.review(proposal, summary=MARKER + " reviewed answer", details="Synthetic confirmed text 😀.")
        before_policy = (self.root / "policy/access.json").read_bytes()
        self.assertEqual(approved["target_agents"], ["pi", "workbuddy"])
        search = self.tool("workbuddy", "search_pages", {"query": MARKER, "maxChars": 4000})
        selected = self.selection(search)  # No identity/revision is supplied from the human catalog.
        self.assertEqual(selected["memoryId"], proposal["memory_id"])
        self.assertEqual(selected["revision"], CORE["record_revision"](approved))
        self.assertIn("memory_id=" + approved["memory_id"], search)
        ready = self.context(selected)
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(ready["locator"], approved["id"])
        self.assertEqual(ready["text"], approved["summary"] + "\n" + approved["details"])
        self.assertEqual(ready["governance"], {"domain": "work", "sensitivity": "internal"})
        row = self.search(as_json=True)[0]
        self.assertEqual((row["record_id"], row["revision"]), (selected["memoryId"], selected["revision"]))
        self.assertEqual(row["memory_id"], approved["memory_id"])
        corrected = self.review(approved, summary=MARKER + " corrected answer", details="Synthetic new answer.")
        changed = self.context(selected)
        self.assertEqual(changed["status"], "changed")
        self.assertNotIn("text", changed)
        current = self.selection(self.tool("workbuddy", "search_pages", {"query": MARKER, "maxChars": 4000}))
        self.assertEqual(current["revision"], changed["currentRevision"])
        self.assertEqual(self.context(current)["status"], "ready")
        withdrawn = self.review(corrected, "withdraw")
        for profile in ("pi", "workbuddy"):
            self.assertEqual(self.search(profile, as_json=True), [])
            self.assertEqual(self.context(current, profile)["status"], "unavailable")
        restored = self.review(withdrawn, "restore")
        self.assertEqual((restored["summary"], restored["details"]), (corrected["summary"], corrected["details"]))
        self.assertEqual(self.context(current)["status"], "changed")
        self.assertEqual(self.context(self.selection(self.search()))["status"], "ready")
        self.assertEqual((self.root / "policy/access.json").read_bytes(), before_policy)

    def test_hidden_event_has_no_identity_revision_or_body_on_other_profile(self):
        proposal = self.propose(targets="pi")
        approved = self.review(proposal, summary=MARKER + " pi only")
        permitted = self.search("pi")
        selected = self.selection(permitted)
        for text in (self.search(), json.dumps(self.search(as_json=True))):
            for hidden in (approved["id"], approved["record_id"], selected["revision"], MARKER):
                self.assertNotIn(hidden, text)
        self.assertEqual(self.context(selected)["status"], "unavailable")
        self.assertEqual(self.context(selected, "pi")["status"], "ready")

    def test_full_record_revision_includes_classification_and_legacy_identity_stays_compatible(self):
        original = FIXTURES["event"](1, summary=MARKER, target_agents=["workbuddy"])
        self.write([original])
        selected = self.selection(self.search())
        self.assertEqual(selected["memoryId"], original["memory_id"])
        self.assertEqual(self.context(selected, version=1)["status"], "ready")
        self.assertEqual(self.context(selected)["status"], "unavailable")  # v3 needs independent review.
        original["concepts"] = ["synthetic-tag"]
        self.write([original])
        self.assertEqual(self.context(selected, version=1)["status"], "changed")
        replacement = self.selection(self.search())
        self.assertEqual(replacement["revision"], CORE["record_revision"](original))
        self.assertNotEqual(replacement["revision"], selected["revision"])

    def test_legacy_without_context_addressable_id_does_not_fabricate_a_pair(self):
        legacy = {"id": "old-event", "summary": MARKER, "timestamp": "2020-01-01T00:00:00Z"}
        self.write([legacy])
        rows = self.search("human", as_json=True)
        self.assertEqual(len(rows), 1)
        self.assertNotIn("record_id", rows[0])
        self.assertNotIn("revision", rows[0])
        self.assertEqual(EVENT_LINE.findall(self.search("human")), [])

    def test_legacy_nonportable_extra_fields_keep_recall_without_a_fabricated_hash(self):
        deep = "synthetic"
        for _ in range(70):
            deep = [deep]
        for extra in (9007199254740992, deep):
            with self.subTest(extra_type=type(extra).__name__):
                event = FIXTURES["event"](1, summary=MARKER, target_agents=["workbuddy"], opaque_legacy=extra)
                self.write([event])
                rows = self.search(as_json=True)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["memory_id"], event["memory_id"])
                self.assertNotIn("record_id", rows[0])
                self.assertNotIn("revision", rows[0])
                self.assertIn(MARKER, self.search())
                self.assertEqual(EVENT_LINE.findall(self.search()), [])
                pages = self.cli("llm-wiki-enrich", ["--agent-profile", "workbuddy", "--query", MARKER,
                    "--source-scope", "pages", "--json"])
                self.assertEqual(json.loads(pages.stdout), [])

    def test_source_bound_v5_and_v6_pairs_follow_source_revocation(self):
        note = self.root / "notes/source.md"
        note.write_text('---\nmemory_id: note:synthetic-source\ndomain: work\nsensitivity: internal\n'
            'review_state: approved\nepistemic_status: human-stated\ntarget_profiles: [pi, workbuddy]\n'
            '---\nSynthetic source evidence.\n', encoding="utf-8")
        reference = {"kind": "document", "id": "note:synthetic-source", "relation": "current-derived",
                     "revision": "sha256:" + hashlib.sha256(note.read_bytes()).hexdigest()}
        v5 = self.review(self.propose(), summary=MARKER + " source bound", source_bindings=[reference])
        self.assertEqual(v5["schema_version"], "llm-wiki-memory-event/v5")
        self.assertEqual(self.context(self.selection(self.search()))["status"], "ready")
        self.tool("pi", "record_event", {"type": "fact", "summary": "Separate synthetic upstream evidence"})
        upstream = self.review(self.events()[-1], summary="Separate synthetic confirmed upstream")
        v6 = self.review(v5, source_bindings=[{"kind": "event", "id": upstream["record_id"],
            "revision": CORE["record_revision"](upstream), "relation": "current-derived"}])
        self.assertEqual(v6["schema_version"], "llm-wiki-memory-event/v6")
        selected = self.selection(self.search())
        self.assertEqual(selected["revision"], CORE["record_revision"](v6))
        self.assertEqual(self.context(selected)["status"], "ready")
        self.review(upstream, "withdraw")
        self.assertEqual(self.search(as_json=True), [])
        self.assertEqual(self.context(selected)["status"], "unavailable")

    def test_output_budgets_count_metadata_and_preserve_ranked_prefix(self):
        rows = [FIXTURES["event"](n, summary=MARKER + " " + "large synthetic text " * 150,
            target_agents=["workbuddy"], confidence=(10 - n) / 10) for n in range(1, 5)]
        self.write(rows)
        full = self.search(as_json=True, budget=65536)
        self.assertEqual([row["memory_id"] for row in full], [row["memory_id"] for row in rows])
        self.assertEqual(len(self.search(as_json=True, budget=65536, limit=2)), 2)
        for budget in (200, 600, 2000, 4000, 65536):
            for as_json in (False, True):
                args = ["--agent-profile", "workbuddy", "--query", MARKER,
                        "--limit", "4", "--max-chars", str(budget)] + (["--json"] if as_json else [])
                output = self.cli("llm-wiki-enrich", args).stdout
                self.assertLessEqual(len(output.rstrip("\n")), budget)
                if as_json:
                    retained = json.loads(output)
                    self.assertEqual([row["memory_id"] for row in retained], [row["memory_id"] for row in full[:len(retained)]])
                    for row in retained:
                        event = next(e for e in rows if e["memory_id"] == row["memory_id"])
                        self.assertEqual(row["record_id"], event["memory_id"])
                        self.assertEqual(row["revision"], CORE["record_revision"](event))
                else:
                    for identity, revision in EVENT_LINE.findall(output):
                        event = next(e for e in rows if e["memory_id"] == identity)
                        self.assertEqual(revision, CORE["record_revision"](event))
        full_markdown = self.search(budget=65536)
        first_line = next(line for line in full_markdown.splitlines() if line.startswith("> event: "))
        boundary = full_markdown.index(first_line) + len(first_line) // 2 + 40
        clipped = self.search(budget=boundary)
        self.assertNotIn("> event:", clipped)
        self.assertIn("[truncated by llm-wiki-enrich]", clipped)

    def test_serialization_policy_change_rejects_the_entire_identity_output(self):
        self.write([FIXTURES["event"](1, summary=MARKER, target_agents=["workbuddy"])])
        render = ENGINE["render_json"]
        policy = self.root / "policy/access.json"
        def change_after_render(*args, **kwargs):
            result = render(*args, **kwargs)
            policy.write_bytes(policy.read_bytes() + b"\n")
            return result
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, self.env, clear=True), patch.dict(ENGINE["main"].__globals__, render_json=change_after_render), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ENGINE["main"](["--agent-profile", "workbuddy", "--query", MARKER, "--json"])
        self.assertEqual(code, 4)
        self.assertEqual(out.getvalue(), "")
        self.assertNotIn(MARKER, err.getvalue())


if __name__ == "__main__":
    unittest.main()
