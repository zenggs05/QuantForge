"""Validate the historical manifest using invented hashes and calendar bounds."""

import copy
import json
import socket
import urllib.request

import pandas as pd
import pytest

from quantforge import data, data_monthly as monthly


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Manifest validation must be offline")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture
def contract():
    return monthly.load_historical_download_contract()


@pytest.fixture
def entries(contract):
    rows = []
    for month in contract["months"]:
        start = pd.Timestamp(f"{month}-01T00:00:00Z")
        end = start + pd.offsets.MonthBegin(1)
        rows.append({
            "month": month,
            "zip_sha256": "a" * 64,
            "checksum_text_sha256": "b" * 64,
            "parquet_sha256": "c" * 64,
            "csv_sha256": "d" * 64,
            "rows": int((end - start) / pd.Timedelta(minutes=1)),
            "timestamp_unit": "ms" if month < "2025-01" else "us",
            "first_open_utc": start.isoformat(),
            "last_open_utc": (end - pd.Timedelta(minutes=1)).isoformat(),
            "bytes_downloaded": 100,
            "zip_bytes": 100,
            "checksum_bytes": 94,
            "attempts": 1,
            "fetched_at_utc": "2026-11-06T00:00:00+00:00",
        })
    return rows


def test_complete_manifest_has_correct_calendar_distribution_and_totals(contract, entries):
    manifest = monthly.build_historical_manifest(entries, contract, protocol_commit="1" * 40)
    monthly.validate_historical_manifest(manifest, contract)
    assert manifest["total_rows"] == sum(entry["rows"] for entry in entries)
    assert manifest["total_bytes_downloaded"] == 6800
    assert sum(entry["timestamp_unit"] == "ms" for entry in manifest["months"]) == 60
    assert sum(entry["timestamp_unit"] == "us" for entry in manifest["months"]) == 8
    for previous, current in zip(manifest["months"], manifest["months"][1:]):
        assert pd.Timestamp(current["first_open_utc"]) - pd.Timestamp(previous["last_open_utc"]) == pd.Timedelta(minutes=1)


@pytest.mark.parametrize("mutation", [
    "missing_hash", "invalid_hash", "wrong_unit", "wrong_rows", "bool_rows",
    "wrong_first", "wrong_last", "missing_month", "reordered", "too_many_attempts",
])
def test_manifest_builder_rejects_incomplete_or_inconsistent_entries(contract, entries, mutation):
    if mutation == "missing_hash":
        entries[0].pop("csv_sha256")
    elif mutation == "invalid_hash":
        entries[0]["parquet_sha256"] = "not-a-sha256"
    elif mutation == "wrong_unit":
        entries[59]["timestamp_unit"] = "us"
    elif mutation == "wrong_rows":
        entries[0]["rows"] -= 1
    elif mutation == "bool_rows":
        entries[0]["rows"] = True
    elif mutation == "wrong_first":
        entries[1]["first_open_utc"] = "2020-02-01T00:01:00+00:00"
    elif mutation == "wrong_last":
        entries[0]["last_open_utc"] = "2020-01-31T23:58:00+00:00"
    elif mutation == "missing_month":
        entries.pop()
    elif mutation == "reordered":
        entries[0], entries[1] = entries[1], entries[0]
    else:
        entries[0]["attempts"] = 4
    with pytest.raises(data.DataValidationError):
        monthly.build_historical_manifest(entries, contract, protocol_commit="1" * 40)


def test_manifest_content_hash_is_checked_independently(contract, entries):
    manifest = monthly.build_historical_manifest(entries, contract, protocol_commit="1" * 40)
    changed = copy.deepcopy(manifest)
    changed["months"][0]["csv_sha256"] = "e" * 64
    with pytest.raises(data.DataValidationError):
        monthly.validate_historical_manifest(changed, contract)
    rebuilt = monthly.build_historical_manifest(entries, contract, protocol_commit="1" * 40)
    assert json.dumps(manifest, sort_keys=True) == json.dumps(rebuilt, sort_keys=True)
