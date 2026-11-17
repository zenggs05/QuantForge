"""Synthetic tests for the narrow Gate 1 ledger atomic-write retry path."""

from __future__ import annotations

import errno
from pathlib import Path

import pytest

from quantforge import data_monthly as monthly


def _payload(label: str = "new") -> bytes:
    return ("{\"state\":\"" + label + "\"}\n").encode("ascii")


def _patch_replace(monkeypatch, outcomes: list[object]):
    """Fail selected calls, then perform the real atomic rename."""
    real_replace = Path.replace
    calls: list[int] = []

    def replace(source: Path, target: Path):
        index = len(calls)
        calls.append(index)
        if index < len(outcomes):
            outcome = outcomes[index]
            if isinstance(outcome, BaseException):
                raise outcome
        return real_replace(source, target)

    monkeypatch.setattr(Path, "replace", replace)
    return calls


def _patch_sleep(monkeypatch):
    waits: list[float] = []

    def sleep(seconds: float):
        waits.append(seconds)

    monkeypatch.setattr(monthly.time, "sleep", sleep)
    return waits


def test_happy_no_permission_error_commits_once(tmp_path, monkeypatch):
    target = tmp_path / "ledger.json"
    calls = _patch_replace(monkeypatch, [])
    waits = _patch_sleep(monkeypatch)

    result = monthly._atomic_json_with_retry(target, _payload())

    assert result["attempts"] == 1
    assert result["permission_error_retries"] == 0
    assert result["waited_seconds"] == 0.0
    assert len(calls) == 1
    assert waits == []
    assert target.read_bytes() == _payload()
    assert not list(tmp_path.glob(".*.partial"))


def test_single_permission_error_then_success(tmp_path, monkeypatch):
    target = tmp_path / "ledger.json"
    calls = _patch_replace(monkeypatch, [PermissionError("transient lock")])
    waits = _patch_sleep(monkeypatch)

    result = monthly._atomic_json_with_retry(target, _payload())

    assert result["attempts"] == 2
    assert result["permission_error_retries"] == 1
    assert result["waited_seconds"] == pytest.approx(0.1)
    assert len(calls) == 2
    assert waits == [0.1]
    assert target.read_bytes() == _payload()
    assert not list(tmp_path.glob(".*.partial"))


def test_five_permission_errors_then_success(tmp_path, monkeypatch):
    target = tmp_path / "ledger.json"
    calls = _patch_replace(monkeypatch, [PermissionError("lock")] * 5)
    waits = _patch_sleep(monkeypatch)

    result = monthly._atomic_json_with_retry(target, _payload())

    assert result["attempts"] == 6
    assert result["permission_error_retries"] == 5
    assert result["waited_seconds"] == pytest.approx(0.1 + 0.2 + 0.5 + 1.0 + 2.0)
    assert len(calls) == 6
    assert waits == [0.1, 0.2, 0.5, 1.0, 2.0]
    assert target.read_bytes() == _payload()
    assert not list(tmp_path.glob(".*.partial"))


def test_exhaustion_raises_ledger_persistence_error(tmp_path, monkeypatch):
    target = tmp_path / "ledger.json"
    calls = _patch_replace(monkeypatch, [PermissionError("persistent lock")] * 11)
    waits = _patch_sleep(monkeypatch)

    with pytest.raises(monthly.LedgerPersistenceError) as error:
        monthly._atomic_json_with_retry(target, _payload())

    assert len(calls) == 11
    assert waits == [0.1, 0.2, 0.5, 1.0, 2.0, 3.0, 3.0, 3.0, 3.0, 3.0]
    assert error.value.diagnostics == {
        "attempts": 11,
        "waited_seconds": pytest.approx(sum(waits)),
        "permission_error_retries": 11,
    }
    assert not target.exists()
    assert not list(tmp_path.glob(".*.partial"))


def test_non_permission_oserror_passthrough(tmp_path, monkeypatch):
    target = tmp_path / "ledger.json"
    calls = _patch_replace(monkeypatch, [OSError(errno.ENOSPC, "disk full")])
    waits = _patch_sleep(monkeypatch)
    diagnostics_before = monthly.get_ledger_persistence_diagnostics()

    with pytest.raises(OSError) as error:
        monthly._atomic_json_with_retry(target, _payload())

    assert error.value.errno == errno.ENOSPC
    assert len(calls) == 1
    assert waits == []
    assert monthly.get_ledger_persistence_diagnostics() == diagnostics_before
    assert not target.exists()
    assert not list(tmp_path.glob(".*.partial"))


def test_interrupt_leaves_target_unchanged(tmp_path, monkeypatch):
    target = tmp_path / "ledger.json"
    original = _payload("old")
    target.write_bytes(original)
    calls = _patch_replace(monkeypatch, [PermissionError("transient lock")])

    def interrupt(_seconds: float):
        raise KeyboardInterrupt

    monkeypatch.setattr(monthly.time, "sleep", interrupt)
    with pytest.raises(KeyboardInterrupt):
        monthly._atomic_json_with_retry(target, _payload("new"))

    assert len(calls) == 1
    assert target.read_bytes() == original
    # A retry interruption may clean the staged file or leave a clearly named
    # orphan for the next process; it must never replace the target in place.
    leftovers = list(tmp_path.glob(".*.partial"))
    assert all(path.name.startswith(".ledger.json.") for path in leftovers)
