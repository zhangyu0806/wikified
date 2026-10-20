import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { hash } from '../bin/llm-wiki-capture-hook.mjs';
import OpenCodeCapture from '../plugins/llm-wiki-capture.js';
import PiCapture from '../plugins/pi-memory-capture.mjs';

const output = 'Completed the synthetic task and verified the requested setting. This proposed memory still requires independent review.';
function fixture(t, profile) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), `capture-plugin-${profile}-`));
  const queue = path.join(root, 'queue'), file = path.join(root, 'config.json');
  const env = `LLM_WIKI_${profile.toUpperCase()}_CAPTURE_CONFIG`, previous = process.env[env];
  process.env[env] = file;
  const config = { enabled: true, profile, queue, workspaces: [{ path: root, project: 'synthetic' }] };
  const write = (value = config) => fs.writeFileSync(file, JSON.stringify(value));
  write();
  t.after(() => {
    if (previous === undefined) delete process.env[env]; else process.env[env] = previous;
    fs.rmSync(root, { recursive: true, force: true });
  });
  return { root, queue, file, config, write,
    jobs: () => fs.existsSync(queue) ? fs.readdirSync(queue).filter(name => /^[a-f0-9]{64}\.json$/.test(name)).map(name => JSON.parse(fs.readFileSync(path.join(queue, name)))) : [],
    status: () => JSON.parse(fs.readFileSync(path.join(queue, `hook-status-${profile}.json`))),
  };
}
function info(sessionID = 'ses_synthetic', id = 'msg_synthetic', fields = {}) {
  return { role: 'assistant', sessionID, id, time: { created: 10, completed: 20 }, finish: 'stop', ...fields };
}
const update = value => ({ event: { type: 'message.updated', properties: { info: value } } });
const textPart = (text = output, message = info(), fields = {}) => ({ type: 'text', text,
  sessionID: message.sessionID, messageID: message.id, ...fields });
async function openCode(f, read = async () => ({ data: { info: info(), parts: [textPart()] } })) {
  return await OpenCodeCapture({ directory: f.root, client: { session: { message: read } } });
}
function pi(f) {
  const handlers = new Map();
  PiCapture({ on: (name, callback) => { assert(!handlers.has(name)); handlers.set(name, callback); } });
  const ctx = (session = 'pi_session', cwd = f.root) => ({ cwd, sessionManager: { getSessionId: () => session } });
  const emit = (name, event = {}, context = ctx()) => handlers.get(name)?.(event, context);
  const final = (text = output, extra = {}) => ({ message: { role: 'assistant', stopReason: 'stop', content: [{ type: 'text', text }], ...extra } });
  return { emit, ctx, final, handlers };
}

test('OpenCode fetches only the completed message and persists selected redacted text under its own identity', async t => {
  const f = fixture(t, 'opencode'), message = info();
  const secret = 'ghp_' + 'S'.repeat(32);
  let calls = 0;
  const hooks = await openCode(f, async request => {
    calls++;
    assert.deepEqual(request.path, { id: message.sessionID, messageID: message.id });
    assert.deepEqual(request.query, { directory: f.root });
    assert(request.signal instanceof AbortSignal);
    return { data: { info: message, parts: [
      textPart(output + '\npassword=' + secret + '\n```js\nRAW_CODE_MUST_NOT_SAVE\n```'),
      textPart('SYNTHETIC_MUST_NOT_SAVE', message, { synthetic: true }),
      textPart('IGNORED_MUST_NOT_SAVE', message, { ignored: true }),
      textPart('REASONING_MUST_NOT_SAVE', message, { type: 'reasoning' }),
      textPart('TOOL_MUST_NOT_SAVE', message, { type: 'tool' }),
      textPart('OTHER_SESSION_MUST_NOT_SAVE', info('ses_other')),
      textPart('OTHER_MESSAGE_MUST_NOT_SAVE', info(message.sessionID, 'msg_other')),
    ] } };
  });
  await hooks.event(update(message));
  await hooks.event(update(message));
  const jobs = f.jobs(); assert.equal(jobs.length, 1); assert.equal(calls, 2);
  assert.equal(jobs[0].profile, 'opencode'); assert.equal(jobs[0].sessionRef, hash(message.sessionID));
  assert(jobs[0].excerpt.includes(output)); assert(!jobs[0].excerpt.includes('password='));
  const saved = JSON.stringify(jobs);
  for (const value of [secret, 'MUST_NOT_SAVE', 'RAW_CODE', message.sessionID, message.id]) assert(!saved.includes(value));
  assert.equal(f.status().state, 'queued');
});

