#!/usr/bin/env python3
"""Synthetic v5 event-to-document dependencies; no installed memory is used."""
import copy
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
V = runpy.run_path(str(ROOT / "tests/test-event-versioned-review.py"), run_name="v5_fixtures")
C, E = V["C"], V["E"]
P = runpy.run_path(str(ROOT / "tests/test-source-freshness.py"), run_name="v5_page_fixtures")
H = runpy.run_path(str(ROOT / "tests/test-source-review-retrieval.py"), run_name="v5_hold_fixtures")
SCHEMA = "llm-wiki-memory-event/v5"


class EventSources(unittest.TestCase):
    setUp = V["VersionedEvents"].setUp
    write = V["VersionedEvents"].write
    cli = V["VersionedEvents"].cli
    bytes = V["VersionedEvents"].bytes
    write_page = P["SourceFreshness"].write
    enable_hold = H["SourceReviewRetrieval"].enable
    append = H["SourceReviewRetrieval"].append

    def source(self, identity="source:synthetic", path="notes/source.md", **extra):
        page = self.write_page(path, identity, "Synthetic source only.\n", **extra)
        return {"kind": "document", "id": identity, "revision": "sha256:" + hashlib.sha256(page.read_bytes()).hexdigest(),
                "relation": "current-derived"}

    def bound(self, refs=None):
        for path in (self.root / "memory/events").glob("*.jsonl"):
            path.unlink()
        a = V["pending"](); self.write([a])
        return self.cli(V["request"](a, "correct", source_bindings=refs if refs is not None else [self.source()]))

    def recall(self, profile="codex"):
        result = subprocess.run([sys.executable, str(ROOT / "bin/llm-wiki-enrich"), "--agent-profile", profile,
            "--query", "vfourmarker", "--json"], env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("source:synthetic", result.stdout + result.stderr)
        return json.loads(result.stdout)

    def context(self, event, profile="codex"):
        value = {"schemaVersion": 1, "selections": [{"kind": "event", "memoryId": event["record_id"],
            "revision": C["record_revision"](event)}]}
        api = runpy.run_path(str(ROOT / "bin/llm-wiki-context"), run_name="v5_context")
        return api["prepare"](self.root, profile, value)["items"][0]

    def catalog(self, version=2, success=True):
        result = subprocess.run([sys.executable, str(ROOT / "bin/llm-wiki-event-catalog"), "--root", str(self.root),
            "--profile", "human", "--purpose", "local-human-review", "--schema-version", str(version)],
            env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0 if success else 2, result.stderr)
        self.assertNotIn("source:synthetic", result.stdout + result.stderr)
        return json.loads(result.stdout) if success else result

    def test_source_only_correction_binds_then_edit_blocks_two_profiles_without_ancestor(self):
        b = self.bound()
        self.assertEqual(b["schema_version"], SCHEMA)
        for profile in ("codex", "opencode"):
            recalled = self.recall(profile)
            self.assertEqual(len(recalled), 1)
            self.assertIn(b["id"], recalled[0]["title"])
            self.assertIn(b["summary"], recalled[0]["text"])
            self.assertEqual(self.context(b, profile)["status"], "ready")
        self.assertEqual(self.catalog()["entries"][0]["sourceFreshness"], {"state": "current", "reuseAllowed": True})
        source = self.root / "notes/source.md"; source.write_bytes(source.read_bytes() + b"Changed.\n")
        for profile in ("codex", "opencode"):
            self.assertEqual(self.recall(profile), [])
            self.assertEqual(self.context(b, profile)["status"], "unavailable")
        self.assertEqual(self.catalog()["entries"][0]["sourceFreshness"], {"state": "stale", "reuseAllowed": False})
        self.catalog(1, False)

    def test_patch_preserves_unselected_edges_and_only_human_rebinding_restores(self):
        first = self.source(); second = self.source("source:second", "notes/second.md")
        b = self.bound([first, second])
        first = self.source(review_state="approved", project="changed-one")
        second = self.source("source:second", "notes/second.md", project="changed-two")
        request = V["request"](b, "correct", source_bindings=[first], confirm_supersedes=True)
        request["request_id"] = "test:first-correction"
        c = self.cli(request)
        self.assertEqual(c["sources"][1], b["sources"][1]); self.assertEqual(self.recall(), [])
        request = V["request"](c, "correct", source_bindings=[second], confirm_supersedes=True)
        request["request_id"] = "test:second-correction"
        d = self.cli(request)
        self.assertEqual(self.context(d)["status"], "ready")

    def test_historical_and_independent_are_explicit_unassessed_and_not_followed(self):
        for relation in ("historical-citation", "independent-judgment"):
            ref = self.source(); ref["relation"] = relation
            b = self.bound([ref]); (self.root / "notes/source.md").unlink()
            self.assertEqual(self.context(b)["status"], "ready")
            self.assertEqual(self.catalog()["entries"][0]["sourceFreshness"], {"state": "unassessed", "reuseAllowed": True})

    def test_downgrade_duplicate_bad_revision_and_old_unknown_sources_cannot_upgrade(self):
        b = self.bound(); before = self.bytes()
        for refs in ([{**b["sources"][0], "relation": "historical-citation"}], b["sources"] * 2, [],
                     [{**b["sources"][0], "revision": "sha256:" + "0" * 64}],
                     [{**b["sources"][0], "locator": "notes/source.md"}]):
            request = V["request"](b, "correct", source_bindings=refs, confirm_supersedes=True)
            request["request_id"] = "test:invalid"
            self.cli(request, False); self.assertEqual(self.bytes(), before)
        a = V["pending"](); a["sources"] = []
        self.write([a]); before = self.bytes()
        self.cli(V["request"](a, "correct", source_bindings=[self.source()]), False)
        self.assertEqual(self.bytes(), before)

    def test_rejected_untrusted_and_human_unauthorized_sources_cannot_bind(self):
        a = V["pending"](); self.write([a])
        for fields in ({"review_state": "rejected"}, {"ai": True, "review_state": "pending"}):
            ref = self.source(**fields); before = self.bytes()
            self.cli(V["request"](a, "correct", source_bindings=[ref]), False)
            self.assertEqual(self.bytes(), before)
        ref = self.source()
        policy_path = self.root / "policy/access.json"; policy = json.loads(policy_path.read_text())
        policy["profiles"]["human"]["domains"] = ["personal"]; policy_path.write_text(json.dumps(policy))
        self.cli(V["request"](a, "correct", source_bindings=[ref]), False)

    def test_each_assistant_checks_own_acl_even_when_human_can_bind(self):
        b = self.bound([self.source(target_profiles=["codex"])])
        self.assertEqual(self.context(b, "codex")["status"], "ready")
        self.assertEqual(self.context(b, "opencode")["status"], "unavailable")
        self.assertEqual(self.catalog()["entries"][0]["sourceFreshness"]["state"], "current")

    def test_policy_only_source_revocation_preserves_other_assistant_and_human_state(self):
        policy_path = self.root / "policy/access.json"; policy = json.loads(policy_path.read_text())
        for profile in ("codex", "opencode"):
            policy["profiles"][profile]["domains"] = ["work", "personal"]
        policy_path.write_text(json.dumps(policy))
        b = self.bound([self.source(domain="personal")])
        for profile in ("codex", "opencode"):
            self.assertEqual(self.context(b, profile)["status"], "ready")
        unchanged_events = self.bytes(); unchanged_source = (self.root / "notes/source.md").read_bytes()
        policy["profiles"]["opencode"]["domains"] = ["work"]; policy_path.write_text(json.dumps(policy))
        self.assertEqual(self.context(b, "codex")["status"], "ready")
        self.assertEqual(self.context(b, "opencode")["status"], "unavailable")
        self.assertEqual(self.catalog()["entries"][0]["sourceFreshness"], {"state": "current", "reuseAllowed": True})
        self.assertEqual(self.bytes(), unchanged_events)
        self.assertEqual((self.root / "notes/source.md").read_bytes(), unchanged_source)

    def test_hold_blocks_both_profiles_and_human_rebinding_without_reading_held_body(self):
        b = self.bound(); self.enable_hold(); self.append("hold", "source:synthetic")
        for profile in ("codex", "opencode"):
            self.assertEqual(self.recall(profile), [])
            self.assertEqual(self.context(b, profile)["status"], "unavailable")
        self.assertEqual(self.catalog()["entries"][0]["sourceFreshness"]["state"], "unavailable")
        before = self.bytes()
        request = V["request"](b, "correct", source_bindings=b["sources"], confirm_supersedes=True)
        request["request_id"] = "test:held-rebind"
        self.cli(request, False); self.assertEqual(self.bytes(), before)
        access = E["access_context"](self.root, "codex")
        reader = E["authorized_page_snapshot"]
        def spy(path, *args, **kwargs):
            self.assertNotEqual(path, self.root / "notes/source.md")
            return reader(path, *args, **kwargs)
        with patch.dict(E["load_events"].__globals__, authorized_page_snapshot=spy):
            self.assertEqual(E["load_events"](self.root, access), [])

    def test_enabled_source_review_without_hold_allows_binding_under_event_writer_lock(self):
        self.enable_hold()
        b = self.bound()
        self.assertEqual(self.context(b)["status"], "ready")
        before = self.bytes()
        self.append("hold", "source:synthetic")
        request = V["request"](b, "correct", source_bindings=b["sources"], confirm_supersedes=True)
        request["request_id"] = "test:locked-held-rebind"
        self.cli(request, False); self.assertEqual(self.bytes(), before)

    def test_replaced_source_review_lock_is_rejected_before_event_append(self):
        self.enable_hold(); a = V["pending"](); self.write([a]); ref = self.source(); before = self.bytes()
        api = runpy.run_path(str(ROOT / "bin/llm-wiki-event-review"), run_name="v5_lock_replacement")
        validate = api["WRITER"]["validate_supersedes"]
        def replace_lock(*args, **kwargs):
            result = validate(*args, **kwargs)
            replacement = self.root / "memory/replacement-lock"; replacement.touch()
            replacement.replace(self.root / "memory/.memory.lock")
            return result
        request = {**V["request"](a, "correct", source_bindings=[ref]), "capability": V["CAPABILITY"]}
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps(request).encode()))
        with patch.dict(os.environ, self.env, clear=True), patch.object(sys, "stdin", stdin), patch.dict(api["WRITER"], validate_supersedes=replace_lock):
            with self.assertRaises(ValueError): api["main"]([])
        self.assertEqual(self.bytes(), before)

    def test_cross_project_transitive_dependency_is_not_filtered_by_event_project(self):
        ref = self.source(project="other-project")
        intermediate = self.write_page("notes/intermediate.md", "source:intermediate", "Synthetic derivation.\n",
                                       ai=True, sources=[ref], project="third-project")
        intermediate_ref = {**ref, "id": "source:intermediate",
                            "revision": "sha256:" + hashlib.sha256(intermediate.read_bytes()).hexdigest()}
        b = self.bound([intermediate_ref]); self.assertEqual(self.context(b)["status"], "ready")
        path = self.root / "notes/source.md"; path.write_bytes(path.read_bytes() + b"Changed ancestor.\n")
        self.assertEqual(self.context(b)["status"], "unavailable")
        self.assertEqual(self.catalog()["entries"][0]["sourceFreshness"]["state"], "stale")

    def test_deletion_withdrawal_and_policy_revocation_do_not_reveal_source(self):
        b = self.bound(); path = self.root / "notes/source.md"; original = path.read_bytes()
        initial_catalog = self.catalog()
        path.unlink()
        self.assertEqual(self.context(b)["status"], "unavailable")
        self.assertNotEqual(self.catalog()["revision"], initial_catalog["revision"])
        self.source(review_state="rejected")
        self.assertEqual(self.context(b)["status"], "unavailable")
        path.write_bytes(original)
        policy_path = self.root / "policy/access.json"; policy = json.loads(policy_path.read_text())
        for name in ("codex", "opencode"):
            policy["profiles"][name]["max_sensitivity"] = "public"
        policy_path.write_text(json.dumps(policy))
        for profile in ("codex", "opencode"):
            self.assertEqual(self.context(b, profile)["status"], "unavailable")

    def test_noncorrect_inherits_sources_and_withdrawal_never_resurrects_ancestor(self):
        b = self.bound(); withdrawn = self.cli(V["request"](b, "withdraw"))
        self.assertEqual(withdrawn["sources"], b["sources"])
        restored = self.cli(V["request"](withdrawn, "restore"))
        self.assertEqual(restored["sources"], b["sources"])
        path = self.root / "notes/source.md"; path.write_bytes(path.read_bytes() + b"Later change.\n")
        self.assertEqual(self.recall(), [])
        denied = V["request"](restored, "withdraw", source_bindings=b["sources"])
        denied["request_id"] = "test:invalid-withdraw"
        self.cli(denied, False)

    def test_tampering_sources_or_removing_edges_fails_the_entire_snapshot(self):
        b = self.bound(); rows = [C["clean"](e) for e in E["_read_event_snapshot"](self.root)]
        changed = copy.deepcopy(b); changed["sources"][0]["revision"] = "sha256:" + "0" * 64
        self.write([rows[0], changed], "tampered.jsonl")
        with self.assertRaises(E["EventSnapshotError"]): E["_read_event_snapshot"](self.root)
        (self.root / "memory/events/tampered.jsonl").unlink()
        request = V["request"](b, "correct", summary="vfourmarker replaced", confirm_supersedes=True)
        request["request_id"] = "test:child"
        child = C["prepare"](b, request, actor_id="synthetic-human", can_review=True,
            now="2026-10-14T06:00:00.000Z", event_id="0000000000000009")
        child["sources"] = [{**b["sources"][0], "id": "source:replacement"}]
        child["review"]["content_revision"] = C["content_revision"](child)
        with self.assertRaises(ValueError): C["validate_links"]([*rows, child])
        child.update(schema_version=C["SCHEMA"], source="human-review/v4"); child.pop("sources")
        child["review"]["content_revision"] = C["content_revision"](child)
        with self.assertRaises(ValueError): C["validate_links"]([*rows, child])

    def test_catalog_v1_only_rejects_visible_v5_and_freshness_does_not_change_record_revision(self):
        b = self.bound(); access = E["access_context"](self.root, "codex")
        rows = E["load_events"](self.root, access)
        self.assertEqual(C["record_revision"](rows[0]), C["record_revision"](b))
        self.catalog(1, False)
        policy_path = self.root / "policy/access.json"; policy = json.loads(policy_path.read_text())
        policy["profiles"]["human"]["domains"] = ["personal"]; policy_path.write_text(json.dumps(policy))
        self.assertEqual(self.catalog(1)["entries"], [])

    def test_source_mutation_after_read_fails_enrich_context_and_catalog_without_partial_output(self):
        b = self.bound(); source = self.root / "notes/source.md"; original = source.read_bytes()
        g = E["main"].__globals__; render = g["render_json"]
        def changed_render(*args, **kwargs):
            result = render(*args, **kwargs); source.write_bytes(original + b"Concurrent change.\n"); return result
        output = io.StringIO()
        with patch.dict(os.environ, self.env, clear=True), patch.dict(g, render_json=changed_render), contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(E["main"](["--agent-profile", "codex", "--query", "vfourmarker", "--json"]), 4)
        self.assertEqual(output.getvalue(), "")
        source.write_bytes(original)
        api = runpy.run_path(str(ROOT / "bin/llm-wiki-context"), run_name="v5_race_context")
        original_load = api["ENGINE"]["load_events"]
        def changed_load(*args, **kwargs):
            result = original_load(*args, **kwargs); source.write_bytes(original + b"Concurrent change.\n"); return result
        selection = {"kind": "event", "memoryId": b["record_id"], "revision": C["record_revision"](b)}
        with patch.dict(api["ENGINE"], load_events=changed_load), self.assertRaises(api["ENGINE"]["PageSnapshotError"]):
            api["prepare"](self.root, "codex", {"schemaVersion": 1, "selections": [selection]})
        source.write_bytes(original)
        api = runpy.run_path(str(ROOT / "bin/llm-wiki-event-catalog"), run_name="v5_race_catalog")
        entry = api["entry_for"]
        def changed_entry(*args, **kwargs):
            result = entry(*args, **kwargs); source.write_bytes(original + b"Concurrent change.\n"); return result
        with patch.dict(api["catalog"].__globals__, entry_for=changed_entry), self.assertRaises(api["ENGINE"]["PageSnapshotError"]):
            api["catalog"](self.root, schema_version=2)

    def test_review_source_race_is_rechecked_before_append(self):
        a = V["pending"](); self.write([a]); ref = self.source(); before = self.bytes()
        api = runpy.run_path(str(ROOT / "bin/llm-wiki-event-review"), run_name="v5_race_review")
        validate = api["WRITER"]["validate_supersedes"]
        def mutate(*args, **kwargs):
            result = validate(*args, **kwargs)
            path = self.root / "notes/source.md"; path.write_bytes(path.read_bytes() + b"Concurrent edit.\n")
            return result
        request = {**V["request"](a, "correct", source_bindings=[ref]), "capability": V["CAPABILITY"]}
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps(request).encode()))
        with patch.dict(os.environ, self.env, clear=True), patch.object(sys, "stdin", stdin), patch.dict(api["WRITER"], validate_supersedes=mutate):
            with self.assertRaises(api["ENGINE"]["PageSnapshotError"]): api["main"]([])
        self.assertEqual(self.bytes(), before)

    def test_hold_ledger_change_after_render_fails_before_output(self):
        self.bound(); self.enable_hold()
        g = E["main"].__globals__; render = g["render_json"]
        def hold_after_render(*args, **kwargs):
            result = render(*args, **kwargs); self.append("hold", "source:synthetic"); return result
        output = io.StringIO()
        with patch.dict(os.environ, self.env, clear=True), patch.dict(g, render_json=hold_after_render), contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(E["main"](["--agent-profile", "codex", "--query", "vfourmarker", "--json"]), 4)
        self.assertEqual(output.getvalue(), "")

    def test_previous_engine_fails_closed_on_v5(self):
        self.bound()
        previous = subprocess.run(["git", "show", "3ed49e8:bin/llm-wiki-enrich"], cwd=ROOT,
                                  capture_output=True, text=True, timeout=10)
        if previous.returncode: self.skipTest("previous reader history unavailable")
        module = types.ModuleType("pre_v5_engine"); module.__file__ = str(ROOT / "bin/llm-wiki-enrich")
        sys.modules[module.__name__] = module
        self.addCleanup(lambda: sys.modules.pop(module.__name__, None))
        exec(compile(previous.stdout, module.__file__, "exec"), module.__dict__)
        with self.assertRaises(module.EventSnapshotError) as error: module._read_event_snapshot(self.root)
        self.assertEqual(error.exception.code, "unknown-event-schema")

    def test_duplicate_identity_and_depth_reference_limits_remain_fail_closed(self):
        b = self.bound()
        self.source(path="notes/duplicate.md")
        api = runpy.run_path(str(ROOT / "bin/llm-wiki-context"), run_name="v5_duplicate_context")
        with self.assertRaises(api["ENGINE"]["PageSnapshotError"]):
            api["prepare"](self.root, "codex", {"schemaVersion": 1, "selections": [
                {"kind": "event", "memoryId": b["record_id"], "revision": C["record_revision"](b)}]})
        (self.root / "notes/duplicate.md").unlink()
        ref = self.source()
        with self.assertRaises(ValueError):
            C["source_bindings"]([{**ref, "id": "source:" + str(i)} for i in range(257)])
        # 32 Markdown edges are within its bound; the extra event edge makes
        # 33 and must stay incomplete, rather than resetting depth at the join.
        for i in range(1, 33):
            path = self.write_page(f"notes/chain-{i}.md", f"source:chain-{i}", "Synthetic intermediate.\n", sources=[ref])
            ref = {**ref, "id": f"source:chain-{i}", "revision": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}
        b = self.bound([ref])
        self.assertEqual(self.context(b)["status"], "unavailable")
        self.assertEqual(self.catalog()["entries"][0]["sourceFreshness"], {"state": "incomplete", "reuseAllowed": False})
        # Direct pure-graph probe covers a matching-revision cycle without
        # pretending a self-referential raw SHA-256 file can be constructed.
        revision = "sha256:" + "a" * 64
        nodes = [{"id": "source:a", "revision": revision, "sources": [{**ref, "id": "source:b", "revision": revision}]},
                 {"id": "source:b", "revision": revision, "sources": [{**ref, "id": "source:a", "revision": revision}]}]
        self.assertEqual(E["SOURCE_FRESHNESS"]["assess"]("source:a", nodes), {"state": "incomplete", "reuseAllowed": False})

    def test_mcp_writer_cannot_inject_bindings_or_review_and_both_profiles_observe_invalidation(self):
        ref = self.source()
        def mcp(profile, name, arguments):
            calls = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                     {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": arguments}}]
            env = {key: value for key, value in self.env.items() if not key.startswith("LLM_WIKI_EVENT_REVIEW_")}
            env.update(LLM_WIKI_AGENT_PROFILE=profile, LLM_WIKI_TARGET_AGENTS="codex,opencode")
            result = subprocess.run(["node", str(ROOT / "bin/llm-wiki-mcp")], input="".join(json.dumps(call) + "\n" for call in calls),
                env=env, text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            return next(json.loads(line) for line in result.stdout.splitlines() if json.loads(line).get("id") == 2)
        result = mcp("codex", "record_event", {"type": "decision", "summary": "vfourmarker synthetic MCP conclusion",
            "project": "snapshot-test", "source_bindings": [ref], "sources": [ref], "review_status": "approved",
            "schema_version": SCHEMA, "actor": {"type": "human", "id": "forged"}})
        self.assertIn("result", result)
        a = C["clean"](E["_read_event_snapshot"](self.root)[0])
        self.assertEqual(a["schema_version"], "llm-wiki-memory-event/v3")
        self.assertEqual(a["review_status"], "pending"); self.assertNotIn("sources", a)
        self.assertEqual(a["actor"], {"type": "ai", "id": "codex"})
        b = self.cli(V["request"](a, "correct", source_bindings=[ref]))
        for profile in ("codex", "opencode"):
            self.assertIn(b["id"], json.dumps(mcp(profile, "search_pages", {"query": "vfourmarker"})))
            self.assertIn("error", mcp(profile, "review_event", {}))
        (self.root / "notes/source.md").unlink()
        for profile in ("codex", "opencode"):
            response = json.dumps(mcp(profile, "search_pages", {"query": "vfourmarker"}))
            self.assertNotIn(b["id"], response); self.assertNotIn("source:synthetic", response)
            response = mcp(profile, "prepare_context", {"selections": [{"kind": "event", "memoryId": b["record_id"],
                "revision": C["record_revision"](b)}]})
            self.assertEqual(json.loads(response["result"]["content"][0]["text"])["items"][0]["status"], "unavailable")

    def test_source_bound_cross_language_vectors_and_old_v4_hashes(self):
        vectors = json.loads((ROOT / "tests/fixtures/source-bound-event-review.json").read_text())
        self.assertEqual(len(vectors["cases"]), 2)
        for case in vectors["cases"]:
            self.assertTrue(C["validate_binding"](case["event"]))
            for key in ("record_revision", "target_revision", "content_revision", "policy_revision"):
                self.assertEqual(C[key](case["event"]), case[key], case["name"])


if __name__ == "__main__": unittest.main(verbosity=2)
