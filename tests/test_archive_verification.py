"""Synthetic tests for the D-039 daily archive verifier.

These tests never touch the real verification directory and never make a network
request.  They exercise the byte preserving parser/comparison layer and the
guards used by the downloader.
"""

from __future__ import annotations

import csv
import hashlib
import io
import importlib.util
import json
import socket
import types
import urllib.error
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from quantforge import archive_verification as av


DAY = "2020-11-30"
BASE_TS = 1_606_694_400_000  # 2020-11-30 00:00:00 UTC, integer milliseconds


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError("Real network requests are prohibited in synthetic tests")
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(av.urllib.request, "urlopen", denied)


@pytest.fixture(autouse=True)
def isolated_evidence_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(av, "EVIDENCE_DIR", tmp_path / "evidence")


def _row(minute: int, *, placeholder: bool = False, close: str = "100.00000000") -> tuple[str, ...]:
    open_time = str(BASE_TS + minute * 60_000)
    close_time = str(BASE_TS + minute * 60_000 + 59_999)
    if placeholder:
        return (open_time, "0", "0", "0", "0", "0", close_time, "0", "0", "0", "0", "0")
    return (
        open_time, close, close, close, close, "1.00000000", close_time,
        "100.00000000", "1", "0.50000000", "50.00000000", "0",
    )


def _csv(rows: list[tuple[str, ...]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _zip(day: str, csv_bytes: bytes) -> bytes:
    name = f"BTCUSDT-1m-{day}.csv"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, csv_bytes)
    return out.getvalue()


def _response(body: bytes, *, status: int = 200, url: str | None = None, last_modified: str | None = None):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _limit=-1):
            return body

        @property
        def status(self):
            return status

        @property
        def headers(self):
            return {"Last-Modified": last_modified} if last_modified else {}

        @property
        def url(self):
            return url or ""

        def geturl(self):
            return self.url

    return Response()


def test_parse_rows_uses_integer_milliseconds_and_preserves_strings():
    rows = [_row(0), _row(1, close="101.25000000")]
    parsed = av.parse_rows(_csv(rows), DAY)
    assert list(parsed) == [BASE_TS, BASE_TS + 60_000]
    assert parsed[BASE_TS][1] == "100.00000000"
    assert parsed[BASE_TS + 60_000][1] == "101.25000000"


def test_compare_day_reports_all_five_classes_and_field_deltas():
    monthly = {BASE_TS + i * 60_000: _row(i) for i in range(6)}
    daily = dict(monthly)
    # minute 1 is present on both sides but differs in price and volume.
    daily[BASE_TS + 60_000] = _row(1, close="101.00000000")
    # minute 2 occurs only in the daily archive.
    daily[BASE_TS + 2 * 60_000] = _row(2)
    monthly.pop(BASE_TS + 2 * 60_000)
    # minute 3 occurs only in the monthly archive.
    daily.pop(BASE_TS + 3 * 60_000)
    # minute 4 is absent on both sides.
    daily.pop(BASE_TS + 4 * 60_000)
    monthly.pop(BASE_TS + 4 * 60_000)
    result = av.compare_day(DAY, monthly, daily)
    assert result["counts"]["both_equal"] == 2
    assert result["counts"]["value_conflict"] == 1
    assert result["counts"]["missing_in_monthly"] == 1
    assert result["counts"]["extra_in_monthly"] == 1
    assert result["counts"]["both_missing"] == 1_435
    assert result["field_conflicts"]["close"] == 1
    assert result["minute_diffs"]
    assert result["minute_diffs"][0]["classification"] == "value_conflict"
    differences = result["minute_diffs"][0]["field_differences"]
    assert differences == [
        {"field": name, "monthly": "100.00000000", "daily": "101.00000000",
         "difference": "1.00000000", "difference_bp": "100.00"}
        for name in ("open", "high", "low", "close")
    ]


