"""Download and validate one official Binance Spot BTCUSDT one-minute day.

No trading API, credentials, or automatic bulk download is used here.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import urllib.request
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BASE_URL = "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m"
COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_asset_volume", "number_of_trades", "taker_buy_base_asset_volume",
    "taker_buy_quote_asset_volume", "ignore",
]
PRICE_COLUMNS = ["open", "high", "low", "close"]
VOLUME_COLUMNS = [
    "volume", "quote_asset_volume", "taker_buy_base_asset_volume",
    "taker_buy_quote_asset_volume",
]


class DataValidationError(ValueError):
    """The source file fails the explicit daily kline data contract."""


def repository_path(path: str | Path) -> Path:
    """Resolve a path and reject writes or reads outside this repository."""
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = REPOSITORY_ROOT / candidate
    candidate = candidate.resolve()
    if not candidate.is_relative_to(REPOSITORY_ROOT):
        raise ValueError(f"Path must remain inside {REPOSITORY_ROOT}")
    return candidate


def archive_name(day: date) -> str:
    return f"BTCUSDT-1m-{day.isoformat()}.zip"


def ensure_daily_allowed(day: date) -> None:
    """Guard a requested UTC day before any file or network access."""
    from quantforge.research_policy import ResearchPolicyError, ensure_not_holdout
    if not isinstance(day, date) or isinstance(day, datetime):
        raise DataValidationError("day must be a calendar date")
    start = pd.Timestamp(day, tz="UTC")
    try:
        ensure_not_holdout(start, start + pd.Timedelta(days=1))
    except ResearchPolicyError as exc:
        raise DataValidationError(str(exc)) from exc


def ensure_daily_path_allowed(path: str | Path) -> None:
    """Check any daily date encoded in a lexical path before opening it."""
    for match in re.finditer(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)", str(path)):
        try:
            day = date.fromisoformat(match.group(1))
        except ValueError as exc:
            raise DataValidationError("Invalid daily date in source path") from exc
        ensure_daily_allowed(day)


def ensure_daily_range_allowed(open_times: object) -> None:
    """Guard the parsed range as a second check against misleading names."""
    from quantforge.research_policy import ResearchPolicyError, ensure_not_holdout
    index = pd.DatetimeIndex(open_times)
    if index.empty or index.hasnans or index.tz is None:
        raise DataValidationError("Daily open times must be nonempty aware UTC timestamps")
    try:
        ensure_not_holdout(index.min(), index.max() + pd.Timedelta(minutes=1))
    except ResearchPolicyError as exc:
        raise DataValidationError(str(exc)) from exc


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_checksum(archive: bytes, checksum: str, filename: str) -> str:
    """Verify the exact filename and SHA256 in Binance's CHECKSUM file."""
    lines = checksum.strip().splitlines()
    if len(lines) != 1:
        raise DataValidationError("Expected exactly one official checksum entry")
    match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?([^\s]+)", lines[0])
    if match is None or match.group(2) != filename:
        raise DataValidationError("Malformed checksum or unexpected archive filename")
    actual = sha256(archive)
    if actual != match.group(1).lower():
        raise DataValidationError("Archive SHA256 does not match official CHECKSUM")
    return actual


def extract_csv(archive: bytes, filename: str) -> bytes:
    """Read only the single expected member in memory; never extract paths."""
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            members = zipped.infolist()
            expected = filename.removesuffix(".zip") + ".csv"
            if len(members) != 1 or members[0].filename != expected:
                raise DataValidationError("ZIP must contain exactly the expected CSV member")
            if members[0].file_size > 8 * 1024 * 1024:
                raise DataValidationError("Uncompressed one-day sample exceeds 8 MiB limit")
            return zipped.read(members[0])
    except zipfile.BadZipFile as exc:
        raise DataValidationError("Invalid ZIP archive or CRC") from exc


def _integer_column(values: pd.Series, name: str) -> pd.Series:
    if not values.str.fullmatch(r"[0-9]+").all():
        raise DataValidationError(f"{name} must contain nonnegative integer strings")
    try:
        return values.astype("int64")
    except (ValueError, OverflowError) as exc:
        raise DataValidationError(f"{name} exceeds int64 range") from exc


