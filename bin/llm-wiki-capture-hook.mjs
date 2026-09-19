#!/usr/bin/env node
// Native, short, durable enqueue. No model, network, WSL launch or transcript copy.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

export const hash = value => crypto.createHash('sha256').update(value).digest('hex');
export function redact(text) {
  return text
    .replace(/-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|$)/g, '[REDACTED_PRIVATE_KEY]')
    .replace(/```[\s\S]*?(?:```|$)/g, '[代码块未自动保存]')
    .replace(/\b(?:sk-[\w-]{12,}|gh[pousr]_[\w]{16,}|github_pat_[\w]{16,}|xox[baprs]-[\w-]{16,})\b/g, '[REDACTED_KEY]')
    .replace(/\beyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}\b/g, '[REDACTED_JWT]')
    .replace(/^.*(?:authorization\s*[:=]|(?:set-)?cookie\s*[:=]).*$/gim, '[凭据行未保存]')
    .replace(/\bBearer\s+[^\s,'"<>]+/gi, 'Bearer [REDACTED]')
    .replace(/((?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|passwd|密码|密钥|验证码)["']?\s*[:=：]\s*)["']?[^\s,'"<>]+/gi, '$1[REDACTED]')
    .replace(/(https?:\/\/)[^\s/@]+:[^\s/@]+@/gi, '$1[REDACTED]@')
    .replace(/\b[A-Za-z0-9_+/=-]{64,}\b/g, '[长不透明值未保存]');
}
export function excerpt(message) {
  if (typeof message !== 'string' || message.length < 80 || message.length > 1_000_000) return null;
  const clean = redact(message).replace(/\u0000/g, '');
  const lines = clean.split(/\r?\n/).map(line => line.trim()).filter(line =>
    line.length >= 8 && !/^\[代码块/.test(line) && !/^\|[- :|]+\|$/.test(line));
  if (!lines.length) return null;
  // Selected output only: bounded first useful lines; explicitly not semantic facts.
  const selected = lines.slice(0, 8).map(line => [...line].slice(0, 360).join('')).join('\n');
  return { title: [...lines[0].replace(/^[#*\s-]+/, '')].slice(0, 72).join(''), text: [...selected].slice(0, 1800).join('') };
}
function inside(candidate, root) {
  const relative = path.relative(root, candidate);
  return relative === '' || (!relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative));
}
function latestFinal(payload, config) {
  if (typeof payload.last_assistant_message === 'string') return payload.last_assistant_message;
  if (!['PreCompact', 'SessionEnd', 'Interrupt'].includes(payload.hook_event_name)) return null;
  if (typeof payload.transcript_path !== 'string') return null;
  const source = fs.realpathSync(payload.transcript_path);
  if (!(config.transcriptRoots || []).some(root => inside(source, fs.realpathSync(root)))) return null;
  const fd = fs.openSync(source, 'r');
  try {
    const info = fs.fstatSync(fd);
    if (!info.isFile()) return null;
    const size = Math.min(info.size, 2 * 1024 * 1024), start = info.size - size;
    const buffer = Buffer.alloc(size);
    fs.readSync(fd, buffer, 0, size, start);
    const lines = buffer.toString('utf8').split('\n');
    if (start) lines.shift();
    for (const line of lines.reverse()) {
      try {
        const row = JSON.parse(line), item = row.payload;
        if (row.type === 'response_item' && item?.type === 'message' && item.role === 'assistant' && item.phase === 'final') {
          return (item.content || []).filter(c => c.type === 'output_text').map(c => c.text).join('\n');
        }
        if (row.type === 'event_msg' && item?.type === 'agent_message' && item.phase !== 'commentary') return item.message;
      } catch { /* Incomplete tail line/unknown host formats are not interpreted. */ }
    }
    return null;
  } finally { fs.closeSync(fd); }
}
export function enqueue(payload, config) {
  if (config.enabled !== true || !['codex', 'opencode'].includes(config.profile)) return { state: 'disabled' };
  if (!['Stop', 'PreCompact', 'SessionEnd', 'Interrupt'].includes(payload.hook_event_name)) return { state: 'ignored-event' };
  if (payload.stop_hook_active === true) return { state: 'ignored-continuation' };
  if (typeof payload.cwd !== 'string' || typeof payload.session_id !== 'string' || payload.session_id.length > 200) return { state: 'invalid-context' };
  const cwd = fs.realpathSync(payload.cwd);
  const workspace = config.workspaces?.find(item => inside(cwd, fs.realpathSync(item.path)));
  if (!workspace || !/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,100}$/.test(workspace.project)) return { state: 'out-of-scope' };
  const selected = excerpt(latestFinal(payload, config));
  if (!selected) return { state: 'no-selected-output' };
  const key = hash(JSON.stringify(['capture/v1', config.profile, payload.session_id, workspace.project, selected]));
  const directory = path.resolve(config.queue);
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  if (fs.lstatSync(directory).isSymbolicLink()) throw new Error('unsafe queue');
  if (fs.existsSync(path.join(directory, key + '.done'))) return { state: 'already-saved' };
  const job = { schema: 1, key, profile: config.profile, project: workspace.project,
    sessionRef: hash(payload.session_id), title: selected.title, excerpt: selected.text };
  const temp = path.join(directory, '.' + crypto.randomUUID() + '.tmp');
  const target = path.join(directory, key + '.json');
  const fd = fs.openSync(temp, 'wx', 0o600);
  try { fs.writeFileSync(fd, JSON.stringify(job) + '\n'); fs.fsyncSync(fd); }
  finally { fs.closeSync(fd); }
  // Same-key enqueue has identical content. Never replace a worker receipt.
  if (fs.existsSync(target)) fs.unlinkSync(temp); else fs.renameSync(temp, target);
  if (process.platform !== 'win32') { const dir = fs.openSync(directory, 'r'); try { fs.fsyncSync(dir); } finally { fs.closeSync(dir); } }
  return { state: 'queued', key };
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const config = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
    let input = '';
    for await (const chunk of process.stdin) { input += chunk; if (input.length > 1_100_000) throw new Error('input limit'); }
    const result = enqueue(JSON.parse(input), config);
    process.stdout.write(JSON.stringify(result.state === 'queued' ? {} : { }) + '\n');
  } catch {
    // No raw input, path or exception text. Never block/restart the assistant.
    process.stdout.write(JSON.stringify({ systemMessage: 'Wikified 自动记录未入队；请检查本机记忆服务。' }) + '\n');
  }
}
