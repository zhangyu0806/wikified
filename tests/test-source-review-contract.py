#!/usr/bin/env python3
"""Portable source-review ledger golden vectors and pure strict-bound checks."""
import copy
import json
from pathlib import Path
import runpy
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
CORE = runpy.run_path(str(REPO / "bin/llm_wiki_source_review.py"))
LEDGER = "1" * 32
HASH = "sha256:" + "a" * 64


def row(action, sequence, previous, **changes):
    value = {"schema_version": CORE["SCHEMA"], "ledger_id": LEDGER, "sequence": sequence, "previous": previous,
        "action": action, "record_id": "memory:synthetic" if sequence else None,
        "record_revision": HASH if sequence else None, "binding_revision": HASH if sequence else None,
        "policy_revision": HASH if sequence else None, "actor_id": "human:synthetic", "at": "2026-09-18T08:00:00.000Z",
        "request_id": "request:" + str(sequence), "request_revision": HASH,
        "reason": "initialize" if not sequence else "source-change" if action == "hold" else "human-reverified", **changes}
    value["entry_revision"] = CORE["entry_revision"](value)
    return value


def fixtures():
    genesis = row("initialize", 0, None)
    hold = row("hold", 1, genesis["entry_revision"])
    release = row("release", 2, hold["entry_revision"], record_revision="sha256:" + "b" * 64, binding_revision="sha256:" + "c" * 64)
    cases = []
    def add(name, entries, valid):
        case = {"name": name, "ledgerId": LEDGER, "entries": entries, "expectedValid": valid}
        if valid:
            result = CORE["validate"](entries, LEDGER)
            case.update(expectedHead=result["head"], expectedHolds=sorted(result["holds"]))
        cases.append(case)
    add("genesis", [genesis], True)
    add("hold", [genesis, hold], True)
    add("release", [genesis, hold, release], True)
    add("new-hold-after-release", [genesis, hold, release, row("hold", 3, release["entry_revision"])], True)
    add("empty", [], False)
    add("missing-genesis", [hold], False)
    add("duplicate-hold", [genesis, hold, row("hold", 2, hold["entry_revision"])], False)
    add("release-without-hold", [genesis, row("release", 1, genesis["entry_revision"])], False)
    add("same-binding-release", [genesis, hold, row("release", 2, hold["entry_revision"], record_revision="sha256:" + "b" * 64)], False)
    add("same-record-release", [genesis, hold, row("release", 2, hold["entry_revision"], binding_revision="sha256:" + "c" * 64)], False)
    add("policy-changed-release", [genesis, hold, row("release", 2, hold["entry_revision"], record_revision="sha256:" + "b" * 64, binding_revision="sha256:" + "c" * 64, policy_revision="sha256:" + "d" * 64)], False)
    add("duplicate-request", [genesis, row("hold", 1, genesis["entry_revision"], request_id=genesis["request_id"])], False)
    add("time-reversed", [genesis, row("hold", 1, genesis["entry_revision"], at="2026-09-17T08:00:00.000Z")], False)
    add("unknown-field", [dict(genesis, injected="no")], False)
    add("wrong-digest", [dict(genesis, entry_revision=HASH)], False)
    add("wrong-previous", [genesis, row("hold", 1, HASH)], False)
    add("fractional-sequence", [row("initialize", 0.5, None)], False)
    add("boolean-sequence", [dict(genesis, sequence=False)], False)
    add("unknown-reason", [genesis, row("hold", 1, genesis["entry_revision"], reason="free text is forbidden")], False)
    request = {"action": "hold", "record_id": "memory:synthetic", "request_id": "request:hold",
        "reason": "source-change", "expected_ledger_revision": genesis["entry_revision"], "expected_record_revision": HASH,
        "expected_policy_revision": "sha256:" + "e" * 64, "capability": "synthetic-not-a-real-credential"}
    binding = {"version": 1, "actor": {"type": "human", "id": "human:synthetic"}, "reason": "人类重新审核", "at": "2026-09-18T08:00:00.000Z", "previous-revision": None}
    return {"schemaVersion": 1, "cases": cases, "digests": {"binding": binding,
        "expectedBindingRevision": CORE["binding_revision"](binding), "request": request,
        "expectedRequestRevision": CORE["request_revision"](request)}}


class SourceReviewContract(unittest.TestCase):
    def test_cross_language_vectors(self):
        data = json.loads((REPO / "tests/fixtures/source-review-vectors.json").read_text())
        self.assertEqual(data, fixtures())
        for case in data["cases"]:
            if case["expectedValid"]:
                result = CORE["validate"](case["entries"], case["ledgerId"])
                self.assertEqual(result["head"], case["expectedHead"])
                self.assertEqual(sorted(result["holds"]), case["expectedHolds"])
            else:
                with self.assertRaises(CORE["SourceReviewError"], msg=case["name"]):
                    CORE["validate"](case["entries"], case["ledgerId"])

    def test_raw_bounds_duplicates_blank_and_truncated_lines(self):
        encoded = CORE["encode"](row("initialize", 0, None))
        self.assertEqual(len(CORE["parse"](encoded, LEDGER)), 1)
        for raw in (b"", encoded[:-1], encoded + b"\n", b"\xef\xbb\xbf" + encoded,
                    encoded.replace(b'"sequence":0', b'"sequence":0,"sequence":0'),
                    encoded[:-2] + b" " * CORE["MAX_LINE_BYTES"] + b"}\n"):
            with self.assertRaises(CORE["SourceReviewError"]):
                CORE["parse"](raw, LEDGER)

    def test_marker_is_explicit_and_strict(self):
        self.assertIsNone(CORE["validate_marker"]({}))
        self.assertEqual(CORE["validate_marker"]({"source_review": {"version": 1, "ledger_id": LEDGER}}), LEDGER)
        for value in (None, False, {}, {"version": True, "ledger_id": LEDGER}, {"version": 1, "ledger_id": "ABC"}, {"version": 1, "ledger_id": LEDGER, "enabled": False}):
            with self.assertRaises(CORE["SourceReviewError"]):
                CORE["validate_marker"]({"source_review": value})

    def test_capability_is_not_hashed_and_entry_tamper_fails(self):
        request = fixtures()["digests"]["request"]
        self.assertEqual(CORE["request_revision"](request), CORE["request_revision"]({**request, "capability": "different"}))
        original = row("initialize", 0, None)
        for key, value in (("actor_id", "another"), ("at", "2026-09-19T08:00:00.000Z"), ("request_id", "another")):
            with self.assertRaises(CORE["SourceReviewError"]):
                CORE["validate"]([{**original, key: value}], LEDGER)

    def test_latest_release_floor_survives_later_holds(self):
        rows = fixtures()["cases"][2]["entries"]
        released = rows[-1]
        self.assertEqual(CORE["validate"](rows, LEDGER)["releases"], {released["record_id"]: released})
        rows = [*rows, row("hold", 3, released["entry_revision"])]
        self.assertEqual(CORE["validate"](rows, LEDGER)["releases"][released["record_id"]], released)
        newer = row("release", 4, rows[-1]["entry_revision"], record_revision="sha256:" + "d" * 64, binding_revision="sha256:" + "e" * 64)
        self.assertEqual(CORE["validate"]([*rows, newer], LEDGER)["releases"][released["record_id"]], newer)


if __name__ == "__main__":
    if sys.argv[1:] == ["--generate"]:
        print(json.dumps(fixtures(), ensure_ascii=False, indent=2))
    else:
        unittest.main()
