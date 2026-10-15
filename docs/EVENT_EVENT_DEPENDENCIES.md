# Explicit reviewed event dependencies (v6)

An independently authorized human can now bind an event conclusion to another
reviewed event. This extends the [v5 document-source workflow](EVENT_SOURCE_FRESHNESS.md)
through a new `llm-wiki-memory-event/v6` record and `source: "human-review/v6"`.
Existing v4/v5 records, hash algorithms and frozen vectors are unchanged.
Ordinary CLI/MCP event capture still writes v3 proposals; it cannot attach source
authority or approve them. No model call, automatic migration or inferred source
binding is part of this change.

## Explicit patch and compatibility

The independently paired review CLI accepts `source_bindings` only on `correct`.
Each selected source has exactly `kind`, `id`, `revision`, and `relation`:

```json
{
  "kind": "event",
  "id": "event:0000000000000001",
  "revision": "sha256:<64 lowercase hexadecimal characters>",
  "relation": "current-derived"
}
```

An event ID is its persistent `record_id` (`event:` and 16 lowercase hexadecimal
characters), not the physical ID of a particular review row. Its revision is the
existing complete event-record digest, not a hash of displayed text. A document
reference still uses its stable Markdown identity and complete raw-byte digest.
No locator, title, actor or client-supplied policy is accepted as source authority.

A patch containing an event source upgrades an eligible v3/v4/v5 target to v6.
Document-only adoption still produces v5. Once v6, all later review actions keep
v6. Its mixed document/event array is bounded to 1–256 unique IDs. A patch may
append a source or update an existing source's exact revision; it cannot remove
an edge, change its kind/relation or refresh unselected edges. Duplicate IDs,
including cross-kind duplicates, fail. Partial rebinding leaves remaining stale
dependencies stale. A legacy unknown `sources` property is never silently
adopted. The existing v5 validator remains strictly document-only.

Catalog v3 is explicit: `llm-wiki-event-catalog --schema-version 3`. It adds
`reviewBinding: "v6"` to the v2 projection and retains only the generic
`sourceFreshness: {state, reuseAllowed}`. Source IDs, paths, counts and rejection
causes are not disclosed. The state is in the human catalog's authority scope;
it does not attest assistant access. Default catalog v1 and explicit v2 reject
the entire response when an authorized visible v6 record is present, including
when a query would omit it. Hidden v6 records do not trigger this version check.
Readers predating v6 reject the unknown schema for the complete event snapshot;
they must not skip it and revive an ancestor. Upgrade relevant readers before
writing v6; rollback needs a reviewed compatibility plan, not history deletion.

## Authorization, dependencies and races

At selection time every source, for all three relations, must be current,
human-authorized, independently reviewed, at its exact version, and reusable
through its full existing source graph. Eligible event leaves are v4 without
opaque sources, v5, or v6. Pending, rejected, legacy, hidden, stale, held-upstream,
expired and otherwise unassessable events cannot be selected. Finding an event
in the pre-freshness graph alone does not grant source eligibility.

`current-derived` follows the complete typed document/event graph. Source
revision changes, withdrawals, ACL changes, missing sources or ancestor Markdown
holds withhold the dependent event under each requesting assistant's own policy.
Lifecycle reduction precedes ACL/freshness filtering, so a denied current event
never revives its old versions. An explicit rebind plus human review is needed
after a source's revision changes; restoring a withdrawn source does not silently
update downstream revisions. `historical-citation` and `independent-judgment`
retain their existing `unassessed` meaning and do not track future source changes.
Existing current-derived edges cannot be downgraded to these relations.

The writer rejects direct self-selection and any selected source whose current
dependency closure includes the event being revised, including mixed
event→document→event paths. One typed graph retains the existing depth 32,
visited-node 256, per-node reference 256 and snapshot-node 10,000 limits.
Each selected source's complete graph is checked before and after the append.
As with v5, a successful review records the human decision; it does not promise
that the new target is ready for recall. A reusable depth-32 source plus the new
target edge reaches depth 33, and individually reusable branches may have a
combined closure over 256 nodes. Such reviewed targets remain `incomplete` and
withheld when read. Partial rebinding can similarly leave stale unselected edges.
Neither writing nor splitting sources resets or raises the read-time budgets.
The existing exclusive-lock and exact-own-append guard permits only the original
prefix plus this writer's new line; unrelated file changes, prefix edits and
extra appends reject the success response. A post-append failure may leave a
stored revision and requires refreshing current state; this is not rollback.

Search, context and event-backed Markdown reads share the graph. Context schemas
1/2 keep their wire shapes; explicit schema 3 additionally attests eligible v6
events with the same authorized `domain`/`sensitivity` fields as v4/v5. The limits
remain 20 selections, 12,000 Unicode codepoints per item and 40,000 total. Source
bodies are never implicitly expanded into event output. Complete output is
serialized and encoded before the existing final generation fence and single
write. These checks do not provide an OS-wide transaction or retract delivered
content. Direct durable holds on events, automatic invalidation queues and real
cross-assistant owner acceptance remain separate work.

Synthetic verification: `python3 tests/test-event-event-dependencies.py` and the
existing v4/v5, event-backed Markdown, graph, context, generation and MCP suites.
`tests/fixtures/event-event-review.json` freezes initial binding, exact rebinding
and mixed v5→v6 upgrade hashes for independent consumer checks.
