"""Synthetic D-033 carry-in, checksum gate, and fresh-attempt accounting."""

from __future__ import annotations

import copy
import hashlib
import socket
import urllib.request

import pandas as pd
import pytest

from quantforge import data, data_monthly as monthly, historical_recovery as recovery, research_policy
from gate1_fixtures import FakeResponse, copy_frozen_contract, synthetic_archive


IMPLEMENTATION_SHA = "a" * 40
PRIOR_FAILED_SHA = "e" * 64


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("D-033 synthetic tests must not make network requests")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture
def contract(tmp_path, monkeypatch):
    copy_frozen_contract(data.REPOSITORY_ROOT, tmp_path)
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    return monthly.load_historical_download_contract()


def _raw_bytes(month: str, size: int) -> bytes:
    """Generate deterministic payload bytes, used only by prefix hash audits."""
    label = ("synthetic retained payload " + month).encode("ascii")
    return label + b"\0" * (size - len(label))


def _checksum(month: str, raw: bytes) -> bytes:
    return f"{hashlib.sha256(raw).hexdigest()} BTCUSDT-1m-{month}.zip\n".encode("ascii")


def _entry(month: str, size: int, *, archive: bytes | None = None) -> dict:
    """Construct audited metadata without parsing any historical observations."""
    start, end = research_policy.historical_month_bounds(month)
    expected = int((end - start) / pd.Timedelta(minutes=1))
    missing = 414 if month == "2020-02" else 0
    unit = "us" if month >= "2025-01" else "ms"
    scale = 1_000 if unit == "us" else 1_000_000
    raw = _raw_bytes(month, size) if archive is None else archive
    checksum = _checksum(month, raw)
    parquet = ("synthetic retained parquet " + month).encode("ascii")
    notes = ["refetch after prior cleanup; gap retained from archive"] if missing else ["none"]
    gaps = [{"start_utc": (start + pd.Timedelta(minutes=100)).isoformat(),
             "end_utc": (start + pd.Timedelta(minutes=513)).isoformat(), "minutes": 414}] if missing else []
    audit = {
        **monthly.classify_historical_integrity(month, expected, expected - missing, int(bool(missing))),
        "rows": expected - missing, "expected_rows": expected, "raw_rows_read": expected - missing,
        "timestamp_unit": unit, "timestamp_unit_raw": unit,
        "first_open_raw": str(start.value // scale),
        "last_open_raw": str((end - pd.Timedelta(minutes=1)).value // scale),
        "first_open_utc": start.isoformat(), "last_open_utc": (end - pd.Timedelta(minutes=1)).isoformat(),
        "leading_missing_minutes": 0, "trailing_missing_minutes": 0,
        "gap_ranges": gaps, "notes": notes,
        "zip_sha256": hashlib.sha256(raw).hexdigest(), "checksum_text_sha256": hashlib.sha256(checksum).hexdigest(),
        "csv_sha256": hashlib.sha256(("synthetic csv " + month).encode()).hexdigest(),
        "checksum_bytes": len(checksum), "csv_bytes": 5_000_000,
    }
    entry = {
        **audit, "audit": copy.deepcopy(audit), "notes": "; ".join(notes),
        "status": "passed", "url": monthly._historical_url(month),
        "checksum_url": monthly._historical_url(month, ".zip.CHECKSUM"),
        "parquet_sha256": hashlib.sha256(parquet).hexdigest(), "parquet_bytes": len(parquet),
        "zip_bytes": len(raw), "bytes_downloaded": len(raw), "checksum_bytes_downloaded": len(checksum),
        "zip_reserved_bytes": 0, "checksum_reserved_bytes": 0,
        "attempts": 1, "http_gets": 2, "expected_unit": unit, "detected_unit": unit,
        "fetched_at_utc": "2026-11-06T00:00:00+00:00",
    }
    if missing:
        entry.update(historical_attempts=2, prior_failed_ledger_sha256=PRIOR_FAILED_SHA, history=[
            {"event": "failed_download_cleanup", "commit_sha": "1d6e1f2"},
            {"event": "D-032_refetch_success",
             "event_ledger_path": "results/phase3a-gate1/continuations/2020-02-refetch.json",
             "success_commit_sha": "1" * 40},
        ])
    return entry


def _receipt(entry, kind, number, *, scope=None, failed=False, size=None, error_type=None):
    row = {"month": entry["month"], "kind": kind, "month_attempt": number,
           "status": "received", "http_status": 200,
           "url": entry["checksum_url" if kind == "checksum" else "url"],
           "body_bytes": entry[f"{kind}_bytes"], "reserved_bytes": 0,
           "content_length": entry[f"{kind}_bytes"],
           "sha256": entry["checksum_text_sha256" if kind == "checksum" else "zip_sha256"]}
    if scope is not None:
        row["scope"] = scope
    if failed:
        row.update(status="failed", body_bytes=size, error_type=error_type)
        row.pop("content_length")
        row.pop("sha256")
    return row


def _refresh_totals(ledger):
    for identifier, attempt in enumerate(ledger["attempts"]):
        attempt["id"] = identifier
    ledger["http_gets_count"] = len(ledger["attempts"])
    for kind in ("zip", "checksum"):
        for field in ("body_bytes", "reserved_bytes"):
            ledger[f"{kind}_{field}"] = sum(a[field] for a in ledger["attempts"] if a["kind"] == kind)
        ledger[f"{kind}_charged_bytes"] = ledger[f"{kind}_body_bytes"] + ledger[f"{kind}_reserved_bytes"]
    ledger["total_bytes_downloaded"] = ledger["zip_body_bytes"]
    ledger["checksum_bytes_downloaded"] = ledger["checksum_body_bytes"]


def _publish_incident(root, incident, monkeypatch):
    encoded = monthly._historical_json_bytes(incident)
    for relative in (recovery.D033_INCIDENT_PATH, recovery.D033_ARCHIVED_INCIDENT_PATH):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded)
    monkeypatch.setattr(recovery, "D033_INCIDENT_SHA256", hashlib.sha256(encoded).hexdigest())


@pytest.fixture
def recovery_fixture(contract, tmp_path, monkeypatch):
    """Synthetic 29-month prefix with the incident's exact accounting shape."""
    sizes = {month: 2_222_222 for month in contract["months"][:29]}
    sizes.update({"2020-01": 2_224_194, "2020-02": 2_077_466, "2022-05": 2_613_268})
    prefix = [_entry(month, sizes[month]) for month in contract["months"][:29]]
    january_retry = next(e for e in prefix if e["month"] == "2022-01")
    january_retry.update(attempts=2, http_gets=4, checksum_bytes_downloaded=176)
    june_archive = synthetic_archive("2022-06")
    june = _entry("2022-06", len(june_archive), archive=june_archive)
    june_checksum = _checksum("2022-06", june_archive)
    monkeypatch.setattr(recovery, "D033_JUNE_CHECKSUM_SHA256", hashlib.sha256(june_checksum).hexdigest())
    expected_february = dict(monthly._D032_FAILED_MONTH_EXPECTED)
    expected_february.update(checksum_sha256=prefix[1]["checksum_text_sha256"], zip_sha256=prefix[1]["zip_sha256"])
    monkeypatch.setattr(monthly, "_D032_FAILED_MONTH_EXPECTED", expected_february)
    ledger = monthly.HistoricalNetworkLedger(contract)._new_state()
    ledger.update(status="failed", failed_month="2022-06", months=copy.deepcopy(prefix),
                  stop_error={"type": "PermissionError", "message": "synthetic atomic ledger lock"},
                  failure={"error_type": "PermissionError", "diagnostics": {"month": "2022-05"}},
                  prior_failed_ledger_sha256=PRIOR_FAILED_SHA,
                  prior_failed_month_history={"month": "2020-02", "status": "failed"},
                  continuation_mode="D-032-single-month-refetch")
    for entry in prefix:
        numbers = (1, 2) if entry["month"] in {"2020-02", "2022-01"} else (1,)
        for number in numbers:
            for kind in ("checksum", "zip"):
                failed = entry["month"] == "2022-01" and number == 1 and kind == "zip"
                ledger["attempts"].append(_receipt(entry, kind, number, failed=failed, size=0, error_type="URLError"))
    ledger["attempts"].extend([
        _receipt(june, "checksum", 1),
        _receipt(june, "zip", 1, failed=True, size=1_601_170, error_type="PermissionError"),
    ])
    _refresh_totals(ledger)
    assert ledger["http_gets_count"] == 64 and ledger["zip_body_bytes"] == 68_371_336
    assert ledger["checksum_body_bytes"] == 2_816
    _publish_incident(tmp_path, ledger, monkeypatch)
    return {"contract": contract, "root": tmp_path, "incident": ledger,
            "prefix": prefix, "june_archive": june_archive, "june_checksum": june_checksum, "june": june}


def _materialize_prefix(fixture):
    """Write deterministic fake retained payloads for byte-only prefix validation."""
    for entry in fixture["prefix"]:
        paths = monthly.historical_month_paths(entry["month"], fixture["contract"])
        raw = _raw_bytes(entry["month"], entry["zip_bytes"])
        payloads = {"raw": raw, "checksum": _checksum(entry["month"], raw),
                    "parquet": ("synthetic retained parquet " + entry["month"]).encode("ascii"),
                    "source": monthly._historical_json_bytes(entry)}
        for key, payload in payloads.items():
            paths[key].parent.mkdir(parents=True, exist_ok=True)
            paths[key].write_bytes(payload)


def test_d033_carries_all_29_months_and_cumulative_costs_without_reset(recovery_fixture):
    fixture = recovery_fixture
    _materialize_prefix(fixture)
    incident_path = fixture["root"] / recovery.D033_INCIDENT_PATH
    original = incident_path.read_bytes()
    with recovery.HistoricalD033Ledger(fixture["contract"], IMPLEMENTATION_SHA) as ledger:
        assert ledger.state["months"] == fixture["prefix"]
        assert ledger.state["attempts"] == fixture["incident"]["attempts"]
        assert ledger.state["http_gets_count"] == 64
        assert ledger.state["zip_body_bytes"] == 68_371_336
        assert ledger.state["checksum_body_bytes"] == 2_816
        assert ledger.state["active_attempt_scope"] == "D-033"
        assert ledger.state["d033_recovery"]["incident"]["failure"]["diagnostics"]["month"] == "2022-05"
    assert incident_path.read_bytes() == original
    recovery_path = fixture["root"] / recovery.D033_LEDGER_PATH
    recovered = recovery_path.read_bytes()
    with pytest.raises(monthly.DownloadBudgetError, match="already exists"):
        with recovery.HistoricalD033Ledger(fixture["contract"], IMPLEMENTATION_SHA):
            pytest.fail("An existing D-033 ledger was reset")
    assert recovery_path.read_bytes() == recovered


@pytest.mark.parametrize("mutation", ["local_differs", "archived_bytes", "month_prefix", "carryin", "last_attempt", "get_count"])
def test_d033_prior_checkpoint_tampering_rejected_before_get(recovery_fixture, monkeypatch, mutation):
    fixture = recovery_fixture
    incident = copy.deepcopy(fixture["incident"])
    if mutation == "local_differs":
        path = fixture["root"] / recovery.D033_INCIDENT_PATH
        path.write_bytes(path.read_bytes() + b"\n")
    elif mutation == "archived_bytes":
        path = fixture["root"] / recovery.D033_ARCHIVED_INCIDENT_PATH
        path.write_bytes(path.read_bytes() + b"\n")
    else:
        if mutation == "month_prefix":
            incident["months"][0]["month"] = "2020-02"
        elif mutation == "carryin":
            incident["attempts"][1]["body_bytes"] -= 1
            _refresh_totals(incident)
        elif mutation == "last_attempt":
            incident["attempts"][-1]["error_type"] = "TimeoutError"
        else:
            incident["attempts"].pop()
            _refresh_totals(incident)
        _publish_incident(fixture["root"], incident, monkeypatch)
    monkeypatch.setattr(monthly, "_open_historical_response", lambda *_: pytest.fail("Invalid carry-in reached GET"))
    with pytest.raises(data.DataValidationError):
        with recovery.HistoricalD033Ledger(fixture["contract"], IMPLEMENTATION_SHA):
            pytest.fail("A malformed incident was accepted")
    assert not (fixture["root"] / recovery.D033_LEDGER_PATH).exists()


def test_d033_changed_june_checksum_stops_before_zip_get(recovery_fixture, monkeypatch):
    fixture = recovery_fixture
    _materialize_prefix(fixture)
    requested = []

    def opener(request):
        requested.append(request.full_url)
        assert request.full_url.endswith("2022-06.zip.CHECKSUM")
        return FakeResponse(request.full_url, fixture["june_checksum"][:-1] + b" ")

    monkeypatch.setattr(monthly, "_open_historical_response", opener)
    with recovery.HistoricalD033Ledger(fixture["contract"], IMPLEMENTATION_SHA) as ledger:
        with pytest.raises(data.DataValidationError, match="CHECKSUM SHA"):
            monthly.download_historical_graded_month("2022-06", fixture["contract"], ledger=ledger)
        assert ledger.state["http_gets_count"] == 65
    assert requested == [monthly._historical_url("2022-06", ".zip.CHECKSUM")]
    assert not any(p.exists() for p in monthly.historical_month_paths("2022-06", fixture["contract"]).values())


def test_fresh_june_scope_allows_attempt_one_without_erasing_incident(recovery_fixture):
    fixture = recovery_fixture
    _materialize_prefix(fixture)
    with recovery.HistoricalD033Ledger(fixture["contract"], IMPLEMENTATION_SHA) as ledger:
        attempt = ledger.start_get(monthly._historical_url("2022-06", ".zip.CHECKSUM"), "checksum", "2022-06", 1)
        assert attempt["id"] == 64 and attempt["scope"] == "D-033"
        assert ledger.state["attempts"][62]["month_attempt"] == attempt["month_attempt"] == 1
        assert "scope" not in ledger.state["attempts"][62]
        with pytest.raises(monthly.DownloadBudgetError, match="repeat"):
            ledger.start_get(monthly._historical_url("2022-06", ".zip.CHECKSUM"), "checksum", "2022-06", 1)
        assert ledger.state["http_gets_count"] == 65


def test_d033_june_publishes_only_new_attempt_costs_and_fresh_quality(recovery_fixture, monkeypatch):
    fixture = recovery_fixture
    _materialize_prefix(fixture)
    requested = []

    def opener(request):
        requested.append(request.full_url)
        payload = fixture["june_checksum"] if request.full_url.endswith(".CHECKSUM") else fixture["june_archive"]
        return FakeResponse(request.full_url, payload)

    monkeypatch.setattr(monthly, "_open_historical_response", opener)
    monkeypatch.setattr(monthly, "_historical_disk_gate", lambda _contract: None)
    with recovery.HistoricalD033Ledger(fixture["contract"], IMPLEMENTATION_SHA) as ledger:
        entry = monthly.download_historical_graded_month("2022-06", fixture["contract"], ledger=ledger)
        assert entry["http_gets"] == 2 and entry["attempts"] == 1
        assert entry["bytes_downloaded"] == len(fixture["june_archive"])
        assert entry["checksum_bytes_downloaded"] == len(fixture["june_checksum"])
        assert ledger.state["http_gets_count"] == 66
        assert ledger.state["zip_body_bytes"] == 68_371_336 + len(fixture["june_archive"])
        assert ledger.state["checksum_body_bytes"] == 2_816 + len(fixture["june_checksum"])
        assert ledger.state["attempts"][:64] == fixture["incident"]["attempts"]
        recovered = ledger.state["d033_recovery"]
        assert recovered["resume_checksum_sha_matches_archived_incident"] is True
        assert recovered["resume_tier"] == "normal"
        assert recovered["resume_missing_minutes"] == 0
        assert recovered["resume_anomalous_close_time_rows"] == 0
        assert recovered["resume_zip_sha256"] == hashlib.sha256(fixture["june_archive"]).hexdigest()
        assert recovered["resume_checksum_sha256"] == hashlib.sha256(fixture["june_checksum"]).hexdigest()
        assert recovered["incident"]["failure"]["diagnostics"]["month"] == "2022-05"
    assert requested == [monthly._historical_url("2022-06", ".zip.CHECKSUM"), monthly._historical_url("2022-06")]
    assert all(path.exists() for path in monthly.historical_month_paths("2022-06", fixture["contract"]).values())


def _completed_metadata(fixture):
    ledger = copy.deepcopy(fixture["incident"])
    entries = copy.deepcopy(fixture["prefix"])
    for month in fixture["contract"]["months"][29:]:
        entry = copy.deepcopy(fixture["june"]) if month == "2022-06" else _entry(month, 100)
        entries.append(entry)
        ledger["attempts"].extend(_receipt(entry, kind, 1, scope="D-033") for kind in ("checksum", "zip"))
    ledger.update(status="passed", active_attempt_scope="D-033", months=copy.deepcopy(entries), d033_recovery={
        "incident_commit": "e0caaaf", "incident_ledger_sha256": recovery.D033_INCIDENT_SHA256,
        "incident_archive_path": recovery.D033_ARCHIVED_INCIDENT_PATH,
        "incident_cumulative_gets_before_resume": 64, "incident_partial_bytes_charged": 1_601_170,
        "incident_checksum_bytes_charged": 88, "incident_zip_body_bytes": 68_371_336,
        "incident_checksum_body_bytes": 2816, "resume_implementation_commit": IMPLEMENTATION_SHA,
        "resume_checksum_sha256_expected": recovery.D033_JUNE_CHECKSUM_SHA256,
        "resume_checksum_sha_matches_archived_incident": True,
        "resume_tier": entries[29]["status_tier"],
        "resume_missing_minutes": entries[29]["missing_minutes"],
        "resume_anomalous_close_time_rows": entries[29]["anomalous_close_time_rows"],
        "resume_zip_sha256": entries[29]["zip_sha256"],
        "resume_checksum_sha256": entries[29]["checksum_text_sha256"],
    })
    _refresh_totals(ledger)
    return entries, ledger


def test_d033_manifest_counts_original_february_and_partial_june_costs(recovery_fixture):
    entries, ledger = _completed_metadata(recovery_fixture)
    result = recovery.validate_d033_manifest_ledger(ledger, entries, recovery_fixture["contract"])
    assert result["prior_failed_payload_bytes"] == {"zip": 2_077_466 + 1_601_170, "checksum": 176}
    assert result["prior_failed_events"]["2022-06"]["zip_body_bytes"] == 1_601_170
    assert result["cumulative_body_bytes"]["zip"] == sum(e["bytes_downloaded"] for e in entries) + 2_077_466 + 1_601_170
    assert result["cumulative_http_gets"] == 142


@pytest.mark.parametrize("mutation", ["old_attempt", "old_month", "new_scope", "june_checksum", "drop_june_cost"])
def test_d033_completed_manifest_rejects_history_and_scope_tampering(recovery_fixture, mutation):
    entries, ledger = _completed_metadata(recovery_fixture)
    if mutation == "old_attempt":
        ledger["attempts"][1]["sha256"] = "0" * 64
    elif mutation == "old_month":
        entries[0]["csv_sha256"] = "0" * 64
        ledger["months"] = copy.deepcopy(entries)
    elif mutation == "new_scope":
        ledger["attempts"][64]["scope"] = "D-032"
    elif mutation == "june_checksum":
        ledger["attempts"][64]["sha256"] = "0" * 64
    else:
        del ledger["attempts"][62:64]
        _refresh_totals(ledger)
    with pytest.raises(data.DataValidationError):
        recovery.validate_d033_manifest_ledger(ledger, entries, recovery_fixture["contract"])
