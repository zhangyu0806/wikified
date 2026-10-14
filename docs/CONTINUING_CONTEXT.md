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

The CLI retains the captured page, event, policy and enabled hold-ledger
observations until the complete response has been serialized and UTF-8 encoded.
It rechecks those observations immediately before one output write. A change
during rendering rejects the whole pack, including ready items earlier in the
selection order; a serialization or encoding failure emits no prefix. This also
applies to packs containing only unavailable or changed selections. The final
check does not make the subsequent pipe write atomic with filesystem writers.

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

## Bracketing a search across multiple context processes

A server that collects several context batches must keep all previews temporary
until it has checked the complete search's filesystem generation. The explicit,
read-only CLI mode is:

```bash
llm-wiki-context --agent-profile codex --observe-generation
```

It is mutually exclusive with `--request` and returns exactly `schemaVersion: 1`,
the fixed `profile`, and `generation: "sha256:<64 lowercase hexadecimal characters>"`.
Call it before candidate discovery and again after all batches have finished,
using the same canonical root and profile. Validate both response shapes and
require equal generations before publishing any accumulated result. On a failed
observation or mismatch, discard the entire temporary result and ask for a fresh
search. An empty result also needs the final observation. The caller's existing
overall deadline and process-concurrency limits apply to both observations.

The opaque digest binds the canonical root and ancestor identities, fixed profile,
policy bytes and complete stat signature, bounded page manifest and directory
topology, memory/event directory and event-file signatures, and the enabled
source-review snapshot including policy, ledger, lock and directory generations.
Capture is followed by verification before returning a digest. Byte strings are
hashed inside the engine; no paths, record identities, metadata, ledger contents
or text are returned. The observer never reads unauthorized page bodies, creates
a token store, or writes to the vault. Keep its digest internal to the calling
server, rather than placing it in a user response or persistent topic file.

Digest construction is included in the capture check. The CLI also retains the
same observations for another verification after serializing and encoding the
opaque reply, before its single output write. Both the initial and final
observation of a bracketed search therefore reject changes during rendering;
neither can publish a previously captured digest after a detected change.

Nanosecond ctime, mtime and inode signatures detect ordinary changes followed by
restoring the same bytes and mtime. Directory signatures additionally detect
transient page creation/deletion and subtree replacement even when surviving
Markdown file signatures do not change. Ancestors outside the vault are bound
by identity, without treating unrelated writes in shared parent directories as
vault changes. Unsafe paths, configured ledger damage and incomplete bounded
observations fail closed.

This is a filesystem observation fence, not an authorization grant or an atomic
transaction against arbitrary same-OS writers or filesystem snapshot rollback.
It does not freeze wall-clock event expiry. Each context batch still performs all
current authorization, review, source/version and text-budget checks; the final
observation adds cross-batch change detection for page-search previews. It cannot
revoke data already delivered to another process. The default CLI request and
MCP protocols are unchanged; this server-internal mode is not an MCP tool.

`python3 -B tests/test-search-generation.py` covers real subprocess observations,
profile/root binding, restore and directory races, source-review holds and lock
generations, rejection of unsafe/incomplete observations, and legacy requests.
`python3 -B tests/test-context-output-fence.py` adds deterministic serialization
and encoding barriers over real synthetic files: page/event/fragment source
changes, withdrawal, deletion, per-profile policy revocation, holds, event and
directory changes, restored bytes, root replacement, failure without prefixes,
and unchanged response bytes. These injected timing tests do not claim an
atomic filesystem transaction or live assistant/model acceptance.