def test_placeholder_crosstab_and_window_annotations():
    monthly = {BASE_TS + i * 60_000: _row(i) for i in range(4)}
    daily = dict(monthly)
    monthly[BASE_TS] = _row(0, placeholder=True)
    daily[BASE_TS] = _row(0, placeholder=True)
    monthly.pop(BASE_TS + 60_000)
    daily[BASE_TS + 60_000] = _row(1, placeholder=True)
    result = av.compare_day(DAY, monthly, daily)
    assert result["placeholder_crosstab"]["monthly_placeholder"] == {
        "daily_missing": 0, "daily_placeholder": 1, "daily_traded": 0,
    }
    assert result["placeholder_crosstab"]["monthly_missing"] == {
        "daily_missing": 1436, "daily_placeholder": 1, "daily_traded": 0,
    }
    assert result["placeholder_crosstab"]["daily_placeholder"] == {
        "monthly_missing": 1, "monthly_placeholder": 1, "monthly_traded": 0,
    }
    assert result["window_counts"] == {"inside_window": 60, "outside_window": 1377}
    assert any(item["classification"] == "missing_in_monthly" for item in result["minute_diffs"])


def test_intervals_merge_identical_minute_signatures():
    monthly = {BASE_TS + i * 60_000: _row(i) for i in range(5)}
    daily = dict(monthly)
    for i in (1, 2, 3):
        daily.pop(BASE_TS + i * 60_000)
    result = av.compare_day(DAY, monthly, daily)
    intervals = [item for item in result["intervals"] if item["classification"] == "extra_in_monthly"]
    assert len(intervals) == 1
    assert intervals[0]["length"] == 3


def test_whitelist_and_holdout_guards():
    assert av.ensure_url(f"https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-{DAY}.zip")[0] == DAY
    with pytest.raises(Exception):
        av.ensure_url("https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2020-11-29.zip")
    with pytest.raises(Exception):
        av.ensure_url("https://example.invalid/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2020-11-30.zip")
    with pytest.raises(Exception):
        av.ensure_day("2026-09-01")
    with pytest.raises(Exception):
        av.ensure_day("2026-07-01")


def test_safe_paths_reject_outside_and_symlink(tmp_path, monkeypatch):
    root = tmp_path / "verify"
    root.mkdir()
    monkeypatch.setattr(av, "VERIFY_DIR", root)
    assert av.safe_verify_path(root / "ok.bin") == root / "ok.bin"
    with pytest.raises(Exception):
        av.safe_verify_path(tmp_path / "outside.bin")
    link = root / "link"
    link.mkdir()
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == link or original_is_symlink(self))
    with pytest.raises(Exception):
        av.safe_verify_path(link / "x.bin")


def test_nonempty_verify_dir_is_rejected(tmp_path, monkeypatch):
    root = tmp_path / "verify"
    root.mkdir()
    (root / "leftover").write_bytes(b"x")
    monkeypatch.setattr(av, "VERIFY_DIR", root)
    with pytest.raises(Exception):
        av.require_empty_verify_dir()


