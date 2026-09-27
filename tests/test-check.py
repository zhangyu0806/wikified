"""Verify suite coverage, clean environment and failure/timeout propagation."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("public_check", Path(__file__).resolve().parents[1] / "scripts/check.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class PublicCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_all_supported_suite_types_and_unknown_type(self):
        (self.root / "tests").mkdir()
        for name in ("test-alpha.sh", "test-beta.py", "test-gamma.mjs", "test-delta.js", "fixture.json"):
            (self.root / "tests" / name).touch()
        self.assertEqual(len(check.discover_tests(self.root)), 4)
        (self.root / "tests/test-unsupported.rb").touch()
        with self.assertRaises(ValueError):
            check.discover_tests(self.root)

    def test_empty_suite_set_is_failure(self):
        with self.assertRaises(ValueError):
            check.discover_tests(self.root)

    def test_environment_drops_live_configuration_and_credentials(self):
        parent = {"PATH": os.defpath, "HOME": "/synthetic/live", "LLM_WIKI_ROOT": "/synthetic/private",
                  "CODEX_HOME": "/synthetic/config", "GH_TOKEN": "synthetic-value",
                  "GIT_DIR": "/synthetic/repo", "PYTHONPATH": "/synthetic/modules"}
        env = check.isolated_environment(self.root, parent)
        for key in ("LLM_WIKI_ROOT", "GH_TOKEN", "GIT_DIR", "PYTHONPATH", "CODEX_HOME"):
            self.assertNotIn(key, env)
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
        for key in ("HOME", "TMPDIR", "XDG_CONFIG_HOME"):
            self.assertTrue(Path(env[key]).is_relative_to(self.root))

    def test_nonzero_and_timeout_fail(self):
        env = check.isolated_environment(self.root, dict(os.environ))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(check.run_command("failed", [sys.executable, "-c", "raise SystemExit(3)"], self.root, env, 5))
            self.assertFalse(check.run_command("timeout", [sys.executable, "-c", "import time; time.sleep(30)"], self.root, env, 1))


if __name__ == "__main__":
    unittest.main()
