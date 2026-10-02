import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { enqueue, excerpt, writeHookStatus } from '../bin/llm-wiki-capture-hook.mjs';

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
  fs.writeFileSync(transcript, JSON.stringify({type:'session_meta',payload:{id:'test'}})+'\n'+JSON.stringify({type:'response_item',payload:{type:'message',role:'user',content:[{type:'input_text',text:'NEVER_PERSIST_USER_TRANSCRIPT'}]}})+'\n'+
    JSON.stringify({type:'response_item',payload:{type:'message',role:'assistant',phase:'final',content:[{type:'output_text',text:'Synthetic handoff output. '.repeat(8)}]}})+'\n');
  const config = {enabled:true,profile:'codex',queue:path.join(root,'queue'),workspaces:[{path:root,project:'fixture'}],transcriptRoots:[root]};
  const result = enqueue({cwd:root,session_id:'test',hook_event_name:'SessionEnd',transcript_path:transcript},config);
  assert.equal(result.state,'queued');
  assert(!fs.readFileSync(path.join(config.queue,result.key+'.json'),'utf8').includes('NEVER_PERSIST'));
});


function fixture(t, rows = []) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wikified-current-capture-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const transcript = path.join(root, 'host.jsonl');
  const config = { enabled: true, profile: 'codex', queue: path.join(root, 'queue'),
    workspaces: [{ path: root, project: 'fixture' }], transcriptRoots: [root] };
  const payload = { cwd: root, session_id: 'fixture-session', turn_id: 'current-turn',
    hook_event_name: 'PreCompact', transcript_path: transcript };
  function write(extra, session = 'fixture-session') {
    fs.writeFileSync(transcript, [
      { type: 'session_meta', payload: { id: session } },
      { type: 'event_msg', payload: { type: 'task_started', turn_id: 'current-turn' } },
      ...extra,
    ].map(row => JSON.stringify(row)).join('\n') + '\n');
  }
  write(rows);
  return { root, transcript, config, payload, write };
}
const syntheticFinal = 'Synthetic final handoff. The implementation has completed and remains an AI proposal awaiting a separate human review.';

function response(text = syntheticFinal, phase = 'final_answer') {
  return { type: 'response_item', payload: { type: 'message', role: 'assistant', phase,
    content: [{ type: 'output_text', text }] } };
}
function completed(text = syntheticFinal, turn = 'current-turn', thread = 'fixture-session') {
  return { type: 'event_msg', payload: { type: 'item_completed', turn_id: turn, thread_id: thread,
    item: { type: 'AgentMessage', phase: 'final_answer', content: [{ type: 'Text', text }] } } };
}

test('current observed final_answer formats and legacy final all select bounded output', t => {
  const cases = [
    [response(), 'transcript-response'],
    [response(syntheticFinal, 'final'), 'transcript-response'],
    [completed(), 'transcript-item-completed'],
    [{ type: 'event_msg', payload: { type: 'task_complete', turn_id: 'current-turn',
      last_agent_message: syntheticFinal } }, 'transcript-task-complete'],
    [{ type: 'event_msg', payload: { type: 'agent_message', phase: 'final',
      message: syntheticFinal } }, 'transcript-agent-message'],
  ];
  for (const [row, source] of cases) {
    const f = fixture(t, [row]);
    const result = enqueue(f.payload, f.config);
    assert.equal(result.state, 'queued');
    assert.equal(result.selectionSource, source);
    const job = JSON.parse(fs.readFileSync(path.join(f.config.queue, result.key + '.json')));
    assert.equal(job.excerpt, syntheticFinal);
    assert.equal(job.sessionRef.length, 64);
    assert.deepEqual(Object.keys(job).sort(), ['schema', 'key', 'profile', 'project', 'sessionRef', 'title', 'excerpt'].sort());
  }
});

test('Stop with absent direct text uses only the requested current turn and deduplicates', t => {
  const f = fixture(t, [completed()]);
  const payload = { ...f.payload, hook_event_name: 'Stop', last_assistant_message: null };
  const first = enqueue(payload, f.config);
  assert.equal(first.state, 'queued');
  assert.equal(enqueue({ ...payload, last_assistant_message: '' }, f.config).key, first.key);
  fs.renameSync(path.join(f.config.queue, first.key + '.json'), path.join(f.config.queue, first.key + '.done'));
  assert.equal(enqueue(payload, f.config).state, 'already-saved');
  assert.equal(enqueue({ ...payload, turn_id: 'different-turn' }, f.config).state, 'no-selected-output');
  assert.equal(enqueue({ ...payload, turn_id: undefined }, f.config).state, 'no-selected-output');
});

