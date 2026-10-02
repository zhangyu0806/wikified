# Codex native memory import

Codex host-local memories and Wikified are separate stores. This explicit importer reads selected native memory Markdown and proposes redacted, versioned events in Wikified. Once the events are saved, an existing private vault publisher can carry them to an owner-facing Web workspace. Human approval remains a separate action before agent recall.

The importer handles these paths under an explicitly supplied `memories` directory:

- The exact top-level files `MEMORY.md`, `memory_summary.md` and `raw_memories.md`.
- Direct Markdown children of `rollout_summaries/`.
- The optional exact path `extensions/ad_hoc/instructions.md`, treated as untrusted source material.

It does not traverse other extensions, native Git metadata, sessions, credentials or arbitrary nested directories. Recognized transcript content is refused. Native files are read only; generated Codex state is never edited or restored from Wikified.

## Preview and import

Use the same pinned engine directory for the importer, event writer and scanner. Python 3 and Node.js are required.

```sh
node /pinned/engine/bin/llm-wiki-import-codex-memory.mjs \
  --source-root /path/to/.codex/memories --dry-run

node /pinned/engine/bin/llm-wiki-import-codex-memory.mjs \
  --source-root /path/to/.codex/memories \
  --root /private/vault --apply
```

For Windows Codex with a WSL vault, run the CLI in WSL and supply the mounted Windows source, for example `/mnt/c/Users/<user>/.codex/memories`. A native Windows invocation needs an available Python command and a local destination supported by the event writer.

Dry-run is the default. It checks a stable source snapshot, removes credential-like values and fenced code, strips native transcript locator metadata, splits all remaining text into bounded Unicode chunks, and scans the proposed output for secrets. It reports counts and fingerprints without printing bodies or original filenames. Nothing is appended to the vault in dry-run mode.

Apply writes `session` events with project `codex-memory`, actor/source `codex-native-memory`, `ai-proposed` epistemic status, `pending` review status and target group `coding`. This project groups imported evidence; it does not assert that every source fact belongs to the same original project. Review its scope and contents before approving reuse.

## Identity, retries and changes

Each chunk carries an opaque source reference, raw source revision and part number. The capture key includes that identity and the redacted chunk content. Repeating the same import uses the event writer's locked idempotency check, so a lost acknowledgment does not create duplicate events. Confidential text is passed to the writer through stdin, not process arguments.

A changed source version produces new pending proposals. It does not replace approved facts, approve proposals, supersede earlier versions or withdraw records when a native file disappears. Reviewers decide those transitions. This is version-level deduplication, not a claim that overlapping consolidated memories and rollout summaries are semantically unique.

## Periodic operation and verification

After a successful dry-run and one verified apply, an owner may run the same apply command before an existing private vault publication job. Keep one vault writer and serialize the integration with the publisher. A failed import must be visible as a service failure; do not report the resulting source as freshly imported. Keep private source/configuration backups outside the public engine repository.

Verify three different results:

1. The importer reports the intended source count, and an immediate repeat adds zero records.
2. The destination owner catalog lists and reads the imported event IDs with their pending status and preserved provenance.
3. Codex/other AI profiles cannot retrieve the pending contents until a human has approved the intended event revision.

Native memory generation is asynchronous and may skip active chats. Importing the native files only imports what Codex has generated. For recent conversation handoffs, use the scoped lifecycle capture described in [AUTOMATIC_MEMORY.md](AUTOMATIC_MEMORY.md). Keep its hooks separately observable and review changed hook definitions through Codex's hook UI; do not edit trust hashes.

The engine's tests use synthetic memories and disposable vaults. A passed test is not evidence that the live Codex client has reloaded or trusted a new hook definition, or that a particular cloud destination has received the records.
