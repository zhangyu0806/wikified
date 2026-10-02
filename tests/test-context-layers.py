#!/usr/bin/env python3
"""Authorized discovery -> bounded extract -> fixed-revision evidence; synthetic only."""
import json
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = runpy.run_path(str(ROOT / "tests/test-continuing-context.py"), run_name="layers_fixture")
M, E, F = FIXTURE["M"], FIXTURE["E"], FIXTURE["F"]


class Layers(unittest.TestCase):
    setUp = FIXTURE["Context"].setUp
    page = FIXTURE["Context"].page
    events = FIXTURE["Context"].events
    selection = FIXTURE["Context"].selection
    pack = FIXTURE["Context"].pack

    def preview(self, selections, profile="codex"):
        return M["preview"](self.root, profile, dict(schemaVersion=1, selections=selections))

    def read(self, selection, profile="codex", **extra):
        return M["read_evidence"](self.root, profile, dict(schemaVersion=1, selection=selection, **extra))

    def search(self, query="synthetic", profile="codex", **extra):
        return M["search"](self.root, profile, dict(schemaVersion=1, query=query, **extra))

    def test_long_document_is_a_small_extract_then_contiguous_fixed_revision_chunks(self):
        row = self.page(body="Synthetic 😀 evidence\n" * 1000 + "LAYERED_TAIL_MARKER")
        full = E["redact"](E["read_page"](self.root, "notes/one.md", E["access_context"](self.root, "codex"), None,
            offset=0, length=8000, revision=row["revision"]))
        self.assertIsNotNone(full)
        for profile in ("codex", "opencode"):
            item = self.preview([row], profile)["items"][0]
            self.assertEqual(item["status"], "ready")
            self.assertNotIn("text", item)
            self.assertEqual(len(item["excerpt"]), 600)
            self.assertNotIn("LAYERED_TAIL_MARKER", json.dumps(item))
            self.assertGreater(item["totalChars"], 12000)
            self.assertEqual(self.pack([row], profile)["items"][0]["status"], "too-large")
            collected, offset = [], 0
            while offset is not None:
                chunk = self.read(row, profile, offset=offset, length=4000)["item"]
                self.assertEqual(chunk["selection"], row)
                self.assertEqual(chunk["offset"], offset)
                self.assertEqual(chunk["totalChars"], item["totalChars"])
                self.assertLessEqual(len(chunk["text"]), 4000)
                collected.append(chunk["text"])
                offset = chunk["nextOffset"]
            self.assertEqual(len("".join(collected)), item["totalChars"])
            self.assertTrue("".join(collected).endswith("LAYERED_TAIL_MARKER"))

    def test_search_uses_project_authority_and_stable_ids_not_human_catalog(self):
        expected = self.page("relevant", "synthetic constellation approved", project="layers-test")
        self.page("other", "synthetic constellation other project", project="other-project")
        self.page("pending", "synthetic constellation hidden pending", project="layers-test", review_state="pending")
        self.page("human", "synthetic constellation hidden human", project="layers-test", target_profiles=["human"])
        self.page("bare", "synthetic constellation no stable identity", project="layers-test")
        path = self.root / "notes/bare.md"
        path.write_text(path.read_text().replace('"memory_id": "note:bare"\n', ""))
        event = F["S"]["event"](10, project="layers-test", summary="synthetic constellation reviewed event", target_agents=["codex", "opencode"])
        self.events([event])
        for profile in ("codex", "opencode"):
            result = self.search("synthetic constellation", profile, project="layers-test")
            self.assertEqual({x["selection"]["memoryId"] for x in result["items"]}, {expected["memoryId"], event["memory_id"]})
            self.assertEqual(result["coverage"], "complete")
            for item in result["items"]:
                self.assertLessEqual(len(item["excerpt"]), 400)
                self.assertEqual(set(item), {"selection", "title", "excerpt"})
                self.assertEqual(self.preview([item["selection"]], profile)["items"][0]["status"], "ready")
        limited = self.search("synthetic constellation", project="layers-test", limit=1)
        self.assertEqual(len(limited["items"]), 1)
        self.assertEqual(limited["coverage"], "limited")

    def test_event_record_identity_and_withdrawal_apply_to_search_preview_and_read(self):
        pending = F["pending"]()
        accepted = F["reviewed"](pending)
        row = self.selection(accepted)
        self.events([pending, accepted])
        found = self.search("vfourmarker")
        self.assertEqual(found["items"][0]["selection"], row)
        self.assertEqual(row["memoryId"], pending["memory_id"])
        self.assertIn("vfourmarker", self.preview([row])["items"][0]["excerpt"])
        text, offset = "", 0
        while offset is not None:
            chunk = self.read(row, offset=offset, length=7)["item"]
            text += chunk["text"]
            offset = chunk["nextOffset"]
        self.assertEqual(text, accepted["summary"] + "\n" + accepted["details"])
        self.events([pending, accepted, F["reviewed"](accepted, "withdraw", 3)])
        self.assertEqual(self.search("vfourmarker")["items"], [])
        self.assertEqual(self.preview([row])["items"], [dict(selection=row, status="unavailable")])
        self.assertEqual(self.read(row)["item"], dict(selection=row, status="unavailable"))

    def test_changed_and_revoked_sources_never_return_old_extract_or_body(self):
        old = self.page()
        current = self.page(body="Changed synthetic source")
        expected = dict(selection=old, status="changed", currentRevision=current["revision"])
        self.assertEqual(self.preview([old])["items"], [expected])
        self.assertEqual(self.read(old)["item"], expected)
        self.page(body="Human-only hidden current source", target_profiles=["human"])
        self.assertEqual(self.preview([old])["items"], [dict(selection=old, status="unavailable")])
        self.assertEqual(self.read(old)["item"], dict(selection=old, status="unavailable"))

    def test_event_revision_alias_does_not_remap_an_independent_page_identity(self):
        pending = F["pending"]()
        accepted = F["reviewed"](pending)
        self.events([pending, accepted])
        self.page(body="Independent onlypagequery evidence", memory_id="event:" + accepted["id"])
        result = self.search("onlypagequery")
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["selection"]["kind"], "page")
        self.assertEqual(result["items"][0]["selection"]["memoryId"], "event:" + accepted["id"])

    def test_stale_derived_sources_are_unavailable_at_all_layers(self):
        source = self.page("source")
        row = self.page(mind2one={"version": 2, "id": "note:one", "kind": "note", "origin": "human", "review-state": "not-required",
            "sources": [{"kind": "document", "id": source["memoryId"], "revision": source["revision"], "relation": "current-derived"}]})
        self.assertEqual(self.read(row)["item"]["status"], "ready")
        self.page("source", body="Changed upstream source")
        self.assertEqual(self.read(row)["item"]["status"], "unavailable")
        self.assertEqual(self.preview([row])["items"][0]["status"], "unavailable")
        self.assertNotIn(row["memoryId"], {item["selection"]["memoryId"] for item in self.search()["items"]})

    def test_redact_before_excerpt_and_chunk_boundaries(self):
        secret = "sk-FAKEabcdefghijklmnop123456789"
        row = self.page(body="Synthetic " + "x" * 580 + " " + secret + " tail")
        values = [self.preview([row]), self.search("Synthetic"), self.read(row, length=8000)]
        for value in values:
            self.assertNotIn("FAKEabcdefgh", json.dumps(value))
        text = self.read(row, length=8000)["item"]["text"]
        for offset in range(max(0, text.index("[REDACTED") - 5), text.index("[REDACTED") + 5):
            self.assertNotIn("FAKEabcdefgh", self.read(row, offset=offset, length=12)["item"]["text"])

    def test_all_operations_fence_entire_generation(self):
        row = self.page()
        original = E["load_events"]
        for target in ("notes/one.md", "policy/access.json", "memory/events/new.jsonl"):
            def mutate(root, access):
                result = original(root, access)
                with (self.root / target).open("a") as handle: handle.write("\n")
                return result
            for operation in (lambda: self.preview([row]), lambda: self.read(row), lambda: self.search()):
                with patch.dict(E, load_events=mutate):
                    with self.assertRaises(Exception): operation()

    def test_invalid_request_fields_and_bounds_fail(self):
        row = self.page()
        for extra in [{"root": "/private"}, {"profile": "human"}, {"query": ""}, {"query": "x" * 501},
                      {"project": ""}, {"limit": 0}, {"limit": True}, {"limit": 11}]:
            with self.assertRaises(ValueError): M["search"](self.root, "codex", {"schemaVersion": 1, "query": "synthetic", **extra})
        for extra in [{"path": "notes/one.md"}, {"offset": -1}, {"offset": True}, {"offset": 99999}, {"length": 0}, {"length": 8001}, {"profile": "human"}]:
            with self.assertRaises(ValueError): M["read_evidence"](self.root, "codex", {"schemaVersion": 1, "selection": row, **extra})

    def test_cli_protocol_is_real_and_rejects_request_profile_override(self):
        row = self.page()
        for operation, args in [("search", {"query": "synthetic"}), ("preview", {"selections": [row]}), ("read", {"selection": row, "length": 50})]:
            command = [sys.executable, str(ROOT / "bin/llm-wiki-context"), "--root", str(self.root), "--agent-profile", "codex", "--operation", operation, "--request"]
            result = subprocess.run(command + [json.dumps(dict(schemaVersion=1, **args))], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["profile"], "codex")
            result = subprocess.run(command + [json.dumps(dict(schemaVersion=1, profile="human", **args))], capture_output=True, text=True, timeout=15)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertNotIn(str(self.root), result.stderr)


if __name__ == "__main__":
    unittest.main()