test('new incomplete or interrupted turn never falls back to the previous final', t => {
  const f = fixture(t, [
    completed(),
    { type: 'event_msg', payload: { type: 'task_started', turn_id: 'new-turn' } },
    response('NEVER_PERSIST_COMMENTARY'.repeat(8), 'commentary'),
    { type: 'event_msg', payload: { type: 'task_complete', turn_id: 'new-turn', last_agent_message: null } },
  ]);
  for (const event of ['Stop', 'Interrupt', 'PreCompact', 'SessionEnd']) {
    assert.equal(enqueue({ ...f.payload, hook_event_name: event, turn_id: 'new-turn' }, f.config).state, 'no-selected-output');
  }
  assert.equal(enqueue({ ...f.payload, hook_event_name: 'SessionEnd', turn_id: undefined }, f.config).state, 'no-selected-output');
  assert(!fs.existsSync(f.config.queue));
});

test('turn_context associates response messages with their actual turn', t => {
  const f = fixture(t, [
    { type: 'turn_context', payload: { turn_id: 'different-turn' } },
    response('NEVER_PERSIST_OTHER_TURN'.repeat(8)),
    { type: 'turn_context', payload: { turn_id: 'current-turn' } },
    response(),
  ]);
  const result = enqueue(f.payload, f.config);
  assert.equal(result.state, 'queued');
  const job = fs.readFileSync(path.join(f.config.queue, result.key + '.json'), 'utf8');
  assert(!job.includes('NEVER_PERSIST_OTHER_TURN'));
});

test('wrong session, wrong wrapper thread, user messages and commentary never become final memory', t => {
  const f = fixture(t, [completed()]);
  f.write([completed()], 'another-session');
  assert.equal(enqueue(f.payload, f.config).state, 'no-selected-output');
  f.write([
    completed('NEVER_PERSIST_OTHER_THREAD'.repeat(8), 'current-turn', 'another-session'),
    { type: 'response_item', payload: { type: 'message', role: 'user', phase: 'final_answer',
      content: [{ type: 'output_text', text: 'NEVER_PERSIST_USER'.repeat(10) }] } },
    response('NEVER_PERSIST_COMMENTARY'.repeat(8), 'commentary'),
    { type: 'event_msg', payload: { type: 'agent_message', phase: 'analysis', message: 'NEVER_PERSIST_ANALYSIS'.repeat(8) } },
    { type: 'event_msg', payload: { type: 'item_completed', thread_id: 'fixture-session', turn_id: 'current-turn',
      item: { type: 'Reasoning', phase: 'final_answer', content: [{ type: 'Text', text: 'NEVER_PERSIST_REASONING'.repeat(8) }] } } },
  ]);
  assert.equal(enqueue(f.payload, f.config).state, 'no-selected-output');
  assert(!fs.existsSync(f.config.queue));
});

test('direct nonempty output retains precedence and a short current message cannot reuse old transcript text', t => {
  const f = fixture(t, [completed()]);
  assert.equal(enqueue({ ...f.payload, last_assistant_message: 'OK' }, f.config).state, 'no-selected-output');
  const result = enqueue({ ...f.payload, last_assistant_message: syntheticFinal + ' Direct.' }, f.config);
  assert.equal(result.selectionSource, 'direct');
});

test('fallback only reads allowlisted real transcript roots and requires a bounded matching header', t => {
  const f = fixture(t, [completed()]);
  const other = fs.mkdtempSync(path.join(os.tmpdir(), 'wikified-outside-transcript-'));
  t.after(() => fs.rmSync(other, { recursive: true, force: true }));
  const outside = path.join(other, 'outside.jsonl');
  fs.copyFileSync(f.transcript, outside);
  assert.equal(enqueue({ ...f.payload, transcript_path: outside }, f.config).state, 'no-selected-output');
  const link = path.join(f.root, 'outside-link.jsonl');
  try {
    fs.symlinkSync(outside, link);
    assert.equal(enqueue({ ...f.payload, transcript_path: link }, f.config).state, 'no-selected-output');
  } catch (error) {
    if (error.code !== 'EPERM') throw error; // Windows may not permit a synthetic symlink.
  }
  fs.writeFileSync(f.transcript, JSON.stringify(completed()) + '\n');
  assert.equal(enqueue(f.payload, f.config).state, 'no-selected-output');
  fs.writeFileSync(f.transcript, JSON.stringify({ type: 'session_meta', payload: { id: 'fixture-session', padding: 'x'.repeat(65536) } }) + '\n' + JSON.stringify(completed()) + '\n');
  assert.equal(enqueue(f.payload, f.config).state, 'no-selected-output');
});

