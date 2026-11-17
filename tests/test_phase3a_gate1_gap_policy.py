"""D-031 integrity grading uses synthetic calendar rows only."""

from __future__ import annotations

import copy
import io
import socket
import urllib.request
import zipfile

import pandas as pd
import pytest

from quantforge import data, data_monthly as monthly, research_policy
from gate1_fixtures import copy_frozen_contract, synthetic_month_csv


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("D-031 synthetic policy tests attempted network access")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture
def contract(tmp_path, monkeypatch):
    copy_frozen_contract(data.REPOSITORY_ROOT, tmp_path)
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    return monthly.load_historical_download_contract()


@pytest.mark.parametrize("missing,tier", [
    (0, "normal"), (100, "normal"), (101, "soft_warning"),
    (500, "soft_warning"),
])
def test_warmup_month_missing_ratio_inclusive_boundaries(missing, tier):
    decision = monthly.classify_historical_integrity("2020-02", 10_000, 10_000 - missing, 0)
    assert decision["missing_minutes"] == missing
    assert decision["missing_ratio"] == missing / 10_000
    assert decision["status_tier"] == tier
    assert decision["role"] == "warmup"


def test_warmup_more_than_five_percent_is_hard_failure():
    with pytest.raises(data.DataValidationError, match="5%|hard|missing"):
        monthly.classify_historical_integrity("2020-02", 10_000, 9_499, 0)


@pytest.mark.parametrize("missing,tier", [
    (100, "normal"), (101, "soft_warning"), (300, "soft_warning"),
])
def test_evaluation_month_three_percent_limit(missing, tier):
    decision = monthly.classify_historical_integrity("2021-01", 10_000, 10_000 - missing, 0)
    assert decision["status_tier"] == tier
    assert decision["role"] == "evaluate"


def test_evaluation_more_than_three_percent_hard_fails():
    with pytest.raises(data.DataValidationError, match="3%|hard|evaluation"):
        monthly.classify_historical_integrity("2021-01", 10_000, 9_699, 0)


@pytest.mark.parametrize("close_rows,tier", [
    (10, "normal"), (11, "soft_warning"), (100, "soft_warning"),
])
def test_close_duration_anomaly_limit_is_inclusive(close_rows, tier):
    decision = monthly.classify_historical_integrity("2020-02", 10_000, 10_000, close_rows)
    assert decision["status_tier"] == tier
    assert decision["anomalous_close_time_rows"] == close_rows


def test_close_duration_over_one_hundred_hard_fails():
    with pytest.raises(data.DataValidationError, match="100|hard|close"):
        monthly.classify_historical_integrity("2020-02", 10_000, 10_000, 101)


@pytest.mark.parametrize("month", ["2026-07", "2026-08", "2026-09", "2026-10"])
def test_grading_never_expands_month_allowlist(month):
    with pytest.raises(research_policy.ResearchPolicyError, match="historical allowlist"):
        monthly.classify_historical_integrity(month, 10_000, 10_000, 0)


def _sparse_february(*, missing: int = 414, bad_close: int = 1) -> bytes:
    lines = synthetic_month_csv("2020-02").splitlines()
    del lines[100:100 + missing]
    for number in range(bad_close):
        fields = lines[1000 + number].split(b",")
        fields[6] = str(int(fields[6]) + 60_000).encode()
        lines[1000 + number] = b",".join(fields)
    return b"\n".join(lines) + b"\n"


def test_graded_parser_keeps_all_original_rows_gaps_and_bad_close_bytes(contract):
    raw = _sparse_february()
    frame, audit = monthly.parse_historical_graded_klines(raw, "2020-02")
    assert len(frame) == audit["actual_minutes"] == 41_346
    assert audit["expected_minutes"] == 41_760
    assert audit["missing_minutes"] == 414
    assert audit["anomalous_close_time_rows"] == 1
    assert audit["status_tier"] == "normal"
    assert audit["role"] == "warmup"
    assert audit["notes"]
    assert sum(gap["minutes"] for gap in audit["gap_ranges"]) == 414
    assert pd.DatetimeIndex(frame.open_time).is_monotonic_increasing
    assert frame.close_time_raw.iloc[1000] - frame.open_time_raw.iloc[1000] != 59_999
    assert frame.open_time.iloc[100] - frame.open_time.iloc[99] == pd.Timedelta(minutes=415)


