import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { enqueue, excerpt } from '../bin/llm-wiki-capture-hook.mjs';

test('scope, minimal output, secret removal and duplicate enqueue', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wikified-capture-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const workspace = path.join(root, 'work'); fs.mkdirSync(workspace);
  const queue = path.join(root, 'queue');
  const config = { enabled: true, profile: 'codex', queue, workspaces: [{ path: workspace, project: 'synthetic' }] };
  const payload = { cwd: workspace, session_id: 'synthetic', turn_id: 'turn1', hook_event_name: 'Stop',
    last_assistant_message: 'Completed a synthetic memory capture implementation. The result is still pending human review.\npassword=hunter123\nAuthorization: Bearer abcdefghijklmnopqrstuvwxyz\n```\nprivate raw code\n```' };
  const first = enqueue(payload, config);
  assert.equal(first.state, 'queued');
  enqueue(payload, config);
  assert.equal(fs.readdirSync(queue).filter(f => f.endsWith('.json')).length, 1);
  const text = fs.readFileSync(path.join(queue, first.key + '.json'), 'utf8');
  assert(!text.includes('hunter123')); assert(!text.includes('abcdefghijklmnopqrstuvwxyz')); assert(!text.includes('private raw code'));
  assert.equal(enqueue({ ...payload, cwd: root }, config).state, 'out-of-scope');
  assert.equal(enqueue({ ...payload, stop_hook_active: true }, config).state, 'ignored-continuation');
  assert.equal(excerpt('OK'), null);
  assert(excerpt('Long text '.repeat(1000)).text.length <= 1800);
});
test('fallback reads bounded final output without copying user transcript', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wikified-fallback-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const transcript = path.join(root, 'host.jsonl');
  fs.writeFileSync(transcript, JSON.stringify({type:'response_item',payload:{type:'message',role:'user',content:[{type:'input_text',text:'NEVER_PERSIST_USER_TRANSCRIPT'}]}})+'\n'+
    JSON.stringify({type:'response_item',payload:{type:'message',role:'assistant',phase:'final',content:[{type:'output_text',text:'Synthetic handoff output. '.repeat(8)}]}})+'\n');
  const config = {enabled:true,profile:'codex',queue:path.join(root,'queue'),workspaces:[{path:root,project:'fixture'}],transcriptRoots:[root]};
  const result = enqueue({cwd:root,session_id:'test',hook_event_name:'SessionEnd',transcript_path:transcript},config);
  assert.equal(result.state,'queued');
  assert(!fs.readFileSync(path.join(config.queue,result.key+'.json'),'utf8').includes('NEVER_PERSIST'));
});
