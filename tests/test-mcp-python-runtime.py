#!/usr/bin/env python3
"""Real MCP helpers with an explicit interpreter and synthetic, isolated vaults."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
UNSET = object()


class PythonRuntime(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wikified-python-runtime-")
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name).resolve()
        self.root = self.work / "synthetic vault 中文"
        for name in ("notes", "wiki", "policy", "memory/events"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "templates/access-policy.json", self.root / "policy/access.json")
        self.empty_path = self.work / "empty-bin"
        self.empty_path.mkdir()
        self.node = shutil.which("node")
        if self.node is None:
            self.skipTest("Node is required for real MCP integration")
        self.python = Path(sys.executable).resolve()
        if os.name != "nt":
            directory = self.work / "Python runtime 中文"
            directory.mkdir()
            selected = directory / "python3"
            selected.symlink_to(self.python)
            self.python = selected
        self.env = {"PATH": str(self.empty_path), "HOME": str(self.work),
                    "LLM_WIKI_ROOT": str(self.root), "LLM_WIKI_BIN_TARGET": str(self.empty_path),
                    "PYTHONDONTWRITEBYTECODE": "1"}
        for key in ("SystemRoot", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"):
            if key in os.environ:
                self.env[key] = os.environ[key]

    def page(self, name, targets, body):
        fields = dict(memory_id="note:" + name, domain="work", sensitivity="internal",
                      review_state="approved", epistemic_status="human-stated", target_profiles=targets)
        path = self.root / "notes" / (name + ".md")
        path.write_text("---\n" + "\n".join(json.dumps(k) + ": " + json.dumps(v)
            for k, v in fields.items()) + "\n---\n" + body, encoding="utf-8")
        return {"kind": "page", "memoryId": fields["memory_id"],
                "revision": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}

    def rpc(self, profile, method, params=None, python=UNSET, engine=None, extra_env=None):
        env = {**self.env, "LLM_WIKI_AGENT_PROFILE": profile}
        if python is not UNSET:
            env["LLM_WIKI_PYTHON"] = str(python)
        env.update(extra_env or {})
        message = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        result = subprocess.run([self.node, str(engine or ROOT / "bin/llm-wiki-mcp")],
            input=json.dumps(message) + "\n", text=True, capture_output=True,
            env=env, cwd=self.work, timeout=15)
        return result, json.loads(result.stdout) if result.stdout.strip() else None

    def tool(self, profile, name, arguments, **kwargs):
        result, reply = self.rpc(profile, "tools/call", {"name": name, "arguments": arguments}, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsInstance(reply, dict)
        return reply

    def text(self, reply):
        self.assertNotIn("error", reply, reply)
        return "\n".join(item["text"] for item in reply["result"]["content"])

    def test_missing_python_handshake_does_not_prove_helper_calls(self):
        row = self.page("runtime", ["pi", "workbuddy"], "SYNTHETIC_RUNTIME_READY")
        for profile in ("pi", "workbuddy"):
            result, reply = self.rpc(profile, "initialize")
            self.assertEqual(result.returncode, 0)
            self.assertEqual(reply["result"]["serverInfo"]["name"], "wikified")
            for name, arguments in (("search_pages", {"query": "SYNTHETIC_RUNTIME_READY"}),
                                    ("prepare_context", {"selections": [row]})):
                reply = self.tool(profile, name, arguments)
                self.assertIn("error", reply)
                self.assertNotIn("SYNTHETIC_RUNTIME_READY", json.dumps(reply))

    def test_explicit_interpreter_runs_all_python_helpers_without_path_python(self):
        own = self.page("runtime", ["pi", "workbuddy"], "SYNTHETIC_RUNTIME_READY")
        pair = self.page("old-pair", ["codex", "opencode"], "SYNTHETIC_OLD_PAIR_WITHHELD")
        for profile in ("pi", "workbuddy"):
            with self.subTest(profile=profile):
                config = self.work / ("client 中文 " + profile) / "mcp.json"
                configured = subprocess.run([sys.executable, "-B", str(ROOT / "bin/llm-wiki-harness"),
                    "configure", "--harness", profile, "--config", str(config),
                    "--engine", str(ROOT / "bin/llm-wiki-mcp"), "--root", str(self.root),
                    "--node", self.node, "--python", str(self.python)],
                    env=self.env, text=True, capture_output=True, timeout=15)
                self.assertEqual(configured.returncode, 0, configured.stderr)
                entry = json.loads(config.read_text(encoding="utf-8"))["mcpServers"]["llm-wiki"]
                self.assertEqual(entry["env"]["LLM_WIKI_PYTHON"], str(self.python))
                def call(name, args):
                    return self.tool(profile, name, args, extra_env=entry["env"])
                self.assertIn("SYNTHETIC_RUNTIME_READY", self.text(call("search_pages", {"query": "SYNTHETIC_RUNTIME_READY"})))
                self.assertIn("SYNTHETIC_RUNTIME_READY", self.text(call("find_related", {"topic": "SYNTHETIC_RUNTIME_READY"})))
                self.assertIn("SYNTHETIC_RUNTIME_READY", self.text(call("read_page", {"path": "notes/runtime.md"})))
                pack = json.loads(self.text(call("prepare_context", {"schemaVersion": 3, "selections": [own, pair]})))
                self.assertEqual(pack["profile"], profile)
                self.assertEqual([item["status"] for item in pack["items"]], ["ready", "unavailable"])
                self.assertNotIn("SYNTHETIC_OLD_PAIR_WITHHELD", json.dumps(pack))
                self.assertIn("error", call("read_page", {"path": "notes/old-pair.md", "profile": "human"}))
                self.assertIn("error", call("prepare_context", {"selections": [pair], "profile": "human"}))
                self.text(call("record_event", {"type": "fact", "summary": "Synthetic runtime pending " + profile,
                    "profile": "human", "actor": "human", "review_status": "approved", "python": "/untrusted"}))
        events = [json.loads(line) for path in (self.root / "memory/events").glob("*.jsonl")
                  for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(events), 2)
        self.assertEqual({event["actor"]["id"] for event in events}, {"pi", "workbuddy"})
        for event in events:
            self.assertEqual(event["actor"]["type"], "ai")
            self.assertEqual(event["review_status"], "pending")
            self.assertEqual(event["target_agents"], [event["actor"]["id"]])

    def test_bad_explicit_interpreter_never_falls_back_to_available_python(self):
        self.page("runtime", ["pi"], "SYNTHETIC_RUNTIME_READY")
        available_path = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
        failing = self.work / "not-python"
        failing.write_text("not a Python runtime", encoding="utf-8")
        failing.chmod(0o700)
        for value in ("", "python3", str(self.work / "missing-python"), str(self.empty_path),
                      str(self.python) + "\n", str(failing)):
            with self.subTest(value=value):
                result, reply = self.rpc("pi", "tools/call",
                    {"name": "search_pages", "arguments": {"query": "SYNTHETIC_RUNTIME_READY"}},
                    python=value, extra_env={"PATH": available_path})
                self.assertTrue(result.returncode != 0 or "error" in (reply or {}))
                self.assertNotIn("SYNTHETIC_RUNTIME_READY", result.stdout)

    def test_custom_profile_registration_uses_the_same_explicit_python(self):
        path = self.root / "policy/access.json"
        policy = json.loads(path.read_text(encoding="utf-8"))
        policy["profiles"]["synthetic-custom"] = {**policy["profiles"]["pi"],
                                                "accepted_targets": ["synthetic-custom"]}
        path.write_text(json.dumps(policy), encoding="utf-8")
        self.page("custom", ["synthetic-custom"], "SYNTHETIC_CUSTOM_RUNTIME")
        result, reply = self.rpc("synthetic-custom", "initialize", python=self.python)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(reply["result"]["serverInfo"]["name"], "wikified")
        self.assertIn("SYNTHETIC_CUSTOM_RUNTIME", self.text(self.tool("synthetic-custom", "search_pages",
            {"query": "SYNTHETIC_CUSTOM_RUNTIME"}, python=self.python)))
        for profile in ("human", "unregistered"):
            result, reply = self.rpc(profile, "initialize", python=self.python)
            self.assertNotEqual(result.returncode, 0)
            self.assertIsNone(reply)

    def standalone(self):
        directory = self.work / "standalone MCP"
        directory.mkdir()
        target = directory / "llm-wiki-mcp"
        shutil.copyfile(ROOT / "bin/llm-wiki-mcp", target)
        return target

    def test_path_fallback_resolves_real_helper_absolutely(self):
        self.page("path", ["pi"], "SYNTHETIC_PATH_HELPER")
        reply = self.tool("pi", "search_pages", {"query": "SYNTHETIC_PATH_HELPER"},
            python=self.python, engine=self.standalone(), extra_env={"PATH": str(ROOT / "bin")})
        self.assertIn("SYNTHETIC_PATH_HELPER", self.text(reply))

    def test_missing_path_helper_never_executes_cwd_lookalike(self):
        marker = self.work / "cwd-helper-was-executed"
        (self.work / "llm-wiki-enrich").write_text(
            "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('unsafe')\n", encoding="utf-8")
        engine = self.standalone()
        for search_path in (str(self.empty_path), ".", ""):
            reply = self.tool("pi", "search_pages", {"query": "synthetic"},
                python=self.python, engine=engine, extra_env={"PATH": search_path})
            self.assertIn("error", reply)
            self.assertFalse(marker.exists())

    def test_explicit_interpreter_ignores_ambient_python_injection(self):
        self.page("isolated", ["pi"], "SYNTHETIC_ISOLATED_PYTHON")
        marker = self.work / "sitecustomize-was-imported"
        (self.work / "sitecustomize.py").write_text(
            "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('unsafe')\n", encoding="utf-8")
        reply = self.tool("pi", "search_pages", {"query": "SYNTHETIC_ISOLATED_PYTHON"},
            python=self.python, extra_env={"PYTHONPATH": str(self.work), "PYTHONHOME": str(self.empty_path)})
        self.assertIn("SYNTHETIC_ISOLATED_PYTHON", self.text(reply))
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
