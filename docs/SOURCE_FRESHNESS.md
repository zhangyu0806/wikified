# Request-local Markdown and event source freshness

This slice gates AI reuse of typed Markdown dependencies. It does not migrate a
vault, rewrite a note, establish a new authoritative body, or implement event
review UI, an incremental index, server replication, or full P3 completion.

## Relation contract

A strict-JSON `mind2one.sources` entry can declare:

- `current-derived`: a current claim depends on an authorized, P2-trusted
  Markdown document with an explicit persisted `memory_id`. Its `revision` must
  be the complete `sha256:<64 lowercase hex>` hash of that source file's raw
  bytes. The dependency graph is followed transitively.
- `historical-citation` or `independent-judgment`: no current dependency edge is
  inferred. Changing or removing that source does not automatically revoke the
  record; this slice reports it as `unassessed`, not current-valid evidence.
- No relation / no source: legacy `unassessed`, not an invented dependency.

The source `kind` for a current dependency can be `document` or `event`. For a
document, `locator` is an
optional bounded portable `wiki/...md` / `notes/...md` hint, never a fallback
identity. Renaming a file while preserving its explicit identity and exact
bytes preserves the edge. A path-derived legacy ID is not a persistent source
identity. Revision mismatches, missing/denied/untrusted sources, malformed
current references, cycles, and traversal bounds cannot yield reusable current
context. The pure contract returns only a generic state and `reuseAllowed`;
it never discloses missing source names, IDs, locators, counts, or bodies.

An `event` source pins an independently reviewed, current v4/v5 event's stable
`record_id` and its complete `wikified-event-record/v1` revision. It does not use
the transient event ID or a hash of its rendered summary. Event locators must be
absent or empty. Legacy/unreviewed events, and v4 events carrying an unknown
`sources` property, cannot act as source-free leaves. Their ordinary legacy
context access is unchanged. A v5 source retains all its document dependencies;
editing, withdrawing, denying, or holding an upstream source withholds the
downstream Markdown conclusion too. Restoring/reviewing an event creates a new
revision and does not automatically update a pinned conclusion.

Existing P2 semantic bindings already cover the relation, source ID, revision,
label, and locator. This implementation does not alter that hash algorithm or
auto-confirm an edited AI claim. Review validity and source freshness are
separate conditions. To rebind a changed claim, a human must review a new
complete declaration; an old approval does not bless new source bytes.
The Markdown version and semantic hash algorithm stay unchanged. Older readers
reject the new `event` source kind instead of treating it as independent content.
Upgrade all relevant readers before adopting these references.

## Authorization and coverage

Each request creates a complete bounded catalog of `wiki/` and explicitly
shared `notes/` documents. Governance and the P2 pre-body guard run before
opening any document body. Denied sources never enter the body reader. The
dependency snapshot includes eligible documents outside the query's project;
project filtering cannot hide an otherwise authorized dependency.

Where current event references are present, the snapshot joins trusted document
nodes with eligible event nodes before freshness filtering. Event lifecycle
closure runs before ACL and source filtering; an unavailable successor cannot
resurrect its predecessor. Node kinds must match reference kinds, and duplicate
stable IDs across either type fail closed. Pure document reads retain their
existing independence from an unrelated event directory. No new event writer
format, event-to-event binding input, index, or automatic review queue is added.

The existing limits remain explicit: 2 MiB per Markdown snapshot, 8 MiB total
authorized snapshots, 2,000 Markdown files, and 8,000 directory entries. A
coverage/resource failure aborts rather than emitting a partial query or
session digest. The graph additionally bounds depth, visited nodes, references,
and snapshot nodes; incomplete assessment cannot be reused as current context.
The unchanged graph bounds are depth 32, visited nodes 256, references per node
256, and snapshot nodes 10,000, shared across both types. Event scanning retains
its existing complete-snapshot file, byte and line limits. Traversal does not
reset at a document/event boundary.

The root identity, exact access-policy bytes/file identity, and complete file
manifest are checked before and after snapshot construction and immediately
before CLI output. Reads and both ranking paths additionally revalidate their
request-local generation before returning. There is no cross-request warm
cache. These observations detect drift but are not a filesystem transaction
against an arbitrary same-OS adversary. No persistent revocation ledger or
temporal history is invented: exact restored authorized source bytes can become
current again on a fresh request.

## Export surfaces and privacy

The same gate covers hybrid and legacy query ranking, full and revision-pinned
chunked `read_page`, MCP's use of those operations, `read_text_head`, and
session-start Markdown sections. Filtering happens before ranking or excerpt
construction; stale conclusions cannot survive as a low-ranked candidate or a
direct-read fallback.

The optional `allow_direct_graph` capability retains only bounded plain legacy
`graphify-out/GRAPH_REPORT.md` query hints, labelled `unassessed`. Reports with
frontmatter are withheld: that directory is not a governed source catalog and
cannot be used as a typed/current dependency bypass. Session-start does not
read graph reports outside the governed Markdown catalog.

Direct exports remove the entire `mind2one.sources` property before ordinary
secret redaction and Unicode chunk slicing. This is a constant projection,
including historical and untyped references, rather than a per-source hidden
count. Remaining fields and the body are not rewritten on disk. A chunk's
`revision` continues to identify the original raw file; offsets and
`totalChars` refer to the exported, redacted projection. That projection is not
a new canonical document or a valid replacement for its stored P2 binding.

The conservative v1 block parser cannot validate nonempty source declarations.
It marks them opaque and blocks current reuse instead of silently erasing their
dependencies. An explicit empty `sources: []` remains ordinary legacy context.
Use the strict JSON product format for typed, review-bound source relationships.

## Synthetic verification

`tests/test-source-freshness.py` uses only temporary vaults. It verifies actual
CLI/MCP paths in Codex and OpenCode profiles, source edits/deletion/restoration,
rename by stable ID, transitive dependencies, source withdrawal/profile
narrowing, invalidated P2 sources, historical/independent semantics, malformed
current references, opaque v1 sources, denial before the body reader, snapshot
budgets, policy/root replacement, and drift immediately before output.

The existing P2 review suite now uses an explicitly historical synthetic source
for its binding-only fixture; its source-relation mutation still invalidates
the P2 binding. Actual current full-SHA dependencies are tested here rather
than treating a nonexistent `source-revision-1` as current evidence.

`tests/test-event-backed-conclusions.py` adds synthetic event revisions,
withdrawal, policy-only revocation, upstream holds, mixed-type graph budgets,
old-reader refusal, final-output races, schema 3/MCP negotiation, and exact
review-writer append checks. The separate `event-backed-markdown.json` semantic
vector leaves prior Markdown and event hash vectors unchanged.
