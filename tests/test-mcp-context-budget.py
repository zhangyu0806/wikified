#!/usr/bin/env python3
"""Actual NDJSON transport with synthetic CLI responses; no user's memory."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class Transport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mcp-budget-synthetic-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        shutil.copyfile(ROOT / "bin/llm-wiki-mcp", self.root / "llm-wiki-mcp")
        for name, body in [("llm-wiki-enrich", "print('记忆🌲' * 4000)"),
                           ("llm-wiki-context", "import json,sys; print(json.dumps(sys.argv[1:]))")]:
            file = self.root / name
            file.write_text("#!/usr/bin/env python3\n" + body + "\n")
            file.chmod(0o755)

    def call(self, name, arguments):
        env = {**os.environ, "LLM_WIKI_AGENT_PROFILE": "codex", "LLM_WIKI_ROOT": str(self.root)}
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
        result = subprocess.run(["node", str(self.root / "llm-wiki-mcp")], input=json.dumps(message) + "\n", text=True, capture_output=True, env=env, timeout=10, check=True)
        return json.loads(result.stdout)

    def test_byte_budget_includes_marker_and_preserves_unicode(self):
        for budget in [200, 1500, 4000]:
            result = self.call("find_related", {"topic": "记忆", "budget": budget})["result"]["content"][0]["text"]
            self.assertLessEqual(len(result.encode("utf-8")), budget)
            self.assertIn("truncated", result)
            self.assertNotIn("\ufffd", result)
        for budget in [199, 4001, True, "200", 200.5]:
            self.assertIn("error", self.call("find_related", {"topic": "x", "budget": budget}))

    def test_context_tools_bind_profile_and_exact_operation(self):
        selection = {"kind": "event", "memoryId": "event:" + "a" * 16, "revision": "sha256:" + "b" * 64}
        for name, args in [("context_search", {"query": "项目", "project": "synthetic", "limit": 3}),
                           ("context_preview", {"selections": [selection]}),
                           ("context_read", {"selection": selection, "offset": 3, "length": 40})]:
            argv = json.loads(self.call(name, args)["result"]["content"][0]["text"])
            self.assertEqual(argv[argv.index("--agent-profile") + 1], "codex")
            self.assertEqual(argv[argv.index("--operation") + 1], name.removeprefix("context_"))
            self.assertEqual(json.loads(argv[argv.index("--request") + 1]), {"schemaVersion": 1, **args})
            self.assertIn("error", self.call(name, {**args, "profile": "human"}))
        for args in [{"selection": selection, "length": 8001}, {"selection": selection, "offset": -1}, {"selection": {**selection, "path": "secret"}}]:
            self.assertIn("error", self.call("context_read", args))

if __name__ == "__main__":
    unittest.main()
