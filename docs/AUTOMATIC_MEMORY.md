# Scoped automatic memory capture (opt-in)

This adds a local, single-writer loop; it is not distributed synchronization or an automatic knowledge compiler.

1. A native hook selects at most eight useful lines / 1,800 Unicode code points from the final assistant output. It redacts credentials and drops fenced code before writing a durable job. It does not copy the transcript, call a model, or make semantic claims about the user.
2. A separate worker writes an idempotent `ai-proposed`, `pending`, `internal` event targeted to the `coding` group. Its domain defaults to `work`; an explicit trusted project mapping can select `personal`. AI recall does not see pending events.
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

Workspace bindings cover their real directory and descendants, not sibling names with the same prefix. The most specific matching directory wins regardless of configuration order; equal paths retain their first binding. Missing or disconnected paths are skipped. A matched binding with an invalid project fails closed rather than being rerouted.

General capture is disabled by default. To explicitly include sessions in all other working directories, add `"fallbackProject": "codex-general"` to this configuration and independently add `codex-general` to the worker's `projects` allowlist. Explicit workspace bindings still take precedence. Removing `fallbackProject` restores workspace-only capture. The hook does not infer projects from directory names or create an unrestricted filesystem binding.

Use `Stop` as the normal trigger, with `PreCompact`, `Interrupt` and `SessionEnd` as bounded fallback. SessionEnd/Interrupt have a maximum three-second execution window; enqueue natively and do not launch WSL, Git, a model or a network request there. The script returns success JSON without blocking/restarting the assistant. If `Stop` has no directly supplied assistant text, it may use the same bounded fallback when a current `turn_id` is present. Fallback checks the transcript root and session identity, reads at most a 64 KiB header plus a 2 MiB tail, and selects a final output from the expected turn. It supports `final` and `final_answer`, text content blocks, completed AgentMessage items and task completion messages. Commentary, mismatched sessions/threads/turns and unknown formats are skipped. A short direct reply does not cause an older reply to be reused. No full transcript is persisted. Concise results can be selected without an 80-character minimum; useful lines must still contain at least eight characters, and empty output, short acknowledgments and fenced code are skipped. Identical selected output within one session/project deduplicates.

Each hook invocation also updates private `queue/hook-status.json` and `queue/hook-status-<profile>.json` with a fixed outcome, event, timestamp and optional opaque session/job hashes and selection-source enum. The shared file remains compatible with existing consumers; the per-profile files retain each host's latest result when profiles share a queue. Only the fixed supported profile names can form a filename. The `coverage` object contains only `workspaceCount` (configured binding count, including unavailable paths) and `fallbackEnabled` (whether a valid fallback project is configured). It never stores a path, project name, title, message or raw exception. Coverage describes the hook configuration, not worker authorization or successful storage. Compare this hook timestamp with `queue/status.json` from the worker: a fresh worker timestamp proves polling, not that a client has invoked the hook. A manual script invocation or synthetic test does not prove that the live client has loaded and trusted the hook. New or changed definitions still require the host's normal hook review.

## Host adapters and identity

Each host has a separate operator-owned configuration with a fixed `profile`: `codex`, `opencode`, `claude`, `cursor`, `grok`, `pi` or `workbuddy`. The hook and worker reject unknown profiles. Payload fields cannot select the stored profile or domain. A supported adapter is not evidence that its hook or plugin has been installed or loaded.

| Host | Capture trigger and fields | Scope |
| --- | --- | --- |
| Codex | `Stop`, bounded `PreCompact`/`SessionEnd`/`Interrupt` fallback | Validated Codex rollout formats only |
| Claude Code | Native command `Stop`; `session_id`, `cwd`, `last_assistant_message`, `stop_hook_active` | Direct completed assistant text |
| WorkBuddy | Native command `Stop`, same direct fields as Claude | Local command hooks, not SDK callback conversion |
| Cursor | `afterAgentResponse`; `conversation_id`, `generation_id`, `workspace_roots`, `text` | Completed assistant message; thoughts and tool hooks are ignored |
| Grok | Native `hookEventName: "stop"`, `reason: "end_turn"`; `sessionId`, `promptId`, `cwd`, `lastAssistantMessage`, `stopHookActive` | Completed main-agent turn; error, cancellation and session-end Stop are ignored |
| OpenCode / Pi | Host plugin supplies the canonical `Stop` payload after a validated completion | Separate plugin installation is required |

Claude's fields follow the [official hooks reference](https://code.claude.com/docs/en/hooks#stop); Cursor's fields follow its [official hooks reference](https://cursor.com/docs/agent/hooks#afteragentresponse) and installed runtime contract. WorkBuddy's local command dispatcher and Grok 1.0.49's bundled hooks guide provide their native contracts. Grok's SDK converts fields to snake case; this adapter consumes the native command payload and deliberately does not guess an SDK shape.

For Claude and WorkBuddy, add a command to the existing settings `hooks.Stop` array, preserving other hooks and settings. For Grok, place the same nested definition in a separate `~/.grok/hooks/mind2one-capture.json`:

```json
{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"node /absolute/llm-wiki-capture-hook.mjs /private/capture-<profile>.json","timeout":3}]}]}}
```

Cursor uses its own user `~/.cursor/hooks.json` shape:

```json
{"version":1,"hooks":{"afterAgentResponse":[{"command":"node /absolute/llm-wiki-capture-hook.mjs /private/capture-cursor.json"}]}}
```

Cursor multiroot conversations use the common selected project when all roots agree; otherwise they require the explicit fallback project. A no-folder conversation also requires that fallback. Native Cursor and Grok events are normalized to `Stop` for status reporting. Their distinctive payload namespaces are rejected by other profile configurations without writing that other profile's status, because compatibility discovery can run a Claude or Cursor hook from Grok. The adapter never switches identity based on an incoming payload.

Only Codex can use the transcript reader. Other hosts must supply direct completed text, so an absent or short message cannot reuse an old transcript. Reasoning blocks, tool output and subagent lifecycle events are not capture triggers. All adapters return passive hook output and do not ask the host to continue, block or approve work.

## Worker and shared recall

Run `python3 /pinned/engine/bin/llm-wiki-capture-worker --config /private/worker.json --watch` as the local user's supervised service. It retries every 15 seconds, handles at most 100 jobs per pass and keeps failed jobs. It writes only counters/timestamps to `queue/status.json`; receipts contain event IDs, not bodies. Keep this queue private to the operating-system user and outside the Git repository.

```json
{"root":"/private/vault","queue":"/private/runtime/queue","projects":["my-project"]}
```

For general conversations that may include personal material, explicitly map the fallback project in the trusted worker configuration:

```json
{
  "root": "/private/vault",
  "queue": "/private/runtime/queue",
  "projects": ["my-project", "codex-general"],
  "projectDomains": {"codex-general": "personal"}
}
```

`projectDomains` accepts only allowlisted project names and the event schema's `work` or `personal` domains; invalid mappings prevent consumption. Unmapped projects retain `work` for compatibility. Queue payloads cannot supply or override the domain. This is a configured routing decision, not content classification; all fallback excerpts use the chosen domain.

The worker's local event writer can submit these personal proposals without expanding a client profile. They remain `pending`, `internal` and targeted to `coding`; no access policy is changed. The default `coding` and `codex` profiles read only approved `work` records, so personal records remain unavailable to them even after approval. The local human catalog can review them. Any later change to recall domains or target permissions requires a separate explicit configuration decision. Changing a project's domain does not rewrite already saved events; conflicting retries retain the existing event and leave the job for inspection.

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
