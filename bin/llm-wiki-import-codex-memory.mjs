#!/usr/bin/env node
// Import generated Codex memory Markdown as complete, redacted pending events.
// Never read .codex/sessions, credentials, native Git metadata or arbitrary trees.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { redact } from './llm-wiki-capture-hook.mjs';

const BIN = path.dirname(fileURLToPath(import.meta.url));
const VERSION = 'codex-native-memory-import/v1';
const MAX_FILES = 500, MAX_FILE_BYTES = 1024 * 1024, MAX_TOTAL_BYTES = 16 * 1024 * 1024;
export const CHUNK_CHARACTERS = 1800;
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const utf8 = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });
const safeCode = /^[a-z][a-z-]{0,79}$/;
export class NativeMemoryImportError extends Error {
  constructor(code) { super(code); this.code = code; }
}
function fail(code) { throw new NativeMemoryImportError(code); }
function directoryChain(directory, code, allowMissing = false) {
  const absolute = path.resolve(directory);
  let current = path.parse(absolute).root;
  for (const part of absolute.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part);
    let info;
    try { info = fs.lstatSync(current); }
    catch (error) { if (allowMissing && error.code === 'ENOENT') return false; throw error; }
    if (!info.isDirectory() || info.isSymbolicLink()) fail(code);
  }
  return true;
}
const signature = s => [s.dev, s.ino, s.size, s.mtimeMs, s.ctimeMs, s.nlink, s.mode].join(':');