def test_network_spy_is_not_touched_by_comparison(monkeypatch):
    called = False

    def fail(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("synthetic comparison must not perform network I/O")

    monkeypatch.setattr(av.urllib.request, "urlopen", fail)
    av.compare_day(DAY, {BASE_TS: _row(0)}, {BASE_TS: _row(0)})
    assert not called


def test_zip_and_checksum_helpers_match_official_bytes():
    csv_bytes = _csv([_row(0)])
    archive = _zip(DAY, csv_bytes)
    name = f"BTCUSDT-1m-{DAY}.zip"
    checksum = f"{hashlib.sha256(archive).hexdigest()}  {name}\n"
    assert av.verify_checksum(archive, checksum.encode(), name) == hashlib.sha256(archive).hexdigest()
    with pytest.raises(Exception):
        av.verify_checksum(archive + b"x", checksum.encode(), name)


def test_download_rejects_redirect_and_404_without_retry(monkeypatch, tmp_path):
    monkeypatch.setattr(av, "VERIFY_DIR", tmp_path / "verify")
    av.VERIFY_DIR.mkdir()
    seen = []

    def opener(request, timeout=30):
        seen.append(request.full_url)
        if request.full_url.endswith(".CHECKSUM"):
            return _response(b"", status=404, url=request.full_url)
        raise AssertionError("ZIP must not be requested after a 404 checksum")

    with pytest.raises(av.DayFailure):
        av.download_one(DAY, opener=opener, sleep=lambda _: None,
                        root=av.VERIFY_DIR)
    assert len(seen) == 1


def test_budget_exceeded_stops_whole_run(monkeypatch, tmp_path):
    monkeypatch.setattr(av, "VERIFY_DIR", tmp_path / "verify")
    av.VERIFY_DIR.mkdir()
    monkeypatch.setattr(av, "TOTAL_CHECKSUM_LIMIT", 1)

    def opener(request, timeout=30):
        return _response(b"x" * 2, url=request.full_url)

    with pytest.raises(av.BudgetExceeded):
        av.download_one(DAY, opener=opener, sleep=lambda _: None,
                        root=av.VERIFY_DIR)


@pytest.mark.parametrize("bad_value", ["NaN", "Infinity", "-Infinity", "nonnumeric"])
def test_nonfinite_or_nonnumeric_fields_are_rejected(bad_value):
    row = list(_row(0))
    row[5] = bad_value
    with pytest.raises(av.ArchiveVerificationError):
        av.parse_rows(_csv([tuple(row)]), DAY)


@pytest.mark.parametrize("mutation", ["duplicate", "off_grid", "outside_day", "microseconds"])
def test_duplicate_timestamp_grid_range_and_unit_errors_are_rejected(mutation):
    rows = [_row(0)]
    if mutation == "duplicate":
        rows.append(_row(0))
    else:
        changed = list(rows[0])
        if mutation == "off_grid":
            changed[0] = str(int(changed[0]) + 1)
        elif mutation == "outside_day":
            changed[0] = str(BASE_TS - 60_000)
        else:
            changed[0] = str(int(changed[0]) * 1000)
            changed[6] = str(int(changed[6]) * 1000)
        rows[0] = tuple(changed)
    with pytest.raises(av.ArchiveVerificationError):
        av.parse_rows(_csv(rows), DAY)


def test_numeric_zero_placeholder_and_close_time_anomaly():
    row = list(_row(0, placeholder=True))
    row[5] = "0.00000000"
    row[8] = "00"
    row[6] = str(BASE_TS + 59_998)
    flags = av.row_flags(tuple(row))
    assert flags == {"zero_trade_placeholder": True, "close_time_anomaly": True}


def test_field_diff_types_and_zero_denominators():
    monthly = list(_row(0))
    daily = list(_row(0))
    monthly[1] = "0.00000000"
    daily[1] = "1.00000000"
    monthly[5] = "0.00000000"
    daily[5] = "2.00000000"
    daily[8] = "4"
    daily[6] = str(BASE_TS + 59_998)
    daily[11] = "2"
    result = av.compare_day(DAY, {BASE_TS: tuple(monthly)}, {BASE_TS: tuple(daily)})
    diffs = {item["field"]: item for item in result["minute_diffs"][0]["field_differences"]}
    assert diffs["open"]["difference"] == "1.00000000"
    assert diffs["open"].get("difference_bp") is None
    assert diffs["volume"]["difference"] == "2.00000000"
    assert diffs["volume"]["ratio"] is None
    assert diffs["number_of_trades"]["difference"] == 3
    assert diffs["close_time"]["difference"] == -1
    assert diffs["ignore"]["difference"] == "2"


def test_inside_outside_window_inclusive_endpoints():
    # Make otherwise identical complete synthetic days, then four conflicts.
    monthly = {BASE_TS + i * 60_000: _row(i) for i in range(1440)}
    daily = dict(monthly)
    for minute in (359, 360, 419, 420):
        daily[BASE_TS + minute * 60_000] = _row(minute, close="101.00000000")
    result = av.compare_day(DAY, monthly, daily)
    assert result["window_counts"] == {"inside_window": 2, "outside_window": 2}
    assert [item["inside_window"] for item in result["minute_diffs"]] == [False, True, True, False]


@pytest.mark.parametrize("failure", ["redirect", "size", "checksum", "404"])
def test_date_failure_does_not_stop_next_date(failure, monkeypatch, tmp_path):
    monkeypatch.setattr(av, "VERIFY_DIR", tmp_path / "verify")
    first, second = DAY, "2020-12-21"
    archive_1 = _zip(first, _csv([_row(0)]))
    second_ts = int((datetime(2020, 12, 21, tzinfo=timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds()) * 1000
    row_2 = list(_row(0)); row_2[0] = str(second_ts); row_2[6] = str(second_ts + 59_999)
    archive_2 = _zip(second, _csv([tuple(row_2)]))
    seen = []

    def opener(request, timeout=30):
        url = request.full_url; seen.append(url)
        assert request.get_method() == "GET"
        assert timeout == 30
        day, kind = av.ensure_url(url)
        if day == first and failure == "404":
            return _response(b"", status=404, url=url)
        if day == first and failure == "redirect":
            return _response(b"", url=url + "?redirected")
        archive = archive_1 if day == first else archive_2
        if kind == "checksum":
            digest = "0" * 64 if day == first and failure == "checksum" else hashlib.sha256(archive).hexdigest()
            body = f"{digest}  BTCUSDT-1m-{day}.zip\n".encode()
        else:
            body = b"X" * (av.ZIP_LIMIT + 1) if day == first and failure == "size" else archive
        return _response(body, url=url)

    result = av.run_downloads(opener=opener, sleep=lambda _: None, dates=(first, second))
    statuses = {day: item["status"] for day, item in result["dates"].items()}
    assert statuses[first] == "failed"
    assert statuses[second] == "passed"
    assert len([url for url in seen if first in url]) == (2 if failure in ("checksum", "size") else 1)
    assert len([url for url in seen if second in url]) == 2


def test_get_budget_stops_before_next_network_request(monkeypatch, tmp_path):
    monkeypatch.setattr(av, "VERIFY_DIR", tmp_path / "verify")
    called = []

    def opener(request, timeout=30):
        called.append(request.full_url)
        raise AssertionError("GET budget must be checked before network")

    with pytest.raises(av.BudgetExceeded):
        av.download_one(DAY, opener=opener, sleep=lambda _: None,
                        counters={"gets": av.GET_LIMIT, "zip_bytes": 0, "checksum_bytes": 0})
    assert not called


def test_gate1_paths_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(av, "VERIFY_DIR", tmp_path / "verify")
    gate1 = Path(__file__).resolve().parents[1] / "data/raw/binance/spot/BTCUSDT/1m/historical/2020-11.zip"
    with pytest.raises(av.ArchiveVerificationError):
        av.safe_verify_path(gate1)


def test_monthly_parser_selects_only_requested_day():
    earlier = list(_row(0)); earlier[0] = str(BASE_TS - 60_000); earlier[6] = str(BASE_TS - 1)
    later = list(_row(0)); later[0] = str(BASE_TS + 86_400_000); later[6] = str(BASE_TS + 86_459_999)
    parsed = av.parse_rows(_csv([tuple(earlier), _row(0), tuple(later)]), DAY, monthly=True)
    assert parsed == {BASE_TS: _row(0)}


@pytest.mark.parametrize("code", [404, 400, 403])
def test_raised_http_4xx_is_not_retried(code, tmp_path, monkeypatch):
    root = tmp_path / "verify"
    monkeypatch.setattr(av, "VERIFY_DIR", root)
    attempts = []

    def opener(request, timeout=30):
        attempts.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, code, "synthetic error", {}, io.BytesIO(b"no"))

    result = av.run_downloads(opener=opener, dates=(DAY,), sleep=lambda _: pytest.fail("4xx must not retry"))
    assert len(attempts) == 1
    assert result["dates"][DAY]["status"] == "failed"
    ledger = json.loads((av.EVIDENCE_DIR / "network-ledger.json").read_bytes())
    assert ledger[0]["http_status"] == code
    assert ledger[0]["bytes"] == 2
    assert ledger[0]["sha256"] == hashlib.sha256(b"no").hexdigest()
    assert any(p.read_bytes() == b"no" for p in (root / "failed").glob("*"))


@pytest.mark.parametrize("failure", ["500", "429", "timeout"])
def test_transient_error_retries_three_times_with_fixed_backoff(failure, tmp_path, monkeypatch):
    monkeypatch.setattr(av, "VERIFY_DIR", tmp_path / "verify")
    attempts, pauses = [], []

    def opener(request, timeout=30):
        attempts.append(request.full_url)
        if failure == "timeout":
            raise TimeoutError("synthetic timeout")
        raise urllib.error.HTTPError(request.full_url, int(failure), "synthetic transient", {}, io.BytesIO(b"retry"))

    result = av.run_downloads(opener=opener, dates=(DAY,), sleep=pauses.append)
    assert len(attempts) == 3
    assert pauses == [1, 4]
    assert result["dates"][DAY]["status"] == "failed"
    ledger = json.loads((av.EVIDENCE_DIR / "network-ledger.json").read_bytes())
    assert len(ledger) == 3
    assert all(row["started_at_utc"] and row["ended_at_utc"] and row["error"] for row in ledger)
    if failure != "timeout":
        assert result["counters"]["checksum_bytes"] == 15
        assert all(row["bytes"] == 5 and row["sha256"] == hashlib.sha256(b"retry").hexdigest() for row in ledger)
        assert len(list((av.VERIFY_DIR / "failed").glob("*"))) == 3


def test_default_opener_installs_no_redirect_handler(monkeypatch):
    captured = []

    class FakeOpener:
        def open(self, request, timeout):
            captured.append((request.full_url, timeout))
            return "sentinel-response"

    def build_opener(*handlers):
        assert len(handlers) == 1
        request = av.urllib.request.Request(av.allowed_urls(DAY)[0])
        assert handlers[0].redirect_request(request, None, 302, "Found", {}, "https://unlisted.invalid") is None
        return FakeOpener()

    monkeypatch.setattr(av.urllib.request, "build_opener", build_opener)
    request = av.urllib.request.Request(av.allowed_urls(DAY)[0], method="GET")
    assert av._open_response(request, timeout=30) == "sentinel-response"
    assert captured == [(request.full_url, 30)]


@pytest.mark.parametrize("linked_component", ["root", "ancestor", "child"])
@pytest.mark.parametrize("link_kind", ["symlink", "junction"])
def test_all_linked_path_components_are_rejected(tmp_path, monkeypatch, linked_component, link_kind):
    ancestor = tmp_path / "parent"
    root = ancestor / "verify"
    child = root / "child"
    child.mkdir(parents=True)
    monkeypatch.setattr(av, "VERIFY_DIR", root)
    linked = {"root": root, "ancestor": ancestor, "child": child}[linked_component]
    if link_kind == "symlink":
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
    with pytest.raises(av.ArchiveVerificationError):
        av.safe_verify_path(child / "result.bin")
    if linked_component != "child":
        with pytest.raises(av.ArchiveVerificationError):
            av.require_empty_verify_dir()


def test_checksum_mismatch_preserves_both_failed_payloads(tmp_path, monkeypatch):
    monkeypatch.setattr(av, "VERIFY_DIR", tmp_path / "verify")
    archive = _zip(DAY, _csv([_row(0)]))
    checksum = f"{'0' * 64}  {av.archive_name(DAY)}\n".encode()

    def opener(request, timeout=30):
        return _response(checksum if request.full_url.endswith(".CHECKSUM") else archive,
                         url=request.full_url)

    result = av.run_downloads(opener=opener, dates=(DAY,), sleep=lambda _: None)
    assert result["dates"][DAY]["status"] == "failed"
    retained = [p.read_bytes() for p in (av.VERIFY_DIR / "failed").glob("*")]
    assert archive in retained and checksum in retained
    assert not (av.VERIFY_DIR / av.archive_name(DAY)).exists()


def test_cli_synthetic_artifacts_are_byte_identical(tmp_path, monkeypatch):
    cli_path = Path(__file__).resolve().parents[1] / "scripts/verify_daily_archives.py"
    spec = importlib.util.spec_from_file_location("d039_cli_synthetic", cli_path)
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    raw = tmp_path / "monthly"
    verify = tmp_path / "verify"
    evidence = tmp_path / "evidence"
    raw.mkdir(); verify.mkdir(); evidence.mkdir()
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "RAW", raw)
    monkeypatch.setattr(cli, "MANIFEST", tmp_path / "manifest.json")
    monkeypatch.setattr(cli, "EVIDENCE", evidence)
    monkeypatch.setattr(av, "VERIFY_DIR", verify)
    monkeypatch.setattr(av, "DATES", (DAY, "2020-12-21"))
    months = []
    downloads = {"dates": {}}
    for day in av.DATES:
        ts = av.day_start_ms(day)
        row = list(_row(0)); row[0] = str(ts); row[6] = str(ts + 59_999)
        csv_payload = _csv([tuple(row)])
        month_zip = _zip(day[:7], csv_payload)
        (raw / f"{day[:7]}.zip").write_bytes(month_zip)
        months.append({"month": day[:7], "zip_sha256": av.sha256(month_zip)})
        daily_zip = _zip(day, csv_payload)
        (verify / av.archive_name(day)).write_bytes(daily_zip)
        (verify / (av.archive_name(day) + ".CHECKSUM")).write_bytes(
            f"{av.sha256(daily_zip)}  {av.archive_name(day)}\n".encode())
        downloads["dates"][day] = {"status": "passed", "sha256": av.sha256(daily_zip)}
    manifest = {"months": months}
    cli.MANIFEST.write_bytes(cli.json_bytes(manifest))
    first = cli.comparison_artifacts(manifest, downloads)
    second = cli.comparison_artifacts(manifest, downloads)
    assert first == second
    assert all(b"\r\n" not in payload for payload in first.values())
    flags = list(csv.DictReader(io.StringIO(first["minute-flags.csv"].decode())))
    intervals = list(csv.DictReader(io.StringIO(first["intervals.csv"].decode())))
    summaries = json.loads(first["comparison-summary.json"])
    for day in av.DATES:
        assert len([row for row in flags if row["day"] == day]) == 1440
        assert sum(int(row["length"]) for row in intervals if row["day"] == day) == 1440
        summary = next(row for row in summaries if row["day"] == day)
        assert sum(summary["counts"].values()) == 1440
    issue = json.loads(first["issue-475-check.json"])
    assert issue["status"] == "checked"
    assert not issue["zip_matches"] and not issue["daily_row_matches"] and not issue["monthly_row_matches"]


def test_run_downloads_stops_all_dates_when_get_budget_is_exhausted(tmp_path, monkeypatch):
    monkeypatch.setattr(av, "VERIFY_DIR", tmp_path / "verify")
    monkeypatch.setattr(av, "GET_LIMIT", 1)
    archive = _zip(DAY, _csv([_row(0)]))
    checksum = f"{av.sha256(archive)}  {av.archive_name(DAY)}\n".encode()
    calls = []

    def opener(request, timeout=30):
        calls.append(request.full_url)
        return _response(checksum, url=request.full_url)

    with pytest.raises(av.BudgetExceeded):
        av.run_downloads(opener=opener, dates=(DAY, "2020-12-21"), sleep=lambda _: None)
    assert calls == [av.allowed_urls(DAY)[1]]
    summary = json.loads((av.EVIDENCE_DIR / "download-summary.json").read_bytes())
    assert summary["status"] == "budget_stopped"
    assert "2020-12-21" not in summary["dates"]

