"""Frozen dataset access gates; all test market values are synthetic."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quantforge import data
from quantforge.datasets import research_dataset as loader
from quantforge.features import build_features
from quantforge.research_policy import (
    ALLOWED_MONTHS, DEFAULT_DEVELOPMENT_MANIFEST, DEFAULT_PERIOD_CONFIG,
    DEVELOPMENT_END, DEVELOPMENT_START, EXPECTED_ROWS, MONTHLY_URL,
    ResearchPolicyError, ensure_allowed_month, load_period_policy, month_bounds, month_paths,
)


@pytest.fixture
def research_root(tmp_path, monkeypatch):
    policy_text = (data.REPOSITORY_ROOT / DEFAULT_PERIOD_CONFIG).read_text(encoding="utf-8")
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    config = tmp_path / DEFAULT_PERIOD_CONFIG
    config.parent.mkdir(parents=True)
    config.write_text(policy_text, encoding="utf-8")
    return tmp_path


def synthetic_month(month):
    start, end = month_bounds(month)
    dates = pd.date_range(start, end, freq="min", inclusive="left").as_unit("us")
    n = len(dates)
    sequence = np.arange(n)
    frame = pd.DataFrame(index=pd.RangeIndex(n))
    frame["open_time"] = dates
    for name, value in (("open", 100.0), ("high", 102.0), ("low", 99.0), ("close", 101.0)):
        frame[name] = value
    frame["volume"] = (10 + sequence % 19).astype("float64")
    frame["close_time"] = dates + pd.Timedelta(minutes=1) - pd.Timedelta(microseconds=1)
    frame["quote_asset_volume"] = frame.volume * 100
    frame["number_of_trades"] = (20 + sequence % 23).astype("int64")
    frame["taker_buy_base_asset_volume"] = frame.volume * 0.4
    frame["taker_buy_quote_asset_volume"] = frame.quote_asset_volume * 0.4
    frame["ignore"] = 0.0
    frame["open_time_raw"] = dates.asi8
    frame["close_time_raw"] = pd.DatetimeIndex(frame.close_time).as_unit("us").asi8
    return frame


def month_manifest(month, root, with_parquet=False):
    paths = month_paths(month)
    paths["manifest"].parent.mkdir(parents=True, exist_ok=True)
    paths["raw"].parent.mkdir(parents=True, exist_ok=True)
    payload = f"synthetic archive stub for loader isolation: {month}".encode()
    paths["raw"].write_bytes(payload)
    archive_hash = data.sha256(payload)
    paths["checksum"].write_text(f"{archive_hash}  {paths['raw'].name}\n", encoding="ascii")
    frame = synthetic_month(month) if with_parquet else None
    if frame is not None:
        frame.to_parquet(paths["parquet"], index=False)
        parquet_hash = data.sha256(paths["parquet"].read_bytes())
    else:
        parquet_hash = "1" * 64
    url = f"{MONTHLY_URL}/BTCUSDT-1m-{month}.zip"
    manifest = {"schema_version": 1, "status": "passed", "market": "spot", "symbol": "BTCUSDT", "interval": "1m", "month": month,
                "raw_path": paths["raw"].relative_to(root).as_posix(), "checksum_path": paths["checksum"].relative_to(root).as_posix(),
                "parquet_path": paths["parquet"].relative_to(root).as_posix(), "archive_sha256": archive_hash,
                "csv_sha256": "2" * 64, "parquet_sha256": parquet_hash,
                "source": {"archive_url": url, "checksum_url": url + ".CHECKSUM"}}
    paths["manifest"].write_text(json.dumps(manifest), encoding="utf-8")
    return manifest, frame


def overall_manifest(root, month_manifests):
    entries = []
    for month in ALLOWED_MONTHS:
        path = month_paths(month)["manifest"]
        entries.append({"month": month, "manifest_path": path.relative_to(root).as_posix(),
                        "manifest_sha256": data.sha256(path.read_bytes())})
    overall = {"schema_version": 1, "status": "passed", "market": "spot", "symbol": "BTCUSDT", "interval": "1m",
               "months": list(ALLOWED_MONTHS), "month_manifests": entries, "expected_rows": EXPECTED_ROWS,
               "development_start": DEVELOPMENT_START.isoformat(), "development_end": DEVELOPMENT_END.isoformat(),
               "holdout_accessed": False}
    path = root / DEFAULT_DEVELOPMENT_MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(overall), encoding="utf-8")
    return path, overall


def test_build_and_load_ten_synthetic_months_preserves_cross_year_and_month_history(research_root, monkeypatch):
    """Test loader contract in isolation; monthly ZIP parsing has its own tests."""
    stored = {month: month_manifest(month, research_root, with_parquet=True) for month in ALLOWED_MONTHS}
    calls = []

    def verify(month):
        calls.append(month)
        manifest, frame = stored[month]
        return frame, manifest

    monkeypatch.setattr(loader, "_verify_cached_month", verify)
    built = loader.build_development_manifest()
    assert built["status"] == "passed"
    assert calls == list(ALLOWED_MONTHS)
    assert (research_root / DEFAULT_DEVELOPMENT_MANIFEST).is_file()
    bars, audit = loader.load_research_dataset()
    assert len(bars) == EXPECTED_ROWS
    assert bars.open_time.iloc[0] == DEVELOPMENT_START
    assert bars.open_time.iloc[-1] == DEVELOPMENT_END - pd.Timedelta(minutes=1)
    assert audit["memory_bytes"] > 0 and audit["elapsed_seconds"] >= 0
    assert audit["cross_month_boundaries"] == 9
    assert audit["missing_minutes"] == audit["duplicate_minutes"] == 0
    assert [row["month"] for row in audit["month_sources"]] == list(ALLOWED_MONTHS)
    around = bars.loc[bars.open_time.between(pd.Timestamp("2025-12-31T22:00Z"), pd.Timestamp("2026-01-01T01:00Z"))]
    features = build_features(around)
    assert features.metadata.loc[pd.Timestamp("2026-01-01T00:00Z"), "valid_features"]
    assert not pd.isna(features.frame.loc[pd.Timestamp("2026-01-01T00:00Z"), "volume_zscore_60m"])


@pytest.mark.parametrize("month", ["2026-07", "2026-08", "2026-09", "2025-08", "2026-06/../../2026-07"])
def test_forbidden_month_fails_before_any_file_open(monkeypatch, month):
    def no_open(*args, **kwargs):
        raise AssertionError("A rejected month must not cause filesystem reads")
    monkeypatch.setattr(Path, "open", no_open)
    with pytest.raises(ResearchPolicyError, match="whitelist"):
        ensure_allowed_month(month)
    with pytest.raises(ResearchPolicyError, match="whitelist"):
        month_paths(month)


def test_holdout_manifest_path_rejected_before_source_read(research_root, monkeypatch):
    monkeypatch.setattr(loader, "_read_json", lambda path: pytest.fail("Must reject path before opening a manifest"))
    with pytest.raises(ResearchPolicyError, match="fixed development path"):
        loader.load_research_dataset("data/processed/binance/spot/BTCUSDT/1m/monthly/BTCUSDT-1m-2026-07.json")


@pytest.mark.parametrize("replacement", ["2026-08-01T00:00:00Z", "2026-07-02T00:00:00Z"])
def test_config_bounds_cannot_override_policy_before_source_access(research_root, monkeypatch, replacement):
    path = research_root / DEFAULT_PERIOD_CONFIG
    path.write_text(path.read_text(encoding="utf-8").replace('development_end = "2026-07-01T00:00:00Z"', f'development_end = "{replacement}"'), encoding="utf-8")
    monkeypatch.setattr(loader, "_read_json", lambda path: pytest.fail("Invalid policy cannot access market source manifests"))
    with pytest.raises(ResearchPolicyError, match="frozen"):
        loader.load_research_dataset()


def test_late_manifest_redirect_is_rejected_before_first_market_read(research_root, monkeypatch):
    manifests = {month: month_manifest(month, research_root)[0] for month in ALLOWED_MONTHS}
    path, overall = overall_manifest(research_root, manifests)
    bad = month_paths("2026-06")["manifest"]
    manifest = manifests["2026-06"]
    manifest["parquet_path"] = manifest["parquet_path"].replace("2026-06", "2026-07")
    bad.write_text(json.dumps(manifest), encoding="utf-8")
    overall["month_manifests"][-1]["manifest_sha256"] = data.sha256(bad.read_bytes())
    path.write_text(json.dumps(overall), encoding="utf-8")
    monkeypatch.setattr(loader, "_sha256_file", lambda *args: pytest.fail("All source references must be preflighted first"))
    with pytest.raises(data.DataValidationError, match="fixed path"):
        loader.load_research_dataset()


def test_wrong_manifest_hash_is_rejected_before_market_access(research_root, monkeypatch):
    manifests = {month: month_manifest(month, research_root)[0] for month in ALLOWED_MONTHS}
    path, overall = overall_manifest(research_root, manifests)
    overall["month_manifests"][0]["manifest_sha256"] = "f" * 64
    path.write_text(json.dumps(overall), encoding="utf-8")
    monkeypatch.setattr(loader, "_sha256_file", lambda *args: pytest.fail("Invalid manifest hash must stop before market reads"))
    with pytest.raises(data.DataValidationError, match="manifest SHA256"):
        loader.load_research_dataset()


def test_footer_rejects_synthetic_out_of_month_payload_before_row_loading(research_root, monkeypatch):
    month = "2026-06"
    manifest, frame = month_manifest(month, research_root, with_parquet=True)
    # This is artificial data shifted beyond the declared month, not holdout access.
    frame["open_time"] += pd.Timedelta(days=1)
    frame["close_time"] += pd.Timedelta(days=1)
    frame["open_time_raw"] += 86_400_000_000
    frame["close_time_raw"] += 86_400_000_000
    parquet = month_paths(month)["parquet"]
    frame.to_parquet(parquet, index=False)
    monkeypatch.setattr(pd, "read_parquet", lambda *args, **kwargs: pytest.fail("Footer rejection must precede row loading"))
    with pytest.raises(data.DataValidationError, match="footer range"):
        loader._validate_parquet_footer(month, parquet)


def test_build_failure_never_publishes_success_manifest(research_root, monkeypatch):
    for month in ALLOWED_MONTHS:
        month_manifest(month, research_root)
    def fail(preflight):
        raise data.DataValidationError("Synthetic cross-month discontinuity")
    monkeypatch.setattr(loader, "_load_verified", fail)
    with pytest.raises(data.DataValidationError, match="discontinuity"):
        loader.build_development_manifest()
    assert not (research_root / DEFAULT_DEVELOPMENT_MANIFEST).exists()


def test_manifest_schema_and_identity_cannot_be_swapped(research_root):
    manifest, _ = month_manifest("2025-09", research_root)
    manifest["market"], manifest["symbol"] = manifest["symbol"], manifest["market"]
    with pytest.raises(data.DataValidationError, match="market"):
        loader._validate_month_manifest("2025-09", manifest)
    manifest.pop("csv_sha256")
    manifest["unrelated"] = "extra does not repair missing key"
    with pytest.raises(data.DataValidationError, match="missing"):
        loader._validate_month_manifest("2025-09", manifest)
