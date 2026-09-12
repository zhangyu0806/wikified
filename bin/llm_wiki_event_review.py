#!/usr/bin/env python3
"""Pure opt-in JSONL v4 review contract. No filesystem or authentication I/O."""
from __future__ import annotations
import copy
import hashlib
import json
import math
import re
import struct
from datetime import datetime, timezone
from typing import Any

SCHEMA = "llm-wiki-memory-event/v4"
ALGORITHM = "wikified-event-review/v1"
ACTIONS = {"accept", "correct", "reject", "withdraw", "restore"}
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}$")
EVENT_ID = re.compile(r"^[0-9a-f]{16}$")
HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
INTERNAL = {"__memory_id", "__governance"}

def text(value: Any, limit: int = 1000, blank: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or (not blank and not value.strip()):
        raise ValueError("invalid bounded text")
    value.encode("utf-8")  # Reject lone surrogates, do not normalize Unicode.
    return value

def canonical(value: Any, depth: int = 0) -> Any:
    """Tagged, sorted JSON tree: no cross-language JSON-number serialization."""
    if depth > 64:
        raise ValueError("event nesting limit")
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["boolean", "true" if value else "false"]
    if isinstance(value, str):
        value.encode("utf-8")
        return ["string", value]
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number) or (number.is_integer() and abs(number) > 9007199254740991):
            raise ValueError("event number is not portable")
        return ["number", struct.pack(">d", 0.0 if number == 0 else number).hex()]
    if isinstance(value, list):
        return ["array", [canonical(item, depth + 1) for item in value]]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return ["object", [[canonical(key, depth + 1), canonical(value[key], depth + 1)] for key in sorted(value)]]
    raise ValueError("non-JSON event value")

