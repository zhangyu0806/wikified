#!/usr/bin/env python3
"""Read-only, cross-process search fences over disposable synthetic vaults."""
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

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = runpy.run_path(str(ROOT / "tests/test-continuing-context.py"))
M, E = FIXTURE["M"], FIXTURE["E"]


class SearchGeneration(unittest.TestCase):
    setUp = FIXTURE["Context"].setUp
    page = FIXTURE["Context"].page

    def cli(self, *args, profile="codex"):
        return subprocess.run([sys.executable, "-B", str(ROOT / "bin/llm-wiki-context"),
            "--root", str(self.root), "--agent-profile", profile, *args], text=True,
            capture_output=True, timeout=15, env={"PATH": os.environ.get("PATH", os.defpath),
            "PYTHONDONTWRITEBYTECODE": "1"})

    def observe(self, profile="codex"):
        result = self.cli("--observe-generation", profile=profile)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(set(payload), {"schemaVersion", "profile", "generation"})
        self.assertEqual(payload["schemaVersion"], 1)
        self.assertEqual(payload["profile"], profile)
        self.assertRegex(payload["generation"], r"^sha256:[0-9a-f]{64}$")
        self.assertNotIn(str(self.root), result.stdout + result.stderr)
        return payload["generation"]

    def restore_after_write(self, path):
        original, info = path.read_bytes(), path.stat()
        path.write_bytes(original + b"temporary synthetic change\n")
        path.write_bytes(original)
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))

    def test_real_cli_stability_profile_root_binding_and_no_writes(self):
        self.page(body="Synthetic private marker which must never enter a generation reply")
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        first = self.observe()
        self.assertEqual(first, self.observe())
        self.assertNotEqual(first, self.observe("opencode"))
        with tempfile.TemporaryDirectory(prefix="generation-copy-") as temporary:
            original = self.root
            self.root = Path(temporary) / "vault"
            shutil.copytree(original, self.root)
            self.assertNotEqual(first, self.observe())
            self.root = original
        after = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_same_bytes_and_mtime_restore_changes_page_policy_and_event_generations(self):
        self.page()
        event = self.root / "memory/events/synthetic.jsonl"
        event.write_text("\n")
        for path in (self.root / "notes/one.md", self.root / "policy/access.json", event):
            with self.subTest(path=path.name):
                before = self.observe()
                self.restore_after_write(path)
                self.assertNotEqual(before, self.observe())

    def test_page_and_directory_create_remove_aba_is_observed(self):
        self.page()
        before = self.observe()
        temporary = self.root / "notes/transient.md"
        temporary.write_text("Transient synthetic page")
        temporary.unlink()
        self.assertNotEqual(before, self.observe())
        before = self.observe()
        temporary = self.root / "wiki/transient"
        temporary.mkdir()
        temporary.rmdir()
        self.assertNotEqual(before, self.observe())

    def test_missing_event_tree_creation_and_removal_changes_generation(self):
        shutil.rmtree(self.root / "memory")
        before = self.observe()
        (self.root / "memory/events").mkdir(parents=True)
        shutil.rmtree(self.root / "memory")
        self.assertNotEqual(before, self.observe())

    def test_ancestor_replacement_is_bound_even_with_same_vault_inode(self):
        self.page()
        with tempfile.TemporaryDirectory(prefix="generation-ancestor-") as temporary:
            top = Path(temporary)
            ancestor = top / "ancestor"
            ancestor.mkdir()
            shutil.copytree(self.root, ancestor / "vault")
            original = self.root
            self.root = ancestor / "vault"
            inode = self.root.stat().st_ino
            before = self.observe()
            ancestor.rename(top / "previous")
            ancestor.mkdir()
            (top / "previous/vault").rename(self.root)
            self.assertEqual(inode, self.root.stat().st_ino)
            self.assertNotEqual(before, self.observe())
            self.root = original

    def test_nested_ancestor_replacement_without_page_stat_changes_is_observed(self):
        branch = self.root / "notes/stable/branch"
        (branch / "nested").mkdir(parents=True)
        self.page("stable/branch/nested/one")
        original_manifest = E["_page_files"](self.root)
        before = self.observe()
        with tempfile.TemporaryDirectory(prefix="generation-subtree-") as temporary:
            moved = Path(temporary) / "branch"
            branch.rename(moved)
            branch.mkdir()
            (moved / "nested").rename(branch / "nested")
            self.assertEqual(original_manifest, E["_page_files"](self.root))
            self.assertNotEqual(before, self.observe())

    def test_capture_verify_races_return_no_generation(self):
        self.page()
        original = E["PageGeneration"].verify
        calls = 0
        def mutate(generation):
            nonlocal calls
            original(generation)
            calls += 1
            if calls == 1:
                self.restore_after_write(self.root / "notes/one.md")
        with patch.object(E["PageGeneration"], "verify", mutate), self.assertRaises(Exception):
            M["observe_generation"](self.root, "codex")

    def test_digest_ignores_scandir_order_for_links_and_empty_directories(self):
        self.page()
        for name in ("empty-b", "empty-a"):
            (self.root / "notes" / name).mkdir()
        for name in ("link-b", "link-a"):
            (self.root / "notes" / name).symlink_to("synthetic-missing-target")
        before = M["observe_generation"](self.root, "codex")
        original = os.scandir
        class ReversedEntries:
            def __init__(self, path):
                with original(path) as entries:
                    self.entries = iter(reversed(list(entries)))
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def __iter__(self): return self
            def __next__(self): return next(self.entries)
            def close(self): pass
        with patch("os.scandir", ReversedEntries):
            self.assertEqual(before, M["observe_generation"](self.root, "codex"))

    def test_event_capture_verify_race_is_rejected(self):
        self.page()
        original = E["_event_manifest"]
        calls = 0
        def mutate(directory):
            nonlocal calls
            observed = original(directory)
            calls += 1
            if calls == 1:
                (directory / "new.jsonl").write_text("\n")
            return observed
        with patch.dict(E, _event_manifest=mutate), self.assertRaises(Exception):
            M["observe_generation"](self.root, "codex")

    def test_directory_bound_unsafe_events_and_symlink_root_fail_closed(self):
        self.page()
        (self.root / "notes/nested").mkdir()
        with patch.dict(E, MAX_PAGE_DIRECTORY_ENTRIES=1), self.assertRaises(Exception):
            M["observe_generation"](self.root, "codex")
        (self.root / "memory/events/unsafe.jsonl").symlink_to(self.root / "notes/one.md")
        denied = self.cli("--observe-generation")
        self.assertNotEqual(denied.returncode, 0)
        self.assertEqual(denied.stdout, "")
        self.assertNotIn("unsafe.jsonl", denied.stderr)
        (self.root / "memory/events/unsafe.jsonl").unlink()
        with tempfile.TemporaryDirectory(prefix="generation-alias-") as temporary:
            alias = Path(temporary) / "alias"
            alias.symlink_to(self.root, target_is_directory=True)
            original, self.root = self.root, alias
            self.assertNotEqual(self.cli("--observe-generation").returncode, 0)
            self.root = original

    def test_observer_arguments_are_explicit_mutually_exclusive_and_not_privileged(self):
        row = self.page()
        request = json.dumps(dict(schemaVersion=1, selections=[row]))
        self.assertEqual(self.cli("--request", request).returncode, 0)
        for args in ((), ("--observe-generation", "--request", request),
                     ("--observe-generation", "--observe-generation"), ("--observe-generation=true",)):
            result = self.cli(*args)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
        result = self.cli("--observe-generation", profile="human")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_real_cli_page_count_limit_returns_no_partial_generation(self):
        for number in range(E["MAX_WIKI_FILES"] + 1):
            (self.root / "notes" / f"synthetic-{number}.md").touch()
        result = self.cli("--observe-generation")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("synthetic-", result.stderr)


