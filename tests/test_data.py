"""Offline tests for schema, UTC precision, integrity and rejection behavior."""

import io
import json
import zipfile
from datetime import date, datetime, timezone

import pandas as pd
import pytest

import quantforge.data as data_module
from quantforge.data import (
    DataValidationError, archive_name, extract_csv, parse_klines,
    repository_path, sha256, verify_checksum,
)


def synthetic_rows(day: date, unit: str) -> list[list[str]]:
    # These deterministic artificial rows are parser fixtures, never market data.
    multiplier = 1_000_000 if unit == "us" else 1_000
    start = int(datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).timestamp()) * multiplier
    step = 60 * multiplier
    return [
        [str(start + i * step), "100.0", "102.0", "99.0", "101.0", "10.0",
         str(start + (i + 1) * step - 1), "1005.0", "20", "4.0", "402.0", "0"]
        for i in range(1440)
    ]


def csv_bytes(rows: list[list[str]]) -> bytes:
    return ("\n".join(",".join(row) for row in rows) + "\n").encode()


@pytest.mark.parametrize("day,unit", [(date(2024, 12, 31), "ms"), (date(2026, 9, 1), "us")])
def test_full_day_utc_and_parquet_roundtrip(day, unit, tmp_path):
    frame, report = parse_klines(csv_bytes(synthetic_rows(day, unit)), day)
    assert report["rows"] == 1440
    assert report["timestamp_unit_raw"] == unit
    assert frame.open_time.iloc[0] == pd.Timestamp(day, tz="UTC")
    assert frame.open_time.iloc[-1] == pd.Timestamp(day, tz="UTC") + pd.Timedelta(hours=23, minutes=59)
    assert frame.close_time.iloc[-1] == pd.Timestamp(day, tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(1, unit=unit)
    target = tmp_path / "sample.parquet"
    frame.to_parquet(target, index=False)
    pd.testing.assert_frame_equal(frame, pd.read_parquet(target), check_exact=True)


@pytest.mark.parametrize("failure", ["missing", "duplicate", "ohlc", "infinite", "negative_volume", "bad_close", "mixed_unit", "column", "order", "noninteger_trades", "wrong_day"])
def test_reject_invalid_daily_data(failure):
    day = date(2026, 9, 1)
    rows = synthetic_rows(day, "us")
    if failure == "missing":
        rows.pop(100)
    elif failure == "duplicate":
        rows[100] = rows[99].copy()
    elif failure == "ohlc":
        rows[100][2] = "90"
    elif failure == "infinite":
        rows[100][4] = "inf"
    elif failure == "negative_volume":
        rows[100][5] = "-1"
    elif failure == "bad_close":
        rows[100][6] = str(int(rows[100][6]) - 1)
    elif failure == "mixed_unit":
        rows[100][0] = str(int(rows[100][0]) // 1000)
    elif failure == "column":
        rows = [row[:-1] for row in rows]
    elif failure == "order":
        rows[99], rows[100] = rows[100], rows[99]
    elif failure == "noninteger_trades":
        rows[100][8] = "1.5"
    elif failure == "wrong_day":
        day = date(2026, 9, 2)
    with pytest.raises(DataValidationError):
        parse_klines(csv_bytes(rows), day)


def test_spot_unit_transition_is_enforced():
    day = date(2026, 9, 1)
    with pytest.raises(DataValidationError, match="Expected us"):
        parse_klines(csv_bytes(synthetic_rows(day, "ms")), day)


def test_checksum_corruption_and_wrong_filename():
    content = b"example bytes, not real market data"
    filename = archive_name(date(2026, 9, 1))
    checksum = f"{sha256(content)}  {filename}"
    assert verify_checksum(content, checksum, filename) == sha256(content)
    with pytest.raises(DataValidationError, match="SHA256"):
        verify_checksum(content + b"corrupt", checksum, filename)
    with pytest.raises(DataValidationError, match="filename"):
        verify_checksum(content, checksum, "wrong.zip")


def test_archive_does_not_extract_unexpected_members():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        zipped.writestr("../outside.csv", "bad")
    with pytest.raises(DataValidationError, match="expected CSV"):
        extract_csv(buffer.getvalue(), "sample.zip")


def test_repository_path_rejects_parent_escape():
    with pytest.raises(ValueError, match="inside"):
        repository_path("../outside.txt")


def fake_archive(day):
    buffer = io.BytesIO()
    name = archive_name(day)
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr(name.replace(".zip", ".csv"), csv_bytes(synthetic_rows(day, "us")))
    return buffer.getvalue()


def test_download_pipeline_rechecks_checksum_and_reuses_cache(monkeypatch, tmp_path):
    day = date(2026, 9, 1)
    archive = fake_archive(day)
    filename = archive_name(day)
    calls = []
    retrieved_at = "2026-11-02T00:00:00+00:00"

    def fake_fetch(url, max_bytes):
        calls.append(url)
        content = f"{sha256(archive)}  {filename}".encode() if url.endswith(".CHECKSUM") else archive
        return content, {"url": url, "http_status": 200, "retrieved_at_utc": retrieved_at, "bytes": len(content)}

    monkeypatch.setattr(data_module, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(data_module, "fetch", fake_fetch)
    first = data_module.download_day(day)
    assert first["validation"]["rows"] == 1440
    assert first["parquet_roundtrip_equal"] is True
    assert first["archive"]["cache_reused"] is False
    assert (tmp_path / first["parquet_path"]).exists()
    second = data_module.download_day(day)
    assert len(calls) == 3
    assert calls[-1].endswith(".CHECKSUM")
    assert second["archive"]["cache_reused"] is True
    assert second["archive"]["http_status"] == 200
    assert second["retrieved_at_utc"] == retrieved_at
    assert second["archive_sha256"] == first["archive_sha256"]
    assert second["parquet_sha256"] == first["parquet_sha256"]


def test_checksum_failure_does_not_write_unverified_files(monkeypatch, tmp_path):
    day = date(2026, 9, 1)
    archive = fake_archive(day)
    filename = archive_name(day)

    def fake_fetch(url, max_bytes):
        content = f"{'0' * 64}  {filename}".encode() if url.endswith(".CHECKSUM") else archive
        return content, {"url": url, "http_status": 200}

    monkeypatch.setattr(data_module, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(data_module, "fetch", fake_fetch)
    with pytest.raises(DataValidationError, match="SHA256"):
        data_module.download_day(day)
    assert not list(tmp_path.rglob("*.zip"))
    assert not list(tmp_path.rglob("*.parquet"))
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("failure", ["oversize", "wrong_source"])
def test_cached_archive_resource_and_provenance_guards(monkeypatch, tmp_path, failure):
    day = date(2026, 9, 1)
    filename = archive_name(day)
    archive = fake_archive(day)
    raw_dir = tmp_path / "data/raw/binance/spot/BTCUSDT/1m"
    raw_dir.mkdir(parents=True)
    raw_path = raw_dir / filename
    if failure == "oversize":
        with raw_path.open("wb") as output:
            output.truncate(8 * 1024 * 1024 + 1)
    else:
        raw_path.write_bytes(archive)
        (raw_dir / (filename + ".source.json")).write_text(
            json.dumps({"url": "https://example.com/wrong.zip", "bytes": len(archive)}), encoding="utf-8"
        )

    def fake_fetch(url, max_bytes):
        assert url.endswith(".CHECKSUM")
        return f"{sha256(archive)}  {filename}".encode(), {"url": url}

    monkeypatch.setattr(data_module, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(data_module, "fetch", fake_fetch)
    with pytest.raises(DataValidationError, match="Cached"):
        data_module.download_day(day)
    assert not list(tmp_path.rglob("*.parquet"))
