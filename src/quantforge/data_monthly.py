"""Strict, budgeted public monthly ingestion for the frozen development months.

The daily parser remains independent. No credentials, Range request, redirect,
holdout access, automatic date expansion or silent cache replacement is used.
"""

from __future__ import annotations

import calendar
from contextlib import contextmanager
from datetime import datetime, timezone
import http.client
import io
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
from uuid import uuid4
import zipfile

import pandas as pd

from quantforge import data
from quantforge import research_policy

BASE_URL = "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m"
ALLOWED_MONTHS = research_policy.ALLOWED_MONTHS
FIRST_MONTH = "2025-09"
ZIP_LIMIT = 4 * 1024 * 1024
CSV_LIMIT = 32 * 1024 * 1024
TOTAL_ZIP_LIMIT = 28 * 1024 * 1024
CHECKSUM_LIMIT = 4096
MAX_ATTEMPTS = 3
READ_CHUNK = 128 * 1024
PARSER_VERSION = 1


class DownloadBudgetError(data.DataValidationError):
    """The fixed resource budget would be exceeded."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_month(month: str) -> str:
    # Keep the network boundary independently closed even if a policy file is edited.
    if month not in ALLOWED_MONTHS:
        raise data.DataValidationError("Month is not an authorized development month; holdout access is forbidden")
    return research_policy.ensure_allowed_month(month)


def monthly_paths(month: str) -> dict[str, Path]:
    _ensure_month(month)
    name = f"BTCUSDT-1m-{month}"
    paths = research_policy.month_paths(month)
    source = f"data/raw/binance/spot/BTCUSDT/1m/monthly/{name}.zip.source.json"
    paths["source"] = research_policy.fixed_project_path(source, source)
    return paths


def latest_verification_path(month: str) -> Path:
    _ensure_month(month)
    relative = f"results/monthly-download/latest-verification/{month}.json"
    return research_policy.fixed_project_path(relative, relative)


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path = _fixed_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _fixed_path(path.parent / f".{path.name}.{uuid4().hex}.tmp")
    with temporary.open("xb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(_fixed_path(path))


def _fixed_path(path: str | Path) -> Path:
    lexical = Path(path)
    if lexical.is_absolute():
        try:
            relative = lexical.relative_to(data.REPOSITORY_ROOT)
        except ValueError as exc:
            raise data.DataValidationError("Monthly path is outside the project") from exc
    else:
        relative = lexical
    return research_policy.fixed_project_path(path, relative)


def _atomic_json(path: Path, value: dict) -> None:
    _atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"))


def _limited_read(path: Path, limit: int) -> bytes:
    path = _fixed_path(path)
    if path.stat().st_size > limit:
        raise data.DataValidationError(f"Cached file exceeds {limit} bytes: {path.name}")
    with path.open("rb") as source:
        content = source.read(limit + 1)
    if len(content) > limit:
        raise data.DataValidationError(f"Cached file exceeds {limit} bytes: {path.name}")
    return content


def _month_bounds(month: str) -> tuple[pd.Timestamp, pd.Timestamp, int]:
    if not isinstance(month, str) or re.fullmatch(r"\d{4}-\d{2}", month) is None:
        raise data.DataValidationError("Month must use exact YYYY-MM format")
    if month in {"2026-07", "2026-08"}:
        raise data.DataValidationError("Final holdout parsing is forbidden")
    year, number = map(int, month.split("-"))
    try:
        days = calendar.monthrange(year, number)[1]
        start = pd.Timestamp(f"{month}-01", tz="UTC")
        end = start + pd.DateOffset(months=1)
    except (ValueError, OverflowError) as exc:
        raise data.DataValidationError("Invalid calendar month") from exc
    return start, end, days


def parse_monthly_klines(csv_bytes: bytes, month: str) -> tuple[pd.DataFrame, dict]:
    """Parse a full calendar month; 2024 synthetic leap-year fixtures are valid.

    Network/file entry points separately restrict the development allowlist.
    Explicit 2026 July/August holdout strings are rejected before CSV parsing.
    """
    start, end, days = _month_bounds(month)
    if len(csv_bytes) > CSV_LIMIT:
        raise data.DataValidationError("Monthly CSV exceeds 32 MiB limit")
    try:
        frame = pd.read_csv(io.BytesIO(csv_bytes), header=None, dtype=str, keep_default_na=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as exc:
        raise data.DataValidationError("Unreadable or empty monthly CSV") from exc
    if frame.shape[1] != len(data.COLUMNS):
        raise data.DataValidationError(f"Expected 12 monthly CSV columns, got {frame.shape[1]}")
    frame.columns = data.COLUMNS
    for column in ("open_time", "close_time", "number_of_trades"):
        frame[column] = data._integer_column(frame[column], column)
    unit = "us" if start.year >= 2025 else "ms"
    step = 60_000_000 if unit == "us" else 60_000
    low, high = (10**15, 10**17 - 1) if unit == "us" else (10**12, 10**14 - 1)
    if not frame[["open_time", "close_time"]].apply(lambda column: column.between(low, high).all()).all():
        raise data.DataValidationError(f"Monthly Spot timestamps must consistently use {unit}")
    columns = data.PRICE_COLUMNS + data.VOLUME_COLUMNS + ["ignore"]
    try:
        for column in columns:
            frame[column] = pd.to_numeric(frame[column], errors="raise").astype("float64")
    except (ValueError, TypeError) as exc:
        raise data.DataValidationError("Non-numeric monthly price/volume field") from exc
    numeric = frame[columns]
    nonfinite = int((numeric.isna() | numeric.isin([float("inf"), float("-inf")])).any(axis=1).sum())
    close_errors = int((frame.close_time - frame.open_time != step - 1).sum())
    frame["open_time_raw"], frame["close_time_raw"] = frame.open_time, frame.close_time
    try:
        frame["open_time"] = pd.to_datetime(frame.open_time, unit=unit, utc=True)
        frame["close_time"] = pd.to_datetime(frame.close_time, unit=unit, utc=True)
    except (ValueError, OverflowError) as exc:
        raise data.DataValidationError("Monthly timestamp conversion failed") from exc
    expected = pd.date_range(start, end, inclusive="left", freq="min")
    actual = pd.DatetimeIndex(frame.open_time)
    bad_ohlc = ((frame[data.PRICE_COLUMNS] <= 0).any(axis=1) | (frame.low > frame.high)
                | (frame.open < frame.low) | (frame.open > frame.high)
                | (frame.close < frame.low) | (frame.close > frame.high))
    bad_volume = ((frame[data.VOLUME_COLUMNS] < 0).any(axis=1)
                  | (frame.taker_buy_base_asset_volume > frame.volume + 1e-8)
                  | (frame.taker_buy_quote_asset_volume > frame.quote_asset_volume + 1e-8))
    validation = {
        "month": month, "calendar_days": days, "rows": len(frame), "expected_rows": days * 1440,
        "timestamp_unit_raw": unit, "timezone": "UTC",
        "first_open_utc": frame.open_time.iloc[0].isoformat() if len(frame) else None,
        "last_open_utc": frame.open_time.iloc[-1].isoformat() if len(frame) else None,
        "last_close_utc": frame.close_time.iloc[-1].isoformat() if len(frame) else None,
        "missing_minutes": len(expected.difference(actual)), "unexpected_open_times": len(actual.difference(expected)),
        "duplicate_open_times": int(actual.duplicated().sum()), "sorted_ascending": actual.is_monotonic_increasing,
        "invalid_ohlc_rows": int(bad_ohlc.sum()), "invalid_volume_rows": int(bad_volume.sum()),
        "nonfinite_numeric_rows": nonfinite, "invalid_close_duration_rows": close_errors,
    }
    if (len(frame) != len(expected) or not actual.equals(expected)
            or bad_ohlc.any() or bad_volume.any() or nonfinite or close_errors):
        raise data.DataValidationError("Monthly kline validation failed: " + json.dumps(validation))
    return frame, validation


def extract_monthly_csv(archive: bytes, month: str) -> bytes:
    _month_bounds(month)
    if len(archive) > ZIP_LIMIT:
        raise data.DataValidationError("Monthly ZIP exceeds 4 MiB limit")
    expected = f"BTCUSDT-1m-{month}.csv"
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            members = zipped.infolist()
            if len(members) != 1 or members[0].filename != expected or members[0].is_dir():
                raise data.DataValidationError("Monthly ZIP must contain exactly its expected CSV member")
            member = members[0]
            if member.flag_bits & 1 or member.file_size > CSV_LIMIT:
                raise data.DataValidationError("Encrypted or oversized monthly CSV member")
            with zipped.open(member) as source:
                csv_bytes = source.read(CSV_LIMIT + 1)
            if len(csv_bytes) > CSV_LIMIT or len(csv_bytes) != member.file_size:
                raise data.DataValidationError("Monthly CSV exceeds decompression limit or declared size")
            return csv_bytes
    except (zipfile.BadZipFile, RuntimeError, EOFError) as exc:
        raise data.DataValidationError("Invalid monthly ZIP/CRC/compression") from exc


@contextmanager
def _download_lock():
    path = _fixed_path("results/monthly-download/download.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise DownloadBudgetError("Another download or stale lock exists; inspect before resuming") from exc
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(json.dumps({"pid": os.getpid(), "created_at_utc": _now()}))
        yield
    finally:
        _fixed_path(path).unlink()


class NetworkLedger:
    """Persistent payload accounting, including retry bytes and crash reservations.

    Reserve before reading; settle only after a returned chunk. A process crash
    leaves a conservative reservation charged to the same fixed phase budget.
    TCP/TLS/header overhead cannot be measured by urllib and is not claimed here.
    """

    def __init__(self):
        self.path = _fixed_path("results/monthly-download/network-ledger.json")
        if self.path.exists():
            self.state = json.loads(_limited_read(self.path, 8 * 1024 * 1024))
            if self.state.get("schema_version") != 1 or self.state.get("zip_budget_bytes") != TOTAL_ZIP_LIMIT:
                raise DownloadBudgetError("Invalid or incompatible existing budget ledger; do not reset automatically")
        else:
            self.state = {"schema_version": 1, "phase": "2B", "created_at_utc": _now(),
                          "zip_budget_bytes": TOTAL_ZIP_LIMIT, "zip_body_bytes": 0,
                          "zip_reserved_bytes": 0, "checksum_body_bytes": 0,
                          "checksum_reserved_bytes": 0, "attempts": [],
                          "accounting": "HTTP response payload bytes plus conservative unresolved read reservations; excludes unmeasured TCP/TLS/headers"}
        for field in ("zip_body_bytes", "zip_reserved_bytes", "checksum_body_bytes", "checksum_reserved_bytes"):
            if not isinstance(self.state.get(field), int) or self.state[field] < 0:
                raise DownloadBudgetError("Invalid ledger counters")
        if not isinstance(self.state.get("attempts"), list):
            raise DownloadBudgetError("Invalid ledger transfer history")
        for attempt in self.state["attempts"]:
            if attempt.get("kind") not in {"zip", "checksum"}:
                raise DownloadBudgetError("Invalid ledger transfer kind")
            _allowed_url(attempt.get("url"))
            if any(not isinstance(attempt.get(field), int) or attempt[field] < 0 for field in ("body_bytes", "reserved_bytes")):
                raise DownloadBudgetError("Invalid ledger attempt counters")
        for kind in ("zip", "checksum"):
            for field in ("body_bytes", "reserved_bytes"):
                if self.state[f"{kind}_{field}"] != sum(attempt[field] for attempt in self.state["attempts"] if attempt["kind"] == kind):
                    raise DownloadBudgetError("Ledger totals do not reconcile with transfer history")
        if self.remaining < 0:
            raise DownloadBudgetError("Existing budget ledger is over the fixed quota")
        self.save()

    @property
    def remaining(self) -> int:
        return TOTAL_ZIP_LIMIT - self.state["zip_body_bytes"] - self.state["zip_reserved_bytes"]

    def save(self):
        self.state["updated_at_utc"] = _now()
        _atomic_json(self.path, self.state)

    def start(self, url: str, kind: str) -> dict:
        attempt = {"id": len(self.state["attempts"]), "url": url, "kind": kind, "started_at_utc": _now(),
                   "status": "started", "body_bytes": 0, "reserved_bytes": 0}
        self.state["attempts"].append(attempt)
        self.save()
        return attempt

    def reserve(self, kind: str, count: int, attempt: dict):
        if count <= 0 or (kind == "zip" and count > self.remaining):
            raise DownloadBudgetError("Cumulative ZIP network payload budget exhausted")
        self.state[f"{kind}_reserved_bytes"] += count
        attempt["reserved_bytes"] += count
        self.save()

    def settle(self, kind: str, reserved: int, received: int, attempt: dict):
        if received < 0 or received > reserved:
            raise DownloadBudgetError("Network reader violated its reserved byte limit")
        self.state[f"{kind}_reserved_bytes"] -= reserved
        self.state[f"{kind}_body_bytes"] += received
        attempt["reserved_bytes"] -= reserved
        attempt["body_bytes"] += received
        self.save()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise data.DataValidationError("Monthly download redirects are forbidden")


def _open_response(request):
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=30)


def _allowed_url(url: str) -> str:
    for month in ALLOWED_MONTHS:
        base = f"{BASE_URL}/BTCUSDT-1m-{month}.zip"
        if url == base or url == base + ".CHECKSUM":
            return month
    raise data.DataValidationError("URL is outside the exact monthly download allowlist")


def _fetch(url: str, kind: str, ledger: NetworkLedger) -> tuple[bytes, dict]:
    _allowed_url(url)
    if kind not in {"zip", "checksum"} or (kind == "checksum") != url.endswith(".CHECKSUM"):
        raise ValueError("Payload kind does not match URL")
    limit = ZIP_LIMIT if kind == "zip" else CHECKSUM_LIMIT
    request = urllib.request.Request(url, headers={"User-Agent": "QuantForge/0.1 noncommercial research", "Accept-Encoding": "identity"})
    for number in range(MAX_ATTEMPTS):
        if kind == "zip" and ledger.remaining <= 0:
            raise DownloadBudgetError("Cumulative ZIP network payload budget exhausted")
        attempt = ledger.start(url, kind)
        started = time.perf_counter()
        try:
            with _open_response(request) as response:
                if response.url != url or response.status != 200:
                    raise data.DataValidationError("Unexpected monthly HTTP status or redirected URL")
                length_text = response.headers.get("Content-Length")
                if length_text is None or re.fullmatch(r"\d+", length_text) is None:
                    raise data.DataValidationError("A valid Content-Length is required for bounded monthly transfers")
                length = int(length_text)
                if length <= 0 or length > limit:
                    raise DownloadBudgetError(f"Declared {kind} body exceeds its fixed per-file limit")
                if kind == "zip" and length > ledger.remaining:
                    raise DownloadBudgetError("Declared ZIP cannot fit the remaining cumulative network budget")
                parts = []
                consumed = 0
                while consumed < length:
                    requested = min(READ_CHUNK, length - consumed)
                    ledger.reserve(kind, requested, attempt)
                    try:
                        chunk = response.read1(requested)
                    except http.client.IncompleteRead as exc:
                        ledger.settle(kind, requested, len(exc.partial), attempt)
                        raise
                    # Other read failures leave a charged reservation: received
                    # bytes may be unavailable to Python after a socket failure.
                    ledger.settle(kind, requested, len(chunk), attempt)
                    if not chunk:
                        raise http.client.IncompleteRead(b"", length - consumed)
                    parts.append(chunk)
                    consumed += len(chunk)
                payload = b"".join(parts)
                attempt.update({"status": "received", "finished_at_utc": _now(),
                                "elapsed_seconds": time.perf_counter() - started,
                                "http_status": response.status, "content_length": length,
                                "last_modified": response.headers.get("Last-Modified"), "etag": response.headers.get("ETag")})
                ledger.save()
                return payload, {"url": url, "resolved_url": response.url, "http_status": response.status,
                                 "retrieved_at_utc": attempt["finished_at_utc"], "bytes": len(payload),
                                 "last_modified": response.headers.get("Last-Modified"), "etag": response.headers.get("ETag"),
                                 "ledger_attempt_id": attempt["id"], "attempt_number": number + 1,
                                 "elapsed_seconds": attempt["elapsed_seconds"]}
        except Exception as exc:
            attempt.update({"status": "failed", "finished_at_utc": _now(), "error_type": type(exc).__name__,
                            "error": str(exc), "elapsed_seconds": time.perf_counter() - started})
            ledger.save()
            retryable = isinstance(exc, (TimeoutError, ConnectionError, urllib.error.URLError, http.client.IncompleteRead))
            if isinstance(exc, urllib.error.HTTPError):
                retryable = exc.code == 429 or 500 <= exc.code < 600
                exc.close()
            if not retryable or number + 1 == MAX_ATTEMPTS:
                raise
            time.sleep(min(2**number, 4))
    raise RuntimeError("Unreachable retry state")


def _relative(path: Path) -> str:
    return path.relative_to(data.REPOSITORY_ROOT).as_posix()


def _verify_cached_month(month: str, *, ignore_latest_failure: bool = False) -> tuple[pd.DataFrame, dict]:
    paths = monthly_paths(month)
    latest = latest_verification_path(month)
    if not ignore_latest_failure and latest.exists() and json.loads(_limited_read(latest, 65536)).get("status") != "passed":
        raise data.DataValidationError("Latest monthly verification failed; earlier success is not current validation")
    manifest = json.loads(_limited_read(paths["manifest"], 1024 * 1024))
    for key, expected in (("status", "passed"), ("month", month), ("market", "spot"), ("symbol", "BTCUSDT"), ("interval", "1m")):
        if manifest.get(key) != expected:
            raise data.DataValidationError(f"Invalid cached monthly manifest field: {key}")
    for key in ("raw", "checksum", "parquet"):
        research_policy.fixed_project_path(manifest[f"{key}_path"], _relative(paths[key]))
    expected_url = f"{BASE_URL}/{paths['raw'].name}"
    if manifest.get("source") != {"archive_url": expected_url, "checksum_url": expected_url + ".CHECKSUM"}:
        raise data.DataValidationError("Cached monthly manifest source is not its canonical official URL")
    archive = _limited_read(paths["raw"], ZIP_LIMIT)
    checksum = _limited_read(paths["checksum"], CHECKSUM_LIMIT)
    digest = data.verify_checksum(archive, checksum.decode("ascii"), paths["raw"].name)
    if digest != manifest["archive_sha256"]:
        raise data.DataValidationError("Cached monthly archive hash differs from manifest")
    csv_bytes = extract_monthly_csv(archive, month)
    if data.sha256(csv_bytes) != manifest["csv_sha256"]:
        raise data.DataValidationError("Cached monthly CSV hash differs from manifest")
    expected, _ = parse_monthly_klines(csv_bytes, month)
    parquet = _limited_read(paths["parquet"], CSV_LIMIT)
    if data.sha256(parquet) != manifest["parquet_sha256"]:
        raise data.DataValidationError("Cached monthly Parquet hash differs from manifest")
    actual = pd.read_parquet(io.BytesIO(parquet))
    pd.testing.assert_frame_equal(expected, actual, check_exact=True)
    return actual, manifest


def verify_cached_month(month: str) -> tuple[pd.DataFrame, dict]:
    """Offline full integrity/schema/Parquet recheck; never access other months."""
    _ensure_month(month)
    return _verify_cached_month(month)


def _download_month(month: str, ledger: NetworkLedger) -> dict:
    paths = monthly_paths(month)
    url = f"{BASE_URL}/{paths['raw'].name}"
    before = {key: ledger.state[key] for key in ("zip_body_bytes", "zip_reserved_bytes", "checksum_body_bytes", "checksum_reserved_bytes")}
    started = time.perf_counter()
    if paths["manifest"].exists():
        _verify_cached_month(month, ignore_latest_failure=True)
    checksum_bytes, checksum_source = _fetch(url + ".CHECKSUM", "checksum", ledger)
    if paths["raw"].exists():
        archive = _limited_read(paths["raw"], ZIP_LIMIT)
        source = json.loads(_limited_read(paths["source"], 65536)) if paths["source"].exists() else {
            "url": url, "bytes": len(archive), "provenance_note": "Original network metadata unavailable",
        }
        if source.get("url") != url or source.get("bytes") != len(archive):
            raise data.DataValidationError("Cached monthly source metadata mismatch")
        source["cache_reused"] = True
    else:
        archive, source = _fetch(url, "zip", ledger)
        source["cache_reused"] = False
    digest = data.verify_checksum(archive, checksum_bytes.decode("ascii"), paths["raw"].name)
    if "sha256" in source and source["sha256"] != digest:
        raise data.DataValidationError("Cached source hash mismatch")
    source["sha256"] = digest
    csv_bytes = extract_monthly_csv(archive, month)
    frame, validation = parse_monthly_klines(csv_bytes, month)
    if paths["manifest"].exists():
        _, previous = _verify_cached_month(month, ignore_latest_failure=True)
        if previous["archive_sha256"] != digest:
            raise data.DataValidationError("Archive changed since prior successful validation")
    parquet_buffer = io.BytesIO()
    frame.to_parquet(parquet_buffer, engine="pyarrow", index=False, compression="zstd")
    parquet_bytes = parquet_buffer.getvalue()
    pd.testing.assert_frame_equal(frame, pd.read_parquet(io.BytesIO(parquet_bytes)), check_exact=True)
    for key, content in (("raw", archive), ("checksum", checksum_bytes), ("source", (json.dumps(source, indent=2) + "\n").encode()), ("parquet", parquet_bytes)):
        _atomic_bytes(paths[key], content)
    manifest = {
        "schema_version": 1, "parser_version": PARSER_VERSION, "status": "passed", "month": month,
        "market": "spot", "symbol": "BTCUSDT", "interval": "1m", "validated_at_utc": _now(),
        "archive_sha256": digest, "csv_sha256": data.sha256(csv_bytes), "parquet_sha256": data.sha256(parquet_bytes),
        "raw_path": _relative(paths["raw"]), "checksum_path": _relative(paths["checksum"]), "parquet_path": _relative(paths["parquet"]),
        "source": {"archive_url": url, "checksum_url": url + ".CHECKSUM"},
        "archive": source, "official_checksum": checksum_source, "checksum_text": checksum_bytes.decode("ascii"),
        "validation": validation, "csv_columns": data.COLUMNS,
        "parquet_dtypes": {column: str(dtype) for column, dtype in frame.dtypes.items()}, "parquet_roundtrip_equal": True,
        "elapsed_seconds": time.perf_counter() - started,
        "network_this_invocation": {key: ledger.state[key] - before[key] for key in before},
        "network_phase_totals": {key: ledger.state[key] for key in before},
        "network_ledger_path": _relative(ledger.path), "network_accounting": ledger.state["accounting"],
        "source_code_sha256": data.sha256(Path(__file__).read_bytes()),
        "resume_policy": "Reuse completed verified files; failed partial transfers restart from byte zero and remain charged. No Range requests.",
    }
    _atomic_json(latest_verification_path(month), {"status": "passed", "month": month, "checked_at_utc": _now(), "archive_sha256": digest})
    _atomic_json(paths["manifest"], manifest)
    return manifest


def _record_failure(month: str, exc: Exception, ledger: NetworkLedger | None) -> None:
    record = {"status": "failed", "month": month, "failed_at_utc": _now(),
              "error_type": type(exc).__name__, "error": str(exc),
              "earlier_success_manifest_is_not_current_verification": True,
              "network_phase_totals": None if ledger is None else {key: ledger.state[key] for key in (
                  "zip_body_bytes", "zip_reserved_bytes", "checksum_body_bytes", "checksum_reserved_bytes")}}
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
    _atomic_json(_fixed_path(f"results/monthly-download/failures/{run}/failure.json"), record)
    if month in ALLOWED_MONTHS:
        _atomic_json(latest_verification_path(month), record)


def download_month(month: str = FIRST_MONTH) -> dict:
    """Download one allowed month; later months require a reverified first month."""
    _ensure_month(month)
    ledger = None
    try:
        with _download_lock():
            ledger = NetworkLedger()
            if month != FIRST_MONTH:
                verify_cached_month(FIRST_MONTH)
            return _download_month(month, ledger)
    except Exception as exc:
        _record_failure(month, exc, ledger)
        raise


def download_development() -> list[dict]:
    """Resume the exact authorized list after first-month full verification."""
    try:
        verify_cached_month(FIRST_MONTH)
    except Exception as exc:
        _record_failure(FIRST_MONTH, exc, None)
        raise
    return [download_month(month) for month in ALLOWED_MONTHS]


# Phase 3A additions do not change any preceding parser/download behavior.
def detect_unit(first_row_open_time: object) -> str:
    """Infer epoch units from an integer without rounding or float conversion."""
    from numbers import Integral

    if (isinstance(first_row_open_time, bool)
            or not isinstance(first_row_open_time, Integral) or first_row_open_time < 0):
        raise data.DataValidationError("Timestamp must be a nonnegative integer")
    if first_row_open_time >= 10**15:
        return "us"
    if first_row_open_time < 10**14:
        return "ms"
    raise data.DataValidationError("Ambiguous timestamp unit magnitude")


def _historical_timestamp_gate(csv_bytes: bytes, month: str, config_path: str | Path,
                               *, daily: bool = False) -> str:
    """Validate policy and every raw timestamp before delegating OHLCV parsing."""
    research_policy.ensure_month_in_historical_allowlist(month)
    start, end = research_policy.historical_month_bounds(month)
    research_policy.ensure_not_holdout(start, end)
    from quantforge.phase3a_config import load_history_config

    config = load_history_config(config_path)
    limit = min(config["download"]["per_csv_limit_bytes"], 8 * 1024**2) if daily else config["download"]["per_csv_limit_bytes"]
    if not isinstance(csv_bytes, bytes) or len(csv_bytes) > limit:
        raise data.DataValidationError("Historical CSV exceeds its bounded byte contract")
    try:
        frame = pd.read_csv(io.BytesIO(csv_bytes), header=None, dtype=str, keep_default_na=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as exc:
        raise data.DataValidationError("Unreadable or empty historical CSV") from exc
    if frame.empty or frame.shape[1] != len(data.COLUMNS):
        raise data.DataValidationError("Historical CSV requires 12 columns and at least one row")
    opens = data._integer_column(frame.iloc[:, 0], "open_time")
    closes = data._integer_column(frame.iloc[:, 6], "close_time")
    unit = detect_unit(opens.iloc[0])
    policy = config["timestamp"]
    ms_end, us_start = pd.Timestamp(policy["ms_window_end"]), pd.Timestamp(policy["us_window_start"])
    if end <= us_start and end - pd.Timedelta(milliseconds=1) <= ms_end:
        expected = "ms"
    elif start >= us_start:
        expected = "us"
    else:
        raise data.DataValidationError("Historical month crosses timestamp-policy boundaries")
    if unit != expected:
        raise data.DataValidationError(
            f"timestamp unit mismatch: expected {expected} for month {month}, got {unit}")
    timestamps = pd.concat([opens, closes], ignore_index=True)
    same_unit = timestamps.ge(10**15).all() if unit == "us" else timestamps.lt(10**14).all()
    if not same_unit:
        raise data.DataValidationError("Mixed or ambiguous historical timestamp units")
    return unit


def parse_historical_daily_klines(csv_bytes: bytes, day,
                                  config_path: str | Path = "configs/history-extension.toml") -> tuple[pd.DataFrame, dict]:
    """Gate a caller-supplied historical day, then reuse unchanged daily validation."""
    data.ensure_daily_allowed(day)
    _historical_timestamp_gate(csv_bytes, day.strftime("%Y-%m"), config_path, daily=True)
    return data.parse_klines(csv_bytes, day)


def parse_historical_monthly_klines(csv_bytes: bytes, month: str,
                                    config_path: str | Path = "configs/history-extension.toml") -> tuple[pd.DataFrame, dict]:
    """Gate a caller-supplied historical month, then reuse unchanged validation."""
    _historical_timestamp_gate(csv_bytes, month, config_path)
    return parse_monthly_klines(csv_bytes, month)


def plan_historical_download(config_path: str | Path = "configs/history-extension.toml") -> dict:
    """Read the frozen configuration and return an offline plan without requests."""
    from quantforge.phase3a_config import load_history_config

    config = load_history_config(config_path)
    months = tuple(config["months"]["warm_up"]) + tuple(config["months"]["evaluate"])
    if months != research_policy.HISTORICAL_MONTHS or len(months) != config["months"]["total"]:
        raise data.DataValidationError("Historical plan must enumerate the exact frozen month allowlist")
    if config["data"] != {"symbol": "BTCUSDT", "market": "spot", "interval": "1m"}:
        raise data.DataValidationError("Historical planning market is frozen")
    if config["download"]["authorized"] is not False:
        raise data.DataValidationError("Historical downloads are not authorized")
    expected = config["download"]["estimated_month_zip_bytes"]
    rows = []
    for month in months:
        research_policy.ensure_month_in_historical_allowlist(month)
        start, end = research_policy.historical_month_bounds(month)
        research_policy.ensure_not_holdout(start, end)
        url = f"{BASE_URL}/BTCUSDT-1m-{month}.zip"
        rows.append({"month": month, "url": url, "expected_bytes_range": list(expected),
                     "expected_checksum_url": url + ".CHECKSUM", "status": "not_yet_downloaded"})
    return {
        "checksum_hard_cap": config["budget"]["checksum_bytes"],
        "download_authorized": False,
        "estimate_basis": "Unverified planning range; no HEAD/CHECKSUM/GET performed.",
        "estimated_zip_bytes_range": [bound * len(rows) for bound in expected],
        "months": rows, "network_requests": 0, "schema_version": 1,
        "status": "not_yet_downloaded",
        "zip_payload_hard_cap": config["budget"]["zip_bytes"],
    }


# Gate 1 additions.  The Phase 2B implementation above is deliberately left
# byte-for-byte unchanged; these entry points use a separate historical root,
# ledger and request allowlist.
import hashlib


def _historical_contract_path(value: str | Path, expected: str) -> Path:
    """Resolve one fixed Gate 1 path without permitting aliases or links."""
    return research_policy.fixed_project_path(value, expected)


def load_historical_download_contract(
    config_path: str | Path = "configs/history-extension.toml",
    output_root: str | Path = "data/raw/binance/spot/BTCUSDT/1m/historical",
) -> dict:
    """Load the frozen contract used by the real-data Gate 1 downloader."""
    from quantforge.phase3a_config import load_history_config

    config = load_history_config(config_path)
    expected_root = "data/raw/binance/spot/BTCUSDT/1m/historical"
    root = _historical_contract_path(output_root, expected_root)
    months = tuple(config["months"]["warm_up"] + config["months"]["evaluate"])
    if months != research_policy.HISTORICAL_MONTHS or len(months) != 68:
        raise data.DataValidationError("Historical contract must contain exactly 68 ordered months")
    draft_path = _historical_contract_path("docs/phase3a-download-manifest-draft.json",
                                           "docs/phase3a-download-manifest-draft.json")
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    if not research_policy._same_typed_value(draft, plan_historical_download(config_path)):
        raise data.DataValidationError("Frozen historical URL draft differs from regenerated plan")
    if tuple(row["month"] for row in draft["months"]) != months:
        raise data.DataValidationError("Historical draft month order differs from frozen config")
    download = config["download"]
    budget = config["budget"]
    if (download["max_concurrent"] != 1 or download["per_month_retry"] != 3
            or download["backoff_seconds"] != [1, 4, 16]
            or download["official_checksum_required"] is not True):
        raise data.DataValidationError("Historical retry contract differs from frozen config")
    return {
        "config": config,
        "config_path": "configs/history-extension.toml",
        "output_root": root,
        "output_root_relative": expected_root,
        "months": months,
        "draft": draft,
        "zip_budget_bytes": int(budget["zip_bytes"]),
        "checksum_budget_bytes": int(budget["checksum_bytes"]),
        "disk_reserve_bytes": int(budget["disk_reserve_bytes"]),
        "per_zip_limit_bytes": int(download["per_zip_limit_bytes"]),
        "per_csv_limit_bytes": int(download["per_csv_limit_bytes"]),
        "per_checksum_limit_bytes": min(int(download["per_checksum_limit_bytes"]), 4096),
        "max_attempts": int(download["per_month_retry"]),
        "http_get_cap": 408,
        "http_gets_cap": 408,
        "backoff_seconds": tuple(download["backoff_seconds"]),
    }


def _validate_historical_contract(contract: dict) -> dict:
    """Reject caller mutations by rebuilding the entire pinned contract."""
    if not isinstance(contract, dict):
        raise data.DataValidationError("A validated historical download contract is required")
    expected = load_historical_download_contract(
        contract.get("config_path", ""), contract.get("output_root", ""))
    if not research_policy._same_typed_value(contract, expected):
        raise data.DataValidationError("Historical contract was modified after frozen validation")
    return expected


def _historical_paths(month: str, contract: dict) -> dict[str, Path]:
    research_policy.ensure_month_in_historical_allowlist(month)
    root = contract["output_root"]
    if not isinstance(root, Path):
        raise data.DataValidationError("Invalid historical output root")
    start, end = research_policy.historical_month_bounds(month)
    research_policy.ensure_not_holdout(start, end)
    name = month
    processed_rel = f"data/processed/binance/spot/BTCUSDT/1m/historical/{name}.parquet"
    return {
        "raw": _historical_contract_path(root / f"{name}.zip", f"{contract['output_root_relative']}/{name}.zip"),
        "checksum": _historical_contract_path(root / f"{name}.zip.CHECKSUM", f"{contract['output_root_relative']}/{name}.zip.CHECKSUM"),
        "source": _historical_contract_path(root / f"{name}.zip.source.json", f"{contract['output_root_relative']}/{name}.zip.source.json"),
        "parquet": _historical_contract_path(processed_rel, processed_rel),
    }


def historical_month_paths(month: str, contract: dict) -> dict[str, Path]:
    """Return only the four fixed local paths for an allowlisted month."""
    research_policy.ensure_month_in_historical_allowlist(month)
    return _historical_paths(month, _validate_historical_contract(contract))


class _HistoricalDownloadLock:
    def __init__(self):
        self.path = _historical_contract_path(
            "results/phase3a-gate1/download.lock", "results/phase3a-gate1/download.lock")
        self.descriptor = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise DownloadBudgetError("Gate 1 download lock exists; inspect before resuming") from exc
        with os.fdopen(self.descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": os.getpid(), "created_at_utc": _now()}))
        return self

    def __exit__(self, *_):
        _historical_contract_path(self.path, "results/phase3a-gate1/download.lock").unlink()
        return False


class HistoricalNetworkLedger:
    """Persistent Gate 1 payload ledger with conservative retry accounting."""

    def __init__(self, contract: dict, *, ledger_path: str | Path | None = None):
        self.contract = _validate_historical_contract(contract)
        selected = "results/phase3a-gate1/network-ledger.json" if ledger_path is None else ledger_path
        allowed = {"results/phase3a-gate1/network-ledger.json",
                   "results/phase3a-gate1/network-ledger-continuation.json",
                   "results/phase3a-gate1/continuations/2020-02-refetch.json"}
        if str(selected).replace("\\", "/") not in allowed:
            raise data.DataValidationError("Historical ledger path is not one of the fixed Gate 1 ledger paths")
        relative = str(selected).replace("\\", "/")
        self.path = _historical_contract_path(relative, relative)
        self.state = None
        self._active = False

    def _new_state(self) -> dict:
        return {
                "schema_version": 1, "phase": "3A-gate1", "status": "in_progress",
                "started_at_utc": _now(), "created_at_utc": _now(),
                "zip_budget_bytes": self.contract["zip_budget_bytes"],
                "checksum_budget_bytes": self.contract["checksum_budget_bytes"],
                "zip_body_bytes": 0, "zip_reserved_bytes": 0,
                "checksum_body_bytes": 0, "checksum_reserved_bytes": 0,
                "http_gets_count": 0, "http_get_cap": 408, "http_gets_cap": 408,
                "attempts": [], "months": [], "holdout_requests": 0,
                "holdout_access_count": 0, "market_rows_read": 0, "final_commit_sha": None,
                "accounting": "HTTP payload bytes plus unresolved read reservations; excludes headers/TCP/TLS",
            }

    def _validate_state(self):
        if (not isinstance(self.state, dict) or self.state.get("schema_version") != 1
                or self.state.get("phase") != "3A-gate1"
                or self.state.get("http_gets_cap") != 408
                or self.state.get("zip_budget_bytes") != self.contract["zip_budget_bytes"]
                or self.state.get("checksum_budget_bytes") != self.contract["checksum_budget_bytes"]):
            raise DownloadBudgetError("Invalid or incompatible Gate 1 network-ledger schema")
        for key in ("zip_body_bytes", "zip_reserved_bytes", "checksum_body_bytes", "checksum_reserved_bytes", "http_gets_count"):
            if type(self.state.get(key)) is not int or self.state[key] < 0:
                raise DownloadBudgetError("Invalid Gate 1 ledger counter")
        if self.state["http_gets_count"] > 408:
            raise DownloadBudgetError("Gate 1 HTTP GET cap already exceeded")
        if not isinstance(self.state.get("attempts"), list) or not isinstance(self.state.get("months"), list):
            raise DownloadBudgetError("Invalid Gate 1 ledger history")
        if self.state["http_gets_count"] != len(self.state["attempts"]):
            raise DownloadBudgetError("Gate 1 request count does not reconcile")
        for identifier, attempt in enumerate(self.state["attempts"]):
            if (attempt.get("id") != identifier or attempt.get("kind") not in {"zip", "checksum"}
                    or type(attempt.get("month_attempt")) is not int
                    or not 1 <= attempt["month_attempt"] <= 3):
                raise DownloadBudgetError("Invalid Gate 1 attempt history")
            kind = attempt["kind"]
            expected = _historical_url(attempt.get("month"), ".zip.CHECKSUM" if kind == "checksum" else ".zip")
            if attempt.get("url") != expected:
                raise DownloadBudgetError("Invalid Gate 1 attempt URL")
            for field in ("body_bytes", "reserved_bytes"):
                if type(attempt.get(field)) is not int or attempt[field] < 0:
                    raise DownloadBudgetError("Invalid Gate 1 attempt byte counter")
        for kind in ("zip", "checksum"):
            for field in ("body_bytes", "reserved_bytes"):
                total = sum(a[field] for a in self.state["attempts"] if a["kind"] == kind)
                if total != self.state[f"{kind}_{field}"]:
                    raise DownloadBudgetError("Gate 1 ledger byte totals do not reconcile")
        if self.zip_remaining < 0 or self.checksum_remaining < 0:
            raise DownloadBudgetError("Gate 1 ledger exceeds the frozen byte budget")
        if self.state.get("holdout_requests") != 0 or self.state.get("holdout_access_count") != 0:
            raise DownloadBudgetError("Gate 1 ledger records forbidden access")

    def _require_active(self):
        if not self._active:
            raise DownloadBudgetError("Gate 1 ledger must be used inside its exclusive context")

    @property
    def zip_remaining(self) -> int:
        return self.contract["zip_budget_bytes"] - self.state["zip_body_bytes"] - self.state["zip_reserved_bytes"]

    @property
    def checksum_remaining(self) -> int:
        return self.contract["checksum_budget_bytes"] - self.state["checksum_body_bytes"] - self.state["checksum_reserved_bytes"]

    def save(self):
        self._require_active()
        self._validate_state()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.state["total_bytes_downloaded"] = self.state["zip_body_bytes"]
        self.state["checksum_bytes_downloaded"] = self.state["checksum_body_bytes"]
        self.state["zip_charged_bytes"] = self.state["zip_body_bytes"] + self.state["zip_reserved_bytes"]
        self.state["checksum_charged_bytes"] = self.state["checksum_body_bytes"] + self.state["checksum_reserved_bytes"]
        self.state["updated_at_utc"] = _now()
        payload = (json.dumps(self.state, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        self.last_persistence_diagnostics = _atomic_json_with_retry(self.path, payload)

    def start_get(self, url: str, kind: str, month: str, month_attempt: int = 1) -> dict:
        self._require_active()
        if kind not in {"zip", "checksum"} or research_policy.ensure_month_in_historical_allowlist(month) != month:
            raise data.DataValidationError("Invalid Gate 1 transfer kind or month")
        expected = _historical_url(month, ".zip.CHECKSUM" if kind == "checksum" else ".zip")
        start, end = research_policy.historical_month_bounds(month)
        research_policy.ensure_not_holdout(start, end)
        if (url != expected or type(month_attempt) is not int or not 1 <= month_attempt <= 3
                or self.state["status"] != "in_progress"):
            raise data.DataValidationError("Invalid Gate 1 request URL, attempt or stopped state")
        scope = self.state.get("active_attempt_scope")
        if any(a["month"] == month and a["kind"] == kind and a["month_attempt"] == month_attempt
               and a.get("scope") == scope
               for a in self.state["attempts"]):
            raise DownloadBudgetError("A month attempt cannot repeat the same GET")
        if self.state["http_gets_count"] >= 408:
            raise DownloadBudgetError("Gate 1 HTTP GET cap 408 exceeded")
        self.state["http_gets_count"] += 1
        attempt = {"id": len(self.state["attempts"]), "month": month, "url": url, "kind": kind,
                   "month_attempt": month_attempt,
                   "started_at_utc": _now(), "status": "started", "body_bytes": 0, "reserved_bytes": 0}
        if scope is not None:
            attempt["scope"] = scope
        self.state["attempts"].append(attempt)
        self.save()
        return attempt

    def reserve(self, kind: str, count: int, attempt: dict):
        self._require_active()
        if attempt not in self.state["attempts"] or attempt["kind"] != kind or type(count) is not int:
            raise DownloadBudgetError("Invalid Gate 1 reservation owner")
        field = f"{kind}_remaining"
        if count <= 0 or count > getattr(self, field):
            raise DownloadBudgetError(f"Gate 1 {kind} byte budget exhausted")
        self.state[f"{kind}_reserved_bytes"] += count
        attempt["reserved_bytes"] += count
        self.save()

    def settle(self, kind: str, reserved: int, received: int, attempt: dict):
        self._require_active()
        if (attempt not in self.state["attempts"] or attempt["kind"] != kind
                or type(reserved) is not int or type(received) is not int
                or not (0 <= received <= reserved <= attempt["reserved_bytes"])):
            raise DownloadBudgetError("Invalid Gate 1 transfer settlement")
        self.state[f"{kind}_reserved_bytes"] -= reserved
        self.state[f"{kind}_body_bytes"] += received
        attempt["reserved_bytes"] -= reserved
        attempt["body_bytes"] += received
        self.save()

    def add_month(self, entry: dict):
        if (self.state["status"] != "in_progress" or any(e["month"] == entry["month"] for e in self.state["months"])):
            raise data.DataValidationError("Gate 1 cannot duplicate a month or continue after failure")
        self.state["months"].append(entry)
        self.save()

    def __enter__(self):
        original = "results/phase3a-gate1/network-ledger.json"
        if self.path != _historical_contract_path(original, original):
            raise DownloadBudgetError("Continuation ledger cannot be initialized from zero")
        if self._active:
            raise DownloadBudgetError("Gate 1 ledger context cannot be nested")
        self._lock = _HistoricalDownloadLock()
        self._lock.__enter__()
        try:
            if self.path.exists():
                self.state = json.loads(_limited_read(self.path, 16 * 1024 * 1024))
                self._validate_state()
                raise DownloadBudgetError("Existing Gate 1 ledger retained; automatic resume/reset is forbidden")
            self.state = self._new_state()
            self._active = True
            self.save()
            return self
        except BaseException:
            self._active = False
            self._lock.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        try:
            if args and args[0] is not None and self.state["status"] == "in_progress":
                self.state["status"] = "failed"
                self.state["error_type"] = args[0].__name__
                self.state["error"] = str(args[1])
                self.save()
            elif self.state["status"] == "in_progress":
                self.state["status"] = "incomplete"
                self.state["finished_at_utc"] = _now()
                self.save()
        finally:
            self._active = False
            self._lock.__exit__(*args)
        return False

    def finish(self, implementation_commit: str) -> None:
        """Publish a completed ledger only after all ordered months passed."""
        if (tuple(e["month"] for e in self.state["months"]) != self.contract["months"]
                or self.state["status"] != "in_progress"
                or re.fullmatch(r"[0-9a-f]{40}", implementation_commit) is None):
            raise data.DataValidationError("Cannot complete an incomplete Gate 1 ledger")
        self.state.update(status="passed", finished_at_utc=_now(), final_commit_sha=implementation_commit)
        self.save()


class HistoricalContinuationLedger(HistoricalNetworkLedger):
    """Resume only the D-032-approved Jan/Feb prefix before March onward."""

    def __init__(self, contract: dict):
        super().__init__(contract, ledger_path="results/phase3a-gate1/network-ledger-continuation.json")

    def __enter__(self):
        if self._active:
            raise DownloadBudgetError("Gate 1 continuation context cannot be nested")
        self._lock = _HistoricalDownloadLock()
        self._lock.__enter__()
        try:
            if not self.path.exists():
                raise DownloadBudgetError("No D-032 continuation ledger exists")
            self.state = json.loads(_limited_read(self.path, 16 * 1024 * 1024))
            self._validate_state()
            if (self.state.get("status") != "ready_to_resume"
                    or tuple(entry.get("month") for entry in self.state["months"]) != ("2020-01", "2020-02")
                    or self.state.get("http_gets_count") != 6
                    or self.state.get("prior_failed_month_history", {}).get("month") != "2020-02"
                    or self.state.get("prior_failed_month_history", {}).get("status") != "failed"):
                raise DownloadBudgetError("Continuation ledger does not match the exact D-032 checkpoint")
            self._active = True
            verified_prefix = _verify_d032_continuation_prefix(self.contract, self.state, ledger=self)
            self.state["months"] = verified_prefix
            self.state["status"] = "in_progress"
            self.state["resume_started_at_utc"] = _now()
            self.save()
            return self
        except BaseException:
            self._active = False
            self._lock.__exit__(None, None, None)
            raise


def _historical_url(month: str, suffix: str = ".zip") -> str:
    research_policy.ensure_month_in_historical_allowlist(month)
    return f"{BASE_URL}/BTCUSDT-1m-{month}{suffix}"


def _open_historical_response(request):
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=30)


def _historical_fetch_once(url: str, kind: str, month: str, contract: dict,
                           ledger: HistoricalNetworkLedger, month_attempt: int,
                           *, destination: Path | None = None,
                           open_response=None) -> tuple[bytes | Path, dict]:
    if url not in {_historical_url(month), _historical_url(month, ".zip.CHECKSUM")}:
        raise data.DataValidationError("Historical URL is outside exact allowlist")
    if kind not in {"zip", "checksum"} or (kind == "checksum") != url.endswith(".CHECKSUM"):
        raise data.DataValidationError("Historical payload kind does not match URL")
    limit = contract["per_zip_limit_bytes"] if kind == "zip" else contract["per_checksum_limit_bytes"]
    request = urllib.request.Request(url, headers={"User-Agent": "QuantForge/0.1 noncommercial historical research", "Accept-Encoding": "identity"})
    attempt = ledger.start_get(url, kind, month, month_attempt)
    started = time.perf_counter()
    output = None
    keep_output = False
    try:
        with (open_response or _open_historical_response)(request) as response:
            status = response.status
            if status != 200:
                if status == 429 or 500 <= status < 600:
                    raise urllib.error.HTTPError(url, status, f"HTTP {status}", response.headers, None)
                raise data.DataValidationError(f"Unexpected Gate 1 HTTP status {status}")
            if response.url != url:
                raise data.DataValidationError("Unexpected Gate 1 HTTP status or redirect")
            length_text = response.headers.get("Content-Length")
            if length_text is None or not re.fullmatch(r"\d+", length_text):
                raise data.DataValidationError("Gate 1 response requires Content-Length")
            length = int(length_text)
            if length <= 0 or length > limit:
                raise DownloadBudgetError("Gate 1 response exceeds per-file limit")
            if length > (ledger.zip_remaining if kind == "zip" else ledger.checksum_remaining):
                raise DownloadBudgetError("Gate 1 response exceeds remaining byte budget")
            parts, consumed, digest = [], 0, hashlib.sha256()
            if destination is not None:
                destination = _fixed_path(destination)
                destination.parent.mkdir(parents=True, exist_ok=True)
                output = destination.open("xb")
            while consumed < length:
                requested = min(READ_CHUNK, length - consumed)
                ledger.reserve(kind, requested, attempt)
                try:
                    chunk = response.read1(requested)
                except http.client.IncompleteRead as exc:
                    received = len(exc.partial)
                    ledger.settle(kind, requested, received, attempt)
                    raise
                if not chunk:
                    raise http.client.IncompleteRead(b"", length - consumed)
                ledger.settle(kind, requested, len(chunk), attempt)
                digest.update(chunk)
                if output is None:
                    parts.append(chunk)
                else:
                    output.write(chunk)
                consumed += len(chunk)
            if output is not None:
                output.flush()
                os.fsync(output.fileno())
                output.close()
                output = None
                payload = destination
            else:
                payload = b"".join(parts)
            attempt.update({"status": "received", "finished_at_utc": _now(), "http_status": response.status,
                            "content_length": length, "sha256": digest.hexdigest(),
                            "elapsed_seconds": time.perf_counter() - started})
            ledger.save()
            keep_output = destination is not None
            return payload, {"url": url, "http_status": response.status, "bytes": length,
                             "sha256": digest.hexdigest(), "attempt": attempt["id"],
                             "month_attempt": month_attempt, "started_at_utc": attempt["started_at_utc"],
                             "finished_at_utc": attempt["finished_at_utc"]}
    except Exception as exc:
        if output is not None:
            output.close()
        attempt.update({"status": "failed", "finished_at_utc": _now(), "error_type": type(exc).__name__,
                        "error": str(exc), "elapsed_seconds": time.perf_counter() - started})
        if isinstance(exc, urllib.error.HTTPError):
            attempt["http_status"] = exc.code
            exc.close()
        ledger.save()
        raise
    finally:
        if destination is not None and not keep_output and destination.exists():
            try:
                destination.unlink()
            except OSError:
                pass


def _historical_retryable(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or 500 <= exc.code < 600
    return isinstance(exc, (TimeoutError, ConnectionError, urllib.error.URLError, http.client.IncompleteRead))


def _historical_extract_csv(archive: bytes, month: str, contract: dict) -> bytes:
    if len(archive) > contract["per_zip_limit_bytes"]:
        raise data.DataValidationError("Historical ZIP exceeds configured limit")
    expected = f"BTCUSDT-1m-{month}.csv"
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            members = zipped.infolist()
            if len(members) != 1 or members[0].filename != expected or members[0].is_dir():
                raise data.DataValidationError("Historical ZIP must contain exactly expected CSV")
            member = members[0]
            if member.flag_bits & 1 or member.file_size > contract["per_csv_limit_bytes"]:
                raise data.DataValidationError("Encrypted or oversized historical CSV")
            with zipped.open(member) as source:
                payload = source.read(contract["per_csv_limit_bytes"] + 1)
            if len(payload) != member.file_size or len(payload) > contract["per_csv_limit_bytes"]:
                raise data.DataValidationError("Historical CSV exceeds decompression limit")
            return payload
    except (zipfile.BadZipFile, RuntimeError, EOFError) as exc:
        raise data.DataValidationError("Invalid historical ZIP/CRC/compression") from exc


def inspect_historical_archive(archive: bytes, checksum: bytes, month: str, contract: dict,
                               *, ledger: HistoricalNetworkLedger | None = None) -> tuple[pd.DataFrame, dict]:
    """Verify archive/checksum and parse one complete calendar month."""
    research_policy.ensure_month_in_historical_allowlist(month)
    start, end = research_policy.historical_month_bounds(month)
    research_policy.ensure_not_holdout(start, end)
    if not isinstance(archive, bytes) or not isinstance(checksum, bytes):
        raise data.DataValidationError("Historical archive and checksum must be bounded byte strings")
    if len(archive) > contract["per_zip_limit_bytes"] or len(checksum) > contract["per_checksum_limit_bytes"]:
        raise data.DataValidationError("Historical archive/checksum exceeds its fixed size limit")
    filename = f"BTCUSDT-1m-{month}.zip"
    checksum_text_sha = data.sha256(checksum)
    zip_sha = data.sha256(archive)
    preliminary = {"month": month, "zip_sha256": zip_sha, "checksum_text_sha256": checksum_text_sha,
                   "checksum_bytes": len(checksum), "archive_bytes": len(archive)}
    if ledger is not None:
        ledger.state["last_month_diagnostics"] = preliminary
        ledger.save()
    try:
        checksum_text = checksum.decode("ascii")
        data.verify_checksum(archive, checksum_text, filename)
    except Exception as exc:
        exc.gate1_audit = preliminary
        raise
    csv_bytes = _historical_extract_csv(archive, month, contract)
    csv_sha = data.sha256(csv_bytes)
    raw_lines = csv_bytes.splitlines()
    row_count = len(raw_lines)
    first_open_raw = raw_lines[0].split(b",", 1)[0].decode("ascii", errors="replace") if raw_lines else None
    last_open_raw = raw_lines[-1].split(b",", 1)[0].decode("ascii", errors="replace") if raw_lines else None
    preliminary.update({"csv_sha256": csv_sha, "csv_bytes": len(csv_bytes), "raw_rows_read": row_count,
                        "first_open_raw": first_open_raw, "last_open_raw": last_open_raw})
    if ledger is not None:
        ledger.state["market_rows_read"] += row_count
        ledger.state["last_month_diagnostics"] = preliminary
        ledger.save()
    try:
        frame, audit = parse_historical_monthly_klines(csv_bytes, month)
    except Exception as exc:
        exc.gate1_audit = preliminary
        raise
    audit = dict(audit, zip_sha256=zip_sha, checksum_text_sha256=checksum_text_sha, csv_sha256=csv_sha,
                 checksum_bytes=len(checksum), csv_bytes=len(csv_bytes), raw_rows_read=row_count,
                 first_open_raw=first_open_raw, last_open_raw=last_open_raw,
                 timestamp_unit=audit["timestamp_unit_raw"])
    if ledger is not None:
        ledger.state["last_month_diagnostics"] = audit
        ledger.save()
    return frame, audit


def _atomic_historical_bytes(path: Path, payload: bytes):
    path = _fixed_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise data.DataValidationError("Historical output already exists; overwrite is forbidden")
    temporary = _fixed_path(path.parent / f".{path.name}.{uuid4().hex}.part")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _historical_temp_path(final_path: Path, suffix: str) -> Path:
    final_path = _fixed_path(final_path)
    return _fixed_path(final_path.parent / f".{final_path.name}.{suffix}.{uuid4().hex}.part")


def _stage_historical_bytes(final_path: Path, payload: bytes, suffix: str) -> Path:
    temporary = _historical_temp_path(final_path, suffix)
    temporary.parent.mkdir(parents=True, exist_ok=True)
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _publish_historical_part(temporary: Path, final_path: Path):
    temporary, final_path = _fixed_path(temporary), _fixed_path(final_path)
    if final_path.exists():
        raise data.DataValidationError("Historical output already exists; overwrite is forbidden")
    os.link(temporary, final_path)


def download_historical_month(month: str, contract: dict, *, ledger: HistoricalNetworkLedger | None = None) -> dict:
    """Download one strict complete historical month; preserve the original Gate 1 behavior."""
    return _download_historical_month(month, contract, ledger=ledger, graded=False)


def download_historical_graded_month(
    month: str, contract: dict, *, ledger: HistoricalNetworkLedger | None = None,
) -> dict:
    """Download one historical month under the D-031 graded integrity policy."""
    return _download_historical_month(month, contract, ledger=ledger, graded=True)


def _download_historical_month(
    month: str, contract: dict, *, ledger: HistoricalNetworkLedger | None, graded: bool,
) -> dict:
    """Download one ordered month with the selected strict or graded policy."""
    research_policy.ensure_month_in_historical_allowlist(month)
    start, end = research_policy.historical_month_bounds(month)
    research_policy.ensure_not_holdout(start, end)
    contract = _validate_historical_contract(contract)
    if ledger is None:
        with HistoricalNetworkLedger(contract) as owned:
            return _download_historical_month(month, contract, ledger=owned, graded=graded)
    ledger._require_active()
    if not research_policy._same_typed_value(ledger.contract, contract):
        raise data.DataValidationError("Ledger and month contract disagree")
    position = len(ledger.state["months"])
    if position >= len(contract["months"]) or month != contract["months"][position]:
        raise data.DataValidationError("Gate 1 must process the complete ordered month sequence")
    if ledger.state["status"] != "in_progress":
        raise data.DataValidationError("Gate 1 cannot continue after an earlier failure")
    paths = _historical_paths(month, contract)
    attempts = 0
    started = _now()
    staged: list[Path] = []
    published: list[Path] = []
    entry = None
    try:
        if graded:
            _check_historical_partial_gap_budget(ledger.state["months"], contract)
        if any(path.exists() for path in paths.values()):
            raise data.DataValidationError("Historical output exists; overwrite/resume is forbidden")
        _historical_disk_gate(contract)
        for attempts in range(1, contract["max_attempts"] + 1):
            raw_part = _historical_temp_path(paths["raw"], f"attempt{attempts}")
            staged.append(raw_part)
            try:
                checksum, checksum_meta = _historical_fetch_once(
                    _historical_url(month, ".zip.CHECKSUM"), "checksum", month,
                    contract, ledger, attempts)
                # A malformed checksum is an integrity error before any ZIP GET.
                _validate_historical_checksum(checksum, month)
                if hasattr(ledger, "validate_received_checksum"):
                    ledger.validate_received_checksum(month, checksum)
                _, archive_meta = _historical_fetch_once(
                    _historical_url(month), "zip", month, contract, ledger, attempts,
                    destination=raw_part)
                break
            except Exception as exc:
                if raw_part.exists():
                    raw_part.unlink()
                if not _historical_retryable(exc) or attempts == contract["max_attempts"]:
                    raise
                time.sleep(contract["backoff_seconds"][attempts - 1])
        archive = _limited_read(raw_part, contract["per_zip_limit_bytes"])
        if graded:
            frame, audit = inspect_historical_graded_archive(archive, checksum, month, contract, ledger=ledger)
        else:
            frame, audit = inspect_historical_archive(archive, checksum, month, contract, ledger=ledger)
        output = io.BytesIO()
        frame.to_parquet(output, index=False)
        parquet = output.getvalue()
        pd.testing.assert_frame_equal(frame, pd.read_parquet(io.BytesIO(parquet)), check_exact=True)
        month_gets = [a for a in ledger.state["attempts"] if a["month"] == month
                      and a.get("scope") == ledger.state.get("active_attempt_scope")]
        zip_received = sum(a["body_bytes"] for a in month_gets if a["kind"] == "zip")
        checksum_received = sum(a["body_bytes"] for a in month_gets if a["kind"] == "checksum")
        expected_unit = "us" if start >= pd.Timestamp(contract["config"]["timestamp"]["us_window_start"]) else "ms"
        entry = {
            "month": month, "status": "passed", "url": _historical_url(month),
            "checksum_url": _historical_url(month, ".zip.CHECKSUM"),
            "started_at_utc": started, "fetched_at_utc": archive_meta["finished_at_utc"],
            "finished_at_utc": _now(), "http_status": 200, "attempts": attempts,
            "http_gets": len(month_gets), "bytes_downloaded": zip_received,
            "checksum_bytes_downloaded": checksum_received,
            "zip_reserved_bytes": sum(a["reserved_bytes"] for a in month_gets if a["kind"] == "zip"),
            "checksum_reserved_bytes": sum(a["reserved_bytes"] for a in month_gets if a["kind"] == "checksum"),
            "zip_bytes": len(archive), "checksum_bytes": len(checksum), "parquet_bytes": len(parquet),
            "zip_sha256": audit["zip_sha256"], "checksum_text_sha256": audit["checksum_text_sha256"],
            "sha256_official_checksum_file": audit["checksum_text_sha256"],
            "sha256_zip_computed": audit["zip_sha256"],
            "parquet_sha256": data.sha256(parquet), "csv_sha256": audit["csv_sha256"],
            "rows": len(frame), "timestamp_unit": audit["timestamp_unit"],
            "expected_unit": expected_unit, "detected_unit": audit["timestamp_unit"],
            "first_open_raw": audit["first_open_raw"],
            "first_open_utc": audit["first_open_utc"], "last_open_utc": audit["last_open_utc"],
            "audit": audit,
        }
        if graded:
            entry.update({
                "csv_bytes": audit["csv_bytes"],
                "last_open_raw": audit["last_open_raw"],
                "leading_missing_minutes": audit["leading_missing_minutes"],
                "trailing_missing_minutes": audit["trailing_missing_minutes"],
                "expected_minutes": audit["expected_minutes"], "actual_minutes": audit["actual_minutes"],
                "missing_minutes": audit["missing_minutes"], "missing_ratio": audit["missing_ratio"],
                "anomalous_close_time_rows": audit["anomalous_close_time_rows"],
                "role": audit["role"], "role_in_protocol": audit["role_in_protocol"],
                "status_tier": audit["status_tier"], "gap_ranges": audit["gap_ranges"],
                "notes": _historical_notes_text(audit["notes"]),
            })
            _validate_historical_graded_entry(entry, month, contract)
            _check_historical_partial_gap_budget([*ledger.state["months"], entry], contract)
        else:
            _validate_historical_month_entry(entry, month, contract)
        if ledger.state["months"] and graded:
            previous = ledger.state["months"][-1]
            previous_audit = previous.get("audit", {})
            trailing = previous_audit.get("trailing_missing_minutes")
            leading = audit["leading_missing_minutes"]
            observed_span = int((pd.Timestamp(entry["first_open_utc"])
                                 - pd.Timestamp(previous["last_open_utc"])) / pd.Timedelta(minutes=1))
            if (type(trailing) is not int or trailing < 0
                    or observed_span != trailing + leading + 1):
                raise data.DataValidationError("Historical cross-month gap metadata is inconsistent")
        elif ledger.state["months"]:
            previous = ledger.state["months"][-1]
            if pd.Timestamp(entry["first_open_utc"]) - pd.Timestamp(previous["last_open_utc"]) != pd.Timedelta(minutes=1):
                raise data.DataValidationError("Historical cross-month boundary is not exactly one minute")
        checksum_part = _stage_historical_bytes(paths["checksum"], checksum, "validated")
        staged.append(checksum_part)
        parquet_part = _stage_historical_bytes(paths["parquet"], parquet, "validated")
        staged.append(parquet_part)
        source_bytes = _historical_json_bytes(entry)
        source_part = _stage_historical_bytes(paths["source"], source_bytes, "validated")
        staged.append(source_part)
        _historical_disk_gate(contract)
        for temporary, key in ((raw_part, "raw"), (checksum_part, "checksum"),
                               (parquet_part, "parquet"), (source_part, "source")):
            _publish_historical_part(temporary, paths[key])
            published.append(paths[key])
        ledger.add_month(entry)
        return entry
    except BaseException as exc:
        for path in reversed(published):
            _fixed_path(path).unlink(missing_ok=True)
        for path in staged:
            _fixed_path(path).unlink(missing_ok=True)
        if entry is not None:
            ledger.state["months"] = [e for e in ledger.state["months"] if e.get("month") != month]
        failure = {
            "status": "failed", "month": month, "started_at_utc": started, "failed_at_utc": _now(),
            "attempts": attempts, "error_type": type(exc).__name__, "error": str(exc),
            "diagnostics": getattr(exc, "gate1_audit", ledger.state.get("last_month_diagnostics")),
            "zip_body_bytes": ledger.state["zip_body_bytes"],
            "zip_reserved_bytes": ledger.state["zip_reserved_bytes"],
            "checksum_body_bytes": ledger.state["checksum_body_bytes"],
            "checksum_reserved_bytes": ledger.state["checksum_reserved_bytes"],
            "http_gets_count": ledger.state["http_gets_count"],
            "month_http_attempts": [a for a in ledger.state["attempts"] if a["month"] == month],
        }
        ledger.state.update(status="failed", failed_month=month, failure=failure)
        ledger.save()
        failure_path = _historical_contract_path(f"results/phase3a-gate1/failures/{month}.json",
                                                 f"results/phase3a-gate1/failures/{month}.json")
        _atomic_historical_bytes(failure_path, _historical_json_bytes(failure))
        raise
    finally:
        for path in staged:
            _fixed_path(path).unlink(missing_ok=True)


def _validate_historical_checksum(checksum: bytes, month: str) -> str:
    if not isinstance(checksum, bytes) or not 0 < len(checksum) <= 4096:
        raise data.DataValidationError("Historical CHECKSUM exceeds 4096 bytes or is empty")
    try:
        lines = checksum.decode("ascii").strip().splitlines()
    except UnicodeDecodeError as exc:
        raise data.DataValidationError("Historical CHECKSUM must be ASCII") from exc
    expected_name = f"BTCUSDT-1m-{month}.zip"
    match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?([^\s]+)", lines[0]) if len(lines) == 1 else None
    if match is None or match.group(2) != expected_name:
        raise data.DataValidationError("Malformed official historical CHECKSUM or wrong filename")
    return match.group(1).lower()


def _historical_json_bytes(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")


def _historical_disk_gate(contract: dict):
    import shutil

    root = data.REPOSITORY_ROOT.resolve()
    if shutil.disk_usage(root).free < contract["disk_reserve_bytes"]:
        raise DownloadBudgetError("Gate 1 requires at least 8 GiB free disk reserve")
    used = 0
    for relative in (contract["output_root_relative"], "data/processed/binance/spot/BTCUSDT/1m/historical"):
        folder = _historical_contract_path(relative, relative)
        if folder.exists():
            for path in folder.iterdir():
                fixed = _fixed_path(path)
                if fixed.is_dir():
                    raise data.DataValidationError("Historical output must be a flat fixed directory")
                used += fixed.stat().st_size
    if used > contract["disk_reserve_bytes"]:
        raise DownloadBudgetError("Gate 1 historical disk footprint exceeds 8 GiB")


def _validate_historical_month_entry(entry: dict, month: str, contract: dict) -> None:
    research_policy.ensure_month_in_historical_allowlist(month)
    if not isinstance(entry, dict) or entry.get("month") != month:
        raise data.DataValidationError("Historical manifest entry month mismatch")
    start, end = research_policy.historical_month_bounds(month)
    research_policy.ensure_not_holdout(start, end)
    for key in ("zip_sha256", "checksum_text_sha256", "parquet_sha256", "csv_sha256"):
        if not isinstance(entry.get(key), str) or re.fullmatch(r"[0-9a-f]{64}", entry[key]) is None:
            raise data.DataValidationError(f"Invalid historical manifest hash: {key}")
    expected_rows = int((end - start) / pd.Timedelta(minutes=1))
    if type(entry.get("rows")) is not int or entry["rows"] != expected_rows:
        raise data.DataValidationError("Historical manifest row count differs from complete calendar month")
    for key, expected in (("first_open_utc", start), ("last_open_utc", end - pd.Timedelta(minutes=1))):
        try:
            actual = pd.Timestamp(entry[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise data.DataValidationError("Historical manifest lacks valid calendar bounds") from exc
        if actual.tzinfo is None or actual.utcoffset().total_seconds() != 0 or actual != expected:
            raise data.DataValidationError("Historical manifest UTC calendar bounds are not exact")
    expected_unit = "us" if start >= pd.Timestamp(contract["config"]["timestamp"]["us_window_start"]) else "ms"
    if entry.get("timestamp_unit") != expected_unit:
        raise data.DataValidationError("Historical manifest timestamp unit violates policy")
    if type(entry.get("attempts")) is not int or not 1 <= entry["attempts"] <= 3:
        raise data.DataValidationError("Historical manifest attempts must be between one and three")
    if type(entry.get("bytes_downloaded")) is not int or entry["bytes_downloaded"] <= 0:
        raise data.DataValidationError("Historical manifest downloaded bytes must be a positive integer")
    if entry["bytes_downloaded"] > entry["attempts"] * contract["per_zip_limit_bytes"]:
        raise data.DataValidationError("Historical manifest downloaded bytes exceed per-attempt limits")
    try:
        fetched = pd.Timestamp(entry["fetched_at_utc"])
    except (KeyError, TypeError, ValueError) as exc:
        raise data.DataValidationError("Historical manifest fetched time is missing or invalid") from exc
    if pd.isna(fetched) or fetched.tzinfo is None or fetched.utcoffset().total_seconds() != 0:
        raise data.DataValidationError("Historical fetched time must be timezone-aware UTC")
    for key, cap in (("zip_bytes", contract["per_zip_limit_bytes"]),
                     ("checksum_bytes", contract["per_checksum_limit_bytes"])):
        if key in entry and (type(entry[key]) is not int or not 0 < entry[key] <= cap):
            raise data.DataValidationError(f"Historical manifest invalid file size: {key}")
    for key in ("zip_reserved_bytes", "checksum_reserved_bytes", "checksum_bytes_downloaded"):
        if key in entry and (type(entry[key]) is not int or entry[key] < 0):
            raise data.DataValidationError("Historical manifest byte accounting is invalid")
    if "url" in entry and entry["url"] != _historical_url(month):
        raise data.DataValidationError("Historical manifest archive URL is not canonical")
    if "checksum_url" in entry and entry["checksum_url"] != _historical_url(month, ".zip.CHECKSUM"):
        raise data.DataValidationError("Historical manifest checksum URL is not canonical")


def verify_historical_month(month: str, contract: dict, entry: dict) -> dict:
    """Reparse raw ZIP and compare all hashes and Parquet bytes without writes."""
    research_policy.ensure_month_in_historical_allowlist(month)
    contract = _validate_historical_contract(contract)
    _validate_historical_month_entry(entry, month, contract)
    paths = _historical_paths(month, contract)
    # No filename or path read is taken from caller-controlled manifest content.
    source = json.loads(_limited_read(paths["source"], 1024 * 1024))
    if not research_policy._same_typed_value(source, entry):
        raise data.DataValidationError("Historical sidecar differs from frozen manifest entry")
    archive = _limited_read(paths["raw"], contract["per_zip_limit_bytes"])
    checksum = _limited_read(paths["checksum"], contract["per_checksum_limit_bytes"])
    frame, audit = inspect_historical_archive(archive, checksum, month, contract)
    for key in ("zip_sha256", "checksum_text_sha256", "csv_sha256", "rows",
                "timestamp_unit", "first_open_utc", "last_open_utc"):
        if audit[key] != entry[key]:
            raise data.DataValidationError(f"Historical cached {key} differs from manifest")
    if audit != entry.get("audit"):
        raise data.DataValidationError("Historical raw audit differs from persisted audit")
    output = io.BytesIO()
    frame.to_parquet(output, index=False)
    rebuilt = output.getvalue()
    parquet = _limited_read(paths["parquet"], contract["per_csv_limit_bytes"] * 2)
    if data.sha256(rebuilt) != entry["parquet_sha256"] or data.sha256(parquet) != entry["parquet_sha256"]:
        raise data.DataValidationError("Historical Parquet hash differs from manifest or regenerated bytes")
    pd.testing.assert_frame_equal(frame, pd.read_parquet(io.BytesIO(parquet)), check_exact=True)
    return dict(entry, reverified=True, audit=audit)


def build_historical_manifest(entries: list[dict], contract: dict, *, ledger_summary: dict | None = None,
                              protocol_commit: str = "927145671910df600a1081d4ce6478439ca4b0a4") -> dict:
    """Build and validate a deterministic manifest; hash excludes its own field."""
    import copy

    contract = _validate_historical_contract(contract)
    if not isinstance(entries, list) or len(entries) != 68:
        raise data.DataValidationError("Historical manifest requires exactly 68 months")
    for entry, month in zip(entries, contract["months"], strict=True):
        _validate_historical_month_entry(entry, month, contract)
    manifest = {
        "schema_version": 1, "gate": "gate1", "status": "passed", "protocol_commit": protocol_commit,
        "evidence_level": "real_data_integrity_only", "gate2_authorised": False,
        "months": copy.deepcopy(entries), "total_rows": sum(e["rows"] for e in entries),
        "total_bytes_downloaded": sum(e["bytes_downloaded"] for e in entries),
        "checksum_bytes_downloaded": sum(e.get("checksum_bytes_downloaded", e.get("checksum_bytes", 0)) for e in entries),
        "zip_reserved_bytes": sum(e.get("zip_reserved_bytes", 0) for e in entries),
        "checksum_reserved_bytes": sum(e.get("checksum_reserved_bytes", 0) for e in entries),
        "timestamp_unit_counts": {"ms": 60, "us": 8}, "adjacent_month_pairs": 67,
        "network_ledger": copy.deepcopy(ledger_summary or {}),
        "overall_hash_definition": "SHA256 of sorted indented UTF-8/LF JSON with final newline, excluding overall_sha256",
    }
    manifest["overall_sha256"] = data.sha256(_historical_json_bytes(manifest))
    validate_historical_manifest(manifest, contract)
    return manifest


def validate_historical_manifest(manifest: dict, contract: dict) -> None:
    """Validate full calendar coverage, units, budgets and manifest hash."""
    contract = _validate_historical_contract(contract)
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or manifest.get("gate") != "gate1" or manifest.get("status") != "passed"
            or manifest.get("evidence_level") != "real_data_integrity_only"
            or manifest.get("gate2_authorised") is not False):
        raise data.DataValidationError("Invalid historical manifest schema")
    if not isinstance(manifest.get("protocol_commit"), str) or not re.fullmatch(r"[0-9a-f]{40}", manifest["protocol_commit"]):
        raise data.DataValidationError("Historical manifest protocol commit must be a full SHA")
    entries = manifest.get("months")
    if not isinstance(entries, list) or len(entries) != 68:
        raise data.DataValidationError("Historical manifest requires all 68 months")
    counts = {"ms": 0, "us": 0}
    for number, (entry, month) in enumerate(zip(entries, contract["months"], strict=True)):
        _validate_historical_month_entry(entry, month, contract)
        counts[entry["timestamp_unit"]] += 1
        if number:
            if pd.Timestamp(entry["first_open_utc"]) - pd.Timestamp(entries[number-1]["last_open_utc"]) != pd.Timedelta(minutes=1):
                raise data.DataValidationError("Historical manifest has a cross-month gap")
    if counts != {"ms": 60, "us": 8} or manifest.get("timestamp_unit_counts") != counts:
        raise data.DataValidationError("Historical manifest requires 60 ms and 8 us months")
    if manifest.get("adjacent_month_pairs") != 67:
        raise data.DataValidationError("Historical manifest requires 67 adjacent month pairs")
    expected = {
        "total_rows": sum(e["rows"] for e in entries),
        "total_bytes_downloaded": sum(e["bytes_downloaded"] for e in entries),
        "checksum_bytes_downloaded": sum(e.get("checksum_bytes_downloaded", e.get("checksum_bytes", 0)) for e in entries),
        "zip_reserved_bytes": sum(e.get("zip_reserved_bytes", 0) for e in entries),
        "checksum_reserved_bytes": sum(e.get("checksum_reserved_bytes", 0) for e in entries),
    }
    if any(type(manifest.get(k)) is not int or manifest[k] != v for k, v in expected.items()):
        raise data.DataValidationError("Historical manifest aggregate totals do not reconcile")
    if expected["total_bytes_downloaded"] + expected["zip_reserved_bytes"] > contract["zip_budget_bytes"]:
        raise DownloadBudgetError("Historical manifest exceeds ZIP transfer budget")
    if expected["checksum_bytes_downloaded"] + expected["checksum_reserved_bytes"] > contract["checksum_budget_bytes"]:
        raise DownloadBudgetError("Historical manifest exceeds CHECKSUM transfer budget")
    ledger = manifest.get("network_ledger")
    if not isinstance(ledger, dict):
        raise data.DataValidationError("Historical manifest network summary must be a dictionary")
    if ledger:
        for field, value in (("zip_body_bytes", expected["total_bytes_downloaded"]),
                             ("checksum_body_bytes", expected["checksum_bytes_downloaded"]),
                             ("zip_reserved_bytes", expected["zip_reserved_bytes"]),
                             ("checksum_reserved_bytes", expected["checksum_reserved_bytes"])):
            if ledger.get(field) != value:
                raise data.DataValidationError("Historical manifest and network ledger bytes disagree")
        if (type(ledger.get("http_gets_count")) is not int or not 136 <= ledger["http_gets_count"] <= 408
                or ledger.get("holdout_requests") != 0 or ledger.get("holdout_access_count") != 0):
            raise data.DataValidationError("Historical manifest network count or access guard is invalid")
    unhashed = {k: v for k, v in manifest.items() if k != "overall_sha256"}
    expected_hash = data.sha256(_historical_json_bytes(unhashed))
    if manifest.get("overall_sha256") != expected_hash:
        raise data.DataValidationError("Historical manifest overall SHA256 mismatch")


# D-031 graded archive integrity. These APIs are additive and do not alter the
# strict parser, manifest, or downloader above.
_D032_FAILED_MONTH_EXPECTED = {
    "checksum_sha256": "d4002dcc1c1e9367fe6fa2369db5159802b3755092e8d451b7cd955012e0c3bf",
    "zip_sha256": "05aedf91c80f579da396951b1d9e84587d02c9684352e1ff17d4468f556c674e",
    "expected_minutes": 41760,
    "actual_minutes": 41346,
    "missing_minutes": 414,
    "anomalous_close_time_rows": 1,
    "timestamp_unit": "ms",
    "first_open_raw": "1580515200000",
    "last_open_raw": "1583020740000",
    "first_open_utc": "2020-02-01T00:00:00+00:00",
    "last_open_utc": "2020-02-29T23:59:00+00:00",
}


_D032_PRIOR_TRANSFERS = (
    ("2020-01", "checksum", 88, "f9534a5fa863e3fd3458c8b8e8254bc61974a9ed8f094d9b77c705895b7a9c7d"),
    ("2020-01", "zip", 2224194, "02df6da44ed8145fbb9ed819858185d9e2f15eb025c5bec8a4ea2d8738cd0d19"),
    ("2020-02", "checksum", 88, _D032_FAILED_MONTH_EXPECTED["checksum_sha256"]),
    ("2020-02", "zip", 2077466, _D032_FAILED_MONTH_EXPECTED["zip_sha256"]),
)


def classify_historical_integrity(month: str, expected_minutes: int, actual_minutes: int,
                                  anomalous_close_time_rows: int) -> dict:
    """Classify one complete historical archive using exact integer thresholds."""
    research_policy.ensure_month_in_historical_allowlist(month)
    if (any(type(value) is not int for value in
            (expected_minutes, actual_minutes, anomalous_close_time_rows))
            or expected_minutes <= 0 or not 0 < actual_minutes <= expected_minutes
            or not 0 <= anomalous_close_time_rows <= actual_minutes):
        raise data.DataValidationError("Invalid historical integrity counts")
    missing = expected_minutes - actual_minutes
    role = "warmup" if month in research_policy.HISTORICAL_WARMUP_MONTHS else "evaluate"
    if missing * 100 > expected_minutes * 5 or anomalous_close_time_rows > 100:
        raise data.DataValidationError("Historical monthly integrity hard limit exceeded")
    if role == "evaluate" and missing * 100 > expected_minutes * 3:
        raise data.DataValidationError("Historical evaluation month exceeds 3% missing")
    tier = "soft_warning" if missing * 100 > expected_minutes or anomalous_close_time_rows > 10 else "normal"
    return {
        "month": month, "role": role,
        "role_in_protocol": "warmup" if role == "warmup" else "evaluation",
        "status_tier": tier,
        "expected_minutes": expected_minutes, "actual_minutes": actual_minutes,
        "missing_minutes": missing, "missing_ratio": missing / expected_minutes,
        "anomalous_close_time_rows": anomalous_close_time_rows,
    }


def classify_historical_aggregate(total_expected: int, total_missing: int,
                                  evaluate_expected: int, evaluate_missing: int) -> dict:
    """Apply D-031 aggregate gates with exact integer comparisons."""
    values = (total_expected, total_missing, evaluate_expected, evaluate_missing)
    if (any(type(value) is not int for value in values) or total_expected <= 0
            or not 0 <= total_missing <= total_expected or evaluate_expected <= 0
            or not 0 <= evaluate_missing <= evaluate_expected or evaluate_expected > total_expected
            or evaluate_missing > total_missing):
        raise data.DataValidationError("Invalid historical aggregate integrity counts")
    if total_missing * 100 > total_expected:
        raise data.DataValidationError("Aggregate historical missing ratio exceeds 1%")
    if evaluate_missing * 1000 > evaluate_expected * 3:
        raise data.DataValidationError("Evaluation aggregate missing ratio exceeds 0.3%")
    total_tier = "soft_warning" if total_missing * 200 > total_expected else "normal"
    eval_tier = "normal"
    return {
        "aggregate_expected_minutes": total_expected,
        "aggregate_missing_minutes": total_missing,
        "aggregate_missing_ratio": total_missing / total_expected,
        "evaluate_expected_minutes": evaluate_expected,
        "evaluate_aggregate_missing_minutes": evaluate_missing,
        "evaluate_aggregate_missing_ratio": evaluate_missing / evaluate_expected,
        "aggregate_status_tier": total_tier,
        "evaluate_aggregate_status_tier": eval_tier,
    }


def _check_historical_partial_gap_budget(entries: list[dict], contract: dict) -> None:
    """Stop once known gaps alone exceed the full-calendar final hard limit."""
    expected = {m: int((research_policy.historical_month_bounds(m)[1]
                       - research_policy.historical_month_bounds(m)[0]) / pd.Timedelta(minutes=1))
                for m in contract["months"]}
    evaluation = research_policy.HISTORICAL_EVALUATION_MONTHS
    classify_historical_aggregate(
        sum(expected.values()), sum(e["missing_minutes"] for e in entries),
        sum(expected[m] for m in evaluation),
        sum(e["missing_minutes"] for e in entries if e["month"] in evaluation))


def _historical_gap_ranges(start: pd.Timestamp, end: pd.Timestamp,
                           actual: pd.DatetimeIndex) -> list[dict]:
    """Return each contiguous missing minute run as its own inclusive interval."""
    missing = pd.date_range(start, end, inclusive="left", freq="min").difference(actual)
    if missing.empty:
        return []
    ranges = []
    first = previous = missing[0]
    for point in missing[1:]:
        if point - previous != pd.Timedelta(minutes=1):
            ranges.append({
                "start_utc": first.isoformat(), "end_utc": previous.isoformat(),
                "minutes": int((previous - first) / pd.Timedelta(minutes=1)) + 1,
            })
            first = point
        previous = point
    ranges.append({
        "start_utc": first.isoformat(), "end_utc": previous.isoformat(),
        "minutes": int((previous - first) / pd.Timedelta(minutes=1)) + 1,
    })
    return ranges


def _historical_notes_text(notes: list[str]) -> str:
    if not notes:
        return "none"
    return "; ".join(notes)


def parse_historical_graded_klines(
    csv_bytes: bytes,
    month: str,
    config_path: str | Path = "configs/history-extension.toml",
) -> tuple[pd.DataFrame, dict]:
    """Preserve received rows while applying the D-031 graded gap policy."""
    research_policy.ensure_month_in_historical_allowlist(month)
    start, end = research_policy.historical_month_bounds(month)
    research_policy.ensure_not_holdout(start, end)
    unit = _historical_timestamp_gate(csv_bytes, month, config_path)
    try:
        frame = pd.read_csv(io.BytesIO(csv_bytes), header=None, dtype=str, keep_default_na=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as exc:
        raise data.DataValidationError("Unreadable historical CSV") from exc
    raw_lines = csv_bytes.splitlines()
    if (frame.empty or frame.shape[1] != len(data.COLUMNS)
            or len(raw_lines) != len(frame) or any(not line.strip() for line in raw_lines)):
        raise data.DataValidationError("Historical CSV must contain exactly 12 populated columns per source row")
    frame.columns = data.COLUMNS
    for column in ("open_time", "close_time", "number_of_trades"):
        frame[column] = data._integer_column(frame[column], column)
    raw_open = frame["open_time"].copy()
    raw_close = frame["close_time"].copy()
    numeric_columns = data.PRICE_COLUMNS + data.VOLUME_COLUMNS + ["ignore"]
    try:
        for column in numeric_columns:
            frame[column] = pd.to_numeric(frame[column], errors="raise").astype("float64")
    except (ValueError, TypeError) as exc:
        raise data.DataValidationError("Non-numeric historical OHLCV field") from exc
    try:
        frame["open_time"] = pd.to_datetime(raw_open, unit=unit, utc=True)
        frame["close_time"] = pd.to_datetime(raw_close, unit=unit, utc=True)
    except (ValueError, OverflowError) as exc:
        raise data.DataValidationError("Historical timestamp conversion failed") from exc
    frame["open_time_raw"] = raw_open
    frame["close_time_raw"] = raw_close
    actual = pd.DatetimeIndex(frame["open_time"])
    expected = pd.date_range(start, end, inclusive="left", freq="min")
    if (actual.has_duplicates or not actual.is_monotonic_increasing
            or len(actual.difference(expected)) or len(frame) > len(expected)):
        raise data.DataValidationError("Historical opens are duplicated, unordered or off-grid")
    numeric = frame[numeric_columns].to_numpy(dtype="float64", copy=False)
    import numpy as np

    if not np.isfinite(numeric).all():
        raise data.DataValidationError("Nonfinite historical OHLCV values")
    bad_ohlc = (
        (frame[data.PRICE_COLUMNS] <= 0).any(axis=1) | (frame.low > frame.high)
        | (frame.open < frame.low) | (frame.open > frame.high)
        | (frame.close < frame.low) | (frame.close > frame.high)
    )
    bad_volume = (
        (frame[data.VOLUME_COLUMNS] < 0).any(axis=1)
        | (frame.taker_buy_base_asset_volume > frame.volume + 1e-8)
        | (frame.taker_buy_quote_asset_volume > frame.quote_asset_volume + 1e-8)
    )
    if bad_ohlc.any() or bad_volume.any():
        raise data.DataValidationError("Invalid historical OHLCV rows")
    ticks_per_minute = 60_000_000 if unit == "us" else 60_000
    anomalous = int((raw_close - raw_open != ticks_per_minute - 1).sum())
    classification = classify_historical_integrity(month, len(expected), len(frame), anomalous)
    gaps = _historical_gap_ranges(start, end, actual)
    if sum(gap["minutes"] for gap in gaps) != classification["missing_minutes"]:
        raise data.DataValidationError("Historical gap audit count mismatch")
    leading = int((actual[0] - start) / pd.Timedelta(minutes=1))
    trailing = int((end - pd.Timedelta(minutes=1) - actual[-1]) / pd.Timedelta(minutes=1))
    notes = []
    if classification["missing_minutes"]:
        notes.append(f"{classification['missing_minutes']} missing minutes in {len(gaps)} exact ranges; no filling")
    if anomalous:
        notes.append(f"{anomalous} close-time span anomalies retained in source rows")
    if not notes:
        notes.append("none")
    audit = {
        **classification, "timestamp_unit": unit, "timestamp_unit_raw": unit,
        "rows": len(frame), "expected_rows": len(expected), "gap_ranges": gaps,
        "leading_missing_minutes": leading, "trailing_missing_minutes": trailing,
        "first_open_utc": actual[0].isoformat(), "last_open_utc": actual[-1].isoformat(),
        "first_open_raw": str(raw_open.iloc[0]), "last_open_raw": str(raw_open.iloc[-1]),
        "first_close_raw": str(raw_close.iloc[0]), "last_close_raw": str(raw_close.iloc[-1]),
        "notes": notes, "duplicate_open_times": 0, "unexpected_open_times": 0,
        "sorted_ascending": True, "invalid_ohlc_rows": 0, "invalid_volume_rows": 0,
        "nonfinite_numeric_rows": 0, "invalid_close_duration_rows": anomalous,
    }
    return frame, audit


def inspect_historical_graded_archive(
    archive: bytes,
    checksum: bytes,
    month: str,
    contract: dict,
    *,
    ledger: HistoricalNetworkLedger | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Verify official archive bytes, then parse under the graded policy."""
    research_policy.ensure_month_in_historical_allowlist(month)
    start, end = research_policy.historical_month_bounds(month)
    research_policy.ensure_not_holdout(start, end)
    _validate_historical_checksum(checksum, month)
    if len(archive) > contract["per_zip_limit_bytes"]:
        raise data.DataValidationError("Historical ZIP exceeds configured limit")
    zip_sha = data.verify_checksum(archive, checksum.decode("ascii"), f"BTCUSDT-1m-{month}.zip")
    csv_bytes = _historical_extract_csv(archive, month, contract)
    raw_rows = len(csv_bytes.splitlines())
    preliminary = {
        "month": month, "zip_sha256": zip_sha,
        "checksum_text_sha256": data.sha256(checksum),
        "csv_sha256": data.sha256(csv_bytes), "raw_rows_read": raw_rows,
        "csv_bytes": len(csv_bytes),
    }
    if ledger is not None:
        ledger.state["market_rows_read"] += raw_rows
        ledger.state["last_month_diagnostics"] = preliminary
        ledger.save()
    try:
        frame, audit = parse_historical_graded_klines(
            csv_bytes, month, contract["config_path"])
    except Exception as exc:
        exc.gate1_audit = preliminary
        raise
    audit.update(preliminary, checksum_bytes=len(checksum))
    if ledger is not None:
        ledger.state["last_month_diagnostics"] = audit
        ledger.save()
    return frame, audit


