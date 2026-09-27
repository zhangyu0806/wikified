#!/usr/bin/env python3
"""Complete bounded snapshots; every write stays in a fresh synthetic vault."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "bin" / "llm-wiki-enrich"
API = runpy.run_path(str(ENGINE), run_name="snapshot_test_engine")
G = API["load_events"].__globals__
WRITER = runpy.run_path(str(REPO / "bin" / "llm-wiki-event"), run_name="snapshot_test_writer")
CONTEXT = API["AccessContext"]("codex", frozenset(["work"]),
    ("public", "internal", "confidential", "restricted"), 1,
    frozenset(["approved"]), "self", frozenset(["codex", "coding"]), False, False)


def event(number: int, **changes):
    event_id = f"{number:016x}"
    value = {
        "schema_version": "llm-wiki-memory-event/v3", "id": event_id,
        "memory_id": "event:" + event_id, "actor": {"type": "human", "id": "test-owner"},
        "domain": "work", "sensitivity": "internal", "epistemic_status": "human-confirmed",
        "review_status": "approved", "target_agents": ["codex"], "project": "snapshot-test",
        "timestamp": "2020-01-01T00:00:00Z", "valid_from": "2020-01-01T00:00:00Z",
        "lifecycle": "active", "type": "fact", "summary": "snapshotmarker " + event_id,
        "supersedes": [], "confidence": 1.0, "half_life_days": 36500,
    }
    value.update(changes)
    return value


class SnapshotIntegrity(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wiki-event-snapshot-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.events = self.root / "memory" / "events"
        self.events.mkdir(parents=True)

    def write(self, rows, name="2020-01.jsonl"):
        path = self.events / name
        path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
        return path

    def ids(self):
        return {row["id"] for row in API["load_events"](self.root, CONTEXT)}

    def fails(self, code):
        with self.assertRaises(API["EventSnapshotError"]) as error:
            self.ids()
        self.assertEqual(error.exception.code, code)

    def test_5001st_revision_and_identical_two_row_control(self):
        old = event(1)
        revision = event(5001, supersedes=[old["id"]])
        rows = [old] + [event(n) for n in range(2, 5001)] + [revision]
        self.assertTrue(all(API["event_metadata"](row) for row in rows))
        self.write(rows)
        full = self.ids()
        self.assertNotIn(old["id"], full)
        self.assertIn(revision["id"], full)
        self.write([old, revision])
        self.assertEqual(self.ids(), {revision["id"]})

    def test_cross_file_edges_do_not_depend_on_file_sort_order(self):
        old, new = event(1), event(2, supersedes=[event(1)["id"]])
        self.write([new], "1900-01.jsonl")
        self.write([old], "2999-12.jsonl")
        self.assertEqual(self.ids(), {new["id"]})

    def test_deprecated_and_expired_replacements_never_resurrect_old_content(self):
        old = event(1)
        for updates in ({"lifecycle": "deprecated"}, {"valid_until": "2021-01-01T00:00:00Z"}):
            with self.subTest(updates=updates):
                self.write([old, event(2, supersedes=[old["id"]], **updates)])
                self.assertEqual(self.ids(), set())

    def test_three_stage_chain_keeps_every_closed_predecessor_closed(self):
        first = event(1)
        second = event(2, supersedes=[first["id"]])
        third = event(3, supersedes=[second["id"]], lifecycle="deprecated")
        self.write([third, first, second])
        self.assertEqual(self.ids(), set())

    def test_inactive_or_cross_scope_edges_cannot_hide_valid_fact(self):
        old = event(1)
        invalid_edges = [
            {"actor": {"type": "ai", "id": "codex"}, "review_status": "pending", "epistemic_status": "ai-proposed"},
            {"valid_from": "2999-01-01T00:00:00Z"},
            {"project": "other-project"}, {"project": ""}, {"domain": "personal"},
            {"review_status": "rejected", "epistemic_status": "disputed"},
            {"valid_until": "2019-01-01T00:00:00Z"},
        ]
        for changes in invalid_edges:
            with self.subTest(changes=changes):
                self.write([old, event(2, supersedes=[old["id"]], **changes)])
                self.assertIn(old["id"], self.ids())

    def test_hidden_effective_revision_closes_old_visible_fact(self):
        self.write([event(1), event(2, target_agents=["opencode"], supersedes=[event(1)["id"]])])
        self.assertEqual(self.ids(), set())

    def test_two_missing_projects_are_not_a_matching_scope(self):
        old = event(1)
        new = event(2, supersedes=[old["id"]])
        old.pop("project")
        new.pop("project")
        self.write([old, new])
        self.assertEqual(self.ids(), {old["id"], new["id"]})
        self.assertFalse(WRITER["is_effective_terminal_revision"](new))
        with self.assertRaisesRegex(ValueError, "another project"):
            WRITER["validate_supersedes"]({old["id"]: old}, [old["id"]], "", "work")

    def test_invalid_known_governance_is_rejected_with_non_content_diagnostic(self):
        forged = event(2, actor={"type": "ai", "id": "codex"}, supersedes=[event(1)["id"]], summary="PRIVATE-CONTENT-DO-NOT-LOG")
        self.write([event(1), forged])
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(self.ids(), {event(1)["id"]})
        self.assertIn("rejected 1 known-schema", stderr.getvalue())
        self.assertNotIn("PRIVATE-CONTENT", stderr.getvalue())

    def test_same_id_content_retries_are_idempotent_across_files(self):
        one = event(1)
        self.write([one, one], "a.jsonl")
        self.write([dict(reversed(list(one.items())))], "b.jsonl")
        self.assertEqual(self.ids(), {one["id"]})

    def test_conflicting_valid_identity_in_another_file_fails_whole_snapshot(self):
        self.write([event(1)], "a.jsonl")
        self.write([event(1, summary="different bytes and meaning")], "b.jsonl")
        self.fails("identity-conflict")

    def test_effective_cycle_and_self_loop_fail_but_future_edge_does_not(self):
        self.write([event(1, supersedes=[event(2)["id"]]), event(2, supersedes=[event(1)["id"]])])
        self.fails("revision-cycle")
        self.write([event(1, supersedes=[event(1)["id"]])])
        self.fails("revision-cycle")
        self.write([event(1, supersedes=[event(2)["id"]]), event(2, valid_from="2999-01-01T00:00:00Z", supersedes=[event(1)["id"]])])
        self.assertEqual(self.ids(), {event(1)["id"]})

    def test_unknown_schema_and_malformed_json_are_not_silent_empty_results(self):
        self.write([event(1), event(2, schema_version="llm-wiki-memory-event/v99")])
        self.fails("unknown-event-schema")
        overflow = json.dumps(event(2, extra="overflow-number")).replace('"overflow-number"', '1e999').encode() + b"\n"
        for tail in (b"{incomplete\n", b"[]\n", b'{"x":NaN}\n', b'{"x":1,"x":2}\n', b'\xff\n', overflow):
            with self.subTest(tail=tail):
                path = self.write([event(1)])
                with path.open("ab") as handle:
                    handle.write(tail)
                self.fails("malformed-jsonl")

    def test_record_limit_allows_exact_eof_and_rejects_prefix(self):
        with patch.dict(G, {"MAX_EVENT_LINES": 2}):
            self.write([event(1)])
            self.assertEqual(len(self.ids()), 1)
            self.write([event(1), event(2)])
            self.assertEqual(len(self.ids()), 2)
            self.write([event(1), event(2), event(3, supersedes=[event(1)["id"]])])
            self.fails("record-count-limit")

    def test_total_byte_limit_and_line_limit_exact_eof(self):
        path = self.write([event(1)])
        size = path.stat().st_size
        for bound in (size, size + 1):
            with patch.dict(G, {"MAX_EVENT_TOTAL_BYTES": bound, "MAX_EVENT_LINE_BYTES": bound}):
                self.assertEqual(self.ids(), {event(1)["id"]})
        with patch.dict(G, {"MAX_EVENT_TOTAL_BYTES": size - 1}):
            self.fails("total-byte-limit")
        with patch.dict(G, {"MAX_EVENT_LINE_BYTES": size - 1}):
            self.fails("line-byte-limit")

    def test_file_and_directory_entry_bounds(self):
        with patch.dict(G, {"MAX_EVENT_FILES": 2, "MAX_EVENT_DIRECTORY_ENTRIES": 3}):
            self.write([event(1)], "a.jsonl")
            self.assertEqual(len(self.ids()), 1)
            self.write([event(2)], "b.jsonl")
            self.assertEqual(len(self.ids()), 2)
            self.write([event(3)], "c.jsonl")
            self.fails("file-count-limit")
        with patch.dict(G, {"MAX_EVENT_FILES": 4, "MAX_EVENT_DIRECTORY_ENTRIES": 3}):
            self.assertEqual(len(self.ids()), 3)
            (self.events / "not-an-event.txt").write_text("ignored-but-counted")
            self.fails("directory-entry-limit")

    def test_oversized_line_cannot_hide_a_later_correction(self):
        path = self.write([event(1)])
        with path.open("ab") as handle:
            handle.write(b"x" * (G["MAX_EVENT_LINE_BYTES"] + 1) + b"\n")
            handle.write((json.dumps(event(2, supersedes=[event(1)["id"]])) + "\n").encode())
        self.fails("line-byte-limit")

    def test_manifest_detects_append_replacement_or_new_month(self):
        original = G["_event_manifest"]
        for operation in ("append", "replace", "new-month"):
            with self.subTest(operation=operation):
                path = self.write([event(1)])
                counter = 0
                def changed(directory):
                    nonlocal counter
                    counter += 1
                    if counter == 2:
                        if operation == "append":
                            with path.open("a") as handle:
                                handle.write(json.dumps(event(2)) + "\n")
                        elif operation == "replace":
                            replacement = path.with_suffix(".tmp")
                            replacement.write_text(json.dumps(event(2)) + "\n")
                            os.replace(replacement, path)
                        else:
                            self.write([event(3)], "new-month.jsonl")
                    return original(directory)
                with patch.dict(G, {"_event_manifest": changed}):
                    self.fails("snapshot-changed")

    def test_unreadable_and_linked_jsonl_are_explicit_failures(self):
        path = self.write([event(1)])
        real_open = os.open
        def denied(file, *args, **kwargs):
            if Path(file) == path:
                raise PermissionError("PRIVATE-PATH-MUST-NOT-LEAK")
            return real_open(file, *args, **kwargs)
        with patch.object(os, "open", side_effect=denied):
            self.fails("unreadable")
        (self.events / "link.jsonl").symlink_to(path)
        self.fails("unsafe-event-file")

    def test_symlinked_memory_parent_cannot_read_outside_selected_vault(self):
        self.write([event(1, summary="outside-selected-vault")])
        other_vault = self.root / "separate-vault"
        other_vault.mkdir()
        (other_vault / "memory").symlink_to(self.root / "memory", target_is_directory=True)
        with self.assertRaises(API["EventSnapshotError"]) as error:
            API["load_events"](other_vault, CONTEXT)
        self.assertEqual(error.exception.code, "unsafe-event-directory")
        self.assertNotIn("outside-selected-vault", str(error.exception))

    def test_cli_failure_never_emits_partial_events_or_wiki_results(self):
        self.write([event(1), event(2, schema_version="unknown")])
        (self.root / "policy").mkdir()
        shutil.copyfile(REPO / "templates" / "access-policy.json", self.root / "policy" / "access.json")
        (self.root / "wiki").mkdir()
        (self.root / "wiki" / "safe.md").write_text("# snapshotmarker wiki page\n")
        environment = {key: value for key, value in os.environ.items() if not key.startswith("LLM_WIKI_")}
        environment["LLM_WIKI_ROOT"] = str(self.root)
        result = subprocess.run([sys.executable, str(ENGINE), "--agent-profile", "codex", "--query", "snapshotmarker", "--json"], env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("unknown-event-schema", result.stderr)
        self.assertNotIn(str(self.root), result.stderr)

    def test_writer_deprecated_terminal_cannot_reopen_review(self):
        pending = event(1, actor={"type": "ai", "id": "codex"}, epistemic_status="ai-proposed", review_status="pending")
        for changes in ({}, {"epistemic_status": "disputed", "review_status": "rejected"}):
            with self.subTest(changes=changes):
                terminal = event(2, lifecycle="deprecated", supersedes=[pending["id"]], **changes)
                self.assertTrue(WRITER["is_effective_terminal_revision"](terminal))
                with self.assertRaisesRegex(ValueError, "no longer current"):
                    WRITER["current_pending_proposal"]({pending["id"]: pending, terminal["id"]: terminal}, pending["id"])
                self.write([pending, terminal])
                self.assertEqual(self.ids(), set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
