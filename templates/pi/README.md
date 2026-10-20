# Pi stdio MCP templates

These are examples, not installed client configuration. Replace every absolute
path and distribution placeholder. The configurator rejects dollar signs and
variable placeholders: client-side interpolation could change the target after
configuration checks. Pi reads user `~/.pi/agent/mcp.json`; a
trusted project may instead use `.pi/mcp.json`. Merge the `llm-wiki` entry into
the existing `mcpServers` object. Do not replace unrelated servers or settings.

The configurator performs this merge with backups and conflict refusal:

```sh
python3 bin/llm-wiki-harness configure --harness pi --transport local \
  --engine /absolute/path/to/wikified/bin/llm-wiki-mcp \
  --node /absolute/path/to/node --python /absolute/path/to/python3 \
  --root /absolute/path/to/vault
```

`--python` sets `LLM_WIKI_PYTHON` for all Python MCP helpers and custom-profile
registration. It is useful when a GUI client's PATH lacks Python. The explicit
interpreter runs in isolated mode; an invalid selection fails without falling
back to another Python. Omit the option (and template environment entry) to
retain the helpers' existing shebang/PATH behavior.

Use `--config` for an explicit project file. Windows clients use `--transport
wsl --wsl-distribution YOUR_DISTRIBUTION`; `--engine`, `--node`, `--python` and `--root`
then name absolute Linux paths in that distribution. Run the configurator with
native Windows Python and a native Windows config path, or from WSL with the
mounted `/mnt/...` config path. No shell command string is generated.

Repeat the same launch options with `status --harness pi --json` or `doctor
--harness pi --json`. `doctor --probe` initializes a separate stdio process and
lists tools only; `helperRuntime` remains `not-tested`. It does not execute
Python helpers, test vault authorization, call a model, write an
event, or prove the actual Pi client has loaded the configuration. Reload the
client and validate it separately. This template installs no automatic capture
or lifecycle extension. New events target `pi`; existing pair-only targets are
not widened.
