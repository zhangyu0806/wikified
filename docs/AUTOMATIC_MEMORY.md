# Scoped automatic memory capture (opt-in)

This adds a local, single-writer loop; it is not distributed synchronization or an automatic knowledge compiler.

1. A native hook selects at most eight useful lines / 1,800 Unicode code points from the final assistant output. It redacts credentials and drops fenced code before writing a durable job. It does not copy the transcript, call a model, or make semantic claims about the user.
2. A separate worker writes an idempotent `ai-proposed`, `pending`, `work`, `internal` event targeted to the `coding` group. AI recall does not see pending events.
3. A separately paired human reviews the current event revision. Correction and withdrawal append new versions; old records are not overwritten. Human approval is not performed by the hook, worker or MCP client.
4. An opt-in writer mirrors permitted memory paths to a verified private GitHub repository after secret scanning. A push is only reported confirmed after reading the remote ref back.

## Capture configuration

Run `node /absolute/path/llm-wiki-capture-hook.mjs /absolute/path/capture.json` with a Codex hook payload on stdin. On Windows install the script on a local disk, not a WSL network share, and use `commandWindows`. Preserve existing SessionStart hooks. Review/trust the new hook definitions in Codex; never bypass hook trust.

```json
{
  "enabled": true,
  "profile": "codex",
  "queue": "/absolute/private/runtime/queue",
  "workspaces": [{"path": "/absolute/project", "project": "my-project"}],
  "transcriptRoots": ["/absolute/codex/sessions"]
}
```

Use `Stop` as the normal trigger, with `PreCompact`, `Interrupt` and `SessionEnd` as bounded fallback. SessionEnd/Interrupt have a maximum three-second execution window; enqueue natively and do not launch WSL, Git, a model or a network request there. The script returns success JSON without blocking/restarting the assistant. Fallback reads only a bounded transcript tail within configured roots and selects one final assistant output; no full transcript is persisted. Unknown transcript formats fail closed. Messages shorter than 80 characters are skipped. Identical selected output within one session/project deduplicates.

The OpenCode integration may explicitly call the same enqueue format, but installing a Codex hook **does not install an OpenCode event plugin**. Until such an integration is enabled, OpenCode can explicitly store proposals through `record_event` on the shared MCP server.

## Worker and shared recall

Run `python3 /pinned/engine/bin/llm-wiki-capture-worker --config /private/worker.json --watch` as the local user's supervised service. It retries every 15 seconds, handles at most 100 jobs per pass and keeps failed jobs. It writes only counters/timestamps to `queue/status.json`; receipts contain event IDs, not bodies. Keep this queue private to the operating-system user and outside the Git repository.

```json
{"root":"/private/vault","queue":"/private/runtime/queue","projects":["my-project"]}
```

Pin the same engine for Codex and OpenCode MCP; set `LLM_WIKI_ROOT` to the same local vault, `LLM_WIKI_DOMAIN=work`, `LLM_WIKI_TARGET_AGENTS=coding`, and `LLM_WIKI_AGENT_PROFILE=codex` or `opencode` respectively. Profile identity is fixed at process start. Never pass human-review credentials to either MCP process. Restart the actual clients after configuration and verify an explicitly human-approved synthetic fixture from both. Successful standalone profile tests are not proof that a currently running client has reloaded its configuration.

## Private Git mirror

```json
{
  "enabled": true,
  "role": "writer",
  "root": "/private/vault",
  "repository": "OWNER/PRIVATE-REPOSITORY",
  "branch": "main",
  "statusFile": "/private/runtime/mirror-status.json"
}
```

Run `python3 /pinned/engine/bin/llm-wiki-auto-commit --config /private/mirror.json`. GitHub CLI authentication must already be available to that OS user; no token belongs in this file. The origin must exactly match the configured GitHub repository. The mirror refuses public repositories, pre-existing staged edits, unmerged paths, remote-ahead/divergent history, scan failures and unconfirmed pushes. It never pulls, merges, rebases, resets, force-pushes or stages arbitrary code/configuration paths. The allowed roots are `memory/events`, `raw/notes`, `raw/sessions`, `notes`, `wiki`, `policy`. Publishing reviewed wiki changes still requires explicit human review beforehand; this transport does not grant promotion authority.

Use one active writer device. A role setting is **not a distributed lease**: stop old home/office writers before handing off. If the remote is ahead, inspect differences and resolve the handoff manually; do not make the timer merge. On failure, user edits and staged data are retained for inspection. A five-minute timer is a batching choice, not an instantaneous multi-device guarantee. `confirmedAt` records the last confirmed ref, not proof that later local writes are already uploaded.

Back up the vault and existing client/service configurations before activation. Disable the new hook/worker/timer and restore the previous configurations for rollback; keep canonical memory records and queued jobs. Do not erase user data to roll back executable code.

## Compatibility and limits

Missing legacy event authors are displayed as unknown by the human catalog without modifying JSONL. Explicit invalid authors still fail closed. Pending records are saved but not available to AI recall; legacy approved events remain subject to the existing governance policy and are not retroactively called human-verified.

This is a local single-user capability boundary, not multi-tenant authentication, cloud storage, encryption, a subscription backend or four-platform acceptance. Raw handoff excerpts are deliberately conservative; semantic distillation and user-authored note capture are separate reviewed workflows.
