#!/usr/bin/env python3
"""Synthetic continuing-topic requests: authority, CAS, generation and two MCP profiles."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import runpy
import selectors
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
M = runpy.run_path(str(ROOT / "bin/llm-wiki-context"), run_name="context_test")
E = M["ENGINE"]
F = runpy.run_path(str(ROOT / "tests/test-event-versioned-review.py"), run_name="context_event_fixture")


class Context(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wiki-topic-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ("notes", "wiki", "memory/events", "policy"):
            (self.root / directory).mkdir(parents=True)
        shutil.copyfile(ROOT / "templates/access-policy.json", self.root / "policy/access.json")

    def page(self, name="one", body="Synthetic topic evidence", **extra):
        fields = dict(memory_id="note:" + name, domain="work", sensitivity="internal", review_state="approved",
                      epistemic_status="human-stated", target_profiles=["codex", "opencode"])
        fields.update(extra)
        path = self.root / "notes" / (name + ".md")
        path.write_text("---\n" + "\n".join(json.dumps(k) + ": " + json.dumps(v) for k, v in fields.items()) + "\n---\n" + body)
        return dict(kind="page", memoryId="note:" + name, revision="sha256:" + hashlib.sha256(path.read_bytes()).hexdigest())

    def pack(self, rows, profile="codex"):
        return M["prepare"](self.root, profile, dict(schemaVersion=1, selections=rows))

    def events(self, rows):
        (self.root / "memory/events/example.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))

    def selection(self, event):
        return dict(kind="event", memoryId=event.get("record_id", event["memory_id"]), revision=F["C"]["record_revision"](event))

    def mcp(self, profile, name, args):
        proc = subprocess.Popen(["node", str(ROOT / "bin/llm-wiki-mcp")], text=True, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LLM_WIKI_ROOT": str(self.root), "LLM_WIKI_AGENT_PROFILE": profile, "LLM_WIKI_TARGET_AGENTS": "codex,opencode",
            "PYTHONDONTWRITEBYTECODE": "1"})
        try:
            proc.stdin.write(json.dumps(dict(jsonrpc="2.0", id=1, method="tools/call", params=dict(name=name, arguments=args))) + "\n")
            proc.stdin.flush()
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(15), "MCP response timeout")
                return json.loads(proc.stdout.readline())
        finally:
            proc.terminate()
            proc.communicate(timeout=5)

    def test_both_profiles_exact_sources_rename_and_no_duplicate_body_store(self):
        row = self.page()
        for profile in ("codex", "opencode"):
            result = self.pack([row], profile)
            self.assertEqual(result["profile"], profile)
            self.assertEqual(result["items"][0]["status"], "ready")
            self.assertIn("Synthetic topic evidence", result["items"][0]["text"])
        (self.root / "notes/one.md").rename(self.root / "notes/renamed.md")
        self.assertEqual(self.pack([row])["items"][0]["locator"], "notes/renamed.md")

    def test_changed_no_body_then_explicit_reselection(self):
        old = self.page()
        new = self.page(body="Corrected synthetic evidence")
        self.assertEqual(self.pack([old])["items"], [dict(selection=old, status="changed", currentRevision=new["revision"])])
        self.assertIn("Corrected", self.pack([new])["items"][0]["text"])

    def test_denied_pending_missing_have_same_shape_and_no_names(self):
        for fields in [dict(target_profiles=["human"]), dict(review_state="pending"), dict(review_state="rejected"), dict(sensitivity="restricted")]:
            row = self.page(**fields)
            self.assertEqual(self.pack([row])["items"], [dict(selection=row, status="unavailable")])
        row["memoryId"] = "note:missing"
        self.assertEqual(self.pack([row])["items"], [dict(selection=row, status="unavailable")])

    def test_bare_notes_and_source_staleness_cannot_become_grants(self):
        source = self.page("source")
        row = self.page(mind2one={"version":2, "id":"note:one", "kind":"note", "origin":"human", "review-state":"not-required",
            "sources":[{"kind":"document", "id":source["memoryId"], "revision":source["revision"], "relation":"current-derived"}]})
        self.assertEqual(self.pack([row])["items"][0]["status"], "ready")
        self.page("source", body="Changed upstream source")
        self.assertEqual(self.pack([row])["items"][0]["status"], "unavailable")
        (self.root / "notes/one.md").write_text("No governance")
        self.assertEqual(self.pack([row])["items"][0]["status"], "unavailable")

    def test_duplicate_identity_and_unauthorized_new_revision_do_not_leak(self):
        row = self.page()
        (self.root / "notes/duplicate.md").write_bytes((self.root / "notes/one.md").read_bytes())
        with self.assertRaises(Exception): self.pack([row])
        (self.root / "notes/duplicate.md").unlink()
        self.page(body="Hidden corrected text", target_profiles=["human"])
        self.assertEqual(self.pack([row])["items"], [dict(selection=row, status="unavailable")])

    def test_redaction_and_budget_are_explicit(self):
        row = self.page(body="prefix sk-" + "FAKEabcdefghijklmnop123456789" + " suffix")
        self.assertNotIn("FAKEabcdefghijklmnop", self.pack([row])["items"][0]["text"])
        row = self.page(body="x" * 12001)
        self.assertEqual(self.pack([row])["items"], [dict(selection=row, status="too-large")])
        rows = [self.page(str(i), "x" * 11000) for i in range(4)]
        self.assertEqual([x["status"] for x in self.pack(rows)["items"]], ["ready", "ready", "ready", "too-large"])

    def test_invalid_or_privileged_requests_fail(self):
        row = self.page()
        for rows in [[], [row, row], [{**row, "path": "../../private"}], [{**row, "revision": "latest"}], [{**row, "kind": "raw"}]]:
            with self.assertRaises(ValueError): self.pack(rows)
        with self.assertRaises(ValueError): self.pack([row], "human")
        with self.assertRaises(ValueError): M["prepare"](self.root, "codex", dict(schemaVersion=1, selections=[row], profile="human"))

    def test_generation_mutation_rejects_entire_pack(self):
        row = self.page()
        original = E["load_events"]
        for target in ("notes/one.md", "policy/access.json", "memory/events/new.jsonl"):
            def mutate(root, access):
                result = original(root, access)
                with (self.root / target).open("a") as handle: handle.write("\n")
                return result
            with patch.dict(E, load_events=mutate):
                with self.assertRaises(Exception): self.pack([row])

    def test_corrupt_event_coverage_never_returns_partial_pages(self):
        row = self.page()
        (self.root / "memory/events/bad.jsonl").write_text("{broken")
        with self.assertRaises(Exception): self.pack([row])

    def test_cross_assistant_mcp_proposal_accept_correct_withdraw(self):
        source = self.page()
        for profile in ("codex", "opencode"):
            response = self.mcp(profile, "prepare_context", {"selections":[source]})
            self.assertNotIn("error", response, response)
            self.assertEqual(json.loads(response["result"]["content"][0]["text"])["items"][0]["status"], "ready")
        self.assertIn("error", self.mcp("opencode", "prepare_context", {"selections":[source], "profile":"human"}))
        response = self.mcp("codex", "record_event", {"type":"decision", "summary":"Synthetic selected conclusion",
            "details":"Selected source: " + source["memoryId"] + " @ " + source["revision"], "project":"topic-test"})
        self.assertNotIn("error", response, response)
        raw = [json.loads(line) for file in (self.root / "memory/events").glob("*.jsonl") for line in file.read_text().splitlines()]
        self.assertEqual(len(raw), 1)
        pending = raw[0]
        self.assertEqual(pending["review_status"], "pending")
        selected = self.selection(pending)
        self.assertEqual(self.pack([selected], "opencode")["items"][0]["status"], "unavailable")
        # Existing human-review algorithm, synthetic owner only. Match the
        # current proposal time so a review cannot predate the proposal.
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        core = F["C"]
        def review(target, action, number, **extra):
            req = F["request"](target, action, **extra)
            if target.get("supersedes"): req["confirm_supersedes"] = True
            return core["prepare"](target, req, actor_id="synthetic-human", can_review=True, now=now, event_id=f"{number:016x}")
        accepted = review(pending, "accept", 101); self.events([pending, accepted])
        # Remove the original month fixture to avoid duplicate copies in later
        # state checks; identical events are legal but unnecessary here.
        for file in (self.root / "memory/events").glob("*.jsonl"):
            if file.name != "example.jsonl": file.unlink()
        corrected = review(accepted, "correct", 102, summary="Synthetic corrected conclusion")
        self.events([pending, accepted, corrected])
        old = self.selection(accepted); new = self.selection(corrected)
        response = self.mcp("opencode", "prepare_context", {"selections":[old]})
        self.assertEqual(json.loads(response["result"]["content"][0]["text"])["items"][0]["status"], "changed")
        response = self.mcp("opencode", "prepare_context", {"selections":[new]})
        self.assertIn("Synthetic corrected conclusion", json.loads(response["result"]["content"][0]["text"])["items"][0]["text"])
        withdrawn = review(corrected, "withdraw", 103)
        self.events([pending, accepted, corrected, withdrawn])
        for profile in ("codex", "opencode"):
            self.assertEqual(self.pack([new], profile)["items"][0]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
