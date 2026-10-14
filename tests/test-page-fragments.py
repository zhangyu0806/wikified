#!/usr/bin/env python3
"""Authorized continuous fragments, using only disposable synthetic vaults."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = runpy.run_path(str(ROOT / "tests/test-continuing-context.py"))
M = FIXTURE["M"]
E = M["ENGINE"]


class PageFragments(unittest.TestCase):
    setUp = FIXTURE["Context"].setUp
    page = FIXTURE["Context"].page
    mcp = FIXTURE["Context"].mcp

    def cli(self, *args, profile="codex", tool="llm-wiki-enrich"):
        return subprocess.run([sys.executable, "-B", str(ROOT / "bin" / tool),
            "--agent-profile", profile, *args], capture_output=True, text=True, timeout=20,
            env={"PATH": os.environ.get("PATH", os.defpath), "LLM_WIKI_ROOT": str(self.root),
                 "PYTHONDONTWRITEBYTECODE": "1"}, cwd=self.root)

    def search(self, query="fragmentneedle", profile="codex", fragments=True, budget=65536, ranking="hybrid"):
        args = ["--query", query, "--source-scope", "pages", "--json", "--max-chars", str(budget),
                "--limit", "40", "--ranking", ranking]
        if fragments:
            args.append("--page-fragments")
        proc = self.cli(*args, profile=profile)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertLessEqual(len(proc.stdout.rstrip("\n")), budget)
        return json.loads(proc.stdout)

    def projection(self, name="one"):
        access = E["access_context"](self.root, "codex")
        pages = E["page_snapshots"](self.root, access, None)
        snapshot = next(snapshot for entry, snapshot in pages if entry.relative == f"notes/{name}.md")
        text = E["redact"](E["projected_page_text"](snapshot))
        pages.verify()
        return text

    def fragment(self, row, offset=0, length=8000, name="one"):
        projection = self.projection(name)
        return {**row, "kind": "page-fragment", "projectionRevision": "sha256:" + hashlib.sha256(
            projection.encode("utf-8")).hexdigest(), "offset": offset, "length": length}

    def pack(self, rows, schema=2, profile="codex"):
        proc = self.cli("--request", json.dumps(dict(schemaVersion=schema, selections=rows)),
                        profile=profile, tool="llm-wiki-context")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["schemaVersion"], schema)
        return result["items"]

    def test_real_long_tail_exact_redacted_coordinates_both_profiles_and_rankers(self):
        # Fake credential is assembled only to test full-page redaction before slicing.
        fake = "sk-" + "FAKEabcdefghijklmno1234567890"
        row = self.page(body="İ😀中文 " * 15000 + fake + "\n  FRAGMENTNEEDLE synthetic tail answer\n" + "尾😀" * 5000)
        projected = self.projection()
        self.assertGreater(len(projected.encode("utf-8")), 70000)
        self.assertNotIn(fake, projected)
        for profile in ("codex", "opencode"):
            for ranking in ("hybrid", "legacy"):
                hit, = self.search(profile=profile, ranking=ranking)
                self.assertEqual(hit["memory_id"], row["memoryId"])
                self.assertEqual(hit["revision"], row["revision"])
                fragment = hit["fragment"]
                self.assertEqual(fragment["offset"], projected.index("FRAGMENTNEEDLE") - 320)
                self.assertEqual(fragment["length"], 8000)
                self.assertEqual(fragment["projectionRevision"], "sha256:" + hashlib.sha256(projected.encode()).hexdigest())
                expected = projected[fragment["offset"]:fragment["offset"] + fragment["length"]]
                self.assertEqual(hit["text"], expected[:1200])
                self.assertTrue(hit["truncated"])
                selection = {**row, "kind": "page-fragment", **fragment}
                item, = self.pack([selection], profile=profile)
                self.assertEqual(item["text"], expected)
                self.assertEqual(item["governance"], {"domain": "work", "sensitivity": "internal"})
                self.assertNotIn(fake, json.dumps(item))
        self.assertEqual(self.pack([row], schema=1), [{"selection": row, "status": "too-large"}])

    def test_short_pages_and_default_search_are_unchanged(self):
        row = self.page(body="fragmentneedle short synthetic evidence")
        self.assertEqual(self.search(), self.search(fragments=False))
        self.assertNotIn("fragment", self.search()[0])
        self.assertEqual(self.pack([row], schema=1), self.pack([row], schema=2))
        self.page("long", "padding " * 9000 + "fragmentneedle")
        for ranking in ("hybrid", "legacy"):
            normal = self.search(fragments=False, ranking=ranking, budget=200000)
            fragments = self.search(ranking=ranking, budget=200000)
            for old, new in zip(normal, fragments):
                for field in ("memory_id", "score", "signals", "path", "revision", "governance"):
                    self.assertEqual(old[field], new[field])
            self.assertEqual(len(normal), len(fragments))

    def test_tail_range_budget_and_preview_are_exact(self):
        row = self.page(body="x " * 9000 + "\n  fragmentneedle\n")
        hit, = self.search()
        projected = self.projection()
        self.assertEqual(hit["fragment"]["length"], len(projected) - hit["fragment"]["offset"])
        self.assertEqual(hit["text"], projected[hit["fragment"]["offset"]:])
        self.assertFalse(hit["truncated"])
        for budget in (200, 900, 1600, 2200):
            result = self.search(budget=budget)
            if result:
                self.assertEqual(result[0]["fragment"], hit["fragment"])
                self.assertTrue(hit["text"].startswith(result[0]["text"]))
                self.assertEqual(result[0]["truncated"], len(result[0]["text"]) < hit["fragment"]["length"])
        selection = self.fragment(row, length=12000)
        self.assertEqual(len(self.pack([selection])[0]["text"]), 12000)
        past_end = {**selection, "offset": len(projected) - 1, "length": 2}
        self.assertEqual(self.pack([past_end]), [{"selection": past_end, "status": "unavailable"}])

    def test_revision_projection_and_rename_fail_closed(self):
        row = self.page(body="fragmentneedle " + "x" * 15000)
        fragment = self.fragment(row)
        (self.root / "notes/one.md").rename(self.root / "notes/renamed.md")
        self.assertEqual(self.pack([fragment])[0]["locator"], "notes/renamed.md")
        mismatch = {**fragment, "projectionRevision": "sha256:" + "0" * 64}
        self.assertEqual(self.pack([mismatch]), [{"selection": mismatch, "status": "unavailable"}])
        path = self.root / "notes/renamed.md"
        path.write_bytes(path.read_bytes() + b"changed")
        current = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(self.pack([mismatch]), [{"selection": mismatch, "status": "changed", "currentRevision": current}])

    def test_schema1_strict_and_schema2_validation_limits(self):
        row = self.page(body="fragmentneedle " + "x" * 15000)
        fragment = self.fragment(row)
        for schema, rows in [(1, [fragment]), (1, [{**row, "offset": 0}]), (2, [row, fragment]),
                             (2, [fragment] * 21), (3, [row])]:
            with self.assertRaises(ValueError):
                M["request"](dict(schemaVersion=schema, selections=rows))
        for change in ({"offset": -1}, {"offset": True}, {"offset": 1.0}, {"offset": 2**53}, {"offset": 2**53 - 1},
                       {"length": 0}, {"length": 12001}, {"length": False}, {"length": 1.5},
                       {"projectionRevision": "latest"}, {"path": "notes/one.md"}, {"kind": "event"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                M["request"](dict(schemaVersion=2, selections=[{**fragment, **change}]))
        with self.assertRaises(ValueError):
            M["prepare"](self.root, "human", dict(schemaVersion=2, selections=[fragment]))

    def test_fragment_and_whole_page_share_total_budget(self):
        rows = []
        for number in range(4):
            row = self.page(str(number), "fragmentneedle " + "x" * 14000)
            rows.append(self.fragment(row, length=12000, name=str(number)))
        self.assertEqual([item["status"] for item in self.pack(rows)], ["ready"] * 3 + ["too-large"])
        whole = self.page("short", "x" * 8000)
        self.assertEqual([item["status"] for item in self.pack([whole, *rows[:3]])], ["ready"] * 3 + ["too-large"])

    def test_forty_ranked_candidates_survive_fragment_preview_budget_both_profiles(self):
        query = "scopealpha scopebeta scopegamma"
        short_selections = []
        for number in range(20):
            directory = f"oversized-{number:02d}"
            (self.root / "notes" / directory).mkdir()
            self.page(directory + "/scopealpha", query + "\nHistorical index only; no current resolution.\n"
                      + "neutral synthetic background\n" * 600)
            short_selections.append(self.page(f"evidence-{number:02d}", "scopealpha owner: synthetic team\n"
                "scopebeta action: clear cache\nscopegamma result: retry once\n"))
        for profile in ("codex", "opencode"):
            complete = self.search(query=query, profile=profile, budget=200000)
            self.assertEqual(len(complete), 40)
            self.assertTrue(all("fragment" in hit for hit in complete[:20]))
            expected_short_ids = [row["memoryId"] for row in short_selections]
            self.assertEqual([hit["memory_id"] for hit in complete[20:]], expected_short_ids)
            bounded = self.search(query=query, profile=profile, budget=65536)
            self.assertEqual(len(bounded), 40)
            self.assertEqual([hit["memory_id"] for hit in bounded], [hit["memory_id"] for hit in complete])
            self.assertEqual(bounded[20:], complete[20:])
            for hit, full in zip(bounded[:20], complete[:20]):
                self.assertEqual(hit["fragment"], full["fragment"])
                self.assertEqual({key: value for key, value in hit.items() if key != "text"},
                                 {key: value for key, value in full.items() if key != "text"})
                self.assertTrue(full["text"].startswith(hit["text"]))
                self.assertLess(len(hit["text"]), 1200)
            # Search preview compression changes neither the actual selected
            # sources nor the unchanged twenty-item / 40,000-code-point pack.
            self.assertEqual([item["status"] for item in self.pack(short_selections, profile=profile)], ["ready"] * 20)
            normal = self.search(query=query, profile=profile, fragments=False)
            self.assertEqual([hit["memory_id"] for hit in normal], [hit["memory_id"] for hit in complete])

    def test_profile_review_and_authority_cannot_be_bypassed(self):
        row = self.page(body="fragmentneedle " + "x" * 15000)
        fragment = self.fragment(row)
        for fields in ({"target_profiles": ["human"]}, {"review_state": "pending"},
                       {"review_state": "rejected"}, {"domain": "personal"}, {"sensitivity": "restricted"}):
            self.page(body="fragmentneedle hidden synthetic material " + "x" * 15000, **fields)
            for profile in ("codex", "opencode"):
                self.assertEqual(self.search(profile=profile), [])
                self.assertEqual(self.pack([fragment], profile=profile), [{"selection": fragment, "status": "unavailable"}])

    def test_bad_event_and_generation_races_never_return_partial_fragment(self):
        row = self.page(body="fragmentneedle " + "x" * 15000)
        fragment = self.fragment(row)
        original = E["load_events"]
        for target in ("notes/one.md", "policy/access.json", "memory/events/new.jsonl"):
            def mutate(root, access):
                result = original(root, access)
                with (self.root / target).open("a") as handle:
                    handle.write("\n")
                return result
            with patch.dict(E, load_events=mutate), self.assertRaises(Exception):
                M["prepare"](self.root, "codex", dict(schemaVersion=2, selections=[fragment]))
        (self.root / "memory/events/bad.jsonl").write_text("{broken")
        for tool, args in (("llm-wiki-enrich", ["--query", "fragmentneedle", "--source-scope", "pages", "--json", "--page-fragments"]),
                           ("llm-wiki-context", ["--request", json.dumps(dict(schemaVersion=2, selections=[fragment]))])):
            result = self.cli(*args, tool=tool)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")

    def test_fragment_is_continuous_not_joined_high_scoring_lines(self):
        self.page(body="alphaseed alpha evidence\n" + "neutral " * 9000 + "\nomegaseed omega evidence\n")
        hit, = self.search(query="alphaseed omegaseed")
        projected = self.projection()
        self.assertEqual(hit["fragment"]["offset"], 0)
        self.assertEqual(hit["text"], projected[:1200])
        self.assertNotIn("omega evidence", hit["text"])
        self.page(body="İ😀" * 36000 + " singlelineneedle tail answer")
        hit, = self.search(query="singlelineneedle")
        projected = self.projection()
        self.assertEqual(hit["fragment"]["offset"], projected.index("singlelineneedle") - 320)
        self.assertIn("singlelineneedle tail answer", hit["text"])

    def test_fragment_flag_requires_explicit_query_pages_json(self):
        for args in (("--query", "fragmentneedle", "--json"), ("--query", "fragmentneedle", "--source-scope", "pages"),
                     ("--file", "fragmentneedle", "--source-scope", "pages", "--json"),
                     ("--session-start", "--source-scope", "pages", "--json"),
                     ("--read-page", "notes/one.md", "--source-scope", "pages", "--json")):
            result = self.cli(*args, "--page-fragments")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")

    def test_mcp_explicit_v2_roundtrip_and_v1_remains_strict(self):
        row = self.page(body="background " * 8000 + "fragmentneedle tail evidence")
        for profile in ("codex", "opencode"):
            response = self.mcp(profile, "search_pages", {"query": "fragmentneedle", "pageFragments": True, "maxChars": 8000})
            self.assertNotIn("error", response, response)
            hit, = json.loads(response["result"]["content"][0]["text"])
            selection = {**row, "kind": "page-fragment", **hit["fragment"]}
            response = self.mcp(profile, "prepare_context", {"schemaVersion": 2, "selections": [selection]})
            self.assertNotIn("error", response, response)
            result = json.loads(response["result"]["content"][0]["text"])
            self.assertEqual(result["schemaVersion"], 2)
            self.assertIn("fragmentneedle tail evidence", result["items"][0]["text"])
            self.assertIn("error", self.mcp(profile, "prepare_context", {"selections": [selection]}))
            self.assertIn("error", self.mcp(profile, "prepare_context", {"schemaVersion": 2, "selections": [selection], "profile": "human"}))
            self.assertIn("error", self.mcp(profile, "prepare_context", {"schemaVersion": True, "selections": [row]}))
            self.assertIn("error", self.mcp(profile, "search_pages", {"query": "fragmentneedle", "pageFragments": "true"}))


SOURCE = runpy.run_path(str(ROOT / "tests/test-source-review-retrieval.py"))


class FragmentSourceGates(unittest.TestCase):
    setUp = SOURCE["SourceReviewRetrieval"].setUp
    write = SOURCE["SourceReviewRetrieval"].write
    ref = SOURCE["SourceReviewRetrieval"].ref
    enable = SOURCE["SourceReviewRetrieval"].enable
    append = SOURCE["SourceReviewRetrieval"].append
    cli = PageFragments.cli
    search = PageFragments.search
    pack = PageFragments.pack

    def accepted(self):
        page = self.write(SOURCE["PATH"], SOURCE["DERIVED_ID"], "neutral " * 10000 + "fragmentneedle reviewed tail", ai=True, sources=[self.ref()])
        hit, = self.search()
        self.assertNotIn(SOURCE["SOURCE_ID"], json.dumps(hit))
        self.assertNotIn("Hidden synthetic source label", json.dumps(hit))
        return page, {"kind": "page-fragment", "memoryId": hit["memory_id"], "revision": hit["revision"], **hit["fragment"]}

    def denied(self, selection):
        for profile in ("codex", "opencode"):
            self.assertEqual(self.search(profile=profile), [])
            self.assertEqual(self.pack([selection], profile=profile), [{"selection": selection, "status": "unavailable"}])

    def test_reviewed_projection_current_then_stale_source_and_tampered_body(self):
        page, selection = self.accepted()
        for profile in ("codex", "opencode"):
            item, = self.pack([selection], profile=profile)
            self.assertIn("fragmentneedle reviewed tail", item["text"])
            self.assertNotIn(SOURCE["SOURCE_ID"], json.dumps(item))
        # The projection hash must bind the whole redacted safe document, even
        # when the selected range starts beyond the stripped source metadata.
        access = E["access_context"](self.root, "codex")
        pages = E["page_snapshots"](self.root, access, None)
        snapshot = next(snapshot for entry, snapshot in pages if entry.relative == SOURCE["PATH"])
        projection = E["redact"](E["projected_page_text"](snapshot))
        self.assertNotIn(SOURCE["SOURCE_ID"], projection)
        self.assertNotIn("Hidden synthetic source label", projection)
        self.assertEqual(selection["projectionRevision"], "sha256:" + hashlib.sha256(projection.encode()).hexdigest())
        prefix = self.pack([{**selection, "offset": 0, "length": 12000}])[0]["text"]
        self.assertEqual(prefix, projection[:12000])
        original = self.source.read_bytes()
        self.source.write_bytes(original + b"changed")
        self.denied(selection)
        self.source.write_bytes(original)
        page.write_bytes(page.read_bytes() + b"unreviewed change")
        self.denied(selection)

    def test_source_review_hold_and_ledger_race_withhold_all(self):
        _, selection = self.accepted()
        self.enable()
        self.assertEqual(self.pack([selection])[0]["status"], "ready")
        original = E["load_events"]
        def mutate(root, access):
            result = original(root, access)
            self.append("hold", SOURCE["DERIVED_ID"])
            return result
        with patch.dict(E, load_events=mutate), self.assertRaises(Exception):
            M["prepare"](self.root, "codex", dict(schemaVersion=2, selections=[selection]))
        self.denied(selection)


if __name__ == "__main__":
    unittest.main()
