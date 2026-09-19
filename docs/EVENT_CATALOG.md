# Read-only local human event catalog

`bin/llm-wiki-event-catalog` projects the engine's active, authorized events for a
local human review view. It is not an MCP tool, an authentication mechanism, or a
review writer. A Web bridge must fix its data root, `human` profile, and
`local-human-review` purpose from trusted operator configuration, never from a
browser request.

The projection reuses the existing complete event snapshot and lifecycle reducer.
Supersession and withdrawal are resolved before profile filtering, so a hidden
terminal event cannot resurrect an older visible record. Only authorized active
rows contribute titles, search matches, counts and record revisions.

Summary and details are redacted before search, excerpts, titles, or detail output.
All projected metadata strings are then checked recursively, including object and
event IDs, actor IDs/types, targets, timestamps and display types. If the existing
secret-pattern redactor would change any metadata string, the entire response
fails with the generic unavailable diagnostic and no partial JSON. Stable IDs are
never rewritten into redaction markers: doing so would corrupt identity or cause
collisions. This is pattern-based defense in depth, not a claim to detect every
possible secret. Repair problematic metadata explicitly in the source workflow;
the reader never modifies records.

Details require both the event ID and its exact revision. New revisions, hidden
records and superseded records are unavailable under an old locator. Queries use
case-folded substring matching over the fully redacted summary/details; returned
offsets refer to Unicode code points in the original redacted field, even when
case folding expands a character.

At most 1,000 matching rows are returned. Search still considers all authorized
active rows before that output limit, and coverage explicitly reports incomplete
results when more match. The entire serialized response is capped at 2 MiB;
exceeding this bound fails before output, rather than emitting truncated JSON.
Policy bytes, root identity and policy filesystem observations are checked before
and after projection. These are bounded snapshot checks, not a transaction lock
against arbitrary same-user filesystem writers.

Synthetic regressions: `python3 tests/test-event-catalog.py`.