@pytest.mark.parametrize("defect", ["duplicate", "reversed"])
def test_graded_parser_rejects_duplicate_or_unsorted_open_time(contract, defect):
    lines = synthetic_month_csv("2020-02").splitlines()
    if defect == "duplicate":
        lines[100] = lines[99]
    else:
        lines[99], lines[100] = lines[100], lines[99]
    with pytest.raises(data.DataValidationError, match="duplicate|order|ascending|validation"):
        monthly.parse_historical_graded_klines(b"\n".join(lines) + b"\n", "2020-02")


def test_graded_archive_rejects_wrong_official_checksum_before_parse(contract, monkeypatch):
    raw = _sparse_february()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("BTCUSDT-1m-2020-02.csv", raw)
    monkeypatch.setattr(monthly, "parse_historical_graded_klines", lambda *_: pytest.fail("bad SHA reached CSV parser"))
    checksum = ("0" * 64 + "  BTCUSDT-1m-2020-02.zip\n").encode()
    with pytest.raises(data.DataValidationError, match="SHA256"):
        monthly.inspect_historical_graded_archive(stream.getvalue(), checksum, "2020-02", contract)


def test_edge_gaps_are_visible_and_boundary_span_uses_both_edges(contract):
    january = synthetic_month_csv("2020-01").splitlines()[:-2]
    february = synthetic_month_csv("2020-02").splitlines()[3:]
    _, jan_audit = monthly.parse_historical_graded_klines(b"\n".join(january) + b"\n", "2020-01")
    _, feb_audit = monthly.parse_historical_graded_klines(b"\n".join(february) + b"\n", "2020-02")
    assert jan_audit["trailing_missing_minutes"] == 2
    assert feb_audit["leading_missing_minutes"] == 3
    pair = monthly.audit_historical_boundaries([
        {"month": "2020-01", "first_open_utc": jan_audit["first_open_utc"],
         "last_open_utc": jan_audit["last_open_utc"],
         "trailing_missing_minutes": jan_audit["trailing_missing_minutes"], "audit": jan_audit},
        {"month": "2020-02", "first_open_utc": feb_audit["first_open_utc"],
         "last_open_utc": feb_audit["last_open_utc"],
         "leading_missing_minutes": feb_audit["leading_missing_minutes"], "audit": feb_audit},
    ])[0]
    assert pair["span_minutes"] == 6
    assert pair["gap_minutes"] == 5


@pytest.mark.parametrize("total_missing,evaluate_missing,expected_tier", [
    (500, 0, "normal"), (501, 0, "soft_warning"),
    (1000, 0, "soft_warning"),
])
def test_aggregate_gap_threshold_inclusive(total_missing, evaluate_missing, expected_tier):
    summary = monthly.classify_historical_aggregate(
        total_expected=100_000, total_missing=total_missing,
        evaluate_expected=50_000, evaluate_missing=evaluate_missing)
    assert summary["aggregate_status_tier"] == expected_tier
    assert summary["aggregate_missing_ratio"] == total_missing / 100_000


def test_aggregate_over_one_percent_hard_fails():
    with pytest.raises(data.DataValidationError, match="1%|aggregate|hard"):
        monthly.classify_historical_aggregate(
            total_expected=100_000, total_missing=1001,
            evaluate_expected=50_000, evaluate_missing=0)


def test_evaluation_aggregate_point_three_percent_is_inclusive():
    summary = monthly.classify_historical_aggregate(
        total_expected=200_000, total_missing=300,
        evaluate_expected=100_000, evaluate_missing=300)
    assert summary["evaluate_aggregate_missing_ratio"] == 0.003


