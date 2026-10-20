# WorkBuddy stdio MCP templates

These are examples, not installed client configuration. Replace every absolute
path and distribution placeholder. The configurator rejects dollar signs and
variable placeholders: client-side interpolation could change the target after
configuration checks. WorkBuddy reads user `~/.workbuddy/mcp.json`
or project `.workbuddy/mcp.json`. Merge the `llm-wiki` entry into the existing
`mcpServers` object. Do not replace unrelated servers or settings.

The configurator performs this merge with backups and conflict refusal:

```sh
python3 bin/llm-wiki-harness configure --harness workbuddy --transport local \
  --engine /absolute/path/to/wikified/bin/llm-wiki-mcp \
  --node /absolute/path/to/node --root /absolute/path/to/vault
```

Use `--config` for an explicit project file. Windows clients use `--transport
wsl --wsl-distribution YOUR_DISTRIBUTION`; `--engine`, `--node` and `--root`
then name absolute Linux paths in that distribution. The Windows client starts
`wsl.exe` with literal arguments. The engine still runs in Linux; this does not
make the Unix engine toolchain native Windows software. Native Windows Python
can merge the Windows config file, or WSL can merge its mounted `/mnt/...` path.

Repeat the same launch options with `status --harness workbuddy --json` or
`doctor --harness workbuddy --json`. `doctor --probe` initializes a separate
stdio process and lists tools only. It does not test vault authorization, call
a model, write an event, or prove WorkBuddy has reloaded the file. Client reload
and acceptance remain separate. No automatic capture hook is installed. New
events target `workbuddy`; existing pair-only targets are not widened.