def audit_historical_boundaries(entries: list[dict]) -> list[dict]:
    """Audit 67 adjacent archive edges using observed gap endpoints."""
    if not isinstance(entries, list) or len(entries) < 2:
        raise data.DataValidationError("Boundary audit requires adjacent ordered month entries")
    rows = []
    for previous, current in zip(entries[:-1], entries[1:], strict=True):
        previous_month, month = previous.get("month"), current.get("month")
        research_policy.ensure_month_in_historical_allowlist(previous_month)
        research_policy.ensure_month_in_historical_allowlist(month)
        _, previous_end = research_policy.historical_month_bounds(previous_month)
        current_start, _ = research_policy.historical_month_bounds(month)
        if previous_end != current_start:
            raise data.DataValidationError("Boundary months are not adjacent in the allowlist")
        previous_last = pd.Timestamp(previous["last_open_utc"])
        next_first = pd.Timestamp(current["first_open_utc"])
        previous_audit, current_audit = previous.get("audit"), current.get("audit")
        if not isinstance(previous_audit, dict) or not isinstance(current_audit, dict):
            raise data.DataValidationError("Boundary entries require nested source audits")
        trailing = previous_audit.get("trailing_missing_minutes")
        leading = current_audit.get("leading_missing_minutes")
        if any(type(value) is not int or value < 0 for value in (trailing, leading)):
            raise data.DataValidationError("Boundary gap metadata is invalid")
        if (previous.get("trailing_missing_minutes") != trailing
                or current.get("leading_missing_minutes") != leading
                or type(previous.get("trailing_missing_minutes")) is not int
                or type(current.get("leading_missing_minutes")) is not int
                or previous_audit.get("last_open_utc") != previous.get("last_open_utc")
                or current_audit.get("first_open_utc") != current.get("first_open_utc")):
            raise data.DataValidationError("Boundary top-level and nested audit fields disagree")
        if any(stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0
               or stamp.second or stamp.microsecond or stamp.nanosecond
               for stamp in (previous_last, next_first)):
            raise data.DataValidationError("Boundary timestamps must be UTC minute boundaries")
        observed_span = int((next_first - previous_last) / pd.Timedelta(minutes=1))
        gap_minutes = trailing + leading
        if observed_span != gap_minutes + 1:
            raise data.DataValidationError("Boundary gap counters disagree with observed timestamps")
        rows.append({
            "previous_month": previous_month, "month": month,
            "previous_last_open_utc": previous_last.isoformat(),
            "next_first_open_utc": next_first.isoformat(),
            "previous_trailing_missing_minutes": trailing,
            "next_leading_missing_minutes": leading,
            "gap_minutes": gap_minutes, "span_minutes": gap_minutes + 1,
            "expected_gap_minutes": 0,
            "status": "continuous" if gap_minutes == 0 else "soft_warning",
        })
    return rows


