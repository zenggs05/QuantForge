"""Synthetic monthly calendar, integrity, quota and interrupted-transfer tests."""

from functools import lru_cache
import http.client
import io
import json
from pathlib import Path
import zipfile

import pandas as pd
import pytest

from quantforge import data
from quantforge import data_monthly as monthly


@lru_cache(maxsize=8)
def synthetic_csv(month):
    start = pd.Timestamp(f"{month}-01", tz="UTC")
    end = start + pd.DateOffset(months=1)
    unit = "us" if start.year >= 2025 else "ms"
    scale = 1000 if unit == "us" else 1_000_000
    first = start.value // scale
    step = 60_000_000 if unit == "us" else 60_000
    count = int((end - start) / pd.Timedelta(minutes=1))
    return ("\n".join(
        f"{first + i * step},100,102,99,101,10,{first + (i + 1) * step - 1},1005,20,4,402,0"
        for i in range(count)) + "\n").encode()


def archive_for(month="2025-09", content=None, member=None, compression=zipfile.ZIP_DEFLATED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        archive.writestr(member or f"BTCUSDT-1m-{month}.csv", synthetic_csv(month) if content is None else content)
    return output.getvalue()


@pytest.mark.parametrize("month,days,unit", [("2026-02", 28, "us"), ("2024-02", 29, "ms"), ("2025-09", 30, "us"), ("2025-12", 31, "us")])
def test_calendar_months_and_exact_parquet_roundtrip(month, days, unit, tmp_path):
    frame, report = monthly.parse_monthly_klines(synthetic_csv(month), month)
    assert report["rows"] == days * 1440
    assert report["timestamp_unit_raw"] == unit
    assert frame.open_time.iloc[0] == pd.Timestamp(f"{month}-01", tz="UTC")
    assert frame.close_time.iloc[-1] == frame.open_time.iloc[0] + pd.DateOffset(months=1) - pd.Timedelta(1, unit=unit)
    path = tmp_path / "sample.parquet"
    frame.to_parquet(path, index=False)
    pd.testing.assert_frame_equal(frame, pd.read_parquet(path), check_exact=True)


def test_calendar_year_boundary_has_exact_one_minute_gap():
    december, _ = monthly.parse_monthly_klines(synthetic_csv("2025-12"), "2025-12")
    january, _ = monthly.parse_monthly_klines(synthetic_csv("2026-01"), "2026-01")
    assert january.open_time.iloc[0] - december.open_time.iloc[-1] == pd.Timedelta(minutes=1)


@pytest.mark.parametrize("failure", ["missing", "duplicate", "order", "month_outside", "columns", "ohlc", "nan", "negative_volume", "trade_decimal", "close_precision", "mixed_unit", "header"])
def test_invalid_monthly_contract_is_rejected(failure):
    rows = synthetic_csv("2025-09").decode().splitlines()
    if failure == "missing":
        rows.pop(400)
    elif failure == "duplicate":
        rows[400] = rows[399]
    elif failure == "order":
        rows[400], rows[401] = rows[401], rows[400]
    elif failure == "header":
        rows.insert(0, ",".join(data.COLUMNS))
    else:
        row = rows[400].split(",")
        if failure == "month_outside":
            row[0] = "1759276800000000"
        elif failure == "columns":
            row.append("extra")
        elif failure == "ohlc":
            row[2] = "95"
        elif failure == "nan":
            row[4] = "NaN"
        elif failure == "negative_volume":
            row[5] = "-1"
        elif failure == "trade_decimal":
            row[8] = "1.2"
        elif failure == "close_precision":
            row[6] = str(int(row[6]) - 1)
        elif failure == "mixed_unit":
            row[0] = str(int(row[0]) // 1000)
        rows[400] = ",".join(row)
    with pytest.raises(data.DataValidationError):
        monthly.parse_monthly_klines(("\n".join(rows) + "\n").encode(), "2025-09")


@pytest.mark.parametrize("month", ["2026-07", "2026-08"])
def test_holdout_rejected_before_parsing_or_network(month, monkeypatch):
    monkeypatch.setattr(monthly, "_open_response", lambda request: pytest.fail("Holdout request issued"))
    monkeypatch.setattr(monthly.pd, "read_csv", lambda *args, **kwargs: pytest.fail("Holdout parsed"))
    with pytest.raises(data.DataValidationError, match="holdout"):
        monthly.parse_monthly_klines(b"never inspect me", month)
    with pytest.raises(data.DataValidationError, match="holdout"):
        monthly.download_month(month)
    with pytest.raises(data.DataValidationError, match="holdout"):
        monthly.verify_cached_month(month)


def test_zip_structure_path_crc_and_decompression_guards(monkeypatch):
    with pytest.raises(data.DataValidationError, match="expected CSV"):
        monthly.extract_monthly_csv(archive_for(content=b"x", member="../outside.csv"), "2025-09")
    content = bytearray(archive_for(content=b"abcdefg", compression=zipfile.ZIP_STORED))
    location = bytes(content).find(b"abcdefg")
    content[location] ^= 1
    with pytest.raises(data.DataValidationError, match="CRC"):
        monthly.extract_monthly_csv(bytes(content), "2025-09")
    monkeypatch.setattr(monthly, "CSV_LIMIT", 5)
    with pytest.raises(data.DataValidationError, match="oversized"):
        monthly.extract_monthly_csv(archive_for(content=b"123456"), "2025-09")


class FakeResponse:
    def __init__(self, url, body, *, fail_after=None, partial=False):
        self.url, self.body, self.fail_after, self.partial = url, body, fail_after, partial
        self.status, self.position = 200, 0
        self.headers = {"Content-Length": str(len(body)), "Last-Modified": "Synthetic fixture only"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read1(self, count):
        if self.fail_after is not None and self.position >= self.fail_after:
            if self.partial:
                received = self.body[self.position:self.position + min(count, 13)]
                raise http.client.IncompleteRead(received, len(self.body) - self.position - len(received))
            raise TimeoutError("Synthetic interrupted transfer")
        chunk = self.body[self.position:self.position + count]
        self.position += len(chunk)
        return chunk


def install_fake_download(monkeypatch, tmp_path, *, bad_checksum=False, fail_once=False):
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(monthly.time, "sleep", lambda seconds: None)
    archive = archive_for()
    checksum = f"{('0' * 64) if bad_checksum else data.sha256(archive)}  BTCUSDT-1m-2025-09.zip\n".encode()
    requests = []
    failed = False

    def opener(request):
        nonlocal failed
        requests.append(request.full_url)
        if request.full_url.endswith(".CHECKSUM"):
            return FakeResponse(request.full_url, checksum)
        assert request.full_url.endswith("BTCUSDT-1m-2025-09.zip")
        if fail_once and not failed:
            failed = True
            return FakeResponse(request.full_url, archive, fail_after=monthly.READ_CHUNK)
        return FakeResponse(request.full_url, archive)

    monkeypatch.setattr(monthly, "_open_response", opener)
    return archive, checksum, requests


def test_complete_download_cache_revalidation_and_persistent_ledger(monkeypatch, tmp_path):
    archive, checksum, requests = install_fake_download(monkeypatch, tmp_path)
    first = monthly.download_month()
    assert first["status"] == "passed"
    assert first["network_phase_totals"]["zip_body_bytes"] == len(archive)
    assert first["network_phase_totals"]["checksum_body_bytes"] == len(checksum)
    frame, stored = monthly.verify_cached_month("2025-09")
    assert len(frame) == 43200 and stored == first
    second = monthly.download_month()
    assert len(requests) == 3
    assert second["archive"]["cache_reused"]
    assert second["network_this_invocation"]["zip_body_bytes"] == 0
    assert second["network_phase_totals"]["checksum_body_bytes"] == 2 * len(checksum)
    assert second["archive_sha256"] == first["archive_sha256"]
    ledger = json.loads((tmp_path / "results/monthly-download/network-ledger.json").read_text())
    assert ledger["zip_body_bytes"] == len(archive)
    assert not list(tmp_path.rglob("failure.json"))


def test_bad_checksum_writes_failure_and_no_success(monkeypatch, tmp_path):
    install_fake_download(monkeypatch, tmp_path, bad_checksum=True)
    with pytest.raises(data.DataValidationError, match="SHA256"):
        monthly.download_month()
    paths = monthly.monthly_paths("2025-09")
    assert not paths["manifest"].exists()
    assert not paths["parquet"].exists()
    assert not paths["raw"].exists()
    assert len(list(tmp_path.rglob("failure.json"))) == 1


def test_retry_counts_partial_transfer_and_resume_does_not_redownload(monkeypatch, tmp_path):
    archive, _, requests = install_fake_download(monkeypatch, tmp_path, fail_once=True)
    result = monthly.download_month()
    assert result["network_phase_totals"]["zip_body_bytes"] == len(archive) + monthly.READ_CHUNK
    # The retry's final reservation is the remaining bytes, not a full chunk.
    assert result["network_phase_totals"]["zip_reserved_bytes"] == len(archive) % monthly.READ_CHUNK
    assert len(requests) == 3
    result = monthly.download_month()
    assert len(requests) == 4
    assert result["network_this_invocation"]["zip_body_bytes"] == 0


def test_cumulative_budget_prevents_request_body_and_persists(monkeypatch, tmp_path):
    archive, _, requests = install_fake_download(monkeypatch, tmp_path)
    monkeypatch.setattr(monthly, "TOTAL_ZIP_LIMIT", len(archive) - 1)
    with pytest.raises(monthly.DownloadBudgetError, match="remaining"):
        monthly.download_month()
    ledger = monthly.NetworkLedger()
    assert ledger.state["zip_body_bytes"] == 0
    assert ledger.remaining == len(archive) - 1
    assert not monthly.monthly_paths("2025-09")["manifest"].exists()


def test_reserved_budget_survives_reopening(monkeypatch, tmp_path):
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    ledger = monthly.NetworkLedger()
    attempt = ledger.start(f"{monthly.BASE_URL}/BTCUSDT-1m-2025-09.zip", "zip")
    ledger.reserve("zip", monthly.TOTAL_ZIP_LIMIT, attempt)
    resumed = monthly.NetworkLedger()
    assert resumed.remaining == 0
    with pytest.raises(monthly.DownloadBudgetError, match="exhausted"):
        monthly._fetch(attempt["url"], "zip", resumed)


def test_first_month_gate_blocks_other_month_network(monkeypatch, tmp_path):
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(monthly, "_open_response", lambda request: pytest.fail("First-month gate bypassed"))
    with pytest.raises(FileNotFoundError):
        monthly.download_month("2025-10")
    assert len(list(tmp_path.rglob("failure.json"))) == 1


def test_old_success_does_not_mask_a_failed_revalidation(monkeypatch, tmp_path):
    install_fake_download(monkeypatch, tmp_path)
    monthly.download_month()
    paths = monthly.monthly_paths("2025-09")
    original = paths["raw"].read_bytes()
    paths["raw"].write_bytes(original + b"corruption")
    with pytest.raises(data.DataValidationError):
        monthly.download_month()
    assert paths["manifest"].exists()  # Preserve evidence; latest failure denies reuse.
    with pytest.raises(data.DataValidationError, match="Latest"):
        monthly.verify_cached_month("2025-09")
    assert len(list(tmp_path.rglob("failure.json"))) == 1


def test_incomplete_read_partial_bytes_are_counted_exactly(monkeypatch, tmp_path):
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(monthly.time, "sleep", lambda seconds: None)
    content = b"x" * (monthly.READ_CHUNK + 100)
    url = f"{monthly.BASE_URL}/BTCUSDT-1m-2025-09.zip"
    responses = iter([FakeResponse(url, content, fail_after=monthly.READ_CHUNK, partial=True), FakeResponse(url, content)])
    monkeypatch.setattr(monthly, "_open_response", lambda request: next(responses))
    ledger = monthly.NetworkLedger()
    actual, _ = monthly._fetch(url, "zip", ledger)
    assert actual == content
    assert ledger.state["zip_body_bytes"] == len(content) + monthly.READ_CHUNK + 13
    assert ledger.state["zip_reserved_bytes"] == 0


def test_failure_before_manifest_commit_cannot_leave_success(monkeypatch, tmp_path):
    install_fake_download(monkeypatch, tmp_path)
    manifest_path = monthly.monthly_paths("2025-09")["manifest"]
    original = monthly._atomic_json

    def fail_manifest(path, payload):
        if path == manifest_path:
            raise OSError("Synthetic manifest persistence failure")
        return original(path, payload)

    monkeypatch.setattr(monthly, "_atomic_json", fail_manifest)
    with pytest.raises(OSError, match="persistence"):
        monthly.download_month()
    assert not manifest_path.exists()
    assert json.loads(monthly.latest_verification_path("2025-09").read_text())["status"] == "failed"
    assert len(list(tmp_path.rglob("failure.json"))) == 1
    monkeypatch.setattr(monthly, "_atomic_json", original)
    resumed = monthly.download_month()
    assert resumed["status"] == "passed" and resumed["archive"]["cache_reused"]
    monthly.verify_cached_month("2025-09")


def test_project_internal_symlink_alias_is_rejected_before_read(monkeypatch, tmp_path):
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    raw = tmp_path / "data/raw/binance/spot/BTCUSDT/1m/monthly/BTCUSDT-1m-2025-09.zip"
    original = Path.resolve

    def redirected(path, *args, **kwargs):
        if path == raw:
            return tmp_path / "forbidden-holdout.zip"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", redirected)
    with pytest.raises(ValueError, match="symlinks"):
        monthly.monthly_paths("2025-09")


def test_manifest_path_and_source_checked_before_archive_read(monkeypatch, tmp_path):
    install_fake_download(monkeypatch, tmp_path)
    monthly.download_month()
    paths = monthly.monthly_paths("2025-09")
    manifest = json.loads(paths["manifest"].read_text())
    manifest["raw_path"] = "data/raw/binance/spot/BTCUSDT/1m/monthly/BTCUSDT-1m-2026-07.zip"
    paths["manifest"].write_text(json.dumps(manifest))
    original = monthly._limited_read

    def guard(path, limit):
        if path.suffix == ".zip":
            pytest.fail("Archive opened before manifest path validation")
        return original(path, limit)

    monkeypatch.setattr(monthly, "_limited_read", guard)
    with pytest.raises(ValueError, match="fixed development path"):
        monthly.verify_cached_month("2025-09")
