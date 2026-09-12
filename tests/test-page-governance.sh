#!/usr/bin/env bash
# Isolated page ACL regression: no installed CLI, real vault or service is used.
set -euo pipefail
REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
python3 - "$REPO" <<'PY'
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest import mock

repo = Path(sys.argv[1])
loader = importlib.machinery.SourceFileLoader("page_governance_regression", str(repo / "bin/llm-wiki-enrich"))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
sys.modules[loader.name] = module
loader.exec_module(module)
marker = "pagegovernancefixturemarker"

def quartet(*, quote=False, domain="work", sensitivity="internal", review="approved", targets=None):
    fields = {
        "domain": domain, "sensitivity": sensitivity, "review_state": review,
        "target_profiles": targets if targets is not None else ["codex", "opencode"],
    }
    return "\n".join(
        (json.dumps(key) if quote else key) + ": " +
        (json.dumps(value, ensure_ascii=False) if quote or isinstance(value, list) else value)
        for key, value in fields.items()
    )

def product(*, domain="work", sensitivity="internal", review="not-required", targets=None):
    top_review = {"not-required": "approved", "accepted": "approved", "corrected": "approved",
                  "pending": "pending", "rejected": "rejected"}[review]
    return (
        '"memory_id": "memory-phase1"\n' + quartet(quote=True, domain=domain,
            sensitivity=sensitivity, review=top_review, targets=targets) +
        '\n"epistemic_status": "human-confirmed"\n'
        '"project": "中文项目"\n'
        '"mind2one":\n'
        '  "version": 1\n'
        '  "id": "memory-phase1"\n'
        '  "kind": "memory"\n'
        '  "para": "resource"\n'
        '  "topics": ["标签"]\n'
        f'  "origin": "{"human" if review == "not-required" else "ai"}"\n'
        f'  "review-state": "{review}"\n'
        '  "agent": "codex"\n'
        '  "operation-id": "operation-phase1"\n'
        '  "sources":\n'
        '    - "kind": "document"\n'
        '      "id": "source-phase1"\n'
        '      "label": "domain: personal is unrelated source text"'
    )