def _graded_entry_from_existing(entry: dict, contract: dict) -> dict:
    month = entry.get("month")
    audit = entry.get("audit")
    if not isinstance(audit, dict):
        raise data.DataValidationError("Historical manifest source entry lacks a parse audit")
    expected = audit.get("expected_minutes", audit.get("expected_rows"))
    actual = audit.get("actual_minutes", audit.get("rows"))
    anomaly_count = audit.get("anomalous_close_time_rows", audit.get("invalid_close_duration_rows"))
    classification = classify_historical_integrity(month, expected, actual, anomaly_count)
    notes = audit.get("notes", [])
    if not isinstance(notes, list) or not all(isinstance(note, str) for note in notes):
        raise data.DataValidationError("Historical manifest notes must be an array of strings")
    if (classification["missing_minutes"] or anomaly_count) and not notes:
        raise data.DataValidationError("Missing minutes or anomalies require explanatory notes")
    if not notes:
        notes = ["none"]
    result = dict(entry)
    result.update(classification)
    result["notes"] = _historical_notes_text(notes)
    result["gap_ranges"] = audit.get("gap_ranges", [])
    result["anomalous_close_time_rows"] = anomaly_count
    return result


def _validate_historical_graded_entry(entry: dict, month: str, contract: dict) -> None:
    research_policy.ensure_month_in_historical_allowlist(month)
    if not isinstance(entry, dict) or entry.get("month") != month:
        raise data.DataValidationError("Historical graded entry month mismatch")
    start, end = research_policy.historical_month_bounds(month)
    research_policy.ensure_not_holdout(start, end)
    for key in ("zip_sha256", "checksum_text_sha256", "parquet_sha256", "csv_sha256"):
        if not isinstance(entry.get(key), str) or re.fullmatch(r"[0-9a-f]{64}", entry[key]) is None:
            raise data.DataValidationError(f"Invalid historical graded hash: {key}")
    expected_minutes = int((end - start) / pd.Timedelta(minutes=1))
    if (type(entry.get("expected_minutes")) is not int or entry["expected_minutes"] != expected_minutes
            or type(entry.get("actual_minutes")) is not int or entry["actual_minutes"] != entry.get("rows")
            or type(entry.get("rows")) is not int or not 0 < entry["rows"] <= expected_minutes):
        raise data.DataValidationError("Historical graded minute counts are invalid")
    classification = classify_historical_integrity(
        month, expected_minutes, entry["rows"], entry.get("anomalous_close_time_rows"))
    for key in ("role", "role_in_protocol", "status_tier", "missing_minutes",
                "anomalous_close_time_rows"):
        if entry.get(key) != classification[key]:
            raise data.DataValidationError("Historical graded classification does not reconcile")
    if (type(entry.get("missing_ratio")) is not float
            or entry["missing_ratio"] != classification["missing_ratio"]):
        raise data.DataValidationError("Historical graded missing ratio is invalid")
    for key in ("first_open_utc", "last_open_utc"):
        try:
            stamp = pd.Timestamp(entry[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise data.DataValidationError("Historical graded entry lacks UTC boundaries") from exc
        if (stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0
                or stamp < start or stamp >= end or stamp.second or stamp.microsecond or stamp.nanosecond):
            raise data.DataValidationError("Historical graded month boundary lies outside its month")
    first, last = pd.Timestamp(entry["first_open_utc"]), pd.Timestamp(entry["last_open_utc"])
    if first > last or (last - first) / pd.Timedelta(minutes=1) < entry["rows"] - 1:
        raise data.DataValidationError("Historical graded timestamp bounds conflict with row count")
    expected_unit = "us" if start >= pd.Timestamp(contract["config"]["timestamp"]["us_window_start"]) else "ms"
    if (entry.get("timestamp_unit") != expected_unit or entry.get("expected_unit") != expected_unit
            or entry.get("detected_unit") != expected_unit):
        raise data.DataValidationError("Historical graded timestamp unit violates policy")
    if type(entry.get("attempts")) is not int or not 1 <= entry["attempts"] <= 3:
        raise data.DataValidationError("Historical graded attempts must be between one and three")
    for key, cap in (("bytes_downloaded", contract["per_zip_limit_bytes"] * 3),
                     ("checksum_bytes_downloaded", contract["per_checksum_limit_bytes"] * 3),
                     ("zip_bytes", contract["per_zip_limit_bytes"]),
                     ("checksum_bytes", contract["per_checksum_limit_bytes"])):
        if type(entry.get(key)) is not int or not 0 < entry[key] <= cap:
            raise data.DataValidationError(f"Historical graded entry has invalid {key}")
    if (type(entry.get("csv_bytes")) is not int or not 0 < entry["csv_bytes"] <= contract["per_csv_limit_bytes"]
            or not isinstance(entry.get("audit"), dict)
            or entry.get("checksum_bytes") != entry["audit"].get("checksum_bytes")):
        raise data.DataValidationError("Historical graded CSV/CHECKSUM byte metadata is invalid")
    notes = entry.get("notes")
    if (not isinstance(notes, str) or not notes.strip()
            or ((entry["missing_minutes"] or entry["anomalous_close_time_rows"])
                and notes.strip().lower() == "none")
            or not isinstance(entry.get("audit"), dict)
            or not isinstance(entry["audit"].get("notes"), list)
            or not all(isinstance(note, str) for note in entry["audit"]["notes"])
            or _historical_notes_text(entry["audit"]["notes"]) != notes):
        raise data.DataValidationError("Historical graded entry requires explanatory notes")
    if month == "2020-02":
        history = entry.get("history")
        if (not isinstance(history, list) or len(history) != 2
                or history[0] != {"event": "failed_download_cleanup", "commit_sha": "1d6e1f2"}
                or not isinstance(history[1], dict)
                or history[1].get("event") != "D-032_refetch_success"
                or history[1].get("event_ledger_path") != "results/phase3a-gate1/continuations/2020-02-refetch.json"
                or (history[1].get("success_commit_sha") is not None
                    and re.fullmatch(r"[0-9a-f]{40}", history[1]["success_commit_sha"]) is None)
                or entry.get("prior_failed_ledger_sha256") is None
                or re.fullmatch(r"[0-9a-f]{64}", entry["prior_failed_ledger_sha256"]) is None):
            raise data.DataValidationError("D-032 February history is incomplete or invalid")
    ranges = entry.get("gap_ranges")
    if not isinstance(ranges, list):
        raise data.DataValidationError("Historical graded entry requires explicit gap ranges")
    missing_count = 0
    previous_end = None
    for gap in ranges:
        try:
            gap_start, gap_end = pd.Timestamp(gap["start_utc"]), pd.Timestamp(gap["end_utc"])
        except (KeyError, TypeError, ValueError) as exc:
            raise data.DataValidationError("Malformed historical gap interval") from exc
        if (gap_start.tzinfo is None or gap_end.tzinfo is None
                or gap_start.utcoffset().total_seconds() != 0 or gap_end.utcoffset().total_seconds() != 0
                or gap_start.second or gap_start.microsecond or gap_start.nanosecond
                or gap_end.second or gap_end.microsecond or gap_end.nanosecond
                or type(gap.get("minutes")) is not int or gap_start < start
                or gap_end >= end or gap_end < gap_start
                or (gap_end - gap_start) % pd.Timedelta(minutes=1)
                or gap.get("minutes") != int((gap_end - gap_start) / pd.Timedelta(minutes=1)) + 1
                or (previous_end is not None and gap_start - previous_end <= pd.Timedelta(minutes=1))):
            raise data.DataValidationError("Invalid or silently combined historical gap interval")
        missing_count += gap["minutes"]
        previous_end = gap_end
    if missing_count != entry["missing_minutes"]:
        raise data.DataValidationError("Historical gap intervals do not match missing count")
    leading = sum(gap["minutes"] for gap in ranges
                  if pd.Timestamp(gap["start_utc"]) == start)
    trailing = sum(gap["minutes"] for gap in ranges
                   if pd.Timestamp(gap["end_utc"]) == end - pd.Timedelta(minutes=1))
    if (entry.get("leading_missing_minutes") != leading
            or entry.get("trailing_missing_minutes") != trailing):
        raise data.DataValidationError("Historical graded edge gap counters do not match explicit intervals")
    if not isinstance(entry.get("audit"), dict):
        raise data.DataValidationError("Historical graded entry lacks source audit")
    nested = entry["audit"]
    audit_fields = {
        "month": "month", "expected_minutes": "expected_minutes", "actual_minutes": "actual_minutes",
        "rows": "rows", "missing_minutes": "missing_minutes", "missing_ratio": "missing_ratio",
        "anomalous_close_time_rows": "anomalous_close_time_rows", "role": "role",
        "role_in_protocol": "role_in_protocol", "status_tier": "status_tier",
        "timestamp_unit": "timestamp_unit", "first_open_utc": "first_open_utc",
        "last_open_utc": "last_open_utc", "first_open_raw": "first_open_raw",
        "last_open_raw": "last_open_raw", "leading_missing_minutes": "leading_missing_minutes",
        "trailing_missing_minutes": "trailing_missing_minutes", "gap_ranges": "gap_ranges",
        "zip_sha256": "zip_sha256", "checksum_text_sha256": "checksum_text_sha256",
        "csv_sha256": "csv_sha256", "checksum_bytes": "checksum_bytes", "csv_bytes": "csv_bytes",
    }
    for key, entry_key in audit_fields.items():
        if nested.get(key) != entry.get(entry_key):
            raise data.DataValidationError(f"Historical graded nested audit disagrees on {key}")
    if nested.get("timestamp_unit_raw") != entry.get("timestamp_unit"):
        raise data.DataValidationError("Historical graded nested raw timestamp unit disagrees")
    if (nested.get("expected_rows") != entry.get("expected_minutes")
            or nested.get("raw_rows_read") != entry.get("rows")):
        raise data.DataValidationError("Historical graded nested row counts disagree")
    for key in ("leading_missing_minutes", "trailing_missing_minutes"):
        if type(nested.get(key)) is not int or nested[key] < 0:
            raise data.DataValidationError("Historical graded month edge counters are invalid")


def _validate_historical_manifest_ledger(ledger: dict, entries: list[dict], contract: dict) -> dict:
    """Validate a completed 68-month ledger and return reconciled byte accounting.

    Successful payload means the retained ZIP/CHECKSUM files. Transfers for
    successful months also include their retry costs. The original failed
    February pair is separate, and remains charged to the cumulative budget.
    This validator reads metadata only and never opens market artifacts.
    """
    if isinstance(ledger, dict) and "d033_recovery" in ledger:
        from quantforge.historical_recovery import validate_d033_manifest_ledger
        return validate_d033_manifest_ledger(ledger, entries, contract)
    contract = _validate_historical_contract(contract)
    if (not isinstance(ledger, dict) or ledger.get("schema_version") != 1
            or ledger.get("phase") != "3A-gate1" or ledger.get("status") != "passed"):
        raise data.DataValidationError("Passed graded manifest requires a completed network ledger")
    for key, expected in (("http_get_cap", 408), ("http_gets_cap", 408),
                          ("zip_budget_bytes", contract["zip_budget_bytes"]),
                          ("checksum_budget_bytes", contract["checksum_budget_bytes"]),
                          ("holdout_requests", 0), ("holdout_access_count", 0)):
        if type(ledger.get(key)) is not int or ledger[key] != expected:
            raise DownloadBudgetError(f"Graded manifest ledger has invalid {key}")
    if (not isinstance(entries, list) or len(entries) != 68
            or any(not isinstance(entry, dict) for entry in entries)
            or tuple(entry.get("month") for entry in entries) != contract["months"]):
        raise data.DataValidationError("Graded manifest ledger requires the complete ordered month set")
    recorded_months = ledger.get("months")
    if (not isinstance(recorded_months, list) or len(recorded_months) != 68
            or not research_policy._same_typed_value(recorded_months, entries)):
        raise data.DataValidationError("Graded manifest entries differ from network ledger months")
    attempts = ledger.get("attempts")
    count = ledger.get("http_gets_count")
    if (not isinstance(attempts, list) or type(count) is not int
            or not 138 <= count <= 408 or count != len(attempts)):
        raise DownloadBudgetError("Graded manifest ledger GET count must reconcile within the 408 cap")
    if (ledger.get("continuation_mode") != "D-032-single-month-refetch"
            or not isinstance(ledger.get("prior_failed_month_history"), dict)
            or ledger["prior_failed_month_history"].get("month") != "2020-02"
            or ledger["prior_failed_month_history"].get("status") != "failed"
            or not isinstance(ledger.get("prior_failed_ledger_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", ledger["prior_failed_ledger_sha256"]) is None):
        raise data.DataValidationError("Graded manifest ledger lacks the D-032 prior failure provenance")
    by_month = {month: [] for month in contract["months"]}
    seen = set()
    previous_order = (-1, 0, -1)
    for identifier, attempt in enumerate(attempts):
        if not isinstance(attempt, dict):
            raise data.DataValidationError("Graded manifest ledger attempt is not an object")
        month, kind, number = attempt.get("month"), attempt.get("kind"), attempt.get("month_attempt")
        if (type(attempt.get("id")) is not int or attempt["id"] != identifier
                or month not in by_month or kind not in {"checksum", "zip"}
                or type(number) is not int or not 1 <= number <= contract["max_attempts"]
                or attempt.get("status") not in {"received", "failed"}):
            raise data.DataValidationError("Graded manifest ledger attempt identity/status is invalid")
        identity = (month, number, kind)
        order = (contract["months"].index(month), number, int(kind == "zip"))
        if identity in seen or order <= previous_order:
            raise data.DataValidationError("Graded manifest ledger attempts are duplicated or unordered")
        seen.add(identity)
        previous_order = order
        suffix = ".zip.CHECKSUM" if kind == "checksum" else ".zip"
        if attempt.get("url") != _historical_url(month, suffix):
            raise data.DataValidationError("Graded manifest ledger attempt URL differs from allowlist")
        cap = contract[f"per_{kind}_limit_bytes"]
        for field in ("body_bytes", "reserved_bytes"):
            if type(attempt.get(field)) is not int or not 0 <= attempt[field] <= cap:
                raise DownloadBudgetError("Graded manifest ledger attempt byte counter is invalid")
        if attempt["body_bytes"] + attempt["reserved_bytes"] > cap:
            raise DownloadBudgetError("Graded manifest ledger attempt exceeds its per-file budget")
        if attempt["status"] == "received":
            if (type(attempt.get("content_length")) is not int
                    or not 0 < attempt["content_length"] == attempt["body_bytes"]
                    or attempt["reserved_bytes"] != 0 or attempt.get("http_status") != 200
                    or not isinstance(attempt.get("sha256"), str)
                    or re.fullmatch(r"[0-9a-f]{64}", attempt["sha256"]) is None):
                raise data.DataValidationError("Graded manifest ledger received payload metadata is invalid")
        by_month[month].append(attempt)
    prior_pair = attempts[2:4]
    expected_prior = (("checksum", 88, _D032_FAILED_MONTH_EXPECTED["checksum_sha256"]),
                      ("zip", 2077466, _D032_FAILED_MONTH_EXPECTED["zip_sha256"]))
    for attempt, (kind, size, digest) in zip(prior_pair, expected_prior, strict=True):
        if (attempt["month"] != "2020-02" or attempt["month_attempt"] != 1
                or attempt["kind"] != kind or attempt["status"] != "received"
                or attempt["body_bytes"] != size or attempt["sha256"] != digest):
            raise data.DataValidationError("Graded manifest ledger prior failed February costs changed")
    payload = {"zip": 0, "checksum": 0}
    successful_transfers = {"zip": 0, "checksum": 0}
    prior_failed = {attempt["kind"]: attempt["body_bytes"] for attempt in prior_pair}
    for entry in entries:
        month = entry["month"]
        month_attempts = by_month[month]
        if (type(entry.get("attempts")) is not int or not 1 <= entry["attempts"] <= 3
                or type(entry.get("http_gets")) is not int):
            raise data.DataValidationError("Graded manifest entry attempt counters are invalid")
        expected_numbers = {1, 2} if month == "2020-02" else set(range(1, entry["attempts"] + 1))
        if ({attempt["month_attempt"] for attempt in month_attempts} != expected_numbers
                or not 2 <= len(month_attempts) <= 6):
            raise data.DataValidationError("Graded manifest ledger month attempt count is invalid")
        for number in sorted(expected_numbers):
            pair = [row for row in month_attempts if row["month_attempt"] == number]
            if (pair[0]["kind"] != "checksum" or len(pair) > 2
                    or (len(pair) == 2 and (pair[1]["kind"] != "zip" or pair[0]["status"] != "received"))
                    or (number < max(expected_numbers) and month != "2020-02"
                        and pair[-1]["status"] != "failed")):
                raise data.DataValidationError("Graded manifest ledger retry sequence is invalid")
        current = [attempt for attempt in month_attempts
                   if month != "2020-02" or attempt["month_attempt"] == 2]
        if (entry.get("http_gets") != len(current)
                or (month == "2020-02" and
                    (entry.get("attempts") != 1 or entry.get("historical_attempts") != 2
                     or entry.get("prior_failed_ledger_sha256") != ledger["prior_failed_ledger_sha256"]))):
            raise data.DataValidationError("Graded manifest month request count/provenance disagrees")
        final_pair = current[-2:]
        for attempt, kind in zip(final_pair, ("checksum", "zip"), strict=True):
            digest_key = "checksum_text_sha256" if kind == "checksum" else "zip_sha256"
            if (attempt["kind"] != kind or attempt["status"] != "received"
                    or attempt["month_attempt"] != max(expected_numbers)
                    or attempt["body_bytes"] != entry.get(f"{kind}_bytes")
                    or attempt["sha256"] != entry.get(digest_key)):
                raise data.DataValidationError("Graded manifest retained payload differs from final received pair")
            body = sum(row["body_bytes"] for row in current if row["kind"] == kind)
            reserved = sum(row["reserved_bytes"] for row in current if row["kind"] == kind)
            transfer_key = "bytes_downloaded" if kind == "zip" else "checksum_bytes_downloaded"
            if (entry.get(transfer_key) != body or entry.get(f"{kind}_reserved_bytes") != reserved):
                raise data.DataValidationError("Graded manifest month transfer totals disagree with attempts")
            payload[kind] += entry[f"{kind}_bytes"]
            successful_transfers[kind] += body
    bodies, reservations, charged = {}, {}, {}
    for kind in ("zip", "checksum"):
        bodies[kind] = sum(attempt["body_bytes"] for attempt in attempts if attempt["kind"] == kind)
        reservations[kind] = sum(attempt["reserved_bytes"] for attempt in attempts if attempt["kind"] == kind)
        charged[kind] = bodies[kind] + reservations[kind]
        if charged[kind] > contract[f"{kind}_budget_bytes"]:
            raise DownloadBudgetError("Graded manifest cumulative transfer exceeds frozen byte budget")
        for field, value in (("body_bytes", bodies[kind]), ("reserved_bytes", reservations[kind]),
                             ("charged_bytes", charged[kind])):
            key = f"{kind}_{field}"
            if type(ledger.get(key)) is not int or ledger[key] != value:
                raise data.DataValidationError(f"Graded manifest ledger cumulative {key} does not reconcile")
        if bodies[kind] != successful_transfers[kind] + prior_failed[kind]:
            raise data.DataValidationError("Graded manifest cumulative bytes omit prior failed transfers")
    for key, value in (("total_bytes_downloaded", bodies["zip"]),
                       ("checksum_bytes_downloaded", bodies["checksum"])):
        if type(ledger.get(key)) is not int or ledger[key] != value:
            raise data.DataValidationError("Graded manifest ledger cumulative downloaded totals disagree")
    return {
        "successful_payload_bytes": payload,
        "successful_month_transfer_body_bytes": successful_transfers,
        "retry_body_bytes": {kind: successful_transfers[kind] - payload[kind] for kind in payload},
        "prior_failed_payload_bytes": prior_failed,
        "cumulative_body_bytes": bodies,
        "unresolved_reserved_bytes": reservations,
        "cumulative_charged_bytes": charged,
        "cumulative_http_gets": count,
    }


def build_historical_graded_manifest(
    entries: list[dict],
    contract: dict,
    *,
    ledger_summary: dict | None = None,
    protocol_commit: str = "927145671910df600a1081d4ce6478439ca4b0a4",
) -> dict:
    """Build the D-031 manifest with per-month and aggregate integrity tiers."""
    import copy

    contract = _validate_historical_contract(contract)
    if not isinstance(entries, list) or len(entries) != 68:
        raise data.DataValidationError("Historical graded manifest requires 68 entries")
    graded = []
    for entry, month in zip(entries, contract["months"], strict=True):
        if entry.get("month") != month:
            raise data.DataValidationError("Historical graded manifest is incomplete or unordered")
        item = _graded_entry_from_existing(entry, contract)
        _validate_historical_graded_entry(item, month, contract)
        graded.append(item)
    boundary_rows = audit_historical_boundaries(graded)
    total_expected = sum(row["expected_minutes"] for row in graded)
    total_missing = sum(row["missing_minutes"] for row in graded)
    eval_rows = [row for row in graded if row["role"] == "evaluate"]
    eval_expected = sum(row["expected_minutes"] for row in eval_rows)
    eval_missing = sum(row["missing_minutes"] for row in eval_rows)
    aggregate = classify_historical_aggregate(total_expected, total_missing, eval_expected, eval_missing)
    aggregate.update({
        "warmup_aggregate_expected_minutes": total_expected - eval_expected,
        "warmup_aggregate_missing_minutes": total_missing - eval_missing,
        "warmup_aggregate_missing_ratio": (total_missing - eval_missing) / (total_expected - eval_expected),
        "evaluate_months_over_3pct_count": sum(
            row["missing_minutes"] * 1000 > row["expected_minutes"] * 30 for row in eval_rows),
        "warmup_months_over_5pct_count": sum(
            row["missing_minutes"] * 100 > row["expected_minutes"] * 5
            for row in graded if row["role"] == "warmup"),
        "boundary_gap_minutes_total": sum(row["gap_minutes"] for row in boundary_rows),
    })
    if aggregate["evaluate_months_over_3pct_count"]:
        raise data.DataValidationError("At least one evaluation month exceeds 3% missing")
    if aggregate["warmup_months_over_5pct_count"]:
        raise data.DataValidationError("At least one warm-up month exceeds 5% missing")
    accounting = _validate_historical_manifest_ledger(ledger_summary, graded, contract)
    manifest = {
        "schema_version": 2, "gate": "gate1", "status": "passed",
        "evidence_level": "real_data_integrity_only", "gate2_authorised": False,
        "protocol_commit": protocol_commit, "months": copy.deepcopy(graded),
        "aggregates": aggregate, "cross_month_boundaries": boundary_rows,
        "total_rows": sum(row["actual_minutes"] for row in graded),
        "total_bytes_downloaded": accounting["cumulative_body_bytes"]["zip"],
        "checksum_bytes_downloaded": accounting["cumulative_body_bytes"]["checksum"],
        "zip_reserved_bytes": accounting["unresolved_reserved_bytes"]["zip"],
        "checksum_reserved_bytes": accounting["unresolved_reserved_bytes"]["checksum"],
        "transfer_accounting": accounting,
        "network_ledger": copy.deepcopy(ledger_summary),
    }
    manifest["overall_sha256"] = data.sha256(_historical_json_bytes(manifest))
    validate_historical_graded_manifest(manifest, contract)
    return manifest


def validate_historical_graded_manifest(manifest: dict, contract: dict) -> None:
    """Validate every row, soft warning, aggregate gate, and hash chain."""
    contract = _validate_historical_contract(contract)
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 2
            or manifest.get("gate") != "gate1" or manifest.get("status") != "passed"
            or manifest.get("evidence_level") != "real_data_integrity_only"
            or manifest.get("gate2_authorised") is not False):
        raise data.DataValidationError("Invalid graded historical manifest schema")
    if (not isinstance(manifest.get("protocol_commit"), str)
            or re.fullmatch(r"[0-9a-f]{40}", manifest["protocol_commit"]) is None):
        raise data.DataValidationError("Graded historical manifest requires full protocol SHA")
    entries = manifest.get("months")
    if not isinstance(entries, list) or len(entries) != 68:
        raise data.DataValidationError("Graded historical manifest requires all 68 months")
    counts = {"ms": 0, "us": 0}
    for entry, month in zip(entries, contract["months"], strict=True):
        _validate_historical_graded_entry(entry, month, contract)
        expected = int((research_policy.historical_month_bounds(month)[1]
                        - research_policy.historical_month_bounds(month)[0]) / pd.Timedelta(minutes=1))
        if (entry.get("expected_minutes") != expected
                or entry.get("actual_minutes") != entry["rows"]
                or entry.get("missing_minutes") != expected - entry["rows"]
                or type(entry.get("missing_ratio")) is not float
                or entry["missing_ratio"] != entry["missing_minutes"] / expected
                or type(entry.get("anomalous_close_time_rows")) is not int):
            raise data.DataValidationError("Graded manifest monthly counts/ratio do not reconcile")
        classification = classify_historical_integrity(
            month, expected, entry["rows"], entry["anomalous_close_time_rows"])
        for key in ("role", "role_in_protocol", "status_tier"):
            if entry.get(key) != classification[key]:
                raise data.DataValidationError("Graded manifest monthly classification mismatch")
        notes = entry.get("notes")
        if (not isinstance(notes, str) or not notes.strip()
                or ((entry["missing_minutes"] or entry["anomalous_close_time_rows"])
                    and notes.strip().lower() == "none")):
            raise data.DataValidationError("Graded manifest notes are missing or invalid")
        if not isinstance(entry.get("gap_ranges"), list):
            raise data.DataValidationError("Graded manifest gap ranges must be explicit")
        gap_minutes = 0
        previous_end = None
        for gap in entry["gap_ranges"]:
            first, last = pd.Timestamp(gap["start_utc"]), pd.Timestamp(gap["end_utc"])
            if (first.tzinfo is None or last.tzinfo is None or first < research_policy.historical_month_bounds(month)[0]
                    or last >= research_policy.historical_month_bounds(month)[1]
                    or last < first or (last - first) % pd.Timedelta(minutes=1)
                    or gap.get("minutes") != int((last - first) / pd.Timedelta(minutes=1)) + 1
                    or (previous_end is not None and first - previous_end <= pd.Timedelta(minutes=1))):
                raise data.DataValidationError("Invalid or silently combined historical gap interval")
            gap_minutes += gap["minutes"]
            previous_end = last
        if gap_minutes != entry["missing_minutes"]:
            raise data.DataValidationError("Historical gap ranges do not cover all missing minutes")
        if entry.get("timestamp_unit") not in counts:
            raise data.DataValidationError("Invalid graded timestamp unit")
        counts[entry["timestamp_unit"]] += 1
    if counts != {"ms": 60, "us": 8}:
        raise data.DataValidationError("Graded manifest requires 60 ms and 8 us months")
    boundaries = audit_historical_boundaries(entries)
    if manifest.get("cross_month_boundaries") != boundaries:
        raise data.DataValidationError("Graded manifest month boundary audit mismatch")
    aggregate = manifest.get("aggregates")
    if not isinstance(aggregate, dict):
        raise data.DataValidationError("Graded manifest aggregate audit is required")
    total_expected = sum(entry["expected_minutes"] for entry in entries)
    total_missing = sum(entry["missing_minutes"] for entry in entries)
    eval_entries = [entry for entry in entries if entry["role"] == "evaluate"]
    eval_expected = sum(entry["expected_minutes"] for entry in eval_entries)
    eval_missing = sum(entry["missing_minutes"] for entry in eval_entries)
    aggregate_check = classify_historical_aggregate(total_expected, total_missing, eval_expected, eval_missing)
    aggregate_check.update({
        "warmup_aggregate_expected_minutes": total_expected - eval_expected,
        "warmup_aggregate_missing_minutes": total_missing - eval_missing,
        "warmup_aggregate_missing_ratio": (total_missing - eval_missing) / (total_expected - eval_expected),
        "evaluate_months_over_3pct_count": sum(
            entry["missing_minutes"] * 1000 > entry["expected_minutes"] * 30 for entry in eval_entries),
        "warmup_months_over_5pct_count": sum(
            entry["missing_minutes"] * 100 > entry["expected_minutes"] * 5
            for entry in entries if entry["role"] == "warmup"),
        "boundary_gap_minutes_total": sum(row["gap_minutes"] for row in boundaries),
    })
    if aggregate != aggregate_check:
        raise data.DataValidationError("Graded historical aggregate fields do not reconcile")
    accounting = _validate_historical_manifest_ledger(manifest.get("network_ledger"), entries, contract)
    if not research_policy._same_typed_value(manifest.get("transfer_accounting"), accounting):
        raise data.DataValidationError("Graded manifest transfer accounting does not reconcile")
    expected_aggregate = {
        "total_rows": sum(entry["actual_minutes"] for entry in entries),
        "total_bytes_downloaded": accounting["cumulative_body_bytes"]["zip"],
        "checksum_bytes_downloaded": accounting["cumulative_body_bytes"]["checksum"],
        "zip_reserved_bytes": accounting["unresolved_reserved_bytes"]["zip"],
        "checksum_reserved_bytes": accounting["unresolved_reserved_bytes"]["checksum"],
    }
    if any(type(manifest.get(key)) is not int or manifest[key] != value
           for key, value in expected_aggregate.items()):
        raise data.DataValidationError("Graded manifest totals do not reconcile")
    unhashed = {key: value for key, value in manifest.items() if key != "overall_sha256"}
    if manifest.get("overall_sha256") != data.sha256(_historical_json_bytes(unhashed)):
        raise data.DataValidationError("Graded manifest overall SHA256 mismatch")


def verify_historical_graded_month(month: str, contract: dict, entry: dict, *,
                                   ledger: HistoricalNetworkLedger | None = None) -> dict:
    """Re-parse local raw files with the graded rules and match every digest."""
    research_policy.ensure_month_in_historical_allowlist(month)
    contract = _validate_historical_contract(contract)
    graded = _graded_entry_from_existing(entry, contract)
    paths = _historical_paths(month, contract)
    source = json.loads(_limited_read(paths["source"], 2 * 1024 * 1024))
    if not research_policy._same_typed_value(source, entry):
        raise data.DataValidationError("Historical graded sidecar differs from manifest entry")
    archive = _limited_read(paths["raw"], contract["per_zip_limit_bytes"])
    checksum = _limited_read(paths["checksum"], contract["per_checksum_limit_bytes"])
    if ledger is not None:
        raw_rows = len(_historical_extract_csv(archive, month, contract).splitlines())
        ledger.state["local_revalidation_rows_read"] = ledger.state.get("local_revalidation_rows_read", 0) + raw_rows
    frame, audit = inspect_historical_graded_archive(archive, checksum, month, contract, ledger=ledger)
    for key in ("zip_sha256", "checksum_text_sha256", "csv_sha256"):
        if audit[key] != entry[key]:
            raise data.DataValidationError(f"Historical graded {key} changed")
    for key in ("rows", "expected_minutes", "actual_minutes", "missing_minutes", "missing_ratio",
                "anomalous_close_time_rows", "gap_ranges", "timestamp_unit", "first_open_utc", "last_open_utc"):
        if audit[key] != graded[key]:
            raise data.DataValidationError(f"Historical graded audit {key} changed")
    if month == "2020-02":
        refetch_note = "refetch after prior cleanup; gap retained from archive"
        if refetch_note not in entry.get("notes", ""):
            raise data.DataValidationError("D-032 refetch note is missing from the February entry")
        audit["notes"].append(refetch_note)
    output = io.BytesIO()
    frame.to_parquet(output, index=False)
    parquet_path = paths["parquet"]
    parquet = _limited_read(parquet_path, contract["per_csv_limit_bytes"] * 2)
    if data.sha256(output.getvalue()) != entry["parquet_sha256"] or data.sha256(parquet) != entry["parquet_sha256"]:
        raise data.DataValidationError("Historical graded Parquet hash changed")
    pd.testing.assert_frame_equal(frame, pd.read_parquet(io.BytesIO(parquet)), check_exact=True)
    refreshed = dict(graded, reverified=True, audit=audit, gap_ranges=audit["gap_ranges"])
    for key in ("first_open_raw", "last_open_raw", "first_open_utc", "last_open_utc",
                "leading_missing_minutes", "trailing_missing_minutes", "csv_bytes"):
        refreshed[key] = audit[key]
    _validate_historical_graded_entry(refreshed, month, contract)
    return refreshed


def _validate_d032_prior_ledger(prior: dict, contract: dict) -> None:
    """Pin the original four transfers before using any D-032 carry-in."""
    validator = HistoricalNetworkLedger(contract)
    validator.state = prior
    validator._validate_state()
    if (prior.get("status") != "failed" or prior.get("failed_month") != "2020-02"
            or prior.get("http_gets_count") != 4 or prior.get("zip_body_bytes") != 4301660
            or prior.get("checksum_body_bytes") != 176 or prior.get("zip_reserved_bytes") != 0
            or prior.get("checksum_reserved_bytes") != 0 or prior.get("market_rows_read") != 85986):
        raise data.DataValidationError("Prior failed ledger does not match the D-032 checkpoint")
    for identifier, (month, kind, size, digest) in enumerate(_D032_PRIOR_TRANSFERS):
        expected = {"id": identifier, "month": month, "kind": kind, "month_attempt": 1,
                    "url": _historical_url(month, ".zip.CHECKSUM" if kind == "checksum" else ".zip"),
                    "status": "received", "http_status": 200, "body_bytes": size,
                    "content_length": size, "reserved_bytes": 0, "sha256": digest}
        if any(not research_policy._same_typed_value(prior["attempts"][identifier].get(k), v)
               for k, v in expected.items()):
            raise data.DataValidationError("D-032 original attempt differs from the pinned failure record")


def _verify_d032_continuation_prefix(contract: dict, continuation: dict, *,
                                     ledger: HistoricalNetworkLedger | None = None) -> list[dict]:
    """Revalidate the exact local Jan/Feb prefix before changing ledger state."""
    prior_relative = "results/phase3a-gate1/network-ledger.json"
    prior_path = _historical_contract_path(prior_relative, prior_relative)
    prior_bytes = _limited_read(prior_path, 16 * 1024 * 1024)
    prior_sha = data.sha256(prior_bytes)
    prior = json.loads(prior_bytes)
    _validate_d032_prior_ledger(prior, contract)
    if (prior_sha != continuation.get("prior_failed_ledger_sha256")
            or prior.get("status") != "failed" or prior.get("failed_month") != "2020-02"
            or prior.get("http_gets_count") != 4 or prior.get("holdout_requests") != 0
            or prior.get("holdout_access_count") != 0):
        raise data.DataValidationError("Original D-032 failed ledger changed or is incompatible")
    state_attempts = continuation.get("attempts", [])
    if (len(state_attempts) != 6
            or not research_policy._same_typed_value(state_attempts[:4], prior["attempts"])
            or sum(a.get("month") == "2020-01" for a in state_attempts) != 2
            or sum(a.get("month") == "2020-02" for a in state_attempts) != 4
            or any(a.get("month") not in {"2020-01", "2020-02"} for a in state_attempts)):
        raise DownloadBudgetError("D-032 continuation request history is not exactly Jan/Feb")
    event_relative = "results/phase3a-gate1/continuations/2020-02-refetch.json"
    event_path = _historical_contract_path(event_relative, event_relative)
    event = json.loads(_limited_read(event_path, 16 * 1024 * 1024))
    expected_success_sha = continuation.get("refetch_implementation_commit_sha")
    if (event.get("status") != "refetch_passed"
            or event.get("refetch_implementation_commit_sha") != expected_success_sha
            or event.get("http_gets_count") != 6
            or not research_policy._same_typed_value(event.get("attempts"), state_attempts)
            or tuple(item.get("month") for item in event.get("months", [])) != ("2020-01", "2020-02")):
        raise data.DataValidationError("D-032 refetch event ledger disagrees with continuation state")
    months = continuation["months"]
    jan_paths = _historical_paths("2020-01", contract)
    jan_source = json.loads(_limited_read(jan_paths["source"], 2 * 1024 * 1024))
    jan = verify_historical_graded_month("2020-01", contract, jan_source, ledger=ledger)
    feb = verify_historical_graded_month("2020-02", contract, months[1], ledger=ledger)
    if not research_policy._same_typed_value(event["months"][1], months[1]):
        raise data.DataValidationError("D-032 February event and continuation entries differ")
    stable_fields = ("zip_sha256", "checksum_text_sha256", "parquet_sha256", "csv_sha256",
                     "rows", "bytes_downloaded", "checksum_bytes_downloaded")
    for fresh, recorded in zip((jan, feb), months, strict=True):
        if fresh.get("month") != recorded.get("month") or any(
                fresh.get(key) != recorded.get(key) for key in stable_fields):
            raise data.DataValidationError("D-032 local prefix differs from continuation ledger")
    feb_expected = {
        key: value for key, value in _D032_FAILED_MONTH_EXPECTED.items()
        if key != "checksum_sha256"
    }
    feb_expected.update({
        "attempts": 1, "historical_attempts": 2, "http_gets": 2,
        "prior_failed_ledger_sha256": prior_sha,
    })
    if (any(feb.get(key) != value for key, value in feb_expected.items())
            or feb.get("checksum_text_sha256") != _D032_FAILED_MONTH_EXPECTED["checksum_sha256"]):
        raise data.DataValidationError("D-032 February refetch no longer matches its approved audit")
    if (continuation.get("continuation_mode") != "D-032-single-month-refetch"
            or continuation.get("resume_from_month") != "2020-03"
            or feb.get("history", [None, {}])[1].get("success_commit_sha") != expected_success_sha
            or continuation.get("zip_reserved_bytes") != 0
            or continuation.get("checksum_reserved_bytes") != 0):
        raise DownloadBudgetError("D-032 continuation checkpoint is not ready for March")
    return [jan, feb]


def refetch_failed_historical_month_2020_02(
    contract: dict,
    *,
    prior_ledger_path: str | Path = "results/phase3a-gate1/network-ledger.json",
    implementation_commit_sha: str | None = None,
    open_response=None,
) -> dict:
    """Perform the single D-032 checksum/ZIP refetch after strict local gates."""
    research_policy.ensure_month_in_historical_allowlist("2020-02")
    start, end = research_policy.historical_month_bounds("2020-02")
    research_policy.ensure_not_holdout(start, end)
    contract = _validate_historical_contract(contract)
    if (implementation_commit_sha is not None
            and re.fullmatch(r"[0-9a-f]{40}", implementation_commit_sha) is None):
        raise data.DataValidationError("D-032 implementation commit must be a full lowercase SHA")
    expected_prior_path = "results/phase3a-gate1/network-ledger.json"
    prior_path = _historical_contract_path(prior_ledger_path, expected_prior_path)
    event_relative = "results/phase3a-gate1/continuations/2020-02-refetch.json"
    aggregate_relative = "results/phase3a-gate1/network-ledger-continuation.json"
    event_path = _historical_contract_path(event_relative, event_relative)
    aggregate_path = _historical_contract_path(aggregate_relative, aggregate_relative)
    continuation_dir = _historical_contract_path("results/phase3a-gate1/continuations",
                                                  "results/phase3a-gate1/continuations")
    paths = _historical_paths("2020-02", contract)
    manifest_path = _historical_contract_path(
        "data/processed/binance/spot/BTCUSDT/1m/historical/historical-manifest.json",
        "data/processed/binance/spot/BTCUSDT/1m/historical/historical-manifest.json")
    expected = _D032_FAILED_MONTH_EXPECTED
    note = "refetch after prior cleanup; gap retained from archive"
    staged: list[Path] = []
    published: list[Path] = []
    lock = _HistoricalDownloadLock()
    with lock:
        prior_bytes = _limited_read(prior_path, 16 * 1024 * 1024)
        prior_sha = data.sha256(prior_bytes)
        prior = json.loads(prior_bytes)
        _validate_d032_prior_ledger(prior, contract)
        if (event_path.exists() or aggregate_path.exists() or continuation_dir.exists()
                and any(continuation_dir.iterdir())):
            raise DownloadBudgetError("A D-032 continuation already exists; do not retry/reset it")
        if manifest_path.exists():
            raise data.DataValidationError("A successful Gate 1 manifest already exists")
        if (prior.get("schema_version") != 1 or prior.get("phase") != "3A-gate1"
                or prior.get("status") != "failed" or prior.get("failed_month") != "2020-02"
                or prior.get("http_gets_count") != 4
                or prior.get("zip_body_bytes") != 4301660 or prior.get("zip_reserved_bytes") != 0
                or prior.get("checksum_body_bytes") != 176 or prior.get("checksum_reserved_bytes") != 0
                or prior.get("holdout_requests") != 0 or prior.get("holdout_access_count") != 0):
            raise data.DataValidationError("Prior failed ledger does not match the D-032 checkpoint")
        prior_attempts = prior.get("attempts")
        feb_checksum_attempts = [a for a in prior_attempts if a.get("month") == "2020-02" and a.get("kind") == "checksum"]
        feb_zip_attempts = [a for a in prior_attempts if a.get("month") == "2020-02" and a.get("kind") == "zip"]
        if (len(feb_checksum_attempts) != 1 or len(feb_zip_attempts) != 1
                or feb_checksum_attempts[0].get("sha256") != expected["checksum_sha256"]
                or feb_zip_attempts[0].get("sha256") != expected["zip_sha256"]
                or feb_checksum_attempts[0].get("body_bytes") != 88
                or feb_zip_attempts[0].get("body_bytes") != 2077466):
            raise data.DataValidationError("Prior failed February transfer evidence differs from D-032")
        prior_failure = prior.get("failure", {})
        diagnostic = prior_failure.get("diagnostics", {})
        if (prior_failure.get("error_type") != "DataValidationError"
                or diagnostic.get("raw_rows_read") != expected["actual_minutes"]
                or diagnostic.get("zip_sha256") != expected["zip_sha256"]
                or diagnostic.get("checksum_text_sha256") != expected["checksum_sha256"]):
            raise data.DataValidationError("Prior February failure diagnostics differ from D-032")
        prior_months = prior.get("months", [])
        if len(prior_months) != 1 or prior_months[0].get("month") != "2020-01":
            raise data.DataValidationError("D-032 requires exactly the verified January prefix")
        jan_entry = prior_months[0]
        jan_paths = _historical_paths("2020-01", contract)
        if any(not jan_paths[key].is_file() for key in ("raw", "checksum", "source", "parquet")):
            raise data.DataValidationError("Verified January prefix files are missing")
        jan_source = json.loads(_limited_read(jan_paths["source"], 1024 * 1024))
        jan_archive = _limited_read(jan_paths["raw"], contract["per_zip_limit_bytes"])
        jan_checksum = _limited_read(jan_paths["checksum"], contract["per_checksum_limit_bytes"])
        if (data.sha256(jan_archive) != jan_entry.get("zip_sha256")
                or data.sha256(jan_checksum) != jan_entry.get("checksum_text_sha256")
                or jan_source.get("zip_sha256") != jan_entry.get("zip_sha256")
                or jan_source.get("checksum_text_sha256") != jan_entry.get("checksum_text_sha256")
                or jan_source.get("parquet_sha256") != jan_entry.get("parquet_sha256")
                or jan_source.get("url") != _historical_url("2020-01")
                or jan_source.get("checksum_url") != _historical_url("2020-01", ".zip.CHECKSUM")):
            raise data.DataValidationError("January source sidecar/hash prefix failed D-032 revalidation")
        data.verify_checksum(jan_archive, jan_checksum.decode("ascii"), "BTCUSDT-1m-2020-01.zip")
        jan_csv = _historical_extract_csv(jan_archive, "2020-01", contract)
        jan_frame, jan_strict_audit = parse_historical_monthly_klines(jan_csv, "2020-01")
        jan_graded_frame, jan_graded_audit = inspect_historical_graded_archive(
            jan_archive, jan_checksum, "2020-01", contract)
        pd.testing.assert_frame_equal(jan_frame, jan_graded_frame, check_exact=True)
        if (len(jan_frame) != 44640 or jan_strict_audit["missing_minutes"] != 0
                or jan_graded_audit["missing_minutes"] != 0 or len(jan_graded_frame) != 44640
                or jan_graded_audit["anomalous_close_time_rows"] != 0):
            raise data.DataValidationError("January strict and graded checks did not pass")
        jan_parquet = _limited_read(jan_paths["parquet"], contract["per_csv_limit_bytes"] * 2)
        if data.sha256(jan_parquet) != jan_entry.get("parquet_sha256"):
            raise data.DataValidationError("January Parquet SHA differs from prior ledger")
        pd.testing.assert_frame_equal(jan_frame, pd.read_parquet(io.BytesIO(jan_parquet)), check_exact=True)
        jan_entry = dict(jan_entry)
        jan_entry.update({
            "csv_bytes": jan_graded_audit["csv_bytes"],
                "checksum_bytes": jan_graded_audit["checksum_bytes"],
            "expected_minutes": jan_graded_audit["expected_minutes"],
            "actual_minutes": jan_graded_audit["actual_minutes"],
            "missing_minutes": jan_graded_audit["missing_minutes"],
            "missing_ratio": jan_graded_audit["missing_ratio"],
            "anomalous_close_time_rows": jan_graded_audit["anomalous_close_time_rows"],
            "role": jan_graded_audit["role"],
            "role_in_protocol": jan_graded_audit["role_in_protocol"],
            "status_tier": jan_graded_audit["status_tier"],
            "gap_ranges": jan_graded_audit["gap_ranges"], "notes": "none",
            "first_open_raw": jan_graded_audit["first_open_raw"],
            "last_open_raw": jan_graded_audit["last_open_raw"],
            "first_open_utc": jan_graded_audit["first_open_utc"],
            "last_open_utc": jan_graded_audit["last_open_utc"],
            "leading_missing_minutes": jan_graded_audit["leading_missing_minutes"],
            "trailing_missing_minutes": jan_graded_audit["trailing_missing_minutes"],
            "audit": jan_graded_audit,
        })
        _validate_historical_graded_entry(jan_entry, "2020-01", contract)
        for key in ("raw", "checksum", "source", "parquet"):
            if paths[key].exists():
                raise data.DataValidationError("February output exists; refetch overwrite is forbidden")
        if (type(prior.get("http_gets_count")) is not int or prior["http_gets_count"] + 2 > 408
                or prior["zip_body_bytes"] >= contract["zip_budget_bytes"]
                or prior["checksum_body_bytes"] >= contract["checksum_budget_bytes"]):
            raise DownloadBudgetError("D-032 refetch would exceed an existing Gate 1 budget")
        _historical_disk_gate(contract)
        if continuation_dir.exists() and not continuation_dir.is_dir():
            raise data.DataValidationError("D-032 continuation path is not a directory")
        continuation_dir.mkdir(parents=True, exist_ok=True)
        state = json.loads(json.dumps(prior))
        state["status"] = "in_progress"
        state.pop("failure", None)
        state.pop("failed_month", None)
        state["prior_failed_month_history"] = {
            "month": "2020-02", "status": "failed", "failed_at_utc": prior_failure.get("failed_at_utc"),
            "error_type": prior_failure.get("error_type"), "error": prior_failure.get("error"),
            "diagnostics": diagnostic,
        }
        state["prior_failed_ledger_sha256"] = prior_sha
        state["continuation_mode"] = "D-032-single-month-refetch"
        state["refetch_implementation_commit_sha"] = implementation_commit_sha
        state["implementation_commit"] = implementation_commit_sha
        state["final_commit_sha"] = implementation_commit_sha
        state["continuation_started_at_utc"] = _now()
        state["local_revalidation_rows_read"] = 2 * len(jan_frame)
        state["market_rows_read"] += state["local_revalidation_rows_read"]
        event_ledger = HistoricalNetworkLedger(contract, ledger_path=event_relative)
        event_ledger.state = state
        event_ledger._active = True
        event_ledger.save()
        started = _now()
        raw_part = _historical_temp_path(paths["raw"], "d032-refetch")
        staged.append(raw_part)
        try:
            checksum, checksum_meta = _historical_fetch_once(
                _historical_url("2020-02", ".zip.CHECKSUM"), "checksum", "2020-02",
                contract, event_ledger, 2, open_response=open_response)
            if data.sha256(checksum) != expected["checksum_sha256"]:
                raise data.DataValidationError("D-032 refetched CHECKSUM SHA differs from original failed ledger")
            if _validate_historical_checksum(checksum, "2020-02") != expected["zip_sha256"]:
                raise data.DataValidationError("D-032 CHECKSUM does not declare the original ZIP SHA")
            _, zip_meta = _historical_fetch_once(
                _historical_url("2020-02"), "zip", "2020-02", contract, event_ledger, 2,
                destination=raw_part, open_response=open_response)
            archive = _limited_read(raw_part, contract["per_zip_limit_bytes"])
            if data.sha256(archive) != expected["zip_sha256"]:
                raise data.DataValidationError("D-032 refetched ZIP SHA differs from original failed ledger")
            frame, audit = inspect_historical_graded_archive(archive, checksum, "2020-02", contract,
                                                              ledger=event_ledger)
            matches = {
                "expected_minutes": audit["expected_minutes"], "actual_minutes": audit["actual_minutes"],
                "missing_minutes": audit["missing_minutes"],
                "anomalous_close_time_rows": audit["anomalous_close_time_rows"],
                "timestamp_unit": audit["timestamp_unit"], "first_open_raw": audit["first_open_raw"],
                "last_open_raw": audit["last_open_raw"], "first_open_utc": audit["first_open_utc"],
                "last_open_utc": audit["last_open_utc"], "zip_sha256": audit["zip_sha256"],
                "checksum_text_sha256": audit["checksum_text_sha256"],
            }
            if any(matches[key] != expected[source_key] for key, source_key in (
                    ("expected_minutes", "expected_minutes"), ("actual_minutes", "actual_minutes"),
                    ("missing_minutes", "missing_minutes"),
                    ("anomalous_close_time_rows", "anomalous_close_time_rows"),
                    ("timestamp_unit", "timestamp_unit"), ("first_open_raw", "first_open_raw"),
                    ("last_open_raw", "last_open_raw"), ("first_open_utc", "first_open_utc"),
                    ("last_open_utc", "last_open_utc"), ("zip_sha256", "zip_sha256"),
                    ("checksum_text_sha256", "checksum_sha256"))):
                raise data.DataValidationError("D-032 refetched graded archive does not match the original audit")
            audit["notes"].append(note)
            output = io.BytesIO()
            frame.to_parquet(output, index=False)
            parquet = output.getvalue()
            pd.testing.assert_frame_equal(frame, pd.read_parquet(io.BytesIO(parquet)), check_exact=True)
            attempts = [a for a in event_ledger.state["attempts"] if a["month"] == "2020-02"]
            if len(attempts) != 4 or event_ledger.state["http_gets_count"] != 6:
                raise DownloadBudgetError("D-032 continuation must contain exactly two additional GETs")
            entry = {
                "month": "2020-02", "status": "passed", "url": _historical_url("2020-02"),
                "checksum_url": _historical_url("2020-02", ".zip.CHECKSUM"),
                "started_at_utc": started, "fetched_at_utc": zip_meta["finished_at_utc"],
                "finished_at_utc": _now(), "http_status": 200, "attempts": 1,
                "historical_attempts": 2, "http_gets": 2,
                "bytes_downloaded": len(archive), "checksum_bytes_downloaded": len(checksum),
                "zip_reserved_bytes": sum(a["reserved_bytes"] for a in attempts if a["kind"] == "zip"),
                "checksum_reserved_bytes": sum(a["reserved_bytes"] for a in attempts if a["kind"] == "checksum"),
                "zip_bytes": len(archive), "checksum_bytes": len(checksum), "parquet_bytes": len(parquet),
                "zip_sha256": audit["zip_sha256"], "checksum_text_sha256": audit["checksum_text_sha256"],
                "sha256_official_checksum_file": audit["checksum_text_sha256"],
                "sha256_zip_computed": audit["zip_sha256"], "parquet_sha256": data.sha256(parquet),
                "csv_sha256": audit["csv_sha256"], "csv_bytes": audit["csv_bytes"], "rows": len(frame),
                "expected_minutes": audit["expected_minutes"], "actual_minutes": audit["actual_minutes"],
                "missing_minutes": audit["missing_minutes"], "missing_ratio": audit["missing_ratio"],
                "anomalous_close_time_rows": audit["anomalous_close_time_rows"],
                "timestamp_unit": audit["timestamp_unit"], "expected_unit": "ms", "detected_unit": "ms",
                "role": audit["role"], "role_in_protocol": audit["role_in_protocol"],
                "status_tier": audit["status_tier"],
                "first_open_raw": audit["first_open_raw"], "first_open_utc": audit["first_open_utc"],
                "last_open_raw": audit["last_open_raw"],
                "last_open_utc": audit["last_open_utc"], "gap_ranges": audit["gap_ranges"],
                "leading_missing_minutes": audit["leading_missing_minutes"],
                "trailing_missing_minutes": audit["trailing_missing_minutes"],
                "notes": _historical_notes_text(audit["notes"]),
                "prior_failed_ledger_sha256": prior_sha,
            "history": [
                {"event": "failed_download_cleanup", "commit_sha": "1d6e1f2"},
                {"event": "D-032_refetch_success", "event_ledger_path": event_relative,
                 "success_commit_sha": implementation_commit_sha},
            ],
                "audit": audit,
            }
            _validate_historical_graded_entry(entry, "2020-02", contract)
            if pd.Timestamp(entry["first_open_utc"]) - pd.Timestamp(jan_entry["last_open_utc"]) != pd.Timedelta(minutes=1):
                raise data.DataValidationError("D-032 January/February boundary differs from prior audit")
            checksum_part = _stage_historical_bytes(paths["checksum"], checksum, "d032-refetch")
            staged.append(checksum_part)
            parquet_part = _stage_historical_bytes(paths["parquet"], parquet, "d032-refetch")
            staged.append(parquet_part)
            source_part = _stage_historical_bytes(paths["source"], _historical_json_bytes(entry), "d032-refetch")
            staged.append(source_part)
            _historical_disk_gate(contract)
            for temporary, key in ((raw_part, "raw"), (checksum_part, "checksum"),
                                   (parquet_part, "parquet"), (source_part, "source")):
                _publish_historical_part(temporary, paths[key])
                published.append(paths[key])
            event_ledger.state["months"] = [jan_entry, entry]
            event_ledger.state.update(status="refetch_passed", refetch_completed_at_utc=_now(),
                                      refetch_month="2020-02", refetch_attempts=1)
            event_ledger.save()
            continuation = json.loads(json.dumps(event_ledger.state))
            continuation.update(status="ready_to_resume", continuation_ledger_path=aggregate_relative,
                                resume_from_month="2020-03")
            aggregate_temp = _stage_historical_bytes(aggregate_path, _historical_json_bytes(continuation),
                                                       "d032-continuation")
            staged.append(aggregate_temp)
            _publish_historical_part(aggregate_temp, aggregate_path)
            published.append(aggregate_path)
            if data.sha256(_limited_read(prior_path, 16 * 1024 * 1024)) != prior_sha:
                raise data.DataValidationError("Original failed ledger changed during D-032 continuation")
            return entry
        except BaseException as exc:
            for path in reversed(published):
                _fixed_path(path).unlink(missing_ok=True)
            for path in staged:
                _fixed_path(path).unlink(missing_ok=True)
            event_ledger.state["months"] = [jan_entry]
            event_ledger.state.update(status="refetch_failed", failed_month="2020-02",
                                      refetch_failed_at_utc=_now(),
                                      refetch_failure={"type": type(exc).__name__, "message": str(exc),
                                                       "diagnostics": getattr(exc, "gate1_audit",
                                                                              event_ledger.state.get("last_month_diagnostics"))})
            event_ledger.save()
            if data.sha256(_limited_read(prior_path, 16 * 1024 * 1024)) != prior_sha:
                raise data.DataValidationError("Original failed ledger changed during D-032 failure handling") from exc
            raise
        finally:
            event_ledger._active = False
            for path in staged:
                _fixed_path(path).unlink(missing_ok=True)


_PERMISSION_BACKOFF_SECONDS = (0.1, 0.2, 0.5, 1.0, 2.0, 3.0, 3.0, 3.0, 3.0, 3.0)
_PERMISSION_MAX_ATTEMPTS = 1 + len(_PERMISSION_BACKOFF_SECONDS)
_LEDGER_PERSISTENCE_DIAGNOSTICS = {
    "persistence_retries_count_total": 0,
    "persistence_wait_seconds_total": 0.0,
    "raw_permission_error_count": 0,
    "permission_error_hard_fail_count": 0,
}
_LEDGER_PERSISTENCE_EVENTS: list[dict] = []


class LedgerPersistenceError(IOError):
    """A Gate 1 ledger could not be atomically persisted after retries."""

    def __init__(self, message: str, diagnostics: dict):
        super().__init__(message)
        self.diagnostics = dict(diagnostics)


def get_ledger_persistence_diagnostics() -> dict:
    """Return cumulative ledger replace retry diagnostics for the current process."""
    return dict(_LEDGER_PERSISTENCE_DIAGNOSTICS)


def get_ledger_persistence_events() -> list[dict]:
    """Return an independent per-write audit snapshot for the current process."""
    return [dict(event) for event in _LEDGER_PERSISTENCE_EVENTS]


def _atomic_json_with_retry(
    target_path: Path,
    payload_bytes: bytes,
    *,
    max_attempts: int = _PERMISSION_MAX_ATTEMPTS,
    backoff: tuple[float, ...] = _PERMISSION_BACKOFF_SECONDS,
) -> dict:
    """Atomically persist ledger bytes, retrying only Windows PermissionError.

    The old ``_atomic_json`` path remains unchanged. This narrow helper is used
    only by the Gate 1 ledger and never falls back to a non-atomic replacement.
    """
    target_path = _fixed_path(target_path)
    if not isinstance(payload_bytes, bytes):
        raise TypeError("payload_bytes must be bytes")
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")
    if len(backoff) < max_attempts - 1 or any(delay < 0 for delay in backoff):
        raise ValueError("backoff must contain one nonnegative delay per retry")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _fixed_path(target_path.parent / f".{target_path.name}.{uuid4().hex}.partial")
    permission_retries = 0
    waited_seconds = 0.0
    attempts = 0
    committed = False
    try:
        with temporary.open("xb") as output:
            output.write(payload_bytes)
            output.flush()
            os.fsync(output.fileno())
        for attempt in range(max_attempts):
            attempts = attempt + 1
            try:
                temporary.replace(target_path)
                committed = True
                diagnostics = {
                    "attempts": attempt + 1,
                    "waited_seconds": waited_seconds,
                    "permission_error_retries": permission_retries,
                }
                return diagnostics
            except PermissionError as exc:
                permission_retries += 1
                _LEDGER_PERSISTENCE_DIAGNOSTICS["persistence_retries_count_total"] += 1
                _LEDGER_PERSISTENCE_DIAGNOSTICS["raw_permission_error_count"] += 1
                if attempt + 1 >= max_attempts:
                    _LEDGER_PERSISTENCE_DIAGNOSTICS["permission_error_hard_fail_count"] += 1
                    raise LedgerPersistenceError(
                        f"atomic ledger replace exhausted after {max_attempts} PermissionError retries "
                        f"on {target_path}",
                        {"attempts": attempts, "waited_seconds": waited_seconds,
                         "permission_error_retries": permission_retries},
                    ) from exc
                delay = float(backoff[attempt])
                time.sleep(delay)
                waited_seconds += delay
                _LEDGER_PERSISTENCE_DIAGNOSTICS["persistence_wait_seconds_total"] += delay
            except OSError:
                # Do not swallow or retry non-PermissionError failures.
                raise
    finally:
        _LEDGER_PERSISTENCE_EVENTS.append({
            "target_path": target_path.relative_to(data.REPOSITORY_ROOT).as_posix(),
            "attempts": attempts, "waited_seconds": waited_seconds,
            "permission_error_retries": permission_retries, "committed": committed,
        })
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            # The target remains the last committed version; an orphan partial
            # is safer than masking the original persistence exception.
            pass
