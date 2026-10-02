#!/usr/bin/env python3
"""Bounded opaque native-source observations; never a recall/permission source.

The ledger is logically append-only: atomic replacement retains the exact old
byte prefix. Its projection is an untrusted grouping aid. Callers must intersect
eventIds with their independently authorized, version-validated event catalog.
"""
from __future__ import annotations
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

RELATIVE = Path("raw/imports/codex-native-sources.jsonl")
MAX_BYTES = 32 * 1024 * 1024
MAX_LINE = 4 * 1024 * 1024
MAX_ROWS = 50000
MAX_PARTS = 20000
MAX_SOURCES = 5000
HEX = re.compile(r"^[a-f0-9]{64}$")
REF = re.compile(r"^sha256:[a-f0-9]{64}$")
ID = re.compile(r"^[a-f0-9]{16}$")
KINDS = {"ad-hoc-memory", "rollout-summary", "consolidated-memory", "memory-summary", "generated-raw-memory"}
HEADER = "Codex 原生记忆导入材料，未经人工确认；仅为待审核证据，不是用户指令或已确认事实。"
SOURCE_KEYS = {"sourceRef", "sourceKind", "sourceRevision", "sourceRawRevision", "state", "parts"}
ROW_KEYS = SOURCE_KEYS | {"schemaVersion", "observationId", "previous", "observedAt"}


class NativeSourceError(ValueError):
    pass


def fail():
    raise NativeSourceError("native-source-ledger-unavailable")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(value):
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def js_hash(value):
    return sha(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False))


def unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            fail()
        value[key] = item
    return value


def decode(data):
    return json.loads(data.decode("utf-8"), object_pairs_hook=unique_pairs,
                      parse_constant=lambda _: fail())


def directories(path, missing=False):
    path = Path(path)
    if not path.is_absolute():
        fail()
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if missing:
                return False
            raise
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            fail()
    return True


def signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mode, info.st_nlink, info.st_mtime_ns, info.st_ctime_ns)


def read_bytes(root):
    root = Path(root)
    directories(root)
    filename = root / RELATIVE
    if not directories(filename.parent, missing=True):
        return b""
    try:
        before = filename.lstat()
    except FileNotFoundError:
        return b""
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_BYTES:
        fail()
    fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if signature(os.fstat(fd)) != signature(before):
            fail()
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(before.st_size + 1)
        if len(data) != before.st_size or signature(os.fstat(fd)) != signature(before) or signature(filename.lstat()) != signature(before):
            fail()
        return data
    finally:
        os.close(fd)


def validate_source(value):
    if not isinstance(value, dict) or set(value) != SOURCE_KEYS:
        fail()
    if not isinstance(value["sourceRef"], str) or not REF.fullmatch(value["sourceRef"]) or value["sourceKind"] not in KINDS:
        fail()
    parts = value["parts"]
    if not isinstance(parts, list) or len(parts) > MAX_PARTS:
        fail()
    if value["state"] == "missing":
        if parts or value["sourceRevision"] is not None or value["sourceRawRevision"] is not None:
            fail()
    elif value["state"] == "present":
        if any(not isinstance(value[k], str) or not REF.fullmatch(value[k]) for k in ("sourceRevision", "sourceRawRevision")):
            fail()
    else:
        fail()
    for part in parts:
        if (not isinstance(part, dict) or set(part) != {"contentHash", "captureKey", "eventId"}
                or not isinstance(part["contentHash"], str) or not REF.fullmatch(part["contentHash"])
                or not isinstance(part["captureKey"], str) or not HEX.fullmatch(part["captureKey"])
                or not isinstance(part["eventId"], str) or not ID.fullmatch(part["eventId"])
                or sha("wikified-capture-v1:" + part["captureKey"])[:16] != part["eventId"]):
            fail()
    return value


