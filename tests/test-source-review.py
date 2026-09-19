#!/usr/bin/env python3
"""Synthetic POSIX CLI, persistence, CAS, retry and failure-path coverage."""
import copy
from datetime import datetime, timezone
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
CORE = runpy.run_path(str(REPO / "bin/llm_wiki_source_review.py"))
CLI = runpy.run_path(str(REPO / "bin/llm-wiki-source-review"), run_name="source_review_test_cli")
ENGINE = CLI["ENGINE"]
CAPABILITY = "TEST_ONLY_SOURCE_REVIEW_AUTHORITY_123456789"
IDENTITY = "memory:synthetic-held"
MARKER = "syntheticheldconclusionmarker"


class SourceReviewCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wikified-source-review-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for relative in ("policy", "notes", "wiki", "memory/events"):
            (self.root / relative).mkdir(parents=True)
        shutil.copyfile(REPO / "templates/access-policy.json", self.root / "policy/access.json")
        self.source = self.root / "wiki/source.md"
        self.source.write_text('---\nmemory_id: source:synthetic\ndomain: work\nsensitivity: internal\nreview_state: approved\ntarget_profiles: [codex, opencode]\nepistemic_status: human-stated\n---\nSynthetic evidence.\n')
        self.page = self.root / "notes/claim.md"
        self.fields = self.write_review()
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LLM_WIKI_ROOT": str(self.root),
                    "LLM_WIKI_SOURCE_REVIEW_CAPABILITY": CAPABILITY, "LLM_WIKI_SOURCE_REVIEW_ACTOR": "human:synthetic",
                    "PYTHONDONTWRITEBYTECODE": "1"}

    def write_review(self, previous=None, *, relation="current-derived", keep_history=True, at=None):
        fields = {"memory_id": IDENTITY, "domain": "work", "sensitivity": "internal", "review_state": "approved",
                  "target_profiles": ["codex", "opencode"], "epistemic_status": "human-confirmed"}
        history = copy.deepcopy([*previous["mind2one"]["review-history"], previous["mind2one"]["review"]] if previous and keep_history else [])
        product = {"version": 2, "id": IDENTITY, "kind": "memory", "origin": "ai", "review-state": "accepted",
                   "reviewed-by": "human", "reviewed-at": at or (datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z") if previous else "2026-01-01T00:00:00.000Z"),
                   "review-history": history, "sources": [{"kind": "document", "id": "source:synthetic",
                    "revision": CORE["raw_revision"](self.source.read_bytes()), "relation": relation, "locator": "wiki/source.md"}]}
        fields["mind2one"] = product
        body = MARKER + "\n"
        product["review"] = {"version": 1, "algorithm": "m2o-semantic-v1", "record-id": IDENTITY,
            "content-revision": ENGINE["compute_content_revision"](fields, body), "policy-revision": ENGINE["compute_policy_revision"](fields),
            "decision": "accept", "actor": {"type": "human", "id": "human:synthetic"}, "at": product["reviewed-at"],
            "reason": "Synthetic re-review", "request-id": "review:" + str(len(history)),
            "previous-revision": history[-1]["content-revision"] if history else None}
        raw = "---\n" + "\n".join(json.dumps(k) + ": " + json.dumps(v) for k, v in fields.items()) + "\n---\n" + body
        self.page.write_text(raw)
        return fields

    def call(self, action, *, ok=True, **fields):
        request = {"action": action, "capability": CAPABILITY, **fields}
        completed = subprocess.run([sys.executable, str(REPO / "bin/llm-wiki-source-review")], input=json.dumps(request),
                                   env=self.env, text=True, capture_output=True, timeout=10)
        self.assertNotIn(CAPABILITY, completed.stdout + completed.stderr)
        if ok:
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            return json.loads(completed.stdout)
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, CLI["ERROR"] + "\n")
        return completed

    def replace_fields(self, fields, body=MARKER + "\n"):
        self.page.write_text("---\n" + "\n".join(json.dumps(k) + ": " + json.dumps(v) for k, v in fields.items()) + "\n---\n" + body)

    def inspect(self):
        return self.call("inspect", record_id=IDENTITY)

    def initialize(self):
        status = self.call("status")
        self.init_request = {"request_id": "init:synthetic", "expected_policy_revision": status["policy_revision"]}
        return self.call("initialize", **self.init_request)

    def mutation(self, action, request_id, **changes):
        inspected = self.call("inspect", record_id=IDENTITY)
        return {"record_id": IDENTITY, "request_id": request_id, "reason": "source-change" if action == "hold" else "human-reverified",
                **{"expected_" + key: inspected[key] for key in ("ledger_revision", "record_revision", "policy_revision")}, **changes}

    def hold(self):
        self.initialize()
        request = self.mutation("hold", "hold:synthetic")
        self.call("hold", **request)
        return request

    def snapshot(self):
        return CORE["read_snapshot"](self.root, json.loads((self.root / "policy/access.json").read_text()))

    def recalled(self):
        result = subprocess.run([sys.executable, str(REPO / "bin/llm-wiki-enrich"), "--agent-profile", "codex", "--query", MARKER, "--json"],
                                env=self.env, text=True, capture_output=True, timeout=10)
        return MARKER in result.stdout

    def test_status_and_inspect_never_initialize_or_write(self):
        before = self.page.read_bytes()
        self.assertFalse(self.call("status")["enabled"])
        inspected = self.call("inspect", record_id=IDENTITY)
        self.assertFalse(inspected["held"])
        self.assertIsNone(inspected["ledger_revision"])
        self.assertFalse((self.root / "memory/.memory.lock").exists())
        self.assertFalse((self.root / "memory/source-review").exists())
        self.assertEqual(self.page.read_bytes(), before)

    def test_inspect_exact_metadata_contract_and_disabled_advisories(self):
        inspected = self.inspect()
        self.assertEqual(set(inspected), {"enabled", "ledger_revision", "policy_revision", "record_id", "record_revision",
            "binding_revision", "held", "hold_reason", "held_at", "can_hold", "can_release", "review_verified", "release_lineage_current"})
        self.assertTrue(inspected["review_verified"])
        for key in ("held", "can_hold", "can_release"):
            self.assertFalse(inspected[key])
        for key in ("hold_reason", "held_at", "release_lineage_current"):
            self.assertIsNone(inspected[key])
        self.assertEqual(inspected["record_revision"], CORE["raw_revision"](self.page.read_bytes()))
        self.assertEqual(inspected["binding_revision"], CORE["binding_revision"](self.fields["mind2one"]["review"]))
        self.initialize()
        self.assertTrue(self.inspect()["can_hold"])
        request = self.mutation("hold", "hold:metadata")
        self.call("hold", **request)
        inspected = self.inspect()
        self.assertTrue(inspected["held"])
        self.assertEqual(inspected["hold_reason"], "source-change")
        self.assertEqual(inspected["held_at"], self.snapshot().holds[IDENTITY]["at"])
        self.assertFalse(inspected["can_hold"])
        self.assertFalse(inspected["can_release"])
        response = json.dumps(inspected)
        for secret in (MARKER, "source:synthetic", "wiki/source.md", "human:synthetic", "hold:metadata", "Synthetic re-review", CAPABILITY):
            self.assertNotIn(secret, response)

    def test_pending_held_and_unbound_records_are_inspectable_but_not_mutable(self):
        self.hold()
        before = (self.root / "memory/source-review/ledger.jsonl").read_bytes()
        pending = copy.deepcopy(self.fields)
        pending["review_state"] = "pending"
        # Ordinary product edits genuinely mark the proposal as ai-proposed;
        # human inspection must not inherit the AI retrieval opt-in filter.
        pending["epistemic_status"] = "ai-proposed"
        pending["mind2one"]["review-state"] = "pending"
        pending["mind2one"].pop("review")
        self.replace_fields(pending)
        inspected = self.inspect()
        self.assertTrue(inspected["held"])
        self.assertIsNone(inspected["binding_revision"])
        self.assertEqual(inspected["hold_reason"], "source-change")
        for key in ("review_verified", "can_hold", "can_release"):
            self.assertFalse(inspected[key])
        self.call("release", ok=False, **self.mutation("release", "release:pending"))
        self.call("hold", ok=False, **self.mutation("hold", "hold:pending"))
        self.assertEqual((self.root / "memory/source-review/ledger.jsonl").read_bytes(), before)

    def test_withdrawn_record_inspection_requires_explicit_rejected_human_acl(self):
        self.hold()
        withdrawn = copy.deepcopy(self.fields)
        withdrawn["review_state"] = "rejected"
        withdrawn["mind2one"]["review-state"] = "withdrawn"
        self.replace_fields(withdrawn)
        self.call("inspect", record_id=IDENTITY, ok=False)
        path = self.root / "policy/access.json"
        policy = json.loads(path.read_text())
        policy["profiles"]["human"]["review_states"].append("rejected")
        path.write_text(json.dumps(policy))
        inspected = self.inspect()
        self.assertTrue(inspected["held"])
        for key in ("review_verified", "can_hold", "can_release"):
            self.assertFalse(inspected[key])
        self.call("release", ok=False, **self.mutation("release", "release:withdrawn"))

    def test_unverified_human_legacy_and_foreign_binding_inspect_without_write_authority(self):
        self.initialize()
        for variant in ("mismatch", "human", "legacy", "foreign-binding"):
            with self.subTest(variant=variant):
                fields = copy.deepcopy(self.fields)
                body = MARKER + "\n"
                if variant == "mismatch":
                    body += "Unreviewed edit\n"
                elif variant == "legacy":
                    fields.pop("mind2one")
                elif variant == "human":
                    fields["mind2one"].update({"origin": "human", "review-state": "not-required"})
                    fields["mind2one"].pop("review")
                else:
                    fields["mind2one"]["review"]["record-id"] = "memory:different"
                self.replace_fields(fields, body)
                inspected = self.inspect()
                self.assertFalse(inspected["review_verified"])
                self.assertFalse(inspected["can_hold"])
                self.assertFalse(inspected["can_release"])
                self.call("hold", ok=False, **self.mutation("hold", "hold:" + variant))

    def test_release_advisory_tracks_all_current_review_requirements(self):
        self.hold()
        self.assertFalse(self.inspect()["can_release"])
        for variant in ("missing-history", "early-review", "independent", "policy-changed", "invalid-history"):
            with self.subTest(variant=variant):
                reviewed = self.write_review(self.fields, keep_history=variant != "missing-history",
                    at="2026-01-02T00:00:00.000Z" if variant == "early-review" else None,
                    relation="independent-judgment" if variant == "independent" else "current-derived")
                if variant == "policy-changed":
                    reviewed["sensitivity"] = "confidential"
                    reviewed["mind2one"]["review"]["policy-revision"] = ENGINE["compute_policy_revision"](reviewed)
                    self.replace_fields(reviewed)
                elif variant == "invalid-history":
                    reviewed["mind2one"]["review-history"][0]["previous-revision"] = "sha256:" + "0" * 64
                    self.replace_fields(reviewed)
                inspected = self.inspect()
                self.assertEqual(inspected["review_verified"], variant != "invalid-history")
                self.assertFalse(inspected["can_release"])
                self.call("release", ok=False, **self.mutation("release", "release:" + variant))
        self.write_review(self.fields)
        inspected = self.inspect()
        self.assertTrue(inspected["review_verified"])
        self.assertTrue(inspected["can_release"])
        target = CLI["record"](self.root, IDENTITY, ENGINE["access_context"](self.root, "human"))
        held = dict(self.snapshot().holds[IDENTITY])
        held["record_revision"] = target["record_revision"]
        self.assertFalse(CLI["releasable"](target, held))

    def test_latest_release_lineage_is_visible_even_for_held_or_rolled_back_records(self):
        original = self.page.read_bytes()
        self.hold()
        released_fields = self.write_review(self.fields)
        self.call("release", **self.mutation("release", "release:lineage"))
        self.assertTrue(self.inspect()["release_lineage_current"])
        self.write_review(released_fields)
        self.assertTrue(self.inspect()["release_lineage_current"])
        self.page.write_bytes(original)
        inspected = self.inspect()
        self.assertTrue(inspected["review_verified"])
        self.assertFalse(inspected["release_lineage_current"])
        self.write_review(released_fields)
        self.call("hold", **self.mutation("hold", "hold:again"))
        inspected = self.inspect()
        self.assertTrue(inspected["held"])
        self.assertTrue(inspected["release_lineage_current"])
        self.assertFalse(inspected["can_release"])
        pending = copy.deepcopy(released_fields)
        pending["review_state"] = "pending"
        pending["mind2one"]["review-state"] = "pending"
        self.replace_fields(pending)
        self.assertFalse(self.inspect()["release_lineage_current"])

    def test_record_symlink_hardlink_collision_and_parent_alias_inspection_fail_closed(self):
        original = self.page.read_bytes()
        outside = self.root / "elsewhere.md"
        outside.write_bytes(original)
        self.page.unlink(); self.page.symlink_to(outside)
        self.call("inspect", record_id=IDENTITY, ok=False)
        self.page.unlink(); os.link(outside, self.page)
        self.call("inspect", record_id=IDENTITY, ok=False)
        self.page.unlink(); self.page.write_bytes(original)
        other = self.root / "notes/duplicate.md"
        other.write_bytes(original)
        self.call("inspect", record_id=IDENTITY, ok=False)
        other.unlink()
        notes = self.root / "notes"
        notes.rename(self.root / "moved-notes")
        notes.symlink_to(self.root / "moved-notes", target_is_directory=True)
        self.call("inspect", record_id=IDENTITY, ok=False)

    def test_record_inspection_is_bounded_and_requires_persisted_identity(self):
        original = self.page.read_bytes()
        self.page.write_bytes(original + b"x" * ENGINE["MAX_PAGE_SNAPSHOT_BYTES"])
        self.call("inspect", record_id=IDENTITY, ok=False)
        self.page.write_bytes(original + b"\xff")
        self.call("inspect", record_id=IDENTITY, ok=False)
        fields = copy.deepcopy(self.fields)
        fields.pop("memory_id")
        self.replace_fields(fields)
        self.call("inspect", record_id=IDENTITY, ok=False)

    def test_inspect_rejects_record_manifest_directory_root_and_policy_drift(self):
        original = self.page.read_bytes()
        request = {"action": "inspect", "record_id": IDENTITY}
        for kind in ("record", "manifest", "directory", "root", "policy"):
            with self.subTest(kind=kind):
                self.page.write_bytes(original)
                real_read = ENGINE["_read_page_header"]
                changed = False
                def read_and_mutate(handle, identity):
                    nonlocal changed
                    metadata, prefix = real_read(handle, identity)
                    # Catalog uses a path-derived fallback; this exact ID is
                    # only used when the selected descriptor is reauthorized.
                    if identity == IDENTITY and not changed:
                        changed = True
                        if kind == "record":
                            self.page.write_bytes(original + b"changed")
                        elif kind == "manifest":
                            (self.root / "wiki/addition.md").write_text("new\n")
                        elif kind == "directory":
                            self.page.parent.rename(self.root / "old-notes")
                            (self.root / "notes").mkdir()
                            self.page.write_bytes(original)
                        elif kind == "root":
                            self.root.rename(self.root.with_name(self.root.name + "-replaced"))
                            shutil.copytree(self.root.with_name(self.root.name + "-replaced"), self.root)
                        else:
                            path = self.root / "policy/access.json"
                            path.write_bytes(path.read_bytes() + b"\n")
                    return metadata, prefix
                with mock.patch.dict(ENGINE, {"_read_page_header": read_and_mutate}):
                    with self.assertRaises(ValueError):
                        CLI["operate"](self.root, request, "human:synthetic")
                self.assertTrue(changed)
                if kind == "manifest":
                    (self.root / "wiki/addition.md").unlink()
                elif kind == "directory":
                    shutil.rmtree(self.root / "old-notes")
                elif kind == "root":
                    shutil.rmtree(self.root.with_name(self.root.name + "-replaced"))

    def test_human_acl_denial_precedes_selected_body_read(self):
        path = self.root / "policy/access.json"
        policy = json.loads(path.read_text())
        policy["profiles"]["human"]["domains"] = ["personal"]
        path.write_text(json.dumps(policy))
        real_fdopen = os.fdopen
        page_inode = self.page.stat().st_ino
        class GuardedPage:
            def __init__(self, handle): self.handle = handle
            def __enter__(self): return self
            def __exit__(self, *args): return self.handle.__exit__(*args)
            def __getattr__(self, name): return getattr(self.handle, name)
            def read(self, *args): raise AssertionError("unauthorized selected body read")
        def guarded_fdopen(descriptor, *args, **kwargs):
            is_page = os.fstat(descriptor).st_ino == page_inode
            handle = real_fdopen(descriptor, *args, **kwargs)
            return GuardedPage(handle) if is_page else handle
        with mock.patch.object(CLI["os"], "fdopen", side_effect=guarded_fdopen):
            with self.assertRaises(ValueError):
                CLI["operate"](self.root, {"action": "inspect", "record_id": IDENTITY}, "human:synthetic")

    def test_initialize_hold_restart_and_exact_retries(self):
        request = self.hold()
        self.assertIn(IDENTITY, self.snapshot().held_ids)
        self.assertFalse(self.recalled())
        self.assertTrue(self.call("hold", **request)["idempotent"])
        self.assertTrue(self.call("initialize", **self.init_request)["idempotent"])
        self.call("hold", ok=False, **{**request, "reason": "manual-concern"})
        self.assertEqual(len(self.snapshot().rows), 2)
        self.assertNotIn(CAPABILITY.encode(), (self.root / "memory/source-review/ledger.jsonl").read_bytes())

    def test_bytes_restore_relation_change_and_new_review_do_not_release(self):
        original = self.page.read_bytes()
        self.hold()
        self.page.write_bytes(original)
        self.assertFalse(self.recalled())
        self.write_review(self.fields, relation="independent-judgment")
        self.assertFalse(self.recalled())
        self.fields = self.write_review(self.fields)
        self.assertFalse(self.recalled())
        self.assertTrue(self.call("inspect", record_id=IDENTITY)["held"])

    def test_exact_retry_does_not_bypass_current_human_acl(self):
        request = self.hold()
        policy_path = self.root / "policy/access.json"
        policy = json.loads(policy_path.read_text())
        policy["profiles"]["human"]["domains"] = ["personal"]
        policy_path.write_text(json.dumps(policy))
        self.call("inspect", record_id=IDENTITY, ok=False)
        self.call("hold", **request, ok=False)
        self.assertIn(IDENTITY, self.snapshot().held_ids)

    def test_explicit_release_requires_new_historical_review_and_all_cas(self):
        self.hold()
        old = self.mutation("release", "release:old")
        self.call("release", ok=False, **old)
        self.write_review(self.fields, keep_history=False)
        self.call("release", ok=False, **self.mutation("release", "release:no-history"))
        self.write_review(self.fields, at="2026-01-02T00:00:00.000Z")
        self.call("release", ok=False, **self.mutation("release", "release:before-hold"))
        self.write_review(self.fields)
        request = self.mutation("release", "release:synthetic")
        for key in ("expected_ledger_revision", "expected_record_revision", "expected_policy_revision"):
            self.call("release", ok=False, **{**request, key: "sha256:" + "f" * 64})
        result = self.call("release", **request)
        self.assertFalse(result["held"])
        self.assertTrue(self.recalled())
        self.assertTrue(self.call("release", **request)["idempotent"])

    def test_initialize_orphan_genesis_exact_retry_and_conflict(self):
        request = {"action": "initialize", "capability": CAPABILITY, "request_id": "init:orphan",
                   "expected_policy_revision": self.call("status")["policy_revision"]}
        with mock.patch.object(CLI["os"], "replace", side_effect=OSError("synthetic")):
            with self.assertRaises(OSError):
                CLI["operate"](self.root, request, "human:synthetic")
        self.assertFalse(self.call("status")["enabled"])
        self.call("initialize", ok=False, **{k: v for k, v in {**request, "request_id": "wrong:retry"}.items() if k not in {"action", "capability"}})
        self.call("initialize", **{k: v for k, v in request.items() if k not in {"action", "capability"}})
        self.assertEqual(len(self.snapshot().rows), 1)

    def test_corrupt_empty_missing_duplicate_truncated_and_wrong_id_never_become_off(self):
        self.hold()
        path = self.root / "memory/source-review/ledger.jsonl"
        original = path.read_bytes()
        for data in (b"", original[:-1], original + original.split(b"\n", 1)[0] + b"\n", original + b"garbage\n"):
            path.write_bytes(data)
            self.call("status", ok=False)
            self.assertFalse(self.recalled())
        path.write_bytes(original)
        marker = self.root / "policy/access.json"
        policy = json.loads(marker.read_text()); policy["source_review"]["ledger_id"] = "0" * 32
        marker.write_text(json.dumps(policy))
        self.call("status", ok=False)
        path.unlink()
        self.call("status", ok=False)

    def test_symlink_hardlink_and_busy_lock_fail_closed(self):
        self.hold()
        path = self.root / "memory/source-review/ledger.jsonl"
        original = path.read_bytes()
        elsewhere = self.root / "synthetic-ledger.jsonl"
        elsewhere.write_bytes(original)
        path.unlink(); path.symlink_to(elsewhere)
        self.call("status", ok=False)
        path.unlink(); os.link(elsewhere, path)
        self.call("status", ok=False)
        path.unlink(); path.write_bytes(original)
        with CORE["memory_lock"](self.root, write=True):
            self.call("status", ok=False)
        self.assertTrue(self.call("status")["enabled"])

    def test_append_fsync_failure_rolls_back_release_under_lock(self):
        self.hold()
        self.write_review(self.fields)
        request = {"action": "release", "capability": CAPABILITY, **self.mutation("release", "release:failed")}
        before = (self.root / "memory/source-review/ledger.jsonl").read_bytes()
        real_fsync = os.fsync
        calls = 0
        def fail_once(descriptor):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("synthetic fsync")
            return real_fsync(descriptor)
        with mock.patch.object(CLI["os"], "fsync", side_effect=fail_once):
            with self.assertRaises(ValueError):
                CLI["operate"](self.root, request, "human:synthetic")
        self.assertEqual((self.root / "memory/source-review/ledger.jsonl").read_bytes(), before)
        self.assertIn(IDENTITY, self.snapshot().held_ids)

    def test_double_write_failure_reports_uncertain_not_unconditional_hold(self):
        self.hold()
        self.write_review(self.fields)
        request = {"action": "release", "capability": CAPABILITY, **self.mutation("release", "release:uncertain")}
        real_fdopen = os.fdopen
        class FailedRollback:
            def __init__(self, handle): self.handle = handle
            def __enter__(self): return self
            def __exit__(self, *args): return self.handle.__exit__(*args)
            def __getattr__(self, name): return getattr(self.handle, name)
            def truncate(self, size): raise OSError("synthetic rollback failure")
        def fdopen(descriptor, *args, **kwargs):
            handle = real_fdopen(descriptor, *args, **kwargs)
            return FailedRollback(handle) if args and args[0] == "r+b" else handle
        with mock.patch.object(CLI["os"], "fdopen", side_effect=fdopen), mock.patch.object(CLI["os"], "fsync", side_effect=OSError("synthetic fsync failure")):
            with self.assertRaises(CLI["UncertainWrite"]):
                CLI["operate"](self.root, request, "human:synthetic")
        # The exact authorized release may already exist. Never claim this
        # uncertain outcome preserved the hold; callers must stop and inspect.
        self.assertNotIn(IDENTITY, self.snapshot().held_ids)

    def test_initialize_policy_rename_then_fsync_failure_is_uncertain(self):
        request = {"action": "initialize", "capability": CAPABILITY, "request_id": "init:uncertain",
                   "expected_policy_revision": self.call("status")["policy_revision"]}
        real_sync = CLI["fsync_directory"]
        def fail_policy(path):
            if path == self.root / "policy":
                raise OSError("synthetic activation fsync")
            real_sync(path)
        with mock.patch.dict(CLI["initialize"].__globals__, {"fsync_directory": fail_policy}):
            with self.assertRaises(CLI["UncertainWrite"]):
                CLI["operate"](self.root, request, "human:synthetic")
        self.assertTrue(self.call("status")["enabled"])
        self.assertTrue(self.call("initialize", **{k: v for k, v in request.items() if k not in {"action", "capability"}})["idempotent"])

    def test_no_posix_lock_support_is_explicitly_unavailable_without_writes(self):
        with mock.patch.dict(sys.modules, {"fcntl": None}):
            with self.assertRaises(CORE["SourceReviewError"]):
                with CORE["memory_lock"](self.root, write=True, create=True):
                    self.fail("unsupported lock entered")
        self.assertFalse((self.root / "memory/.memory.lock").exists())

    def test_stale_policy_or_record_cas_and_ineligible_record_cannot_hold(self):
        self.initialize()
        request = self.mutation("hold", "hold:stale")
        policy_path = self.root / "policy/access.json"
        policy_path.write_bytes(policy_path.read_bytes() + b"\n")
        self.call("hold", ok=False, **request)
        request = self.mutation("hold", "hold:independent")
        self.write_review(self.fields, relation="independent-judgment")
        self.call("hold", ok=False, **request)
        self.call("hold", ok=False, **self.mutation("hold", "hold:independent-current-cas"))
        self.assertEqual(len(self.snapshot().rows), 1)

    def test_capability_is_mandatory_and_errors_are_constant(self):
        for changes in ({"capability": "wrong"}, {"record_id": "notes/claim.md"}, {"unknown": "private-sensitive-body"}):
            self.call("inspect", ok=False, **{"record_id": IDENTITY, **changes})
        unsupported = {**self.env, "LLM_WIKI_SOURCE_REVIEW_ACTOR": "not valid"}
        with mock.patch.dict(self.env, unsupported):
            self.call("status", ok=False)

    def test_missing_companion_has_only_fixed_diagnostic(self):
        isolated = self.root / "isolated-source-review"
        shutil.copyfile(REPO / "bin/llm-wiki-source-review", isolated)
        result = subprocess.run([sys.executable, str(isolated)], input="{}", env=self.env, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, CLI["ERROR"] + "\n")


if __name__ == "__main__":
    unittest.main()