with tempfile.TemporaryDirectory(prefix="wikified-page-governance-") as temporary:
    root = Path(temporary)
    (root / "wiki/context").mkdir(parents=True)
    (root / "memory/events").mkdir(parents=True)
    (root / "policy").mkdir()
    shutil.copyfile(repo / "templates/access-policy.json", root / "policy/access.json")
    path = root / "wiki/context/CRITICAL_FACTS.md"
    checked = 0

    def write(header, *, prefix="", newline="\n"):
        text = marker + "\n" if header is None else "---\n" + header + "\n---\n" + marker + "\n"
        path.write_bytes((prefix + text.replace("\n", newline)).encode("utf-8"))

    def metadata():
        with contextlib.redirect_stderr(io.StringIO()):
            return module.page_metadata(path, root / "wiki")

    def run(profile, args):
        result = subprocess.run(
            [sys.executable, str(repo / "bin/llm-wiki-enrich"), "--agent-profile", profile, *args],
            env={**os.environ, "LLM_WIKI_ROOT": str(root)}, capture_output=True, text=True,
        )
        assert marker not in result.stderr, result.stderr
        return result

    def surfaces(expected, *, malformed=False):
        for profile in ("codex", "opencode"):
            search = run(profile, ["--query", marker, "--json", "--max-chars", "12000"])
            assert search.returncode == 0, search.stderr
            assert (marker in search.stdout) == expected, (profile, search.stdout, search.stderr)
            direct = run(profile, ["--read-page", "wiki/context/CRITICAL_FACTS.md"])
            assert (direct.returncode == 0) == expected, (profile, direct.stdout, direct.stderr)
            assert (marker in direct.stdout) == expected, direct.stdout
            session = run(profile, ["--session-start"])
            assert session.returncode == 0, session.stderr
            assert (marker in session.stdout) == expected, (profile, session.stdout, session.stderr)
            if malformed:
                assert "invalid or unsupported page governance" in search.stderr, search.stderr
                assert "page unavailable" in direct.stderr, direct.stderr

    def allowed(name, header, **options):
        write(header, **options)
        result = metadata()
        assert result is not None, name
        assert module.is_authorized(result.governance, module.access_context(root, "codex")), name
        surfaces(True)
        return result

    valid = product()
    json_product = valid.split('"mind2one":')[0] + '"mind2one": ' + json.dumps({
        "version": 1, "id": "memory-phase1", "kind": "note", "origin": "human",
        "review-state": "not-required", "sources": [], "para": None,
        "topics": [], "project-ids": [], "area-ids": [], "due-at": None, "action": None,
    })
    result = allowed("product quoted", valid)
    assert result.memory_id == "memory-phase1"
    assert result.governance["project"] == "中文项目"
    moved = path.with_name("moved.md")
    path.rename(moved)
    assert module.page_metadata(moved, root / "wiki").memory_id == result.memory_id
    moved.rename(path)
    allowed("BOM CRLF", valid, prefix="\ufeff", newline="\r\n")
    allowed("single quoted keys", valid.replace('"domain": "work"', "'domain': 'work'"))
    allowed("escaped quoted key", valid.replace('"domain"', '"\\u0064omain"'))
    allowed("quoted inline comments", valid.replace('"domain": "work"', '"domain": "work" # scope'))
    allowed("unquoted product", valid.replace('"domain": "work"', 'domain: work'))
    allowed("legacy zero fields", None)
    allowed("legacy ordinary metadata", "title: Bob's note\ntags: [one, two]\nsource:\n  domain: personal")
    fallback = allowed("legacy quartet", quartet())
    assert fallback.memory_id.startswith("wiki:")
    assert fallback.governance["epistemic_status"] == "legacy-imported"
    allowed("legacy aliases", quartet().replace("review_state", "review_status").replace("target_profiles", "target_agents"))
    allowed("legacy optional null project", quartet() + "\nproject: null")
    allowed("consistent aliases", quartet() + '\nreview_status: approved\ntarget_agents: ["codex", "opencode"]')
    allowed("block targets", quartet().replace('target_profiles: ["codex", "opencode"]', "target_profiles:\n  - codex\n  - 'opencode'"))
    allowed("flow list comments", quartet().replace('target_profiles: ["codex", "opencode"]', "target_profiles: [codex, 'opencode'] # explicit"))
    allowed("nested irrelevant metadata", valid + '\nsource-description: |\n  domain: personal\n  review_state: rejected\nother:\n  target_profiles: [human]')
    allowed("human product JSON flow", json_product)
    allowed("product JSON with comment", json_product + " # generated by product")
    allowed("unrelated JSON cannot override", json_product.replace('"sources": []',
        '"sources": [{"kind": "document", "id": "source-one", "domain": "personal", "review-state": "rejected"}]'))
    checked += 18

    for name, header in (
        ("personal", product(domain="personal")),
        ("human only", product(targets=["human"])),
        ("pending", product(review="pending")),
        ("rejected", product(review="rejected")),
        ("confidential", product(sensitivity="confidential")),
        # P2 intentionally preserves human inspection, not default durable AI recall,
        # for accepted/corrected v1 AI records with no content binding.
        ("legacy AI accepted is unbound", product(review="accepted")),
        ("legacy AI corrected is unbound", product(review="corrected")),
        ("nested cannot widen", product(targets=["human"]) + "\nsource:\n  target_profiles: [codex, opencode]"),
    ):
        write(header)
        assert metadata() is not None, name
        surfaces(False)
        checked += 1

    invalid = {
        "missing domain": quartet().replace("domain: work\n", ""),
        "missing sensitivity": quartet().replace("sensitivity: internal\n", ""),
        "missing review": quartet().replace("review_state: approved\n", ""),
        "missing targets": quartet().split("\ntarget_profiles:")[0],
        "only one ACL": "domain: work",
        "id without ACL": "memory_id: memory-phase1",
        "epistemic without ACL": "epistemic_status: human-confirmed",
        "quoted duplicate": quartet() + '\n"domain": personal',
        "escaped duplicate": quartet() + '\n"\\u0064omain": personal',
        "single quoted duplicate": quartet() + "\n'domain': personal",
        "review alias conflict": quartet() + "\nreview_status: pending",
        "targets alias conflict": quartet() + "\ntarget_agents: [human]",
        "unknown domain": quartet().replace("domain: work", "domain: unknown"),
        "unknown sensitivity": quartet().replace("sensitivity: internal", "sensitivity: unknown"),
        "unknown review": quartet().replace("review_state: approved", "review_state: unknown"),
        "invalid target": quartet().replace('["codex", "opencode"]', '["bad target"]'),
        "duplicate targets": quartet().replace('["codex", "opencode"]', '["codex", "codex"]'),
        "target nested map": quartet().replace('["codex", "opencode"]', "\n  codex: true"),
        "target null declaration": quartet().replace('["codex", "opencode"]', ""),
        "target list trailing garbage": quartet().replace('["codex", "opencode"]', '["codex"] garbage'),
        "target list nested": quartet().replace('["codex", "opencode"]', '[["codex"]]'),
        "target list malformed": quartet().replace('["codex", "opencode"]', '["codex" "opencode"]'),
        "unknown product version": valid.replace('"version": 1', '"version": 99'),
        "missing product version": valid.replace('  "version": 1\n', ''),
        "quoted product version": valid.replace('"version": 1', '"version": "1"'),
        "duplicate product version": valid.replace('"version": 1', '"version": 1\n  version: 1'),
        "product identity mismatch": valid.replace('"id": "memory-phase1"', '"id": "different-id"'),
        "product review mismatch": valid.replace('"review-state": "not-required"', '"review-state": "pending"'),
        "product missing epistemic": valid.replace('"epistemic_status": "human-confirmed"\n', ''),
        "product missing memory id": valid.replace('"memory_id": "memory-phase1"\n', ''),
        "AI bypass review": valid.replace('"origin": "human"', '"origin": "ai"'),
        "mind object flow": quartet() + '\nmind2one: {version: 99}',
        "JSON duplicate identity": json_product.replace('"id": "memory-phase1"', '"id": "different", "id": "memory-phase1"'),
        "JSON unknown version": json_product.replace('"version": 1', '"version": 99'),
        "JSON infinite value": json_product.replace('"action": null', '"action": NaN'),
        "JSON overflow in unknown field": json_product.replace('"action": null', '"action": 1e999'),
        "JSON overflow in governance": json_product.replace('"version": 1', '"version": 1e999'),
        "JSON negative nested overflow": json_product.replace('"sources": []', '"sources": [{"number": -1e999}]'),
        "JSON large version integer": json_product.replace('"version": 1', '"version": ' + "1" * 5000),
        "JSON trailing garbage": json_product + " trailing",
        "JSON nested after flow": json_product + "\n  version: 1",
        "anchor": quartet().replace("domain: work", "domain: &shared work"),
        "alias": quartet().replace("domain: work", "domain: *shared"),
        "tag": quartet().replace("domain: work", "domain: !!str work"),
        "merge": quartet() + "\n<<: *shared",
        "quoted merge": quartet() + '\n"<<": *shared',
        "broken quote": quartet().replace("domain: work", '"domain: work'),
        "unterminated value": quartet().replace("domain: work", 'domain: "work'),
        "scalar with children": quartet().replace("domain: work", "domain: work\n  sensitivity: public"),
        "indented root": "  " + quartet().replace("\n", "\n  "),
        "control key": valid.replace('"domain"', '"domain\\u0000"'),
        "control scalar": valid.replace('"domain": "work"', '"domain": "work\\n"'),
        "surrogate scalar": valid.replace('"domain": "work"', '"domain": "\\ud800"'),
        "large scalar number": valid.replace('"version": 1', '"version": ' + "1" * 5000),
        "large ACL scalar number": valid.replace('"domain": "work"', '"domain": ' + "1" * 5000),
        "unclosed unrelated flow": quartet() + "\nrelated: [incomplete",
        "flow root": '{"domain": "personal"}',
        "oversized": quartet() + "\nlarge: " + "x" * (module.MAX_FRONTMATTER_BYTES + 1),
    }
    prefix, encoded = json_product.split('"mind2one": ', 1)
    decoded = json.loads(encoded)
    for field in ("origin", "review-state", "version", "id"):
        for index, malformed in enumerate((None, True, False, [], {}, 0, 1.5)):
            invalid[f"product {field} wrong type {index}"] = (
                prefix + '"mind2one": ' + json.dumps({**decoded, field: malformed})
            )
    for name, header in invalid.items():
        write(header)
        assert metadata() is None, name
        surfaces(False, malformed=True)
        checked += 1

    for name, content in {
        "missing closing delimiter": "---\n" + quartet() + "\n" + marker,
        "blank before delimiter": "\n---\n" + quartet() + "\n---\n" + marker,
        "trailing delimiter": "--- \n" + quartet() + "\n---\n" + marker,
        "invalid UTF8": b"---\ndomain: \xff\n---\n" + marker.encode(),
    }.items():
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
        assert metadata() is None, name
        surfaces(False, malformed=True)
        checked += 1

    # Denied pages are opened only for their bounded metadata, never for ranking their body.
    write(product(targets=["human"]))
    real_open = Path.open
    page_opens = []
    def tracked(candidate, *args, **kwargs):
        if candidate == path:
            page_opens.append(1)
        return real_open(candidate, *args, **kwargs)
    with mock.patch.object(Path, "open", tracked):
        result = module.wiki_candidates(root, marker, module.token_terms([marker]),
            module.query_term_groups([marker]), module.access_context(root, "codex"))
    assert result == [] and len(page_opens) == 1, page_opens
    print(f"PASS page governance: {checked} synthetic cases; search/read/session-start for codex+opencode; pre-body ACL")
PY
