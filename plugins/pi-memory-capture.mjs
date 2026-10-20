import { realpathSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

/** Pi 1.2 extension: only the final settled boundary consumes this run's bounded output. */
export default async function PiMemoryCapture(pi) {
  // Pi's TS loader resolves static imports from the .ts symlink, not its target.
  // Resolve this installation first so a user-directory link keeps snapshot-relative imports.
  const source = realpathSync(fileURLToPath(import.meta.url));
  const { captureSink, selectedText, validId } = await import(pathToFileURL(join(dirname(source), 'capture-runtime.mjs')).href);
  function contextIdentity(ctx) {
    const session = ctx?.sessionManager?.getSessionId();
    return validId(session) && typeof ctx?.cwd === 'string' ? { session, cwd: ctx.cwd } : null;
  }
  const matches = (run, identity) => !!run && !!identity && run.session === identity.session && run.cwd === identity.cwd;
  const payloadFor = (identity, event = 'Stop') => ({ hook_event_name: event, cwd: identity?.cwd, session_id: identity?.session });
  const sink = captureSink('pi');
  let run = null;
  const guarded = callback => (event, ctx) => {
    try { callback(event, ctx); }
    catch {
      run = null;
      sink.record({ hook_event_name: 'Stop' }, 'error');
    }
  };
  pi.on('session_start', () => { run = null; });
  pi.on('session_shutdown', () => { run = null; });
  pi.on('agent_start', guarded((_event, ctx) => {
    const identity = contextIdentity(ctx);
    run = identity ? { ...identity, selected: null } : null;
  }));
  pi.on('message_end', guarded((event, ctx) => {
    const identity = contextIdentity(ctx);
    if (!matches(run, identity)) { run = null; return; }
    // A later message, retry or failure must invalidate any earlier completed candidate.
    run.selected = null;
    const message = event?.message;
    if (!sink.enabled() || message?.role !== 'assistant' || message.stopReason !== 'stop' || message.errorMessage) return;
    run.selected = selectedText(message.content);
  }));
  pi.on('session_before_compact', () => { if (run) run.selected = null; });
  pi.on('agent_settled', guarded((event, ctx) => {
    const current = run;
    run = null; // Consume once, including aborts; late duplicate events cannot reuse old output.
    const identity = contextIdentity(ctx);
    if (!matches(current, identity)) return;
    const payload = payloadFor(identity, event?.aborted === true ? 'Interrupt' : 'Stop');
    if (typeof event?.aborted !== 'boolean') { sink.record(payload, 'invalid-context'); return; }
    sink.record({ ...payload, last_assistant_message: event.aborted ? '' : current.selected ?? '' });
  }));
}