def digest(label: str, value: Any) -> str:
    encoded = json.dumps([label, canonical(value)], ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()

def clean(event: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if key not in INTERNAL}

def record_revision(event: dict[str, Any]) -> str:
    return digest("wikified-event-record/v1", clean(event))

def target_revision(event: dict[str, Any]) -> str:
    return digest("wikified-event-target/v1", {k: v for k, v in clean(event).items() if k != "concepts"})

def content_revision(event: dict[str, Any]) -> str:
    return digest("wikified-event-content/v1", {k: v for k, v in clean(event).items() if k not in {"review", "concepts"}})

def policy_revision(event: dict[str, Any]) -> str:
    return digest("wikified-event-policy/v1", [event.get("record_id", event.get("memory_id")),
        event["project"], event["domain"], event["sensitivity"], sorted(event["target_agents"])])

def valid_time(value: Any) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", value):
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat(timespec="milliseconds").replace("+00:00", "Z") == value
    except ValueError:
        return False

def validate_binding(event: dict[str, Any]) -> bool:
    try:
        b = event["review"]
        keys = {"version", "algorithm", "decision", "target_id", "target_revision", "content_revision",
            "policy_revision", "actor", "at", "reason", "request_id", "request_revision"}
        if not isinstance(b, dict) or set(b) != keys or type(b["version"]) not in (int, float) or b["version"] != 1:
            return False
        if event["schema_version"] != SCHEMA or b["algorithm"] != ALGORITHM or b["decision"] not in ACTIONS:
            return False
        if event.get("source") != "human-review/v4" or event.get("lifecycle") != "active":
            return False
        if not EVENT_ID.fullmatch(event["id"]) or event["memory_id"] != "event:" + event["id"]:
            return False
        if not re.fullmatch(r"event:[0-9a-f]{16}", event["record_id"]) or not EVENT_ID.fullmatch(b["target_id"]):
            return False
        if b["actor"] != event["actor"] or set(b["actor"]) != {"type", "id"} or b["actor"]["type"] != "human":
            return False
        if not IDENTIFIER.fullmatch(b["actor"]["id"]) or not IDENTIFIER.fullmatch(b["request_id"]):
            return False
        if not valid_time(b["at"]) or b["at"] != event["timestamp"]:
            return False
        text(b["reason"], blank=b["decision"] not in {"withdraw", "restore"})
        if not all(HASH.fullmatch(b[key]) for key in ["target_revision", "content_revision", "policy_revision", "request_revision"]):
            return False
        if b["content_revision"] != content_revision(event) or b["policy_revision"] != policy_revision(event):
            return False
        terminal = b["decision"] in {"reject", "withdraw"}
        return event["review_status"] == ("rejected" if terminal else "approved") and event["epistemic_status"] == ("disputed" if terminal else "human-confirmed")
    except (KeyError, TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        return False

def validate_links(events: list[dict[str, Any]]) -> None:
    """Fail the complete snapshot on a damaged chain, never resurrect a parent."""
    index = {e["id"]: clean(e) for e in events if isinstance(e.get("id"), str)}
    reviewed: set[str] = set()
    requests: set[str] = set()
    for raw in events:
        e = clean(raw)
        if e.get("schema_version") != SCHEMA:
            continue
        if not validate_binding(e):
            raise ValueError("invalid event review binding")
        b = e["review"]
        target = index.get(b["target_id"])
        if target is None or target_revision(target) != b["target_revision"]:
            raise ValueError("event review target missing or changed")
        if e["record_id"] != target.get("record_id", target["memory_id"]) or policy_revision(e) != policy_revision(target):
            raise ValueError("event review scope changed")
        if b["target_id"] in reviewed or b["request_id"] in requests:
            raise ValueError("forked event review or repeated request identity")
        reviewed.add(b["target_id"]); requests.add(b["request_id"])
        allowed = [b["target_id"]]
        if b["decision"] in {"accept", "correct"}:
            allowed = list(dict.fromkeys(allowed + target.get("supersedes", [])))
        if e.get("supersedes") != allowed:
            raise ValueError("invalid event review replacement edges")
        status = target["review_status"]
        if not transition(b["decision"], status, target.get("schema_version")):
            raise ValueError("invalid event review transition")
        # Accept/reject/withdraw/restore cannot silently rewrite the claim.
        if b["decision"] != "correct" and (e.get("summary") != target.get("summary") or e.get("details", "") != target.get("details", "")):
            raise ValueError("non-correction changed event content")

def transition(action: str, status: str, schema: str | None) -> bool:
    if action == "accept":
        return status == "pending" or (schema != SCHEMA and status == "approved")
    if action == "correct":
        return status in {"pending", "approved"}
    if action == "reject":
        return status == "pending"
    if action == "withdraw":
        return status == "approved"
    return action == "restore" and status == "rejected"

def request_revision(request: dict[str, Any]) -> str:
    return digest("wikified-event-request/v1", {k:v for k,v in request.items() if k != "capability"})

def prepare(target: dict[str, Any], request: dict[str, Any], *, actor_id: str, can_review: bool, now: str, event_id: str) -> dict[str, Any]:
    if not can_review or not IDENTIFIER.fullmatch(actor_id) or not valid_time(now):
        raise ValueError("trusted event review authority required")
    allowed = {"action", "target_id", "expected_revision", "request_id", "reason", "summary", "details", "confirm_supersedes", "confirm_global_target", "capability"}
    required = {"action", "target_id", "expected_revision", "request_id", "reason"}
    if set(request) - allowed or not required <= set(request):
        raise ValueError("invalid review request fields")
    action = request["action"]
    if action not in ACTIONS or not IDENTIFIER.fullmatch(request["request_id"]) or not EVENT_ID.fullmatch(event_id):
        raise ValueError("invalid review action or identity")
    target = clean(target)
    if request["target_id"] != target["id"] or request["expected_revision"] != record_revision(target):
        raise ValueError("event revision conflict")
    if not transition(action, target["review_status"], target.get("schema_version")):
        raise ValueError("invalid event review transition")
    text(request["reason"], blank=action not in {"withdraw", "restore"})
    if action != "correct" and ("summary" in request or "details" in request):
        raise ValueError("only correction may change content")
    if action == "correct" and not ({"summary", "details"} & set(request)):
        raise ValueError("correction requires an explicit replacement")
    if any(v == "*" or v.lower() == "all" for v in target["target_agents"]) and request.get("confirm_global_target") is not True:
        raise ValueError("global target requires explicit confirmation")
    inherited = target.get("supersedes", []) if action in {"accept", "correct"} else []
    if inherited and request.get("confirm_supersedes") is not True:
        raise ValueError("inherited replacements require explicit confirmation")
    e = copy.deepcopy(target)
    e.pop("review", None)
    e.update(schema_version=SCHEMA, id=event_id, memory_id="event:"+event_id,
        record_id=target.get("record_id", target["memory_id"]), actor={"type":"human","id":actor_id},
        timestamp=now, lifecycle="active", source="human-review/v4",
        supersedes=list(dict.fromkeys([target["id"]]+inherited)))
    if action == "correct":
        if "summary" in request:
            e["summary"] = text(request["summary"], 10000)
        if "details" in request:
            e["details"] = text(request["details"], 50000, blank=True)
    terminal = action in {"reject", "withdraw"}
    e["review_status"] = "rejected" if terminal else "approved"
    e["epistemic_status"] = "disputed" if terminal else "human-confirmed"
    e["review"] = {"version":1,"algorithm":ALGORITHM,"decision":action,"target_id":target["id"],
        "target_revision":target_revision(target),"content_revision":content_revision(e),"policy_revision":policy_revision(e),
        "actor":e["actor"],"at":now,"reason":request["reason"],"request_id":request["request_id"],"request_revision":request_revision(request)}
    if not validate_binding(e):
        raise ValueError("invalid generated event review")
    return e
