#!/usr/bin/env python3
"""P2 contract + isolated CLI regressions. Never uses an installed CLI or real vault."""
from __future__ import annotations

import copy
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("versioned_review_under_test", str(REPO / "bin/llm-wiki-enrich"))
spec = importlib.util.spec_from_loader(loader.name, loader)
engine = importlib.util.module_from_spec(spec)
sys.modules[loader.name] = engine
loader.exec_module(engine)
MARKER = "versionedreviewfixturemarker"
BODY = f"# {MARKER}\n\n{MARKER} A synthetic claim for a synthetic review.\n"
AT = "2026-09-12T08:00:00.000Z"


def source() -> dict:
    return {
        "memory_id": "review:synthetic", "domain": "work", "sensitivity": "internal",
        "epistemic_status": "human-confirmed", "review_state": "approved",
        "target_profiles": ["codex", "opencode"], "project": "synthetic-project",
        "mind2one": {
            "version": 2, "id": "review:synthetic", "kind": "memory", "origin": "ai",
            "agent": "synthetic-agent", "operation-id": "synthetic-operation",
            "review-state": "accepted", "reviewed-at": AT, "reviewed-by": "human",
            "sources": [{"kind": "document", "id": "source:synthetic", "label": "Synthetic evidence",
                         "revision": "source-revision-1", "relation": "current-derived", "locator": "section-1"}],
            "action": {"level": "key-result", "status": "active", "parent-id": "objective:one",
                       "metric": {"start": 0, "current": 0.1, "target": 10, "unit": "units", "basis": "fixture"}},
            "scheduled-for": "2026-09-12T09:00:00.000Z", "due-at": "2026-09-13T09:00:00.000Z",
            "review-history": [],
        },
    }


def approve(frontmatter: dict, body: str = BODY, *, decision: str = "accept") -> dict:
    result = copy.deepcopy(frontmatter)
    product = result["mind2one"]
    product["review-state"] = "corrected" if decision == "correct" else "accepted"
    product["reviewed-at"], product["reviewed-by"] = AT, "human"
    result["review_state"], result["epistemic_status"] = "approved", "human-confirmed"
    product["review"] = {
        "version": 1, "algorithm": "m2o-semantic-v1", "record-id": result["memory_id"],
        "content-revision": engine.compute_content_revision(result, body),
        "policy-revision": engine.compute_policy_revision(result), "decision": decision,
        "actor": {"type": "human", "id": "synthetic-owner"}, "at": AT,
        "reason": "Synthetic review", "request-id": "synthetic-request", "previous-revision": None,
    }
    return result


def change(root: dict, path: tuple, value) -> dict:
    result = copy.deepcopy(root)
    current = result
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = value
    return result


def render(frontmatter: dict, body: str = BODY, *, crlf=False, reverse=False) -> bytes:
    entries = list(frontmatter.items())
    if reverse:
        entries.reverse()
    header = "---\n" + "\n".join(json.dumps(k) + ": " + json.dumps(v, ensure_ascii=False, separators=(",", ":")) for k, v in entries) + "\n---\n"
    if crlf:
        return ("\ufeff" + header.replace("\n", "\r\n") + body.replace("\n", "\r\n")).encode("utf-8")
    return (header + body).encode("utf-8")


checked = 0
base = approve(source())
assert engine.assess_memory_review(base, BODY)["state"] == "verified"
assert not engine.assess_memory_review(base)["trusted"], "No body must never mean verified"
assert engine.compute_content_revision(base, BODY.replace("\n", "\r\n")) == engine.compute_content_revision(base, BODY)
assert engine.compute_content_revision(base, BODY.replace("\n", "\r")) == engine.compute_content_revision(base, BODY)
assert engine.compute_content_revision(base, BODY.rstrip("\n")) != engine.compute_content_revision(base, BODY)
assert engine._semantic_number(-0.0) == engine._semantic_number(0) == "0000000000000000"
checked += 6

