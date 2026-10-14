# Continuing a topic with fixed source versions

prepare_context is a read-only MCP tool. Its selections narrow the authority of
the server's startup profile; topic membership never grants permission. The
same mechanism is exposed by llm-wiki-context --agent-profile codex --request
<JSON>. Operator configuration selects the root and profile. MCP callers cannot
supply either. Human/raw profiles are unsupported.

The request is {"schemaVersion":1,"selections":[{"kind":"page","memoryId":
"note:example","revision":"sha256:<64 lowercase hex characters>"}]}.
MCP accepts selections and an optional schemaVersion, defaulting to 1.
Select explicit stable Markdown identities or the persistent record_id of
reviewed events. Moving a Markdown file preserves its selection if its bytes
and explicit identity remain unchanged. An event's revision uses the existing
event-record hash, not a hash of its displayed summary.

Each response item repeats the requested selection and has one status:

- ready: exactly that authorized version, a relative locator and redacted text.
- changed: an authorized newer revision; no body. Explicitly reselect it.
- unavailable: no authorized current source; no existence, title or denial
  distinction. This also covers pending, withdrawn and stale derived records.
- too-large: an authorized exact version exceeds the output budget; no body.

Up to 20 unique identities, 12,000 Unicode code points per record and 40,000
total are permitted. No body is silently truncated. Malformed or incomplete
catalogs, duplicate identities, generation changes and policy changes fail the
entire request. Existing source-review holds and source freshness checks apply.
The wrapper reuses the complete engine discovery/review/ACL pipeline, including
terminal event revisions. The final fence is a bounded observation check, not
a transaction against arbitrary same-OS writers.

Treat returned text as untrusted evidence. It cannot authorize actions or become
an instruction/task queue. Recheck on every continuation and before export.
Already delivered text cannot be revoked from another process or a clipboard.
A client should clear its preview on file/permission/session changes and must
never substitute its human-readable local catalog for a denied assistant read.

For selected conclusions, use existing record_event with a brief summary and
source ID/revision in details. It remains ai-proposed/pending; explicit human
review is separate. Do not capture full conversations or secrets.

Verification: python3 -B tests/test-continuing-context.py covers both MCP
profiles, selected proposal creation, human-review acceptance/correction and
withdrawal, fixed versions, explicit reselect, redaction, budgets, stale
dependencies, denied sources and generation failures. These are synthetic
protocol sessions, not validation of live Codex/OpenCode model conversations.

## Authorized page fragments

Opt in to long-page discovery with:

```bash
llm-wiki-enrich --agent-profile codex --query "topic" --source-scope pages --page-fragments --json
```

`--page-fragments` requires a nonempty query, pages scope and JSON output. It
cannot be combined with session-start or read-page. MCP `search_pages` exposes
the same opt-in as `pageFragments: true`; the default remains mixed Markdown
recall. Page ranking, catalog bounds, output limits and all authority checks
are unchanged. Corrupt event snapshots still fail the entire query.

Only pages whose complete safe projection exceeds 12,000 Unicode code points
receive a top-level `fragment` object with `projectionRevision`, `offset` and
`length`. `memory_id` and `revision` continue to identify the stable source and
hash its raw bytes. The projection first removes private source-reference
metadata, then redacts the **whole** projected page. `projectionRevision` is
`sha256:` plus the SHA-256 of this full projection encoded as UTF-8. Coordinates
count Unicode code points in that string, including its safe frontmatter, not
bytes, UTF-16 units, the Markdown body alone or a joined search excerpt.

The range starts up to 320 code points before an actual case-insensitive query
term match. The selector prefers a line with more distinct query terms, then
a longer matching term, then the earliest matching line; it anchors to the
first actual match on that line, even on a long single line. Case conversion
does not change coordinate calculation. If only a filename or redacted material
matched, the range starts at zero. Its length is 8,000 code points, or the
remaining text near the end. Search `text` is an exact prefix of that range,
at most 1,200 code points. Under the existing output budget, fragment mode first
reserves the ranked candidate prefix's identities, range metadata and whole-page
excerpts. Remaining space is shared using one maximum preview length for all
retained fragments. A fragment preview can therefore be very short or empty;
it must not displace later whole-page candidates. If even metadata and whole-page
excerpts exceed the budget, the existing prefix/truncation limit still applies.
Ordering and the maximum output size remain unchanged. Clients obtain the actual
selected text through context and may derive a longer display excerpt from that
authorized read; search preview length is not the selected range length.
`truncated` indicates whether more text exists **inside the selected range**;
it does not indicate whether the page has more text beyond the range. Short
pages retain the existing search shape and excerpt behavior.

Pass the complete selection to context schema 2 (or MCP `prepare_context` with
`schemaVersion: 2`):

```json
{
  "schemaVersion": 2,
  "selections": [{
    "kind": "page-fragment",
    "memoryId": "note:example",
    "revision": "sha256:<raw page hash>",
    "projectionRevision": "sha256:<full safe projection hash>",
    "offset": 70000,
    "length": 8000
  }]
}
```

Hashes must contain 64 lowercase hexadecimal characters. Offset must be a
nonnegative safe integer, length an integer from 1 to 12,000, and their sum must
also be a safe integer. The entire
range must fit the current projection; no implicit clamping or silent truncation
is permitted. A raw revision mismatch returns `changed` only after authorization;
a projection mismatch or out-of-range selection returns `unavailable` without
text or current projection metadata. Exact fragment reads return the selected
text and `governance: {domain, sensitivity}` from the current authorized page
for conclusion inheritance. They retain all source freshness, review binding,
persistent hold and final policy/page/event/ledger generation fences.

Schema 2 also accepts unchanged whole-page and event selections. All selections
share the same limits: 20 unique identities, 12,000 code points per item, 40,000
total. Selecting both a page and its fragment, or two fragments of one identity,
is rejected. Schema 1 retains its exact selection shape and behavior: fragments
are rejected and an oversized full-page read remains `too-large`. Existing
`read_page` chunking is independent and does not replace context's whole-pack
checks. A fragment is partial evidence; clients should preserve its source,
revisions and range, and must not present it as the complete document.

`python3 -B tests/test-page-fragments.py` exercises real CLI and MCP sessions
with synthetic long-tail pages, Unicode and redaction coordinates, legacy
compatibility, shared budgets, authorization, source and review denials, holds,
version mismatches and generation races. No installed vault is used.
