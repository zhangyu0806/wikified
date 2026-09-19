# Explicit, durable human source-review holds

This opt-in local slice lets a person **pause reuse of one existing reviewed AI
claim**. It is separate from source freshness: changing a source can make a
claim stale, but a profile's temporary inability to read a source never writes
a global hold automatically. Reads do not initialize, migrate, append, approve,
release, or modify Markdown.

The CLI and enabled ledger locking are currently **POSIX-only**. On Windows,
use the existing Ubuntu WSL environment. TypeScript shares the pure contract;
that is not evidence of a native Windows/mobile writer or four-client rollout.

## Activation and authority

The operator explicitly configures `LLM_WIKI_ROOT` to a canonical absolute
synthetic or intended vault path, `LLM_WIKI_SOURCE_REVIEW_CAPABILITY` to an
independent human-review capability, and `LLM_WIKI_SOURCE_REVIEW_ACTOR` to the
authorized actor ID. Do not put capabilities into Git, Markdown, logs, or shell
history. Requests are bounded JSON on standard input to
`bin/llm-wiki-source-review`; there are no command-line mutation arguments and
no MCP mutation tool. Even status/inspection require the capability.

An untouched access policy has no `source_review` field: legacy-off behavior.
Explicit initialization adds only this marker after durably creating genesis:

```json
"source_review": {"version": 1, "ledger_id": "<32 lowercase hexadecimal characters>"}
```

The authoritative history is `memory/source-review/ledger.jsonl`, not a cache.
Once enabled, an absent/empty/damaged/oversized/wrong-ID ledger fails closed.
Do not delete its directory to repair a cache, or remove the policy marker to
bypass a problem. Inspection/recovery is a deliberate operator action.

Initialization checks the exact old access-policy bytes, creates genesis with
exclusive creation and fsync, then atomically replaces the policy marker. A
failure before marker activation can leave an unactivated genesis. Only an
exact matching request ID, request hash, and actor may finish that activation;
conflicting existing files are not overwritten or silently migrated.

## JSON requests and responses

All requests include `action` and `capability`. The capability is excluded from
hashes, responses, and persisted entries. These are the remaining fields:

| Action | Required additional fields |
| --- | --- |
| `status` | none |
| `inspect` | `record_id` |
| `initialize` | `request_id`, `expected_policy_revision` |
| `hold` | `record_id`, `request_id`, `reason`, `expected_ledger_revision`, `expected_record_revision`, `expected_policy_revision` |
| `release` | the same fields as hold |

`status` returns `enabled`, `ledger_revision`, and `policy_revision`.
`inspect` additionally returns the requested `record_id`, `record_revision`,
`binding_revision`, `held`, `hold_reason`, `held_at`, `can_hold`, `can_release`,
`review_verified`, and `release_lineage_current`. It exposes no body, source
identities, source labels, actors, request/review history, or vault-wide counts.
Resolve a persisted `memory_id`, never submit a filesystem path as record
identity. Invalid or non-persisted identities are unavailable, not an invitation
to create or migrate a note.

Inspection applies the current **human ACL**, independently of AI reuse gates.
This lets an authorized person see a held, pending, unbound, or mismatched
record's control state without granting AI access. It does not relax domain,
sensitivity, target, or review-state authorization. In particular, withdrawn
records have top-level `rejected` governance, so inspecting them requires the
human profile to explicitly authorize `rejected`; the default policy does not.
Inspection returns no Markdown and does not write the note or ledger. It reads
one complete bounded snapshot only after header authorization, with file,
ancestor-directory, catalog-manifest, root, and policy-generation fences.

- `binding_revision` is null when no validly shaped P2 binding exists. A digest
  can still be present for a pending or mismatched record; it is not approval.
- `review_verified` means the complete current bytes pass P2 review validation
  with the same persisted identity. This alone is not source-current, ACL, or
  release authorization.
- `hold_reason` and `held_at` are null without an active hold; otherwise they
  contain only the fixed reason code and hold timestamp.
- `can_hold` and `can_release` are current-snapshot UI advice, false while the
  ledger is disabled. A mutation independently rechecks capability, human ACL,
  all CAS values, eligibility, and full release-history conditions under lock.
- `release_lineage_current` is null before any release. Afterwards it is true
  only if the current record is P2-verified and its complete valid history
  contains the latest released binding (or that binding is current). It can
  remain true while held; a hold is a separate gate. A rollback to a previously
  accepted but pre-release note makes this false even if `review_verified` is
  true. It does not claim all other reuse gates have passed.

