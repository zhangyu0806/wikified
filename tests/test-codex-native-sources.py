#!/usr/bin/env python3
"""Synthetic native-source ledger bounds and provenance projection."""
import copy
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

API = runpy.run_path(str(Path(__file__).resolve().parents[1] / "bin/llm_wiki_codex_native_sources.py"))
G = API["read_source_projection"].__globals__


class NativeSources(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="native-source-ledger-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.file = self.root / API["RELATIVE"]

    def origin(self, text="A synthetic imported statement.\n"):
        digest = API["sha"](text)
        key = API["js_hash"](["codex-native-memory-import/v2", digest])
        identity = API["sha"]("wikified-capture-v1:" + key)[:16]
        return {"schema_version": "llm-wiki-memory-event/v3", "id": identity, "memory_id": "event:" + identity,
                "capture_key": key, "source": "codex-native-memory", "actor": {"type": "ai", "id": "codex-native-memory"},
                "project": "codex-memory", "type": "session", "domain": "work", "sensitivity": "internal",
                "target_agents": ["coding"], "files": [], "review_status": "pending", "epistemic_status": "ai-proposed",
                "summary": "Codex 原生记忆 · 内容 · " + digest[:12],
                "details": API["HEADER"] + "\nContent ref: sha256:" + digest + "\n\n" + text,
                "evidence_refs": ["codex-native-content:" + digest]}

    def observation(self, event, source="synthetic-source", text="A synthetic imported statement.\n"):
        return {"sourceRef": "sha256:" + API["sha"](source), "sourceKind": "consolidated-memory",
                "sourceRevision": "sha256:" + API["sha"](text), "sourceRawRevision": "sha256:" + API["sha"]("raw:" + text),
                "state": "present", "parts": [{"contentHash": "sha256:" + API["sha"](text),
                                             "captureKey": event["capture_key"], "eventId": event["id"]}]}

    def append(self, observations):
        state = API["read_state"](self.root)
        return API["append_observations"](self.root, observations, state["revision"])

    def project(self, events):
        return API["read_source_projection"](self.root, events)

    def test_two_sources_one_content_projection_contains_only_whitelisted_metadata(self):
        event = self.origin()
        self.append([self.observation(event), self.observation(event, "second-source")])
        result = self.project([event])
        self.assertEqual(result["state"], "complete")
        self.assertEqual(len(result["groups"]), 1)
        group = result["groups"][0]
        self.assertEqual(group["eventIds"], [event["id"]])
        self.assertEqual(len(group["sources"]), 2)
        self.assertEqual(set(group), {"contentHash", "contentGroupId", "eventIds", "sources"})
        text = json.dumps(result)
        for forbidden in ("A synthetic imported", str(self.root), "second-source", "pending", "approved"):
            self.assertNotIn(forbidden, text)
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o600)

    def test_migration_groups_all_verified_legacy_exact_duplicates(self):
        text = "A synthetic imported statement.\n"
        event = self.origin(text)
        self.append([self.observation(event)])
        legacy = dict(event)
        ref, revision = "a" * 64, "b" * 64
        key = API["js_hash"](["codex-native-memory-import/v1", ref, revision, 0, API["sha"](text)])
        identity = API["sha"]("wikified-capture-v1:" + key)[:16]
        legacy.update(id=identity, memory_id="event:" + identity, capture_key=key,
                      summary="Codex 原生记忆 · consolidated-memory · " + ref[:12] + " · 1/1",
                      details=API["HEADER"] + "\nSource kind: consolidated-memory\nSource ref: sha256:" + ref
                              + "\nSource revision: sha256:" + revision + "\nPart: 1/1\n\n" + text,
                      evidence_refs=["codex-native-source:" + ref, "codex-native-revision:" + revision, "codex-native-part:1/1"])
        group = self.project([event, legacy])["groups"][0]
        self.assertEqual(group["eventIds"], sorted([event["id"], identity]))

    def test_noop_keeps_bytes_and_changed_metadata_appends_exact_prefix(self):
        event = self.origin()
        observation = self.observation(event)
        self.append([observation])
        before = self.file.read_bytes()
        self.assertEqual(self.append([observation])["observationsAppended"], 0)
        self.assertEqual(self.file.read_bytes(), before)
        changed = dict(observation, sourceRawRevision="sha256:" + "d" * 64)
        self.assertEqual(self.append([changed])["observationsAppended"], 1)
        self.assertTrue(self.file.read_bytes().startswith(before))
        sources = self.project([event])["groups"][0]["sources"]
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["observationState"], "current")

    def test_new_content_keeps_historical_and_current_source_observations(self):
        old, new = self.origin(), self.origin("A newly revised statement.\n")
        self.append([self.observation(old)])
        self.append([self.observation(new, text="A newly revised statement.\n")])
        groups = self.project([old, new])["groups"]
        states = {row["eventIds"][0]: row["sources"][0]["observationState"] for row in groups}
        self.assertEqual(states, {old["id"]: "historical", new["id"]: "current"})

    def test_missing_source_keeps_evidence_without_mutating_origin(self):
        event = self.origin()
        original = copy.deepcopy(event)
        observation = self.observation(event)
        self.append([observation])
        self.append([dict(observation, sourceRevision=None, sourceRawRevision=None, state="missing", parts=[])])
        self.assertEqual(self.project([event])["groups"][0]["sources"][0]["observationState"], "missing")
        self.assertEqual(event, original)

    def test_review_alias_requires_same_origin_details_evidence_and_scope(self):
        event = self.origin()
        self.append([self.observation(event)])
        approved = dict(event, schema_version="llm-wiki-memory-event/v4", id="b" * 16,
                        record_id="event:" + event["id"], actor={"type": "human", "id": "synthetic-reviewer"},
                        source="human-review/v4", review_status="approved", epistemic_status="human-confirmed",
                        summary="A human supplied summary")
        ids = self.project([event, approved])["groups"][0]["eventIds"]
        self.assertEqual(ids, sorted([event["id"], approved["id"]]))
        for changed in (dict(approved, details="Different body"), dict(approved, domain="personal"),
                        dict(approved, evidence_refs=[]), dict(approved, record_id="event:" + "0" * 16)):
            self.assertEqual(self.project([event, changed])["groups"][0]["eventIds"], [event["id"]])

    def test_wrong_chunk_hash_and_full_source_hash_fail_closed(self):
        event = self.origin()
        self.append([self.observation(event)])
        data = self.file.read_bytes()
        for field in ("contentHash", "sourceRevision"):
            row = json.loads(data)
            if field == "contentHash":
                row["parts"][0][field] = "sha256:" + "a" * 64
            else:
                row[field] = "sha256:" + "a" * 64
            row["observationId"] = API["sha"](API["canonical"]({k: v for k, v in row.items() if k != "observationId"}))
            self.file.write_bytes(API["canonical"](row) + b"\n")
            self.assertEqual(self.project([event]), {"schemaVersion": 1, "state": "unavailable", "groups": []})

    def test_malformed_unknown_duplicate_keys_and_partial_rows_fail_closed(self):
        event = self.origin()
        self.append([self.observation(event)])
        valid = self.file.read_bytes()
        for data in (valid[:-1], valid + b"{}\n", valid.replace(b'"schemaVersion":1', b'"schemaVersion":1,"schemaVersion":1'),
                     valid.replace(b'"schemaVersion":1', b'"schemaVersion":2'), valid + b"\xff\n"):
            self.file.write_bytes(data)
            self.assertEqual(self.project([event])["state"], "unavailable")

    def test_observation_cas_rejects_outdated_planner_without_replacing_any_bytes(self):
        event = self.origin()
        empty_revision = API["read_state"](self.root)["revision"]
        self.append([self.observation(event)])
        before = self.file.read_bytes()
        with self.assertRaises(API["NativeSourceError"]):
            API["append_observations"](self.root, [self.observation(event, "second")], empty_revision)
        self.assertEqual(self.file.read_bytes(), before)

    def test_corrupt_or_missing_canonical_origin_never_creates_a_group(self):
        event = self.origin()
        self.append([self.observation(event)])
        for events in ([], [dict(event, details=event["details"] + "tampered")], [dict(event, domain="personal")]):
            self.assertEqual(self.project(events)["state"], "unavailable")

    def test_bounds_fail_before_partial_projection_or_write(self):
        event = self.origin()
        self.append([self.observation(event)])
        before = self.file.read_bytes()
        for limit in ({"MAX_BYTES": 16}, {"MAX_LINE": 16}, {"MAX_ROWS": 0}, {"MAX_PARTS": 0}):
            with patch.dict(G, limit):
                self.assertEqual(self.project([event])["state"], "unavailable")
        with patch.dict(G, {"MAX_BYTES": len(before) + 1}):
            with self.assertRaises(API["NativeSourceError"]):
                self.append([self.observation(event, "new-source")])
        self.assertEqual(self.file.read_bytes(), before)

    def test_links_and_special_files_are_not_followed(self):
        event = self.origin()
        self.file.parent.mkdir(parents=True)
        outside = self.root / "outside"
        outside.write_text("private unrelated content")
        self.file.symlink_to(outside)
        self.assertEqual(self.project([event])["state"], "unavailable")
        self.file.unlink()
        os.link(outside, self.file)
        self.assertEqual(self.project([event])["state"], "unavailable")
        self.file.unlink()
        os.mkfifo(self.file)
        self.assertEqual(self.project([event])["state"], "unavailable")
        self.file.unlink()
        self.file.parent.rmdir()
        self.file.parent.symlink_to(self.root)
        self.assertEqual(self.project([event])["state"], "unavailable")

    def test_failed_replace_preserves_old_ledger_and_cleans_temporary_file(self):
        event = self.origin()
        self.append([self.observation(event)])
        before = self.file.read_bytes()
        with patch.object(API["os"], "replace", side_effect=OSError("synthetic")):
            with self.assertRaises(OSError):
                self.append([self.observation(event, "second")])
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(list(self.file.parent.glob(".native-sources-*")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
