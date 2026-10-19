#!/usr/bin/env python3
"""Explicit Pi/WorkBuddy identities over real CLI/MCP and disposable synthetic vaults."""
import hashlib
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
CONTEXT = runpy.run_path(str(ROOT / "bin/llm-wiki-context"), run_name="harness_profile_context")
ENGINE = CONTEXT["ENGINE"]
REVIEW = runpy.run_path(str(ROOT / "bin/llm_wiki_event_review.py"))
NEW_PROFILES = ("pi", "workbuddy")
CAPABILITY = "synthetic-harness-profile-human-review-only"


class HarnessProfiles(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="wikified-harness-profiles-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for directory in ("notes", "wiki", "memory/events", "policy"):
            (self.root / directory).mkdir(parents=True)
        shutil.copyfile(ROOT / "templates/access-policy.json", self.root / "policy/access.json")
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.root),
                    "XDG_CONFIG_HOME": str(self.root / "config"), "PYTHONDONTWRITEBYTECODE": "1",
                    "LLM_WIKI_ROOT": str(self.root), "LLM_WIKI_BIN_TARGET": str(ROOT / "bin")}

    def page(self, name, targets, body="Synthetic profile evidence", **extra):
        fields = dict(memory_id="note:" + name, domain="work", sensitivity="internal",
                      review_state="approved", epistemic_status="human-stated", target_profiles=targets)
        fields.update(extra)
        path = self.root / "notes" / (name + ".md")
        path.write_text("---\n" + "\n".join(json.dumps(key) + ": " + json.dumps(value)
            for key, value in fields.items()) + "\n---\n" + body, encoding="utf-8")
        return {"kind": "page", "memoryId": fields["memory_id"],
                "revision": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}

    def pack(self, profile, selections, version=1):
        return CONTEXT["prepare"](self.root, profile, {"schemaVersion": version, "selections": selections})

    def cli(self, profile, selections=None, version=1):
        args = [sys.executable, "-B", str(ROOT / "bin/llm-wiki-context"), "--root", str(self.root),
                "--agent-profile", profile]
        args += ["--observe-generation"] if selections is None else ["--request", json.dumps(
            {"schemaVersion": version, "selections": selections})]
        return subprocess.run(args, text=True, capture_output=True, env=self.env, timeout=15)

    def mcp_raw(self, profile, method, params=None, targets=None):
        env = {**self.env, "LLM_WIKI_AGENT_PROFILE": profile}
        if targets is not None:
            env["LLM_WIKI_TARGET_AGENTS"] = ",".join(targets)
        return subprocess.run(["node", str(ROOT / "bin/llm-wiki-mcp")], input=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}) + "\n",
            text=True, capture_output=True, env=env, timeout=15)

    def mcp(self, profile, tool, args, targets=None):
        result = self.mcp_raw(profile, "tools/call", {"name": tool, "arguments": args}, targets)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def events(self):
        return [json.loads(line) for path in sorted((self.root / "memory/events").glob("*.jsonl"))
                for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def history(self):
        return {path.name: path.read_bytes() for path in (self.root / "memory/events").glob("*.jsonl")}

    def selection(self, event):
        return {"kind": "event", "memoryId": event.get("record_id", event["memory_id"]),
                "revision": REVIEW["record_revision"](event)}

    def review(self, event, action):
        request = {"capability": CAPABILITY, "target_id": event["id"], "action": action,
                   "expected_revision": REVIEW["record_revision"](event),
                   "request_id": "synthetic-" + action + "-" + event["id"],
                   "reason": "Synthetic independent owner review", "confirm_supersedes": True}
        result = subprocess.run([sys.executable, "-B", str(ROOT / "bin/llm-wiki-event-review")],
            input=json.dumps(request), text=True, capture_output=True, timeout=15,
            env={**self.env, "LLM_WIKI_EVENT_REVIEW_CAPABILITY": CAPABILITY,
                 "LLM_WIKI_EVENT_REVIEW_ACTOR": "synthetic-independent-human"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(CAPABILITY, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_new_template_profiles_are_narrow_and_old_pair_remains_private(self):
        policy = json.loads((self.root / "policy/access.json").read_text())
        for profile in NEW_PROFILES:
            self.assertEqual(policy["profiles"][profile], dict(domains=["work"], max_sensitivity="internal",
                review_states=["approved"], target_mode="self", accepted_targets=[profile, "coding"],
                allow_raw=False, allow_direct_graph=False))
        pair = self.page("oldpair", ["codex", "opencode"], "OLD_PAIR_BODY")
        coding = self.page("coding", ["coding"], "SHARED_CODING_BODY")
        owned = {profile: self.page(profile, [profile], profile + "_OWN_BODY") for profile in NEW_PROFILES}
        denied = [self.page("human", ["human"], "HUMAN_BODY"),
                  self.page("pending", ["coding"], "PENDING_BODY", review_state="pending"),
                  self.page("personal", ["coding"], "PERSONAL_BODY", domain="personal"),
                  self.page("restricted", ["coding"], "RESTRICTED_BODY", sensitivity="restricted")]
        rows = [pair, coding, owned["pi"], owned["workbuddy"], *denied]
        original = {path.name: path.read_bytes() for path in (self.root / "notes").glob("*.md")}
        for version in (1, 2, 3):
            for profile in ("codex", "opencode", *NEW_PROFILES):
                with self.subTest(profile=profile, schema=version):
                    result = self.pack(profile, rows, version)
                    self.assertEqual((result["schemaVersion"], result["profile"]), (version, profile))
                    for item in result["items"]:
                        identity = item["selection"]["memoryId"]
                        allowed = identity == "note:coding" or identity == "note:" + profile or (
                            identity == "note:oldpair" and profile in ("codex", "opencode"))
                        self.assertEqual(item["status"], "ready" if allowed else "unavailable")
                        if not allowed:
                            self.assertEqual(set(item), {"selection", "status"})
        self.assertEqual(original, {path.name: path.read_bytes() for path in (self.root / "notes").glob("*.md")})

    def test_cli_observation_and_context_use_exact_new_identity(self):
        row = self.page("common", list(NEW_PROFILES))
        digests = []
        for profile in NEW_PROFILES:
            observed = self.cli(profile)
            self.assertEqual(observed.returncode, 0, observed.stderr)
            value = json.loads(observed.stdout)
            self.assertEqual(set(value), {"schemaVersion", "profile", "generation"})
            self.assertEqual((value["schemaVersion"], value["profile"]), (1, profile))
            self.assertRegex(value["generation"], r"^sha256:[a-f0-9]{64}$")
            self.assertEqual(value, json.loads(self.cli(profile).stdout))
            digests.append(value["generation"])
            for version in (1, 2, 3):
                result = self.cli(profile, [row], version)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["items"][0]["status"], "ready")
        self.assertNotEqual(*digests)

    def test_unknown_human_and_profile_override_are_rejected(self):
        row = self.page("common", list(NEW_PROFILES))
        for profile in ("human", "unknown-harness", "PI", "workbuddy\n", ""):
            for selections in (None, [row]):
                result = self.cli(profile, selections)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
            result = self.mcp_raw(profile, "initialize")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
        for profile in NEW_PROFILES:
            reply = self.mcp(profile, "prepare_context", {"selections": [row], "profile": "human"})
            self.assertIn("error", reply)

    def test_missing_new_policy_profile_never_falls_back_to_coding(self):
        row = self.page("common", ["coding"])
        path = self.root / "policy/access.json"
        policy = json.loads(path.read_text())
        for profile in NEW_PROFILES:
            policy["profiles"].pop(profile, None)
        path.write_text(json.dumps(policy))
        for profile in NEW_PROFILES:
            for selections in (None, [row]):
                result = self.cli(profile, selections)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
            self.assertIn("error", self.mcp(profile, "prepare_context", {"selections": [row]}))
        self.assertEqual(self.pack("coding", [row])["items"][0]["status"], "ready")

    def test_registered_custom_mcp_profile_keeps_read_compatibility_without_context_expansion(self):
        profile = "synthetic-research"
        path = self.root / "policy/access.json"
        policy = json.loads(path.read_text())
        policy["profiles"][profile] = {**policy["profiles"]["pi"], "accepted_targets": [profile]}
        path.write_text(json.dumps(policy))
        shared = self.page("custom", [profile], "customprofileprobe CUSTOM_AUTHORIZED_BODY")
        self.page("pair", ["codex", "opencode"], "customprofileprobe OLD_PAIR_PRIVATE_BODY")
        initialized = self.mcp_raw(profile, "initialize")
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        self.assertIn("result", json.loads(initialized.stdout))
        for tool, args in (("search_pages", {"query": "customprofileprobe", "maxChars": 8000}),
                           ("read_page", {"path": "notes/custom.md"}),
                           ("find_related", {"topic": "customprofileprobe"})):
            reply = self.mcp(profile, tool, args)
            self.assertNotIn("error", reply, reply)
            text = reply["result"]["content"][0]["text"]
            self.assertIn("CUSTOM_AUTHORIZED_BODY", text)
            self.assertNotIn("OLD_PAIR_PRIVATE_BODY", text)
        self.assertIn("error", self.mcp(profile, "read_page", {"path": "notes/pair.md"}))
        for version in (1, 2, 3):
            self.assertIn("error", self.mcp(profile, "prepare_context", {
                "schemaVersion": version, "selections": [shared]}))
        self.assertEqual(self.history(), {})

        # A name alone is not registration: malformed policy and removed entries
        # must fail before the MCP handshake, without returning policy details.
        for change in ("invalid-custom", "invalid-other", "missing-custom", "missing-policy"):
            broken = json.loads(json.dumps(policy))
            if change == "invalid-custom": broken["profiles"][profile]["allow_raw"] = "true"
            elif change == "invalid-other": broken["profiles"]["pi"]["unknown"] = True
            elif change == "missing-custom": del broken["profiles"][profile]
            if change == "missing-policy": path.unlink()
            else: path.write_text(json.dumps(broken))
            result = self.mcp_raw(profile, "initialize")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr.strip(), "unsupported assistant profile")

    def test_real_mcp_reads_only_authorized_sources_under_fixed_identity(self):
        shared = self.page("shared", ["coding"], "profileprobe SHARED_BODY")
        self.page("pair", ["codex", "opencode"], "profileprobe PAIR_ONLY_BODY")
        for profile in NEW_PROFILES:
            for tool, args in (("search_pages", {"query": "profileprobe", "maxChars": 8000}),
                               ("read_page", {"path": "notes/shared.md"})):
                reply = self.mcp(profile, tool, args)
                self.assertNotIn("error", reply, reply)
                text = reply["result"]["content"][0]["text"]
                self.assertIn("SHARED_BODY", text)
                self.assertNotIn("PAIR_ONLY_BODY", text)
            self.assertIn("error", self.mcp(profile, "read_page", {"path": "notes/pair.md"}))
            reply = self.mcp(profile, "prepare_context", {"schemaVersion": 3, "selections": [shared]})
            self.assertNotIn("error", reply, reply)
            self.assertEqual(json.loads(reply["result"]["content"][0]["text"])["profile"], profile)

    def test_default_writes_keep_independent_actor_self_target_and_pending_review(self):
        for profile in NEW_PROFILES:
            result = self.mcp(profile, "record_event", {"type": "decision", "summary": "newprofileproposal " + profile,
                "details": "Synthetic untrusted proposal", "actor": {"type": "human", "id": "forged"},
                "review_status": "approved", "profile": "human", "target_agents": ["coding", "*"]})
            self.assertNotIn("error", result, result)
            event = self.events()[-1]
            self.assertEqual(event["actor"], {"type": "ai", "id": profile})
            self.assertEqual(event["source"], "mcp:" + profile)
            self.assertEqual(event["target_agents"], [profile])
            self.assertEqual((event["review_status"], event["epistemic_status"]), ("pending", "ai-proposed"))
            self.assertEqual(event["schema_version"], "llm-wiki-memory-event/v3")
            for reader in ("codex", "opencode", *NEW_PROFILES):
                self.assertEqual(self.pack(reader, [self.selection(event)], 3)["items"][0]["status"], "unavailable")
            accepted = self.review(event, "accept")
            for reader in ("codex", "opencode", *NEW_PROFILES):
                expected = "ready" if reader == profile else "unavailable"
                self.assertEqual(self.pack(reader, [self.selection(accepted)], 3)["items"][0]["status"], expected)

    def test_explicit_four_profile_proposal_requires_human_review_and_withdrawal_rechecks(self):
        targets = ["codex", "opencode", *NEW_PROFILES]
        for writer in NEW_PROFILES:
            response = self.mcp(writer, "record_event", {"type": "decision", "summary": "explicit shared " + writer}, targets)
            self.assertNotIn("error", response, response)
            pending = self.events()[-1]
            self.assertEqual(pending["target_agents"], targets)
            self.assertEqual(pending["actor"]["id"], writer)
            accepted = self.review(pending, "accept")
            for reader in targets:
                reply = self.mcp(reader, "prepare_context", {"schemaVersion": 3, "selections": [self.selection(accepted)]})
                self.assertNotIn("error", reply, reply)
                item = json.loads(reply["result"]["content"][0]["text"])["items"][0]
                self.assertEqual(item["status"], "ready")
                self.assertEqual(item["governance"], {"domain": "work", "sensitivity": "internal"})
            self.review(accepted, "withdraw")
            before = self.history()
            for reader in targets:
                reply = self.mcp(reader, "prepare_context", {"schemaVersion": 3, "selections": [self.selection(accepted)]})
                self.assertNotIn("error", reply, reply)
                self.assertEqual(json.loads(reply["result"]["content"][0]["text"])["items"],
                                 [{"selection": self.selection(accepted), "status": "unavailable"}])
            self.assertEqual(self.history(), before)

    def test_existing_budgets_and_snapshot_failure_apply_to_new_profiles(self):
        too_large = self.page("large", ["coding"], "x" * 12001)
        rows = [self.page("budget" + str(index), ["coding"], "x" * 11000) for index in range(4)]
        for profile in NEW_PROFILES:
            for version in (1, 2, 3):
                self.assertEqual(self.pack(profile, [too_large], version)["items"][0]["status"], "too-large")
                self.assertEqual([item["status"] for item in self.pack(profile, rows, version)["items"]],
                                 ["ready", "ready", "ready", "too-large"])
            oversized = [{**rows[0], "memoryId": "note:selection" + str(index)} for index in range(21)]
            with self.assertRaises(ValueError): self.pack(profile, oversized)
            original = ENGINE["load_events"]
            def mutate(root, access, **kwargs):
                result = original(root, access, **kwargs)
                with (self.root / "policy/access.json").open("a") as handle: handle.write("\n")
                return result
            with patch.dict(ENGINE, load_events=mutate), self.assertRaises(Exception):
                self.pack(profile, rows)

    def test_fragments_keep_schema_projection_and_codepoint_budgets(self):
        rows = [self.page("fragment" + str(index), ["coding"], "😀fragmentneedle\n" + "文" * 18000)
                for index in range(4)]
        for profile in NEW_PROFILES:
            pages = ENGINE["page_snapshots"](self.root, ENGINE["access_context"](self.root, profile), None)
            projected = {snapshot.metadata.memory_id: ENGINE["redact"](ENGINE["projected_page_text"](snapshot))
                         for _, snapshot in pages}
            pages.verify()
            fragments = [{**row, "kind": "page-fragment", "offset": 100, "length": 12000,
                "projectionRevision": "sha256:" + hashlib.sha256(projected[row["memoryId"]].encode()).hexdigest()}
                for row in rows]
            for version in (2, 3):
                reply = self.mcp(profile, "prepare_context", {"schemaVersion": version, "selections": fragments})
                self.assertNotIn("error", reply, reply)
                result = json.loads(reply["result"]["content"][0]["text"])
                self.assertEqual([item["status"] for item in result["items"]], ["ready"] * 3 + ["too-large"])
                for item in result["items"][:3]:
                    self.assertEqual(item["text"], projected[item["selection"]["memoryId"]][100:12100])
                    self.assertEqual(len(item["text"]), 12000)
                    self.assertEqual(item["governance"], {"domain": "work", "sensitivity": "internal"})
                wrong = {**fragments[0], "projectionRevision": "sha256:" + "0" * 64}
                self.assertEqual(self.pack(profile, [wrong], version)["items"], [{"selection": wrong, "status": "unavailable"}])
                with self.assertRaises(ValueError): self.pack(profile, [{**fragments[0], "length": 12001}], version)
            with self.assertRaises(ValueError): self.pack(profile, [fragments[0]], 1)

    def test_revoking_one_profile_does_not_change_other_profile_or_evidence(self):
        row = self.page("shared", ["coding"], "profileprobe SHARED_BODY")
        original = (self.root / "notes/shared.md").read_bytes()
        for profile in NEW_PROFILES:
            reply = self.mcp(profile, "prepare_context", {"selections": [row]})
            self.assertEqual(json.loads(reply["result"]["content"][0]["text"])["items"][0]["status"], "ready")
        path = self.root / "policy/access.json"
        policy = json.loads(path.read_text())
        policy["profiles"]["pi"]["accepted_targets"] = ["pi"]
        path.write_text(json.dumps(policy))
        for profile, status in (("pi", "unavailable"), ("workbuddy", "ready")):
            reply = self.mcp(profile, "prepare_context", {"selections": [row]})
            result = json.loads(reply["result"]["content"][0]["text"])
            self.assertEqual(result["items"][0]["status"], status)
            if status == "unavailable": self.assertNotIn("SHARED_BODY", json.dumps(result))
        self.assertEqual((self.root / "notes/shared.md").read_bytes(), original)
        self.assertEqual(self.history(), {})


if __name__ == "__main__":
    unittest.main()