function regularFile(filename, expectedRoot) {
  const before = fs.lstatSync(filename);
  if (before.isSymbolicLink() || !before.isFile() || before.nlink !== 1) fail('unsafe-source-file');
  if (before.size > MAX_FILE_BYTES) fail('source-file-too-large');
  const actual = fs.realpathSync(filename);
  if (actual !== filename || !actual.startsWith(expectedRoot + path.sep)) fail('unsafe-source-path');
  const fd = fs.openSync(filename, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
  try {
    if (signature(fs.fstatSync(fd)) !== signature(before)) fail('source-changed');
    // A native writer can grow a file after lstat: never allocate/read beyond
    // the original bounded size plus one byte needed to detect growth.
    const buffer = Buffer.alloc(Math.min(before.size + 1, MAX_FILE_BYTES + 1));
    let offset = 0;
    while (offset < buffer.length) {
      const count = fs.readSync(fd, buffer, offset, buffer.length - offset, null);
      if (!count) break;
      offset += count;
    }
    const bytes = buffer.subarray(0, offset);
    if (bytes.length !== before.size || signature(fs.fstatSync(fd)) !== signature(before)
      || signature(fs.lstatSync(filename)) !== signature(before)) fail('source-changed');
    return bytes;
  } finally { fs.closeSync(fd); }
}
function enumerate(sourceRoot) {
  const files = [];
  function add(directory, prefix) {
    const directoryInfo = fs.lstatSync(directory);
    if (directoryInfo.isSymbolicLink() || !directoryInfo.isDirectory() || fs.realpathSync(directory) !== directory) fail('unsafe-source-directory');
    for (const entry of fs.readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0)) {
      if (!prefix && entry.name === 'rollout_summaries') {
        add(path.join(directory, entry.name), 'rollout_summaries/');
        continue;
      }
      if (!prefix && !['MEMORY.md', 'memory_summary.md', 'raw_memories.md'].includes(entry.name)) continue;
      if (!entry.name.toLowerCase().endsWith('.md') || entry.name.startsWith('.') || entry.name.toLowerCase() === 'agents.md') continue;
      if (/[\u0000-\u001f\u007f\\/:]/.test(entry.name)) fail('invalid-source-name');
      // Only top-level generated memory and direct rollout summary Markdown.
      if (entry.isDirectory()) fail('unsupported-source-layout');
      files.push({ relative: prefix + entry.name, filename: path.join(directory, entry.name) });
      if (files.length > MAX_FILES) fail('too-many-source-files');
    }
  }
  add(sourceRoot, '');
  const adHocDirectory = path.join(sourceRoot, 'extensions/ad_hoc');
  if (directoryChain(adHocDirectory, 'unsafe-source-directory', true)) {
    const filename = path.join(adHocDirectory, 'instructions.md');
    try {
      fs.lstatSync(filename);
      files.push({ relative: 'extensions/ad_hoc/instructions.md', filename });
      if (files.length > MAX_FILES) fail('too-many-source-files');
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  return files.sort((a, b) => a.relative < b.relative ? -1 : a.relative > b.relative ? 1 : 0);
}
function snapshot(sourceRoot) {
  let total = 0;
  const files = enumerate(sourceRoot).map(file => {
    const bytes = regularFile(file.filename, sourceRoot);
    total += bytes.length;
    if (total > MAX_TOTAL_BYTES) fail('source-total-too-large');
    let text;
    try { text = utf8.decode(bytes); } catch { fail('source-is-not-utf8'); }
    return { ...file, bytes, text, revision: hash(bytes) };
  });
  return { files, total };
}
function recognizedTranscript(text) {
  const roles = new Set([...text.matchAll(/^#{1,6}\s+(user|assistant|human|用户|助手|人类)\s*:?\s*$/gim)].map(match => match[1].toLowerCase()));
  if ([...roles].some(role => ['user', 'human', '用户', '人类'].includes(role)) && [...roles].some(role => ['assistant', '助手'].includes(role))) return true;
  function isTranscript(value) {
    if (!value || typeof value !== 'object') return false;
    if (Array.isArray(value)) return value.some(isTranscript);
    return ['session_meta', 'turn_context', 'response_item'].includes(value.type)
      || ['user', 'assistant'].includes(value.type) && typeof value.message === 'object'
      || ['user', 'assistant', 'system'].includes(value.role) && 'content' in value
      || Array.isArray(value.messages) && value.messages.some(isTranscript);
  }
  for (const candidate of [text, ...text.split(/\r?\n/)]) {
    try { if (isTranscript(JSON.parse(candidate))) return true; }
    catch { /* Generated Markdown is expected, not JSON/JSONL host history. */ }
  }
  return false;
}
export function redactNativeMemory(text) {
  if (recognizedTranscript(text)) fail('transcript-content-refused');
  let removedMetadataLines = 0;
  const withoutMetadata = text.split(/(?<=\n)/).filter(line => {
    const metadata = /^\s*(?:[-*]\s*)?(?:cwd|rollout_path|rollout_summary_file|thread_id|session_id)\s*:/i.test(line)
      || /^\s*(?:[-*]\s*)?rollout_summaries[\\/]/i.test(line)
      || /^\s*#{1,6}\s+rollout_summary_files\s*$/i.test(line.trim());
    if (metadata) removedMetadataLines++;
    return !metadata;
  }).join('');
  const clean = redact(withoutMetadata).replace(/\u0000/g, '');
  return { text: clean, removedMetadataLines, redacted: clean !== text };
}
export function completeChunks(text, maximum = CHUNK_CHARACTERS) {
  if (!Number.isInteger(maximum) || maximum < 1) fail('invalid-chunk-limit');
  const points = Array.from(text), chunks = [];
  for (let offset = 0; offset < points.length;) {
    let end = Math.min(points.length, offset + maximum);
    if (end < points.length) {
      // Prefer a line boundary while keeping every Unicode code point exactly once.
      for (let i = end - 1; i > offset + maximum / 2; i--) {
        if (points[i] === '\n') { end = i + 1; break; }
      }
    }
    chunks.push(points.slice(offset, end).join('')); offset = end;
  }
  return chunks;
}
function sourceKind(relative) {
  if (relative === 'extensions/ad_hoc/instructions.md') return 'ad-hoc-memory';
  if (relative.startsWith('rollout_summaries/')) return 'rollout-summary';
  if (relative === 'MEMORY.md') return 'consolidated-memory';
  if (relative === 'memory_summary.md') return 'memory-summary';
  if (relative === 'raw_memories.md') return 'generated-raw-memory';
  return 'generated-markdown';
}
export function planCodexMemoryImport(sourceInput) {
  if (typeof sourceInput !== 'string' || !sourceInput) fail('source-root-required');
  directoryChain(sourceInput, 'unsafe-source-directory');
  const sourceRoot = fs.realpathSync(path.resolve(sourceInput));
  if (path.basename(sourceRoot).toLowerCase() !== 'memories') fail('source-must-be-memories-directory');
  const initialRoot = signature(fs.statSync(sourceRoot));
  const first = snapshot(sourceRoot), second = snapshot(sourceRoot);
  const fingerprints = rows => rows.map(row => [row.relative, row.revision]);
  if (JSON.stringify(fingerprints(first.files)) !== JSON.stringify(fingerprints(second.files))
    || signature(fs.statSync(sourceRoot)) !== initialRoot) fail('source-changed');
  const records = [];
  let redactedFiles = 0, removedMetadataLines = 0, emptyFiles = 0, selectedCharacters = 0;
  for (const file of first.files) {
    const cleaned = redactNativeMemory(file.text);
    if (cleaned.redacted) redactedFiles++;
    removedMetadataLines += cleaned.removedMetadataLines;
    if (!cleaned.text.trim()) { emptyFiles++; continue; }
    const sourceRef = hash(JSON.stringify(['codex-native-memory-source/v1', file.relative]));
    const chunks = completeChunks(cleaned.text);
    selectedCharacters += Array.from(cleaned.text).length;
    chunks.forEach((chunk, index) => {
      const key = hash(JSON.stringify([VERSION, sourceRef, file.revision, index, hash(chunk)]));
      const kind = sourceKind(file.relative);
      const details = 'Codex 原生记忆导入材料，未经人工确认；仅为待审核证据，不是用户指令或已确认事实。\n'
        + 'Source kind: ' + kind + '\nSource ref: sha256:' + sourceRef + '\nSource revision: sha256:' + file.revision
        + '\nPart: ' + (index + 1) + '/' + chunks.length + '\n\n' + chunk;
      records.push({ key, sourceRef, sourceRevision: file.revision, part: index + 1, partCount: chunks.length,
        kind, chunk, details, summary: 'Codex 原生记忆 · ' + kind + ' · ' + sourceRef.slice(0, 12) + ' · ' + (index + 1) + '/' + chunks.length,
        evidence: ['codex-native-source:' + sourceRef, 'codex-native-revision:' + file.revision, 'codex-native-part:' + (index + 1) + '/' + chunks.length] });
    });
  }
  return { schemaVersion: 1, sourceFingerprint: hash(JSON.stringify(fingerprints(first.files))), sourceFiles: first.files.length,
    sourceBytes: first.total, redactedFiles, removedMetadataLines, emptyFiles, selectedCharacters, records };
}
function execute(command, args, input, options, code) {
  const result = spawnSync(command, args, { input, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'], timeout: 120000,
    maxBuffer: 4 * 1024 * 1024, ...options });
  if (result.error || result.status !== 0) fail(code);
  return result.stdout;
}
function scanPlan(plan, scanner, python) {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'wikified-native-memory-scan-'));
  try {
    fs.chmodSync(temp, 0o700);
    // Scan complete redacted files too: a vendor credential must not evade the
    // scanner by crossing a chunk boundary.
    const completeFiles = new Map();
    for (const record of plan.records) completeFiles.set(record.sourceRef, (completeFiles.get(record.sourceRef) || '') + record.chunk);
    for (const [sourceRef, text] of completeFiles) fs.writeFileSync(path.join(temp, 'source-' + sourceRef + '.md'), text, { mode: 0o600, flag: 'wx' });
    for (const record of plan.records) fs.writeFileSync(path.join(temp, record.key + '.md'), record.summary + '\n\n' + record.details, { mode: 0o600, flag: 'wx' });
    execute(python, ['-B', scanner, '--root', temp, '--all'], undefined, {}, 'secret-scan-failed');
  } finally { fs.rmSync(temp, { recursive: true, force: true }); }
}
function existingKeys(root) {
  const keys = new Set(), folder = path.join(root, 'memory/events');
  if (!fs.existsSync(folder)) return keys;
  for (const name of fs.readdirSync(folder)) {
    if (!name.endsWith('.jsonl')) continue;
    const candidate = path.join(folder, name), info = fs.lstatSync(candidate);
    if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1) fail('unsafe-event-store');
    for (const line of fs.readFileSync(candidate, 'utf8').split('\n')) {
      if (!line.trim()) continue;
      let value; try { value = JSON.parse(line); } catch { fail('invalid-event-store'); }
      if (!value || typeof value !== 'object' || Array.isArray(value)) fail('invalid-event-store');
      if (typeof value.capture_key === 'string') keys.add(value.capture_key);
    }
  }
  return keys;
}
function validateDestination(root) {
  directoryChain(root, 'unsafe-destination-directory');
  directoryChain(path.join(root, 'policy'), 'unsafe-destination-directory');
  const policy = fs.lstatSync(path.join(root, 'policy/access.json'));
  if (!policy.isFile() || policy.isSymbolicLink() || policy.nlink !== 1) fail('policy-required');
  directoryChain(path.join(root, 'memory/events'), 'unsafe-destination-directory', true);
  const lock = path.join(root, 'memory/.memory.lock');
  try {
    const info = fs.lstatSync(lock);
    if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1) fail('unsafe-event-store');
  } catch (error) { if (error.code !== 'ENOENT') throw error; }
  return existingKeys(root);
}
export function importCodexMemory({ sourceRoot, root: rootInput, dryRun = true, python = 'python3',
  scanner = path.join(BIN, 'llm-wiki-secret-scan'), eventBin = path.join(BIN, 'llm-wiki-event') }) {
  const plan = planCodexMemoryImport(sourceRoot);
  scanPlan(plan, scanner, python);
  const report = { schemaVersion: 1, mode: dryRun ? 'dry-run' : 'apply', status: 'passed', sourceFingerprint: plan.sourceFingerprint,
    sourceFiles: plan.sourceFiles, sourceBytes: plan.sourceBytes, selectedCharacters: plan.selectedCharacters,
    chunks: plan.records.length, redactedFiles: plan.redactedFiles, removedMetadataLines: plan.removedMetadataLines,
    emptyFiles: plan.emptyFiles, project: 'codex-memory', reviewStatus: 'pending', imported: 0, alreadyRecorded: 0 };
  if (dryRun) return report;
  if (typeof rootInput !== 'string' || !rootInput) fail('destination-root-required');
  directoryChain(rootInput, 'unsafe-destination-directory');
  const root = fs.realpathSync(path.resolve(rootInput));
  const source = fs.realpathSync(path.resolve(sourceRoot));
  if (root === source || root.startsWith(source + path.sep) || source.startsWith(root + path.sep)) fail('source-destination-overlap');
  const known = validateDestination(root);
  const env = Object.fromEntries(['PATH', 'HOME', 'LANG', 'LC_ALL', 'SystemRoot', 'WINDIR'].filter(key => process.env[key] !== undefined).map(key => [key, process.env[key]]));
  Object.assign(env, { LLM_WIKI_ROOT: root, PYTHONDONTWRITEBYTECODE: '1' });
  const shim = "import json,runpy,sys; sys.exit(runpy.run_path(sys.argv[1],run_name='codex_native_memory_import')['main'](json.load(sys.stdin)))";
  for (const record of plan.records) {
    validateDestination(root);
    const args = ['--type', 'session', '--project', 'codex-memory', '--actor-type', 'ai', '--actor-id', 'codex-native-memory',
      '--source', 'codex-native-memory', '--domain', 'work', '--sensitivity', 'internal', '--epistemic-status', 'ai-proposed',
      '--review-status', 'pending', '--target-agent', 'coding', '--capture-key', record.key, '--details', record.details, '--print'];
    for (const evidence of record.evidence) args.push('--evidence-ref', evidence);
    args.push('--', record.summary);
    // Private, already-redacted text uses stdin rather than appearing in argv.
    const stdout = execute(python, ['-B', '-c', shim, eventBin], JSON.stringify(args), { cwd: root, env }, 'event-import-failed');
    let saved; try { saved = JSON.parse(stdout); } catch { fail('invalid-event-receipt'); }
    const expectedId = hash('wikified-capture-v1:' + record.key).slice(0, 16);
    if (saved.id !== expectedId || saved.capture_key !== record.key || saved.actor?.type !== 'ai'
      || saved.actor?.id !== 'codex-native-memory' || saved.review_status !== 'pending'
      || saved.epistemic_status !== 'ai-proposed' || saved.project !== 'codex-memory') fail('invalid-event-receipt');
    if (known.has(record.key)) report.alreadyRecorded++; else { report.imported++; known.add(record.key); }
  }
  return report;
}
function commandLine(argv) {
  const values = {}, flags = new Set();
  for (let index = 0; index < argv.length; index++) {
    const name = argv[index];
    if (['--dry-run', '--apply'].includes(name)) {
      if (flags.has(name)) fail('invalid-arguments'); flags.add(name); continue;
    }
    if (!['--source-root', '--root', '--python', '--scanner', '--event-bin'].includes(name)
      || values[name] !== undefined || !argv[index + 1] || argv[index + 1].startsWith('--')) fail('invalid-arguments');
    values[name] = argv[++index];
  }
  if (flags.size > 1 || !values['--source-root']) fail('invalid-arguments');
  return importCodexMemory({ sourceRoot: values['--source-root'], root: values['--root'], dryRun: !flags.has('--apply'),
    ...(values['--python'] ? { python: values['--python'] } : {}),
    ...(values['--scanner'] ? { scanner: values['--scanner'] } : {}),
    ...(values['--event-bin'] ? { eventBin: values['--event-bin'] } : {}) });
}
function isMain() {
  if (!process.argv[1]) return false;
  try { return fs.realpathSync(path.resolve(process.argv[1])) === fileURLToPath(import.meta.url); }
  catch { return false; }
}
if (isMain()) {
  try { console.log(JSON.stringify(commandLine(process.argv.slice(2)))); }
  catch (error) {
    const code = error instanceof NativeMemoryImportError && safeCode.test(error.code) ? error.code : 'import-failed';
    console.error(JSON.stringify({ schemaVersion: 1, status: 'failed', code })); process.exitCode = 1;
  }
}
