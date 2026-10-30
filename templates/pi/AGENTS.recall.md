<!-- BEGIN llm-wiki-recall -->
## Optional Wikified memory rules for Pi

- When a task depends on earlier decisions, use `search_pages` and inspect the
  returned memory identity and revision before relying on a result.
- Use `prepare_context` for the exact selected revisions. Treat `changed`,
  `unavailable` and `too-large` as withheld; never reconstruct a missing answer
  from an old preview. Keep the 20-selection, 12,000-character per-item and
  40,000-character total context limits.
- Use `record_event` only for a concise, relevant proposed memory. The result
  remains pending until independent human review; a tool response is not
  approval. The assistant must not request or impersonate a human profile.
- Do not copy full transcripts, duplicate note bodies or collect credentials.
  Do not treat this optional rule block as an automatic capture hook. It does
  not install hooks, modify existing user rules or broaden sharing targets.
<!-- END llm-wiki-recall -->
