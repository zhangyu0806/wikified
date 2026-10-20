import importlib.machinery
import importlib.util
import json
import os
import runpy
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('worker', str(REPO / 'bin/llm-wiki-capture-worker'))
spec = importlib.util.spec_from_loader(loader.name, loader); worker = importlib.util.module_from_spec(spec); loader.exec_module(worker)

class CaptureTests(unittest.TestCase):
    def test_retry_pending_scope_and_redaction(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); vault = root / 'vault'; queue = root / 'queue'
            (vault / 'policy').mkdir(parents=True); queue.mkdir()
            shutil.copy(REPO / 'templates/access-policy.json', vault / 'policy/access.json')
            sensitive_value = 'synthetic-private-value'
            job = {'schema':1,'key':'a'*64,'profile':'codex','project':'fixture','sessionRef':'b'*64,
                   'title':'Synthetic capture','excerpt':'A synthetic implementation completed; ' + 'token' + '=' + sensitive_value}
            path = queue / (job['key']+'.json'); path.write_text(json.dumps(job))
            config = {'root':str(vault),'queue':str(queue),'projects':['fixture']}
            self.assertEqual(worker.consume(config)['saved'],1)
            # Lost acknowledgment: same job reappears, one canonical event survives.
            path.write_text(json.dumps(job)); self.assertEqual(worker.consume(config)['saved'],1)
            rows = [json.loads(line) for p in (vault/'memory/events').glob('*.jsonl') for line in p.read_text().splitlines()]
            self.assertEqual(len(rows),1); self.assertEqual(rows[0]['actor'],{'type':'ai','id':'codex'})
            self.assertEqual(rows[0]['review_status'],'pending'); self.assertEqual(rows[0]['target_agents'],['coding'])
            self.assertEqual(rows[0]['domain'],'work')
            self.assertNotIn('synthetic-private-value', json.dumps(rows))
            job['excerpt']='Conflicting replacement'; path.write_text(json.dumps(job))
            self.assertEqual(worker.consume(config)['failed'],1); self.assertTrue(path.exists())
            job['key']='c'*64; job['project']='outside'; path = queue/(job['key']+'.json'); path.write_text(json.dumps(job))
            self.assertGreaterEqual(worker.consume(config)['failed'],1)

    def test_general_project_requires_worker_authorization_and_uses_trusted_domain(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); vault = root / 'vault'; queue = root / 'queue'
            (vault / 'policy').mkdir(parents=True); queue.mkdir()
            shutil.copy(REPO / 'templates/access-policy.json', vault / 'policy/access.json')
            job = {'schema':1,'key':'a'*64,'profile':'codex','project':'codex-general','sessionRef':'b'*64,
                   'title':'Synthetic personal schedule','excerpt':'Synthetic outlet enabled daily from 08:00 until 15:00.'}
            path = queue / (job['key']+'.json'); path.write_text(json.dumps(job))
            config = {'root':str(vault),'queue':str(queue),'projects':['fixture']}
            self.assertEqual(worker.consume(config)['failed'],1)
            self.assertTrue(path.exists())
            config['projects'].append('codex-general')
            config['projectDomains'] = {'codex-general':'personal'}
            self.assertEqual(worker.consume(config)['saved'],1)
            rows = [json.loads(line) for p in (vault/'memory/events').glob('*.jsonl') for line in p.read_text().splitlines()]
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['domain'],'personal')
            self.assertEqual(rows[0]['review_status'],'pending')
            self.assertEqual(rows[0]['epistemic_status'],'ai-proposed')
            self.assertEqual(rows[0]['target_agents'],['coding'])
            self.assertEqual(rows[0]['sensitivity'],'internal')
            enrich = runpy.run_path(str(REPO / 'bin/llm-wiki-enrich'))
            _, governance = enrich['event_metadata'](rows[0])
            for profile in ('coding', 'codex'):
                access = enrich['access_context'](vault, profile)
                self.assertFalse(enrich['is_authorized'](governance, access))
                self.assertFalse(enrich['is_authorized']({**governance, 'review_status':'approved'}, access))
            self.assertTrue(enrich['is_authorized'](governance, enrich['access_context'](vault, 'human')))
            # Queue content cannot choose or override the configured domain.
            job['key']='c'*64; job['domain']='work'
            path = queue / (job['key']+'.json'); path.write_text(json.dumps(job))
            self.assertEqual(worker.consume(config)['failed'],1)
            self.assertTrue(path.exists())

    def test_domain_configuration_is_validated_before_consuming_jobs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); vault = root / 'vault'; queue = root / 'queue'
            (vault / 'policy').mkdir(parents=True); queue.mkdir()
            shutil.copy(REPO / 'templates/access-policy.json', vault / 'policy/access.json')
            config = {'root':str(vault),'queue':str(queue),'projects':['codex-general']}
            for mapping in (None, [], {'outside':'personal'}, {'codex-general':'unknown'}, {'codex-general':['personal']}):
                with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                    worker.consume({**config, 'projectDomains':mapping})
            self.assertFalse((vault/'memory').exists())

if __name__ == '__main__': unittest.main()
