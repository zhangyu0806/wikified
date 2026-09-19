#!/usr/bin/env python3
"""Synthetic notes/wiki retrieval, complete coverage, and revision-pinned chunks."""
import contextlib
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
loader = importlib.machinery.SourceFileLoader("notes_recall_engine", str(REPO / "bin/llm-wiki-enrich"))
spec = importlib.util.spec_from_loader(loader.name, loader)
engine = importlib.util.module_from_spec(spec)
sys.modules[loader.name] = engine
loader.exec_module(engine)
MARKER = "syntheticnotesrecallmarker"


class NotesRecall(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wikified-notes-recall-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ("wiki", "notes", "policy", "memory/events"):
            (self.root / directory).mkdir(parents=True)
        shutil.copyfile(REPO / "templates/access-policy.json", self.root / "policy/access.json")
        self.access = engine.access_context(self.root, "codex")

    def fields(self, **changed):
        return {"domain": "work", "sensitivity": "internal", "review_state": "approved",
                "target_profiles": ["codex", "opencode"], "epistemic_status": "human-stated", **changed}

    def write(self, path="notes/example.md", *, header=None, body=None, bare=False):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        fields = self.fields() if header is None else header
        source = "" if bare else "---\n" + "\n".join(json.dumps(k) + ": " + json.dumps(v) for k, v in fields.items()) + "\n---\n"
        target.write_text(source + (body if body is not None else MARKER + "\n"), encoding="utf-8")
        return target

    def run_cli(self, *args, profile="codex"):
        return subprocess.run([sys.executable, str(REPO / "bin/llm-wiki-enrich"), "--agent-profile", profile, *args],
                              env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LLM_WIKI_ROOT": str(self.root)},
                              text=True, capture_output=True, timeout=15)

    def search(self, query=MARKER, profile="codex"):
        return self.run_cli("--query", query, "--json", "--max-chars", "16000", profile=profile)

    def test_explicit_notes_share_search_and_read_contract_without_changing_policy(self):
        self.write(header=self.fields(memory_id="note:explicit"))
        for profile in ("codex", "opencode"):
            search = self.search(profile=profile)
            self.assertEqual(search.returncode, 0, search.stderr)
            item = json.loads(search.stdout)[0]
            self.assertEqual((item["path"], item["memory_id"]), ("notes/example.md", "note:explicit"))
            self.assertRegex(item["revision"], r"^sha256:[0-9a-f]{64}$")
            direct = self.run_cli("--read-page", item["path"], profile=profile)
            self.assertEqual(direct.returncode, 0, direct.stderr)
            self.assertIn(MARKER, direct.stdout)
        self.assertEqual((self.root / "policy/access.json").read_bytes(), (REPO / "templates/access-policy.json").read_bytes())

    def test_notes_missing_governance_never_inherit_legacy_wiki_grants(self):
        headers = [{}, {"title": "Ordinary note"}]
        for key in self.fields():
            fields = self.fields(); del fields[key]; headers.append(fields)
        for fields in headers:
            self.write(header=fields)
            self.assertIsNone(engine.read_page(self.root, "notes/example.md", self.access, None))
            self.assertNotIn(MARKER, self.search().stdout)
        self.write(bare=True)
        self.assertIsNone(engine.read_page(self.root, "notes/example.md", self.access, None))
        self.write("wiki/legacy.md", bare=True)
        self.assertIn(MARKER, engine.read_page(self.root, "wiki/legacy.md", self.access, None))

    def test_notes_keep_profile_and_version_review_denials(self):
        mutations = [{"target_profiles": ["human"]}, {"domain": "personal"}, {"sensitivity": "confidential"},
                     {"review_state": "pending"}, {"review_state": "rejected"}, {"epistemic_status": "ai-proposed"}]
        for version in (1, 2):
            mutations.append({"memory_id": "note:unreviewed", "mind2one": {"version": version, "id": "note:unreviewed",
                              "kind": "note", "origin": "ai", "review-state": "accepted", "sources": []}})
        for changed in mutations:
            self.write(header=self.fields(**changed))
            self.assertIsNone(engine.read_page(self.root, "notes/example.md", self.access, None))
            self.assertNotIn(MARKER, self.search().stdout)
        # Header authorization must stop before the full snapshot/body reader.
        self.write(header=self.fields(target_profiles=["human"]))
        with mock.patch.object(engine, "authorized_page_snapshot", side_effect=AssertionError("body read")):
            self.assertEqual(engine.page_snapshots(self.root, self.access, None), [])

    def test_tail_after_70kib_is_searchable_in_both_rankers(self):
        self.write(body=("background material\n" * 4500) + MARKER + "\n")
        for ranking in ("hybrid", "legacy"):
            result = self.run_cli("--query", MARKER, "--ranking", ranking, "--json", "--max-chars", "16000")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(MARKER, result.stdout)

    def test_large_pages_use_bounded_chunks_and_revision_pin(self):
        path = self.write(body=("bounded source text\n" * 18000) + MARKER + "\n")
        self.assertGreater(path.stat().st_size, 256 * 1024)
        self.assertIsNone(engine.read_page(self.root, "notes/example.md", self.access, None))
        first = json.loads(engine.read_page(self.root, "notes/example.md", self.access, None, offset=0, length=32000))
        self.assertTrue(first["truncated"])
        self.assertEqual(first["nextOffset"], 32000)
        self.assertIsNone(engine.read_page(self.root, "notes/example.md", self.access, None, offset=32000))
        end = json.loads(engine.read_page(self.root, "notes/example.md", self.access, None,
                                         offset=first["totalChars"] - len(MARKER) - 1, revision=first["revision"]))
        self.assertEqual(end["text"], MARKER + "\n")
        self.assertIsNone(end["nextOffset"])
        self.write(body="changed " + MARKER)
        self.assertIsNone(engine.read_page(self.root, "notes/example.md", self.access, None, offset=0, revision=first["revision"]))

    def test_redaction_occurs_before_unicode_chunks(self):
        fake = "sk-" + "FAKEabcdefghijklmnop123456789"
        self.write(body="💡 中文 prefix " + fake + " suffix\n")
        first = json.loads(engine.read_page(self.root, "notes/example.md", self.access, None, offset=0, length=7))
        parts, cursor = [first["text"]], first["nextOffset"]
        while cursor is not None:
            chunk = json.loads(engine.read_page(self.root, "notes/example.md", self.access, None, offset=cursor, length=7, revision=first["revision"]))
            parts.append(chunk["text"]); cursor = chunk["nextOffset"]
        text = "".join(parts)
        self.assertIn("💡 中文 prefix", text)
        self.assertIn("[REDACTED_OPENAI_KEY]", text)
        self.assertNotIn(fake, text)

    def test_qualified_paths_distinguish_same_names_but_ids_and_case_aliases_fail_closed(self):
        self.write("wiki/example.md")
        self.write("notes/example.md")
        items = json.loads(self.search().stdout)
        self.assertEqual({item["path"] for item in items}, {"wiki/example.md", "notes/example.md"})
        self.assertEqual(len({item["memory_id"] for item in items}), 2)
        self.write("wiki/example.md", header=self.fields(memory_id="duplicate:synthetic"))
        self.write("notes/example.md", header=self.fields(memory_id="duplicate:synthetic"))
        result = self.search()
        self.assertEqual(result.returncode, 4)
        self.assertEqual(result.stdout, "")
        self.assertIn("identity-conflict", result.stderr)
        self.assertNotIn("duplicate:synthetic", result.stderr)
        self.assertIsNone(engine.read_page(self.root, "wiki/example.md", self.access, None))
        self.write("notes/example.md")
        self.write("notes/Example.md")
        self.assertEqual(self.search().returncode, 4)

    def test_budget_limits_fail_explicitly_instead_of_returning_prefix(self):
        self.write()
        self.write("notes/later.md", body="lastpagedistinctmarker\n")
        for bound, value in (("MAX_WIKI_FILES", 1), ("MAX_WIKI_TOTAL_BYTES", 1), ("MAX_PAGE_SNAPSHOT_BYTES", 1), ("MAX_PAGE_DIRECTORY_ENTRIES", 1)):
            with mock.patch.object(engine, bound, value):
                with self.assertRaises(engine.PageSnapshotError):
                    engine.page_snapshots(self.root, self.access, None)
        with mock.patch.object(engine, "MAX_WIKI_FILES", 1), mock.patch.object(engine, "root_path", return_value=self.root):
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(engine.main(["--agent-profile", "codex", "--query", MARKER, "--json"]), 4)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("file-count-limit", stderr.getvalue())

    def test_new_or_changed_pages_during_snapshot_fail_instead_of_claiming_complete(self):
        self.write()
        original = engine.authorized_page_snapshot
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            self.write("notes/added.md", body="newlyaddedmarker")
            return result
        with mock.patch.object(engine, "authorized_page_snapshot", side_effect=changed):
            with self.assertRaisesRegex(engine.PageSnapshotError, "snapshot-changed"):
                engine.page_snapshots(self.root, self.access, None)

    def test_symlink_and_traversal_never_expand_roots(self):
        self.write()
        (self.root / "notes/link.md").symlink_to(self.root / "notes/example.md")
        (self.root / "wiki/alias").symlink_to(self.root / "notes", target_is_directory=True)
        for requested in ("notes/link.md", "wiki/alias/example.md", "notes/../policy/access.json", "/notes/example.md"):
            self.assertIsNone(engine.read_page(self.root, requested, self.access, None), requested)

    def test_real_mcp_notes_search_and_chunk_transport(self):
        self.write(header=self.fields(memory_id="note:mcp"), body=MARKER + "\n" + ("bounded\n" * 40000))
        for profile in ("codex", "opencode"):
            requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "notes-test", "version": "1"}}},
                        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "search_pages", "arguments": {"query": MARKER, "maxChars": 8000}}},
                        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "read_page", "arguments": {"path": "notes/example.md", "offset": 0, "length": 1000}}},
                        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "read_page", "arguments": {"path": "notes/example.md", "offset": 1000}}}]
            result = subprocess.run(["node", str(REPO / "bin/llm-wiki-mcp")], input="\n".join(json.dumps(row) for row in requests) + "\n",
                                    env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LLM_WIKI_ROOT": str(self.root), "LLM_WIKI_AGENT_PROFILE": profile},
                                    text=True, capture_output=True, timeout=20)
            responses = {row["id"]: row for line in result.stdout.splitlines() if line.strip() for row in [json.loads(line)] if "id" in row}
            self.assertIn("notes/example.md", json.dumps(responses[2]))
            chunk = json.loads(responses[3]["result"]["content"][0]["text"])
            self.assertEqual(chunk["memory_id"], "note:mcp")
            self.assertTrue(chunk["truncated"])
            self.assertIn("page unavailable", json.dumps(responses[4]))


if __name__ == "__main__":
    unittest.main()
