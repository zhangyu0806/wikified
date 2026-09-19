"""Persistent human source-review holds: strict contract and read-only snapshots."""
from __future__ import annotations
from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import stat
from types import MappingProxyType

EVENT = runpy.run_path(str(Path(__file__).resolve().with_name("llm_wiki_event_review.py")))
SCHEMA = "llm-wiki-source-review/v1"
MAX_ROWS, MAX_BYTES, MAX_LINE_BYTES, MAX_POLICY_BYTES = 4096, 4 * 1024 * 1024, 16 * 1024, 64 * 1024
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
IDENTIFIER = EVENT["IDENTIFIER"]
HASH = EVENT["HASH"]
LEDGER_ID = re.compile(r"[0-9a-f]{32}")
HOLD_REASONS = {"source-change", "source-withdrawal", "source-unavailable", "manual-concern"}
KEYS = {"schema_version", "ledger_id", "sequence", "previous", "action", "record_id", "record_revision",
        "binding_revision", "policy_revision", "actor_id", "at", "request_id", "request_revision", "reason", "entry_revision"}


class SourceReviewError(ValueError):
    def __init__(self):
        super().__init__("source review snapshot unavailable")


def entry_revision(row):
    return EVENT["digest"]("wikified-source-review-entry/v1", {k: v for k, v in row.items() if k != "entry_revision"})


def binding_revision(binding):
    return EVENT["digest"]("wikified-source-review-binding/v1", binding)


def request_revision(request):
    return EVENT["digest"]("source-review-request/v1", {k: v for k, v in request.items() if k != "capability"})


def raw_revision(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def validate_marker(policy):
    if not isinstance(policy, dict):
        raise SourceReviewError()
    if "source_review" not in policy:
        return None
    marker = policy["source_review"]
    if (not isinstance(marker, dict) or set(marker) != {"version", "ledger_id"}
        or type(marker["version"]) not in {int, float} or marker["version"] != 1
        or not matches(LEDGER_ID, marker["ledger_id"])):
        raise SourceReviewError()
    return marker["ledger_id"]


def encode(row):
    return (json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def validate(rows, ledger_id):
    """Validate integrity/state, not caller authority or durable anti-rollback."""
    try:
        if not matches(LEDGER_ID, ledger_id) or not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
            raise SourceReviewError()
        holds, releases, requests, head, last_at, total = {}, {}, set(), None, "", 0
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or set(row) != KEYS:
                raise SourceReviewError()
            if type(row["sequence"]) not in {int, float} or row["sequence"] != index:
                raise SourceReviewError()
            # Parsed 1 and 1.0 are the same portable JSON number. Raw transport
            # whitespace/number spelling is bounded separately by parse().
            size = len(encode({**row, "sequence": index})); total += size
            if (size > MAX_LINE_BYTES or total > MAX_BYTES or row["schema_version"] != SCHEMA
                or row["ledger_id"] != ledger_id or type(row["sequence"]) not in {int, float}
                or row["sequence"] != index or row["previous"] != head
                or not matches(IDENTIFIER, row["actor_id"]) or not matches(IDENTIFIER, row["request_id"])
                or row["request_id"] in requests or not matches(HASH, row["request_revision"])
                or not EVENT["valid_time"](row["at"]) or row["at"] < last_at
                or row["entry_revision"] != entry_revision(row)):
                raise SourceReviewError()
            action, identity = row["action"], row["record_id"]
            if index == 0:
                if (action != "initialize" or row["reason"] != "initialize"
                    or any(row[k] is not None for k in ("record_id", "record_revision", "binding_revision", "policy_revision"))):
                    raise SourceReviewError()
            else:
                if not matches(ID, identity) or not all(matches(HASH, row[k]) for k in ("record_revision", "binding_revision", "policy_revision")):
                    raise SourceReviewError()
                if action == "hold":
                    if row["reason"] not in HOLD_REASONS or identity in holds:
                        raise SourceReviewError()
                    holds[identity] = copy.deepcopy(row)
                elif action == "release":
                    held = holds.get(identity)
                    if (row["reason"] != "human-reverified" or held is None
                        or row["policy_revision"] != held["policy_revision"]
                        or row["binding_revision"] == held["binding_revision"]
                        or row["record_revision"] == held["record_revision"]):
                        raise SourceReviewError()
                    del holds[identity]
                    releases[identity] = copy.deepcopy(row)
                else:
                    raise SourceReviewError()
            head, last_at = row["entry_revision"], row["at"]
            requests.add(row["request_id"])
        return {"head": head, "holds": holds, "releases": releases}
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError):
        raise SourceReviewError() from None


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SourceReviewError()
        result[key] = value
    return result


def reject_constant(_):
    raise SourceReviewError()


def decode(raw):
    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant)


