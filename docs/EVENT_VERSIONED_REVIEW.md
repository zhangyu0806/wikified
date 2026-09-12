# Opt-in version-bound JSONL review (v4)

This is an explicit local CLI capability, not a default migration, cloud identity
service, native client release or product event-review inbox. `llm-wiki-event`
and Agent MCP still create v3 proposals. Existing v1–v3 read compatibility remains;
old approvals do **not** become content-bound merely by upgrading the reader.

## One append-only chain

`llm-wiki-event-review` appends a v4 revision for accept, correct, reject, withdraw
or restore. The original row stays intact. Every new row has a new `id` and
`memory_id`, while `record_id` retains the original `event:<id>` identity.
`review.target_id` and `review.target_revision` bind the source version. A
separate content digest binds the new payload, including its access policy,
provenance, timing and replacement edges.

- Accept a pending proposal (or explicitly re-confirm a legacy approved row).
- Correct pending/approved content with an explicit summary/details replacement.
- Reject pending content; withdraw approved content with a reason.
- Restore rejected/withdrawn content with an explicit reason and a new binding.
- An accepted/corrected proposal with inherited replacement edges requires
  `confirm_supersedes: true`. Global targets also require explicit confirmation.
- No request may supply actor/time/ACL. The new row inherits the target's scope.
- Concepts are classification, not approval: changing them changes the full CAS
  revision but not content or source-version binding. Strings are not trimmed,
  EOL-normalized or Unicode-normalized by fingerprinting.

The complete reader checks own bindings, source links, scope, transitions and
duplicate requests/forks before computing terminal edges or applying profile
visibility. Withdrawal closes the reviewed version; its ancestors remain closed.
Missing or changed sources, broken bindings and conflicting branches fail the
**whole event snapshot** explicitly. They never silently revive an earlier row.
This availability tradeoff is intentional; repair requires inspecting the
history/backup, not deleting a troublesome revision or recomputing its hashes.
Do not manually edit v4 rows or the semantic fields of their reviewed sources.

## Portable fingerprints

Four SHA-256 domains distinguish record CAS, source binding, reviewed payload
and policy. Input is compact UTF-8 JSON `[domainLabel, taggedTree]`.

```text
null       -> ["null"]
boolean    -> ["boolean", "true" | "false"]
string     -> ["string", exactUnicodeText]
number     -> ["number", bigEndianBinary64Hex]  (-0 becomes +0)
array      -> ["array", [taggedElements...]]
object     -> ["object", [[taggedKey, taggedValue]...]]
```

Object keys sort by Unicode codepoint. Lone surrogates, nonfinite numbers,
unsafe integers and nesting beyond 64 are rejected. This avoids cross-language
number-string and surrogate-order differences. Unknown payload fields remain in
the digest; reserved reader annotations `__memory_id`/`__governance` are excluded.

| Domain | Input |
| --- | --- |
| `wikified-event-record/v1` | Entire decoded row, including review and concepts |
| `wikified-event-target/v1` | Row excluding concepts |
| `wikified-event-content/v1` | Row excluding review and concepts |
| `wikified-event-policy/v1` | `[record_id or memory_id, project, domain, sensitivity, sorted target_agents]` |

The v4 `review` has version 1, algorithm `wikified-event-review/v1`, decision,
target identity/revision, content/policy revisions, server actor/time, reason,
request ID and request fingerprint. IDs use the existing 1–128 ASCII profile
syntax; review timestamps are real UTC `YYYY-MM-DDTHH:mm:ss.sssZ`. Reasons are
bounded to 1,000 Unicode codepoints. Payload strings requiring credential
redaction are rejected before review; no unseen sanitized replacement is approved.

## Local use after a deployment checkpoint

Only a trusted **operator/review process** receives the independently configured
`LLM_WIKI_EVENT_REVIEW_ACTOR` and `LLM_WIKI_EVENT_REVIEW_CAPABILITY`. Do not put
these in an Agent harness environment, shared vault, Git, command arguments,
logs or screenshots. The capability must be a separate random 32–512 printable
ASCII value. No actual value is provisioned by this change.

1. Set an explicit `LLM_WIKI_ROOT` to the authorized vault. The command never
   guesses the default data root.
2. `llm-wiki-event-review --inspect <event-id>` returns the current event plus
   `expected_revision`, without writing or approving. Inspection is an OS-local
   operator read, not an Agent MCP API.
3. Send the reviewed request as JSON on stdin to `llm-wiki-event-review`:

```json
{
  "action": "correct",
  "target_id": "<inspected event id>",
  "expected_revision": "<inspected record revision>",
  "request_id": "<new unique request id>",
  "reason": "Reason for this correction",
  "summary": "Replacement explicitly checked by the reviewer",
  "capability": "<supplied independently; never save in the vault>"
}
```

The CLI checks authority, then locks `.memory.lock`, rereads a complete bounded
snapshot, checks CAS/currentness and appends+fsyncs one row. Same-request retries
survive process restart and do not append twice. A late retry after a subsequent
revision returns conflict instead of presenting the old approval as current.
Malformed ledgers, unsafe links, size/physical-line/file-count limits and stale
versions never cause a new review row to be appended. An interrupted write may
leave a partial physical line: the complete reader fails closed pending recovery;
this is not a multi-file database transaction or power-loss recovery system.

Agent MCP has no review tool and never receives the review capability. Its
`record_event` always selects AI/pending regardless of actor labels in arguments.
Legacy CLI approval cannot reopen a v4-superseded proposal; ordinary v3
`--supersedes` cannot target v4 rows to bypass explicit restore/withdraw actions.

## Compatibility and boundaries

The pinned earlier reader `a0fd743` fails closed on a v4-containing event snapshot
instead of ignoring v4 and replaying its predecessors. Consequently, enabling v4
before **all** relevant readers are upgraded makes event recall unavailable to
old readers. Rolling back requires a reviewed data/reader compatibility plan,
not deletion of withdrawal records. Ordinary Markdown remains a separate path.

Legacy v3 human approvals retain their earlier, unbound semantics; they are not
silently migrated or newly attested. This release does not auto-select the new
review CLI. Deployment must decide the legacy policy and writer cutover before
enabling it on real data. Historical v4 audit requires retaining its source rows.

Hashes are not signatures: an unrestricted same-OS writer can forge the entire
ledger, labels and hashes. OS/process separation and later hosted authentication
remain necessary. Derived Markdown/context invalidation and an MD/JSONL unified
inbox belong to the source-aware context phase; v4 does not claim to erase every
previously generated derivative or already-delivered model context.

## Verification

`python3 tests/test-event-versioned-review.py` uses only synthetic temporary
vaults: independent TS/Python golden vectors, action/withdrawal chains, competing
processes, persisted retries, source tampering, malformed/linked ledgers, resource
bounds, exact Unicode JSONL normalization, pinned old-reader rejection, and actual
Codex/OpenCode MCP proposal/search processes without review authority.

Also run event snapshot/lifecycle, JSONL locking, security and Markdown review
regressions. The Mind2One memory-loop probe separately verifies the real MCP
proposal → independent CLI correction → second-profile recall → withdrawal →
explicit restore flow. Tests do not activate installed links or touch real notes.
