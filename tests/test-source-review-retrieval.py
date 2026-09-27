#!/usr/bin/env python3
"""Synthetic persistent-hold export tests; never access an installed vault."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest import mock

# Reuse only the existing synthetic fixture builders, not its test cases.
fixtures = runpy.run_path(str(Path(__file__).with_name("test-source-freshness.py")))
engine, REPO = fixtures["engine"], fixtures["REPO"]
MARKER, SOURCE_ID, DERIVED_ID, PATH, AT = (fixtures[name] for name in ("MARKER", "SOURCE_ID", "DERIVED_ID", "PATH", "AT"))
LEDGER_ID = "1234567890abcdef1234567890abcdef"
HASH = "sha256:" + "a" * 64


class SourceReviewRetrieval(unittest.TestCase):
    setUp = fixtures["SourceFreshness"].setUp
    write = fixtures["SourceFreshness"].write
    ref = fixtures["SourceFreshness"].ref
    derived = fixtures["SourceFreshness"].derived
    cli = fixtures["SourceFreshness"].cli
    surfaces = fixtures["SourceFreshness"].surfaces

    def enable(self):
        policy = self.root / "policy/access.json"
        document = json.loads(policy.read_text())
        document["source_review"] = {"version": 1, "ledger_id": LEDGER_ID}
        policy.write_text(json.dumps(document))
        (self.root / "memory/.memory.lock").touch()
        self.ledger = self.root / "memory/source-review/ledger.jsonl"
        self.ledger.parent.mkdir(parents=True)
        self.rows = []
        self.append("initialize")

    def append(self, action, record_id=None):
        record_revision = HASH if record_id else None
        binding_revision = HASH if record_id else None
        policy_revision = HASH if record_id else None
        if record_id is not None:
            entry = next((item for item in engine.page_catalog(self.root) if item.metadata.memory_id == record_id), None)
            if entry is not None:
                record_revision = "sha256:" + hashlib.sha256(entry.path.read_bytes()).hexdigest()
                binding = (entry.metadata.frontmatter or {}).get("mind2one", {}).get("review")
                if binding is not None:
                    binding_revision = engine.SOURCE_REVIEW["binding_revision"](binding)
                    policy_revision = binding["policy-revision"]
        row = {"schema_version": "llm-wiki-source-review/v1", "ledger_id": LEDGER_ID,
               "sequence": len(self.rows), "previous": self.rows[-1]["entry_revision"] if self.rows else None,
               "action": action, "record_id": record_id,
               "record_revision": record_revision, "binding_revision": binding_revision,
               "policy_revision": policy_revision, "actor_id": "synthetic-human", "at": AT,
               "request_id": "synthetic-hold-" + str(len(self.rows)), "request_revision": HASH,
               "reason": {"initialize": "initialize", "hold": "manual-concern", "release": "human-reverified"}[action]}
        row["entry_revision"] = engine.SOURCE_REVIEW["entry_revision"](row)
        self.rows.append(row)
        self.ledger.write_text("".join(json.dumps(item, separators=(",", ":")) + "\n" for item in self.rows))

    def review_again(self, path=PATH, sources=None):
        target = self.root / path
        metadata = engine.page_metadata(target, self.root / Path(path).parts[0])
        frontmatter = copy.deepcopy(metadata.frontmatter)
        body = target.read_text().split("\n---\n", 1)[1]
        product = frontmatter["mind2one"]
        previous = copy.deepcopy(product["review"])
        product["review-history"].append(previous)
        if sources is not None: product["sources"] = sources
        product["review"] = {**previous, "request-id": "synthetic-new-review-" + str(len(product["review-history"])),
            "previous-revision": previous["content-revision"],
            "content-revision": engine.compute_content_revision(frontmatter, body),
            "policy-revision": engine.compute_policy_revision(frontmatter)}
        target.write_text("---\n" + "\n".join(json.dumps(key) + ": " + json.dumps(value) for key, value in frontmatter.items()) + "\n---\n" + body)

    def inventory(self):
        return {path.relative_to(self.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                if path.is_file() else "directory" for path in self.root.rglob("*")}

    def assert_no_ledger_disclosure(self, result):
        self.assertNotIn(LEDGER_ID, result.stdout + result.stderr)
        self.assertNotIn("manual-concern", result.stdout + result.stderr)
        self.assertNotIn("synthetic-human", result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_disabled_feature_preserves_legacy_exports_without_creating_files(self):
        self.derived()
        # An unactivated directory is not a hidden switch and is never repaired
        # by a query. No shared lock is required for an old vault.
        stray = self.root / "memory/source-review/ledger.jsonl"
        stray.parent.mkdir(); stray.write_bytes(b"unactivated invalid material\n")
        before = self.inventory()
        self.surfaces(True)
        self.assertEqual(self.inventory(), before)

    def test_initialized_empty_holds_preserve_exports_and_every_read_is_side_effect_free(self):
        self.derived(); self.enable()
        before = self.inventory()
        self.surfaces(True)
        self.assertEqual(self.inventory(), before)

    def test_hold_withholds_all_exports_in_both_profiles_without_disclosing_ledger(self):
        self.derived(); self.enable(); self.append("hold", DERIVED_ID)
        before = self.inventory()
        self.surfaces(False)
        for profile in ("codex", "opencode"):
            for args in (("--query", MARKER, "--json"), ("--query", MARKER, "--ranking", "legacy", "--json"),
                         ("--read-page", PATH), ("--session-start", "--session-start-scope", "critical")):
                result = self.cli(*args, profile=profile)
                self.assert_no_ledger_disclosure(result)
                self.assertNotIn(DERIVED_ID, result.stdout + result.stderr)
        self.assertEqual(self.inventory(), before)

    def test_held_record_never_enters_body_reader_or_dependency_nodes(self):
        self.derived(); self.enable(); self.append("hold", DERIVED_ID)
        access = engine.access_context(self.root, "codex")
        original = engine.authorized_page_snapshot
        def spy(path, *args, **kwargs):
            self.assertNotEqual(path, self.root / PATH, "Held record body reader was invoked")
            return original(path, *args, **kwargs)
        with mock.patch.object(engine, "authorized_page_snapshot", side_effect=spy):
            self.assertIsNone(engine.read_page(self.root, PATH, access, None))
        # A held source cannot remain in the graph merely because its bytes and
        # P2 binding still match its descendant's pinned reference.
        self.rows = self.rows[:1]; self.append("hold", SOURCE_ID)
        original = engine.authorized_page_snapshot
        def source_spy(path, *args, **kwargs):
            self.assertNotEqual(path, self.source, "Held dependency body reader was invoked")
            return original(path, *args, **kwargs)
        with mock.patch.object(engine, "authorized_page_snapshot", side_effect=source_spy):
            self.assertIsNone(engine.read_page(self.root, PATH, access, None))

    def test_source_byte_restore_rename_and_record_reapproval_do_not_release_hold(self):
        page = self.derived(); self.enable(); self.append("hold", DERIVED_ID)
        original_source = self.source.read_bytes()
        self.source.write_bytes(original_source + b"Synthetic change.\n")
        self.source.write_bytes(original_source)
        for references in ([], [self.ref(relation="independent-judgment")], [self.ref(relation="historical-citation")]):
            self.derived(references)
            self.surfaces(False, ("codex",))
        page.rename(self.root / "notes/renamed-derived.md")
        result = self.cli("--read-page", "notes/renamed-derived.md")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_profile_source_denial_never_creates_a_global_hold(self):
        self.write("notes/source.md", SOURCE_ID, "Synthetic primary evidence.\n", target_profiles=["codex"])
        self.derived(); self.enable()
        before = self.inventory()
        self.surfaces(False, ("opencode",))
        self.surfaces(True, ("codex",))
        self.assertEqual(self.inventory(), before)

    def test_resolved_ledger_is_not_an_allow_override_for_source_acl_or_p2(self):
        page = self.derived(); self.enable(); self.append("hold", DERIVED_ID)
        self.surfaces(False, ("codex",))
        # This reader fixture represents already-authorized ledger commands.
        # Authority/new-review creation are covered by the independent CLI suite.
        self.review_again(); self.append("release", DERIVED_ID)
        self.surfaces(True, ("codex",))
        source_bytes = self.source.read_bytes()
        self.source.write_bytes(source_bytes + b"A changed source after release.\n")
        self.surfaces(False, ("codex",))
        self.source.write_bytes(source_bytes)
        page.write_bytes(page.read_bytes() + b"An unreviewed semantic edit.\n")
        self.surfaces(False, ("codex",))
        self.write("notes/source.md", SOURCE_ID, "Synthetic primary evidence.\n", target_profiles=["codex"])
        self.review_again(sources=[self.ref()])
        self.surfaces(True, ("codex",))
        self.surfaces(False, ("opencode",))

    def test_latest_release_prevents_old_approved_record_rollback_before_body_read(self):
        page = self.derived(); before_hold = page.read_bytes()
        self.enable(); self.append("hold", DERIVED_ID)
        self.review_again(); first_release = page.read_bytes(); self.append("release", DERIVED_ID)
        self.surfaces(True)
        self.review_again()
        self.surfaces(True)
        # A later hold/release raises the floor; the earlier release is no
        # longer sufficient even though it was once a valid accepted version.
        self.append("hold", DERIVED_ID); self.review_again(); self.append("release", DERIVED_ID)
        current = page.read_bytes()
        self.surfaces(True, ("codex",))
        for old in (before_hold, first_release):
            page.write_bytes(old)
            self.surfaces(False)
            access = engine.access_context(self.root, "codex")
            read_body = engine.authorized_page_snapshot
            def spy(path, *args, **kwargs):
                self.assertNotEqual(path, page, "A retired released lineage reached the body reader")
                return read_body(path, *args, **kwargs)
            with mock.patch.object(engine, "authorized_page_snapshot", side_effect=spy):
                self.assertIsNone(engine.read_page(self.root, PATH, access, None))
        page.write_bytes(current)
        self.surfaces(True, ("codex",))

    def test_rolled_back_release_target_is_removed_from_dependency_graph(self):
        page = self.derived(); old = page.read_bytes()
        self.enable(); self.append("hold", DERIVED_ID)
        self.review_again(); self.append("release", DERIVED_ID)
        page.write_bytes(old)
        downstream = "notes/after-release-dependent.md"
        marker = "syntheticreleasefloorchildmarker"
        self.write(downstream, "derived:release-child", marker + "\n", ai=True,
                   sources=[self.ref(page, DERIVED_ID, locator=PATH)])
        # Its byte pin matches the restored old file; only the durable review
        # lineage gate can stop this otherwise-current dependency.
        for profile in ("codex", "opencode"):
            result = self.cli("--read-page", downstream, profile=profile)
            self.assertNotEqual(result.returncode, 0); self.assertEqual(result.stdout, "")
            self.assertNotIn(marker, self.cli("--query", marker, "--json", profile=profile).stdout)

    def test_release_floor_cannot_be_replaced_by_generic_human_trust_or_unlinked_history(self):
        page = self.derived(); self.enable(); self.append("hold", DERIVED_ID)
        self.review_again(); self.append("release", DERIVED_ID)
        current = page.read_bytes()
        human = current.decode().replace('"origin": "ai"', '"origin": "human"') \
            .replace('"review-state": "accepted"', '"review-state": "not-required"')
        page.write_text(human + "Unreviewed human-labeled body.\n")
        self.surfaces(False)
        metadata = engine.page_metadata(page, self.root / "wiki")
        self.assertEqual(engine.assess_memory_review(metadata.frontmatter)["state"], "human-authored")
        read_body = engine.authorized_page_snapshot
        def spy(path, *args, **kwargs):
            self.assertNotEqual(path, page, "An unreviewed release floor reached the body reader")
            return read_body(path, *args, **kwargs)
        with mock.patch.object(engine, "authorized_page_snapshot", side_effect=spy):
            self.assertIsNone(engine.read_page(self.root, PATH, engine.access_context(self.root, "codex"), None))
        page.write_bytes(current)
        fields = copy.deepcopy(engine.page_metadata(page, self.root / "wiki").frontmatter)
        fields["mind2one"]["review-history"].append(copy.deepcopy(fields["mind2one"]["review"]))
        fields["mind2one"]["review"]["previous-revision"] = None
        self.assertFalse(engine._valid_review_history(fields["mind2one"]))
        body = current.decode().split("\n---\n", 1)[1]
        page.write_text("---\n" + "\n".join(json.dumps(key) + ": " + json.dumps(value) for key, value in fields.items()) + "\n---\n" + body)
        self.surfaces(False)

    def test_missing_corrupt_oversized_or_foreign_ledger_fails_closed_for_all_markdown(self):
        self.derived(); self.enable()
        saved = self.ledger.read_bytes()
        foreign = dict(self.rows[0], ledger_id="f" * 32)
        foreign["entry_revision"] = engine.SOURCE_REVIEW["entry_revision"](foreign)
        for replacement in (None, b"", b"{bad json}\n", saved.rstrip(b"\n"),
                            (json.dumps(foreign) + "\n").encode(), b"x" * (5 * 1024 * 1024)):
            if replacement is None: self.ledger.unlink()
            else: self.ledger.write_bytes(replacement)
            self.surfaces(False, ("codex",))
            result = self.cli("--read-page", "notes/source.md")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assert_no_ledger_disclosure(result)
            self.ledger.write_bytes(saved)

    def test_invalid_marker_never_falls_back_to_disabled(self):
        self.derived()
        path = self.root / "policy/access.json"
        policy = json.loads(path.read_text())
        for marker in (None, {}, {"version": True, "ledger_id": LEDGER_ID},
                       {"version": 2, "ledger_id": LEDGER_ID}, {"version": 1, "ledger_id": "short"},
                       {"version": 1, "ledger_id": LEDGER_ID, "allow": True}):
            path.write_text(json.dumps({**policy, "source_review": marker}))
            result = self.cli("--query", MARKER, "--json")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assert_no_ledger_disclosure(result)

    def test_hold_between_chunks_and_after_warm_snapshot_denies_reuse(self):
        self.derived(); self.enable()
        first = json.loads(self.cli("--read-page", PATH, "--offset", "0", "--length", "50").stdout)
        access = engine.access_context(self.root, "codex")
        warm = engine.page_snapshots(self.root, access, None)
        self.append("hold", DERIVED_ID)
        with self.assertRaises(engine.PageSnapshotError): warm.verify()
        result = self.cli("--read-page", PATH, "--offset", "50", "--length", "100", "--revision", first["revision"])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_ledger_change_at_final_query_and_read_rendering_withholds_every_byte(self):
        self.derived(); self.enable()
        original = engine.render_json
        def changed_render(*args, **kwargs):
            result = original(*args, **kwargs)
            self.append("hold", DERIVED_ID)
            return result
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(engine, "root_path", return_value=self.root), mock.patch.object(engine, "render_json", side_effect=changed_render), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(engine.main(["--agent-profile", "codex", "--query", MARKER, "--json"]), 4)
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn(LEDGER_ID, stderr.getvalue())
        # A new clean initialization gives the direct reader the same final
        # rendering drift case, without relying on any cached outcome.
        self.rows = self.rows[:1]
        self.ledger.write_text(json.dumps(self.rows[0]) + "\n")
        access = engine.access_context(self.root, "codex")
        project = engine.projected_page_text
        def changed_projection(snapshot):
            result = project(snapshot); self.append("hold", DERIVED_ID); return result
        with mock.patch.object(engine, "projected_page_text", side_effect=changed_projection):
            self.assertIsNone(engine.read_page(self.root, PATH, access, None))

    def test_ledger_change_during_body_read_or_final_session_render_is_rejected(self):
        self.derived(); self.enable()
        access = engine.access_context(self.root, "codex")
        reader = engine.authorized_page_snapshot
        def changed_read(*args, **kwargs):
            result = reader(*args, **kwargs)
            if len(self.rows) == 1: self.append("hold", DERIVED_ID)
            return result
        with mock.patch.object(engine, "authorized_page_snapshot", side_effect=changed_read):
            with self.assertRaises(engine.PageSnapshotError): engine.page_snapshots(self.root, access, None)
        self.rows = self.rows[:1]
        self.ledger.write_text(json.dumps(self.rows[0]) + "\n")
        redact = engine.redact
        def changed_redact(text):
            result = redact(text)
            if MARKER in text and len(self.rows) == 1: self.append("hold", DERIVED_ID)
            return result
        with mock.patch.object(engine, "redact", side_effect=changed_redact):
            with self.assertRaises(engine.PageSnapshotError):
                engine.render_session_start(self.root, 2500, 5, access, "critical")

    def test_busy_ledger_writer_is_fail_closed_without_waiting_or_reading_body(self):
        self.derived(); self.enable()
        before = self.inventory()
        with engine.SOURCE_REVIEW["memory_lock"](self.root, write=True):
            result = self.cli("--query", MARKER, "--json")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assert_no_ledger_disclosure(result)
            with mock.patch.object(engine, "authorized_page_snapshot", side_effect=AssertionError("body must not be read")):
                self.assertIsNone(engine.read_page(self.root, PATH, engine.access_context(self.root, "codex"), None))
        self.assertEqual(self.inventory(), before)

    def test_real_mcp_fresh_processes_enforce_hold(self):
        self.derived(); self.enable(); self.append("hold", DERIVED_ID)
        for profile in ("codex", "opencode"):
            calls = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                     {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "search_pages", "arguments": {"query": MARKER}}},
                     {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "read_page", "arguments": {"path": PATH}}}]
            result = subprocess.run(["node", str(REPO / "bin/llm-wiki-mcp")], text=True, capture_output=True, timeout=15,
                input="".join(json.dumps(call) + "\n" for call in calls),
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LLM_WIKI_ROOT": str(self.root), "LLM_WIKI_AGENT_PROFILE": profile})
            self.assertEqual(result.returncode, 0, result.stderr)
            responses = {item["id"]: item for item in map(json.loads, result.stdout.splitlines())}
            self.assertIn(2, responses); self.assertIn(3, responses)
            self.assertNotIn(MARKER, result.stdout)
            self.assertNotIn(DERIVED_ID, result.stdout)
            self.assert_no_ledger_disclosure(result)


if __name__ == "__main__":
    unittest.main()