SOURCE = runpy.run_path(str(ROOT / "tests/test-source-review-retrieval.py"))


class SourceReviewGeneration(unittest.TestCase):
    setUp = SOURCE["SourceReviewRetrieval"].setUp
    write = SOURCE["SourceReviewRetrieval"].write
    ref = SOURCE["SourceReviewRetrieval"].ref
    derived = SOURCE["SourceReviewRetrieval"].derived
    enable = SOURCE["SourceReviewRetrieval"].enable
    append = SOURCE["SourceReviewRetrieval"].append
    cli = SearchGeneration.cli
    observe = SearchGeneration.observe
    restore_after_write = SearchGeneration.restore_after_write

    def test_hold_append_and_identical_ledger_restore_do_not_reuse_generation(self):
        self.derived()
        self.enable()
        original, metadata = self.ledger.read_bytes(), self.ledger.stat()
        before = self.observe()
        self.append("hold", SOURCE["DERIVED_ID"])
        held = self.observe()
        self.assertNotEqual(before, held)
        self.ledger.write_bytes(original)
        os.utime(self.ledger, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
        self.assertNotIn(self.observe(), (before, held))

    def test_configured_missing_ledger_is_not_a_disabled_generation(self):
        self.enable()
        self.ledger.unlink()
        denied = self.cli("--observe-generation")
        self.assertNotEqual(denied.returncode, 0)
        self.assertEqual(denied.stdout, "")
        self.assertNotIn("ledger", denied.stderr)

    def test_review_lock_same_byte_restore_changes_generation(self):
        self.enable()
        before = self.observe()
        self.restore_after_write(self.root / "memory/.memory.lock")
        self.assertNotEqual(before, self.observe())

    def test_real_cli_contended_review_lock_returns_no_generation(self):
        import fcntl
        self.enable()
        with (self.root / "memory/.memory.lock").open("rb") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.cli("--observe-generation")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertNotIn(".memory.lock", result.stderr)


if __name__ == "__main__":
    unittest.main()