test('OpenCode ignores streaming, summaries, tools, aborted, error and unknown events before reading messages', async t => {
  const f = fixture(t, 'opencode');
  let calls = 0;
  const hooks = await openCode(f, async () => { calls++; throw new Error('must not run'); });
  for (const fields of [{ role: 'user' }, { role: 'unknown' }, { finish: 'tool-calls' }, { finish: 'length' },
    { finish: 'aborted' }, { error: { message: 'PRIVATE_ERROR' } }, { summary: true }, { time: { created: 10 } },
    { time: { completed: Infinity } }, { time: { completed: 0 } }]) await hooks.event(update(info('ses_synthetic', 'msg_synthetic', fields)));
  await hooks.event({ event: { type: 'session.idle', properties: { sessionID: 'ses_synthetic' } } });
  await hooks.event(); await hooks.event(null); await hooks.event({ event: { type: 'unknown' } });
  assert.equal(calls, 0); assert.deepEqual(f.jobs(), []); assert(!fs.existsSync(f.queue));
});

test('OpenCode validates IDs, fetched identity and finality; no raw SDK errors escape', async t => {
  const f = fixture(t, 'opencode');
  let response, calls = 0;
  const hooks = await openCode(f, async () => { calls++; if (response instanceof Error) throw response; return response; });
  for (const bad of ['../secret', '', 'id\nsecret', 'a'.repeat(201), null]) {
    await hooks.event(update(info(bad)));
    await hooks.event(update(info('ses_synthetic', bad)));
  }
  assert.equal(calls, 0); assert.equal(f.status().state, 'invalid-context');
  for (const candidate of [
    { data: { info: info('ses_other'), parts: [textPart()] } },
    { data: { info: info('ses_synthetic', 'msg_other'), parts: [textPart()] } },
    { data: { info: info('ses_synthetic', 'msg_synthetic', { finish: 'tool-calls' }), parts: [textPart()] } },
    { error: { message: 'SECRET_ERROR_BODY' } },
    new Error('SECRET_ERROR_BODY'),
  ]) {
    response = candidate;
    await assert.doesNotReject(hooks.event(update(info())));
    assert.equal(f.status().state, 'error');
    assert(!JSON.stringify(f.status()).includes('SECRET_ERROR_BODY'));
  }
  assert.deepEqual(f.jobs(), []);
  response = { data: { info: info(), parts: [textPart()] } };
  await hooks.event(update(info()));
  assert.equal(f.jobs().length, 1, 'a transient failure must remain retryable');
});

test('OpenCode deduplicates concurrent callbacks and isolates sessions across asynchronous replies', async t => {
  const f = fixture(t, 'opencode'), replies = new Map();
  let calls = 0;
  const hooks = await openCode(f, request => {
    calls++; return new Promise(resolve => replies.set(request.path.id, resolve));
  });
  const first = hooks.event(update(info('ses_first', 'msg_first')));
  await hooks.event(update(info('ses_first', 'msg_first')));
  await hooks.event(update(info('ses_first', 'msg_first')));
  const second = hooks.event(update(info('ses_second', 'msg_second')));
  assert.equal(calls, 2);
  for (const sid of ['second', 'first']) {
    const message = info('ses_' + sid, 'msg_' + sid);
    replies.get(message.sessionID)({ data: { info: message, parts: [textPart('Synthetic result for ' + sid + ' with enough useful content.', message)] } });
  }
  await Promise.all([first, second]);
  assert.equal(f.jobs().length, 2);
  for (const sid of ['first', 'second']) {
    const job = f.jobs().find(row => row.sessionRef === hash('ses_' + sid));
    assert(job.excerpt.includes(sid)); assert(!job.excerpt.includes(sid === 'first' ? 'second' : 'first'));
  }
});

test('OpenCode bounds an unresponsive client call and reports only safe metadata', async t => {
  const f = fixture(t, 'opencode');
  let signal;
  const hooks = await openCode(f, request => { signal = request.signal; return new Promise(() => {}); });
  const started = Date.now();
  await assert.doesNotReject(hooks.event(update(info())));
  assert(Date.now() - started < 5000); assert.equal(signal.aborted, true);
  assert.equal(f.status().state, 'error'); assert.deepEqual(f.jobs(), []);
});

