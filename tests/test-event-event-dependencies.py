#!/usr/bin/env python3
"""Explicit v6 event dependencies. Synthetic vaults and independent human review."""
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
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
V = runpy.run_path(str(ROOT / "tests/test-event-versioned-review.py"), run_name="event_event_fixtures")
P = runpy.run_path(str(ROOT / "tests/test-source-freshness.py"), run_name="event_event_pages")
H = runpy.run_path(str(ROOT / "tests/test-source-review-retrieval.py"), run_name="event_event_holds")
E, C = V["E"], V["C"]
SCHEMA = "llm-wiki-memory-event/v6"
MARKER = "syntheticeventdependency"


class EventDependencies(unittest.TestCase):
    setUp = V["VersionedEvents"].setUp
    write = V["VersionedEvents"].write
    review = V["VersionedEvents"].cli
    event_bytes = V["VersionedEvents"].bytes
    write_page = P["SourceFreshness"].write
    enable_hold = H["SourceReviewRetrieval"].enable
    append = H["SourceReviewRetrieval"].append

    def propose(self, number, **extra):
        event = V["pending"]()
        event.update(id=f"{number:016x}", memory_id=f"event:{number:016x}", summary=f"{MARKER} claim {number}", **extra)
        # Synthetic proposals are append-only too; preserve reviewed bytes.
        with (self.root / "memory/events/2020-01.jsonl").open("a") as handle:
            handle.write(json.dumps(event) + "\n")
        return event

    def act(self, event, action="accept", success=True, **extra):
        request = V["request"](event, action, **extra)
        request["request_id"] = f"request:{event['id']}:{action}"
        if event.get("supersedes") and action in {"accept", "correct"}: request["confirm_supersedes"] = True
        return self.review(request, success)

    def ref(self, event, relation="current-derived"):
        return {"kind": "event", "id": event["record_id"], "revision": C["record_revision"](event), "relation": relation}

    def source(self, path="notes/source.md", identity="source:synthetic", **extra):
        page = self.write_page(path, identity, "Synthetic leaf evidence.\n", **extra)
        return {"kind": "document", "id": identity, "revision": "sha256:" + hashlib.sha256(page.read_bytes()).hexdigest(),
                "relation": "current-derived"}

    def bound(self):
        source = self.act(self.propose(1))
        derived = self.act(self.propose(10), "correct", source_bindings=[self.ref(source)])
        return source, derived

    def context(self, event, version=3, profile="codex"):
        api = runpy.run_path(str(ROOT / "bin/llm-wiki-context"), run_name="event_event_context")
        return api["prepare"](self.root, profile, {"schemaVersion": version, "selections": [
            {"kind": "event", "memoryId": event.get("record_id", event["memory_id"]),
             "revision": C["record_revision"](event)}]})["items"][0]

    def catalog(self, version=3, success=True):
        result = subprocess.run([sys.executable, str(ROOT / "bin/llm-wiki-event-catalog"), "--root", str(self.root),
            "--profile", "human", "--purpose", "local-human-review", "--schema-version", str(version)],
            env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0 if success else 2, result.stderr)
        if not success: self.assertEqual(result.stdout, "")
        return json.loads(result.stdout) if success else result

    def test_independent_review_binds_event_v6_and_old_context_shapes_remain(self):
        source, derived = self.bound()
        self.assertEqual(derived["schema_version"], SCHEMA)
        self.assertEqual(derived["source"], "human-review/v6")
        self.assertEqual(derived["sources"], [self.ref(source)])
        self.assertTrue(C["validate_binding"](derived))
        for profile in ("codex", "opencode"):
            for version in (1, 2, 3):
                item = self.context(derived, version, profile)
                self.assertEqual(item["status"], "ready")
                self.assertEqual("governance" in item, version == 3)
                self.assertNotIn("sources", item)

    def test_revision_change_withdraw_restore_requires_explicit_rebinding(self):
        source, derived = self.bound()
        current = self.act(source, "correct", summary="Changed source claim")
        for profile in ("codex", "opencode"):
            self.assertEqual(self.context(derived, profile=profile)["status"], "unavailable")
        rebound = self.act(derived, "correct", source_bindings=[self.ref(current)])
        self.assertEqual(self.context(rebound)["status"], "ready")
        withdrawn = self.act(current, "withdraw")
        self.assertEqual(self.context(rebound)["status"], "unavailable")
        restored = self.act(withdrawn, "restore")
        self.assertEqual(self.context(rebound)["status"], "unavailable")
        final = self.act(rebound, "correct", source_bindings=[self.ref(restored)])
        self.assertEqual(self.context(final)["status"], "ready")

    def test_catalog_v3_is_explicit_without_source_metadata_disclosure(self):
        source, derived = self.bound()
        catalog = self.catalog()
        entry = next(row for row in catalog["entries"] if row["objectId"] == derived["record_id"])
        self.assertEqual(entry["reviewBinding"], "v6")
        self.assertEqual(entry["sourceFreshness"], {"state": "current", "reuseAllowed": True})
        self.assertNotIn("sources", entry)
        self.assertNotIn("sourceCount", entry)
        for version in (1, 2): self.catalog(version, False)

    def test_self_and_transitive_event_dependencies_write_nothing(self):
        source, derived = self.bound()
        before = self.event_bytes()
        for target, ref in ((source, self.ref(source)), (source, self.ref(derived))):
            self.act(target, "correct", success=False, source_bindings=[ref])
            self.assertEqual(self.event_bytes(), before)

    def test_v5_document_only_upgrade_retains_unselected_edges(self):
        doc = self.source()
        source = self.act(self.propose(1))
        v5 = self.act(self.propose(10), "correct", source_bindings=[doc])
        self.assertEqual(v5["schema_version"], "llm-wiki-memory-event/v5")
        v6 = self.act(v5, "correct", source_bindings=[self.ref(source)])
        self.assertEqual(v6["sources"], [doc, self.ref(source)])
        (self.root / "notes/source.md").unlink()
        partial = self.act(v6, "correct", source_bindings=[self.ref(source)])
        self.assertEqual(partial["sources"], [doc, self.ref(source)])
        self.assertEqual(self.context(partial)["status"], "unavailable")

    def test_legacy_pending_opaque_wrong_kind_and_wrong_revision_cannot_bind(self):
        proposal = self.propose(1)
        other = self.propose(10)
        ref = {"kind": "event", "id": proposal["memory_id"], "revision": C["record_revision"](proposal), "relation": "current-derived"}
        self.act(other, "correct", False, source_bindings=[ref])
        source = self.act(proposal)
        for bad in ({**self.ref(source), "kind": "document"}, {**self.ref(source), "revision": "sha256:" + "0" * 64},
                    {**self.ref(source), "locator": "memory/events/2020-01.jsonl"}):
            before = self.event_bytes()
            self.act(other, "correct", False, source_bindings=[bad])
            self.assertEqual(self.event_bytes(), before)

    def test_historical_and_independent_event_references_do_not_claim_current(self):
        source = self.act(self.propose(1))
        outputs = [self.act(self.propose(number), "correct", source_bindings=[self.ref(source, relation)])
            for number, relation in ((10, "historical-citation"), (20, "independent-judgment"))]
        self.act(source, "withdraw")
        for event in outputs:
            self.assertEqual(self.context(event)["status"], "ready")
            entry = next(row for row in self.catalog()["entries"] if row["objectId"] == event["record_id"])
            self.assertEqual(entry["sourceFreshness"], {"state": "unassessed", "reuseAllowed": True})

    def test_fixed_vectors_preserve_v4_v5_hashes_and_bind_v6_sources(self):
        for name in ("versioned-event-review.json", "source-bound-event-review.json", "event-event-review.json"):
            vectors = json.loads((ROOT / "tests/fixtures" / name).read_text())
            for case in vectors["cases"]:
                if name != "versioned-event-review.json":
                    self.assertTrue(C["validate_binding"](case["event"]))
                for field in ("record_revision", "target_revision", "content_revision", "policy_revision"):
                    self.assertEqual(C[field](case["event"]), case[field], (name, case["name"]))
                if name == "event-event-review.json":
                    self.assertEqual(C["request_revision"](case["request"]), case["request_revision"])
                    forged = copy.deepcopy(case["event"]); forged["sources"][0]["revision"] = "sha256:" + "0" * 64
                    self.assertFalse(C["validate_binding"](forged))

    def test_v5_and_source_patches_remain_strict_and_append_only(self):
        source, derived = self.bound()
        ref = self.ref(source)
        for bad in ([ref, ref], [{**ref, "kind": "task"}], [{**ref, "relation": None}],
                    [{**ref, "id": "not-an-event-id"}], [{**ref, "locator": ""}],
                    [{**ref, "id": f"event:{i:016x}"} for i in range(257)]):
            with self.assertRaises((TypeError, ValueError)): C["source_bindings"](bad, typed=True)
        with self.assertRaises(ValueError): C["source_bindings"]([ref])
        before = self.event_bytes()
        for changes in ({"kind": "document"}, {"relation": "historical-citation"}, {"relation": "independent-judgment"}):
            self.act(derived, "correct", False, source_bindings=[{**ref, **changes}])
            self.assertEqual(self.event_bytes(), before)
        forged = copy.deepcopy(derived)
        forged.update(schema_version="llm-wiki-memory-event/v5", source="human-review/v5")
        forged["review"]["content_revision"] = C["content_revision"](forged)
        self.assertFalse(C["validate_binding"](forged))

    def test_v6_withdraw_restore_keeps_all_bindings_and_cannot_downgrade(self):
        source, derived = self.bound()
        withdrawn = self.act(derived, "withdraw")
        restored = self.act(withdrawn, "restore")
        for row in (withdrawn, restored):
            self.assertEqual(row["schema_version"], SCHEMA)
            self.assertEqual(row["sources"], derived["sources"])
        self.assertEqual(self.context(restored)["status"], "ready")
        self.act(source, "withdraw")
        self.assertEqual(self.context(restored)["status"], "unavailable")
        before = self.event_bytes()
        self.act(restored, "withdraw", False, source_bindings=[self.ref(source)])
        self.assertEqual(self.event_bytes(), before)

    def test_opaque_v4_source_and_opaque_legacy_target_are_never_adopted(self):
        source = self.act(self.propose(1, sources=[]))
        target = self.propose(10)
        self.act(target, "correct", False, source_bindings=[self.ref(source)])
        opaque = self.propose(20, sources=[])
        valid = self.act(target)
        self.act(opaque, "correct", False, source_bindings=[self.ref(valid)])

    def test_policy_only_source_acl_change_keeps_other_profile_and_human_current(self):
        policy_path = self.root / "policy/access.json"
        policy = json.loads(policy_path.read_text())
        for profile in ("codex", "opencode"): policy["profiles"][profile]["domains"] = ["work", "personal"]
        policy_path.write_text(json.dumps(policy))
        source = self.act(self.propose(1, domain="personal"))
        derived = self.act(self.propose(10), "correct", source_bindings=[self.ref(source)])
        before = self.event_bytes()
        policy["profiles"]["codex"]["domains"] = ["work"]; policy_path.write_text(json.dumps(policy))
        self.assertEqual(self.context(derived, profile="codex")["status"], "unavailable")
        self.assertEqual(self.context(derived, profile="opencode")["status"], "ready")
        entry = next(row for row in self.catalog()["entries"] if row["objectId"] == derived["record_id"])
        self.assertEqual(entry["sourceFreshness"]["state"], "current")
        self.assertEqual(self.event_bytes(), before)
        policy["profiles"]["human"]["domains"] = ["work"]; policy_path.write_text(json.dumps(policy))
        self.act(derived, "correct", False, source_bindings=[self.ref(source)])
        self.assertEqual(self.event_bytes(), before)

    def test_transitive_source_freshness_blocks_new_bindings_including_historical(self):
        doc = self.source()
        source = self.act(self.propose(1), "correct", source_bindings=[doc])
        derived = self.act(self.propose(10), "correct", source_bindings=[self.ref(source)])
        leaf = self.root / "notes/source.md"; leaf.write_text(leaf.read_text() + "Changed raw source.\n")
        before = self.event_bytes()
        for relation in ("current-derived", "historical-citation", "independent-judgment"):
            self.act(derived, "correct", False, source_bindings=[self.ref(source, relation)])
            self.assertEqual(self.event_bytes(), before)
        for profile in ("codex", "opencode"):
            self.assertEqual(self.context(derived, profile=profile)["status"], "unavailable")

    def test_ancestor_hold_invalidates_mixed_chain_without_changing_source_bytes(self):
        doc = self.source(ai=True)
        source = self.act(self.propose(1), "correct", source_bindings=[doc])
        derived = self.act(self.propose(10), "correct", source_bindings=[self.ref(source)])
        self.enable_hold()
        original = (self.root / "notes/source.md").read_bytes(); before = self.event_bytes()
        self.append("hold", "source:synthetic")
        for profile in ("codex", "opencode"):
            self.assertEqual(self.context(derived, profile=profile)["status"], "unavailable")
        self.act(derived, "correct", False, source_bindings=[self.ref(source)])
        self.assertEqual((self.root / "notes/source.md").read_bytes(), original)
        self.assertEqual(self.event_bytes(), before)

    def test_cross_type_cycle_is_rejected_before_append(self):
        source = self.act(self.propose(1))
        doc = self.source(sources=[self.ref(source)])
        derived = self.act(self.propose(10), "correct", source_bindings=[doc])
        before = self.event_bytes()
        self.act(source, "correct", False, source_bindings=[self.ref(derived)])
        self.assertEqual(self.event_bytes(), before)

    def test_partial_event_rebinding_keeps_other_stale_edge(self):
        first = self.act(self.propose(1)); second = self.act(self.propose(2))
        derived = self.act(self.propose(10), "correct", source_bindings=[self.ref(first), self.ref(second)])
        current1 = self.act(first, "correct", summary="First changed")
        current2 = self.act(second, "correct", summary="Second changed")
        partial = self.act(derived, "correct", source_bindings=[self.ref(current1)])
        self.assertEqual(partial["sources"], [self.ref(current1), self.ref(second)])
        self.assertEqual(self.context(partial)["status"], "unavailable")
        final = self.act(partial, "correct", source_bindings=[self.ref(current2)])
        self.assertEqual(self.context(final)["status"], "ready")

    def test_previous_reader_with_original_helper_rejects_entire_v6_snapshot(self):
        self.bound()
        with tempfile.TemporaryDirectory(prefix="pre-v6-reader-") as directory:
            old_root = Path(directory)
            for name in ("llm-wiki-enrich", "llm_wiki_event_review.py", "llm_wiki_source_freshness.py", "llm_wiki_source_review.py"):
                prior = subprocess.run(["git", "show", "41dc184:" + "bin/" + name], cwd=ROOT,
                                       capture_output=True, text=True, timeout=10)
                if prior.returncode: self.skipTest("previous reader history unavailable")
                (old_root / name).write_text(prior.stdout)
            module = types.ModuleType("pre_v6_reader"); module.__file__ = str(old_root / "llm-wiki-enrich")
            sys.modules[module.__name__] = module; self.addCleanup(lambda: sys.modules.pop(module.__name__, None))
            exec(compile((old_root / "llm-wiki-enrich").read_text(), module.__file__, "exec"), module.__dict__)
            with self.assertRaises(module.EventSnapshotError) as caught: module._read_event_snapshot(self.root)
            self.assertEqual(caught.exception.code, "unknown-event-schema")

    def test_no_auto_approval_or_source_injection_through_real_mcp(self):
        source = self.act(self.propose(1))
        def mcp(profile, name, arguments):
            calls = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                     {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": arguments}}]
            env = {k: v for k, v in self.env.items() if not k.startswith("LLM_WIKI_EVENT_REVIEW_")}
            env.update(LLM_WIKI_AGENT_PROFILE=profile, LLM_WIKI_TARGET_AGENTS="codex,opencode")
            result = subprocess.run(["node", str(ROOT / "bin/llm-wiki-mcp")],
                input="".join(json.dumps(call) + "\n" for call in calls), env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            return next(json.loads(line) for line in result.stdout.splitlines() if json.loads(line).get("id") == 2)
        mcp("codex", "record_event", {"type": "decision", "project": "snapshot-test", "summary": MARKER + " MCP proposal",
            "source_bindings": [self.ref(source)], "sources": [self.ref(source)], "schema_version": SCHEMA,
            "review_status": "approved", "actor": {"type": "human", "id": "forged"}})
        proposal = next(C["clean"](row) for row in E["_read_event_snapshot"](self.root) if row["summary"].endswith("MCP proposal"))
        self.assertEqual(proposal["schema_version"], "llm-wiki-memory-event/v3")
        self.assertEqual(proposal["review_status"], "pending"); self.assertNotIn("sources", proposal)
        derived = self.act(proposal, "correct", source_bindings=[self.ref(source)])
        selection = {"kind": "event", "memoryId": derived["record_id"], "revision": C["record_revision"](derived)}
        for profile in ("codex", "opencode"):
            response = mcp(profile, "prepare_context", {"schemaVersion": 3, "selections": [selection]})
            item = json.loads(response["result"]["content"][0]["text"])["items"][0]
            self.assertEqual(item["status"], "ready")
            self.assertEqual(item["governance"], {"domain": "work", "sensitivity": "internal"})
            self.assertIn(derived["id"], json.dumps(mcp(profile, "search_pages", {"query": MARKER})))
            self.assertIn("error", mcp(profile, "review_event", {}))
        self.act(source, "withdraw")
        for profile in ("codex", "opencode"):
            response = mcp(profile, "prepare_context", {"schemaVersion": 3, "selections": [selection]})
            self.assertEqual(json.loads(response["result"]["content"][0]["text"])["items"][0]["status"], "unavailable")
            response = json.dumps(mcp(profile, "search_pages", {"query": MARKER}))
            self.assertNotIn(derived["id"], response)
            self.assertNotIn("sources", response)

    def test_v6_source_is_eligible_for_pending_markdown_conclusions(self):
        source, derived = self.bound()
        self.write_page("notes/conclusion.md", "note:conclusion", "Synthetic final conclusion.\n", ai=True, sources=[self.ref(derived)])
        for allowed in (True, False):
            for profile in ("codex", "opencode"):
                result = subprocess.run([sys.executable, str(ROOT / "bin/llm-wiki-enrich"), "--agent-profile", profile,
                    "--read-page", "notes/conclusion.md"], env=self.env, capture_output=True, text=True, timeout=15)
                self.assertEqual("Synthetic final conclusion." in result.stdout, allowed)
            if allowed: self.act(source, "withdraw")

    def test_v6_total_graph_bounds_are_not_reset_at_event_edges(self):
        # Build deterministic signed synthetic rows using the pure review core;
        # runtime CLI tests above separately prove review authority enforcement.
        rows = []
        source = None
        for index in range(34):
            proposal = V["pending"](); proposal.update(id=f"{100 + index * 2:016x}", memory_id=f"event:{100 + index * 2:016x}")
            request = V["request"](proposal, "accept" if source is None else "correct",
                **({} if source is None else {"source_bindings": [self.ref(source)]}))
            request["request_id"] = f"depth:{index}"
            source = C["prepare"](proposal, request, actor_id="synthetic-human", can_review=True, now=V["NOW"], event_id=f"{101 + index * 2:016x}")
            rows.extend([proposal, source]); self.write(rows)
            if index in (32, 33):
                self.assertEqual(self.context(source)["status"], "ready" if index == 32 else "unavailable")
                if index == 32:
                    # The selected source itself is reusable. Recording a new
                    # edge preserves the human review fact, but never raises
                    # the read budget: the resulting depth 33 is withheld.
                    target = self.propose(1000)
                    bound = self.act(target, "correct", source_bindings=[self.ref(source)])
                    self.assertEqual(self.context(bound)["status"], "unavailable")
                    entry = next(row for row in self.catalog()["entries"] if row["objectId"] == bound["record_id"])
                    self.assertEqual(entry["sourceFreshness"]["state"], "incomplete")
                    # Reset this synthetic observation before the next point.
                    for path in (self.root / "memory/events").glob("*.jsonl"):
                        if path.name != "2020-01.jsonl": path.unlink()
        target = self.propose(1000); before = self.event_bytes()
        self.act(target, "correct", False, source_bindings=[self.ref(source)])
        self.assertEqual(self.event_bytes(), before)
        api = runpy.run_path(str(ROOT / "bin/llm-wiki-context"), run_name="event_event_limits")
        self.assertEqual((api["MAX_SELECTIONS"], api["MAX_ITEM_TEXT"], api["MAX_TEXT"]), (20, 12000, 40000))
        self.assertEqual(E["SOURCE_FRESHNESS"]["LIMITS"], {"depth": 32, "visited": 256, "references": 256, "snapshotNodes": 10000})

    def test_selected_source_union_cannot_increase_total_visited_budget(self):
        rows, leaves = [], []
        def reviewed(number, refs=None):
            proposal = V["pending"](); proposal.update(id=f"{number:016x}", memory_id=f"event:{number:016x}")
            request = V["request"](proposal, "accept" if refs is None else "correct",
                **({} if refs is None else {"source_bindings": refs}))
            request["request_id"] = f"union:{number}"
            result = C["prepare"](proposal, request, actor_id="synthetic-human", can_review=True,
                                  now=V["NOW"], event_id=f"{number + 1:016x}")
            rows.extend([proposal, result])
            return result
        for index in range(254): leaves.append(reviewed(100 + index * 2))
        first = reviewed(1000, [self.ref(leaf) for leaf in leaves[:127]])
        second = reviewed(1002, [self.ref(leaf) for leaf in leaves[127:]])
        self.write(rows)
        for source in (first, second): self.assertEqual(self.context(source)["status"], "ready")
        target = self.act(self.propose(2000), "correct", source_bindings=[self.ref(first), self.ref(second)])
        for profile in ("codex", "opencode"):
            self.assertEqual(self.context(target, profile=profile)["status"], "unavailable")
        entry = next(row for row in self.catalog()["entries"] if row["objectId"] == target["record_id"])
        self.assertEqual(entry["sourceFreshness"], {"state": "incomplete", "reuseAllowed": False})

    def test_old_catalog_checks_visible_v6_before_query_limit_but_not_hidden_v6(self):
        source = self.act(self.propose(1))
        derived = self.act(self.propose(10, domain="personal"), "correct", source_bindings=[self.ref(source)])
        api = runpy.run_path(str(ROOT / "bin/llm-wiki-event-catalog"), run_name="event_event_catalog_compat")
        for version in (1, 2):
            with self.assertRaises(ValueError): api["catalog"](self.root, query="no-such-query", schema_version=version)
        policy_path = self.root / "policy/access.json"; policy = json.loads(policy_path.read_text())
        policy["profiles"]["human"]["domains"] = ["work"]; policy_path.write_text(json.dumps(policy))
        for version in (1, 2):
            catalog = self.catalog(version)
            self.assertEqual([entry["objectId"] for entry in catalog["entries"]], [source["record_id"]])
            self.assertNotIn(derived["record_id"], json.dumps(catalog))

    def test_selected_event_change_before_append_rejects_without_output(self):
        source = self.act(self.propose(1)); target = self.propose(10)
        api = runpy.run_path(str(ROOT / "bin/llm-wiki-event-review"), run_name="event_event_before_append")
        original = api["source_selection"]
        def changed(*args):
            original(*args)
            with (self.root / "memory/events/2020-01.jsonl").open("a") as handle: handle.write("\n")
        request = {**V["request"](target, "correct", source_bindings=[self.ref(source)]), "capability": V["CAPABILITY"]}
        output = io.StringIO(); before_count = len(E["_read_event_snapshot"](self.root))
        with patch.dict(os.environ, self.env, clear=True), patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(request).encode()))), \
             patch.dict(api["main"].__globals__, source_selection=changed), contextlib.redirect_stdout(output):
            with self.assertRaises((ValueError, api["ENGINE"]["EventSnapshotError"])): api["main"]([])
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(len(E["_read_event_snapshot"](self.root)), before_count)

    def test_exact_own_append_rechecks_extra_bytes_and_changed_prefix(self):
        for mutation in ("other-file", "prefix", "extra-append"):
            with self.subTest(mutation=mutation):
                for path in (self.root / "memory/events").glob("*.jsonl"): path.unlink()
                source = self.act(self.propose(1)); target = self.propose(10)
                api = runpy.run_path(str(ROOT / "bin/llm-wiki-event-review"), run_name="event_event_after_append")
                original = api["ExpectedAppend"].written
                def changed(guard, info):
                    if mutation == "other-file":
                        with (self.root / "memory/events/2020-01.jsonl").open("ab") as handle: handle.write(b"\n")
                    elif mutation == "prefix":
                        guard.path.write_bytes(guard.path.read_bytes().replace(b"syntheticeventdependency", b"syntheticeventdependencz", 1))
                    else:
                        with guard.path.open("ab") as handle: handle.write(b"\n")
                    return original(guard, info)
                request = {**V["request"](target, "correct", source_bindings=[self.ref(source)]), "capability": V["CAPABILITY"]}
                output = io.StringIO()
                with patch.dict(os.environ, self.env, clear=True), patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(request).encode()))), \
                     patch.object(api["ExpectedAppend"], "written", changed), contextlib.redirect_stdout(output):
                    with self.assertRaises((ValueError, api["ENGINE"]["EventSnapshotError"])): api["main"]([])
                self.assertEqual(output.getvalue(), "")

    def test_final_serialization_fence_rejects_all_context_versions_and_profiles(self):
        source, derived = self.bound()
        for profile in ("codex", "opencode"):
            for version in (1, 2, 3):
                api = runpy.run_path(str(ROOT / "bin/llm-wiki-context"), run_name="event_event_emit")
                pack = api["prepare"](self.root, profile, {"schemaVersion": version, "selections": [
                    {"kind": "event", "memoryId": derived["record_id"], "revision": C["record_revision"](derived)}]})
                self.assertEqual(pack["items"][0]["status"], "ready")
                original = api["json"].dumps
                def changed(*args, **kwargs):
                    result = original(*args, **kwargs)
                    with (self.root / "memory/events/2020-01.jsonl").open("ab") as handle: handle.write(b"\n")
                    return result
                output = io.BytesIO()
                with patch.object(api["json"], "dumps", changed), patch.object(sys, "stdout", types.SimpleNamespace(buffer=output)):
                    with self.assertRaises((ValueError, api["ENGINE"]["EventSnapshotError"])): api["emit"](pack)
                self.assertEqual(output.getvalue(), b"")


if __name__ == "__main__": unittest.main(verbosity=2)