def test_evaluation_aggregate_over_point_three_percent_hard_fails():
    with pytest.raises(data.DataValidationError, match="0.3%|evaluation|aggregate"):
        monthly.classify_historical_aggregate(
            total_expected=200_000, total_missing=301,
            evaluate_expected=100_000, evaluate_missing=301)


def test_partial_total_gap_budget_uses_fixed_full_calendar_denominator(contract):
    monthly._check_historical_partial_gap_budget(
        [{"month": "2020-01", "missing_minutes": 29_808}], contract)
    with pytest.raises(data.DataValidationError, match="Aggregate.*1%"):
        monthly._check_historical_partial_gap_budget(
            [{"month": "2020-01", "missing_minutes": 29_809}], contract)


def test_partial_evaluation_gap_budget_uses_fixed_full_calendar_denominator(contract):
    monthly._check_historical_partial_gap_budget(
        [{"month": "2021-01", "missing_minutes": 7_361}], contract)
    with pytest.raises(data.DataValidationError, match="Evaluation.*0.3%"):
        monthly._check_historical_partial_gap_budget(
            [{"month": "2021-01", "missing_minutes": 7_362}], contract)


@pytest.fixture
def manifest_metadata(contract):
    """Invent receipts and audited counts without opening any market archive."""
    entries = []
    prior_sha = "e" * 64
    for month in contract["months"]:
        start, end = research_policy.historical_month_bounds(month)
        expected = int((end - start) / pd.Timedelta(minutes=1))
        missing = 414 if month == "2020-02" else 0
        unit = "us" if month >= "2025-01" else "ms"
        scale = 1_000 if unit == "us" else 1_000_000
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
            "zip_sha256": monthly._D032_FAILED_MONTH_EXPECTED["zip_sha256"] if missing else "a" * 64,
            "checksum_text_sha256": monthly._D032_FAILED_MONTH_EXPECTED["checksum_sha256"] if missing else "b" * 64,
            "csv_sha256": "d" * 64, "checksum_bytes": 88, "csv_bytes": 5_000_000,
        }
        size = 2_224_194 if month == "2020-01" else 2_077_466 if missing else 100
        entry = {
            **audit, "audit": copy.deepcopy(audit), "notes": "; ".join(notes),
            "status": "passed", "url": monthly._historical_url(month),
            "checksum_url": monthly._historical_url(month, ".zip.CHECKSUM"),
            "parquet_sha256": "c" * 64, "zip_bytes": size, "bytes_downloaded": size,
            "checksum_bytes_downloaded": 88, "zip_reserved_bytes": 0, "checksum_reserved_bytes": 0,
            "attempts": 1, "http_gets": 2, "expected_unit": unit, "detected_unit": unit,
        }
        if missing:
            entry.update(historical_attempts=2, prior_failed_ledger_sha256=prior_sha, history=[
                {"event": "failed_download_cleanup", "commit_sha": "1d6e1f2"},
                {"event": "D-032_refetch_success",
                 "event_ledger_path": "results/phase3a-gate1/continuations/2020-02-refetch.json",
                 "success_commit_sha": "1" * 40},
            ])
        entries.append(entry)
    ledger = {
        "schema_version": 1, "phase": "3A-gate1", "status": "passed",
        "http_get_cap": 408, "http_gets_cap": 408,
        "zip_budget_bytes": contract["zip_budget_bytes"], "checksum_budget_bytes": contract["checksum_budget_bytes"],
        "holdout_requests": 0, "holdout_access_count": 0,
        "months": copy.deepcopy(entries), "attempts": [],
        "prior_failed_ledger_sha256": prior_sha,
        "prior_failed_month_history": {"month": "2020-02", "status": "failed"},
        "continuation_mode": "D-032-single-month-refetch",
    }
    for entry in entries:
        for number in ((1, 2) if entry["month"] == "2020-02" else (1,)):
            for kind in ("checksum", "zip"):
                ledger["attempts"].append({
                    "id": len(ledger["attempts"]), "month": entry["month"], "kind": kind,
                    "month_attempt": number, "status": "received", "http_status": 200,
                    "url": entry["checksum_url" if kind == "checksum" else "url"],
                    "body_bytes": entry[f"{kind}_bytes"], "content_length": entry[f"{kind}_bytes"],
                    "reserved_bytes": 0,
                    "sha256": entry["checksum_text_sha256" if kind == "checksum" else "zip_sha256"],
                })
    _refresh_test_ledger_totals(ledger)
    return entries, ledger


