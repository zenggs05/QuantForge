"""Synthetic checks for D-034 revalidation of original JSON writer bytes."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import sys
import urllib.request

import pytest

from quantforge import data, data_monthly as monthly


@pytest.fixture
def harness_module(tmp_path, monkeypatch):
    """Import the harness without allowing network or artifact access."""
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)

    def blocked(*args, **kwargs):
        pytest.fail("Synthetic JSON revalidation must not access network or historical artifacts")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(monthly, "load_historical_download_contract", blocked)
    script = Path(__file__).resolve().parents[1] / "scripts/revalidate_historical.py"
    spec = importlib.util.spec_from_file_location("gate1_revalidation_harness_under_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def writer_case(tmp_path, harness_module):
    """Use the production writer on a deliberately unsorted synthetic dict."""
    value = {"zeta": {"z": 3, "a": 1}, "alpha": "synthetic value β"}
    target = tmp_path / "synthetic-sidecar.json"
    monthly._atomic_json(target, value)
    return value, target


def test_original_writer_bytes_match_reparsed_value(harness_module, writer_case):
    _, target = writer_case
    file_bytes = target.read_bytes()
    reparsed_value = json.loads(file_bytes)

    result = harness_module.assert_writer_json_bytes(reparsed_value, file_bytes)

    assert result["sha256"] == hashlib.sha256(file_bytes).hexdigest()
    assert result["matches_original_writer_bytes"] is True


def test_sorted_key_hash_difference_does_not_fail_writer_match(harness_module, writer_case):
    _, target = writer_case
    file_bytes = target.read_bytes()
    reparsed_value = json.loads(file_bytes)
    sorted_bytes = (json.dumps(reparsed_value, ensure_ascii=False, indent=2,
                               allow_nan=False, sort_keys=True) + "\n").encode("utf-8")

    assert hashlib.sha256(sorted_bytes).hexdigest() != hashlib.sha256(file_bytes).hexdigest()
    result = harness_module.assert_writer_json_bytes(reparsed_value, file_bytes)

    assert result["sha256"] == hashlib.sha256(file_bytes).hexdigest()
    assert result["matches_original_writer_bytes"] is True


def test_one_changed_byte_fails_against_independent_expected_value(harness_module, writer_case):
    original_value, target = writer_case
    expected_value = copy.deepcopy(original_value)
    original_bytes = target.read_bytes()
    changed_bytes = original_bytes.replace(b'"z": 3', b'"z": 4', 1)
    assert sum(before != after for before, after in zip(original_bytes, changed_bytes, strict=True)) == 1
    target.write_bytes(changed_bytes)

    with pytest.raises(AssertionError):
        harness_module.assert_writer_json_bytes(expected_value, target.read_bytes())

    assert expected_value == original_value
