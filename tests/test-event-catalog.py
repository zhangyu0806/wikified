#!/usr/bin/env python3
"""Local-human event catalog contract; all data is disposable synthetic input."""
from __future__ import annotations

import io
import copy
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

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "bin" / "llm-wiki-event-catalog"
API = runpy.run_path(str(CLI), run_name="catalog_test")
CORE = API["CORE"]
ENGINE = API["ENGINE"]
FIXTURE = runpy.run_path(str(REPO / "tests" / "test-event-snapshot-integrity.py"), run_name="catalog_fixture")
EVENT = FIXTURE["event"]


def pending(number=1, **changes):
    return EVENT(number, actor={"type": "ai", "id": "codex"}, review_status="pending",
        epistemic_status="ai-proposed", summary="Synthetic pending catalog proposal", details="Body-only marker 😀", **changes)


def reviewed(target, action="accept", number=2):
    request = {"target_id": target["id"], "action": action,
        "expected_revision": CORE["record_revision"](target), "request_id": f"test-{number}",
        "reason": "Synthetic event catalog review"}
    if target.get("supersedes") and action in {"accept", "correct"}:
        request["confirm_supersedes"] = True
    return CORE["prepare"](target, request, actor_id="synthetic-human", can_review=True,
        now="2026-01-02T00:00:00.000Z", event_id=f"{number:016x}")


class CatalogTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="wikified-event-catalog-test-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "memory/events").mkdir(parents=True)
        (self.root / "policy").mkdir()
        shutil.copyfile(REPO / "templates/access-policy.json", self.root / "policy/access.json")
        self.env = {key: value for key, value in os.environ.items() if key in {"PATH", "SystemRoot", "WINDIR", "LANG", "LC_ALL"}}
        self.env.update(HOME=str(self.root), PYTHONDONTWRITEBYTECODE="1", LLM_WIKI_ROOT="/must-not-use-installed-default")

    def write(self, rows, name="2020-01.jsonl"):
        path = self.root / "memory/events" / name
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        return path

    def cli(self, extra=(), success=True, base=None):
        arguments = base if base is not None else ["--root", str(self.root), "--profile", "human", "--purpose", "local-human-review"]
        result = subprocess.run([sys.executable, str(CLI), *arguments, *extra], env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0 if success else 2, result.stderr)
        if not success:
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, API["SAFE_FAILURE"] + "\n")
            return result
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def files(self):
        return {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

    def test_pending_visible_only_to_human_and_detail_read_is_read_only(self):
        proposal = pending()
        self.write([proposal])
        before = self.files()
        result = self.cli()
        self.assertEqual(result["purpose"], "local-human-review")
        self.assertEqual(result["coverage"], {"status": "complete", "scope": "authorized-active-events", "indexed": 1, "eligible": 1, "reasons": []})
        entry = result["entries"][0]
        self.assertEqual(entry["objectId"], proposal["memory_id"])
        self.assertEqual(entry["reviewStatus"], "pending")
        self.assertEqual(entry["reviewBinding"], "legacy")
        self.assertNotIn("details", entry)
        self.assertNotIn("summary", entry)
        self.assertNotIn("sources", entry)
        detail = self.cli(["--event-id", entry["eventId"], "--revision", entry["revision"]])
        self.assertEqual(detail, {"schemaVersion": 1, "entry": entry, "summary": proposal["summary"], "details": proposal["details"]})
        self.assertEqual(ENGINE["load_events"](self.root, ENGINE["access_context"](self.root, "codex")), [])
        self.assertEqual(self.files(), before)

    def test_explicit_bounded_cli_scope_and_no_installed_defaults(self):
        self.write([pending()])
        for arguments in [[], ["--root", str(self.root)],
            ["--root", str(self.root), "--profile", "codex", "--purpose", "local-human-review"],
            ["--root", str(self.root), "--profile", "human", "--purpose", "assistant-recall"]]:
            self.cli(success=False, base=arguments)
        for extra in [["--query", "x" * 501], ["--root", str(self.root)], ["--revision", "sha256:" + "0" * 64],
            ["--event-id", "0000000000000001"], ["--revision", ""], ["--secret-value=private"], ["--profile=human"]]:
            self.cli(extra, success=False)

    def test_authorization_before_title_search_count_and_detail(self):
        policy_path = self.root / "policy/access.json"
        policy = json.loads(policy_path.read_text())
        policy["profiles"]["human"].update(domains=["work"], max_sensitivity="internal", target_mode="self", accepted_targets=["human"])
        policy_path.write_text(json.dumps(policy))
        visible = pending(target_agents=["human"])
        hidden = EVENT(2, domain="personal", target_agents=["human"], summary="HIDDEN-TITLE", details="SECRET-QUERY")
        self.write([visible, hidden])
        result = self.cli(["--query", "secret-query"])
        self.assertEqual(result["coverage"]["eligible"], 0)
        self.assertEqual(result["entries"], [])
        catalog = self.cli()
        self.assertEqual(catalog["coverage"]["eligible"], 1)
        self.assertNotIn("HIDDEN", json.dumps(catalog))
        self.cli(["--event-id", hidden["id"], "--revision", CORE["record_revision"](hidden)], success=False)
        self.write([visible, {**hidden, "summary": "OTHER-HIDDEN-TITLE"}])
        self.assertEqual(self.cli()["revision"], catalog["revision"])

    def test_hidden_withdrawal_never_resurrects_approved_ancestor(self):
        a = pending(); b = reviewed(a); c = reviewed(b, "withdraw", 3)
        self.write([a, b, c])
        # Default human profile excludes rejected; its invisible withdrawal must
        # nevertheless close both older versions before any row is filtered.
        result = self.cli()
        self.assertEqual(result["entries"], [])
        self.assertEqual(result["coverage"]["eligible"], 0)
        self.cli(["--event-id", b["id"], "--revision", CORE["record_revision"](b)], success=False)

    def test_current_record_identity_survives_review_and_old_revision_is_denied(self):
        a = pending(); self.write([a]); first = self.cli()["entries"][0]
        b = reviewed(a); self.write([a, b]); second = self.cli()["entries"][0]
        self.assertEqual(first["objectId"], second["objectId"])
        self.assertNotEqual(first["eventId"], second["eventId"])
        self.assertEqual(second["reviewBinding"], "v4")
        self.assertEqual(second["decision"], "accept")
        self.cli(["--event-id", first["eventId"], "--revision", first["revision"]], success=False)
        self.cli(["--event-id", second["eventId"], "--revision", first["revision"]], success=False)
        self.assertEqual(self.cli(["--event-id", second["eventId"], "--revision", second["revision"]])["entry"], second)

    def test_changed_event_and_narrowed_policy_invalidate_prior_view(self):
        a = pending(); self.write([a]); first = self.cli(); entry = first["entries"][0]
        self.write([{**a, "summary": "Changed current proposal"}])
        self.assertNotEqual(self.cli()["revision"], first["revision"])
        self.cli(["--event-id", entry["eventId"], "--revision", entry["revision"]], success=False)
        policy_path = self.root / "policy/access.json"
        policy = json.loads(policy_path.read_text()); policy["profiles"]["human"]["review_states"] = ["approved"]
        policy_path.write_text(json.dumps(policy))
        self.assertEqual(self.cli()["entries"], [])
        self.cli(["--event-id", entry["eventId"], "--revision", entry["revision"]], success=False)

    def test_duplicate_physical_identity_and_concurrent_object_heads_fail(self):
        one = pending()
        self.write([one, one]); self.assertEqual(len(self.cli()["entries"]), 1)
        for rows in [[one, {**one, "summary": "Conflicting same ID"}],
            [one, {**pending(2), "record_id": one["memory_id"]}],
            [one, reviewed(one), reviewed(one, number=3)]]:
            self.write(rows); self.cli(success=False)

    def test_malformed_jsonl_unknown_schema_and_unrepresentable_authorized_legacy_fail(self):
        for tail in ["{broken\n", '{"schema_version":"unknown"}\n', "[]\n"]:
            path = self.write([pending()]); path.write_text(path.read_text() + tail)
            self.cli(success=False)
        legacy = {"id": "0000000000000001", "summary": "No explicit actor"}
        self.write([legacy]); before = self.files()
        entry = self.cli()["entries"][0]
        self.assertIsNone(entry["actor"])
        self.assertEqual(entry["reviewBinding"], "legacy")
        self.assertEqual(self.files(), before)
        for actor in [None, {}, {"type": "human", "id": ""}]:
            self.write([{**legacy, "actor": actor}]); self.cli(success=False)

    def test_search_uses_redacted_body_and_unicode_codepoint_offsets(self):
        proposal = pending()
        assignment = "password" + "="
        proposal.update(summary="X" * 200 + " " + assignment + "synthetic-private-value",
            details="😀Straße body-only-needle " + assignment + "synthetic-other-value")
        self.write([proposal])
        item = self.cli(["--query", "STRASSE"])["entries"][0]
        self.assertEqual(len(item["title"]), 120)
        self.assertEqual(item["match"]["field"], "details")
        self.assertEqual(item["match"]["offset"], 1)
        self.assertEqual(item["match"]["offsetUnit"], "unicode-codepoint")
        self.assertLessEqual(len(item["match"]["excerpt"]), 240)
        self.assertNotIn("synthetic-other-value", json.dumps(item))
        detail = self.cli(["--event-id", item["eventId"], "--revision", item["revision"]])
        self.assertNotIn("synthetic-private-value", json.dumps(detail))
        self.assertEqual(self.cli(["--query", "synthetic-other-value"])["coverage"]["eligible"], 0)
        self.assertEqual(self.cli(["--query", "body-only-needle"])["coverage"]["eligible"], 1)

    def test_sensitive_metadata_fails_closed_without_rewriting_any_identity(self):
        synthetic_key = "sk-" + "syntheticabcdefghijklmnop123456"
        cases = [
            {"actor": {"type": "ai", "id": synthetic_key}},
            {"record_id": synthetic_key},
            {"target_agents": [synthetic_key]},
            {"type": "password" + "=" + "synthetic-private-metadata"},
        ]
        for changed in cases:
            with self.subTest(fields=list(changed)):
                proposal = {**pending(), **changed}
                self.assertIsNotNone(ENGINE["event_metadata"](proposal), "Fixture must reach catalog projection")
                self.write([pending(2), proposal])
                before = self.files()
                self.cli(success=False)
                self.cli(["--query", "Synthetic"], success=False)
                self.cli(["--event-id", proposal["id"], "--revision", CORE["record_revision"](proposal)], success=False)
                self.assertEqual(self.files(), before, "Do not rewrite identity metadata to make it safe")

    def test_metadata_guard_covers_every_entry_string_and_nested_target(self):
        self.write([pending()])
        entry = self.cli()["entries"][0]
        # Includes generated IDs/revisions, actor type/id, targets, timestamp,
        # enums and display text; no field may accidentally bypass the guard.
        paths = []
        def collect(value, path=()):
            if isinstance(value, str):
                paths.append(path)
            elif isinstance(value, dict):
                for key, item in value.items():
                    collect(item, path + (key,))
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    collect(item, path + (index,))
        collect(entry)
        self.assertIn(("actor", "id"), paths)
        self.assertIn(("modifiedAt",), paths)
        self.assertIn(("targetProfiles", 0), paths)
        self.assertGreaterEqual(len(paths), 15)
        for path in paths:
            changed = copy.deepcopy(entry)
            parent = changed
            for component in path[:-1]:
                parent = parent[component]
            parent[path[-1]] = "token" + "=" + "synthetic-private-metadata"
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "sensitive catalog metadata"):
                API["assert_safe_metadata"](changed)

    def test_hidden_sensitive_metadata_never_affects_visible_catalog_or_coverage(self):
        policy_path = self.root / "policy/access.json"
        policy = json.loads(policy_path.read_text())
        policy["profiles"]["human"]["domains"] = ["work"]
        policy_path.write_text(json.dumps(policy))
        visible = pending()
        self.write([visible])
        before = self.cli()
        hidden = pending(2, domain="personal")
        hidden["actor"]["id"] = "sk-" + "syntheticabcdefghijklmnop123456"
        self.write([visible, hidden])
        self.assertEqual(self.cli(), before)
        self.cli(["--event-id", hidden["id"], "--revision", CORE["record_revision"](hidden)], success=False)

    def test_result_limit_is_honest_and_query_searches_beyond_first_page(self):
        rows = [pending(number) for number in range(1, 1003)]
        rows[-1]["details"] = "Late-only needle"
        self.write(list(reversed(rows)))
        result = self.cli()
        self.assertEqual(len(result["entries"]), 1000)
        self.assertEqual(result["coverage"], {"status": "incomplete", "scope": "authorized-active-events", "indexed": 1000, "eligible": 1002, "reasons": ["entry-limit"]})
        self.assertEqual(result["entries"][0]["eventId"], rows[0]["id"])
        result = self.cli(["--query", "late-only"])
        self.assertEqual(result["coverage"]["status"], "complete")
        self.assertEqual(result["entries"][0]["eventId"], rows[-1]["id"])

    def test_policy_generation_changes_and_policy_races_fail_safely(self):
        self.write([pending()]); before = self.cli()
        path = self.root / "policy/access.json"
        path.write_text(path.read_text() + "\n")
        self.assertNotEqual(self.cli()["revision"], before["revision"])
        original = API["policy_generation"]
        calls = 0
        def changed(root):
            nonlocal calls
            calls += 1
            if calls == 2:
                path.write_text(path.read_text() + " ")
            return original(root)
        with patch.dict(API["catalog"].__globals__, {"policy_generation": changed}):
            with self.assertRaisesRegex(ValueError, "authority changed"):
                API["catalog"](self.root)

    def test_linked_input_and_missing_human_profile_fail_without_partial_output(self):
        path = self.write([pending()])
        (path.parent / "linked.jsonl").symlink_to(path)
        self.cli(success=False)
        (path.parent / "linked.jsonl").unlink()
        policy_path = self.root / "policy/access.json"
        policy = json.loads(policy_path.read_text()); del policy["profiles"]["human"]
        policy_path.write_text(json.dumps(policy)); self.cli(success=False)

    def test_output_byte_limit_fails_before_emitting_any_json(self):
        self.write([pending()])
        # Patch only the bound, not the real projection/serialization pipeline.
        class Stdout:
            buffer = io.BytesIO()
        with patch.dict(API["main"].__globals__, {"MAX_OUTPUT_BYTES": 5}), patch.object(sys, "stdout", Stdout()):
            with self.assertRaisesRegex(ValueError, "output limit"):
                API["main"](["--root", str(self.root), "--profile", "human", "--purpose", "local-human-review"])
            self.assertEqual(sys.stdout.buffer.getvalue(), b"")

    def test_missing_companion_code_has_only_the_safe_diagnostic(self):
        isolated = self.root / "synthetic-incomplete-code"
        isolated.mkdir()
        shutil.copyfile(CLI, isolated / CLI.name)
        result = subprocess.run([sys.executable, str(isolated / CLI.name), "--help"], env=self.env,
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, API["SAFE_FAILURE"] + "\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
