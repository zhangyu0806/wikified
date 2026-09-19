import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('mirror', str(REPO / 'bin/llm-wiki-auto-commit'))
spec = importlib.util.spec_from_loader(loader.name, loader)
mirror = importlib.util.module_from_spec(spec); loader.exec_module(mirror)
REAL_RUN = subprocess.run

class MirrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.base = Path(self.temp.name)
        self.root = self.base / 'vault'; self.remote = self.base / 'remote.git'
        self.root.mkdir(); self.private = True; self.scan_ok = True; self.calls = []
        self.git('init', '-q', '-b', 'main'); self.git('config', 'user.name', 'Synthetic')
        self.git('config', 'user.email', 'synthetic@example.invalid')
        (self.root / 'memory/events').mkdir(parents=True)
        (self.root / 'memory/events/test.jsonl').write_text('{}\n')
        self.git('add', '.'); self.git('commit', '-qm', 'fixture')
        REAL_RUN(['git', 'init', '--bare', '-q', str(self.remote)], check=True)
        self.git('remote', 'add', 'origin', str(self.remote)); self.git('push', '-q', 'origin', 'main')
        self.config = {'root': str(self.root), 'repository': 'synthetic/private', 'branch': 'main',
                       'role': 'writer', 'enabled': True, 'statusFile': str(self.base / 'status/mirror.json')}
    def tearDown(self): self.temp.cleanup()
    def git(self, *args):
        return REAL_RUN(['git', '-C', str(self.root), *args], check=True, capture_output=True, text=True).stdout.strip()
    def mock_command(self, args, **kwargs):
        self.calls.append(args)
        if args[:2] == ['gh', 'api']:
            return subprocess.CompletedProcess(args, 0, json.dumps({'private': self.private, 'full_name': 'synthetic/private'}), '')
        if args[3:] == ['remote', 'get-url', 'origin']:
            return subprocess.CompletedProcess(args, 0, 'https://github.com/synthetic/private.git', '')
        if args[0].endswith('/llm-wiki-secret-scan'):
            return subprocess.CompletedProcess(args, 0 if self.scan_ok else 1, '', '')
        return REAL_RUN(args, **kwargs)
    def mirror(self):
        with patch.object(mirror.subprocess, 'run', self.mock_command): return mirror.mirror(self.config)
    def test_private_mirror_ack_and_only_memory_paths(self):
        (self.root/'memory/events/test.jsonl').write_text('{}\n{}\n')
        (self.root/'unrelated.txt').write_text('untouched')
        result = self.mirror()
        self.assertEqual(result['state'], 'synced'); self.assertEqual(result['remoteHead'], self.git('rev-parse', 'HEAD'))
        self.assertEqual(self.git('status', '--short'), '?? unrelated.txt')
        self.assertTrue(any(a[-1:] == ['--staged'] for a in self.calls))
        self.assertTrue(any(a[-1:] == ['--all'] for a in self.calls))
        self.assertEqual(self.mirror()['state'], 'synced')
    def test_public_repository_refused_before_stage(self):
        self.private = False
        (self.root/'memory/events/test.jsonl').write_text('changed')
        self.assertEqual(self.mirror()['reason'], 'remote-not-private')
        self.assertFalse(self.git('diff', '--cached', '--name-only'))
        self.assertFalse(any('push' in a for a in self.calls))
    def test_existing_stage_and_secret_failure_never_push(self):
        (self.root/'memory/events/test.jsonl').write_text('changed'); self.git('add', '.')
        self.assertEqual(self.mirror()['reason'], 'existing-staged-changes')
        self.assertTrue(self.git('diff', '--cached', '--name-only'))
        # A fresh fixture run checks the scanner failure; the test explicitly unstages its own fixture.
        self.git('restore', '--staged', 'memory/events/test.jsonl'); self.scan_ok = False
        self.assertEqual(self.mirror()['state'], 'paused')
        self.assertFalse(any('push' in a for a in self.calls))
        self.assertEqual((self.root/'memory/events/test.jsonl').read_text(), 'changed')
    def test_other_device_ahead_pauses_without_merge(self):
        before = self.git('rev-parse', 'HEAD')
        other = self.base/'home'
        REAL_RUN(['git', 'clone', '-q', '-b', 'main', str(self.remote), str(other)], check=True)
        (other/'home-note.md').write_text('synthetic home note')
        REAL_RUN(['git', '-C', str(other), 'add', '.'], check=True)
        REAL_RUN(['git', '-C', str(other), '-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid', 'commit', '-qm', 'home'], check=True)
        REAL_RUN(['git', '-C', str(other), 'push', '-q'], check=True)
        self.assertEqual(self.mirror()['reason'], 'remote-ahead-or-diverged')
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)
        self.assertFalse(any('push' in a for a in self.calls))

if __name__ == '__main__': unittest.main()