semantic_changes = [
    (("mind2one", "kind"), "note"), (("mind2one", "origin"), "mixed"),
    (("mind2one", "agent"), "another-agent"), (("mind2one", "operation-id"), "another-operation"),
    (("mind2one", "action", "status"), "done"),
    (("mind2one", "action", "metric", "start"), -1), (("mind2one", "action", "metric", "current"), 2),
    (("mind2one", "action", "metric", "target"), 20), (("mind2one", "action", "metric", "unit"), "different"),
    (("mind2one", "action", "metric", "basis"), "different-basis"),
    (("mind2one", "scheduled-for"), "2026-09-12T10:00:00.000Z"),
    (("mind2one", "due-at"), "2026-09-14T09:00:00.000Z"),
    (("mind2one", "sources", 0, "kind"), "external"), (("mind2one", "sources", 0, "id"), "source:two"),
    (("mind2one", "sources", 0, "label"), "Different source"), (("mind2one", "sources", 0, "revision"), "rev-2"),
    (("mind2one", "sources", 0, "relation"), "historical-citation"),
    (("mind2one", "sources", 0, "locator"), "section-2"),
]
for path, value in semantic_changes:
    altered = change(base, path, value)
    assert engine.compute_content_revision(altered, BODY) != base["mind2one"]["review"]["content-revision"], path
    assert engine.assess_memory_review(altered, BODY)["state"] == "mismatch", path
    checked += 1

classification_changes = [
    (("tags",), ["another-tag"]), (("mind2one", "para"), "archive"),
    (("mind2one", "topics"), ["another-topic"]), (("mind2one", "project-ids"), ["project:two"]),
    (("mind2one", "area-ids"), ["area:two"]), (("mind2one", "action", "parent-id"), "objective:two"),
    (("mind2one", "captured-at"), "2020-01-01T00:00:00.000Z"),
    (("mind2one", "review-after"), "2030-01-01T00:00:00.000Z"),
]
for path, value in classification_changes:
    assert engine.assess_memory_review(change(base, path, value), BODY)["state"] == "verified", path
    checked += 1
assert engine.compute_policy_revision(change(base, ("target_profiles",), ["opencode", "codex"])) == engine.compute_policy_revision(base)