def parse(raw, ledger_id):
    try:
        if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES or not raw.endswith(b"\n"):
            raise SourceReviewError()
        lines = raw.split(b"\n")[:-1]
        if len(lines) > MAX_ROWS or any(not line.strip() or len(line) + 1 > MAX_LINE_BYTES for line in lines):
            raise SourceReviewError()
        rows = [decode(line) for line in lines]
        validate(rows, ledger_id)
        return rows
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError):
        raise SourceReviewError() from None


def signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def directory_identity(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise SourceReviewError()
    return (info.st_dev, info.st_ino)


def root_identity(root):
    if not root.is_absolute() or root.resolve(strict=True) != root:
        raise SourceReviewError()
    return (str(root), *directory_identity(root))


def read_file(path, maximum):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > maximum:
            raise SourceReviewError()
        raw = handle.read(maximum + 1)
        after = os.fstat(handle.fileno())
    if len(raw) != after.st_size or len(raw) > maximum or signature(before) != signature(after) or signature(after) != signature(path.lstat()):
        raise SourceReviewError()
    return raw, signature(after)


@contextmanager
def memory_lock(root, *, write=False, create=False):
    try:
        import fcntl
    except ImportError:
        raise SourceReviewError() from None
    if os.name != "posix":
        raise SourceReviewError()
    initial_root = root_identity(root)
    memory = root / "memory"
    before = directory_identity(memory)
    flags = (os.O_RDWR if write else os.O_RDONLY) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    if create:
        flags |= os.O_CREAT
    descriptor = os.open(memory / ".memory.lock", flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SourceReviewError()
        fcntl.flock(descriptor, (fcntl.LOCK_EX if write else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        def verify():
            if (root_identity(root) != initial_root or directory_identity(memory) != before
                or signature(os.fstat(descriptor)) != signature((memory / ".memory.lock").lstat())):
                raise SourceReviewError()
        verify()
        yield verify
        verify()
    finally:
        os.close(descriptor)


def _generation(root, policy_document):
    identity = root_identity(root)
    policy_directory = directory_identity(root / "policy")
    policy_raw, policy_stat = read_file(root / "policy/access.json", MAX_POLICY_BYTES)
    actual_policy = decode(policy_raw)
    if actual_policy != policy_document:
        raise SourceReviewError()
    ledger_id = validate_marker(actual_policy)
    if ledger_id is None:
        generation = (identity, policy_directory, policy_stat, policy_raw)
        rows, raw = [], b""
    else:
        directories = (directory_identity(root / "memory"), directory_identity(root / "memory/source-review"))
        lock_raw, lock_stat = read_file(root / "memory/.memory.lock", 1024)
        raw, ledger_stat = read_file(root / "memory/source-review/ledger.jsonl", MAX_BYTES)
        rows = parse(raw, ledger_id)
        generation = (identity, policy_directory, policy_stat, policy_raw, directories, lock_stat, lock_raw, ledger_stat, raw)
        if directories != (directory_identity(root / "memory"), directory_identity(root / "memory/source-review")):
            raise SourceReviewError()
    if root_identity(root) != identity or directory_identity(root / "policy") != policy_directory or read_file(root / "policy/access.json", MAX_POLICY_BYTES) != (policy_raw, policy_stat):
        raise SourceReviewError()
    return generation, ledger_id, rows, policy_raw, raw


class Snapshot:
    def __init__(self, root, policy_document, generation, ledger_id, rows, policy_raw, raw):
        result = validate(rows, ledger_id) if ledger_id is not None else {"head": None, "holds": {}, "releases": {}}
        self.enabled, self.ledger_id, self.head = ledger_id is not None, ledger_id, result["head"]
        self.held_ids = frozenset(result["holds"])
        self.holds = MappingProxyType(copy.deepcopy(result["holds"]))
        self.releases = MappingProxyType(copy.deepcopy(result["releases"]))
        self.rows = tuple(copy.deepcopy(rows))
        self.policy_revision, self.raw = raw_revision(policy_raw), raw
        self._root, self._policy, self._generation = root, copy.deepcopy(policy_document), generation

    def _verify_unlocked(self):
        if _generation(self._root, self._policy)[0] != self._generation:
            raise SourceReviewError()

    def verify(self):
        try:
            if self.enabled:
                with memory_lock(self._root):
                    self._verify_unlocked()
            else:
                self._verify_unlocked()
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError):
            raise SourceReviewError() from None


def _read_snapshot_unlocked(root, policy_document):
    return Snapshot(root, policy_document, *_generation(root, policy_document))


def read_snapshot(root, policyDocument):
    """Read only; missing marker is legacy-off, configured damage is never off."""
    try:
        if validate_marker(policyDocument) is not None:
            with memory_lock(root):
                return _read_snapshot_unlocked(root, policyDocument)
        return _read_snapshot_unlocked(root, policyDocument)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError):
        raise SourceReviewError() from None