test('operator config is opt-in, bounded, profile-bound and rechecked after an asynchronous read', async t => {
  const f = fixture(t, 'opencode');
  let calls = 0, finish;
  const hooks = await openCode(f, () => { calls++; return new Promise(resolve => { finish = resolve; }); });
  fs.unlinkSync(f.file);
  await hooks.event(update(info())); assert.equal(calls, 0); assert(!fs.existsSync(f.queue));
  for (const contents of ['{', ' '.repeat(70000)]) {
    fs.writeFileSync(f.file, contents); await hooks.event(update(info()));
    assert.equal(calls, 0); assert(!fs.existsSync(f.queue));
  }
  f.write({ ...f.config, profile: 'codex' }); await hooks.event(update(info()));
  assert.equal(calls, 0); assert.equal(f.status().state, 'disabled');
  f.write();
  const running = hooks.event(update(info()));
  f.write({ ...f.config, enabled: false });
  finish({ data: { info: info(), parts: [textPart()] } }); await running;
  assert.deepEqual(f.jobs(), []); assert.equal(f.status().state, 'disabled');
  fs.unlinkSync(f.file);
  const target = path.join(f.root, 'config-target.json'); fs.writeFileSync(target, JSON.stringify(f.config));
  fs.symlinkSync(target, f.file);
  await hooks.event(update(info())); assert.equal(calls, 1); assert.deepEqual(f.jobs(), []);
});

test('Pi only captures a completed settled run, excludes nontext parts and consumes its bounded candidate once', t => {
  const f = fixture(t, 'pi'), host = pi(f), secret = 'github_pat_' + 'X'.repeat(40);
  host.emit('agent_start');
  host.emit('message_end', host.final(output + '\npassword=' + secret, { content: [
    { type: 'thinking', thinking: 'REASONING_MUST_NOT_SAVE' },
    { type: 'text', text: output + '\npassword=' + secret + '\n' + 'Long synthetic line '.repeat(10000) },
    { type: 'toolCall', arguments: 'TOOL_MUST_NOT_SAVE' },
    { type: 'text', text: 'SYNTHETIC_MUST_NOT_SAVE', synthetic: true },
  ] }));
  assert.deepEqual(f.jobs(), []);
  host.emit('agent_end', { messages: [] }); assert.deepEqual(f.jobs(), []);
  host.emit('agent_settled', { aborted: false });
  assert.equal(f.jobs().length, 1);
  const job = f.jobs()[0];
  assert.equal(job.profile, 'pi'); assert.equal(job.sessionRef, hash('pi_session'));
  assert([...job.excerpt].length <= 1800); assert(!job.excerpt.includes('password='));
  assert(!JSON.stringify(job).includes(secret)); assert(!JSON.stringify(job).includes('MUST_NOT_SAVE'));
  const before = fs.readFileSync(path.join(f.queue, 'hook-status-pi.json'), 'utf8');
  host.emit('agent_settled', { aborted: false });
  assert.equal(fs.readFileSync(path.join(f.queue, 'hook-status-pi.json'), 'utf8'), before);
});

test('Pi never reuses a previous run across retries, cancellation, compaction or session replacement', t => {
  const f = fixture(t, 'pi'), host = pi(f);
  const prepare = () => { host.emit('agent_start'); host.emit('message_end', host.final()); };
  prepare(); host.emit('agent_start'); host.emit('agent_settled', { aborted: false });
  assert.deepEqual(f.jobs(), []); assert.equal(f.status().state, 'no-selected-output');
  prepare(); host.emit('agent_settled', { aborted: true });
  assert.deepEqual(f.jobs(), []); assert.equal(f.status().event, 'Interrupt');
  prepare(); host.emit('session_before_compact'); host.emit('agent_settled', { aborted: false });
  assert.deepEqual(f.jobs(), []);
  prepare(); host.emit('session_shutdown'); host.emit('agent_settled', { aborted: false });
  assert.deepEqual(f.jobs(), []);
  prepare(); host.emit('session_start'); host.emit('agent_settled', { aborted: false });
  assert.deepEqual(f.jobs(), []);
  prepare(); host.emit('agent_settled', { aborted: false }, host.ctx('pi_other'));
  host.emit('agent_settled', { aborted: false }); assert.deepEqual(f.jobs(), []);
  prepare(); host.emit('message_end', host.final('Different session output must not save.'), host.ctx('pi_other'));
  host.emit('agent_settled', { aborted: false }); assert.deepEqual(f.jobs(), []);
});