def _refresh_test_ledger_totals(ledger):
    ledger["http_gets_count"] = len(ledger["attempts"])
    for kind in ("zip", "checksum"):
        ledger[f"{kind}_body_bytes"] = sum(a["body_bytes"] for a in ledger["attempts"] if a["kind"] == kind)
        ledger[f"{kind}_reserved_bytes"] = sum(a["reserved_bytes"] for a in ledger["attempts"] if a["kind"] == kind)
        ledger[f"{kind}_charged_bytes"] = ledger[f"{kind}_body_bytes"] + ledger[f"{kind}_reserved_bytes"]
    ledger["total_bytes_downloaded"] = ledger["zip_body_bytes"]
    ledger["checksum_bytes_downloaded"] = ledger["checksum_body_bytes"]


def test_graded_manifest_charges_original_failed_february_once(contract, manifest_metadata):
    entries, ledger = manifest_metadata
    manifest = monthly.build_historical_graded_manifest(entries, contract, ledger_summary=ledger)
    monthly.validate_historical_graded_manifest(manifest, contract)
    accounting = manifest["transfer_accounting"]
    assert accounting["prior_failed_payload_bytes"] == {"zip": 2_077_466, "checksum": 88}
    assert accounting["successful_payload_bytes"]["zip"] == sum(e["zip_bytes"] for e in entries)
    assert accounting["successful_month_transfer_body_bytes"] == accounting["successful_payload_bytes"]
    assert accounting["cumulative_http_gets"] == 138
    assert manifest["total_bytes_downloaded"] == sum(e["bytes_downloaded"] for e in entries) + 2_077_466
    assert manifest["checksum_bytes_downloaded"] == 69 * 88
    assert accounting["cumulative_body_bytes"] == accounting["cumulative_charged_bytes"]


def test_graded_manifest_preserves_partial_retry_and_unresolved_reservation(contract, manifest_metadata):
    entries, ledger = manifest_metadata
    retry_checksum, retry_zip = copy.deepcopy(ledger["attempts"][-2:])
    retry_zip.update(status="failed", body_bytes=7, reserved_bytes=9,
                     error_type="TimeoutError", error="Synthetic interrupted read")
    retry_zip.pop("sha256")
    retry_zip.pop("content_length")
    for attempt in ledger["attempts"][-2:]:
        attempt["month_attempt"] = 2
    ledger["attempts"][-2:-2] = [retry_checksum, retry_zip]
    for identifier, attempt in enumerate(ledger["attempts"]):
        attempt["id"] = identifier
    entries[-1].update(attempts=2, http_gets=4, bytes_downloaded=107,
                       checksum_bytes_downloaded=176, zip_reserved_bytes=9)
    ledger["months"] = copy.deepcopy(entries)
    _refresh_test_ledger_totals(ledger)
    manifest = monthly.build_historical_graded_manifest(entries, contract, ledger_summary=ledger)
    accounting = manifest["transfer_accounting"]
    assert accounting["retry_body_bytes"] == {"zip": 7, "checksum": 88}
    assert accounting["unresolved_reserved_bytes"] == {"zip": 9, "checksum": 0}
    assert accounting["cumulative_charged_bytes"]["zip"] == manifest["total_bytes_downloaded"] + 9


