# Markdown version-bound review (P2)

This is a reader contract, not a claim that every client or historic record has
been migrated. The engine uses only Python standard-library dependencies.

## Content and policy bindings

New reviewed Markdown uses `mind2one.version: 2`. Its `mind2one` value is a strict
single-line JSON flow object inside YAML frontmatter, either on the same line as
the key or on one following indented line. Top-level governance keys
may use the quoted/unquoted subset documented and tested by P1. General YAML v2
objects are rejected rather than partially interpreted.

The `m2o-semantic-v1` digest is SHA-256 over compact UTF-8 JSON of this fixed array:

```text
["m2o-semantic-v1", id, bodyWithLF, kind, origin, agentOrNull,
 operationIdOrNull, actionOrNull, scheduledForOrNull, dueAtOrNull,
 completedAtOrNull, sources]
action = [level, status, metricOrNull]
metric = [startHex, currentHex, targetHex, unit, basisOrNull]
source = [kind, id, labelOrNull, revisionOrNull, relationOrNull, locatorOrNull]
```

Body CRLF/CR becomes LF; trailing newlines and Unicode normalization are not
changed. Surrogates are rejected. Finite metric numbers are binary64, big-endian,
16 lowercase hex digits; negative zero becomes positive zero. Optional missing,
null, or empty strings become null. Sources retain their order. Tags, PARA,
topics, project/area associations, action parent, captured date and review-after
are not semantic approval fields. Unknown data is not granted new semantics.

The separate policy digest covers
`["m2o-policy-v1", memory_id, domain, sensitivity, sortedTargets, projectOrNull]`.
Changing either bound projection invalidates current approval. Consumers
recompute both digests; they never trust a stored projection as current content.

The binding records a human actor, decision, current content/policy revisions,
request identifier, reason and strict UTC review timestamp
(`YYYY-MM-DDTHH:mm:ss[.sss]Z`, a real calendar date). Actor/request identifiers
contain 1–200 Unicode codepoints; reasons contain at most 1,000. Lone surrogates
are invalid. Withdrawal/restoration requires a nonblank reason. At most 32 prior
bindings form a record-id-consistent previous-revision chain; corrupt history
cannot produce a verified current record. No previous full body is embedded.

## Reading and compatibility

Search, direct `read_page`, legacy ranking and session-start use one authorized
snapshot path. It reads bounded governance first; rejected ACLs never cause a
full-body read. An authorized page is read completely (at most 2 MiB including
frontmatter), checked for observed replacement/change, and its review binding is
recomputed before content is returned or ranked. Header limit remains 16 KiB;
search/display budgets remain 64 KiB and direct-page output remains 256 KiB.
Large or changing files are denied, never approved from a truncated prefix.

- Ordinary human notes do not require an AI review event.
- Governance-free legacy notes and complete legacy ACL quartets retain P1 policy.
- v1 AI/mixed accepted/corrected records are `legacy-unbound`; default AI durable
  recall excludes them until explicitly reviewed against the current content.
- Pending, rejected and withdrawn content is excluded from durable AI context.
- These CLI search/read/session-start paths apply current-trust checks even to
  the `human` profile. Its broader ACL does not convert drafts into durable
  memory; the product's explicit human review view is the draft-inspection path.
- Invalid, unknown-algorithm, unbound or mismatched approvals cannot grant access.
- Human inspection, export and explicit temporary draft discussion are different
  operations; this reader does not add an AI history/read bypass.
- v1 block metadata remains supported for legacy governance/authorship. The
  bounded block parser does not advertise a full semantic content digest.

P1 JSONL v3 lifecycle and access checks remain unchanged. Old v3 events do not
acquire a version-bound approval merely because this Markdown reader exists.

SHA-256 is an integrity binding, not a signature. A process with unrestricted OS
write access can forge both file contents and metadata. Authentic reviewer
authority must be enforced by the writer boundary; separate roots/OS permissions
remain necessary. Stat checks detect observed drift but do not create a
transaction against arbitrary unsynchronized filesystem writers.

## Verification

```bash
python3 tests/test-versioned-memory-review.py
bash tests/test-page-governance.sh
bash tests/test-policy-prefilter.sh
bash tests/test-security-boundaries.sh
```

The shared synthetic golden file independently checks Python against the
TypeScript content/policy hashes and assessment states, then exercises both
Codex/OpenCode profiles through CLI search, direct read, session-start and MCP
search/read. Additional fixtures alter content beyond the search excerpt,
semantics, ACLs, chronology and history, and instrument ACL-before-body reads.

All fixtures are synthetic. These checks do not modify installed links, running
services or real memory. Deployment still requires a separate compatibility and
backup checkpoint; old files are not automatically rewritten or approved.
