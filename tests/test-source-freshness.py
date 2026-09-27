#!/usr/bin/env python3
"""Synthetic, request-local P3 Markdown source freshness/export regressions."""
import contextlib
import copy
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("source_freshness_engine", str(REPO / "bin/llm-wiki-enrich"))
spec = importlib.util.spec_from_loader(loader.name, loader)
engine = importlib.util.module_from_spec(spec)
sys.modules[loader.name] = engine
loader.exec_module(engine)
MARKER = "syntheticfreshnessconclusionmarker"
SOURCE_ID = "source:hidden-synthetic-identity"
DERIVED_ID = "derived:synthetic"
PATH = "wiki/context/CRITICAL_FACTS.md"
AT = "2026-09-18T08:00:00.000Z"


class SourceFreshness(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wikified-source-freshness-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ("wiki/context", "notes", "policy", "memory/events"):
            (self.root / directory).mkdir(parents=True)
        shutil.copyfile(REPO / "templates/access-policy.json", self.root / "policy/access.json")
        self.source = self.write("notes/source.md", SOURCE_ID, "Synthetic primary evidence.\n", project="source-project")

    def write(self, path, identity, body, *, sources=None, ai=False, **fields):
        frontmatter = {"memory_id": identity, "domain": "work", "sensitivity": "internal",
                       "review_state": "approved", "target_profiles": ["codex", "opencode"],
                       "epistemic_status": "human-confirmed" if ai else "human-stated", **fields}
        product = {"version": 2, "id": identity, "kind": "memory" if ai else "note",
                   "origin": "ai" if ai else "human", "review-state": "accepted" if ai else "not-required",
                   "sources": [] if sources is None else sources}
        frontmatter["mind2one"] = product
        if ai:
            product.update({"reviewed-at": AT, "reviewed-by": "human", "review-history": []})
            product["review"] = {"version": 1, "algorithm": "m2o-semantic-v1", "record-id": identity,
                "content-revision": engine.compute_content_revision(frontmatter, body),
                "policy-revision": engine.compute_policy_revision(frontmatter), "decision": "accept",
                "actor": {"type": "human", "id": "synthetic-owner"}, "at": AT,
                "reason": "Synthetic review", "request-id": "synthetic-request", "previous-revision": None}
        raw = "---\n" + "\n".join(json.dumps(k) + ": " + json.dumps(v, ensure_ascii=False) for k, v in frontmatter.items()) + "\n---\n" + body
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(raw, encoding="utf-8")
        return target

    def ref(self, path=None, identity=SOURCE_ID, **changed):
        target = path if path is not None else self.source
        return {"kind": "document", "id": identity, "revision": "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest(),
                "relation": "current-derived", "label": "Hidden synthetic source label",
                "locator": "notes/source.md", **changed}

    def derived(self, sources=None, **fields):
        return self.write(PATH, DERIVED_ID, MARKER + "\n", ai=True,
                          sources=[self.ref()] if sources is None else sources, project="derived-project", **fields)

    def cli(self, *arguments, profile="codex"):
        return subprocess.run([sys.executable, str(REPO / "bin/llm-wiki-enrich"), "--agent-profile", profile, *arguments],
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LLM_WIKI_ROOT": str(self.root), "PYTHONDONTWRITEBYTECODE": "1"},
            text=True, capture_output=True, timeout=15)

    def surfaces(self, allowed, profiles=("codex", "opencode")):
        for profile in profiles:
            for arguments in (("--query", MARKER, "--json"), ("--query", MARKER, "--ranking", "legacy", "--json"),
                              ("--read-page", PATH), ("--read-page", PATH, "--offset", "0", "--length", "32000"),
                              ("--session-start", "--session-start-scope", "critical")):
                result = self.cli(*arguments, profile=profile)
                self.assertEqual(MARKER in result.stdout, allowed, (profile, arguments, result.stdout, result.stderr))
                self.assertNotIn("Traceback", result.stderr)
                self.assertNotIn(SOURCE_ID, result.stdout + result.stderr)
                self.assertNotIn("Hidden synthetic source label", result.stdout + result.stderr)

    def test_reviewed_current_source_exports_all_surfaces_without_source_metadata(self):
        page = self.derived()
        before = page.read_bytes()
        self.surfaces(True)
        query = json.loads(self.cli("--query", MARKER, "--project", "derived-project", "--json").stdout)
        self.assertEqual(query[0]["governance"]["source_freshness"], "current")
        self.assertEqual(query[0]["revision"], "sha256:" + hashlib.sha256(before).hexdigest())
        self.assertEqual(page.read_bytes(), before)
        direct = self.cli("--read-page", PATH).stdout
        self.assertNotIn('"sources"', direct)

    def test_edit_delete_and_exact_restore_reassessed_without_warm_cache(self):
        self.derived()
        original = self.source.read_bytes()
        self.source.write_bytes(original + b"Changed evidence.\n")
        self.surfaces(False)
        self.source.unlink()
        self.surfaces(False)
        self.source.write_bytes(original)
        self.surfaces(True)

    def test_rename_uses_stable_identity_not_advisory_locator(self):
        self.derived()
        self.source.rename(self.root / "notes/renamed.md")
        self.surfaces(True)

    def test_path_derived_fallback_identity_is_not_a_persistent_dependency(self):
        legacy = self.root / "wiki/legacy.md"
        legacy.write_text("Legacy ungoverned evidence.\n")
        fallback = engine.stable_memory_id("wiki", "legacy.md")
        self.derived([self.ref(legacy, fallback, locator="wiki/legacy.md")])
        self.surfaces(False, ("codex",))

    def test_chunk_pin_is_raw_revision_but_source_change_denies_later_chunk(self):
        page = self.derived()
        first = json.loads(self.cli("--read-page", PATH, "--offset", "0", "--length", "50").stdout)
        self.assertEqual(first["revision"], "sha256:" + hashlib.sha256(page.read_bytes()).hexdigest())
        arguments = ("--read-page", PATH, "--offset", "50", "--length", "32000", "--revision", first["revision"])
        self.assertEqual(self.cli(*arguments).returncode, 0)
        self.source.write_bytes(self.source.read_bytes() + b"Source changed between chunks.\n")
        denied = self.cli(*arguments)
        self.assertNotEqual(denied.returncode, 0)
        self.assertEqual(denied.stdout, "")

    def test_source_withdrawal_and_profile_narrowing_block_current_dependents(self):
        self.derived()
        original = self.source.read_bytes()
        self.write("notes/source.md", SOURCE_ID, "Synthetic primary evidence.\n", review_state="rejected")
        self.surfaces(False)
        self.source.write_bytes(original)
        self.write("notes/source.md", SOURCE_ID, "Synthetic primary evidence.\n", target_profiles=["codex"])
        self.derived()
        self.surfaces(True, ("codex",))
        self.surfaces(False, ("opencode",))

    def test_invalidated_p2_source_is_not_a_trusted_dependency(self):
        self.write("notes/source.md", SOURCE_ID, "Synthetic AI evidence.\n", ai=True)
        self.derived()
        self.surfaces(True)
        self.source.write_bytes(self.source.read_bytes() + b"Unreviewed changed evidence.\n")
        # Even a newly reviewed descendant cannot bind an untrusted P2 source.
        self.derived()
        self.surfaces(False)

    def test_current_dependency_chain_propagates_staleness(self):
        intermediate = self.write("notes/intermediate.md", "intermediate:synthetic", "Intermediate conclusion.\n",
                                  ai=True, sources=[self.ref()])
        self.derived([self.ref(intermediate, "intermediate:synthetic", locator="notes/intermediate.md")])
        self.surfaces(True)
        self.source.write_bytes(self.source.read_bytes() + b"Changed source.\n")
        self.surfaces(False)

    def test_historical_independent_and_legacy_edges_are_unassessed_not_followed(self):
        self.source.unlink()
        for relation in ("historical-citation", "independent-judgment", None):
            self.derived([{"kind": "document", "id": SOURCE_ID, "revision": "legacy-revision",
                           "relation": relation, "label": "Hidden synthetic source label", "locator": "notes/hidden.md"}])
            self.surfaces(True)
            query = json.loads(self.cli("--query", MARKER, "--json").stdout)
            self.assertEqual(query[0]["governance"]["source_freshness"], "unassessed")

    def test_bad_current_source_revision_or_kind_never_falls_back_to_locator(self):
        for changes in ({"revision": "truncated"}, {"id": "missing:identity"}, {"kind": "external"},
                        {"locator": "../private.md"}):
            self.derived([self.ref(**changes)])
            self.surfaces(False, ("codex",))

    def test_v1_nonempty_block_sources_are_opaque_and_fail_closed(self):
        source = ('---\nmemory_id: legacy:synthetic\ndomain: work\nsensitivity: internal\nreview_state: approved\n'
                  'target_profiles: [codex, opencode]\nepistemic_status: human-stated\nmind2one:\n'
                  '  version: 1\n  id: legacy:synthetic\n  origin: human\n  review-state: not-required\n')
        for declaration in ('  sources:\n    - kind: document\n      id: hidden:synthetic\n',
                            '  sources: [{"kind":"document","id":"hidden:synthetic"}]\n'):
            (self.root / PATH).write_text(source + declaration + "---\n" + MARKER + "\n")
            self.surfaces(False, ("codex",))
        (self.root / PATH).write_text(source + "  sources: []\n---\n" + MARKER + "\n")
        self.surfaces(True, ("codex",))
        rich = source + '  action:\n    level: goal\n    status: active\n  due-at: "2026-10-01"\n  sources: []\n---\n' + MARKER + "\n"
        (self.root / PATH).write_text(rich)
        for arguments in (("--read-page", PATH), ("--read-page", PATH, "--offset", "0", "--length", "32000")):
            result = self.cli(*arguments)
            exported = json.loads(result.stdout)["text"] if "--offset" in arguments else result.stdout
            self.assertIn('  action:\n    level: goal\n    status: active\n  due-at: "2026-10-01"\n', exported)
            self.assertNotIn("sources:", exported)

    def test_denied_source_never_enters_body_reader(self):
        self.write("notes/source.md", SOURCE_ID, "Never read private evidence.\n", target_profiles=["human"])
        self.derived()
        access = engine.access_context(self.root, "codex")
        original = engine.authorized_page_snapshot
        def spy(path, *args, **kwargs):
            self.assertNotEqual(path, self.source, "Denied dependency body reader was invoked")
            return original(path, *args, **kwargs)
        with mock.patch.object(engine, "authorized_page_snapshot", side_effect=spy):
            self.assertIsNone(engine.read_page(self.root, PATH, access, None))

    def test_direct_graph_capability_cannot_bypass_typed_source_gate(self):
        self.derived([self.ref(revision="invalid-current-pin")])
        report = self.root / "graphify-out/GRAPH_REPORT.md"
        report.parent.mkdir()
        report.write_bytes((self.root / PATH).read_bytes())
        result = self.cli("--query", MARKER, "--json", profile="human")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(MARKER, result.stdout)
        self.assertNotIn(SOURCE_ID, result.stdout)
        result = self.cli("--session-start", profile="human")
        self.assertNotIn(MARKER, result.stdout)
        report.write_text(MARKER + " legacy plain graph context\n")
        legacy = json.loads(self.cli("--query", MARKER, "--json", profile="human").stdout)
        self.assertEqual(legacy[0]["source"], "graphify-report")
        self.assertEqual(legacy[0]["governance"]["source_freshness"], "unassessed")

    def test_policy_drift_and_root_replacement_fail_closed(self):
        self.derived()
        access = engine.access_context(self.root, "codex")
        policy = self.root / "policy/access.json"
        policy.write_bytes(policy.read_bytes() + b"\n")
        with self.assertRaises(engine.PageSnapshotError):
            engine.page_snapshots(self.root, access, None)
        access = engine.access_context(self.root, "codex")
        original = engine.authorized_page_snapshot
        def change(*args, **kwargs):
            result = original(*args, **kwargs)
            policy.write_bytes(policy.read_bytes() + b"\n")
            return result
        with mock.patch.object(engine, "authorized_page_snapshot", side_effect=change):
            self.assertIsNone(engine.read_page(self.root, PATH, access, None))
        access = engine.access_context(self.root, "codex")
        moved = self.root.with_name(self.root.name + "-moved")
        self.root.rename(moved)
        try:
            shutil.copytree(moved, self.root)
            with self.assertRaises(engine.PageSnapshotError):
                engine.page_snapshots(self.root, access, None)
        finally:
            # Both are owned TemporaryDirectory paths; retain original cleanup.
            shutil.rmtree(moved)

    def test_just_before_output_policy_drift_emits_no_partial_query(self):
        self.derived()
        original = engine.render_json
        def render(*args, **kwargs):
            result = original(*args, **kwargs)
            policy = self.root / "policy/access.json"
            policy.write_bytes(policy.read_bytes() + b"\n")
            return result
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(engine, "root_path", return_value=self.root), mock.patch.object(engine, "render_json", side_effect=render), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(engine.main(["--agent-profile", "codex", "--query", MARKER, "--json"]), 4)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("snapshot-changed", stderr.getvalue())

    def test_complete_snapshot_budget_has_no_partial_query_or_session(self):
        self.derived()
        with mock.patch.object(engine, "MAX_WIKI_TOTAL_BYTES", 1):
            with self.assertRaises(engine.PageSnapshotError):
                engine.read_text_head(self.root / PATH, self.root, engine.access_context(self.root, "codex"))
            self.assertIsNone(engine.read_page(self.root, PATH, engine.access_context(self.root, "codex"), None))

    def test_session_rendering_rechecks_generation_before_return(self):
        self.derived()
        access = engine.access_context(self.root, "codex")
        original = engine.redact
        def changed(text):
            result = original(text)
            self.source.write_bytes(self.source.read_bytes() + b"changed during rendering\n")
            return result
        with mock.patch.object(engine, "redact", side_effect=changed):
            with self.assertRaises(engine.PageSnapshotError):
                engine.render_session_start(self.root, 2500, 5, access, "critical")

    def test_mcp_real_search_and_revision_pinned_chunk_revalidate_source(self):
        self.derived()
        for stale in (False, True):
            if stale:
                self.source.write_bytes(self.source.read_bytes() + b"Changed evidence.\n")
            for profile in ("codex", "opencode"):
                calls = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                         {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "search_pages", "arguments": {"query": MARKER}}},
                         {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "read_page", "arguments": {"path": PATH, "offset": 0, "length": 32000}}}]
                result = subprocess.run(["node", str(REPO / "bin/llm-wiki-mcp")],
                    input="".join(json.dumps(call) + "\n" for call in calls), text=True, capture_output=True, timeout=15,
                    env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LLM_WIKI_ROOT": str(self.root), "LLM_WIKI_AGENT_PROFILE": profile})
                responses = {item["id"]: item for item in map(json.loads, result.stdout.strip().split("\n"))}
                for identity in (2, 3):
                    self.assertEqual(MARKER in json.dumps(responses[identity]), not stale)
                    self.assertNotIn(SOURCE_ID, json.dumps(responses[identity]))


if __name__ == "__main__":
    unittest.main()
