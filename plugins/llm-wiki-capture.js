import { captureSink, selectedText, validId } from './capture-runtime.mjs';

const finalAssistant = info => info?.role === 'assistant' && info.finish === 'stop' && !info.error && !info.summary
  && Number.isFinite(info.time?.completed) && info.time.completed > 0;

/** OpenCode's host event and exact message endpoint; no prompt-driven calls or transcript fallback. */
export default async function LlmWikiCapturePlugin({ client, directory }) {
  const sink = captureSink('opencode');
  const pending = new Set();
  return {
    event: async input => {
      let payload;
      let key;
      let active = false;
      let timer;
      try {
        const event = input?.event;
        if (event?.type !== 'message.updated') return;
        const info = event.properties?.info;
        if (!finalAssistant(info)) return;
        if (!validId(info.sessionID) || !validId(info.id)) {
          sink.record({ hook_event_name: 'Stop' }, 'invalid-context');
          return;
        }
        payload = { hook_event_name: 'Stop', cwd: directory, session_id: info.sessionID, turn_id: info.id };
        if (!sink.enabled()) { sink.record(payload); return; }
        key = info.sessionID + ':' + info.id;
        if (pending.has(key)) return;
        if (pending.size >= 64) { sink.record(payload, 'error'); return; }
        pending.add(key);
        active = true;
        const controller = new AbortController();
        const timeout = new Promise((_resolve, reject) => {
          timer = setTimeout(() => { controller.abort(); reject(new Error('capture timeout')); }, 3000);
        });
        const response = await Promise.race([client.session.message({
          path: { id: info.sessionID, messageID: info.id }, query: { directory }, signal: controller.signal,
        }), timeout]);
        const message = response?.data;
        if (response?.error || !finalAssistant(message?.info) || message.info.id !== info.id || message.info.sessionID !== info.sessionID) {
          sink.record(payload, 'error');
          return;
        }
        const selected = selectedText(message.parts, part => part.sessionID === info.sessionID && part.messageID === info.id);
        sink.record({ ...payload, last_assistant_message: selected ?? '' });
      } catch { sink.record(payload ?? { hook_event_name: 'Stop' }, 'error'); }
      finally { clearTimeout(timer); if (active) pending.delete(key); }
    },
  };
}
