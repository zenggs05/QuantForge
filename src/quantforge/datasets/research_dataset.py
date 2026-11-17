"""Provenance-first loading of the frozen ten-month development dataset."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import time

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from quantforge import data
from quantforge.research_policy import (
    ALLOWED_MONTHS, DEFAULT_DEVELOPMENT_MANIFEST, DEVELOPMENT_END, DEVELOPMENT_START,
    EXPECTED_ROWS, MONTHLY_URL, fixed_project_path, load_period_policy, month_bounds, month_paths,
)

REQUIRED_MANIFEST_KEYS = {
    "schema_version", "status", "market", "symbol", "interval", "month", "raw_path", "checksum_path",
    "parquet_path", "archive_sha256", "csv_sha256", "parquet_sha256", "source",
}
PARQUET_COLUMNS = data.COLUMNS + ["open_time_raw", "close_time_raw"]


def _read_json(path: Path) -> tuple[dict, str]:
    if path.stat().st_size > 1024 * 1024:
        raise data.DataValidationError("Research provenance manifest exceeds 1 MiB limit")
    payload = path.read_bytes()
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise data.DataValidationError("Research provenance manifest must be a JSON object")
    return value, data.sha256(payload)


def _sha256_file(path: Path, max_bytes: int) -> str:
    """Hash bounded chunks without a second large byte allocation."""
    digest, consumed = hashlib.sha256(), 0
    with path.open("rb") as source:
        while chunk := source.read(min(1024 * 1024, max_bytes - consumed + 1)):
            consumed += len(chunk)
            if consumed > max_bytes:
                raise data.DataValidationError(f"{path.name} exceeds its research read limit")
            digest.update(chunk)
    return digest.hexdigest()


def _require_hash(value: object, name: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise data.DataValidationError(f"{name} must be a lowercase SHA256 digest")


def _validate_month_manifest(month: str, manifest: dict) -> dict[str, Path]:
    """Validate ALL references before any market-data path can be opened."""
    paths = month_paths(month)
    if not REQUIRED_MANIFEST_KEYS.issubset(manifest):
        raise data.DataValidationError(f"Monthly manifest {month} is missing required provenance fields")
    for name, expected in (("schema_version", 1), ("status", "passed"), ("month", month),
                           ("market", "spot"), ("symbol", "BTCUSDT"), ("interval", "1m")):
        if type(manifest.get(name)) is not type(expected) or manifest.get(name) != expected:
            raise data.DataValidationError(f"Monthly manifest {month} has an invalid {name}")
    for key in ("raw", "checksum", "parquet"):
        relative = paths[key].relative_to(data.REPOSITORY_ROOT).as_posix()
        if manifest.get(f"{key}_path") != relative:
            raise data.DataValidationError(f"Monthly manifest {month} {key}_path is not the fixed path")
    for key in ("archive_sha256", "csv_sha256", "parquet_sha256"):
        _require_hash(manifest[key], key)
    source = manifest.get("source")
    url = f"{MONTHLY_URL}/BTCUSDT-1m-{month}.zip"
    if not isinstance(source, dict) or source.get("archive_url") != url or source.get("checksum_url") != url + ".CHECKSUM":
        raise data.DataValidationError(f"Monthly manifest {month} source URLs must match the exact official archive")
    return paths


def _utc_stat(value: object) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")


def _validate_parquet_footer(month: str, parquet: Path) -> None:
    """Gate declared date bounds and schema using the footer before row reads."""
    metadata = pq.read_metadata(parquet)
    start, end = month_bounds(month)
    if metadata.num_rows != (end - start).days * 1440:
        raise data.DataValidationError(f"Monthly Parquet {month} has an unexpected row count")
    schema = metadata.schema.to_arrow_schema()
    if schema.names != PARQUET_COLUMNS:
        raise data.DataValidationError(f"Monthly Parquet {month} schema columns differ from the verified contract")
    for field in schema:
        if field.name in ("open_time", "close_time"):
            valid = pa.types.is_timestamp(field.type) and field.type.tz == "UTC" and field.type.unit in ("us", "ns")
        elif field.name in ("number_of_trades", "open_time_raw", "close_time_raw"):
            valid = pa.types.is_int64(field.type)
        else:
            valid = pa.types.is_float64(field.type)
        if not valid:
            raise data.DataValidationError(f"Monthly Parquet {month} has invalid dtype for {field.name}")
    for name in ("open_time", "close_time", "open_time_raw", "close_time_raw"):
        column = metadata.schema.names.index(name)
        stats = [metadata.row_group(i).column(column).statistics for i in range(metadata.num_row_groups)]
        if not stats or any(stat is None or not stat.has_min_max or stat.null_count != 0 for stat in stats):
            raise data.DataValidationError(f"Monthly Parquet {month} lacks complete {name} range statistics")
        low, high = min(stat.min for stat in stats), max(stat.max for stat in stats)
        if name.endswith("_raw"):
            low, high = pd.to_datetime(low, unit="us", utc=True), pd.to_datetime(high, unit="us", utc=True)
        else:
            low, high = _utc_stat(low), _utc_stat(high)
        if low < start or high >= end:
            raise data.DataValidationError(f"Monthly Parquet {month} footer range escapes its permitted month")
        if name in ("open_time", "open_time_raw") and (low != start or high != end - pd.Timedelta(minutes=1)):
            raise data.DataValidationError(f"Monthly Parquet {month} footer does not cover exact month endpoints")


def _verify_cached_month(month: str) -> tuple[pd.DataFrame, dict]:
    # Import locally so policy/loader utilities do not initialize downloader state.
    from quantforge.data_monthly import verify_cached_month
    return verify_cached_month(month)


def _preflight(entries: list[dict]) -> list[tuple[str, dict, dict[str, Path], str]]:
    """Check every month/reference before opening the first archive/Parquet."""
    if not isinstance(entries, list) or len(entries) != len(ALLOWED_MONTHS):
        raise data.DataValidationError("Overall manifest must enumerate exactly ten month manifests")
    if any(not isinstance(entry, dict) for entry in entries):
        raise data.DataValidationError("Month manifest entries must be JSON objects")
    if [entry.get("month") for entry in entries] != list(ALLOWED_MONTHS):
        raise data.DataValidationError("Month entries must match the ordered frozen development whitelist")
    preflight = []
    # A malicious later entry must fail before even reading earlier manifests.
    for month, entry in zip(ALLOWED_MONTHS, entries, strict=True):
        expected = month_paths(month)["manifest"].relative_to(data.REPOSITORY_ROOT).as_posix()
        fixed_project_path(entry.get("manifest_path", ""), expected)
        _require_hash(entry.get("manifest_sha256"), "manifest_sha256")
    for month, entry in zip(ALLOWED_MONTHS, entries, strict=True):
        path = month_paths(month)["manifest"]
        manifest, digest = _read_json(path)
        if digest != entry["manifest_sha256"]:
            raise data.DataValidationError(f"Month manifest SHA256 mismatch for {month}")
        paths = _validate_month_manifest(month, manifest)
        preflight.append((month, manifest, paths, digest))
    return preflight


def _load_verified(preflight: list[tuple]) -> tuple[pd.DataFrame, dict]:
    started = time.perf_counter()
    frames, sources = [], []
    for month, manifest, paths, manifest_hash in preflight:
        for key, limit in (("raw", 4 * 1024 * 1024), ("parquet", 64 * 1024 * 1024)):
            expected_hash = manifest["archive_sha256" if key == "raw" else "parquet_sha256"]
            if _sha256_file(paths[key], limit) != expected_hash:
                raise data.DataValidationError(f"Monthly {month} {key} SHA256 mismatch")
        _validate_parquet_footer(month, paths["parquet"])
        frame, verified_manifest = _verify_cached_month(month)
        if any(verified_manifest.get(key) != manifest.get(key) for key in REQUIRED_MANIFEST_KEYS):
            raise data.DataValidationError(f"Monthly {month} provenance changed during verification")
        index = pd.DatetimeIndex(frame.open_time)
        start, end = month_bounds(month)
        expected = pd.date_range(start, end, freq="min", inclusive="left")
        if not index.equals(expected) or len(frame) != len(expected):
            raise data.DataValidationError(f"Monthly {month} minute grid is incomplete or out of order")
        frames.append(frame)
        sources.append({"month": month, "manifest_path": paths["manifest"].relative_to(data.REPOSITORY_ROOT).as_posix(),
                        "manifest_sha256": manifest_hash, "archive_sha256": manifest["archive_sha256"],
                        "csv_sha256": manifest["csv_sha256"], "parquet_sha256": manifest["parquet_sha256"],
                        "rows": len(frame), "raw_bytes": paths["raw"].stat().st_size,
                        "parquet_bytes": paths["parquet"].stat().st_size})
    combined = pd.concat(frames, ignore_index=True)
    index = pd.DatetimeIndex(combined.open_time)
    expected = pd.date_range(DEVELOPMENT_START, DEVELOPMENT_END, freq="min", inclusive="left")
    if len(combined) != EXPECTED_ROWS or not index.equals(expected):
        raise data.DataValidationError("Cross-month continuity or frozen development bounds failed")
    return combined, {
        "status": "passed", "month_sources": sources, "rows": len(combined),
        "start": DEVELOPMENT_START.isoformat(), "end_exclusive": DEVELOPMENT_END.isoformat(),
        "cross_month_boundaries": len(sources) - 1, "missing_minutes": 0, "duplicate_minutes": 0,
        "memory_bytes": int(combined.memory_usage(index=True, deep=True).sum()),
        "elapsed_seconds": time.perf_counter() - started, "holdout_accessed": False,
        "verification": "manifest/raw/Parquet hashes, official cached checksum, CSV exact Parquet equality and full UTC minute grid",
    }


def _validate_overall(overall: dict) -> None:
    expected = {"schema_version": 1, "status": "passed", "market": "spot", "symbol": "BTCUSDT", "interval": "1m",
                "months": list(ALLOWED_MONTHS), "expected_rows": EXPECTED_ROWS,
                "development_start": DEVELOPMENT_START.isoformat(), "development_end": DEVELOPMENT_END.isoformat(),
                "holdout_accessed": False}
    for key, value in expected.items():
        if type(overall.get(key)) is not type(value) or overall.get(key) != value:
            raise data.DataValidationError(f"Overall manifest {key} does not match the frozen policy")


def load_research_dataset(manifest_path: str | Path = DEFAULT_DEVELOPMENT_MANIFEST) -> tuple[pd.DataFrame, dict]:
    """Offline load of development data after policy/reference/integrity gates."""
    policy = load_period_policy()
    path = fixed_project_path(manifest_path, DEFAULT_DEVELOPMENT_MANIFEST)
    overall, digest = _read_json(path)
    _validate_overall(overall)
    bars, audit = _load_verified(_preflight(overall.get("month_manifests")))
    audit.update(policy=policy, overall_manifest_sha256=digest,
                 overall_manifest_path=path.relative_to(data.REPOSITORY_ROOT).as_posix())
    return bars, audit


def build_development_manifest() -> dict:
    """Verify all ten months and continuity, then atomically publish provenance."""
    load_period_policy()
    entries = []
    for month in ALLOWED_MONTHS:
        path = month_paths(month)["manifest"]
        manifest, digest = _read_json(path)
        _validate_month_manifest(month, manifest)
        entries.append({"month": month, "manifest_path": path.relative_to(data.REPOSITORY_ROOT).as_posix(), "manifest_sha256": digest})
    bars, audit = _load_verified(_preflight(entries))
    del bars
    overall = {"schema_version": 1, "status": "passed", "market": "spot", "symbol": "BTCUSDT", "interval": "1m",
               "months": list(ALLOWED_MONTHS), "month_manifests": entries, "expected_rows": EXPECTED_ROWS,
               "development_start": DEVELOPMENT_START.isoformat(), "development_end": DEVELOPMENT_END.isoformat(),
               "holdout_accessed": False, "validation": audit}
    path = fixed_project_path(DEFAULT_DEVELOPMENT_MANIFEST, DEFAULT_DEVELOPMENT_MANIFEST)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = fixed_project_path(str(path.relative_to(data.REPOSITORY_ROOT)) + ".partial", DEFAULT_DEVELOPMENT_MANIFEST + ".partial")
    payload = json.dumps(overall, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)
    return overall
