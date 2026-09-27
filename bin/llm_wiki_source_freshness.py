"""Pure source assessment; caller supplies a complete authorized, trusted snapshot.

No filesystem/policy/body I/O. This is separate from both access control and P2
review binding. A locator is advisory, never an alternative source identity.
"""
from __future__ import annotations
import re
import unicodedata
from typing import Any

LIMITS = {"depth": 32, "visited": 256, "references": 256, "snapshotNodes": 10000}
IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
REVISION = re.compile(r"sha256:[0-9a-f]{64}")
KINDS = {"document", "conversation", "task", "external"}
RELATIONS = {"current-derived", "historical-citation", "independent-judgment"}
RANK = {"current": 0, "unassessed": 1, "stale": 2, "incomplete": 3, "unavailable": 4}


def _identity(value: Any) -> bool:
    return isinstance(value, str) and IDENTITY.fullmatch(value) is not None


def _revision(value: Any) -> bool:
    return isinstance(value, str) and REVISION.fullmatch(value) is not None


def _locator(value: Any) -> bool:
    if value is None or value == "":
        return True
    if (not isinstance(value, str) or len(value) > 1024
        or re.search(r"[\\\x00-\x1f\x7f:%?#\ud800-\udfff]", value)
        or value != unicodedata.normalize("NFC", value)
        or not re.fullmatch(r"(?:wiki|notes)/.+\.(?:md|markdown)", value)):
        return False
    # Match ECMAScript trim, not Python's broader whitespace class.
    whitespace = "\u0009\u000b\u000c\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000\ufeff\u000a\u000d\u2028\u2029"
    return all(part not in {"", ".", ".."} and part.strip(whitespace) == part and not part.endswith(".") for part in value.split("/"))


def _assessment(state: str) -> dict[str, Any]:
    return {"state": state, "reuseAllowed": state in {"current", "unassessed"}}


def assess(root_id: Any, nodes: Any) -> dict[str, Any]:
    """Return only a generic state, never source identity/title/count diagnostics."""
    if not _identity(root_id) or not isinstance(nodes, list):
        return _assessment("unavailable")
    if len(nodes) > LIMITS["snapshotNodes"]:
        return _assessment("incomplete")
    index: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if (not isinstance(node, dict) or not _identity(node.get("id"))
            or not _revision(node.get("revision")) or node["id"] in index
            or ("opaqueSources" in node and not isinstance(node["opaqueSources"], bool))):
            return _assessment("unavailable")
        index[node["id"]] = node
    visiting: set[str] = set()
    visited: set[str] = set()
    cache: dict[str, tuple[str, int]] = {}

    def walk(identity: str, depth: int) -> tuple[str, int]:
        if depth > LIMITS["depth"] or identity in visiting:
            return "incomplete", 0
        if identity in cache:
            state, height = cache[identity]
            return ("incomplete", 0) if depth + height > LIMITS["depth"] else (state, height)
        node = index.get(identity)
        if node is None:
            return "unavailable", 0
        visited.add(identity)
        if len(visited) > LIMITS["visited"] or node.get("opaqueSources", False):
            return "incomplete", 0
        sources = node.get("sources")
        if sources is None:
            sources = []
        if not isinstance(sources, list):
            return "unavailable", 0
        if len(sources) > LIMITS["references"]:
            return "incomplete", 0
        visiting.add(identity)
        state, height = ("current" if sources else "unassessed"), 0

        def merge(next_state: str) -> None:
            nonlocal state
            if RANK[next_state] > RANK[state]:
                state = next_state

        for source in sources:
            if (not isinstance(source, dict) or not isinstance(source.get("kind"), str)
                or source["kind"] not in KINDS or not _identity(source.get("id"))):
                merge("unavailable")
                continue
            relation = source.get("relation")
            if relation is None or relation == "":
                merge("unassessed")
                continue
            if not isinstance(relation, str) or relation not in RELATIONS:
                merge("unavailable")
                continue
            if relation != "current-derived":
                merge("unassessed")
                continue
            if source["kind"] != "document" or not _revision(source.get("revision")) or not _locator(source.get("locator")):
                merge("unavailable")
                continue
            target = index.get(source["id"])
            if target is None:
                merge("unavailable")
                continue
            if target["revision"] != source["revision"]:
                merge("stale")
                continue
            nested_state, nested_height = walk(source["id"], depth + 1)
            height = max(height, nested_height + 1)
            if not _assessment(nested_state)["reuseAllowed"]:
                merge(nested_state)
        visiting.remove(identity)
        cache[identity] = (state, height)
        return state, height

    return _assessment(walk(root_id, 0)[0])
