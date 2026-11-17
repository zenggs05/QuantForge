"""Canonical append-only event logs; integrity depends on an external anchor."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from quantforge import data

GENESIS = "0" * 64
ENVELOPE_SCHEMA = frozenset({"prev_hash", "this_hash", "payload"})


def canonical_json(value) -> bytes:
    """Encode exactly the JSON representation used by event hash links."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate audit JSON key")
        value[key] = item
    return value


def _parse(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=lambda text: (_ for _ in ()).throw(ValueError("Nonfinite audit JSON")))
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Malformed audit-log JSON") from exc


def _log_path(log_path) -> Path:
    root = data.REPOSITORY_ROOT.resolve()
    supplied = Path(log_path)
    path = supplied if supplied.is_absolute() else root / supplied
    if (".." in path.parts or path.suffix != ".jsonl" or not path.is_relative_to(root)
            or path.resolve() != path):
        raise ValueError("Audit-log path must be a local JSONL without path escapes")
    return path


def _hex_hash(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _envelope(previous: str, payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Audit-log payload must be a JSON object")
    try:
        digest = hashlib.sha256(previous.encode("ascii") + canonical_json(payload)).hexdigest()
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("Audit-log payload must contain finite JSON values") from exc
    return {"prev_hash": previous, "this_hash": digest, "payload": payload}


def _iter_records(path: Path):
    previous = GENESIS
    with path.open("rb") as handle:
        for number, raw in enumerate(handle):
            if not raw.endswith(b"\n"):
                raise ValueError(f"Truncated audit-log record at row {number}")
            row = _parse(raw)
            if (not isinstance(row, dict) or set(row) != ENVELOPE_SCHEMA
                    or not _hex_hash(row["prev_hash"]) or not _hex_hash(row["this_hash"])):
                raise ValueError(f"Invalid audit-log envelope at row {number}")
            if row["prev_hash"] != previous:
                raise ValueError(f"Audit-log hash link mismatch at row {number}")
            if _envelope(previous, row["payload"])["this_hash"] != row["this_hash"]:
                raise ValueError(f"Audit-log hash mismatch at row {number}")
            if raw != canonical_json(row) + b"\n":
                raise ValueError(f"Noncanonical audit-log record at row {number}")
            previous = row["this_hash"]
            yield row


def verify_chain(log_path: str | Path) -> dict:
    """Stream the entire log and report its row count and final link."""
    count, head = 0, GENESIS
    for record in _iter_records(_log_path(log_path)):
        count += 1
        head = record["this_hash"]
    return {"rows": count, "head": head}


def read_records(log_path: str | Path, start: int = 0, end: int | None = None) -> list[dict]:
    """Return envelopes in a zero-based range after validating the whole chain."""
    if type(start) is not int or start < 0 or (end is not None and (type(end) is not int or end < start)):
        raise ValueError("Invalid audit-log range")
    return [row for i, row in enumerate(_iter_records(_log_path(log_path)))
            if i >= start and (end is None or i < end)]


def _write_records(log_path, payloads, *, exclusive=False, chunk_rows=1) -> dict:
    """Single-writer streaming append; checkpoint publication uses exclusive mode."""
    path = _log_path(log_path)
    if type(chunk_rows) is not int or chunk_rows < 1:
        raise ValueError("Audit chunk rows must be positive")
    if exclusive and path.exists():
        raise FileExistsError(path)
    prior = verify_chain(path) if path.exists() else {"rows": 0, "head": GENESIS}
    path.parent.mkdir(parents=True, exist_ok=True)
    count, previous = prior["rows"], prior["head"]
    with path.open("xb" if exclusive else "ab") as handle:
        for payload in payloads:
            record = _envelope(previous, payload)
            handle.write(canonical_json(record) + b"\n")
            count += 1
            previous = record["this_hash"]
            if count % chunk_rows == 0:
                handle.flush()
        handle.flush()
        os.fsync(handle.fileno())
    return {"rows": count, "head": previous}


def write_record(log_path: str | Path, payload: dict) -> dict:
    """Validate existing links, append one record, and fsync the new tail.

    The caller owns single-writer synchronization. A published checkpoint log
    must not be appended because its recorded full-file hash would change.
    """
    _envelope(GENESIS, payload)
    return _write_records(log_path, [payload])