test('Pi invalidates earlier finals on nonfinal messages and rejects unknown, tool and error output', t => {
  const f = fixture(t, 'pi'), host = pi(f);
  for (const message of [
    { role: 'user', stopReason: 'stop', content: [{ type: 'text', text: 'USER_MUST_NOT_SAVE' }] },
    { role: 'toolResult', content: [{ type: 'text', text: 'TOOL_MUST_NOT_SAVE' }] },
    { role: 'assistant', stopReason: 'toolUse', content: [{ type: 'text', text: 'PARTIAL_MUST_NOT_SAVE' }] },
    { role: 'assistant', stopReason: 'error', errorMessage: 'PRIVATE_ERROR', content: [{ type: 'text', text: 'FAILED_MUST_NOT_SAVE' }] },
    { role: 'assistant', stopReason: 'stop', errorMessage: 'PRIVATE_ERROR', content: [{ type: 'text', text: output }] },
    { role: 'assistant', stopReason: 'stop', content: [{ type: 'unknown', text: output }] },
  ]) {
    host.emit('agent_start'); host.emit('message_end', host.final());
    host.emit('message_end', { message }); host.emit('agent_settled', { aborted: false });
    assert.deepEqual(f.jobs(), []); assert(!JSON.stringify(f.status()).includes('MUST_NOT_SAVE'));
  }
  host.emit('message_end', host.final()); host.emit('agent_settled', { aborted: false });
  assert.deepEqual(f.jobs(), [], 'message without this run start must not be captured');
});

test('Pi handles disabled/missing config, invalid context and callback failures without interrupting the host', t => {
  const f = fixture(t, 'pi'), host = pi(f);
  fs.unlinkSync(f.file);
  host.emit('agent_start'); host.emit('message_end', host.final()); host.emit('agent_settled', { aborted: false });
  assert(!fs.existsSync(f.queue));
  f.write();
  host.emit('agent_start', {}, host.ctx('../invalid'));
  host.emit('message_end', host.final()); host.emit('agent_settled', { aborted: false });
  assert.deepEqual(f.jobs(), []);
  host.emit('agent_start'); host.emit('message_end', host.final());
  f.write({ ...f.config, enabled: false }); host.emit('agent_settled', { aborted: false });
  assert.deepEqual(f.jobs(), []); assert.equal(f.status().state, 'disabled');
  f.write();
  host.emit('agent_start');
  assert.doesNotThrow(() => host.emit('message_end', host.final(), { cwd: f.root, sessionManager: { getSessionId: () => { throw new Error('PRIVATE_ERROR'); } } }));
  assert.equal(f.status().state, 'error'); assert(!JSON.stringify(f.status()).includes('PRIVATE_ERROR'));
});

test('short concrete setting replies remain eligible in both hosts', async t => {
  const short = '已设置为每天08:00开启、15:00关闭。';
  assert(short.length < 80);
  const oc = fixture(t, 'opencode'), p = fixture(t, 'pi'), host = pi(p);
  const hooks = await openCode(oc, async () => ({ data: { info: info(), parts: [textPart(short)] } }));
  await hooks.event(update(info()));
  host.emit('agent_start'); host.emit('message_end', host.final(short)); host.emit('agent_settled', { aborted: false });
  assert.equal(oc.jobs()[0]?.excerpt, short);
  assert.equal(p.jobs()[0]?.excerpt, short);
});

test('code-only and credential-only replies create no synthetic facts after redaction', async t => {
  const oc = fixture(t, 'opencode'), p = fixture(t, 'pi'), host = pi(p);
  let text = '';
  const hooks = await openCode(oc, async () => ({ data: { info: info(), parts: [textPart(text)] } }));
  for (const value of [
    '```js\nconst privateToken = "must-not-save";\n```',
    'password=' + 'secret-fixture-value',
    'Authorization: Bearer abcdefghijklmnopqrstuvwxyz',
    'ghp_' + 'S'.repeat(32),
    ['-----BEGIN', 'PRIVATE KEY-----'].join(' ') + '\nSYNTHETIC_PRIVATE_KEY_CONTENT\n' + ['-----END', 'PRIVATE KEY-----'].join(' '),
    'Cookie: auth=synthetic-cookie-value',
  ]) {
    text = value;
    await hooks.event(update(info()));
    host.emit('agent_start'); host.emit('message_end', host.final(value)); host.emit('agent_settled', { aborted: false });
    assert.deepEqual(oc.jobs(), []); assert.deepEqual(p.jobs(), []);
    assert.equal(oc.status().state, 'no-selected-output'); assert.equal(p.status().state, 'no-selected-output');
  }
});