def parse_klines(csv_bytes: bytes, day: date) -> tuple[pd.DataFrame, dict]:
    """Validate raw timestamps and OHLCV, returning UTC-aware rows and a report.

    Timestamp units are detected from every open/close value and checked against
    the official 2025 Spot format transition. A day is the half-open UTC interval
    [00:00:00, next 00:00:00), with 1,440 distinct one-minute opens.
    """
    ensure_daily_allowed(day)
    try:
        frame = pd.read_csv(io.BytesIO(csv_bytes), header=None, dtype=str,
                            keep_default_na=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise DataValidationError("Unreadable or empty CSV") from exc
    if len(frame.columns) != len(COLUMNS):
        raise DataValidationError(f"Expected 12 CSV columns, got {len(frame.columns)}")
    frame.columns = COLUMNS
    if frame.empty:
        raise DataValidationError("Empty CSV")
    for name in ("open_time", "close_time", "number_of_trades"):
        frame[name] = _integer_column(frame[name], name)
    timestamps = pd.concat([frame.open_time, frame.close_time], ignore_index=True)
    if timestamps.between(10**15, 10**17 - 1).all():
        unit = "us"
        ticks_per_minute = 60_000_000
    elif timestamps.between(10**12, 10**14 - 1).all():
        unit = "ms"
        ticks_per_minute = 60_000
    else:
        raise DataValidationError("Mixed or unsupported timestamp units")
    expected_unit = "us" if day >= date(2025, 1, 1) else "ms"
    if unit != expected_unit:
        raise DataValidationError(f"Expected {expected_unit} Spot timestamps for {day}, got {unit}")
    numeric_columns = PRICE_COLUMNS + VOLUME_COLUMNS + ["ignore"]
    try:
        for name in numeric_columns:
            frame[name] = pd.to_numeric(frame[name], errors="raise").astype("float64")
    except (TypeError, ValueError) as exc:
        raise DataValidationError("Non-numeric price, volume or ignore field") from exc
    numeric = frame[numeric_columns]
    nonfinite = int((numeric.isna() | numeric.isin([float("inf"), float("-inf")])).any(axis=1).sum())
    close_duration_errors = int(((frame.close_time - frame.open_time) != ticks_per_minute - 1).sum())
    frame["open_time_raw"] = frame.open_time
    frame["close_time_raw"] = frame.close_time
    try:
        frame["open_time"] = pd.to_datetime(frame.open_time, unit=unit, utc=True)
        frame["close_time"] = pd.to_datetime(frame.close_time, unit=unit, utc=True)
    except (ValueError, OverflowError) as exc:
        raise DataValidationError("Timestamp conversion failed") from exc
    ensure_daily_range_allowed(frame.open_time)
    expected = pd.date_range(pd.Timestamp(day, tz="UTC"), periods=1440, freq="min")
    actual = pd.DatetimeIndex(frame.open_time)
    missing = expected.difference(actual)
    unexpected = actual.difference(expected)
    invalid_ohlc = (
        (frame[PRICE_COLUMNS] <= 0).any(axis=1)
        | (frame.low > frame.high)
        | (frame.open < frame.low) | (frame.open > frame.high)
        | (frame.close < frame.low) | (frame.close > frame.high)
    )
    invalid_volume = (
        (frame[VOLUME_COLUMNS] < 0).any(axis=1)
        | (frame.taker_buy_base_asset_volume > frame.volume + 1e-8)
        | (frame.taker_buy_quote_asset_volume > frame.quote_asset_volume + 1e-8)
    )
    report = {
        "rows": len(frame), "expected_rows": 1440, "timestamp_unit_raw": unit,
        "timezone": "UTC", "first_open_utc": frame.open_time.iloc[0].isoformat(),
        "last_open_utc": frame.open_time.iloc[-1].isoformat(),
        "last_close_utc": frame.close_time.iloc[-1].isoformat(),
        "missing_minutes": len(missing), "duplicate_open_times": int(actual.duplicated().sum()),
        "unexpected_open_times": len(unexpected), "sorted_ascending": actual.is_monotonic_increasing,
        "invalid_ohlc_rows": int(invalid_ohlc.sum()), "invalid_volume_rows": int(invalid_volume.sum()),
        "nonfinite_numeric_rows": nonfinite, "invalid_close_duration_rows": close_duration_errors,
    }
    failed = (
        len(frame) != 1440 or len(missing) or len(unexpected) or actual.has_duplicates
        or not actual.is_monotonic_increasing or invalid_ohlc.any() or invalid_volume.any()
        or nonfinite or close_duration_errors
    )
    if failed:
        raise DataValidationError("Daily kline validation failed: " + json.dumps(report))
    return frame, report


def fetch(url: str, max_bytes: int) -> tuple[bytes, dict]:
    request = urllib.request.Request(url, headers={"User-Agent": "QuantForge/0.1 research"})
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise DataValidationError(f"Response exceeds {max_bytes} byte limit")
        return body, {
            "url": url, "resolved_url": response.url, "http_status": response.status,
            "last_modified": response.headers.get("Last-Modified"), "bytes": len(body),
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        }


def download_day(day: date) -> dict:
    """Download or verify a cached day, validate it, and write Parquet/provenance."""
    ensure_daily_allowed(day)
    name = archive_name(day)
    url = f"{BASE_URL}/{name}"
    raw_dir = repository_path("data/raw/binance/spot/BTCUSDT/1m")
    processed_dir = repository_path("data/processed/binance/spot/BTCUSDT/1m")
    report_dir = repository_path("results/data-validation")
    for directory in (raw_dir, processed_dir, report_dir):
        directory.mkdir(parents=True, exist_ok=True)
    # Resolve each final target as well, to reject symlinks outside the repository.
    raw_path = repository_path(raw_dir / name)
    checksum_path = repository_path(raw_dir / (name + ".CHECKSUM"))
    source_path = repository_path(raw_dir / (name + ".source.json"))
    checksum_bytes, checksum_source = fetch(url + ".CHECKSUM", 4096)
    if raw_path.exists():
        with raw_path.open("rb") as cached:
            archive = cached.read(8 * 1024 * 1024 + 1)
        if len(archive) > 8 * 1024 * 1024:
            raise DataValidationError("Cached one-day archive exceeds 8 MiB limit")
        archive_source = json.loads(source_path.read_text(encoding="utf-8")) if source_path.exists() else {
            "url": url, "bytes": len(archive), "provenance_note": "Original HTTP metadata unavailable",
        }
        if not isinstance(archive_source, dict) or archive_source.get("url") != url or archive_source.get("bytes") != len(archive):
            raise DataValidationError("Cached source metadata does not match archive URL or size")
        if "sha256" in archive_source and archive_source["sha256"] != sha256(archive):
            raise DataValidationError("Cached source metadata SHA256 does not match archive")
        archive_source["cache_reused"] = True
    else:
        archive, archive_source = fetch(url, 8 * 1024 * 1024)
        archive_source["cache_reused"] = False
    digest = verify_checksum(archive, checksum_bytes.decode("ascii"), name)
    archive_source["sha256"] = digest
    csv_bytes = extract_csv(archive, name)
    frame, validation = parse_klines(csv_bytes, day)
    raw_path.write_bytes(archive)
    checksum_path.write_bytes(checksum_bytes)
    source_path.write_text(json.dumps(archive_source, indent=2) + "\n", encoding="utf-8")
    parquet_path = repository_path(processed_dir / name.replace(".zip", ".parquet"))
    frame.to_parquet(parquet_path, engine="pyarrow", index=False, compression="zstd")
    roundtrip = pd.read_parquet(parquet_path, engine="pyarrow")
    pd.testing.assert_frame_equal(frame, roundtrip, check_exact=True)
    manifest = {
        "schema_version": 1, "status": "passed", "market": "spot", "symbol": "BTCUSDT",
        "interval": "1m", "date_utc": day.isoformat(),
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "retrieved_at_utc": archive_source.get("retrieved_at_utc"),
        "source_documentation": "https://github.com/binance/binance-public-data#spot",
        "archive": archive_source, "official_checksum": checksum_source,
        "archive_sha256": digest, "csv_sha256": sha256(csv_bytes),
        "parquet_sha256": sha256(parquet_path.read_bytes()),
        "raw_path": raw_path.relative_to(REPOSITORY_ROOT).as_posix(),
        "parquet_path": parquet_path.relative_to(REPOSITORY_ROOT).as_posix(),
        "csv_columns": COLUMNS, "parquet_dtypes": {key: str(value) for key, value in frame.dtypes.items()},
        "validation": validation, "parquet_roundtrip_equal": True,
        "limitation": "One day validates ingestion only; no alpha, costs, execution or LOB claims.",
    }
    report_path = repository_path(report_dir / f"BTCUSDT-1m-{day.isoformat()}.json")
    report_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest
