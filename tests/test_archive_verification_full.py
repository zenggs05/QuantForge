"""Synthetic contracts for the D-041 full daily archive verification.

All writes and network responses are redirected into pytest fixtures.  No test
uses the real full-verification destination or any real market archive.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import errno
import hashlib
import http.client
import io
import importlib.util
import json
from pathlib import Path
import re
import socket
import ssl
import types
import urllib.error
import urllib.request
import zipfile

import pytest

from quantforge import archive_verification_full as full


REAL_VERIFY = Path(r"D:\zgs_project\QuantForge-verification\binance-spot-daily-1m-full-d041")
REAL_EVIDENCE = Path(__file__).resolve().parents[1] / "docs/phase3a-full-verification-evidence"
DAY = "2020-01-01"
EPOCH = date(1970, 1, 1)


def _directory_metadata(path: Path) -> tuple:
    if not path.exists():
        return (False,)
    return (True, tuple((p.relative_to(path).as_posix(), p.stat().st_size,
                         p.stat().st_mtime_ns)
                        for p in sorted(path.rglob("*")) if p.is_file()))


@pytest.fixture(scope="session", autouse=True)
def real_destinations_remain_untouched():
    before = (_directory_metadata(REAL_VERIFY), _directory_metadata(REAL_EVIDENCE))
    yield
    assert (_directory_metadata(REAL_VERIFY), _directory_metadata(REAL_EVIDENCE)) == before


@pytest.fixture(autouse=True)
def isolated_outputs_and_network(tmp_path, monkeypatch):
    monkeypatch.setattr(full, "VERIFY_DIR_FULL", tmp_path / "verify")
    monkeypatch.setattr(full, "EVIDENCE_DIR", tmp_path / "evidence")

    def denied(*args, **kwargs):
        raise AssertionError("A synthetic test attempted a real network request")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(urllib.request, "urlopen", denied)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", denied)
    monkeypatch.setattr(full, "check_resources", lambda: None)


def _start_us(day: str) -> int:
    return (date.fromisoformat(day) - EPOCH).days * 86_400_000_000


def _row(day: str = DAY, minute: int = 0, *, unit: str | None = None,
         changes: dict[str, str] | None = None) -> tuple[str, ...]:
    unit = unit or ("us" if day >= "2025-01-01" else "ms")
    factor = 1 if unit == "us" else 1000
    start = (_start_us(day) + minute * 60_000_000) // factor
    span = 59_999_999 if unit == "us" else 59_999
    row = [str(start), "100.00000000", "100.00000000", "100.00000000",
           "100.00000000", "100.00000000", str(start + span),
           "10000.00000000", "10000", "50.00000000", "5000.00000000", "0"]
    columns = ("open_time", "open", "high", "low", "close", "volume", "close_time",
               "quote_asset_volume", "number_of_trades", "taker_buy_base_asset_volume",
               "taker_buy_quote_asset_volume", "ignore")
    for field, value in (changes or {}).items():
        row[columns.index(field)] = value
    return tuple(row)


def _csv(rows) -> bytes:
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(rows)
    return output.getvalue().encode("utf-8")


def _parsed(row: tuple[str, ...], day: str = DAY, side: str = "daily"):
    return full.parse_daily_csv(_csv([row]), day, side=side)


def _bar(row: tuple[str, ...], day: str = DAY, side: str = "daily"):
    parsed = _parsed(row, day, side)
    assert not parsed.abnormal
    return next(iter(parsed.rows.values()))


def _zip(day: str, rows) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"BTCUSDT-1m-{day}.csv", _csv(rows))
    return output.getvalue()


class Response:
    """A bounded in-memory HTTP response, including the read-one-byte check."""

    def __init__(self, payload: bytes, url: str, *, status: int = 200,
                 content_length: object = "auto", fail_after: int | None = None,
                 error: BaseException | None = None):
        self.payload = payload
        self.url = url
        self.status = status
        self.code = status
        self.offset = 0
        self.fail_after = fail_after
        self.error = error
        self.headers = {"Last-Modified": "Wed, 01 Jan 2020 00:00:00 GMT"}
        if content_length == "auto":
            self.headers["Content-Length"] = str(len(payload))
        elif content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def geturl(self):
        return self.url

    def read(self, count: int = -1):
        if self.fail_after is not None and self.offset >= self.fail_after:
            raise self.error or TimeoutError("synthetic timeout")
        upper = len(self.payload) if count < 0 else min(len(self.payload), self.offset + count)
        if self.fail_after is not None:
            upper = min(upper, self.fail_after)
        result = self.payload[self.offset:upper]
        self.offset = upper
        return result

    read1 = read

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_whitelist_exact_size_dates_and_urls():
    days = full.generate_dates()
    assert len(days) == len(set(days)) == 2059
    assert days[0] == "2020-01-01" and days[-1] == "2025-08-31"
    excluded = {"2020-11-30", "2020-12-21", "2021-01-11", "2021-01-21", "2021-02-11",
                "2021-04-22", "2021-04-23", "2021-09-23", "2021-09-29", "2022-04-13", "2023-03-24"}
    assert not excluded.intersection(days)
    urls = [url for day in days for url in full.urls_for_day(day)]
    assert len(urls) == len(set(urls)) == 4118
    assert all(full.allowed_url(url)[0] in days for url in urls)


@pytest.mark.parametrize("day", ["2019-12-31", "2025-09-01", "2026-07-01", "2026-08-31",
                                  "2026-09-01", "2026-09-02", "2026-10-31", "2020-11-30"])
def test_unauthorized_dates_are_rejected(day):
    with pytest.raises(Exception):
        full.ensure_day(day)


@pytest.mark.parametrize("day,unit", [("2020-02-29", "ms"), ("2024-02-29", "ms"),
                                      ("2024-12-31", "ms"), ("2025-01-01", "us"),
                                      ("2025-08-31", "us")])
def test_integer_timestamp_normalization_and_calendar_boundaries(day, unit):
    rows = [_row(day, 0), _row(day, 1439)]
    parsed = full.parse_daily_csv(_csv(rows), day)
    assert not parsed.abnormal
    assert parsed.unit == unit
    assert sorted(parsed.rows) == [_start_us(day), _start_us(day) + 1439 * 60_000_000]
    assert all(isinstance(k, int) for k in parsed.rows)
    assert parsed.rows[_start_us(day)].close_end_us == _start_us(day) + 60_000_000


def test_missing_minutes_remain_comparable_and_grid_is_complete():
    monthly = full.parse_daily_csv(_csv([_row(minute=0), _row(minute=1)]), DAY, side="monthly")
    daily = full.parse_daily_csv(_csv([_row(minute=0), _row(minute=2)]), DAY)
    result = full.compare_day(DAY, monthly, daily)
    assert result["counts"] == {"both_equal": 1, "value_conflict": 0,
                                 "missing_in_monthly": 1, "extra_in_monthly": 1,
                                 "both_missing": 1437}
    assert len(result["minutes"]) == 1440


def test_different_units_same_minute_produce_only_time_semantics():
    monthly = _parsed(_row("2024-12-31", unit="ms"), "2024-12-31", "monthly")
    daily = _parsed(_row("2024-12-31", unit="us"), "2024-12-31")
    result = full.compare_day("2024-12-31", monthly, daily)
    assert result["counts"]["value_conflict"] == 0
    assert result["counts"]["both_equal"] == 0
    assert result["timestamp_anomalies"]
    minute = result["minutes"][0]
    assert minute["status"] == "time_semantics_uncomparable"
    assert minute["classification"] == ""


@pytest.mark.parametrize("mutation", ["mixed_open", "mixed_close", "ambiguous_unit", "duplicate", "off_grid", "outside_day", "nonnumeric"])
def test_abnormal_rows_make_the_file_unverifiable(mutation):
    row = list(_row())
    rows = [tuple(row)]
    if mutation == "mixed_open":
        row[0] = str(int(row[0]) * 1000)
    elif mutation == "mixed_close":
        row[6] = str(int(row[6]) * 1000)
    elif mutation == "ambiguous_unit":
        row[0] = str(10**14)
    elif mutation == "duplicate":
        rows.append(tuple(row))
    elif mutation == "off_grid":
        row[0] = str(int(row[0]) + 1)
    elif mutation == "outside_day":
        row[0] = str(int(row[0]) - 60_000)
    else:
        row[5] = "not-a-number"
    if mutation != "duplicate":
        rows = [tuple(row)]
    parsed = full.parse_daily_csv(_csv(rows), DAY)
    assert parsed.abnormal
    assert parsed.anomalies


@pytest.mark.parametrize("day", ["2020-01-01", "2020-01-31", "2020-02-01", "2020-02-29",
                                  "2024-02-29", "2025-08-01", "2025-08-31"])
def test_first_last_and_leap_days_remain_in_correct_month(day):
    parsed = full.parse_daily_csv(_csv([_row(day, 0), _row(day, 1439)]), day)
    assert not parsed.abnormal
    assert all((datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=t)).strftime("%Y-%m") == day[:7]
               for t in parsed.rows)


@pytest.mark.parametrize("close,volume,trades,severity", [
    ("100.01", "100", "10000", "minor"),
    ("100.01000001", "100", "10000", "moderate"),
    ("100.1", "100", "10000", "moderate"),
    ("100.10000001", "100", "10000", "major"),
    ("100", "99", "9900", "minor"),
    ("100", "98.999999", "10000", "moderate"),
    ("100", "90", "9000", "moderate"),
    ("100", "89.999999", "10000", "major"),
])
def test_severity_decimal_boundaries(close, volume, trades, severity):
    monthly = _bar(_row(), side="monthly")
    daily = _bar(_row(changes={"close": close, "volume": volume, "number_of_trades": trades}))
    result = full.classify_rows(monthly, daily, DAY)
    assert result["classification"] == "value_conflict"
    assert result["severity"] == severity
    assert result["third_source_status"] == "not_checked"


def test_format_only_and_timestamp_only_are_separate():
    monthly = _bar(_row(), side="monthly")
    formatted = _bar(_row(changes={"close": "100.0"}))
    changed_time = _bar(_row(changes={"close_time": str(int(_row()[6]) - 1)}))
    assert full.classify_rows(monthly, formatted, DAY)["severity"] == "format_only"
    assert full.classify_rows(monthly, changed_time, DAY)["severity"] == "timestamp_only"


def test_structural_priority_and_source_confidence():
    placeholder = _bar(_row(changes={"volume": "0", "number_of_trades": "0",
                                     "quote_asset_volume": "0", "taker_buy_base_asset_volume": "0",
                                     "taker_buy_quote_asset_volume": "0"}), side="monthly")
    traded = _bar(_row())
    result = full.classify_rows(placeholder, traded, DAY)
    assert result["severity"] == "structural"
    assert result["confidence"] == "monthly_incomplete_pattern"
    assert full.classify_rows(traded, placeholder, DAY)["confidence"] == "daily_incomplete_pattern"
    assert full.classify_rows(placeholder, None, DAY)["confidence"] == "monthly_only_placeholder"
    assert full.classify_rows(traded, None, DAY)["confidence"] == "monthly_only_traded"
    assert full.classify_rows(None, traded, DAY)["confidence"] == "daily_only_row"


def test_decimal_metrics_consider_all_volume_fields():
    monthly = _bar(_row(), side="monthly")
    daily = _bar(_row(changes={"taker_buy_quote_asset_volume": "10000"}))
    result = full.classify_rows(monthly, daily, DAY)
    assert result["severity"] == "major"
    assert Decimal(result["vol_rel"]) == Decimal("0.5")


@pytest.mark.parametrize("error", [TimeoutError("x"), ConnectionResetError("x"), ConnectionAbortedError("x"),
                                    http.client.RemoteDisconnected("x"), http.client.IncompleteRead(b"part", 5),
                                    ssl.SSLEOFError("x"), ssl.SSLZeroReturnError("x"), socket.gaierror("x")])
def test_exact_transient_error_allowlist(error):
    assert full.classify_network_error(error) == "retry"
    assert full.classify_network_error(urllib.error.URLError(error)) == "retry"


@pytest.mark.parametrize("error", [PermissionError("denied"), OSError(errno.EACCES, "denied"),
                                    OSError(errno.EPERM, "denied"), ssl.SSLCertVerificationError("cert"),
                                    ssl.CertificateError("cert"), ValueError("unknown"),
                                    urllib.error.URLError(OSError(errno.EIO, "io")),
                                    urllib.error.URLError("unknown string")])
def test_refusals_certificates_and_unknown_errors_are_not_retried(error):
    assert full.classify_network_error(error) == "stop"


@pytest.mark.parametrize("code", [5, 10013])
def test_windows_access_denial_is_not_retried(code):
    error = OSError("access denied")
    error.winerror = code
    assert full.classify_network_error(error) == "stop"
    assert full.classify_network_error(urllib.error.URLError(error)) == "stop"


@pytest.mark.parametrize("length", [None, "", "NaN", "-1", "0", "1.0", "4097"])
def test_content_length_contract_rejects_missing_invalid_or_large(length):
    response = Response(b"x", "https://unused.invalid", content_length=length)
    with pytest.raises(full.StopTask):
        full.read_response(response, "checksum", 1_048_576, 120, clock=lambda: 0)


def test_content_length_exact_bytes_and_truncated_bytes():
    assert full.read_response(Response(b"abc", "https://unused.invalid"), "checksum", 20, 120, clock=lambda: 0) == (b"abc", 3)
    with pytest.raises(Exception) as caught:
        full.read_response(Response(b"ab", "https://unused.invalid", content_length="3"), "checksum", 20, 120, clock=lambda: 0)
    assert full.classify_network_error(caught.value) == "retry"
    assert getattr(caught.value, "payload", b"") == b"ab"


def test_received_bytes_above_content_length_stop():
    with pytest.raises(full.StopTask):
        full.read_response(Response(b"abcd", "https://unused.invalid", content_length="3"), "checksum", 20, 120, clock=lambda: 0)


def test_wall_clock_attempt_deadline_is_a_retryable_timeout():
    with pytest.raises(Exception) as caught:
        full.read_response(Response(b"abc", "https://unused.invalid"), "checksum", 20, 120, clock=lambda: 121)
    assert full.classify_network_error(caught.value) == "retry"


def test_stop_filename_is_exact_utc_format():
    stamp = datetime(2026, 11, 10, 1, 2, 3, tzinfo=timezone.utc)
    assert full.stop_filename(stamp) == "stop-20261110T010203Z.json"


@pytest.mark.parametrize("target", [
    Path(r"D:\zgs_project\QuantForge-verification\binance-spot-daily-1m-20261110\x.zip"),
    Path(r"D:\zgs_project\QuantForge\data\x.zip"),
    Path(r"D:\zgs_project\QuantForge-2f\data\x.zip"),
    Path(__file__).resolve().parents[1] / "data/raw/binance/spot/BTCUSDT/1m/historical/2020-01.zip",
    Path(__file__).resolve().parents[1] / "docs/phase3a-daily-verification-evidence/x.json",
])
def test_paths_outside_full_outputs_are_rejected(target):
    with pytest.raises(full.StopTask):
        full.safe_path(target, full.VERIFY_DIR_FULL)


@pytest.mark.parametrize("component", ["root", "ancestor", "child"])
@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_links_in_any_path_component_are_rejected(component, kind, tmp_path, monkeypatch):
    root = tmp_path / "parent" / "verify"
    child = root / "child"
    child.mkdir(parents=True)
    monkeypatch.setattr(full, "VERIFY_DIR_FULL", root)
    linked = {"root": root, "ancestor": root.parent, "child": child}[component]
    if kind == "symlink":
        original = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda self: self == linked or original(self))
    else:
        original = Path.lstat
        def fake_lstat(self, *args, **kwargs):
            actual = original(self, *args, **kwargs)
            if self == linked:
                return types.SimpleNamespace(st_file_attributes=0x400, st_mode=actual.st_mode)
            return actual
        monkeypatch.setattr(Path, "lstat", fake_lstat)
    with pytest.raises(full.StopTask):
        full.safe_path(child / "x.zip", root)


def _contamination(presence: bytearray, conflicts: dict, verified_days=None):
    verified_days = {DAY} if verified_days is None else verified_days
    keys = {name: set(conflicts.get(name, ())) for name in ("struct", "close", "cvt", "bar", "any")}
    outputs = full.contamination_artifacts(presence, keys, verified_days)
    table = list(csv.DictReader(io.StringIO(outputs["contamination-by-month.csv"].decode("utf-8"))))
    return table, outputs


def _count(table, scope, population, field, *, month="2020-01", horizon=5):
    row = next(r for r in table if r["decision_month"] == month and r["scope"] == scope
               and r["population"] == population and int(r["horizon"]) == horizon)
    return int(row[field])


def test_contamination_upper_bound_and_frozen_dependency_closed_forms():
    presence = bytearray([1]) * 200
    table, _ = _contamination(presence, {"close": {100}, "cvt": {100}, "any": {100}})
    assert _count(table, "U", "all", "feature_contaminated") == 61
    assert _count(table, "F", "all", "feature_contaminated") == 61
    assert _count(table, "U", "all", "label_contaminated") == 6
    assert _count(table, "F", "all", "label_contaminated") == 2
    assert _count(table, "U", "all", "feature_or_label_contaminated") == 66
    assert _count(table, "F", "all", "feature_or_label_contaminated") == 62
    assert _count(table, "U", "all", "label_contaminated", horizon=15) == 16
    assert _count(table, "F", "all", "label_contaminated", horizon=15) == 2
    assert _count(table, "F", "structurally_valid", "feature_contaminated") == 61


def test_bar_only_conflict_is_current_feature_only_under_frozen_rule():
    table, _ = _contamination(bytearray([1]) * 200, {"bar": {100}, "any": {100}})
    assert _count(table, "U", "all", "feature_contaminated") == 61
    assert _count(table, "F", "all", "feature_contaminated") == 1
    assert _count(table, "U", "all", "label_contaminated") == 6
    assert _count(table, "F", "all", "label_contaminated") == 0


def test_quote_only_conflict_is_excluded_from_frozen_dependency_rule():
    table, _ = _contamination(bytearray([1]) * 200, {"any": {100}})
    assert _count(table, "U", "all", "feature_contaminated") == 61
    assert _count(table, "F", "all", "feature_contaminated") == 0
    assert _count(table, "F", "all", "label_contaminated") == 0


def test_structural_conflict_in_a_gap_uses_time_axis_without_compressing():
    presence = bytearray([1]) * 200
    presence[100] = 0
    table, _ = _contamination(presence, {"struct": {100}, "any": {100}})
    assert _count(table, "F", "all", "feature_contaminated") == 60
    assert _count(table, "F", "all", "label_contaminated") == 5
    assert _count(table, "F", "structurally_valid", "feature_contaminated") == 0
    assert _count(table, "F", "structurally_valid", "label_contaminated") == 0


def test_initial_history_and_unverified_windows_are_marked():
    table, _ = _contamination(bytearray([1]) * 200, {"any": {0}, "close": {0}, "cvt": {0}}, set())
    assert _count(table, "U", "all", "insufficient_history") == 60
    assert _count(table, "U", "all", "feature_window_touches_unverified") == 200
    assert _count(table, "U", "all", "label_window_touches_unverified") == 195
    assert _count(table, "U", "structurally_valid", "feature_contaminated") == 1


def test_decision_month_uses_bar_plus_sixty_one_seconds():
    presence = bytearray(31 * 1440 + 2)
    presence[31 * 1440 - 1] = 1
    table, _ = _contamination(presence, {"bar": {31 * 1440 - 1}, "any": {31 * 1440 - 1}}, {"2020-01-31", "2020-02-01"})
    assert _count(table, "F", "all", "sample_bars", month="2020-02") == 1
    assert _count(table, "F", "all", "feature_contaminated", month="2020-02") == 1


def test_final_august_bar_is_outside_protocol_and_has_insufficient_future():
    total = 2070 * 1440
    presence = bytearray(total)
    presence[-61:] = b"\x01" * 61
    last = total - 1
    table, _ = _contamination(presence, {"struct": {last}, "any": {last}}, {"2025-08-31"})
    rows = [r for r in table if r["decision_month"] == "2025-09" and r["population"] == "all"]
    assert len(rows) == 4
    for row in rows:
        assert row["role"] == "outside_protocol"
        assert int(row["sample_bars"]) == 1
        assert int(row["label_contaminated"]) == 0
        assert int(row["insufficient_future"]) == 1


def test_gw_exposure_counts_previous_calendar_month_only():
    december_last = 366 * 1440 - 1
    january_first = december_last + 1
    presence = bytearray(january_first + 1)
    _, outputs = _contamination(presence, {"struct": {december_last}, "close": {december_last, january_first}}, {"2020-12-31", "2021-01-01"})
    rows = list(csv.DictReader(io.StringIO(outputs["gw-instrument-exposure.csv"].decode("utf-8"))))
    assert len(rows) == 56
    assert rows[0]["evaluation_month"] == "2021-01"
    assert rows[0]["previous_month"] == "2020-12"
    assert int(rows[0]["conflict_minutes"]) == 1
    assert rows[1]["previous_month"] == "2021-01"
    assert int(rows[1]["conflict_minutes"]) == 1


def _successful_opener(day=DAY, rows=None):
    archive = _zip(day, rows or [_row(day)])
    checksum = f"{hashlib.sha256(archive).hexdigest()}  BTCUSDT-1m-{day}.zip\n".encode()
    calls = []

    def opener(request, timeout=30):
        assert request.get_method() == "GET" and timeout == 30
        calls.append(request.full_url)
        parsed_day, kind = full.allowed_url(request.full_url)
        assert parsed_day == day
        return Response(checksum if kind == "checksum" else archive, request.full_url)

    return opener, calls, archive, checksum


def test_intent_is_fsynced_before_each_get_and_settle_charges_bytes(monkeypatch):
    real_fsync = full.os.fsync
    fsyncs = []

    def fsync(descriptor):
        fsyncs.append(descriptor)
        return real_fsync(descriptor)

    monkeypatch.setattr(full.os, "fsync", fsync)
    base_opener, calls, archive, checksum = _successful_opener()

    def opener(request, timeout=30):
        events = full.ledger_events()
        assert events[-1]["event"] == "intent"
        assert events[-1]["url"] == request.full_url
        assert fsyncs
        return base_opener(request, timeout)

    result = full.acquire_day(DAY, "B2020", opener=opener, sleep=lambda _: None)
    assert result["status"] == "verified"
    assert calls == list(full.urls_for_day(DAY))
    state = full.rebuild_ledger()
    assert state["gets"] == state["distinct_urls"] == 2
    assert state["zip_bytes"] == len(archive)
    assert state["checksum_bytes"] == len(checksum)
    assert not state["unsettled_intents"]
    assert not list((full.VERIFY_DIR_FULL / ".partial").glob("*"))
    full.verify_download_inventory()


def test_verified_event_precedes_atomic_replace(monkeypatch):
    original_replace = full.os.replace
    moves = []

    def replace(source, destination):
        assert Path(source).parent.name == ".partial"
        assert full.ledger_events()[-1]["event"] == "verified"
        moves.append((Path(source), Path(destination)))
        return original_replace(source, destination)

    monkeypatch.setattr(full.os, "replace", replace)
    opener, _, _, _ = _successful_opener()
    full.acquire_day(DAY, "B2020", opener=opener, sleep=lambda _: None)
    assert len(moves) == 2


def test_permission_error_from_atomic_replace_is_not_retried(monkeypatch):
    moves = []

    def replace(source, destination):
        moves.append((source, destination))
        raise PermissionError("synthetic refusal")

    monkeypatch.setattr(full.os, "replace", replace)
    opener, calls, _, _ = _successful_opener()
    with pytest.raises(PermissionError):
        full.acquire_day(DAY, "B2020", opener=opener, sleep=lambda _: None)
    assert len(moves) == 1
    assert len(calls) == 2


def test_unsettled_intent_reserves_full_budget_and_blocks_restart():
    url = full.urls_for_day(DAY)[1]
    full.append_event({"event": "intent", "id": "000001", "batch": "B2020", "day": DAY,
                       "url": url, "kind": "zip", "attempt": 1,
                       "reserved_bytes": full.ZIP_LIMIT + 1, "utc": "2026-11-10T00:00:00Z"})
    state = full.rebuild_ledger()
    assert state["gets"] == state["distinct_urls"] == 1
    assert state["zip_bytes"] == full.ZIP_LIMIT + 1
    assert state["unsettled_intents"] == ["000001"]
    with pytest.raises(full.StopTask):
        full.download_url(DAY, "zip", "B2020", opener=lambda *_a, **_k: pytest.fail("crash must block network"))


@pytest.mark.parametrize("status", [301, 302, 307, 308, 201, 204, 400, 401, 403, 418])
def test_disallowed_http_status_stops_once(status):
    calls = []

    def opener(request, timeout=30):
        calls.append(request.full_url)
        return Response(b"error", request.full_url, status=status)

    with pytest.raises(full.StopTask):
        full.acquire_day(DAY, "B2020", opener=opener, sleep=lambda _: pytest.fail("no retry"))
    assert len(calls) == 1
    assert full.ledger_events()[-1]["result"] == "stop"


def test_final_url_mismatch_stops_before_following_or_retry():
    calls = []

    def opener(request, timeout=30):
        calls.append(request.full_url)
        return Response(b"redirect", "https://unapproved.invalid/file")

    with pytest.raises(full.StopTask):
        full.acquire_day(DAY, "B2020", opener=opener, sleep=lambda _: pytest.fail("no retry"))
    assert len(calls) == 1


@pytest.mark.parametrize("status", [429, 500, 503, 599])
def test_transient_http_exhaustion_records_day_and_bounded_backoff(status):
    waits = []
    calls = []

    def opener(request, timeout=30):
        calls.append(request.full_url)
        return Response(b"error", request.full_url, status=status)

    result = full.acquire_day(DAY, "B2020", opener=opener, sleep=waits.append)
    assert result["status"] == "unverifiable_network"
    assert len(calls) == 3
    assert waits == [1, 4]
    assert full.rebuild_ledger()["checksum_bytes"] == 15
    assert full.rebuild_ledger()["unverifiable_dates"] == 1
    assert len(list((full.VERIFY_DIR_FULL / "failed").glob("*"))) == 3


def test_checksum_404_skips_zip_and_terminal_day_is_not_requested_again():
    calls = []

    def opener(request, timeout=30):
        calls.append(request.full_url)
        return Response(b"missing", request.full_url, status=404)

    first = full.acquire_day(DAY, "B2020", opener=opener, sleep=lambda _: None)
    second = full.acquire_day(DAY, "B2020", opener=opener, sleep=lambda _: None)
    assert first == second
    assert first["status"] == "unverifiable_missing_file"
    assert calls == [full.urls_for_day(DAY)[0]]
    assert not list((full.VERIFY_DIR_FULL / ".partial").glob("*"))


def test_zip_404_preserves_accepted_checksum_without_redownloading():
    base_opener, calls, _, _ = _successful_opener()

    def opener(request, timeout=30):
        if request.full_url.endswith(".CHECKSUM"):
            return base_opener(request, timeout)
        calls.append(request.full_url)
        return Response(b"missing", request.full_url, status=404)

    result = full.acquire_day(DAY, "B2020", opener=opener, sleep=lambda _: None)
    assert result["status"] == "unverifiable_missing_file"
    assert calls == list(full.urls_for_day(DAY))
    assert len(list((full.VERIFY_DIR_FULL / "failed").glob("*"))) == 2
    assert not list((full.VERIFY_DIR_FULL / ".partial").glob("*"))


@pytest.mark.parametrize("bad_checksum", [b"not-checksum", b"0" * 64 + b"  wrong.zip\n", b"\xff"])
def test_checksum_format_failure_stops_before_zip(bad_checksum):
    calls = []

    def opener(request, timeout=30):
        calls.append(request.full_url)
        return Response(bad_checksum, request.full_url)

    with pytest.raises(full.StopTask):
        full.acquire_day(DAY, "B2020", opener=opener, sleep=lambda _: pytest.fail("no retry"))
    assert calls == [full.urls_for_day(DAY)[0]]


def test_full_length_zip_sha_mismatch_stops_whole_task():
    opener, calls, archive, checksum = _successful_opener()

    def changed_opener(request, timeout=30):
        response = opener(request, timeout)
        if request.full_url.endswith(".zip"):
            return Response(archive + b"tampered", request.full_url)
        return response

    with pytest.raises(full.StopTask, match="SHA-256"):
        full.acquire_day(DAY, "B2020", opener=changed_opener, sleep=lambda _: pytest.fail("no retry"))
    assert len(calls) == 2
    assert full.rebuild_ledger()["zip_bytes"] == len(archive) + 8


@pytest.mark.parametrize("error", [PermissionError("denied"), ssl.SSLCertVerificationError("bad cert"),
                                    urllib.error.URLError(PermissionError("wrapped denial")),
                                    urllib.error.URLError(OSError(errno.EIO, "unknown"))])
def test_network_refusal_cert_and_unknown_error_stop_without_retry(error):
    calls = []

    def opener(request, timeout=30):
        calls.append(request.full_url)
        raise error

    with pytest.raises(full.StopTask):
        full.acquire_day(DAY, "B2020", opener=opener, sleep=lambda _: pytest.fail("no retry"))
    assert len(calls) == 1
    assert full.rebuild_ledger()["gets"] == 1


def test_truncated_responses_charge_partial_bytes_on_all_attempts():
    calls = []
    waits = []

    def opener(request, timeout=30):
        calls.append(request.full_url)
        return Response(b"short", request.full_url, content_length=8)

    result = full.acquire_day(DAY, "B2020", opener=opener, sleep=waits.append)
    assert result["status"] == "unverifiable_network"
    assert waits == [1, 4]
    assert full.rebuild_ledger()["checksum_bytes"] == 15
    assert all(event["sha256"] == hashlib.sha256(b"short").hexdigest()
               for event in full.ledger_events() if event["event"] == "settle")


def test_budget_reservation_is_checked_before_intent_or_request(monkeypatch):
    monkeypatch.setattr(full, "TOTAL_CHECKSUM_LIMIT", full.CHECKSUM_LIMIT)
    with pytest.raises(full.StopTask, match="budget"):
        full.acquire_day(DAY, "B2020", opener=lambda *_a, **_k: pytest.fail("budget must precede request"))
    assert full.rebuild_ledger()["gets"] == 0


def test_accepted_url_cannot_be_requested_twice():
    opener, calls, _, _ = _successful_opener()
    full.download_url(DAY, "checksum", "B2020", opener=opener, sleep=lambda _: None)
    with pytest.raises(full.StopTask, match="terminal URL"):
        full.download_url(DAY, "checksum", "B2020", opener=opener, sleep=lambda _: None)
    assert len(calls) == 1


def test_verified_but_abnormal_daily_file_is_retained_and_not_compared():
    rows = [_row(), _row()]
    opener, calls, _, _ = _successful_opener(rows=rows)
    result = full.acquire_day(DAY, "B2020", opener=opener, sleep=lambda _: None)
    assert result["status"] == "unverifiable_abnormal_file"
    assert result["anomalies"]
    assert (full.VERIFY_DIR_FULL / f"BTCUSDT-1m-{DAY}.zip").exists()
    assert len(calls) == 2
    full.verify_download_inventory()


def test_close_before_open_is_retained_as_time_semantics_anomaly():
    row = _row(changes={"close_time": str(int(_row()[0]) - 1)})
    parsed = _parsed(row)
    assert not parsed.abnormal
    reasons = {item["reason"] for item in parsed.anomalies}
    assert {"native_close_span_anomaly", "close_before_open"} <= reasons


def test_microsecond_precision_is_not_rounded_to_milliseconds():
    day = "2025-01-01"
    monthly = _bar(_row(day), day, "monthly")
    daily = _bar(_row(day, changes={"close_time": str(int(_row(day)[6]) - 1)}), day)
    assert monthly.close_end_us - daily.close_end_us == 1
    result = full.classify_rows(monthly, daily, day)
    assert result["severity"] == "timestamp_only"


def test_numeric_conflict_takes_priority_over_timestamp_and_format_only():
    monthly = _bar(_row(), side="monthly")
    daily = _bar(_row(changes={"close": "100.01", "open": "100.0", "close_time": str(int(_row()[6]) - 1)}))
    result = full.classify_rows(monthly, daily, DAY)
    assert result["severity"] == "minor"
    assert set(result["conflict_fields"]) == {"open", "close", "close_time"}


def test_all_three_placeholder_crosstabs_preserve_missing_and_traded_states():
    placeholder = {"volume": "0", "number_of_trades": "0", "quote_asset_volume": "0",
                   "taker_buy_base_asset_volume": "0", "taker_buy_quote_asset_volume": "0"}
    monthly = full.parse_daily_csv(_csv([_row(minute=0, changes=placeholder),
                                        _row(minute=1, changes=placeholder), _row(minute=2)]), DAY, "monthly")
    daily = full.parse_daily_csv(_csv([_row(minute=0), _row(minute=2, changes=placeholder),
                                      _row(minute=3, changes=placeholder)]), DAY)
    result = full.compare_day(DAY, monthly, daily)
    cross = result["placeholder_crosstab"]
    assert cross["monthly_placeholder"] == {"daily_missing": 1, "daily_placeholder": 0, "daily_traded": 1}
    assert cross["monthly_missing"] == {"daily_missing": 1436, "daily_placeholder": 1, "daily_traded": 0}
    assert cross["daily_placeholder"] == {"monthly_missing": 1, "monthly_placeholder": 0, "monthly_traded": 1}


def test_intervals_connect_midnight_month_end_but_respect_time_gaps():
    last = _start_us("2020-01-31") + 1439 * 60_000_000
    rows = [{"open_time_us": last + i * 60_000_000, "classification": "value_conflict", "severity": "minor"}
            for i in (0, 1, 3)]
    intervals = full.merge_intervals(rows)
    assert len(intervals) == 2
    assert intervals[0]["minutes"] == 2
    assert intervals[0]["start_utc"] == "2020-01-31T23:59:00Z"
    assert intervals[0]["end_utc"] == "2020-02-01T00:00:00Z"


def test_episodes_connect_sixty_minutes_inclusively_and_require_verified_dates():
    last = _start_us("2020-01-31") + 1439 * 60_000_000
    rows = [{"day": "2020-01-31", "open_time_us": last, "classification": "value_conflict", "severity": "minor"},
            {"day": "2020-02-01", "open_time_us": last + 60 * 60_000_000, "classification": "extra_in_monthly", "severity": "structural"},
            {"day": "2020-02-01", "open_time_us": last + 121 * 60_000_000, "classification": "value_conflict", "severity": "moderate"}]
    complete = full.merge_episodes(rows, {"2020-01-31", "2020-02-01"})
    assert [r["conflict_minutes"] for r in complete] == [2, 1]
    missing = full.merge_episodes(rows, {"2020-01-31"})
    assert len(missing) == 3


def test_neighbour_continuity_is_row_local_and_only_for_conflicts():
    monthly = full.parse_daily_csv(_csv([_row(minute=0), _row(minute=1), _row(minute=2)]), DAY, "monthly")
    daily = full.parse_daily_csv(_csv([_row(minute=0), _row(minute=1, changes={"open": "101", "close": "102"}), _row(minute=2)]), DAY)
    result = full.compare_day(DAY, monthly, daily)
    assert len(result["conflict_minutes"]) == 1
    conflict = result["conflict_minutes"][0]
    assert Decimal(conflict["daily_previous_gap_bp"]) == Decimal(100)
    assert Decimal(conflict["daily_next_gap_bp"]) == abs((Decimal(100) - Decimal(102)) / Decimal(102) * Decimal(10000))
    assert "daily_previous_gap_bp" not in result["minutes"][0]


def test_linear_quantiles_and_histogram_boundaries_are_fixed():
    quantiles = full._linear_quantiles([Decimal(0), Decimal(10)])
    assert Decimal(quantiles["0.5"]) == Decimal(5)
    assert Decimal(quantiles["0.9"]) == Decimal(9)
    assert Decimal(quantiles["0.99"]) == Decimal("9.9")
    assert Decimal(quantiles["1"]) == Decimal(10)
    bins = full._histogram([Decimal(0), Decimal(".01"), Decimal(1)], ("0", "0.01", "1.0"))
    assert bins["counts"] == [1, 2]
    assert bins["final_bin_includes_right"]


def test_partial_timeout_keeps_received_bytes_in_ledger():
    waits = []

    def opener(request, timeout=30):
        return Response(b"abcdef", request.full_url, fail_after=2, error=TimeoutError("after prefix"))

    result = full.acquire_day(DAY, "B2020", opener=opener, sleep=waits.append)
    assert result["status"] == "unverifiable_network"
    assert full.rebuild_ledger()["checksum_bytes"] == 6
    assert waits == [1, 4]
    settles = [e for e in full.ledger_events() if e["event"] == "settle"]
    assert [e["bytes"] for e in settles] == [2, 2, 2]
    assert all(e["sha256"] == hashlib.sha256(b"ab").hexdigest() for e in settles)


def test_next_batch_requires_previous_completion_and_same_commit(monkeypatch):
    monkeypatch.setattr(full, "git_preflight", lambda **kwargs: "synthetic-c1")
    full.append_event({"event": "run_start", "commit": "synthetic-c1", "utc": "2026-11-10T00:00:00Z"})
    with pytest.raises(full.StopTask, match="previous batch"):
        full.acquire_batch(2021, opener=lambda *_a, **_k: pytest.fail("no request without prior batch"))
    full.append_event({"event": "batch_complete", "batch": "B2020", "utc": "2026-11-10T00:00:01Z"})
    monkeypatch.setattr(full, "git_preflight", lambda **kwargs: "changed-code")
    with pytest.raises(full.StopTask, match="commit"):
        full.acquire_batch(2021, opener=lambda *_a, **_k: pytest.fail("no request from changed code"))


def test_synthetic_first_batch_reconciles_before_requests_and_marks_complete(monkeypatch):
    order = []
    monkeypatch.setattr(full, "D041_DAYS", (DAY,))
    monkeypatch.setattr(full, "preflight", lambda **kwargs: order.append("preflight") or {})
    monkeypatch.setattr(full, "reconcile_d039", lambda *args, **kwargs: order.append("reconcile") or {"passed": True})
    monkeypatch.setattr(full, "isolation_snapshot", lambda: order.append("snapshot") or {"synthetic": True})
    monkeypatch.setattr(full, "assert_isolation", lambda *args, **kwargs: None)
    monkeypatch.setattr(full, "git_preflight", lambda **kwargs: "synthetic-c1")
    monkeypatch.setattr(full, "check_memory", lambda: 1000)
    base, calls, archive, checksum = _successful_opener()

    def opener(request, timeout=30):
        assert order == ["preflight", "reconcile", "snapshot"]
        assert (full.EVIDENCE_DIR / "d039-reconciliation.json").exists()
        return base(request, timeout)

    summary = full.acquire_batch(2020, opener=opener, sleep=lambda _: None)
    assert summary["batch_complete"] == ["B2020"]
    assert summary["counters"]["gets"] == 2
    assert summary["counters"]["zip_bytes"] == len(archive)
    assert json.loads((full.EVIDENCE_DIR / "download-summary.json").read_bytes()) == summary
    assert full.ledger_events()[-1]["event"] == "batch_complete"


def test_reconciliation_failure_stops_before_creating_outputs(monkeypatch):
    monkeypatch.setattr(full, "preflight", lambda **kwargs: {})
    monkeypatch.setattr(full, "reconcile_d039", lambda *args, **kwargs: {"passed": False})
    with pytest.raises(full.StopTask, match="reconciliation"):
        full.acquire_batch(2020, opener=lambda *_a, **_k: pytest.fail("reconciliation precedes networking"))
    assert not full.VERIFY_DIR_FULL.exists()
    assert not full.EVIDENCE_DIR.exists()


def test_twenty_first_unverifiable_day_stops_batch(monkeypatch):
    days = tuple((date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(22))
    monkeypatch.setattr(full, "D041_DAYS", days)
    monkeypatch.setattr(full, "preflight", lambda **kwargs: {})
    monkeypatch.setattr(full, "reconcile_d039", lambda *args, **kwargs: {"passed": True})
    monkeypatch.setattr(full, "isolation_snapshot", lambda: {})
    monkeypatch.setattr(full, "assert_isolation", lambda *args, **kwargs: None)
    monkeypatch.setattr(full, "git_preflight", lambda **kwargs: "synthetic-c1")
    monkeypatch.setattr(full, "check_memory", lambda: 1000)
    calls = []

    def opener(request, timeout=30):
        calls.append(request.full_url)
        return Response(b"missing", request.full_url, status=404)

    with pytest.raises(full.StopTask, match="more than 20"):
        full.acquire_batch(2020, opener=opener, sleep=lambda _: None)
    assert len(calls) == 21
    assert full.rebuild_ledger()["unverifiable_dates"] == 21
    assert not full.rebuild_ledger()["batch_complete"]


def test_unexpected_file_in_full_directory_blocks_continuation():
    full.VERIFY_DIR_FULL.mkdir(parents=True)
    (full.VERIFY_DIR_FULL / "unexpected.txt").write_bytes(b"synthetic unexpected file")
    with pytest.raises(full.StopTask, match="differs from ledger"):
        full.verify_download_inventory()
    assert (full.VERIFY_DIR_FULL / "unexpected.txt").exists()


def test_get_limit_does_not_write_intent_or_call_opener(monkeypatch):
    monkeypatch.setattr(full, "GET_LIMIT", 0)
    with pytest.raises(full.StopTask, match="budget"):
        full.acquire_day(DAY, "B2020", opener=lambda *_a, **_k: pytest.fail("no budget"))
    assert full.ledger_events() == []


def test_content_length_above_remaining_budget_stops_without_reading():
    response = Response(b"abc", "https://unused.invalid")
    with pytest.raises(full.StopTask, match="budget"):
        full.read_response(response, "checksum", 2, 120, clock=lambda: 0)
    assert response.offset == 0


def test_malformed_incomplete_ledger_is_not_recovered_or_truncated():
    full.EVIDENCE_DIR.mkdir(parents=True)
    path = full.EVIDENCE_DIR / full.LEDGER_NAME
    payload = b'{"event":"intent"'
    path.write_bytes(payload)
    with pytest.raises(full.StopTask, match="incomplete"):
        full.rebuild_ledger()
    assert path.read_bytes() == payload


def _cli():
    path = Path(__file__).resolve().parents[1] / "scripts/verify_daily_archives_full.py"
    spec = importlib.util.spec_from_file_location("d041_cli_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_cli_compression_threshold_and_gzip_are_deterministic():
    import gzip
    cli = _cli()
    boundary = b"x" * (20 * 1024**2)
    first = cli.prepare_artifacts({"exact.csv": boundary, "large.csv": boundary + b"x", "derived/minute-flags.csv.gz": b"already-compressed"})
    second = cli.prepare_artifacts({"exact.csv": boundary, "large.csv": boundary + b"x", "derived/minute-flags.csv.gz": b"already-compressed"})
    assert first == second
    assert "exact.csv" in first and "large.csv.gz" in first
    assert first["large.csv.gz"][1][4:8] == b"\x00\x00\x00\x00"
    assert gzip.decompress(first["large.csv.gz"][1]) == boundary + b"x"
    assert first["derived/minute-flags.csv.gz"][0] == full.VERIFY_DIR_FULL / "derived/minute-flags.csv.gz"


def test_cli_evidence_budget_moves_large_files_to_external_derived(monkeypatch):
    cli = _cli()
    full.EVIDENCE_DIR.mkdir(parents=True)
    existing = full.EVIDENCE_DIR / "existing.bin"
    existing.write_bytes(b"x")
    original = Path.stat

    def stat(self, *args, **kwargs):
        actual = original(self, *args, **kwargs)
        if self == existing:
            return types.SimpleNamespace(st_mode=actual.st_mode, st_size=100 * 1024**2, st_mtime_ns=actual.st_mtime_ns)
        return actual

    monkeypatch.setattr(Path, "stat", stat)
    prepared = cli.prepare_artifacts({"big.csv": b"1234", "small.csv": b"1"})
    assert all(target.is_relative_to(full.VERIFY_DIR_FULL / "derived") for target, _ in prepared.values())
    assert not (full.VERIFY_DIR_FULL / "derived").exists()


def test_cli_compare_then_replay_writes_only_replay_metadata(monkeypatch):
    import gzip
    cli = _cli()
    full.EVIDENCE_DIR.mkdir(parents=True)
    before = {"synthetic": "isolation"}
    (full.EVIDENCE_DIR / "isolation-before.json").write_bytes(full.json_bytes(before))
    reconciliation = full.json_bytes({"passed": True, "synthetic": True})
    (full.EVIDENCE_DIR / "d039-reconciliation.json").write_bytes(reconciliation)
    artifacts = {"daily-integrity.csv": b"day,status\n2020-01-01,verified\n",
                 "d039-reconciliation.json": reconciliation,
                 "derived/minute-flags.csv.gz": gzip.compress(b"minute,flag\n0,0\n", mtime=0)}
    monkeypatch.setattr(cli, "offline_preflight", lambda: ({}, {}))
    monkeypatch.setattr(full, "run_comparison", lambda *args, **kwargs: artifacts)
    monkeypatch.setattr(full, "isolation_snapshot", lambda: before)
    monkeypatch.setattr(full, "assert_isolation", lambda *args, **kwargs: None)
    monkeypatch.setattr(full, "inventory", lambda: [])
    monkeypatch.setattr(full, "check_memory", lambda: 1)
    cli.compare()
    preserved = {p: p.read_bytes() for root in (full.EVIDENCE_DIR, full.VERIFY_DIR_FULL)
                 for p in root.rglob("*") if p.is_file()}
    cli.replay_check()
    assert all(p.read_bytes() == payload for p, payload in preserved.items())
    new = {p.name for p in full.EVIDENCE_DIR.iterdir() if p not in preserved}
    assert new == {"determinism-check.json", "runtime-replay.json"}
    assert json.loads((full.EVIDENCE_DIR / "determinism-check.json").read_bytes())["passed"]


def test_cli_replay_mismatch_stops_without_publishing_success(monkeypatch):
    cli = _cli()
    full.EVIDENCE_DIR.mkdir(parents=True)
    (full.EVIDENCE_DIR / "daily-integrity.csv").write_bytes(b"original")
    (full.EVIDENCE_DIR / "artifact-index.json").write_bytes(full.json_bytes({
        "daily-integrity.csv": {"root": "evidence", "path": "daily-integrity.csv", "bytes": 8,
                                "sha256": full.sha256(b"original")}}))
    monkeypatch.setattr(cli, "offline_preflight", lambda: ({}, {}))
    monkeypatch.setattr(full, "run_comparison", lambda *args, **kwargs: {"daily-integrity.csv": b"different"})
    with pytest.raises(full.StopTask, match="replay differs"):
        cli.replay_check()
    assert not (full.EVIDENCE_DIR / "determinism-check.json").exists()
    assert (full.EVIDENCE_DIR / "daily-integrity.csv").read_bytes() == b"original"


def test_cli_refusal_stops_once_without_retry_or_fallback(monkeypatch):
    import sys
    cli = _cli()
    calls = []
    denied = PermissionError("synthetic refusal")

    def acquire(year):
        calls.append(year)
        raise denied

    stops = []
    monkeypatch.setattr(full, "acquire_batch", acquire)
    monkeypatch.setattr(full, "write_stop", lambda exc, phase: stops.append((exc, phase)) or full.EVIDENCE_DIR / "stop-20261110T000000Z.json")
    monkeypatch.setattr(sys, "argv", ["verify_daily_archives_full.py", "acquire", "--batch", "2020"])
    assert cli.main() == 1
    assert calls == [2020]
    assert stops == [(denied, "B2020")]


def test_cli_stop_record_write_refusal_propagates_without_fallback(monkeypatch):
    import sys
    cli = _cli()
    denied = PermissionError("stop recording refused")
    monkeypatch.setattr(full, "acquire_batch", lambda year: (_ for _ in ()).throw(full.StopTask("synthetic task stop")))
    calls = []

    def stop(exc, phase):
        calls.append(phase)
        raise denied

    monkeypatch.setattr(full, "write_stop", stop)
    monkeypatch.setattr(sys, "argv", ["verify_daily_archives_full.py", "acquire", "--batch", "2020"])
    with pytest.raises(PermissionError) as caught:
        cli.main()
    assert caught.value is denied
    assert calls == ["B2020"]


def test_cached_checksum_is_reused_but_missing_zip_is_not_re_requested(monkeypatch):
    opener, calls, _, _ = _successful_opener()
    checksum_url = full.urls_for_day(DAY)[0]
    checksum_response = opener(urllib.request.Request(checksum_url), 30)
    checksum_payload = checksum_response.read()
    full.append_event({"event": "intent", "id": "000001", "batch": "B2020", "day": DAY,
                       "url": checksum_url, "kind": "checksum", "attempt": 1,
                       "reserved_bytes": full.CHECKSUM_LIMIT + 1, "utc": "2026-11-10T00:00:00Z"})
    checksum_path = full.VERIFY_DIR_FULL / ".partial" / "000001.bin"
    checksum_path.parent.mkdir(parents=True)
    checksum_path.write_bytes(checksum_payload)
    full.append_event({"event": "settle", "id": "000001", "utc": "2026-11-10T00:00:01Z",
                       "http_status": 200, "final_url": checksum_url, "content_length": len(checksum_payload),
                       "bytes": len(checksum_payload), "sha256": full.sha256(checksum_payload),
                       "last_modified": None, "error_category": None, "error": None,
                       "result": "accepted", "file": {"path": ".partial/000001.bin", "bytes": len(checksum_payload), "sha256": full.sha256(checksum_payload)}})
    with pytest.raises(full.StopTask):
        full.acquire_day(DAY, "B2020", opener=lambda request, timeout=30: (_ for _ in ()).throw(AssertionError("checksum must be cached")), sleep=lambda _: None)


def test_first_offline_inventory_accepts_missing_artifact_index():
    opener, _, _, _ = _successful_opener()
    assert full.acquire_day(DAY, "B2020", opener=opener, sleep=lambda _: None)["status"] == "verified"
    before = {path: path.read_bytes() for path in full.VERIFY_DIR_FULL.rglob("*") if path.is_file()}
    assert not (full.EVIDENCE_DIR / "artifact-index.json").exists()
    full.verify_download_inventory(acquisition_only=False)
    assert all(path.read_bytes() == payload for path, payload in before.items())
    assert not (full.EVIDENCE_DIR / "artifact-index.json").exists()


@pytest.mark.parametrize("tampered", [False, True])
def test_offline_inventory_verifies_indexed_external_derived_bytes(tampered):
    payload = b"synthetic derived bytes"
    relative = "derived/minute-flags.csv.gz"
    target = full.VERIFY_DIR_FULL / relative
    target.parent.mkdir(parents=True)
    target.write_bytes((b"X" + payload[1:]) if tampered else payload)
    full.EVIDENCE_DIR.mkdir(parents=True)
    index_path = full.EVIDENCE_DIR / "artifact-index.json"
    index = full.json_bytes({relative: {"root": "external", "path": relative,
                                      "bytes": len(payload), "sha256": full.sha256(payload)}})
    index_path.write_bytes(index)
    if tampered:
        with pytest.raises(full.StopTask, match="derived file inventory"):
            full.verify_download_inventory(acquisition_only=False)
    else:
        full.verify_download_inventory(acquisition_only=False)
    assert index_path.read_bytes() == index
    assert target.read_bytes() == ((b"X" + payload[1:]) if tampered else payload)


@pytest.mark.parametrize("fields", [("open_time",), ("close_time",), ("open_time", "close_time")])
def test_oversized_integer_timestamps_return_recorded_abnormal_day(fields):
    parsed = _parsed(_row(changes={field: str(10**1000) for field in fields}))
    assert parsed.abnormal
    assert not parsed.rows
    assert parsed.anomalies
    assert all(item["reason"] for item in parsed.anomalies)
    if "open_time" in fields:
        assert all(item["open_time_utc"] is None for item in parsed.anomalies)


def test_incomplete_read_counts_repeated_prefix_as_additional_bytes():
    response = Response(b"ababxx", "https://unused.invalid", fail_after=2,
                        error=http.client.IncompleteRead(b"ab", 2))
    with pytest.raises(http.client.IncompleteRead) as caught:
        full.read_response(response, "checksum", 20, 120, clock=lambda: 0)
    assert caught.value.payload == b"abab"
    assert len(caught.value.payload) == 4
    assert caught.value.content_length == 6


def test_comparison_outputs_preserve_uncomparable_status_and_unverified_monthly_anomalies(monkeypatch):
    import gzip
    second_day = "2020-01-02"
    days = (DAY, second_day)
    monthly = {DAY: _parsed(_row(), side="monthly"),
               second_day: _parsed(_row(second_day, changes={"close_time": str(_start_us(second_day) // 1000 + 59_998)}),
                                   second_day, side="monthly")}
    daily = {DAY: _parsed(_row(unit="us")), second_day: None}
    daily_anomaly = {"day": second_day, "side": "daily", "row_number": 1,
                     "open_time_us": _start_us(second_day), "open_time_utc": second_day + "T00:00:00Z",
                     "reason": "synthetic_bad_daily_row", "raw_row": "synthetic"}
    statuses = {DAY: {"status": "verified"},
                second_day: {"status": "unverifiable_abnormal_file", "anomalies": [daily_anomaly]}}
    monkeypatch.setattr(full, "ALL_DAYS", days)
    monkeypatch.setattr(full, "D039_DAYS", ())
    monkeypatch.setattr(full, "_d039_statuses", lambda: {})
    monkeypatch.setattr(full, "_compare_d039_from_results", lambda results: {"passed": True})
    monkeypatch.setattr(full, "_monthly_days", lambda entry: monthly)
    monkeypatch.setattr(full, "_load_daily", lambda day, status, source: (daily[day], {}))
    monkeypatch.setattr(full, "check_memory", lambda: 1)
    artifacts = full.run_comparison({"months": [{"month": "2020-01", "zip_sha256": "synthetic-month"}]},
                                    {"dates": statuses})
    flags = list(csv.DictReader(io.StringIO(gzip.decompress(artifacts["derived/minute-flags.csv.gz"]).decode("utf8"))))
    assert len(flags) == 2880
    assert flags[0]["status"] == "time_semantics_uncomparable"
    assert flags[0]["verified"] == "True" and flags[0]["classification"] == ""
    assert flags[1440]["status"] == "unverifiable_abnormal_file"
    assert flags[1440]["verified"] == "False"
    integrity = list(csv.DictReader(io.StringIO(artifacts["daily-integrity.csv"].decode("utf8"))))
    assert integrity[0]["unit_incomparable_minutes"] == "1"
    assert integrity[1]["time_semantics_events"] == "2"
    assert integrity[1]["time_semantics_rows"] == "2"
    assert integrity[1]["time_semantics_minutes"] == "1"
    anomalies = list(csv.DictReader(io.StringIO(artifacts["timestamp-anomalies.csv"].decode("utf8"))))
    assert {(item["side"], item["reason"]) for item in anomalies if item["day"] == second_day} == {
        ("monthly", "native_close_span_anomaly"), ("daily", "synthetic_bad_daily_row")}


def test_day_summary_has_explicit_cohorts_for_both_sources():
    days = (DAY, "2020-01-02")
    rows = [{"day": days[0], "source": "D-041", "status": "verified", "both_equal": 1440},
            {"day": days[1], "source": "D-039", "status": "verified", "both_equal": 1439}]
    grouped = full._summary_groups(rows, "day")
    assert len(grouped) == 6
    by_key = {(row["day"], row["source"]): row for row in grouped}
    assert set(by_key) == {(day, source) for day in days for source in ("D-041", "D-039", "combined")}
    for day, source, count in ((days[0], "D-041", 1440), (days[1], "D-039", 1439)):
        assert by_key[day, source]["dates"] == 1
        assert by_key[day, "combined"]["both_equal"] == count
        other = "D-039" if source == "D-041" else "D-041"
        assert by_key[day, other]["dates"] == by_key[day, other]["both_equal"] == 0
