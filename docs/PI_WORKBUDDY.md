# Pi and WorkBuddy on macOS and Windows

Pi and WorkBuddy can launch Wikified's stdio MCP server with independent identities. The configuration command supports macOS local processes and Windows native clients connected to a selected WSL distribution. Windows requires WSL for this engine path; the native Windows Python configuration utility does not turn the Unix engine into a native Windows service.

## Before connecting

Use a fixed engine checkout with its sibling `bin/` helpers and a separately selected vault. Install Node and Python in the engine environment. Pi 1.2 already supports native MCP; a third-party MCP bridge is unnecessary. WorkBuddy supports a user or project MCP configuration.

Existing vault policies are not rewritten by configuration. The vault owner must explicitly add the desired `pi` or `workbuddy` policy from `templates/access-policy.json`. These templates allow approved work/internal material targeted to that assistant or the existing `coding` group. They do not authorize personal/raw material, human review, or records targeted only to `codex,opencode`. New MCP proposals default to the originating assistant as their sole target. Sharing a conclusion is a separate explicit owner decision.

The managed server name is `llm-wiki`. A conflicting entry is preserved and reported; unrelated servers and settings remain untouched. Both client files must be strict JSON. Comments, trailing commas and duplicate keys are rejected without changing the original. Launch paths must be literal absolute paths. Dollar signs (including environment-variable placeholders) are rejected because the clients' interpolation rules differ from the diagnostic process. Configure one new harness per command. Legacy `configure --harness all` and unqualified `status` retain their original five-client behavior.

## macOS local configuration

Replace the example paths with absolute paths for the selected installation and vault:

```sh
python3 /absolute/wikified/bin/llm-wiki-harness configure \
  --harness pi --transport local \
  --node /opt/homebrew/bin/node \
  --python /opt/homebrew/bin/python3 \
  --engine /absolute/wikified/bin/llm-wiki-mcp \
  --root "/absolute/Shared Memory"
```

Repeat with `--harness workbuddy`. The default destinations are `~/.pi/agent/mcp.json` and `~/.workbuddy/mcp.json`. Supply `--config /absolute/project/.pi/mcp.json` or `/absolute/project/.workbuddy/mcp.json` for an explicit project destination. Pi project configuration requires project trust.

`--node` can be omitted if the MCP executable's shebang can find Node in the client environment. `--python` selects the interpreter for memory helpers and custom-profile registration. Absolute executable paths are more reliable for GUI launches, whose PATH may differ from the terminal. An explicit Python selection never silently falls back to another interpreter; omitting it retains the existing shebang/PATH behavior. Replace the examples with paths that exist on the selected installation.

A Node-only connection can initialize and list tools while Python helper calls fail. A successful transport handshake alone does not test helper availability or memory authorization. Adding `--python` to an existing different managed entry is reported as a conflict; inspect the existing file and deliberately reconcile the entry rather than overwriting unrelated configuration.

## Windows clients with WSL

Run the configuration utility with native Windows Python. The utility checkout can be on Windows while the runtime checkout and vault are inside WSL. In PowerShell:

```powershell
py -3 "C:\Tools\wikified\bin\llm-wiki-harness" configure `
  --harness pi --transport wsl `
  --wsl-distribution Ubuntu `
  --node /usr/bin/node `
  --python /usr/bin/python3 `
  --engine /home/user/wikified/bin/llm-wiki-mcp `
  --root "/home/user/Shared Memory"
```

Repeat with `--harness workbuddy`. The default file is under the Windows user's home. Select the actual distribution and Linux Node/Python paths; `--python` is the interpreter inside WSL, not the Windows Python running the configuration utility. No default distribution or vault is inferred. `--wsl-command` optionally selects an absolute Windows `wsl.exe` path. Paths with spaces or Unicode are represented as separate arguments, not shell command strings.

When running the configuration utility inside WSL, explicitly pass the mounted Windows config file, for example `--config /mnt/c/Users/YourName/.pi/agent/mcp.json`. A Windows `C:\...` string is rejected on POSIX rather than creating a misleading relative file. Keep both engine checkouts at the intended fixed revision.

## Diagnose and verify

Use the same launch arguments and config destination for `status` or `doctor`:

```sh
python3 /absolute/wikified/bin/llm-wiki-harness doctor \
  --harness pi --transport local \
  --node /opt/homebrew/bin/node \
  --python /opt/homebrew/bin/python3 \
  --engine /absolute/wikified/bin/llm-wiki-mcp \
  --root "/absolute/Shared Memory" --probe --json
```

Without `--probe`, diagnostics only inspect configuration. The explicit probe sends bounded `initialize` and `tools/list` requests; it does not search, read, record memory or call a model. Its report separately states configuration, transport, untested helper execution, vault authorization, actual-client acceptance and automatic capture. A correct configuration is not proof that the client reloaded it or that the selected vault has granted access.

In Pi, run `pi mcp list` and inspect `/mcp` in a new or reloaded session. In WorkBuddy, inspect the custom connector and enable it for the intended task. Then use an isolated synthetic vault to verify search, exact-version preparation, pending proposals, independent human approval, new-session reuse and withdrawal. Record the operating system and client version with the results.

## Usage and limits

The optional `templates/pi/AGENTS.recall.md` and `templates/workbuddy/AGENTS.recall.md` explain when to search and propose a memory. Merge their rules deliberately into the appropriate client/project instructions. Configuration does not install automatic capture hooks or replace user rules.

The existing maximum of 20 selected items, 12000 Unicode code points per item and 40000 total stays unchanged. Pi truncates model-facing MCP text above 20 KB, so request small search results and exact selections rather than relying on a maximum-size response. Native client limits can be lower than the engine's hard ceiling.

WorkBuddy's built-in remembered preferences are a separate store. Copying governed project facts into it does not establish revision or withdrawal synchronization. The same distinction applies when a model is accessed through a provider gateway: memory tools belong to the executing harness, not to the model account.

## Official configuration references

- [Pi 1.2 MCP](https://github.com/earendil-works/pi/blob/v1.2.0/packages/coding-agent/docs/mcp.md)
- [WorkBuddy MCP paths and configuration](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/MCP-Guide)
- [WorkBuddy connectors](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Connector)
- [WorkBuddy built-in memory](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Memory)