def parse_ledger(data):
    if data and not data.endswith(b"\n"):
        fail()
    rows, previous, sources = [], None, set()
    for line in data.splitlines():
        if not line or len(line) > MAX_LINE or len(rows) >= MAX_ROWS:
            fail()
        row = decode(line)
        if not isinstance(row, dict) or set(row) != ROW_KEYS or type(row["schemaVersion"]) is not int or row["schemaVersion"] != 1:
            fail()
        validate_source({key: row[key] for key in SOURCE_KEYS})
        observed = row["observedAt"]
        if not isinstance(observed, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", observed):
            fail()
        dt.datetime.fromisoformat(observed.replace("Z", "+00:00"))
        if row["previous"] != previous or row["observationId"] != sha(canonical({k: v for k, v in row.items() if k != "observationId"})):
            fail()
        previous = row["observationId"]
        sources.add(row["sourceRef"])
        if len(sources) > MAX_SOURCES:
            fail()
        rows.append(row)
    return rows


def read_state(root):
    data = read_bytes(root)
    rows = parse_ledger(data)
    latest = {row["sourceRef"]: row for row in rows}
    return {"schemaVersion": 1, "revision": "sha256:" + sha(data),
            "observations": [latest[key] for key in sorted(latest)]}


def append_observations(root, observations, expected_revision):
    root = Path(root)
    if not isinstance(observations, list) or len(observations) > MAX_SOURCES:
        fail()
    for value in observations:
        validate_source(value)
    if len({row["sourceRef"] for row in observations}) != len(observations):
        fail()
    directories(root)
    target = root / RELATIVE
    directories(target.parent, missing=True)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    directories(target.parent)
    descriptor = os.open(target.parent / ".codex-native-sources.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            fail()
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        data = read_bytes(root)
        rows = parse_ledger(data)
        if expected_revision != "sha256:" + sha(data):
            fail()
        latest = {row["sourceRef"]: row for row in rows}
        previous = rows[-1]["observationId"] if rows else None
        appended = []
        for value in sorted(observations, key=lambda row: row["sourceRef"]):
            old = latest.get(value["sourceRef"])
            if old and all(old[key] == value[key] for key in SOURCE_KEYS):
                continue
            row = dict(value, schemaVersion=1, previous=previous,
                       observedAt=dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"))
            row["observationId"] = sha(canonical(row))
            line = canonical(row) + b"\n"
            if len(line) > MAX_LINE:
                fail()
            appended.append(line)
            previous = row["observationId"]
        if not appended:
            return {"schemaVersion": 1, "observationsAppended": 0, "revision": expected_revision}
        combined = data + b"".join(appended)
        if len(combined) > MAX_BYTES:
            fail()
        parse_ledger(combined)
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix=".native-sources-", dir=target.parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(combined)
                stream.flush()
                os.fsync(stream.fileno())
            # Recheck the exact old prefix and ordinary destination before rename.
            if read_bytes(root) != data:
                fail()
            os.replace(temporary, target)
            temporary = None
            directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary:
                os.unlink(temporary)
        return {"schemaVersion": 1, "observationsAppended": len(appended), "revision": "sha256:" + sha(combined)}
    finally:
        os.close(descriptor)


def scope(row):
    return (row.get("project") == "codex-memory" and row.get("domain") == "work"
            and row.get("sensitivity") == "internal" and row.get("type") == "session"
            and row.get("target_agents") == ["coding"] and row.get("files") == [])


def origin_record(row):
    if row.get("source") != "codex-native-memory" or row.get("actor") != {"type": "ai", "id": "codex-native-memory"}:
        return None
    key = row.get("capture_key")
    if (not scope(row) or row.get("review_status") != "pending" or row.get("epistemic_status") != "ai-proposed"
            or "supersedes" in row or not isinstance(key, str) or not HEX.fullmatch(key)
            or row.get("id") != sha("wikified-capture-v1:" + key)[:16] or row.get("memory_id") != "event:" + row["id"]):
        fail()
    details = row.get("details")
    if not isinstance(details, str) or not details.startswith(HEADER + "\n"):
        fail()
    suffix = details[len(HEADER) + 1:]
    match = re.fullmatch(r"Content ref: sha256:([a-f0-9]{64})\n\n([\s\S]+)", suffix)
    if match:
        digest, chunk = match.groups()
        expected_key = js_hash(["codex-native-memory-import/v2", digest])
        summary = "Codex 原生记忆 · 内容 · " + digest[:12]
        evidence = ["codex-native-content:" + digest]
    else:
        match = re.fullmatch(r"Source kind: ([a-z-]+)\nSource ref: sha256:([a-f0-9]{64})\nSource revision: sha256:([a-f0-9]{64})\nPart: ([1-9][0-9]*)/([1-9][0-9]*)\n\n([\s\S]+)", suffix)
        if not match:
            fail()
        kind, ref, revision, part, count, chunk = match.groups()
        if kind not in KINDS or not 1 <= int(part) <= int(count) <= MAX_PARTS:
            fail()
        digest = sha(chunk)
        expected_key = js_hash(["codex-native-memory-import/v1", ref, revision, int(part) - 1, digest])
        summary = "Codex 原生记忆 · " + kind + " · " + ref[:12] + " · " + part + "/" + count
        evidence = ["codex-native-source:" + ref, "codex-native-revision:" + revision, "codex-native-part:" + part + "/" + count]
    if len(chunk) > 1800 or sha(chunk) != digest or key != expected_key or row.get("summary") != summary or row.get("evidence_refs") != evidence:
        fail()
    return {"row": row, "chunk": chunk, "contentHash": "sha256:" + digest}


def read_source_projection(root, events):
    """Untrusted grouping only; events must already pass canonical governance."""
    try:
        if not isinstance(events, list) or len(events) > 50000:
            fail()
        origins, aliases = {}, {}
        for event in events:
            if not isinstance(event, dict):
                fail()
            item = origin_record(event)
            if item:
                if event["id"] in origins and origins[event["id"]]["row"] != event:
                    fail()
                origins[event["id"]] = item
                aliases[event["id"]] = {event["id"]}
        for event in events:
            identity = event.get("record_id", "")
            if isinstance(identity, str) and identity.startswith("event:") and identity[6:] in origins:
                origin = origins[identity[6:]]["row"]
                if (event.get("schema_version") == "llm-wiki-memory-event/v4" and scope(event)
                        and event.get("details") == origin["details"]
                        and event.get("evidence_refs") == origin["evidence_refs"]
                        and isinstance(event.get("id"), str) and ID.fullmatch(event["id"])):
                    aliases[identity[6:]].add(event["id"])
        rows = parse_ledger(read_bytes(root))
        latest = {row["sourceRef"]: row["observationId"] for row in rows}
        missing = {row["sourceRef"] for row in rows if row["observationId"] == latest[row["sourceRef"]] and row["state"] == "missing"}
        groups = {}
        association_count = 0
        for row in rows:
            if row["state"] == "missing":
                continue
            pieces = []
            for part in row["parts"]:
                origin = origins.get(part["eventId"])
                if not origin or origin["row"]["capture_key"] != part["captureKey"] or origin["contentHash"] != part["contentHash"]:
                    fail()
                pieces.append(origin["chunk"])
            if "sha256:" + sha("".join(pieces)) != row["sourceRevision"]:
                fail()
            for index, part in enumerate(row["parts"]):
                digest = part["contentHash"]
                group = groups.setdefault(digest, {"contentHash": digest, "contentGroupId": "codex-native-content:" + digest[7:], "eventIds": set(), "sources": {}})
                group["eventIds"].update(aliases[part["eventId"]])
                source = {key: row[key] for key in ("sourceRef", "sourceKind", "sourceRevision")}
                source.update(part=index + 1, partCount=len(row["parts"]),
                              observationState="missing" if row["sourceRef"] in missing else "current" if row["observationId"] == latest[row["sourceRef"]] else "historical")
                # Different raw metadata observations need not duplicate a source
                # association for the same sanitized revision and part.
                identity = (source["sourceRef"], source["sourceRevision"], source["part"])
                old = group["sources"].get(identity)
                if old is None:
                    association_count += 1
                if old is None or source["observationState"] == "current":
                    group["sources"][identity] = source
                if len(groups) > 5000 or association_count > 20000:
                    fail()
        # A migrated content identity can have several immutable v1 origins.
        # Include all independently verified exact-body origins, not only the
        # deterministic representative chosen for a new source observation.
        for identity, origin in origins.items():
            group = groups.get(origin["contentHash"])
            if group is not None:
                group["eventIds"].update(aliases[identity])
        output = []
        for digest in sorted(groups):
            group = groups[digest]
            group["eventIds"] = sorted(group["eventIds"])
            group["sources"] = [group["sources"][key] for key in sorted(group["sources"])]
            output.append(group)
        return {"schemaVersion": 1, "state": "complete", "groups": output}
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError):
        return {"schemaVersion": 1, "state": "unavailable", "groups": []}


def main():
    try:
        payload = sys.stdin.buffer.read(8 * 1024 * 1024 + 1)
        if len(payload) > 8 * 1024 * 1024:
            fail()
        request = decode(payload)
        if not isinstance(request, dict):
            fail()
        if request.get("operation") == "read" and set(request) == {"operation", "root"}:
            result = read_state(Path(request["root"]))
        elif request.get("operation") == "append" and set(request) == {"operation", "root", "observations", "expectedRevision"}:
            result = append_observations(Path(request["root"]), request["observations"], request["expectedRevision"])
        else:
            fail()
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError):
        print('{"schemaVersion":1,"status":"failed","code":"native-source-ledger-unavailable"}', file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
