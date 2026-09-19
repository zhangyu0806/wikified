#!/usr/bin/env python3
"""Strict, read-only human queue coverage using disposable synthetic vaults."""
import copy
import json
import os
from pathlib import Path
import runpy
import unittest
from unittest import mock

FIXTURE = runpy.run_path(str(Path(__file__).with_name("test-source-review.py")), run_name="source_review_queue_fixture")
BASE = FIXTURE["SourceReviewCLI"]
CORE, CLI, ENGINE = FIXTURE["CORE"], FIXTURE["CLI"], FIXTURE["ENGINE"]
IDENTITY, MARKER, CAPABILITY = FIXTURE["IDENTITY"], FIXTURE["MARKER"], FIXTURE["CAPABILITY"]


class SourceReviewQueue(unittest.TestCase):
    setUp = BASE.setUp
    write_review = BASE.write_review
    call = BASE.call
    initialize = BASE.initialize
    mutation = BASE.mutation
    hold = BASE.hold
    snapshot = BASE.snapshot
    replace_fields = BASE.replace_fields
    inspect = BASE.inspect

    def queue(self):
        return self.call("queue")

    def add_held(self, count, *, domain="work"):
        """Build valid synthetic histories without spawning one process per row."""
        snapshot = self.snapshot()
        rows = list(snapshot.rows)
        for index in range(count):
            identity = "memory:queue-" + domain + "-" + str(index)
            fields = copy.deepcopy(self.fields)
            fields["memory_id"] = identity
            fields["domain"] = domain
            product = fields["mind2one"]
            product["id"] = identity
            product["review"]["record-id"] = identity
            product["review"]["policy-revision"] = ENGINE["compute_policy_revision"](fields)
            product["review"]["content-revision"] = ENGINE["compute_content_revision"](fields, MARKER + "\n")
            path = self.root / "notes" / ("queue-" + domain + "-" + str(index).zfill(3) + ".md")
            path.write_text("---\n" + "\n".join(json.dumps(key) + ": " + json.dumps(value) for key, value in fields.items()) + "\n---\n" + MARKER + "\n")
            target = {"record_id": identity, "record_revision": CORE["raw_revision"](path.read_bytes()),
                "binding_revision": CORE["binding_revision"](product["review"]), "binding": product["review"]}
            request = {"action": "hold", "request_id": "queue:" + domain + ":" + str(index), "reason": "manual-concern"}
            rows.append(CLI["make_row"](request, "human:synthetic", snapshot.ledger_id, len(rows), rows[-1]["entry_revision"], target))
        CORE["validate"](rows, snapshot.ledger_id)
        (self.root / "memory/source-review/ledger.jsonl").write_bytes(b"".join(CORE["encode"](row) for row in rows))

    def operate(self):
        return CLI["operate"](self.root, {"action": "queue"}, "human:synthetic")

    def test_disabled_does_not_scan_markdown_or_create_lock_or_ledger(self):
        self.page.write_text("---\nmalformed: [\n")
        with mock.patch.dict(CLI["queue"].__globals__, {"human_manifest": mock.Mock(side_effect=AssertionError("disabled scan"))}):
            result = self.operate()
        self.assertEqual(result, {**self.call("status"), "coverage": "not-enabled", "items": []})
        self.assertFalse((self.root / "memory/.memory.lock").exists())
        self.assertFalse((self.root / "memory/source-review").exists())

    def test_queue_request_is_fixed_and_requires_independent_capability(self):
        for fields in ({"capability": "wrong"}, {"limit": 100}, {"profile": "human"}, {"root": str(self.root)}, {"cursor": ""}, {"filter": "held"}):
            self.call("queue", ok=False, **fields)

    def test_enabled_exact_contract_matches_selected_inspection_without_metadata_leaks(self):
        self.hold()
        before = (self.page.read_bytes(), (self.root / "memory/source-review/ledger.jsonl").read_bytes())
        result = self.queue()
        self.assertEqual(set(result), {"enabled", "policy_revision", "ledger_revision", "coverage", "items"})
        self.assertEqual(result["coverage"], "complete")
        inspected = self.inspect()
        item = {"path": "notes/claim.md", **{key: value for key, value in inspected.items() if key not in {"enabled", "policy_revision", "ledger_revision"}}}
        self.assertEqual(result["items"], [item])
        encoded = json.dumps(result)
        for secret in (MARKER, "source:synthetic", "wiki/source.md", "human:synthetic", "hold:synthetic", "Synthetic re-review", CAPABILITY):
            self.assertNotIn(secret, encoded)
        self.assertEqual((self.page.read_bytes(), (self.root / "memory/source-review/ledger.jsonl").read_bytes()), before)

    def test_real_pending_ai_proposed_is_visible_to_human_not_releasable(self):
        self.hold()
        fields = copy.deepcopy(self.fields)
        fields["review_state"] = "pending"
        fields["epistemic_status"] = "ai-proposed"
        fields["mind2one"]["review-state"] = "pending"
        self.replace_fields(fields)
        item = self.queue()["items"][0]
        self.assertTrue(item["held"])
        self.assertFalse(item["review_verified"])
        self.assertFalse(item["can_release"])
        self.assertEqual(self.inspect()["record_revision"], item["record_revision"])
        self.call("release", ok=False, **self.mutation("release", "release:queue-pending"))

    def test_withdrawn_scope_uses_human_acl_not_an_automatic_review_grant(self):
        self.hold()
        fields = copy.deepcopy(self.fields)
        fields["review_state"] = "rejected"
        fields["epistemic_status"] = "disputed"
        fields["mind2one"]["review-state"] = "withdrawn"
        self.replace_fields(fields)
        self.assertEqual(self.queue()["items"], [])
        path = self.root / "policy/access.json"
        policy = json.loads(path.read_text())
        policy["profiles"]["human"]["review_states"].append("rejected")
        path.write_text(json.dumps(policy))
        self.assertTrue(self.queue()["items"][0]["held"])

    def test_only_held_or_stale_release_lineage_are_rows(self):
        self.initialize()
        self.assertEqual(self.queue()["items"], [])
        original = self.page.read_bytes()
        self.call("hold", **self.mutation("hold", "hold:queue"))
        self.write_review(self.fields)
        self.call("release", **self.mutation("release", "release:queue"))
        self.assertEqual(self.queue()["items"], [])
        self.page.write_bytes(original)
        item = self.queue()["items"][0]
        self.assertFalse(item["held"])
        self.assertFalse(item["release_lineage_current"])
        self.assertTrue(item["review_verified"])
        self.assertTrue(item["can_hold"])

    def test_missing_and_hidden_ledger_ids_do_not_become_rows_or_coverage_hints(self):
        self.hold()
        self.add_held(101, domain="personal")
        path = self.root / "policy/access.json"
        policy = json.loads(path.read_text())
        policy["profiles"]["human"]["domains"] = ["work"]
        path.write_text(json.dumps(policy))
        result = self.queue()
        self.assertEqual(result["coverage"], "complete")
        self.assertEqual(len(result["items"]), 1)
        self.assertNotIn("queue-personal", json.dumps(result))
        self.page.unlink()
        result = self.queue()
        self.assertEqual(result["coverage"], "complete")
        self.assertEqual(result["items"], [])

    def test_only_authorized_matching_overflow_produces_limited_without_total(self):
        self.initialize()
        self.add_held(101)
        result = self.queue()
        self.assertEqual(result["coverage"], "limited")
        self.assertEqual(len(result["items"]), 100)
        self.assertEqual(result["items"][0]["path"], "notes/queue-work-000.md")
        self.assertEqual(result["items"][-1]["path"], "notes/queue-work-099.md")
        self.assertEqual(set(result), {"enabled", "policy_revision", "ledger_revision", "coverage", "items"})
        self.assertLess(len(json.dumps(result).encode()), CLI["MAX_QUEUE_OUTPUT_BYTES"])

    def test_duplicate_malformed_and_nonregular_markdown_are_not_silently_skipped(self):
        self.hold()
        extra = self.root / "notes/extra.md"
        extra.write_bytes(self.page.read_bytes())
        self.call("queue", ok=False)
        extra.write_text("---\nmemory_id: bad\ndomain: [wrong\n---\nprivate\n")
        self.call("queue", ok=False)
        extra.unlink(); os.mkfifo(extra)
        self.call("queue", ok=False)

    def test_symlinked_tree_markdown_hardlinks_and_case_aliases_fail_closed(self):
        self.hold()
        linked = self.root / "notes/link.md"
        linked.symlink_to(self.page)
        self.call("queue", ok=False)
        linked.unlink(); os.link(self.page, linked)
        self.call("queue", ok=False)
        linked.unlink()
        alias = self.root / "notes/CLAIM.md"
        alias.write_text("plain note\n")
        self.call("queue", ok=False)
        alias.unlink()
        folder = self.root / "notes/linked-directory"
        folder.symlink_to(self.root / "wiki", target_is_directory=True)
        self.call("queue", ok=False)

    def test_header_scan_failure_even_outside_authorized_domain_is_fixed_not_partial_empty(self):
        self.hold()
        bad = self.root / "notes/private-broken.md"
        bad.write_text("---\ndomain: personal\nsensitivity: restricted\nreview_state: [invalid\n---\nprivate contents\n")
        self.call("queue", ok=False)

    def test_unsupported_authorized_matching_path_is_rejected_not_omitted(self):
        self.hold()
        self.page.rename(self.root / "notes/.hidden.md")
        self.call("queue", ok=False)

    def test_renamed_record_uses_its_current_safe_locator(self):
        self.hold()
        destination = self.root / "notes/renamed-笔记.md"
        self.page.rename(destination)
        item = self.queue()["items"][0]
        self.assertEqual(item["path"], "notes/renamed-笔记.md")
        self.assertEqual(item["record_id"], IDENTITY)

    def test_queue_never_reads_unauthorized_or_nonledger_bodies(self):
        self.initialize()
        self.add_held(1, domain="personal")
        policy_path = self.root / "policy/access.json"
        policy = json.loads(policy_path.read_text())
        policy["profiles"]["human"]["domains"] = ["work"]
        policy_path.write_text(json.dumps(policy))
        protected = {self.page.stat().st_ino, (self.root / "notes/queue-personal-000.md").stat().st_ino}
        real_fdopen = os.fdopen
        class HeaderOnly:
            def __init__(self, handle): self.handle = handle
            def __enter__(self): return self
            def __exit__(self, *args): return self.handle.__exit__(*args)
            def __getattr__(self, name): return getattr(self.handle, name)
            def read(self, *args): raise AssertionError("queue read an excluded body")
        def guarded(descriptor, *args, **kwargs):
            deny = os.fstat(descriptor).st_ino in protected
            handle = real_fdopen(descriptor, *args, **kwargs)
            return HeaderOnly(handle) if deny else handle
        with mock.patch.object(CLI["os"], "fdopen", side_effect=guarded):
            self.assertEqual(self.operate()["items"], [])

    def test_body_and_output_budgets_refuse_instead_of_claiming_limited(self):
        self.hold()
        self.add_held(1)
        for key, maximum in (("MAX_QUEUE_READ_BYTES", 128), ("MAX_QUEUE_OUTPUT_BYTES", 128)):
            with self.subTest(bound=key), mock.patch.dict(CLI["queue"].__globals__, {key: maximum}):
                with self.assertRaises(ValueError):
                    self.operate()
        self.page.write_bytes(self.page.read_bytes() + b"x" * ENGINE["MAX_PAGE_SNAPSHOT_BYTES"])
        self.call("queue", ok=False)

    def test_bad_utf8_missing_ledger_and_busy_lock_fail_without_an_empty_result(self):
        self.hold()
        original = self.page.read_bytes()
        self.page.write_bytes(original + b"\xff")
        self.call("queue", ok=False)
        self.page.write_bytes(original)
        with CORE["memory_lock"](self.root, write=True):
            self.call("queue", ok=False)
        (self.root / "memory/source-review/ledger.jsonl").unlink()
        self.call("queue", ok=False)

    def test_manifest_and_catalog_scan_counts_do_not_grow_per_record(self):
        self.initialize()
        self.add_held(10)
        manifest = mock.Mock(wraps=CLI["human_manifest"])
        catalog = mock.Mock(wraps=CLI["human_catalog"])
        with mock.patch.dict(CLI["queue"].__globals__, {"human_manifest": manifest, "human_catalog": catalog}):
            self.assertEqual(len(self.operate()["items"]), 10)
        self.assertEqual(manifest.call_count, 3)
        self.assertEqual(catalog.call_count, 1)

    def test_generation_fences_reject_prior_record_manifest_policy_and_ledger_drift(self):
        self.hold()
        self.add_held(1)
        ledger_path = self.root / "memory/source-review/ledger.jsonl"
        policy_path = self.root / "policy/access.json"
        before = {self.page: self.page.read_bytes(), ledger_path: ledger_path.read_bytes(), policy_path: policy_path.read_bytes()}
        later = self.root / "notes/queue-work-000.md"
        real_fdopen = os.fdopen
        for kind in ("prior-record", "manifest", "policy", "ledger"):
            with self.subTest(kind=kind):
                changed = False
                class ChangedDuringRead:
                    def __init__(self, handle): self.handle = handle
                    def __enter__(self): return self
                    def __exit__(self, *args): return self.handle.__exit__(*args)
                    def __getattr__(self, name): return getattr(self.handle, name)
                    def read(self, *args):
                        nonlocal changed
                        result = self.handle.read(*args)
                        if not changed:
                            changed = True
                            if kind == "prior-record": self_path = self_outer.page
                            elif kind == "policy": self_path = policy_path
                            elif kind == "ledger": self_path = ledger_path
                            else: self_path = self_outer.root / "notes/addition.md"
                            self_path.write_bytes((self_path.read_bytes() if self_path.exists() else b"") + b"\n")
                        return result
                self_outer = self
                inode = later.stat().st_ino
                def change(descriptor, *args, **kwargs):
                    selected = os.fstat(descriptor).st_ino == inode
                    handle = real_fdopen(descriptor, *args, **kwargs)
                    return ChangedDuringRead(handle) if selected else handle
                try:
                    with mock.patch.object(CLI["os"], "fdopen", side_effect=change):
                        with self.assertRaises(ValueError):
                            self.operate()
                    self.assertTrue(changed)
                finally:
                    for path, data in before.items(): path.write_bytes(data)
                    (self.root / "notes/addition.md").unlink(missing_ok=True)


del BASE  # unittest must not discover the imported fixture's separate suite.

if __name__ == "__main__":
    unittest.main()