The policy CAS is SHA-256 of the exact current `policy/access.json` bytes; the
record CAS is SHA-256 of exact Markdown bytes; the ledger CAS is its current
entry digest. Re-inspect before forming a new operation. Exact request retries
are idempotent without appending again, but they still recheck current record
authorization; an old successful request does not grant permanent visibility.
Conflicting reuse of a request ID is rejected.

Mutation responses contain `action`, `entry_revision`, current
`ledger_revision` and `policy_revision`, `record_id`, `held`, and `idempotent`.
Initialization has null `record_id`/`held`. A retry returns current held state,
not an assertion that a historical hold still controls the record.

## Hold and release rules

A hold is permitted only for an existing persisted ID whose Markdown is v2,
AI/mixed authored, currently P2-verified, and declares at least one document
`current-derived` source. Hold reasons are fixed codes:
`source-change`, `source-withdrawal`, `source-unavailable`, `manual-concern`.

The hold is keyed by the record ID, not its current filename, bytes, source
relations, or approval timestamp. Restarting, restoring old bytes, changing a
relation to independent judgment, or adding a fresh review does not clear it.
Retrieval omits held records before body reads and removes them as dependency
targets, so their current-derived descendants cannot silently reuse them.

Release is a separate explicit human operation with reason `human-reverified`.
It requires all three current CAS values, a different verified P2 binding and
different raw record revision, the held binding's exact digest in the complete
valid review history, a current binding timestamp no earlier than the hold,
the same P2 policy revision, and a current document-derived declaration. The
CLI never edits the note to create this proof. Review first, then explicitly
release. Release does not grant access or make an otherwise stale source
current: ordinary ACL, P2 binding, and source-freshness gates still apply.

Each record also retains its latest released binding as a **minimum review
history requirement**. A currently reusable record must carry that exact valid
P2 binding or continue it in its complete valid review history. Restoring the
old pre-hold Markdown bytes after release cannot revive the old claim. A new
hold does not erase this requirement, and a later release advances it. This is
derived from authoritative ledger history, not a second Markdown body or a
cache entry; it still cannot prevent rollback of the entire vault and ledger.

## Integrity, locking, and failure limits

The ledger has strict fields, monotonically sequenced hash-linked rows, unique
request IDs, nondecreasing UTC-millisecond timestamps, and a legal hold/release
state machine. Limits are 4,096 rows, 4 MiB raw bytes, and 16 KiB per physical
line including LF. Blank, duplicate-key, partial, invalid-UTF-8, and unterminated
lines are rejected. Entry/binding/request digests reuse the existing tagged
JSON canonicalization without changing the P2 semantic hash.

Writers share `memory/.memory.lock` with the existing event writer. Readers of
an enabled ledger take a nonblocking shared lock; writers take a nonblocking
exclusive lock. Busy means unavailable, not indefinite waiting. Lock/policy/
ledger files reject symlinks and hardlinks, and root/directory/file generations
are checked around operations. Ordinary Markdown editors are not made
transactional by this protocol; generation drift causes conflict.

Appending is fsynced under the exclusive lock. If writing or verification
fails, the writer attempts to roll back **only its exact newly appended tail
on the same inode** before readers resume. A successful rollback preserves the
prior hold. This is not an unconditional rollback guarantee under multiple
I/O failures. If fsync and rollback both fail, a complete authorized release
may already exist. Similarly, initialization's policy rename can happen before
its directory fsync fails.

Those outcomes use a distinct **uncertain** diagnostic and exit code 3, not a
success response and not a claim that state is unchanged. Stop automatic reuse
and independently inspect/recover the durable state before retrying. Exit code
2 is the fixed ordinary unavailable/conflict diagnostic; configured corrupted
history still fails closed. No automatic recovery silently discards history.

The ledger is not cryptographically signed or anchored outside the vault.
Hashes detect inconsistent changes, not an attacker able to replace an entire
vault with a historically valid policy/ledger prefix. This slice does not claim
protection against complete-history rollback, arbitrary same-OS tampering,
cross-device conflict resolution, or durable distributed transactions.

## Verification

`tests/test-source-review-contract.py` and the matching TypeScript contract use
the same 19 golden vectors. `tests/test-source-review.py` uses temporary vaults
for real CLI capability/CAS/retry, persistence, history-gated release, corruption,
symlink/hardlink, lock contention, orphan initialization, and injected fsync/
rollback/activation failures. Metadata-only inspection also covers pending and
withdrawn human ACLs, exact response fields, advisory eligibility, latest-release
lineage, complete bounded snapshots, and record/manifest/directory/root/policy
drift. `tests/test-source-review-retrieval.py` verifies
actual request-time held-record and dependency exclusion. No test uses or
initializes the user's real memory root.
