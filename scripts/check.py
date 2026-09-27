#!/usr/bin/env python3
"""Run every public contract suite with disposable user/configuration paths."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUNNERS = {".sh": ["bash"], ".py": [sys.executable, "-B"],
           ".mjs": ["node", "--test"], ".js": ["node", "--test"]}


def discover_tests(root: Path) -> list[tuple[str, list[str]]]:
    suites = []
    for path in sorted((root / "tests").glob("test-*")):
        if not path.is_file():
            continue
        if path.suffix not in RUNNERS:
            raise ValueError(f"Unsupported test suite: {path.name}")
        suites.append((path.name, [*RUNNERS[path.suffix], str(path)]))
    if not suites:
        raise ValueError("No test suites discovered")
    return suites


def isolated_environment(work: Path, parent: dict[str, str]) -> dict[str, str]:
    # Do not inherit live vault, harness, Git, proxy or credential configuration.
    env = {"PATH": parent.get("PATH", os.defpath), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
           "PYTHONDONTWRITEBYTECODE": "1", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0",
           "LLM_WIKI_DISABLE_PATH_DETECTION": "1"}
    for key, name in {"HOME": "home", "TMPDIR": "tmp", "XDG_CONFIG_HOME": "config",
                      "XDG_CACHE_HOME": "cache", "XDG_STATE_HOME": "state",
                      "XDG_DATA_HOME": "data"}.items():
        target = work / name
        target.mkdir(parents=True, exist_ok=True)
        env[key] = str(target)
    return env


def run_command(label: str, command: list[str], root: Path, env: dict[str, str], timeout: int) -> bool:
    print(f"[RUN] {label}", flush=True)
    with subprocess.Popen(command, cwd=root, env=env, start_new_session=True) as process:
        try:
            result = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Stop descendants too; a timed-out fixture must not leave a server running.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            print(f"[FAIL] {label}: exceeded {timeout}s", flush=True)
            return False
    print(f"[{'PASS' if result == 0 else 'FAIL'}] {label}: exit {result}", flush=True)
    return result == 0


def syntax_checks(root: Path, env: dict[str, str], timeout: int) -> bool:
    paths = [root / "install.sh", root / ".githooks/pre-commit"]
    for folder in ("bin", "plugins", "tests", "scripts"):
        paths.extend(path for path in (root / folder).glob("*") if path.is_file())
    for path in sorted(paths):
        text = path.read_text(encoding="utf-8")
        first = text.splitlines()[0] if text else ""
        relative = str(path.relative_to(root))
        if path.suffix == ".py" or (first.startswith("#!") and "python" in first):
            compile(text, relative, "exec")  # Syntax only; never write __pycache__ in source.
        elif path.suffix == ".sh" or (first.startswith("#!") and "bash" in first):
            if not run_command("syntax " + relative, ["bash", "-n", str(path)], root, env, timeout):
                return False
        elif path.suffix in (".js", ".mjs") or (first.startswith("#!") and "node" in first):
            if not run_command("syntax " + relative, ["node", "--check", str(path)], root, env, timeout):
                return False
    print("[PASS] Python syntax (no bytecode written)", flush=True)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="list discovered suites without running them")
    parser.add_argument("--timeout", type=int, default=120, help="maximum seconds per command (default: 120)")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    suites = discover_tests(ROOT)
    if args.list:
        for label, _ in suites:
            print(label)
        return 0
    with tempfile.TemporaryDirectory(prefix="wikified-check-") as directory:
        env = isolated_environment(Path(directory), dict(os.environ))
        if not syntax_checks(ROOT, env, args.timeout):
            return 1
        for label, command in suites:
            if not run_command(label, command, ROOT, env, args.timeout):
                return 1
        scanner = [sys.executable, "-B", str(ROOT / "bin/llm-wiki-secret-scan"), "--root", str(ROOT), "--all"]
        if not run_command("public repository secret scan", scanner, ROOT, env, args.timeout):
            return 1
    print(f"[PASS] {len(suites)} suites, syntax checks and public repository secret scan", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
