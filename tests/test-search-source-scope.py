#!/usr/bin/env python3
"""Real CLI source-scope contracts, using disposable synthetic vaults only."""
from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "bin/llm-wiki-enrich"
QUERY = "scopealpha scopebeta scopegamma"
PROFILES = ("codex", "opencode")
RANKINGS = ("hybrid", "legacy")
PAGE_SOURCES = {"wiki-page", "note-page"}


class SearchSourceScope(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="wiki-search-scope-synthetic-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for directory in ("notes", "wiki", "memory/events", "policy"):
            (self.root / directory).mkdir(parents=True)
        shutil.copyfile(REPO / "templates/access-policy.json", self.root / "policy/access.json")

    def page(self, path="notes/evidence.md", body="scopealpha synthetic note evidence", **changes):
        fields = dict(memory_id="page:" + path, domain="work", sensitivity="internal",
                      review_state="approved", epistemic_status="human-stated",
                      target_profiles=list(PROFILES))
        fields.update(changes)
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("---\n" + "\n".join(json.dumps(key) + ": " + json.dumps(value)
                          for key, value in fields.items()) + "\n---\n" + body, encoding="utf-8")
        return fields["memory_id"]

    def events(self, count=45, details=""):
        rows = []
        for number in range(1, count + 1):
            identity = f"{number:016x}"
            rows.append(dict(schema_version="llm-wiki-memory-event/v3", id=identity,
                memory_id="event:" + identity, actor={"type": "human", "id": "synthetic-owner"},
                domain="work", sensitivity="internal", epistemic_status="human-confirmed",
                review_status="approved", target_agents=list(PROFILES), project="synthetic-scope",
                timestamp="2020-01-01T00:00:00Z", valid_from="2020-01-01T00:00:00Z",
                lifecycle="active", type="fact", summary=QUERY, details=details,
                files=["scope-fixture.ts"], supersedes=[], confidence=0, half_life_days=90))
        # Zero confidence removes clock-dependent scores; three lexical matches
        # still outrank the one-term note in both existing ranking modes.
        (self.root / "memory/events/synthetic.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def cli(self, *args, profile="codex"):
        return subprocess.run([sys.executable, "-B", str(ENGINE), "--agent-profile", profile, *args],
            env={"PATH": os.environ.get("PATH", os.defpath), "LLM_WIKI_ROOT": str(self.root),
                 "PYTHONDONTWRITEBYTECODE": "1"}, cwd=self.root,
            capture_output=True, text=True, timeout=15)

    def search(self, *args, profile="codex", ranking="hybrid", query=QUERY, limit=100, budget=200000):
        result = self.cli("--query", query, "--ranking", ranking, "--limit", str(limit),
                          "--max-chars", str(budget), "--json", *args, profile=profile)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLessEqual(len(result.stdout.rstrip("\n")), max(200, budget))
        return json.loads(result.stdout)

    def test_forty_higher_ranked_events_do_not_displace_pages(self):
        identity = self.page()
        self.events()
        for profile in PROFILES:
            for ranking in RANKINGS:
                with self.subTest(profile=profile, ranking=ranking):
                    mixed = self.search(profile=profile, ranking=ranking, limit=40)
                    self.assertEqual(len(mixed), 40)
                    self.assertEqual({row["source"] for row in mixed}, {"memory-event"})
                    complete = self.search(profile=profile, ranking=ranking)
                    self.assertEqual(complete[-1]["memory_id"], identity)
                    self.assertEqual(len(complete), 46)
                    pages = self.search("--source-scope", "pages", profile=profile,
                                        ranking=ranking, limit=40)
                    self.assertEqual([row["memory_id"] for row in pages], [identity])
                    self.assertEqual(pages[0]["source"], "note-page")

    def test_default_all_page_ranking_and_limit_are_preserved(self):
        self.page()
        self.page("wiki/scopealpha.md", QUERY + " synthetic wiki evidence")
        self.page("notes/second.md", "scopebeta scopegamma synthetic note evidence")
        self.events(2)
        for profile in PROFILES:
            for ranking in RANKINGS:
                with self.subTest(profile=profile, ranking=ranking):
                    mixed = self.search(profile=profile, ranking=ranking)
                    self.assertEqual(mixed, self.search("--source-scope", "all", profile=profile, ranking=ranking))
                    expected = [row for row in mixed if row["source"] in PAGE_SOURCES]
                    self.assertEqual(len(expected), 3)
                    self.assertEqual({row["source"] for row in expected}, PAGE_SOURCES)
                    pages = self.search("--source-scope", "pages", profile=profile, ranking=ranking)
                    self.assertEqual(pages, expected)
                    self.assertEqual(self.search("--source-scope", "pages", profile=profile,
                        ranking=ranking, limit=2), expected[:2])

    def test_excluded_events_do_not_consume_render_budget(self):
        identity = self.page()
        self.events(details="synthetic event padding " * 300)
        for profile in PROFILES:
            with self.subTest(profile=profile):
                mixed = self.search(profile=profile, budget=2000)
                self.assertEqual(len(mixed), 1)
                self.assertEqual(mixed[0]["source"], "memory-event")
                self.assertTrue(mixed[0]["truncated"])
                pages = self.search("--source-scope", "pages", profile=profile, budget=2000)
                self.assertEqual([row["memory_id"] for row in pages], [identity])
                self.assertNotIn("truncated", pages[0])

    def test_file_search_and_markdown_support_pages_scope(self):
        identity = self.page(body="scope-fixture.ts synthetic file evidence")
        self.events()
        for profile in PROFILES:
            for ranking in RANKINGS:
                with self.subTest(profile=profile, ranking=ranking):
                    args = ("--file", "scope-fixture.ts", "--ranking", ranking, "--limit", "40",
                            "--max-chars", "200000")
                    mixed = self.cli(*args, "--json", profile=profile)
                    self.assertEqual(mixed.returncode, 0, mixed.stderr)
                    self.assertEqual({row["source"] for row in json.loads(mixed.stdout)}, {"memory-event"})
                    pages = self.cli(*args, "--source-scope", "pages", "--json", profile=profile)
                    self.assertEqual(pages.returncode, 0, pages.stderr)
                    self.assertEqual([row["memory_id"] for row in json.loads(pages.stdout)], [identity])
                    markdown = self.cli(*args, "--source-scope", "pages", profile=profile)
                    self.assertEqual(markdown.returncode, 0, markdown.stderr)
                    self.assertIn("synthetic file evidence", markdown.stdout)
                    self.assertNotIn("memory-event", markdown.stdout)

    def test_pages_scope_preserves_profile_and_review_denials(self):
        allowed = self.page()
        denied = ({"target_profiles": ["human"]}, {"domain": "personal"},
                  {"sensitivity": "restricted"}, {"review_state": "pending"},
                  {"review_state": "rejected"})
        for number, fields in enumerate(denied):
            self.page(f"notes/hidden-{number}.md", QUERY + " syntheticdeniedmarker", **fields)
        (self.root / "notes/bare.md").write_text(QUERY + " syntheticdeniedmarker")
        for profile in PROFILES:
            for ranking in RANKINGS:
                with self.subTest(profile=profile, ranking=ranking):
                    rows = self.search("--source-scope", "pages", profile=profile, ranking=ranking)
                    self.assertEqual([row["memory_id"] for row in rows], [allowed])
                    self.assertNotIn("syntheticdeniedmarker", json.dumps(rows))

    def test_bad_event_snapshot_still_fails_without_partial_pages(self):
        self.page()
        (self.root / "memory/events/broken.jsonl").write_text("{synthetic-broken-event\n")
        for profile in PROFILES:
            for scope in ("all", "pages"):
                with self.subTest(profile=profile, scope=scope):
                    result = self.cli("--query", QUERY, "--source-scope", scope, "--json", profile=profile)
                    self.assertEqual(result.returncode, 4, result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertIn("[malformed-jsonl]", result.stderr)
                    self.assertNotIn("synthetic-broken-event", result.stderr)

    def test_pages_scope_excludes_graph_even_when_profile_allows_it(self):
        self.page(target_profiles=["human"])
        graph = self.root / "graphify-out/GRAPH_REPORT.md"
        graph.parent.mkdir()
        graph.write_text(QUERY + " synthetic graph evidence\n")
        mixed = self.search(profile="human")
        self.assertIn("graphify-report", {row["source"] for row in mixed})
        pages = self.search("--source-scope", "pages", profile="human")
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["source"], "note-page")

    def test_invalid_scope_and_non_search_combinations_are_rejected(self):
        self.page()
        for args in (("--query", QUERY, "--source-scope", "unknown"),
                     ("--read-page", "notes/evidence.md", "--source-scope", "pages"),
                     ("--session-start", "--source-scope", "pages"),
                     ("--source-scope", "pages")):
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
        for args in (("--read-page", "notes/evidence.md"), ("--session-start",)):
            with self.subTest(args=args):
                default = self.cli(*args)
                explicit = self.cli(*args, "--source-scope", "all")
                self.assertEqual(default.returncode, 0, default.stderr)
                self.assertEqual(explicit.returncode, 0, explicit.stderr)
                self.assertEqual(default.stdout, explicit.stdout)


# Reuse only public synthetic source/review fixture builders. These exercises
# invoke the real CLI with pages scope; no installed vault or service is used.
SOURCE = runpy.run_path(str(REPO / "tests/test-source-review-retrieval.py"))


class SourceScopeSourceGates(unittest.TestCase):
    setUp = SOURCE["SourceReviewRetrieval"].setUp
    write = SOURCE["SourceReviewRetrieval"].write
    ref = SOURCE["SourceReviewRetrieval"].ref
    derived = SOURCE["SourceReviewRetrieval"].derived
    cli = SOURCE["SourceReviewRetrieval"].cli
    enable = SOURCE["SourceReviewRetrieval"].enable
    append = SOURCE["SourceReviewRetrieval"].append

    def assert_visible(self, allowed):
        for profile in PROFILES:
            for ranking in RANKINGS:
                result = self.cli("--query", SOURCE["MARKER"], "--source-scope", "pages",
                                  "--ranking", ranking, "--json", profile=profile)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(SOURCE["MARKER"] in result.stdout, allowed)
                self.assertNotIn(SOURCE["SOURCE_ID"], result.stdout + result.stderr)

    def test_current_source_then_stale_or_unreviewed_body(self):
        derived = self.derived()
        self.assert_visible(True)
        original = self.source.read_bytes()
        self.source.write_bytes(original + b"Changed synthetic primary evidence.\n")
        self.assert_visible(False)
        self.source.write_bytes(original)
        self.assert_visible(True)
        derived.write_bytes(derived.read_bytes() + b"Unreviewed synthetic conclusion.\n")
        self.assert_visible(False)

    def test_persistent_hold_still_withholds_pages(self):
        self.derived()
        self.enable()
        self.assert_visible(True)
        self.append("hold", SOURCE["DERIVED_ID"])
        self.assert_visible(False)


if __name__ == "__main__":
    unittest.main()