with tempfile.TemporaryDirectory(prefix="wikified-versioned-review-") as directory:
    root = Path(directory)
    (root / "wiki/context").mkdir(parents=True)
    (root / "memory/events").mkdir(parents=True)
    (root / "policy").mkdir()
    shutil.copyfile(REPO / "templates/access-policy.json", root / "policy/access.json")
    page = root / "wiki/context/CRITICAL_FACTS.md"

    def surfaces(name, frontmatter, body=BODY, *, allowed=False, marker=MARKER, raw=None, mcp=False, **render_options):
        global checked
        original = raw if raw is not None else render(frontmatter, body, **render_options)
        page.write_bytes(original)
        for profile in ("codex", "opencode"):
            for args in (["--query", marker, "--json"], ["--read-page", "wiki/context/CRITICAL_FACTS.md"], ["--session-start"]):
                result = subprocess.run([sys.executable, str(REPO / "bin/llm-wiki-enrich"), "--agent-profile", profile, *args],
                    env={**os.environ, "LLM_WIKI_ROOT": str(root)}, text=True, capture_output=True)
                assert (marker in result.stdout) == allowed, (name, profile, args, result.stdout, result.stderr)
                assert "Traceback" not in result.stderr and marker not in result.stderr, (name, result.stderr)
                if "--read-page" in args:
                    assert (result.returncode == 0) == allowed, (name, result.stderr)
                else:
                    assert result.returncode == 0, (name, result.stderr)
            if mcp:
                calls = [
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                        "name": "read_page", "arguments": {"path": "wiki/context/CRITICAL_FACTS.md"}}},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                        "name": "search_pages", "arguments": {"query": marker}}},
                ]
                result = subprocess.run(["node", str(REPO / "bin/llm-wiki-mcp")],
                    input="".join(json.dumps(call) + "\n" for call in calls),
                    env={**os.environ, "LLM_WIKI_ROOT": str(root), "LLM_WIKI_AGENT_PROFILE": profile,
                         "LLM_WIKI_BIN_TARGET": str(REPO / "bin")}, text=True, capture_output=True, timeout=20)
                assert result.returncode == 0 and "Traceback" not in result.stderr, (name, result.stderr)
                # MCP NDJSON is delimited by LF, not Unicode U+2028/U+2029
                # occurring legitimately inside JSON strings in golden vectors.
                replies = {value["id"]: value for value in map(json.loads, result.stdout.strip().split("\n"))}
                for identity in (2, 3):
                    assert identity in replies, (name, result.stdout)
                    assert (marker in json.dumps(replies[identity], ensure_ascii=False)) == allowed, (name, replies)
                assert ("result" in replies[2]) == allowed, (name, replies)
                if not allowed:
                    assert replies[2]["error"]["message"] == "llm-wiki-enrich: page unavailable", (name, replies)
        assert page.read_bytes() == original, "The reader must not rewrite unbound or stale source"
        checked += 1

    surfaces("verified", base, allowed=True)
    surfaces("line endings and field ordering", base, allowed=True, crlf=True, reverse=True)
    surfaces("JSON numeric versions are equal", change(change(base, ("mind2one", "version"), 2.0),
             ("mind2one", "review", "version"), 1.0), allowed=True)
    surfaces("JSON exponent numeric versions are equal", base, allowed=True,
             raw=render(base).replace(b'"version":2,', b'"version":2e0,').replace(b'"version":1,', b'"version":1e0,'))
    surfaces("JSON boolean product version denied", change(base, ("mind2one", "version"), True))
    surfaces("JSON boolean binding version denied", change(base, ("mind2one", "review", "version"), True))
    surfaces("YAML block decimal version remains unsupported", base,
             raw=render(base).split(b'"mind2one":', 1)[0] +
             b'"mind2one":\n  version: 1.0\n  id: review:synthetic\n  origin: human\n  review-state: not-required\n---\n' + BODY.encode())
    surfaces("indented single-line JSON", base, allowed=True,
             raw=render(base).replace(b'"mind2one": {', b'"mind2one":\n  {'))
    surfaces("multiline JSON remains unsupported", base,
             raw=render(base).replace(b'"mind2one": {"version":2,', b'"mind2one": {\n  "version":2,'))
    surfaces("v2 partial block remains unsupported", base,
             raw=render(base).split(b'"mind2one":', 1)[0] +
             b'"mind2one":\n  version: 2\n  id: review:synthetic\n  origin: ai\n  review-state: accepted\n---\n' + BODY.encode())
    surfaces("corrected", approve(source(), decision="correct"), allowed=True)
    surfaces("body changed", base, BODY + "New unreviewed content.\n")
    surfaces("last newline changed", base, BODY.rstrip("\n"))
    for path, value in semantic_changes:
        surfaces("semantic edit " + str(path), change(base, path, value))
    for path, value in classification_changes:
        surfaces("classification " + str(path), change(base, path, value), allowed=True)
    surfaces("source order changed", approve(change(source(), ("mind2one", "sources"), [
        *source()["mind2one"]["sources"], {"kind": "document", "id": "source:two"}])), allowed=True)
    ordered_sources = approve(change(source(), ("mind2one", "sources"), [
        *source()["mind2one"]["sources"], {"kind": "document", "id": "source:two"}]))
    surfaces("unreviewed source reordering", change(ordered_sources, ("mind2one", "sources"),
             list(reversed(ordered_sources["mind2one"]["sources"]))))
    surfaces("policy changed", change(base, ("project",), "another-project"))
    surfaces("target order unchanged", change(base, ("target_profiles",), ["opencode", "codex"]), allowed=True)
    surfaces("private target", change(base, ("target_profiles",), ["human"]))
    surfaces("v1 AI accepted unbound", change(base, ("mind2one", "version"), 1))
    surfaces("v1 mixed unbound", change(change(base, ("mind2one", "version"), 1), ("mind2one", "origin"), "mixed"))
    human = change(change(base, ("mind2one", "origin"), "human"), ("mind2one", "review-state"), "not-required")
    human["mind2one"].pop("review")
    surfaces("ordinary human without AI review", human, allowed=True)
    human["mind2one"]["version"] = 1
    surfaces("ordinary legacy human", human, allowed=True)
    for detail in ("rejected", "withdrawn", "pending"):
        changed = change(base, ("mind2one", "review-state"), detail)
        changed["review_state"] = "pending" if detail == "pending" else "rejected"
        changed["epistemic_status"] = "ai-proposed" if detail == "pending" else "disputed"
        surfaces(detail + " excluded", changed)
    for field, value in (
        ("algorithm", "future-algorithm"), ("actor", {"type": "ai", "id": "synthetic"}),
        ("content-revision", "sha256:" + "0" * 64), ("policy-revision", "sha256:" + "0" * 64),
        ("record-id", "another-record"), ("at", "not-a-time"), ("request-id", ""),
        ("reason", "x" * 1001), ("version", 2),
    ):
        surfaces("invalid binding " + field, change(base, ("mind2one", "review", field), value))
    surfaces("reviewed-by mismatch", change(base, ("mind2one", "reviewed-by"), "ai"))
    surfaces("reviewed-at mismatch", change(base, ("mind2one", "reviewed-at"), "2025-01-01T00:00:00.000Z"))
    for at in ("2026-02-30T00:00:00Z", "2026-09-12", "2026-09-12T08:00:00+00:00",
               "2026-09-12T24:00:00Z", "2026-09-12T08:00:00.00Z"):
        surfaces("invalid strict time " + at, change(base, ("mind2one", "review", "at"), at))
    unicode_binding = change(change(change(base, ("mind2one", "review", "actor", "id"), "😀" * 200),
        ("mind2one", "review", "request-id"), "😀" * 200), ("mind2one", "review", "reason"), "😀" * 1000)
    surfaces("unicode codepoint bounds", unicode_binding, allowed=True)
    surfaces("ECMAScript nonblank Unicode NEL", change(base, ("mind2one", "review", "actor", "id"), "\u0085"), allowed=True)
    surfaces("ECMAScript blank BOM", change(base, ("mind2one", "review", "actor", "id"), "\ufeff"))
    for path in (("actor", "id"), ("request-id",), ("reason",)):
        for value in ("😀" * (1001 if path == ("reason",) else 201), "\ud800"):
            # Surrogates cannot be encoded as UTF-8 source; JSON escapes still
            # exercise the parsed binding validator without encoding ambiguity.
            bad = change(base, ("mind2one", "review", *path), value)
            assert not engine.assess_memory_review(bad, BODY)["trusted"]
            checked += 1
    history = copy.deepcopy(base["mind2one"]["review"])
    chained = change(base, ("mind2one", "review-history"), [history])
    chained["mind2one"]["review"]["previous-revision"] = history["content-revision"]
    surfaces("valid review history", chained, allowed=True)
    surfaces("broken previous revision", change(chained, ("mind2one", "review", "previous-revision"), None))
    surfaces("wrong history record", change(chained, ("mind2one", "review-history", 0, "record-id"), "wrong-record"))
    surfaces("invalid history container", change(base, ("mind2one", "review-history"), {}))
    surfaces("oversized history", change(base, ("mind2one", "review-history"), [history] * 33))
    long_body = BODY + "x" * (70 * 1024) + "\nlast-line\n"
    long_record = approve(source(), long_body)
    surfaces("complete long-body approval", long_record, long_body, allowed=True)
    surfaces("edit beyond search prefix", long_record, long_body.replace("last-line", "unapproved-tail"))
    surfaces("oversized complete body", base, BODY + "x" * engine.MAX_PAGE_SNAPSHOT_BYTES)

    # Unauthorized metadata prevents every full-body read, even with a syntactically
    # valid review object. A second open is used only after metadata authorization.
    page.write_bytes(render(change(base, ("target_profiles",), ["human"])))
    real_open = Path.open
    opens = []
    def counted(path, *args, **kwargs):
        if path == page:
            opens.append(path)
        return real_open(path, *args, **kwargs)
    with mock.patch.object(Path, "open", counted):
        assert engine.authorized_page_snapshot(page, root / "wiki", engine.access_context(root, "codex")) is None
    assert len(opens) == 1, opens
    checked += 1

    golden = REPO / "tests/fixtures/versioned-memory-review.json"
    data = json.loads(golden.read_text(encoding="utf-8"))
    assert data["schemaVersion"] == 1 and len(data["cases"]) >= 15
    for case in data["cases"]:
        frontmatter, body = case["frontmatter"], case["body"]
        assert engine.compute_content_revision(frontmatter, body) == case["expectedContentRevision"], case["name"]
        assert engine.compute_policy_revision(frontmatter) == case["expectedPolicyRevision"], case["name"]
        assessment = engine.assess_memory_review(frontmatter, body)
        assert assessment["state"] == case["expectedState"], (case["name"], assessment)
        assert assessment["trusted"] == case["expectedTrusted"], (case["name"], assessment)
        assert engine._projection_hash(case["projection"]) == case["expectedContentRevision"], case["name"]
        surfaces("golden " + case["name"], frontmatter, body, allowed=case["expectedTrusted"],
                 marker="Human", raw=case["source"].encode("utf-8"), mcp=True)

    # Metadata changed between prefilter and the body-open must not be reused.
    page.write_bytes(render(base))
    opened = 0
    def replace_before_snapshot(path, *args, **kwargs):
        global opened
        if path == page:
            opened += 1
            if opened == 2:
                with real_open(page, "wb") as output:
                    output.write(render(change(base, ("target_profiles",), ["human"])))
        return real_open(path, *args, **kwargs)
    with mock.patch.object(Path, "open", replace_before_snapshot):
        assert engine.authorized_page_snapshot(page, root / "wiki", engine.access_context(root, "codex")) is None
    checked += 1

print(f"PASS versioned review: {checked} contract/synthetic cases; {len(data['cases'])} cross-language golden vectors; "
      "unchanged files; ACL-before-body; all three CLI read surfaces and two MCP tools across codex/opencode")
