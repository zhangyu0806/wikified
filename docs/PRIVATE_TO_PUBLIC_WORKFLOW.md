# Private data → public mechanism workflow

Wikified deliberately separates two repositories:

- the **public mechanism repository** contains CLIs, MCP, skills, templates,
  installers, tests, and public documentation;
- a **private data repository** contains personal `wiki/`, `raw/`, events,
  local decisions, and machine-specific data configuration.

The private repository may expose bugs, but it must not become a second copy of
the mechanism. A generally useful fix is complete only after it has been
implemented and tested in the public repository.

## Classification

Publish to the mechanism repository when the problem concerns:

- CLI, MCP, hook, skill, installer, schema, retrieval, redaction, locking, or
  sync behavior;
- compatibility with Claude Code, OpenCode, Grok, Codex, Cursor, or another
  reusable harness;
- a data-format invariant or migration that applies to more than one private
  repository;
- a security boundary that can be reproduced without real private content.

Keep it private when the material is only:

- personal memory content, transcripts, notes, preferences, or project facts;
- credentials, authentication state, private endpoints, hostnames, account
  identifiers, or recovery material;
- machine runtime state, caches, logs, browser state, databases, or local
  layout that has no reusable mechanism change.

When a private-only fact reveals a general bug, split the two: keep the fact
private and publish a synthetic reproduction plus the mechanism fix.

## Required feedback loop

1. Reproduce the problem against a disposable repository or isolated HOME.
2. Replace private paths, content, identifiers, hosts, and secrets with
   synthetic fixtures.
3. Add a failing regression test in the public mechanism repository.
4. Fix the public implementation without weakening fail-closed boundaries.
5. Run syntax checks, secret scanning, focused tests, and the full relevant
   suite.
6. Review the complete staged tree—not only the diff—for private material.
7. Publish through a branch and pull request in the public repository.
8. After the public fix is accepted, update the private installation to that
   public revision; do not hand-copy a divergent implementation back into the
   private data repository.

## Public release gate

Before a branch is pushed, verify that the staged tree contains none of:

- `.env`, API keys, tokens, passwords, cookies, private keys, or auth state;
- real private memory pages, raw notes, events, transcripts, or databases;
- real user HOME paths, browser/download state, temporary worktrees, or local
  validation logs;
- links to private conversations or attachments used during investigation;
- a generic `agent` integration whose product identity was not proven.

Synthetic secret-like fixtures are allowed only when clearly constructed by
tests and required to prove redaction or scanning behavior.

## Ownership rule

The public mechanism repository is the only installation source for reusable
code. Stable legacy command names and environment variables may remain as
compatibility APIs, but their implementations must resolve to the public
mechanism checkout. The private repository is data, not a task queue and not a
fork of the product.
