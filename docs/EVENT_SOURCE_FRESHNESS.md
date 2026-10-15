# Opt-in source-bound event conclusions (v5)

An independently authorized human review can bind an event conclusion to stable
Markdown source identities and exact raw revisions. This appends
`llm-wiki-memory-event/v5` with `source: "human-review/v5"`; it does not rewrite
the proposal or automatically reinterpret provenance in old records.
`llm-wiki-event` and MCP `record_event` still create v3 proposals. MCP has no
review authority and cannot inject these bindings through its writer arguments.

## Explicit review and rebinding

The existing local [review CLI](EVENT_VERSIONED_REVIEW.md) accepts an optional
`source_bindings` field only on `action: "correct"`. A correction containing
only this field is valid; summary/details remain unchanged. Each element has
exactly these four fields:

```json
{
  "kind": "document",
  "id": "note:synthetic-source",
  "revision": "sha256:<64 lowercase hexadecimal characters>",
  "relation": "current-derived"
}
```

The array must contain 1–256 unique source IDs. An ID is 1–128 ASCII characters
matching `[A-Za-z0-9][A-Za-z0-9._:/-]*`. Paths, labels, and client-supplied policy
are not accepted. `revision` hashes the complete raw Markdown bytes, including
frontmatter; projected text and fragment revisions cannot substitute for it.

The field is an **explicit patch**: update the revision of a selected existing
ID, or append a new source. Existing IDs cannot change kind or relation. All
unselected edges keep their exact previous revisions. There is no delete,
automatic refresh, relationship downgrade, or implicit source replacement.
The accumulated array remains bounded to 256. Partial rebinding may therefore
leave an event stale or unavailable. Correcting text alone, accepting, withdrawing,
or restoring never refreshes its sources.

The review CLI resolves each selected identity through a complete, trusted,
`human`-authorized Markdown snapshot. It requires exact revision agreement,
approved/review-bound content where required, reusable source lineage, and no
active source-review hold. It rechecks this generation before append and before
returning. Actor, event ACL, temporal validity, CAS, idempotency, append locking,
redaction, and byte/count limits remain unchanged. Binding is not a grant of
assistant access: every subsequent assistant read checks that assistant's policy.
Hashes are integrity bindings, not signatures against an unrestricted OS writer.
With source-review enabled, this check reuses the writer's verified exclusive
lock descriptor rather than trying to acquire a second shared lock. A race
detected after append may leave a stored revision without a success response;
refresh current state before deciding whether to retry. Subsequent reads still
check its sources, and idempotency prevents duplicate successful retries.

Selected Markdown can itself depend on reviewed events. These dependencies are
assessed in one bounded typed graph, without resetting depth or visited budgets.
Before writing, the reviewer rejects a selected source whose current dependency
closure includes the event being revised: the new version would immediately
invalidate that source. Source inputs remain document-only; this is not a new
event-to-event review API.

The writer distinguishes its own append from an unrelated source-generation
change. Under the existing exclusive lock it pins the old event manifest and
target bytes, accepts only the exact old prefix plus its serialized new line,
keeps every other file and authority observation unchanged, then rebuilds and
rechecks the complete source graph. A new monthly file may advance only its
expected directory/file observation. Unexpected extra appends, prefix edits,
or another file's change fail without a success response. This preserves the
existing post-write failure boundary above; it is not rollback or an OS-wide
atomic transaction.

First adoption on v3/v4 requires the absence of a `sources` property. Previously
unknown `sources` data is neither discarded nor silently assigned new meaning;
such an upgrade is rejected for a separately reviewed recovery process. Existing
legacy content and fingerprints stay unchanged.

## Read semantics and limits

For `current-derived`, an event is reusable only when every referenced document
exists with its pinned raw revision and is authorized, trusted, and reusable for
the requesting profile. Source changes, disappearance, review withdrawal,
permission loss, holds, or invalidated upstream Markdown dependencies withhold
the event. Cross-project dependencies are checked; the query's project is not
an access boundary. The existing graph limits apply across the event-to-document
edge too: depth 32, visited nodes 256, references per node 256, snapshot nodes
10,000, in addition to the existing bounded Markdown catalog.

An initial explicit human binding may instead use `historical-citation` or
`independent-judgment`. These retain provenance without asserting that the
conclusion tracks a current source. Their assessment is `unassessed`; subsequent
source changes do not invalidate that relation. Selecting one requires the same
trusted source check at review time. Existing current-derived edges cannot be
changed to either relation through correction.

The event's own ACL, review state, lifecycle, and validity window remain required.
The complete terminal chain is reduced **before** source filtering, so withholding
a newer event never revives its predecessors. Broken v5 bindings or chains fail
the whole event snapshot, as v4 did; an otherwise valid event with an unavailable
Markdown dependency is individually withheld.

Search, file recall, and context preparation share this check. MCP delegates to
these same CLIs. Sources are never copied into assistant result metadata; only a
generic freshness state is attached to the internal governance projection.
Event and source observations are checked again after result construction and
before output. This detects observed filesystem races, not a multi-file OS
transaction, and cannot retract already delivered context. Session-start and
Markdown read-page retain Markdown output, including event-backed conclusions
only when their complete source graph passes the same gates.

Existing context schemas and budgets are unchanged: at most 20 selections,
12,000 Unicode codepoints per item and 40,000 total. No source body is added to
an event's context text. The opaque search-generation observer also retains its
existing protocol and fences. Explicit context schema 3 additionally prepares
eligible reviewed events as conclusion sources with authorized governance;
see [continuing context](CONTINUING_CONTEXT.md).

## Human catalog and compatibility

`llm-wiki-event-catalog --schema-version 2` returns `reviewBinding: "v5"` and
`sourceFreshness: {state, reuseAllowed}`. States are `current`, `unassessed`,
`stale`, `incomplete`, and `unavailable`; only the first two permit reuse in the
catalog's **human scope**. This is not an assistant authorization result.
Source identities, paths, labels, and counts are not returned. The catalog keeps
authorized events whose sources are unusable so a human can review them, and its
aggregate revision changes with their generic freshness state.

Catalog v1 remains the default with its original shape. It fails closed if an
authorized visible v5 event requires a representation it cannot express; hidden
v5 events do not trigger this capability error. New clients must explicitly
negotiate v2 and must not silently downgrade. v1–v4 event reading and all existing
review/hash algorithms remain supported without migration.

Readers predating v5 fail the whole event snapshot on the unknown schema, rather
than ignoring it and replaying ancestors. Upgrade every relevant reader before
writing v5; rollback needs a reviewed compatibility plan, not deletion of
versions. There is no automatic migration, retroactive provenance inference,
event-to-event dependency graph, or durable hold on an event in this slice.

Synthetic verification: `python3 tests/test-event-source-freshness.py`, plus the
existing event review/catalog/snapshot, Markdown source, context, fragment and
MCP suites. The separate `source-bound-event-review.json` fixture adds v5
cross-language hashes without changing the eight legacy vectors.