test('bounded tail cannot recover a final outside 2 MiB and ignores incomplete trailing JSON', t => {
  const f = fixture(t, [completed(), { type: 'unrecognized', payload: 'x'.repeat(2 * 1024 * 1024) }]);
  assert.equal(enqueue(f.payload, f.config).state, 'no-selected-output');
  f.write([completed()]);
  fs.appendFileSync(f.transcript, '{"incomplete":');
  assert.equal(enqueue(f.payload, f.config).state, 'queued');
});

test('hook status is bounded metadata without transcript paths, bodies, raw IDs or injected error fields', t => {
  const f = fixture(t);
  writeHookStatus(f.config, { ...f.payload, session_id: 'PRIVATE_SESSION_ID' }, {
    state: 'no-selected-output', message: 'PRIVATE_MESSAGE', error: 'PRIVATE_ERROR',
    selectionSource: 'private-unrecognized-source', key: 'PRIVATE_KEY',
  });
  const file = path.join(f.config.queue, 'hook-status.json');
  const first = JSON.parse(fs.readFileSync(file));
  assert.equal(first.state, 'no-selected-output');
  assert.equal(first.event, 'PreCompact');
  assert.equal(first.sessionRef.length, 64);
  assert.deepEqual(Object.keys(first).sort(), ['schema', 'checkedAt', 'state', 'event', 'sessionRef'].sort());
  const text = JSON.stringify(first);
  assert(!text.includes('PRIVATE_')); assert(!text.includes(f.root)); assert(!text.includes('transcript'));
  writeHookStatus(f.config, { hook_event_name: 'SECRET_EVENT' }, { state: 'SECRET_STATE' });
  assert.equal(JSON.parse(fs.readFileSync(file)).state, 'error');
  assert.equal(JSON.parse(fs.readFileSync(file)).event, 'unknown');
  assert.equal(fs.readdirSync(f.config.queue).length, 1);
});

test('CLI records quiet skip and parse error metadata while returning valid safe hook JSON', t => {
  const f = fixture(t, [completed()]);
  const configPath = path.join(f.root, 'config.json');
  fs.writeFileSync(configPath, JSON.stringify(f.config));
  const script = fileURLToPath(new URL('../bin/llm-wiki-capture-hook.mjs', import.meta.url));
  const run = input => spawnSync(process.execPath, [script, configPath], { input, encoding: 'utf8' });
  const skipped = run(JSON.stringify({ ...f.payload, last_assistant_message: 'OK' }));
  assert.equal(skipped.status, 0);
  assert.deepEqual(JSON.parse(skipped.stdout), {});
  assert.equal(JSON.parse(fs.readFileSync(path.join(f.config.queue, 'hook-status.json'))).state, 'no-selected-output');
  const failed = run('{PRIVATE_MALFORMED');
  assert.equal(failed.status, 0);
  assert.equal(typeof JSON.parse(failed.stdout).systemMessage, 'string');
  assert(!failed.stdout.includes('PRIVATE_MALFORMED'));
  assert(!failed.stderr.includes('PRIVATE_MALFORMED'));
  assert.equal(JSON.parse(fs.readFileSync(path.join(f.config.queue, 'hook-status.json'))).state, 'error');
});


test('stale earlier workspace bindings do not hide a valid scoped workspace', t => {
  const f = fixture(t, [completed()]);
  f.config.workspaces.unshift({ path: path.join(f.root, 'not-present'), project: 'stale' });
  assert.equal(enqueue(f.payload, f.config).state, 'queued');
});

test('fallback rejects hardlinked transcript and a canonical path replaced while open', t => {
  const f = fixture(t, [completed()]);
  const link = path.join(f.root, 'linked.jsonl');
  fs.linkSync(f.transcript, link);
  assert.equal(enqueue(f.payload, f.config).state, 'no-selected-output');
  fs.unlinkSync(link);
  const original = fs.openSync;
  let replaced = false;
  fs.openSync = function(file, ...args) {
    const fd = original.call(this, file, ...args);
    if (file === f.transcript && !replaced) {
      replaced = true;
      fs.renameSync(f.transcript, path.join(f.root, 'original-open.jsonl'));
      fs.writeFileSync(f.transcript, 'replacement');
    }
    return fd;
  };
  try {
    assert.equal(enqueue(f.payload, f.config).state, 'no-selected-output');
  } finally {
    fs.openSync = original;
  }
  assert(!fs.existsSync(f.config.queue));
});
