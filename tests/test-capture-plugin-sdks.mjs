/**
 * Optional installed-SDK acceptance; supply explicit local package roots:
 * LLM_WIKI_TEST_PI_PACKAGE_ROOT and LLM_WIKI_TEST_OPENCODE_SDK_ROOT.
 * Uses disposable queues, an empty project/agent directory and no model/network.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const script = fileURLToPath(import.meta.url);
const pluginRoot = path.resolve(path.dirname(script), '../plugins');
const output = 'Synthetic installed SDK capture result with no model call and no real memory access.';

async function worker(spec) {
  const jobs = () => fs.existsSync(spec.queue) ? fs.readdirSync(spec.queue).filter(name => /^[a-f0-9]{64}\.json$/.test(name))
    .map(name => JSON.parse(fs.readFileSync(path.join(spec.queue, name)))) : [];
  if (spec.profile === 'pi') {
    const metadata = JSON.parse(fs.readFileSync(path.join(spec.packageRoot, 'package.json')));
    assert.equal(metadata.name, '@earendil-works/pi-coding-agent');
    assert.equal(metadata.version, '1.2.0');
    const { DefaultResourceLoader, ExtensionRunner, SessionManager, SettingsManager } =
      await import(pathToFileURL(path.join(spec.packageRoot, 'dist/index.js')).href);
    const loader = new DefaultResourceLoader({
      cwd: spec.cwd, agentDir: spec.agentDir, settingsManager: SettingsManager.inMemory({}, { projectTrusted: false }),
      noExtensions: true, noSkills: true, noPromptTemplates: true, noThemes: true, noContextFiles: true,
      additionalExtensionPaths: [path.join(pluginRoot, 'pi-memory-capture.mjs')],
    });
    await loader.reload();
    const loaded = loader.getExtensions();
    assert.deepEqual(loaded.errors, []);
    assert.equal(loaded.extensions.length, 1);
    assert.deepEqual([...loaded.extensions[0].handlers.keys()].sort(),
      ['session_start', 'session_shutdown', 'agent_start', 'message_end', 'session_before_compact', 'agent_settled'].sort());
    const session = SessionManager.inMemory(spec.cwd);
    // The capture extension never accesses modelRegistry or model state.
    const runner = new ExtensionRunner(loaded.extensions, loaded.runtime, spec.cwd, session, undefined);
    const errors = []; runner.onError(error => errors.push(error));
    await runner.emit({ type: 'session_start', reason: 'startup' });
    await runner.emit({ type: 'agent_start' });
    const message = { role: 'assistant', content: [{ type: 'text', text: output }], stopReason: 'stop' };
    await runner.emitMessageEnd({ type: 'message_end', message });
    await runner.emit({ type: 'agent_end', messages: [message] });
    assert.deepEqual(jobs(), [], 'agent_end must not capture before retries/continuations settle');
    await runner.emit({ type: 'agent_settled', aborted: false });
    assert.equal(jobs().length, 1); assert.equal(jobs()[0].profile, 'pi');
    assert.equal(jobs()[0].excerpt, output);
    await runner.emit({ type: 'agent_start' });
    await runner.emitMessageEnd({ type: 'message_end', message: { ...message, content: [{ type: 'text', text: 'Aborted SDK result must never be saved.' }] } });
    await runner.emit({ type: 'agent_settled', aborted: true });
    assert.equal(jobs().length, 1);
    await runner.emit({ type: 'session_shutdown', reason: 'quit' });
    assert.deepEqual(errors, []); assert.deepEqual(session.getEntries(), []);
    assert.equal(session.getSessionFile(), undefined);
  } else {
    const metadata = JSON.parse(fs.readFileSync(path.join(spec.packageRoot, 'package.json')));
    assert.equal(metadata.name, '@opencode-ai/sdk');
    const { createOpencodeClient } = await import(pathToFileURL(path.join(spec.packageRoot, 'dist/client.js')).href);
    const { default: plugin } = await import(pathToFileURL(path.join(pluginRoot, 'llm-wiki-capture.js')).href);
    const info = { id: 'msg_installedsdk', sessionID: 'ses_installedsdk', role: 'assistant', finish: 'stop', time: { created: 10, completed: 20 } };
    let calls = 0;
    const client = createOpencodeClient({ baseUrl: 'http://synthetic.invalid', fetch: async request => {
      calls++; assert.equal(request.method, 'GET');
      const url = new URL(request.url);
      assert.equal(url.pathname, '/session/ses_installedsdk/message/msg_installedsdk');
      assert.equal(url.searchParams.get('directory'), spec.cwd);
      assert(request.signal instanceof AbortSignal);
      return new Response(JSON.stringify({ info, parts: [{ id: 'prt_synthetic', type: 'text', text: output,
        sessionID: info.sessionID, messageID: info.id }] }), { headers: { 'content-type': 'application/json' } });
    } });
    const hooks = await plugin({ client, directory: spec.cwd });
    await hooks.event({ event: { type: 'message.updated', properties: { info } } });
    assert.equal(calls, 1); assert.equal(jobs().length, 1);
    assert.equal(jobs()[0].profile, 'opencode'); assert.equal(jobs()[0].excerpt, output);
  }
}

if (process.argv[2] === '--worker') {
  await worker(JSON.parse(fs.readFileSync(process.argv[3], 'utf8')));
  console.log('INSTALLED_SDK_CAPTURE_PASS');
} else {
  for (const [profile, variable] of [['pi', 'LLM_WIKI_TEST_PI_PACKAGE_ROOT'], ['opencode', 'LLM_WIKI_TEST_OPENCODE_SDK_ROOT']]) {
    test(`${profile} installed SDK loads and dispatches capture using only synthetic data`, { skip: !process.env[variable] }, t => {
      const packageRoot = fs.realpathSync(process.env[variable]);
      const root = fs.mkdtempSync(path.join(os.tmpdir(), 'capture-installed-sdk-'));
      t.after(() => fs.rmSync(root, { recursive: true, force: true }));
      const spec = { profile, packageRoot, cwd: path.join(root, 'empty-project'), agentDir: path.join(root, 'agent'), queue: path.join(root, 'queue') };
      const config = path.join(root, 'capture.json'), specPath = path.join(root, 'spec.json'), emptyPath = path.join(root, 'empty-path');
      for (const directory of [spec.cwd, spec.agentDir, emptyPath]) fs.mkdirSync(directory);
      fs.writeFileSync(config, JSON.stringify({ enabled: true, profile, queue: spec.queue,
        workspaces: [{ path: spec.cwd, project: 'sdk-fixture' }] }), { mode: 0o600 });
      fs.writeFileSync(specPath, JSON.stringify(spec), { mode: 0o600 });
      const result = spawnSync(process.execPath, [script, '--worker', specPath], { cwd: spec.cwd,
        env: { PATH: emptyPath, PI_CODING_AGENT_DIR: spec.agentDir, PI_CODING_AGENT_SESSION_DIR: path.join(root, 'sessions'),
          PI_OFFLINE: '1', PI_SKIP_VERSION_CHECK: '1', PI_TELEMETRY: '0', NO_COLOR: '1',
          [`LLM_WIKI_${profile.toUpperCase()}_CAPTURE_CONFIG`]: config },
        encoding: 'utf8', timeout: 20000, maxBuffer: 1024 * 1024 });
      assert.equal(result.status, 0, result.stderr);
      assert(result.stdout.includes('INSTALLED_SDK_CAPTURE_PASS'));
    });
  }
}
