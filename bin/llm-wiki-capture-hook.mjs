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
  if (typeof message !== 'string' || message.length > 1_000_000) return null;
  const clean = redact(message).replace(/\u0000/g, '');
  const lines = clean.split(/\r?\n/).map(line => line.trim()).filter(line => {
    // Redaction markers and credential labels alone are not a handoff fact.
    const meaningful = line
      .replace(/["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|passwd|密码|密钥|验证码)["']?\s*[:=：]\s*["']?\[REDACTED\]["']?/gi, '')
      .replace(/\bBearer\s+\[REDACTED\]/gi, '')
      .replace(/\[(?:REDACTED(?:_[A-Z]+)*|凭据行未保存|长不透明值未保存|代码块未自动保存)\]/gi, '')
      .trim();
    return meaningful.length >= 8 && /[\p{L}\p{N}]/u.test(meaningful) && !/^\|[- :|]+\|$/.test(line);
  });
  if (!lines.length) return null;
  // Selected output only: bounded first useful lines; explicitly not semantic facts.
  const selected = lines.slice(0, 8).map(line => [...line].slice(0, 360).join('')).join('\n');
  return { title: [...lines[0].replace(/^[#*\s-]+/, '')].slice(0, 72).join(''), text: [...selected].slice(0, 1800).join('') };
}
function inside(candidate, root) {
  const relative = path.relative(root, candidate);
  return relative === '' || (!relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative));
}
const FINAL_PHASES = new Set(['final', 'final_answer']);
const HOOK_EVENTS = new Set(['Stop', 'PreCompact', 'SessionEnd', 'Interrupt']);
const OUTCOME_STATES = new Set(['disabled', 'ignored-event', 'ignored-continuation',
  'invalid-context', 'out-of-scope', 'no-selected-output', 'already-saved', 'queued', 'error']);
const SELECTION_SOURCES = new Set(['direct', 'transcript-response', 'transcript-agent-message',
  'transcript-item-completed', 'transcript-task-complete']);
const MAX_TRANSCRIPT_TAIL = 2 * 1024 * 1024;
const MAX_TRANSCRIPT_HEADER = 64 * 1024;
export const CAPTURE_PROFILES = new Set(['codex', 'opencode', 'claude', 'cursor', 'grok', 'pi', 'workbuddy']);
const PROJECT_ID = /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,100}$/;
const validProject = project => typeof project === 'string' && PROJECT_ID.test(project);

function captureProject(cwd, config) {
  let selected = null;
  for (const item of Array.isArray(config.workspaces) ? config.workspaces : []) {
    try {
      if (typeof item?.path !== 'string') continue;
      const root = fs.realpathSync(item.path);
      if (inside(cwd, root) && (!selected || root.length > selected.root.length)) selected = { root, item };
    } catch { /* A disconnected/stale workspace must not hide another valid binding. */ }
  }
  // A specific binding wins, even if malformed: do not reroute it to the fallback.
  const project = selected ? selected.item.project : config.fallbackProject;
  return validProject(project) ? project : null;
}

// Only the trusted configuration chooses the host protocol and stored identity.
function foreignHostPayload(payload, config) {
  const grok = typeof payload?.hookEventName === 'string' && typeof payload?.sessionId === 'string' &&
    typeof payload?.workspaceRoot === 'string';
  const cursor = typeof payload?.conversation_id === 'string' && typeof payload?.generation_id === 'string' &&
    Array.isArray(payload?.workspace_roots);
  return (grok && config.profile !== 'grok') || (cursor && config.profile !== 'cursor');
}

function normalizedPayload(payload, config) {
  if (config.profile === 'grok') return {
    hook_event_name: payload?.hookEventName === 'stop' && payload?.reason === 'end_turn' ? 'Stop' : undefined,
    session_id: payload?.sessionId,
    turn_id: payload?.promptId,
    cwd: payload?.cwd,
    stop_hook_active: payload?.stopHookActive,
    last_assistant_message: payload?.lastAssistantMessage,
  };
  if (config.profile !== 'cursor') return payload;
  return {
    hook_event_name: payload?.hook_event_name === 'afterAgentResponse' ? 'Stop' : undefined,
    session_id: payload?.conversation_id,
    turn_id: payload?.generation_id,
    workspace_roots: payload?.workspace_roots,
    last_assistant_message: payload?.text,
  };
}

function contentText(content) {
  if (!Array.isArray(content)) return null;
  const parts = content.filter(item => item && ['output_text', 'Text', 'text'].includes(item.type) &&
    typeof item.text === 'string').map(item => item.text);
  return parts.length ? parts.join('\n') : null;
}

function finalRecord(row) {
  const item = row?.payload;
  if (row?.type === 'response_item' && item?.type === 'message' && item.role === 'assistant' &&
      FINAL_PHASES.has(item.phase)) {
    return { message: contentText(item.content), source: 'transcript-response' };
  }
  if (row?.type !== 'event_msg') return null;
  if (item?.type === 'agent_message' && (item.phase == null || FINAL_PHASES.has(item.phase))) {
    return { message: item.message, source: 'transcript-agent-message' };
  }
  if (item?.type === 'item_completed' && item.item?.type === 'AgentMessage' && FINAL_PHASES.has(item.item.phase)) {
    return { message: contentText(item.item.content), source: 'transcript-item-completed' };
  }
  if (item?.type === 'task_complete') {
    return { message: item.last_agent_message, source: 'transcript-task-complete' };
  }
  return null;
}

function latestFinal(payload, config) {
  if (typeof payload.last_assistant_message === 'string' && payload.last_assistant_message.length) {
    return { message: payload.last_assistant_message, source: 'direct' };
  }
  // Other hosts provide completed text directly. Their transcript formats have
  // not been validated as Codex rollouts and must never reuse Codex fallback.
  if (config.profile !== 'codex') return null;
  if (!HOOK_EVENTS.has(payload.hook_event_name) || typeof payload.transcript_path !== 'string') return null;
  // Stop must name the current turn rather than silently reusing an earlier final.
  if (payload.hook_event_name === 'Stop' && (typeof payload.turn_id !== 'string' || !payload.turn_id)) return null;
  const source = fs.realpathSync(payload.transcript_path);
  if (!(config.transcriptRoots || []).some(root => {
    try { return inside(source, fs.realpathSync(root)); } catch { return false; }
  })) return null;
  const fd = fs.openSync(source, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
  try {
    const info = fs.fstatSync(fd);
    const sameFile = current => current.isFile() && current.nlink === 1 &&
      current.dev === info.dev && current.ino === info.ino;
    if (!sameFile(info) || !sameFile(fs.lstatSync(source))) return null;
    const header = Buffer.alloc(Math.min(info.size, MAX_TRANSCRIPT_HEADER));
    fs.readSync(fd, header, 0, header.length, 0);
    const newline = header.indexOf(10);
    if (newline < 0) return null;
    let metadata;
    try { metadata = JSON.parse(header.subarray(0, newline).toString('utf8')); } catch { return null; }
    if (metadata?.type !== 'session_meta' || metadata.payload?.id !== payload.session_id) return null;

    const size = Math.min(info.size, MAX_TRANSCRIPT_TAIL), start = info.size - size;
    const buffer = Buffer.alloc(size);
    fs.readSync(fd, buffer, 0, size, start);
    const lines = buffer.toString('utf8').split('\n');
    if (start) lines.shift();
    let activeTurn = null;
    const candidates = [];
    for (const line of lines) {
      try {
        const row = JSON.parse(line), item = row.payload;
        if (typeof item?.thread_id === 'string' && item.thread_id !== payload.session_id) continue;
        if ((row.type === 'turn_context' || (row.type === 'event_msg' && item?.type === 'task_started')) &&
            typeof item?.turn_id === 'string' && item.turn_id) activeTurn = item.turn_id;
        const selected = finalRecord(row);
        if (selected && typeof selected.message === 'string' && selected.message.length) {
          candidates.push({ ...selected, turn: typeof item?.turn_id === 'string' ? item.turn_id : activeTurn });
        }
      } catch { /* Incomplete tail line/unknown host formats are not interpreted. */ }
    }
    // Appends are normal for a live rollout; replacement or link substitution is not.
    if (!sameFile(fs.fstatSync(fd)) || !sameFile(fs.lstatSync(source))) return null;
    const expectedTurn = typeof payload.turn_id === 'string' && payload.turn_id ? payload.turn_id : activeTurn;
    return candidates.reverse().find(item => expectedTurn === null || item.turn === expectedTurn) || null;
  } finally { fs.closeSync(fd); }
}

// Metadata only: no transcript path, title, excerpt, message, or raw error.
export function writeHookStatus(config, payload, result) {
  if (!config || typeof config.queue !== 'string') return;
  // Compatibility scanners can execute another host's hook command. Do not
  // attribute that invocation to the command's configured host, even as a skip.
  if (foreignHostPayload(payload, config)) return;
  payload = normalizedPayload(payload, config);
  const status = {
    schema: 1, checkedAt: new Date().toISOString(),
    state: OUTCOME_STATES.has(result?.state) ? result.state : 'error',
    event: HOOK_EVENTS.has(payload?.hook_event_name) ? payload.hook_event_name : 'unknown',
    coverage: {
      workspaceCount: Array.isArray(config.workspaces) ? config.workspaces.length : 0,
      fallbackEnabled: validProject(config.fallbackProject),
    },
  };
  if (typeof payload?.session_id === 'string' && payload.session_id.length <= 200) status.sessionRef = hash(payload.session_id);
  if (SELECTION_SOURCES.has(result?.selectionSource)) status.selectionSource = result.selectionSource;
  if (typeof result?.key === 'string' && /^[a-f0-9]{64}$/.test(result.key)) status.key = result.key;
  const directory = path.resolve(config.queue);
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  if (fs.lstatSync(directory).isSymbolicLink()) throw new Error('unsafe queue');
  const names = ['hook-status.json'];
  if (CAPTURE_PROFILES.has(config.profile)) names.push(`hook-status-${config.profile}.json`);
  for (const name of names) {
    const temp = path.join(directory, '.' + crypto.randomUUID() + '.tmp');
    const fd = fs.openSync(temp, 'wx', 0o600);
    try { fs.writeFileSync(fd, JSON.stringify(status) + '\n'); fs.fsyncSync(fd); }
    finally { fs.closeSync(fd); }
    try { fs.renameSync(temp, path.join(directory, name)); }
    finally { if (fs.existsSync(temp)) fs.unlinkSync(temp); }
  }
  if (process.platform !== 'win32') { const dir = fs.openSync(directory, 'r'); try { fs.fsyncSync(dir); } finally { fs.closeSync(dir); } }
}

export function enqueue(payload, config) {
  if (config.enabled !== true || !CAPTURE_PROFILES.has(config.profile)) return { state: 'disabled' };
  if (foreignHostPayload(payload, config)) return { state: 'ignored-event' };
  payload = normalizedPayload(payload, config);
  if (!['Stop', 'PreCompact', 'SessionEnd', 'Interrupt'].includes(payload.hook_event_name)) return { state: 'ignored-event' };
  if (payload.stop_hook_active === true) return { state: 'ignored-continuation' };
  if (typeof payload.session_id !== 'string' || !payload.session_id || payload.session_id.length > 200) return { state: 'invalid-context' };
  let project;
  if (config.profile === 'cursor') {
    const roots = payload.workspace_roots;
    if (!Array.isArray(roots) || roots.length > 100 || !roots.every(root => typeof root === 'string') ||
        typeof payload.turn_id !== 'string' || !payload.turn_id) return { state: 'invalid-context' };
    const projects = new Set(roots.map(root => captureProject(fs.realpathSync(root), config)));
    // Multiroot/no-folder chats use their common project, otherwise the opt-in
    // fallback. Do not label a cross-project conversation after its first root.
    project = projects.size === 1 ? [...projects][0] : (validProject(config.fallbackProject) ? config.fallbackProject : null);
  } else {
    if (typeof payload.cwd !== 'string') return { state: 'invalid-context' };
    project = captureProject(fs.realpathSync(payload.cwd), config);
  }
  if (!project) return { state: 'out-of-scope' };
  const candidate = latestFinal(payload, config);
  const selected = excerpt(candidate?.message);
  if (!selected) return { state: 'no-selected-output' };
  const key = hash(JSON.stringify(['capture/v1', config.profile, payload.session_id, project, selected]));
  const directory = path.resolve(config.queue);
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  if (fs.lstatSync(directory).isSymbolicLink()) throw new Error('unsafe queue');
  if (fs.existsSync(path.join(directory, key + '.done'))) return { state: 'already-saved', selectionSource: candidate.source };
  const job = { schema: 1, key, profile: config.profile, project,
    sessionRef: hash(payload.session_id), title: selected.title, excerpt: selected.text };
  const temp = path.join(directory, '.' + crypto.randomUUID() + '.tmp');
  const target = path.join(directory, key + '.json');
  const fd = fs.openSync(temp, 'wx', 0o600);
  try { fs.writeFileSync(fd, JSON.stringify(job) + '\n'); fs.fsyncSync(fd); }
  finally { fs.closeSync(fd); }
  // Same-key enqueue has identical content. Never replace a worker receipt.
  if (fs.existsSync(target)) fs.unlinkSync(temp); else fs.renameSync(temp, target);
  if (process.platform !== 'win32') { const dir = fs.openSync(directory, 'r'); try { fs.fsyncSync(dir); } finally { fs.closeSync(dir); } }
  return { state: 'queued', key, selectionSource: candidate.source };
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  let config, payload;
  try {
    config = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
    let input = '';
    for await (const chunk of process.stdin) { input += chunk; if (input.length > 1_100_000) throw new Error('input limit'); }
    payload = JSON.parse(input);
    const result = enqueue(payload, config);
    writeHookStatus(config, payload, result);
    process.stdout.write('{}\n');
  } catch {
    // No raw input, path or exception text. Never block/restart the assistant.
    try { writeHookStatus(config, payload, { state: 'error' }); } catch { /* Status storage may also be unavailable. */ }
    process.stdout.write(JSON.stringify({ systemMessage: 'Wikified 自动记录状态异常；请检查本机记忆服务。' }) + '\n');
  }
}
