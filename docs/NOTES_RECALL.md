# Explicitly governed notes and revision-pinned reading

Wikified can retrieve Markdown from both `wiki/` and `notes/` through the same
policy-filtered `search_pages` and `read_page` MCP tools. This does not copy notes,
change the access-policy template, promote drafts, or enable automatic injection
of ordinary notes at session start.

## Opt in deliberately

Unlike the backwards-compatible `wiki/` legacy path, `notes/` has **no implicit
legacy permission**. A note must explicitly declare all five governance fields:

```yaml
---
memory_id: note:example
domain: work
sensitivity: internal
review_state: approved
target_profiles: [codex, opencode]
epistemic_status: human-stated
---
An example record written by its human owner.
```

`memory_id` is optional; without it a collection-qualified path-derived ID is
used. Give a note an explicit ID when it should retain identity after a rename.
The existing `review_status` and `target_agents` aliases also work, but conflicting
aliases fail closed. Missing governance, `ai-proposed`, pending/rejected content,
human-only targets, and profiles outside a note's domain/sensitivity allowance
are not returned. Writing `approved` does not bypass an AI-origin product note's
existing versioned human-review binding. Notes without product review metadata
remain an explicitly governed import, not proof of an independently authenticated
review ceremony. Do not relabel an AI draft as human-authored to make it readable.

The server's profile stays fixed at process start; callers cannot assert a more
privileged identity per request. Header authorization precedes full-body reads,
tokenization, ranking, and excerpts. Existing `wiki/` legacy compatibility remains.

## Search coverage and identity

Search examines complete authorized page snapshots, including material beyond the
old 64 KiB search prefix. The same bounds apply across `wiki/` and `notes/`:

- At most 2,000 Markdown files and 8,000 filesystem directory entries discovered.
- At most 2 MiB per eligible complete page and 8 MiB of authorized page snapshots.
- Governance headers retain their existing 16 KiB bound.

These are complete-snapshot safety limits, not instructions to silently take the
first N files or first N bytes. A discovery/eligible-page/total-byte limit or a
changed filesystem snapshot makes search fail with exit code 4 and **no partial
results**. This also tightens the former silent oversized-wiki-page omission.
Invalid or denied governance is still excluded, not treated as public coverage.
No denied page names, contents, IDs, or per-profile coverage counts are emitted.

Page search results include a root-relative `path` and a raw-file `revision`
(`sha256:<64 hex digits>`). Markdown output includes the same provenance.
`wiki/example.md` and `notes/example.md` are separate paths, not replacements for
one another. Duplicate stable IDs or case-insensitive path aliases across the
Markdown catalog fail closed; they never select an arbitrary winner. Symlinks do
not expand either collection. This is a filesystem snapshot check, not a lock
against an arbitrary same-user process deliberately modifying and restoring files.

## Read chunks without mixing versions

Calls containing no chunk parameters retain the old full-text behavior and
256 KiB file limit. To read a larger page, request a chunk explicitly:

```json
{"path":"notes/example.md","offset":0,"length":8000}
```

The MCP text result contains JSON with `path`, `memory_id`, `revision`, `offset`,
`nextOffset`, `totalChars`, `truncated`, and `text`. `offset` and `length` count
Unicode code points in the **fully redacted** page, including its frontmatter,
not UTF-8 bytes or JavaScript UTF-16 code units. Length is 1–32,000 (default 8,000).
Redaction runs before splitting, preventing a secret split across chunks from
escaping detection.

For the next chunk, send the previous `nextOffset` and `revision`:

```json
{"path":"notes/example.md","offset":8000,"length":8000,"revision":"sha256:<revision returned by first chunk>"}
```

The placeholder above must be replaced with the actual 64-digit hash. A revision
is mandatory when `offset > 0`; it can also pin the first chunk using a search
result's revision. If the file changes, authorization changes, the offset is out
of range, or the page is unavailable, the request returns the same generic
`page unavailable` error. Start again instead of concatenating different versions.
`nextOffset: null` marks the end. Each chunk reauthorizes the complete bounded
snapshot; chunking does not bypass the 2 MiB maximum.

Equivalent CLI options are `--read-page`, `--offset`, `--length`, and `--revision`.
Synthetic regression coverage lives in `tests/test-notes-recall.py`.
