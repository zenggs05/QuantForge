"""Audit-log corruption controls with synthetic JSON payloads only."""
import hashlib
import json

import pytest

from quantforge import data
from quantforge.online.audit_log import (
    GENESIS, canonical_json, read_records, verify_chain, write_record,
)


@pytest.fixture
def log_path(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    return tmp_path / "synthetic.jsonl"


def test_append_keeps_old_bytes_and_independent_expected_hash(log_path):
    first = {"counter": 1, "unicode": "合成"}
    result = write_record(log_path, first)
    before = log_path.read_bytes()
    expected = hashlib.sha256(GENESIS.encode("ascii") + canonical_json(first)).hexdigest()
    assert result == {"rows": 1, "head": expected}
    write_record(log_path, {"counter": 2})
    assert log_path.read_bytes().startswith(before)
    rows = read_records(log_path, 1, 2)
    assert rows[0]["payload"] == {"counter": 2}
    assert rows[0]["prev_hash"] == expected
    assert verify_chain(log_path)["rows"] == 2


@pytest.mark.parametrize("kind,message", [
    ("link", "hash link mismatch"), ("payload", "hash mismatch"),
    ("truncate", "Truncated"), ("schema", "envelope"),
    ("invalid_json", "Malformed"), ("noncanonical", "Noncanonical"),
])
def test_corrupted_chain_is_rejected(log_path, kind, message):
    for number in range(3):
        write_record(log_path, {"number": number})
    raw = log_path.read_bytes()
    rows = [json.loads(line) for line in raw.splitlines()]
    if kind == "link":
        rows[1]["prev_hash"] = "a" * 64
    elif kind == "payload":
        rows[1]["payload"]["number"] = 99
    elif kind == "schema":
        rows[1]["unknown"] = True
    if kind == "truncate":
        changed = raw[:-1]
    elif kind == "invalid_json":
        changed = b"not json\n"
    elif kind == "noncanonical":
        changed = b"\n".join(json.dumps(row).encode() for row in rows) + b"\n"
    else:
        changed = b"\n".join(canonical_json(row) for row in rows) + b"\n"
    log_path.write_bytes(changed)
    with pytest.raises(ValueError, match=message):
        verify_chain(log_path)
    # Range selection cannot hide corruption elsewhere in the stream.
    with pytest.raises(ValueError, match=message):
        read_records(log_path, 0, 0)


@pytest.mark.parametrize("payload", [{"y": float("nan")}, {"y": float("inf")}, [1, 2]])
def test_invalid_payload_never_changes_existing_log(log_path, payload):
    write_record(log_path, {"counter": 0})
    before = log_path.read_bytes()
    with pytest.raises(ValueError, match="payload"):
        write_record(log_path, payload)
    assert log_path.read_bytes() == before


@pytest.mark.parametrize("start,end", [(-1, None), (False, 1), (2, 1), (0, 1.2)])
def test_invalid_range_fails_before_read(log_path, start, end):
    with pytest.raises(ValueError, match="range"):
        read_records(log_path, start, end)


def test_empty_chain_and_duplicate_key_json(log_path):
    log_path.write_bytes(b"")
    assert verify_chain(log_path) == {"rows": 0, "head": GENESIS}
    log_path.write_bytes(b'{"prev_hash":"a","prev_hash":"b"}\n')
    with pytest.raises(ValueError, match="Malformed"):
        verify_chain(log_path)
