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
        history = ([*previous["mind2one"]["review-history"], previous["mind2one"]["review"]] if previous and keep_history else [])
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
