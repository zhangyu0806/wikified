#!/usr/bin/env python3
"""Synthetic reviewed events as typed Markdown sources; no installed data."""
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
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
V = runpy.run_path(str(ROOT / "tests/test-event-versioned-review.py"), run_name="event_backed_fixtures")
P = runpy.run_path(str(ROOT / "tests/test-source-freshness.py"), run_name="event_backed_pages")
H = runpy.run_path(str(ROOT / "tests/test-source-review-retrieval.py"), run_name="event_backed_holds")
E, C = V["E"], V["C"]
MARKER = "syntheticeventbackedconclusion"
PAGE = "wiki/context/CRITICAL_FACTS.md"


class EventBackedConclusions(unittest.TestCase):
    setUp = V["VersionedEvents"].setUp
    write = V["VersionedEvents"].write
    review = V["VersionedEvents"].cli
    event_bytes = V["VersionedEvents"].bytes
    write_page = P["SourceFreshness"].write
    enable_hold = H["SourceReviewRetrieval"].enable
    append = H["SourceReviewRetrieval"].append

    def test_event_source_semantic_hash_vector_keeps_existing_algorithm(self):
        vector = json.loads((ROOT / "tests/fixtures/event-backed-markdown.json").read_text())
        self.assertEqual(E["compute_content_revision"](vector["frontmatter"], vector["body"]), vector["contentRevision"])
        self.assertEqual(E["compute_policy_revision"](vector["frontmatter"]), vector["policyRevision"])

    def event(self, *, sources=None, **extra):
        proposal = V["pending"](); proposal.update(extra)
        accepted = V["reviewed"](proposal, "accept" if sources is None else "correct",
                                 **({} if sources is None else {"source_bindings": sources}))
        self.write([proposal, accepted])
        return proposal, accepted

    def source(self, path="notes/source.md", identity="source:synthetic", **extra):
        page = self.write_page(path, identity, "Synthetic upstream evidence.\n", **extra)
        return {"kind": "document", "id": identity, "revision": "sha256:" + hashlib.sha256(page.read_bytes()).hexdigest(),
                "relation": "current-derived"}

    def ref(self, event):
        return {"kind": "event", "id": event["record_id"], "revision": C["record_revision"](event),
                "relation": "current-derived"}

    def conclusion(self, event, **extra):
        return self.write_page(PAGE, "conclusion:synthetic", MARKER + "\n", ai=True,
                               sources=[self.ref(event)], **extra)

    def context(self, event, version=3, profile="codex"):
        api = runpy.run_path(str(ROOT / "bin/llm-wiki-context"), run_name="event_backed_context")
        return api["prepare"](self.root, profile, {"schemaVersion": version, "selections": [
            {"kind": "event", "memoryId": event.get("record_id", event["memory_id"]),
             "revision": C["record_revision"](event)}]})["items"][0]

    def surfaces(self, allowed, profiles=("codex", "opencode")):
        for profile in profiles:
            for args in (("--query", MARKER, "--json"), ("--query", MARKER, "--ranking", "legacy", "--json"),
                         ("--read-page", PAGE), ("--read-page", PAGE, "--offset", "0", "--length", "32000"),
                         ("--session-start", "--session-start-scope", "critical")):
                result = subprocess.run([sys.executable, str(ROOT / "bin/llm-wiki-enrich"), "--agent-profile", profile, *args],
                                        env=self.env, text=True, capture_output=True, timeout=15)
                self.assertEqual(MARKER in result.stdout, allowed, (args, result.stdout, result.stderr))
                self.assertNotIn("Traceback", result.stderr)
                self.assertNotIn('"sources"', result.stdout)

    def test_v3_context_requires_independently_reviewed_event_and_keeps_legacy_wire(self):
        _, event = self.event()
        for profile in ("codex", "opencode"):
            item = self.context(event, profile=profile)
            self.assertEqual(item["status"], "ready")
            self.assertEqual(item["governance"], {"domain": "work", "sensitivity": "internal"})
            for version in (1, 2):
                legacy = self.context(event, version, profile)
                self.assertEqual({k: v for k, v in item.items() if k != "governance"}, legacy)
        proposal = V["pending"](); proposal.update(actor={"type": "human", "id": "synthetic-owner"},
            review_status="approved", epistemic_status="human-stated")
        self.write([proposal])
        self.assertEqual(self.context(proposal, 2)["status"], "ready")
        self.assertEqual(self.context(proposal, 3)["status"], "unavailable")

    def test_reviewed_event_exports_and_revision_or_withdrawal_blocks_all_surfaces(self):
        proposal, event = self.event(); self.conclusion(event); self.surfaces(True)
        child = V["reviewed"](event, "correct", 3, summary="Synthetic changed conclusion")
        self.write([proposal, event, child]); self.surfaces(False)
        self.conclusion(child); self.surfaces(True)
        withdrawal = V["reviewed"](child, "withdraw", 4)
        self.write([proposal, event, child, withdrawal]); self.surfaces(False)

    def test_upstream_document_and_policy_are_transitive_across_event(self):
        policy_path = self.root / "policy/access.json"; policy = json.loads(policy_path.read_text())
        for profile in ("codex", "opencode"): policy["profiles"][profile]["domains"] = ["work", "personal"]
        policy_path.write_text(json.dumps(policy))
        _, event = self.event(sources=[self.source(domain="personal")]); self.conclusion(event); self.surfaces(True)
        unchanged = self.event_bytes()
        policy["profiles"]["opencode"]["domains"] = ["work"]; policy_path.write_text(json.dumps(policy))
        self.surfaces(True, ("codex",)); self.surfaces(False, ("opencode",))
        self.assertEqual(self.event_bytes(), unchanged)
        source = self.root / "notes/source.md"; source.write_bytes(source.read_bytes() + b"Changed.\n")
        self.surfaces(False)

    def test_v4_opaque_sources_and_unreviewed_events_never_become_leaves(self):
        for sources in ([], [{"unexpected": "legacy opaque source"}]):
            proposal = V["pending"](); proposal["sources"] = sources
            event = V["reviewed"](proposal); self.write([proposal, event])
            self.conclusion(event)
            self.assertEqual(self.context(event, 2)["status"], "ready")
            self.assertEqual(self.context(event, 3)["status"], "unavailable")
            self.surfaces(False, ("codex",))

    def test_upstream_hold_and_event_conclusion_hold_are_enforced(self):
        _, event = self.event(sources=[self.source()]); self.conclusion(event)
        self.enable_hold(); self.surfaces(True)
        self.append("hold", "source:synthetic"); self.surfaces(False)

    def test_event_relation_is_eligible_for_explicit_hold_and_release(self):
        _, event = self.event(); self.conclusion(event); self.enable_hold()
        api = runpy.run_path(str(ROOT / "bin/llm-wiki-source-review"), run_name="event_backed_hold")
        access = api["ENGINE"]["access_context"](self.root, "human")
        record = api["record"](self.root, "conclusion:synthetic", access)
        self.assertTrue(api["current_relation"](record))
        self.append("hold", "conclusion:synthetic"); self.surfaces(False)

    def test_typed_graph_keeps_one_depth_and_visited_budget_and_rejects_kind_confusion(self):
        assess = E["SOURCE_FRESHNESS"]["assess"]
        revision = "sha256:" + "a" * 64
        def node(index, next_index=None):
            kind = "event" if index % 2 else "document"
            return {"kind": kind, "id": "node:" + str(index), "revision": revision,
                    "sources": [] if next_index is None else [{"kind": "event" if next_index % 2 else "document",
                        "id": "node:" + str(next_index), "revision": revision, "relation": "current-derived"}]}
        graph = [node(i, i + 1) for i in range(32)] + [node(32)]
        self.assertTrue(assess("node:0", graph)["reuseAllowed"])
        graph[-1] = node(32, 33); graph.append(node(33))
        self.assertEqual(assess("node:0", graph)["state"], "incomplete")
        self.assertEqual(assess("node:0", [node(0, 1), node(1, 0)])["state"], "incomplete")
        wrong = [node(0, 1), {**node(1), "kind": "document"}]
        self.assertEqual(assess("node:0", wrong)["state"], "unavailable")
        self.assertEqual(assess("node:0", [node(0), {**node(0), "kind": "event"}])["state"], "unavailable")
        for count, expected in ((255, "current"), (256, "incomplete"), (257, "incomplete")):
            leaves = [node(i) for i in range(1, count + 1)]
            root = node(0); root["sources"] = [{"kind": leaf["kind"], "id": leaf["id"], "revision": revision,
                "relation": "current-derived"} for leaf in leaves]
            self.assertEqual(assess("node:0", [root, *leaves])["state"], expected)
        self.assertEqual(assess("node:0", [node(i) for i in range(10001)])["state"], "incomplete")
        for locator in ("notes/source.md", "memory/events/synthetic.jsonl"):
            graph = [node(0, 1), node(1)]; graph[0]["sources"][0]["locator"] = locator
            self.assertEqual(assess("node:0", graph)["state"], "unavailable")

    def test_same_raw_revision_does_not_hide_cross_type_depth(self):
        ref = self.source()
        for i in range(30):
            ref = self.source(f"notes/chain-{i}.md", f"chain:{i}", sources=[ref])
        _, event = self.event(sources=[ref]); self.conclusion(event)
        self.surfaces(True, ("codex",))
        # A second Markdown edge makes the full document/event graph 33 deep.
        middle = self.write_page("notes/middle.md", "middle:synthetic", "Synthetic intermediate.\n", sources=[self.ref(event)])
        self.write_page(PAGE, "conclusion:synthetic", MARKER + "\n", ai=True, sources=[{
            "kind": "document", "id": "middle:synthetic", "revision": "sha256:" + hashlib.sha256(middle.read_bytes()).hexdigest(),
            "relation": "current-derived"}])
        self.surfaces(False, ("codex",))

    def test_writer_accepts_exact_own_append_but_rejects_target_dependency_before_write(self):
        proposal, event = self.event(); conclusion = self.conclusion(event)
        ref = {"kind": "document", "id": "conclusion:synthetic", "revision": "sha256:" + hashlib.sha256(conclusion.read_bytes()).hexdigest(),
               "relation": "current-derived"}
        before = self.event_bytes()
        self.review(V["request"](event, "correct", source_bindings=[ref], confirm_supersedes=True), False)
        self.assertEqual(self.event_bytes(), before)
        other = V["pending"](); other.update(id="0000000000000010", memory_id="event:0000000000000010")
        self.write([proposal, event, other])
        approved = self.review(V["request"](other, "correct", source_bindings=[ref]))
        self.assertEqual(self.context(approved)["status"], "ready")
        request = V["request"](approved, "correct", source_bindings=[ref], confirm_supersedes=True)
        request["request_id"] = "test:second-exact-append"
        again = self.review(request)
        self.assertEqual(self.context(again)["status"], "ready")

    def test_writer_rechecks_other_event_changes_and_exact_prior_prefix_after_append(self):
        for mutation in ("other-file", "prefix", "extra-append"):
            with self.subTest(mutation=mutation):
                for path in (self.root / "memory/events").glob("*.jsonl"): path.unlink()
                proposal, event = self.event(); conclusion = self.conclusion(event)
                other = V["pending"](); other.update(id="0000000000000010", memory_id="event:0000000000000010")
                self.write([proposal, event, other])
                ref = {"kind": "document", "id": "conclusion:synthetic", "revision": "sha256:" + hashlib.sha256(conclusion.read_bytes()).hexdigest(),
                       "relation": "current-derived"}
                api = runpy.run_path(str(ROOT / "bin/llm-wiki-event-review"), run_name="event_backed_write_race")
                written = api["ExpectedAppend"].written
                def mutate(guard, info):
                    if mutation == "other-file":
                        path = self.root / "memory/events/2020-01.jsonl"; path.write_bytes(path.read_bytes() + b"\n")
                    elif mutation == "prefix":
                        raw = guard.path.read_bytes(); guard.path.write_bytes(raw.replace(b"vfourmarker", b"vfourmarkez", 1))
                    else:
                        with guard.path.open("ab") as handle: handle.write(b"\n")
                    return written(guard, info)
                request = {**V["request"](other, "correct", source_bindings=[ref]), "capability": V["CAPABILITY"]}
                output = io.StringIO()
                with patch.dict(os.environ, self.env, clear=True), patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(request).encode()))), \
                     patch.object(api["ExpectedAppend"], "written", mutate), contextlib.redirect_stdout(output):
                    with self.assertRaises((ValueError, api["ENGINE"]["EventSnapshotError"])): api["main"]([])
                self.assertEqual(output.getvalue(), "")

    def test_event_mutation_after_render_rejects_every_export_without_prefix(self):
        for function, args in (("read_page", ["--read-page", PAGE]),
                               ("read_page", ["--read-page", PAGE, "--offset", "0", "--length", "1000"]),
                               ("render_session_start", ["--session-start", "--session-start-scope", "critical"]),
                               ("render_json", ["--query", MARKER, "--json"])):
            with self.subTest(function=function, args=args):
                proposal, event = self.event(); self.conclusion(event)
                globals_ = E["main"].__globals__; original = globals_[function]
                def changed(*a, **kw):
                    output = original(*a, **kw)
                    self.write([proposal, event, V["reviewed"](event, "withdraw", 3)])
                    return output
                output = io.StringIO()
                with patch.dict(os.environ, self.env, clear=True), patch.dict(globals_, **{function: changed}), \
                     contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                    self.assertNotEqual(E["main"](["--agent-profile", "codex", *args]), 0)
                self.assertEqual(output.getvalue(), "")

    def test_previous_reader_fails_closed_on_reviewed_event_source(self):
        _, event = self.event(); self.conclusion(event)
        prior = subprocess.run(["git", "show", "30a095c:bin/llm-wiki-enrich"], cwd=ROOT, capture_output=True, text=True, timeout=10)
        if prior.returncode: self.skipTest("previous reader history unavailable")
        module = types.ModuleType("pre_event_source_engine"); module.__file__ = str(ROOT / "bin/llm-wiki-enrich")
        sys.modules[module.__name__] = module; self.addCleanup(lambda: sys.modules.pop(module.__name__, None))
        exec(compile(prior.stdout, module.__file__, "exec"), module.__dict__)
        access = module.access_context(self.root, "codex")
        self.assertIsNone(module.read_page(self.root, PAGE, access, None))

    def test_historical_and_independent_event_references_do_not_claim_currentness(self):
        _, event = self.event()
        for relation in ("historical-citation", "independent-judgment"):
            self.write_page(PAGE, "conclusion:synthetic", MARKER + "\n", ai=True,
                            sources=[{**self.ref(event), "relation": relation}])
            for path in (self.root / "memory/events").glob("*.jsonl"): path.unlink()
            self.surfaces(True, ("codex",))
            pages = E["page_snapshots"](self.root, E["access_context"](self.root, "codex"), None)
            self.assertEqual(pages[0][1].metadata.governance["source_freshness"], "unassessed")

    def test_missing_or_bad_event_snapshot_is_not_hidden_by_direct_page_read(self):
        _, event = self.event(); self.conclusion(event)
        for path in (self.root / "memory/events").glob("*.jsonl"): path.unlink()
        self.surfaces(False, ("codex",))
        (self.root / "memory/events/bad.jsonl").write_bytes(b"{not json}\n")
        self.surfaces(False, ("codex",))
        # Legacy document-only reads retain their previous event independence.
        self.write_page(PAGE, "conclusion:synthetic", MARKER + "\n", ai=True)
        result = subprocess.run([sys.executable, str(ROOT / "bin/llm-wiki-enrich"), "--agent-profile", "codex", "--read-page", PAGE],
                                env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr); self.assertIn(MARKER, result.stdout)

    def test_context_v3_serialization_rechecks_source_event_and_keeps_budgets(self):
        proposal, event = self.event(); self.conclusion(event)
        api = runpy.run_path(str(ROOT / "bin/llm-wiki-context"), run_name="event_backed_emit")
        request = {"schemaVersion": 3, "selections": [{"kind": "event", "memoryId": event["record_id"],
                    "revision": C["record_revision"](event)}]}
        pack = api["prepare"](self.root, "codex", request)
        original = api["json"].dumps
        rows = [proposal, event, V["reviewed"](event, "withdraw", 3)]
        def changed(*a, **kw):
            output = original(*a, **kw)
            # Avoid recursively calling the patched serializer in this mutation.
            (self.root / "memory/events/2020-01.jsonl").write_text("\n".join(original(row) for row in rows) + "\n")
            return output
        buffer = io.BytesIO(); stdout = types.SimpleNamespace(buffer=buffer)
        with patch.object(api["json"], "dumps", changed), patch.object(sys, "stdout", stdout):
            with self.assertRaises((ValueError, api["ENGINE"]["EventSnapshotError"])): api["emit"](pack)
        self.assertEqual(buffer.getvalue(), b"")
        self.assertEqual((api["MAX_SELECTIONS"], api["MAX_ITEM_TEXT"], api["MAX_TEXT"]), (20, 12000, 40000))
        with self.assertRaises(ValueError): api["request"]({**request, "selections": request["selections"] * 21})

    def test_mcp_advertises_and_dispatches_opt_in_context_v3(self):
        _, event = self.event()
        messages = [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "prepare_context",
                     "arguments": {"schemaVersion": 3, "selections": [{"kind": "event", "memoryId": event["record_id"],
                         "revision": C["record_revision"](event)}]}}}]
        result = subprocess.run(["node", str(ROOT / "bin/llm-wiki-mcp")],
            input="".join(json.dumps(message) + "\n" for message in messages), capture_output=True, text=True,
            env={**self.env, "LLM_WIKI_AGENT_PROFILE": "codex"}, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        responses = {value["id"]: value for line in result.stdout.splitlines() if (value := json.loads(line)).get("id")}
        tool = next(tool for tool in responses[1]["result"]["tools"] if tool["name"] == "prepare_context")
        self.assertEqual(tool["inputSchema"]["properties"]["schemaVersion"], {"type": "integer", "enum": [1, 2, 3], "default": 1})
        pack = json.loads(responses[2]["result"]["content"][0]["text"])
        self.assertEqual(pack["schemaVersion"], 3)
        self.assertEqual(pack["items"][0]["governance"], {"domain": "work", "sensitivity": "internal"})


if __name__ == "__main__":
    unittest.main()
