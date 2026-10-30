#!/usr/bin/env python3
"""Portable Pi/WorkBuddy config and bounded stdio probes; synthetic files only."""
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
H = runpy.run_path(str(ROOT / "bin/llm-wiki-harness"), run_name="portable_harness_tests")


class HarnessPortability(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wikified-portable-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.config = self.home / "client space 中文" / "mcp.json"

    def entry(self, harness="pi", **overrides):
        args = dict(transport="local", engine="/synthetic engine/llm-wiki-mcp",
                    root="/synthetic vault/中文", node="/synthetic node/bin/node",
                    distribution=None, wsl_command="wsl.exe", host_platform="darwin")
        args.update(overrides)
        return H["portable_mcp_entry"](harness, **args)

    def put(self, data):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def test_local_paths_and_identity_are_argv_and_environment(self):
        for harness in ("pi", "workbuddy"):
            row = self.entry(harness)
            self.assertEqual(row["command"], "/synthetic node/bin/node")
            self.assertEqual(row["args"], ["/synthetic engine/llm-wiki-mcp"])
            self.assertEqual(row["env"], {"LLM_WIKI_AGENT_PROFILE": harness,
                "LLM_WIKI_DOMAIN": "work", "LLM_WIKI_ROOT": "/synthetic vault/中文",
                "LLM_WIKI_TARGET_AGENTS": harness})
        self.assertEqual(self.entry()["exposure"], "direct")
        self.assertTrue(self.entry()["enabled"])

    def test_wsl_uses_explicit_distribution_and_linux_paths(self):
        row = self.entry("workbuddy", transport="wsl", distribution="Ubuntu 合成",
                         wsl_command=r"C:\Windows Path\System32\wsl.exe", host_platform="win32")
        self.assertEqual(row["command"], r"C:\Windows Path\System32\wsl.exe")
        self.assertEqual(row["args"], ["--distribution", "Ubuntu 合成", "--exec", "/usr/bin/env",
            "LLM_WIKI_AGENT_PROFILE=workbuddy", "LLM_WIKI_DOMAIN=work",
            "LLM_WIKI_ROOT=/synthetic vault/中文", "LLM_WIKI_TARGET_AGENTS=workbuddy",
            "/synthetic node/bin/node", "/synthetic engine/llm-wiki-mcp"])
        self.assertNotIn("sh", row["args"])

    def test_explicit_python_is_literal_local_env_or_linux_wsl_argument(self):
        interpreter = "/synthetic Python 中文/bin/python3"
        for harness in ("pi", "workbuddy"):
            local = self.entry(harness, python=interpreter)
            self.assertEqual(local["env"]["LLM_WIKI_PYTHON"], interpreter)
            remote = self.entry(harness, python=interpreter, transport="wsl", distribution="Ubuntu")
            self.assertIn("LLM_WIKI_PYTHON=" + interpreter, remote["args"])
            self.assertEqual(remote["env"], {})
        for invalid in ("python3", "", r"C:\Python\python.exe", "/runtime/$PYTHON", "/runtime/python\n"):
            with self.subTest(invalid=invalid), self.assertRaises(H["HarnessError"]):
                self.entry(python=invalid)

    def test_shell_characters_remain_literal_arguments(self):
        path = "/synthetic 中文/space ' quote ; (literal) & engine"
        row = self.entry(transport="wsl", distribution="Ubuntu", engine=path, root=path)
        self.assertEqual(row["args"][-1], path)
        self.assertIn("LLM_WIKI_ROOT=" + path, row["args"])

    def test_client_variable_interpolation_is_rejected(self):
        for harness in ("pi", "workbuddy"):
            for options in ({"root": "/synthetic/${HOME}"}, {"root": "/synthetic/$HOME"},
                            {"root": "/synthetic/$$"}, {"root": "/synthetic/$!"},
                            {"root": "/synthetic/$(literal)"}, {"engine": "/synthetic/${ENGINE:-other}"},
                            {"node": "/synthetic/${NODE}"},
                            {"transport": "wsl", "distribution": "${DISTRO}"},
                            {"transport": "wsl", "distribution": "Ubuntu",
                             "wsl_command": r"C:\${DIRECTORY}\wsl.exe"}):
                with self.subTest(harness=harness, options=options), self.assertRaises(H["HarnessError"]):
                    self.entry(harness, **options)

    def test_profiles_and_invalid_paths_fail_closed(self):
        for profile in ("human", "unknown", "pi\n", "codex"):
            with self.subTest(profile=profile), self.assertRaises(H["HarnessError"]):
                self.entry(profile)
        for options in ({"root": "relative"}, {"engine": "~/engine"}, {"node": "node"},
                        {"root": "/a\nprofile=human"}, {"root": r"C:\vault"},
                        {"transport": "wsl"}, {"transport": "wsl", "distribution": ""},
                        {"transport": "local", "host_platform": "win32"},
                        {"transport": "wsl", "distribution": "Ubuntu", "wsl_command": "cmd.exe"}):
            with self.subTest(options=options), self.assertRaises(H["HarnessError"]):
                self.entry(**options)

    def test_merge_preserves_unknown_fields_and_is_idempotent(self):
        self.put({"unknown": {"setting": "preserve"}, "mcpServers": {"other": {"command": "untouched"}}})
        original = self.config.read_bytes()
        self.assertTrue(H["configure_portable_file"](self.config, self.entry()))
        result = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(result["unknown"], {"setting": "preserve"})
        self.assertEqual(result["mcpServers"]["other"], {"command": "untouched"})
        backups = list(self.config.parent.glob("*.bak-wikified-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        first = self.config.read_bytes()
        self.assertFalse(H["configure_portable_file"](self.config, self.entry()))
        self.assertEqual(self.config.read_bytes(), first)
        self.assertEqual(list(self.config.parent.glob("*.bak-wikified-*")), backups)

    def test_existing_entry_extra_client_settings_are_preserved(self):
        row = self.entry()
        row["syntheticFutureOption"] = {"keep": True}
        self.put({"mcpServers": {"llm-wiki": row}})
        original = self.config.read_bytes()
        self.assertFalse(H["configure_portable_file"](self.config, self.entry()))
        self.assertEqual(original, self.config.read_bytes())

    def test_conflicting_or_disabled_entry_never_overwritten(self):
        for mutate in (lambda row: row.update(command="other"),
                       lambda row: row.update(enabled=False),
                       lambda row: row.update(disabled=True),
                       lambda row: row["env"].update(LLM_WIKI_AGENT_PROFILE="human"),
                       lambda row: row["env"].update(UNREVIEWED_ENV="value")):
            row = self.entry()
            mutate(row)
            self.put({"mcpServers": {"llm-wiki": row}})
            original = self.config.read_bytes()
            with self.assertRaises(H["HarnessError"]):
                H["configure_portable_file"](self.config, self.entry())
            self.assertEqual(original, self.config.read_bytes())
            self.assertEqual(H["portable_file_status"](self.config, self.entry()), "stale/wrong-link")

    def test_malformed_and_duplicate_config_left_unchanged(self):
        self.config.parent.mkdir(parents=True)
        for text in ('{"mcpServers":', '{"mcpServers": {}, "mcpServers": {}}', '[]',
                     '{\n// synthetic comment\n"mcpServers": {}\n}', '{"mcpServers": {},}',
                     '{"mcpServers": {}, "future": NaN}'):
            self.config.write_text(text, encoding="utf-8")
            with self.assertRaises(H["HarnessError"]):
                H["configure_portable_file"](self.config, self.entry())
            self.assertEqual(self.config.read_text(encoding="utf-8"), text)
            self.assertEqual(H["portable_file_status"](self.config, self.entry()), "unverifiable")

    def test_status_reads_no_process_or_client_configuration_beyond_selected_file(self):
        self.put({"mcpServers": {"llm-wiki": self.entry()}})
        with patch("subprocess.Popen", side_effect=AssertionError("status must not spawn")):
            row = H["inspect_portable"]("pi", self.config, self.entry())
        self.assertEqual(row.overall, "configured")
        self.assertEqual(row.executable, "not-checked")
        self.assertIn("not verified", row.note)

    def test_symlinked_config_is_refused_when_supported(self):
        target = self.home / "target.json"
        target.write_text("{}", encoding="utf-8")
        self.config.parent.mkdir(parents=True)
        try:
            self.config.symlink_to(target)
        except OSError:
            self.skipTest("host does not grant symlink creation")
        with self.assertRaises(H["HarnessError"]):
            H["configure_portable_file"](self.config, self.entry())
        self.assertEqual(target.read_text(encoding="utf-8"), "{}")

    def test_atomic_writer_without_fchmod(self):
        with patch.object(os, "fchmod", None, create=True):
            H["atomic_write"](self.config, "{}\n")
        self.assertEqual(self.config.read_text(encoding="utf-8"), "{}\n")

    def test_foreign_windows_config_string_is_not_a_posix_relative_file(self):
        if sys.platform == "win32":
            self.assertEqual(str(H["portable_config_path"]("pi", r"C:\Synthetic User\中文\mcp.json")),
                             r"C:\Synthetic User\中文\mcp.json")
        else:
            with self.assertRaises(H["HarnessError"]):
                H["portable_config_path"]("pi", r"C:\Synthetic User\中文\mcp.json")
        with self.assertRaises(H["HarnessError"]):
            H["portable_config_path"]("pi", "relative/mcp.json")

    def test_cli_wsl_configuration_and_static_doctor_are_cross_platform(self):
        common = ["--harness", "pi", "--config", str(self.config), "--transport", "wsl",
                  "--engine", "/synthetic/bin/llm-wiki-mcp", "--root", "/synthetic/vault",
                  "--wsl-distribution", "Ubuntu", "--node", "/synthetic/node",
                  "--python", "/synthetic Python 中文/python3"]
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "LLM_WIKI_DISABLE_PATH_DETECTION": "1"}
        cli = [sys.executable, "-B", str(ROOT / "bin/llm-wiki-harness")]
        made = subprocess.run([*cli, "configure", *common], env=env, capture_output=True, text=True)
        self.assertEqual(made.returncode, 0, made.stderr)
        config = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertIn("LLM_WIKI_PYTHON=/synthetic Python 中文/python3", config["mcpServers"]["llm-wiki"]["args"])
        checked = subprocess.run([*cli, "doctor", *common, "--json"], env=env, capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        report = json.loads(checked.stdout)
        self.assertEqual(report["configuration"], "configured")
        self.assertEqual(report["transport"]["state"], "not-run")
        self.assertEqual(report["realClientAcceptance"], "not-verified")
        self.assertEqual(report["vaultAuthorization"], "not-tested")
        self.assertEqual(report["helperRuntime"], "not-tested")
        # A stale entry makes doctor fail before any executable can be launched.
        config = json.loads(self.config.read_text(encoding="utf-8"))
        config["mcpServers"]["llm-wiki"]["command"] = "untrusted-no-execution"
        self.put(config)
        failed = subprocess.run([*cli, "doctor", *common, "--probe", "--json"], env=env,
                                capture_output=True, text=True)
        self.assertEqual(failed.returncode, 1)
        self.assertEqual(json.loads(failed.stdout)["transport"]["state"], "not-run")

    def test_cli_refuses_incomplete_and_mixed_portable_configuration(self):
        cli = [sys.executable, "-B", str(ROOT / "bin/llm-wiki-harness")]
        for args in (["configure", "--harness", "pi"],
                     ["configure", "--harness", "pi", "--harness", "workbuddy"],
                     ["configure", "--harness", "human"],
                     ["status", "--python", "/synthetic/python3"],
                     ["status", "--engine", "/synthetic/bin/llm-wiki-mcp"]):
            with self.subTest(args=args):
                result = subprocess.run([*cli, *args], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)

    def probe(self, behavior="good", timeout=2):
        server = self.home / "fake stdio 中文.py"
        calls = self.home / "calls.jsonl"
        server.write_text('''import json, os, sys, time
behavior = sys.argv[1]
if behavior == "environment" and any(key in os.environ for key in ("OPENAI_API_KEY", "LLM_WIKI_AGENT_PROFILE")):
    raise SystemExit(4)
for line in sys.stdin:
    request = json.loads(line)
    with open(sys.argv[2], "a", encoding="utf-8") as handle: handle.write(json.dumps(request) + "\\n")
    if "id" not in request: continue
    if behavior == "timeout": time.sleep(30)
    if behavior == "overflow": print("x" * 300000, flush=True); continue
    if behavior == "junk": print("synthetic-private-marker", flush=True); continue
    if request["method"] == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "other" if behavior == "identity" else "wikified", "version": "synthetic"}}
    else:
        names = ["prepare_context", "search_pages", "read_page", "record_event"]
        if behavior == "missing": names.remove("prepare_context")
        result = {"tools": [{"name": name, "inputSchema": {"type": "object"}} for name in names]}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
''', encoding="utf-8")
        return H["probe_stdio"]({"command": sys.executable, "args": [str(server), behavior, str(calls)], "env": {}}, timeout)

    def test_active_probe_initializes_and_lists_tools_only(self):
        self.assertEqual(self.probe()["state"], "initialized")
        calls = [json.loads(line)["method"] for line in (self.home / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(calls, ["initialize", "notifications/initialized", "tools/list"])

    def test_probe_does_not_inherit_api_credentials_or_ambient_profile(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-not-a-real-credential",
                                    "LLM_WIKI_AGENT_PROFILE": "human"}):
            self.assertEqual(self.probe("environment")["state"], "initialized")

    def test_real_mcp_initialization_does_not_require_or_create_a_vault(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node is not installed")
        missing = self.home / "not a vault 中文"
        for harness in ("pi", "workbuddy"):
            entry = {"command": node, "args": [str(ROOT / "bin/llm-wiki-mcp")], "env": {
                "LLM_WIKI_AGENT_PROFILE": harness, "LLM_WIKI_DOMAIN": "work",
                "LLM_WIKI_TARGET_AGENTS": harness, "LLM_WIKI_ROOT": str(missing)}}
            self.assertEqual(H["probe_stdio"](entry)["state"], "initialized")
        self.assertFalse(missing.exists())

    def test_probe_rejects_wrong_identity_missing_tools_and_non_json(self):
        for behavior in ("identity", "missing", "junk", "overflow"):
            with self.subTest(behavior=behavior):
                result = self.probe(behavior)
                self.assertEqual(result["state"], "failed")
                self.assertNotIn("synthetic-private-marker", json.dumps(result))

    def test_probe_timeout_is_not_success(self):
        result = self.probe("timeout", timeout=0.2)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["reason"], "timeout")

    def test_parent_exit_with_descendant_holding_stdout_is_bounded_and_cleaned(self):
        started = self.home / "descendant-started"
        leaked = self.home / "descendant-leaked"
        child = ("from pathlib import Path; import time; "
                 f"Path({str(started)!r}).write_text('started'); "
                 f"time.sleep(1.8); Path({str(leaked)!r}).write_text('leaked')")
        parent = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child!r}])"
        before = time.monotonic()
        result = H["probe_stdio"]({"command": sys.executable, "args": ["-c", parent], "env": {}}, 1)
        self.assertLess(time.monotonic() - before, 3)
        self.assertEqual(result["state"], "failed")
        self.assertTrue(started.is_file(), "fixture must actually start the stdout-holding descendant")
        time.sleep(1.9)
        self.assertFalse(leaked.exists(), "the descendant must not survive diagnostic cleanup")


if __name__ == "__main__":
    unittest.main()
