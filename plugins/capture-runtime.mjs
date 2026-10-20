import fs from 'node:fs';
import path from 'node:path';
import { homedir } from 'node:os';
import { enqueue, excerpt, writeHookStatus } from '../bin/llm-wiki-capture-hook.mjs';

const CONFIG_LIMIT = 64 * 1024;
export const validId = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_-]{0,199}$/.test(value);

/** Never retain raw messages, reasoning, tool results, synthetic text or whole transcripts. */
export function selectedText(parts, belongs = () => true) {
  if (!Array.isArray(parts) || parts.length > 4096) return null;
  const texts = [];
  let length = 0;
  for (const part of parts) {
    if (!part || part.type !== 'text' || part.synthetic || part.ignored || typeof part.text !== 'string' || !belongs(part)) continue;
    length += part.text.length + 1;
    // Reject oversized input before joining; never truncate a credential before redaction.
    if (length > 1_000_000 || texts.length >= 128) return null;
    texts.push(part.text);
  }
  return excerpt(texts.join('\n'))?.text ?? null;
}

function readConfig(profile) {
  const configured = process.env[`LLM_WIKI_${profile.toUpperCase()}_CAPTURE_CONFIG`];
  const file = configured === undefined ? path.join(homedir(), '.config', 'llm-wiki', `capture-${profile}.json`) : configured;
  if (!path.isAbsolute(file)) return null;
  let fd;
  try {
    if (fs.lstatSync(file).isSymbolicLink()) return null;
    fd = fs.openSync(file, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.size > CONFIG_LIMIT) return null;
    const current = fs.lstatSync(file);
    if (!current.isFile() || current.dev !== stat.dev || current.ino !== stat.ino) return null;
    const buffer = Buffer.alloc(CONFIG_LIMIT + 1);
    const count = fs.readSync(fd, buffer, 0, buffer.length, 0);
    if (count > CONFIG_LIMIT) return null;
    const value = JSON.parse(buffer.subarray(0, count).toString('utf8'));
    if (!value || typeof value !== 'object' || Array.isArray(value) || typeof value.queue !== 'string' || !path.isAbsolute(value.queue)) return null;
    // The installation determines identity. A mismatched config cannot impersonate another host.
    return { enabled: value.enabled === true && value.profile === profile, profile,
      queue: value.queue, workspaces: value.workspaces, fallbackProject: value.fallbackProject };
  } catch { return null; }
  finally { if (fd !== undefined) fs.closeSync(fd); }
}

/** Read operator config on every boundary so disabling capture applies without a host restart. */
export function captureSink(profile) {
  function record(payload, forcedState) {
    let config;
    try {
      config = readConfig(profile);
      if (!config) return { state: 'disabled' };
      const result = config.enabled !== true ? { state: 'disabled' }
        : forcedState ? { state: forcedState } : enqueue(payload, config);
      writeHookStatus(config, payload, result);
      return result;
    } catch {
      try { writeHookStatus(config, payload, { state: 'error' }); } catch { /* Never interrupt the host or expose raw errors. */ }
      return { state: 'error' };
    }
  }
  return { enabled: () => readConfig(profile)?.enabled === true, record };
}
