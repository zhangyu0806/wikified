#!/usr/bin/env python3
"""Final serialization races over disposable, genuinely governed source files."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = runpy.run_path(str(ROOT / "tests/test-event-source-freshness.py"), run_name="output_fixtures")
API = runpy.run_path(str(ROOT / "bin/llm-wiki-context"), run_name="output_context")
ENGINE = API["ENGINE"]
ERRORS = (ValueError, ENGINE["PageSnapshotError"], ENGINE["EventSnapshotError"], ENGINE["PolicyError"])


class Output:
    """Capture both legacy print and a single binary write without a real pipe."""
    def __init__(self):
        self.buffer = io.BytesIO()
        self.writes = []
        write = self.buffer.write
        def record(value):
            self.writes.append(value)
            return write(value)
        self.buffer.write = record

    def write(self, value):
        return self.buffer.write(value.encode("utf-8"))

    def flush(self):
        pass


class OutputFence(unittest.TestCase):
    @contextlib.contextmanager
    def fixture(self, kind="page", hold=False):
        f = FIXTURES["EventSources"]()
        f.setUp()
        try:
            policy = f.root / "policy/access.json"
            value = json.loads(policy.read_text())
            for profile in ("codex", "opencode"):
                value["profiles"][profile]["domains"] = ["work", "personal"]
            policy.write_text(json.dumps(value))
            ref = f.source(domain="personal")
            if hold:
                f.enable_hold()
            prefix = f.write_page("notes/prefix.md", "note:prefix", "Synthetic first item must not escape.\n")
            prefix_selection = {"kind": "page", "memoryId": "note:prefix",
                                "revision": "sha256:" + hashlib.sha256(prefix.read_bytes()).hexdigest()}
            if kind == "event":
                event = f.bound([ref])  # Real independent review CLI, including v5 bindings.
                selected = {"kind": "event", "memoryId": event["record_id"],
                            "revision": FIXTURES["C"]["record_revision"](event)}
            else:
                body = "Synthetic output evidence 中文😀\n" * (900 if kind == "page-fragment" else 1)
                page = f.write_page("notes/derived.md", "note:derived", body, ai=True, sources=[ref])
                selected = {"kind": "page", "memoryId": "note:derived",
                            "revision": "sha256:" + hashlib.sha256(page.read_bytes()).hexdigest()}
                if kind == "page-fragment":
                    pages = ENGINE["page_snapshots"](f.root, ENGINE["access_context"](f.root, "codex"), None)
                    snapshot = next(snapshot for _, snapshot in pages if snapshot.metadata.memory_id == "note:derived")
                    text = ENGINE["redact"](ENGINE["projected_page_text"](snapshot))
                    pages.verify()
                    selected.update(kind=kind, projectionRevision="sha256:" + hashlib.sha256(text.encode()).hexdigest(),
                                    offset=100, length=8000)
            yield f, [prefix_selection, selected]
        finally:
            f.doCleanups()

    def mutate(self, f, mutation, profile="codex"):
        source = f.root / "notes/source.md"
        if mutation == "edit":
            source.write_bytes(source.read_bytes() + b"Synthetic changed evidence.\n")
        elif mutation == "delete":
            source.unlink()
        elif mutation == "withdraw":
            fields = ENGINE["page_metadata"](source, f.root / "notes").frontmatter
            fields["review_state"] = "rejected"
            fields["epistemic_status"] = "disputed"
            fields["mind2one"]["review-state"] = "withdrawn"
            body = source.read_text().split("\n---\n", 1)[1]
            source.write_text("---\n" + "\n".join(json.dumps(key) + ": " + json.dumps(value)
                for key, value in fields.items()) + "\n---\n" + body)
            self.assertEqual(ENGINE["page_metadata"](source, f.root / "notes").governance["review_status"], "rejected")
        elif mutation == "policy":
            path = f.root / "policy/access.json"
            value = json.loads(path.read_text())
            value["profiles"][profile]["domains"] = ["work"]
            path.write_text(json.dumps(value))
        elif mutation == "hold":
            f.append("hold", "source:synthetic")
        elif mutation == "event":
            (f.root / "memory/events/new.jsonl").write_text("\n")
        elif mutation == "directory-aba":
            path = f.root / "notes/transient"
            path.mkdir(); path.rmdir()
        elif mutation == "restore":
            original, info = source.read_bytes(), source.stat()
            source.write_bytes(original + b"Temporary change.\n")
            source.write_bytes(original)
            os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns))
        elif mutation == "root":
            previous = f.root.with_name(f.root.name + "-previous")
            f.root.rename(previous)
            f.addCleanup(shutil.rmtree, previous)
            shutil.copytree(previous, f.root)
        else:
            raise AssertionError("Unknown synthetic mutation")

    def invoke(self, root, profile, request=None, after_json=None, after_encode=None, serialization_error=None):
        argv = ["llm-wiki-context", "--root", str(root), "--agent-profile", profile]
        argv += ["--observe-generation"] if request is None else ["--request", json.dumps(request)]
        dumps = json.dumps
        output, terminal = Output(), []

        class EncodedBoundary(str):
            def __add__(self, other):
                return EncodedBoundary(super().__add__(other))

            def encode(self, *args, **kwargs):
                result = super().encode(*args, **kwargs)
                after_encode()
                return result

        def serialize(value, *args, **kwargs):
            result = dumps(value, *args, **kwargs)
            if isinstance(value, dict) and value.get("profile") == profile and ("items" in value or "generation" in value):
                terminal.append(True)
                if serialization_error == "json":
                    raise ValueError("Synthetic serialization failure")
                if serialization_error == "encoding":
                    result += "\ud800"
                if after_json:
                    after_json()
                if after_encode:
                    return EncodedBoundary(result)
            return result

        with patch.object(sys, "argv", argv), patch.object(sys, "stdout", output), patch.object(json, "dumps", serialize):
            try:
                API["main"]()
            except Exception as error:
                return output, terminal, error
        return output, terminal, None

    def assert_rejected(self, result):
        output, terminal, error = result
        self.assertEqual(terminal, [True], "The real result must reach the final serialization barrier")
        self.assertIsInstance(error, ERRORS)
        self.assertEqual(output.buffer.getvalue(), b"", "No successful item prefix or digest may escape")
        self.assertEqual(output.writes, [])

    def test_source_changes_after_serialization_reject_pages_events_and_fragments(self):
        for profile in ("codex", "opencode"):
            for kind, schema in (("page", 1), ("page", 2), ("event", 1), ("event", 2), ("page-fragment", 2)):
                for mutation in ("edit", "delete", "withdraw", "policy", "hold"):
                    with self.subTest(profile=profile, kind=kind, schema=schema, mutation=mutation), self.fixture(kind, mutation == "hold") as (f, selections):
                        request = {"schemaVersion": schema, "selections": selections}
                        baseline = API["prepare"](f.root, profile, request)
                        self.assertEqual([row["status"] for row in baseline["items"]], ["ready", "ready"])
                        source_bytes = (f.root / "notes/source.md").read_bytes()
                        self.assert_rejected(self.invoke(f.root, profile, request,
                            after_json=lambda: self.mutate(f, mutation, profile)))
                        self.assertEqual(API["prepare"](f.root, profile, request)["items"][1]["status"], "unavailable")
                        if mutation == "policy":
                            self.assertEqual((f.root / "notes/source.md").read_bytes(), source_bytes)
                            other = "opencode" if profile == "codex" else "codex"
                            self.assertEqual(API["prepare"](f.root, other, request)["items"][1]["status"], "ready")

    def test_encoding_boundary_also_precedes_final_context_verification(self):
        with self.fixture("event") as (f, selections):
            request = {"schemaVersion": 2, "selections": selections}
            self.assert_rejected(self.invoke(f.root, "codex", request, after_encode=lambda: self.mutate(f, "edit")))

    def test_nonready_only_packs_still_verify_after_serialization(self):
        for status in ("unavailable", "changed", "too-large"):
            with self.subTest(status=status), self.fixture() as (f, selections):
                selection = dict(selections[0])
                if status == "unavailable":
                    selection["memoryId"] = "note:absent"
                elif status == "changed":
                    selection["revision"] = "sha256:" + "0" * 64
                else:
                    path = f.write_page("notes/large.md", "note:large", "x" * 12001)
                    selection = {"kind": "page", "memoryId": "note:large",
                                 "revision": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}
                request = {"schemaVersion": 1, "selections": [selection]}
                self.assertEqual(API["prepare"](f.root, "codex", request)["items"][0]["status"], status)
                self.assert_rejected(self.invoke(f.root, "codex", request, after_json=lambda: self.mutate(f, "policy")))

    def test_event_manifest_change_after_serialization_rejects_pack(self):
        with self.fixture() as (f, selections):
            request = {"schemaVersion": 1, "selections": selections}
            self.assert_rejected(self.invoke(f.root, "codex", request, after_json=lambda: self.mutate(f, "event")))

    def test_initial_and_final_observations_reject_serialization_races(self):
        for phase in ("initial", "final"):
            for profile in ("codex", "opencode"):
                for mutation in ("edit", "policy", "hold", "event", "directory-aba", "restore", "root"):
                    with self.subTest(phase=phase, profile=profile, mutation=mutation), self.fixture(hold=mutation == "hold") as (f, _):
                        if phase == "final":
                            before = API["observe_generation"](f.root, profile)
                            self.assertEqual(before, API["observe_generation"](f.root, profile))
                        self.assert_rejected(self.invoke(f.root, profile, after_json=lambda: self.mutate(f, mutation, profile)))

    def test_observation_encoding_boundary_also_precedes_verification(self):
        with self.fixture() as (f, _):
            self.assert_rejected(self.invoke(f.root, "codex", after_encode=lambda: self.mutate(f, "directory-aba")))

    def test_internal_digest_serialization_is_verified_before_direct_return(self):
        with self.fixture() as (f, _):
            dumps, barrier = json.dumps, []
            def mutate_digest(value, *args, **kwargs):
                text = dumps(value, *args, **kwargs)
                if isinstance(value, dict) and value.get("protocol") == "wikified-search-generation/v1":
                    self.mutate(f, "edit"); barrier.append(True)
                return text
            with patch.object(json, "dumps", mutate_digest), self.assertRaises(ERRORS):
                API["observe_generation"](f.root, "codex")
            self.assertEqual(barrier, [True])

    def test_serialization_and_encoding_errors_never_write_a_prefix(self):
        for observe in (False, True):
            for failure in ("json", "encoding"):
                with self.subTest(observe=observe, failure=failure), self.fixture() as (f, selections):
                    request = None if observe else {"schemaVersion": 2, "selections": selections}
                    output, terminal, error = self.invoke(f.root, "codex", request, serialization_error=failure)
                    self.assertEqual(terminal, [True])
                    self.assertIsInstance(error, (ValueError, UnicodeError))
                    self.assertEqual(output.buffer.getvalue(), b"")
                    self.assertEqual(output.writes, [])

    def test_unchanged_wire_bytes_and_single_write_for_both_modes(self):
        for profile in ("codex", "opencode"):
            for schema in (1, 2):
                with self.subTest(profile=profile, schema=schema), self.fixture() as (f, selections):
                    request = {"schemaVersion": schema, "selections": selections}
                    expected = API["prepare"](f.root, profile, request)
                    output, terminal, error = self.invoke(f.root, profile, request)
                    self.assertIsNone(error)
                    self.assertEqual(output.buffer.getvalue(), (json.dumps(expected, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
                    self.assertEqual(len(output.writes), 1)
                    self.assertEqual(terminal, [True])
                    generation = API["observe_generation"](f.root, profile)
                    output, _, error = self.invoke(f.root, profile)
                    self.assertIsNone(error)
                    self.assertEqual(output.buffer.getvalue(), (json.dumps(generation, allow_nan=False) + "\n").encode("utf-8"))
                    self.assertEqual(len(output.writes), 1)
                    self.assertIsInstance(expected, dict)
                    self.assertIsInstance(generation, dict)


if __name__ == "__main__":
    unittest.main(verbosity=2)
