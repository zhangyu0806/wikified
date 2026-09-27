#!/usr/bin/env python3
"""Independent public source contract golden vectors; synthetic data only."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("source_freshness", ROOT / "bin" / "llm_wiki_source_freshness.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
VECTORS = json.loads((ROOT / "tests" / "fixtures" / "source-freshness-vectors.json").read_text(encoding="utf-8"))
HASH, ALTERNATE = VECTORS["revision"], VECTORS["alternateRevision"]


def ref(identity):
    return {"kind": "document", "id": identity, "revision": HASH, "relation": "current-derived"}


def node(identity, sources=None):
    return {"id": identity, "revision": HASH, "sources": [] if sources is None else sources}


def fixture(vector):
    if "generate" in vector:
        g = vector["generate"]
        identity = lambda i: "root" if i == 0 else f"node-{i}"
        nodes = [node(identity(i)) for i in range(g["count"])]
        if g["shape"] == "chain":
            for i in range(len(nodes) - 1):
                nodes[i]["sources"] = [ref(identity(i + 1))]
            if g.get("cycle"):
                nodes[-1]["sources"] = [ref("root")]
            if g.get("changedLeaf"):
                nodes[-1]["revision"] = ALTERNATE
            if g.get("missingLeaf"):
                nodes.pop()
        elif g["shape"] == "star":
            nodes[0]["sources"] = [ref(n["id"]) for n in nodes[1:]]
        return nodes
    nodes = copy.deepcopy(vector["nodes"])
    for n in nodes:
        n.setdefault("revision", ALTERNATE if n.pop("useAlternateRevision", False) else HASH)
        if isinstance(n.get("sources"), list):
            for source in n["sources"]:
                if isinstance(source, dict) and source.pop("useDefaultRevision", False):
                    source["revision"] = HASH
    return nodes


class SourceFreshnessContract(unittest.TestCase):
    def test_golden_vectors_and_no_mutation(self):
        self.assertEqual(VECTORS["schemaVersion"], 1)
        self.assertEqual(len(VECTORS["cases"]), 31)
        for vector in VECTORS["cases"]:
            with self.subTest(vector=vector["name"]):
                nodes = fixture(vector)
                previous = copy.deepcopy(nodes)
                self.assertEqual(module.assess("root", nodes), {"state": vector["state"], "reuseAllowed": vector["reuseAllowed"]})
                self.assertEqual(nodes, previous)

    def test_invalid_runtime_inputs_are_generic(self):
        expected = {"state": "unavailable", "reuseAllowed": False}
        for identity in ["", " root", "root\n", "根", "x" * 129, None, [], {}]:
            self.assertEqual(module.assess(identity, [node("root")]), expected)
        for revision in [HASH + "\n", HASH.upper(), "sha256:a", 1, None]:
            self.assertEqual(module.assess("root", [dict(node("root"), revision=revision)]), expected)
        for nodes in [None, {}, "hidden-title", True]:
            self.assertEqual(module.assess("root", nodes), expected)
        self.assertEqual(module.assess("root", [dict(node("root"), opaqueSources="yes")]), expected)

    def test_locator_safety_and_unassessed_relations(self):
        for locator in ["/notes/x.md", "notes/../x.md", "notes//x.md", "notes/x.md#heading", "notes/x.md?x", "notes/%2e%2e/x.md", "notes\\x.md", "file:///x.md", "notes/e\u0301.md", "notes/x.md\n", "notes/ x.md", "notes/x./y.md"]:
            with self.subTest(locator=locator):
                self.assertEqual(module.assess("root", [node("root", [dict(ref("leaf"), locator=locator)]), node("leaf")]), {"state": "unavailable", "reuseAllowed": False})
        for relation in ["historical-citation", "independent-judgment"]:
            self.assertEqual(module.assess("root", [node("root", [dict(ref("leaf"), relation=relation)]), dict(node("leaf"), revision=ALTERNATE, opaqueSources=True)]), {"state": "unassessed", "reuseAllowed": True})

    def test_memoized_dag_depth_and_bounded_repeated_references(self):
        nodes = [node("root", [ref("leaf"), ref("n1")]), node("leaf")]
        for i in range(1, 33):
            nodes.append(node(f"n{i}", [ref("leaf" if i == 32 else f"n{i + 1}")]))
        self.assertEqual(module.assess("root", nodes), {"state": "incomplete", "reuseAllowed": False})
        self.assertEqual(module.LIMITS, {"depth": 32, "visited": 256, "references": 256, "snapshotNodes": 10000})
        self.assertEqual(module.assess("root", [node("root", [ref("leaf")] * 256), node("leaf")]), {"state": "current", "reuseAllowed": True})


if __name__ == "__main__":
    unittest.main()
