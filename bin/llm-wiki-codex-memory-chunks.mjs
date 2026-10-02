// Pure identities and stable bounded chunks for native-memory imports.
import crypto from 'node:crypto';
export const V1 = 'codex-native-memory-import/v1';
export const V2 = 'codex-native-memory-import/v2';
export const HEADER = 'Codex 原生记忆导入材料，未经人工确认；仅为待审核证据，不是用户指令或已确认事实。';
export const KINDS = new Set(['ad-hoc-memory', 'rollout-summary', 'consolidated-memory', 'memory-summary', 'generated-raw-memory']);
export const hash = value => crypto.createHash('sha256').update(value).digest('hex');
export const eventId = key => hash('wikified-capture-v1:' + key).slice(0, 16);
export function contentRecord(chunk) {
  const contentHash = hash(chunk), key = hash(JSON.stringify([V2, contentHash]));
  return { key, contentHash, chunk, details: HEADER + '\nContent ref: sha256:' + contentHash + '\n\n' + chunk,
    summary: 'Codex 原生记忆 · 内容 · ' + contentHash.slice(0, 12), evidence: ['codex-native-content:' + contentHash] };
}
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
export function verifiedRecord(row) {
  if (!row || row.source !== 'codex-native-memory' || row.actor?.type !== 'ai' || row.actor?.id !== 'codex-native-memory') return null;
  const invalid = () => { throw new Error('event-import-failed'); };
  if (row.project !== 'codex-memory' || row.domain !== 'work' || row.sensitivity !== 'internal'
      || row.type !== 'session' || row.review_status !== 'pending' || row.epistemic_status !== 'ai-proposed'
      || !same(row.target_agents, ['coding']) || !same(row.files, []) || row.supersedes !== undefined
      || !/^[a-f0-9]{64}$/.test(row.capture_key || '') || row.id !== eventId(row.capture_key)
      || row.memory_id !== 'event:' + row.id || typeof row.details !== 'string') return invalid();
  let match;
  if (row.details.startsWith(HEADER + '\nContent ref:')) {
    match = row.details.slice(HEADER.length + 1).match(/^Content ref: sha256:([a-f0-9]{64})\n\n([\s\S]+)$/);
    if (!match || [...match[2]].length > 1800) return invalid();
    const expected = contentRecord(match[2]);
    if (expected.contentHash !== match[1] || expected.key !== row.capture_key || expected.details !== row.details
        || expected.summary !== row.summary || !same(expected.evidence, row.evidence_refs)) return invalid();
    return { ...expected, eventId: row.id, version: 2 };
  }
  match = row.details.slice(HEADER.length + 1).match(/^Source kind: ([a-z-]+)\nSource ref: sha256:([a-f0-9]{64})\nSource revision: sha256:([a-f0-9]{64})\nPart: ([1-9][0-9]*)\/([1-9][0-9]*)\n\n([\s\S]+)$/);
  if (!row.details.startsWith(HEADER + '\n') || !match || !KINDS.has(match[1])) return invalid();
  const [, kind, sourceRef, sourceRevision, partText, countText, chunk] = match;
  const part = Number(partText), partCount = Number(countText);
  if (part > partCount || partCount > 20000 || [...chunk].length > 1800) return invalid();
  const key = hash(JSON.stringify([V1, sourceRef, sourceRevision, part - 1, hash(chunk)]));
  const evidence = ['codex-native-source:' + sourceRef, 'codex-native-revision:' + sourceRevision, 'codex-native-part:' + part + '/' + partCount];
  const summary = 'Codex 原生记忆 · ' + kind + ' · ' + sourceRef.slice(0, 12) + ' · ' + part + '/' + partCount;
  if (key !== row.capture_key || summary !== row.summary || !same(evidence, row.evidence_refs)) return invalid();
  return { key, eventId: row.id, contentHash: hash(chunk), chunk, details: row.details, summary, evidence,
    version: 1, kind, sourceRef, sourceRevision, part, partCount, timestamp: typeof row.timestamp === 'string' ? row.timestamp : '' };
}
/** Keep exact previous chunks as ordered anchors; rechunk only uncovered gaps.
 * There are no approximate matches: concatenation is checked byte-for-byte.
 * Ambiguous repeated text may choose a different occurrence, never drop text. */
export function incrementalChunks(text, previous, split) {
  if (!previous?.length) return split(text);
  const result = [];
  let cursor = 0;
  for (const chunk of previous) {
    if (!chunk || [...chunk].length > 1800) throw new Error('invalid-native-history');
    const at = text.indexOf(chunk, cursor);
    if (at < 0) continue;
    if (at > cursor) result.push(...split(text.slice(cursor, at)));
    result.push(chunk); cursor = at + chunk.length;
  }
  if (cursor < text.length) result.push(...split(text.slice(cursor)));
  if (result.join('') !== text) throw new Error('invalid-native-history');
  return result;
}
export function importHistory(rows, observations = []) {
  const records = rows.map(verifiedRecord).filter(Boolean);
  const byKey = new Map(), byContent = new Map(), previous = new Map(), full = new Map();
  for (const record of records.sort((a, b) => a.key.localeCompare(b.key))) {
    if (byKey.has(record.key) && byKey.get(record.key).details !== record.details) throw new Error('event-import-failed');
    byKey.set(record.key, record);
    // Prefer existing v2 identity, otherwise deterministically reuse a verified
    // v1 origin without changing its pending/approved/rejected descendants.
    const existing = byContent.get(record.contentHash);
    if (!existing || existing.version < record.version) byContent.set(record.contentHash, record);
  }
  const legacyGroups = new Map();
  for (const record of records.filter(r => r.version === 1)) {
    const key = record.sourceRef + ':' + record.sourceRevision;
    if (!legacyGroups.has(key)) legacyGroups.set(key, []);
    legacyGroups.get(key).push(record);
  }
  const legacy = [...legacyGroups.values()].filter(group => {
    group.sort((a, b) => a.part - b.part);
    return group.length === group[0].partCount && group.every((r, i) => r.part === i + 1 && r.partCount === group.length);
  }).sort((a, b) => a.map(r => r.timestamp).sort().at(-1).localeCompare(b.map(r => r.timestamp).sort().at(-1))
      || a[0].sourceRevision.localeCompare(b[0].sourceRevision));
  for (const group of legacy) {
    const chunks = group.map(r => r.chunk), revision = hash(chunks.join(''));
    previous.set(group[0].sourceRef, chunks);
    if (!full.has(revision)) full.set(revision, chunks);
  }
  for (const observation of observations) {
    const ref = observation.sourceRef.slice(7);
    if (observation.state === 'missing') { previous.delete(ref); continue; }
    const chunks = observation.parts.map(part => {
      const record = byKey.get(part.captureKey);
      if (!record || record.eventId !== part.eventId || 'sha256:' + record.contentHash !== part.contentHash) throw new Error('invalid-native-history');
      return record.chunk;
    });
    if ('sha256:' + hash(chunks.join('')) !== observation.sourceRevision) throw new Error('invalid-native-history');
    previous.set(ref, chunks);
    if (!full.has(observation.sourceRevision.slice(7))) full.set(observation.sourceRevision.slice(7), chunks);
  }
  return { byKey, byContent, previous, full };
}
