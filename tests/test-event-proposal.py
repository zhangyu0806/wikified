#!/usr/bin/env python3
"""Synthetic proposal CAS, scope intersection and independent-review boundary."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
M = runpy.run_path(str(ROOT / "bin/llm-wiki-event-proposal"), run_name="proposal_test")
F = runpy.run_path(str(ROOT / "tests/test-event-versioned-review.py"), run_name="proposal_fixture")
E, C = M["ENGINE"], M["CORE"]
CAP = "synthetic-independent-proposal-capability-only"


class Proposal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wiki-proposal-synthetic-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "memory/events").mkdir(parents=True)
        (self.root / "policy").mkdir()
        shutil.copyfile(ROOT / "templates/access-policy.json", self.root / "policy/access.json")
        self.rows = [F["S"]["event"](i, target_agents=["codex", "opencode"], files=[], concepts=[]) for i in (1, 2)]
        self.write()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("LLM_WIKI_")}
        self.env.update(LLM_WIKI_ROOT=str(self.root), LLM_WIKI_EVENT_REVIEW_CAPABILITY=CAP, LLM_WIKI_EVENT_REVIEW_ACTOR="synthetic-human")

    def write(self):
        (self.root / "memory/events/original.jsonl").write_text("".join(json.dumps(row) + "\n" for row in self.rows))

    def request(self):
        return {"schemaVersion": 1, "selections": [{"eventId": row["id"], "revision": C["record_revision"](row)} for row in self.rows],
            "summary": "Synthetic replacement candidate", "details": "Human-written comparison proposal; not an approved fact.", "requestId": "compare:one"}

    def invoke(self, request, success=True):
        output = subprocess.run([sys.executable, "-B", str(ROOT / "bin/llm-wiki-event-proposal")], env=self.env,
            input=json.dumps({"capability": CAP, **request}), text=True, capture_output=True, timeout=10)
        self.assertEqual(output.returncode, 0 if success else 2, output.stderr)
        self.assertNotIn(CAP, output.stdout + output.stderr)
        return json.loads(output.stdout) if success else output

    def bytes(self):
        return {path.name: path.read_bytes() for path in (self.root / "memory/events").glob("*.jsonl")}

    def test_pending_does_not_replace_originals_until_independent_review(self):
        candidate = self.invoke(self.request())
        self.assertEqual(candidate["review_status"], "pending")
        self.assertEqual(candidate["actor"]["type"], "ai")
        self.assertEqual(candidate["supersedes"], [row["id"] for row in self.rows])
        access = E["access_context"](self.root, "codex")
        self.assertEqual({row["id"] for row in E["load_events"](self.root, access)}, {row["id"] for row in self.rows})
        request = F["request"](candidate, "accept", confirm_supersedes=True)
        approved = C["prepare"](candidate, request, actor_id="synthetic-human", can_review=True,
            now=datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"), event_id="a" * 16)
        with (self.root / "memory/events/review.jsonl").open("w") as stream:
            stream.write(json.dumps(approved) + "\n")
        self.assertEqual({row["id"] for row in E["load_events"](self.root, access)}, {approved["id"]})

    def test_duplicate_and_concurrent_retries_append_once(self):
        request = self.request()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(self.invoke, [request, request]))
        self.assertEqual(first, second)
        before = self.bytes()
        self.assertEqual(self.invoke(request), first)
        self.assertEqual(self.bytes(), before)
        self.invoke({**request, "summary": "Changed request with reused identity"}, success=False)
        self.assertEqual(self.bytes(), before)

    def test_content_and_metadata_changes_refuse_stale_cas(self):
        for field, value in [("details", "external edit"), ("concepts", ["new-classification"]), ("target_agents", ["human"])]:
            original = json.loads(json.dumps(self.rows))
            request = self.request()
            self.rows[0][field] = value
            self.write()
            before = self.bytes()
            self.invoke(request, success=False)
            self.assertEqual(self.bytes(), before)
            self.rows = original
            self.write()

    def test_scope_intersection_and_maximum_sensitivity(self):
        self.rows[0].update(target_agents=["codex", "opencode"], sensitivity="internal")
        self.rows[1].update(target_agents=["opencode"], sensitivity="confidential")
        self.write()
        result = self.invoke(self.request())
        self.assertEqual(result["target_agents"], ["opencode"])
        self.assertEqual(result["sensitivity"], "confidential")

    def test_disjoint_targets_projects_domains_and_denied_rows_never_write(self):
        original = json.loads(json.dumps(self.rows))
        for change in [{"target_agents": ["planning"]}, {"project": "other"}, {"domain": "personal"}, {"review_status": "rejected", "epistemic_status": "disputed"}]:
            self.rows = json.loads(json.dumps(original))
            self.rows[1].update(change)
            self.write()
            before = self.bytes()
            self.invoke(self.request(), success=False)
            self.assertEqual(self.bytes(), before)

    def test_capability_unknown_fields_duplicate_selections_and_secrets_refused(self):
        request = self.request()
        for invalid in [{**request, "capability": "wrong"}, {**request, "actor": "human"},
                        {**request, "selections": [request["selections"][0]] * 2},
                        {**request, "details": "token=ghp_FAKE1234567890abcdefghij1234"}]:
            before = self.bytes()
            self.invoke(invalid, success=False)
            self.assertEqual(self.bytes(), before)

    def test_policy_or_event_change_after_prepare_refuses_append(self):
        now = datetime.now(timezone.utc)
        for path in [self.root / "policy/access.json", self.root / "memory/events/original.jsonl"]:
            event, reused, generation = M["prepare"](self.root, self.request(), "synthetic-human", now)
            self.assertFalse(reused)
            original = path.read_bytes()
            path.write_bytes(original + b"\n")
            with self.assertRaises(ValueError):
                M["append"](self.root, event, now, generation)
            path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