@pytest.mark.parametrize("replacement", [None, {}, {"status": "failed"}])
def test_graded_manifest_rejects_missing_or_failed_network_ledger(contract, manifest_metadata, replacement):
    entries, _ = manifest_metadata
    with pytest.raises(data.DataValidationError, match="completed network ledger"):
        monthly.build_historical_graded_manifest(entries, contract, ledger_summary=replacement)


@pytest.mark.parametrize("mutation", [
    "failed", "get_cap", "get_count", "byte_budget", "holdout", "missing_month", "entry_digest",
    "attempt_id", "attempt_url", "attempt_length", "attempt_digest", "attempt_state",
    "attempt_reserved", "cumulative_body", "cumulative_charged", "prior_pair_removed",
])
def test_graded_manifest_rejects_tampered_network_metadata(contract, manifest_metadata, mutation):
    entries, ledger = manifest_metadata
    if mutation == "failed":
        ledger["status"] = "failed"
    elif mutation == "get_cap":
        ledger["http_gets_cap"] = 409
    elif mutation == "get_count":
        ledger["http_gets_count"] = 409
    elif mutation == "byte_budget":
        ledger["zip_budget_bytes"] += 1
    elif mutation == "holdout":
        ledger["holdout_access_count"] = 1
    elif mutation == "missing_month":
        ledger["months"].pop()
    elif mutation == "entry_digest":
        ledger["months"][-1]["zip_sha256"] = "0" * 64
    elif mutation == "attempt_id":
        ledger["attempts"][-1]["id"] -= 1
    elif mutation == "attempt_url":
        ledger["attempts"][-1]["url"] += "?changed=1"
    elif mutation == "attempt_length":
        ledger["attempts"][-1]["content_length"] += 1
    elif mutation == "attempt_digest":
        ledger["attempts"][-1]["sha256"] = "0" * 64
    elif mutation == "attempt_state":
        ledger["attempts"][-1]["status"] = "started"
    elif mutation == "attempt_reserved":
        ledger["attempts"][-1]["reserved_bytes"] = 1
    elif mutation == "cumulative_body":
        ledger["zip_body_bytes"] -= 2_077_466
    elif mutation == "cumulative_charged":
        ledger["zip_charged_bytes"] -= 1
    elif mutation == "prior_pair_removed":
        del ledger["attempts"][2:4]
        for index, attempt in enumerate(ledger["attempts"]):
            attempt["id"] = index
        _refresh_test_ledger_totals(ledger)
    with pytest.raises(data.DataValidationError):
        monthly.build_historical_graded_manifest(entries, contract, ledger_summary=ledger)


def test_graded_manifest_checks_cumulative_budget_after_reconciling_attempts(contract, manifest_metadata):
    entries, ledger = manifest_metadata
    large_zip = contract["per_zip_limit_bytes"]
    for entry in entries[2:]:
        entry["zip_bytes"] = entry["bytes_downloaded"] = large_zip
    ledger["months"] = copy.deepcopy(entries)
    for attempt in ledger["attempts"][6:]:
        if attempt["kind"] == "zip":
            attempt["body_bytes"] = attempt["content_length"] = large_zip
    _refresh_test_ledger_totals(ledger)
    assert ledger["zip_body_bytes"] > contract["zip_budget_bytes"]
    with pytest.raises(monthly.DownloadBudgetError, match="cumulative transfer.*budget"):
        monthly.build_historical_graded_manifest(entries, contract, ledger_summary=ledger)


def test_graded_manifest_rejects_rehashed_success_only_top_level_totals(contract, manifest_metadata):
    entries, ledger = manifest_metadata
    manifest = monthly.build_historical_graded_manifest(entries, contract, ledger_summary=ledger)
    manifest["total_bytes_downloaded"] -= 2_077_466
    manifest["overall_sha256"] = data.sha256(monthly._historical_json_bytes(
        {key: value for key, value in manifest.items() if key != "overall_sha256"}))
    with pytest.raises(data.DataValidationError, match="totals do not reconcile"):
        monthly.validate_historical_graded_manifest(manifest, contract)
