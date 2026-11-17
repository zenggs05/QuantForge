"""The daily/monthly comparison must fail on disagreement, never pick a winner."""

import importlib.util
from pathlib import Path
import sys

import pandas as pd
import pytest

from quantforge import data


@pytest.fixture
def comparison_module(monkeypatch, tmp_path, minute_bars):
    script = Path(__file__).resolve().parents[1] / "scripts/validate_monthly_data.py"
    spec = importlib.util.spec_from_file_location("monthly_validation_cli", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/validate_monthly_data.py").write_bytes(script.read_bytes())
    (tmp_path / "results/monthly-download").mkdir(parents=True)
    daily = minute_bars(1440, start="2025-09-01T00:00:00Z")
    month_prefix = pd.concat([daily, minute_bars(1440, start="2025-09-02T00:00:00Z")], ignore_index=True)
    daily.to_parquet(tmp_path / "daily.parquet", index=False)
    monkeypatch.setattr(module, "verify_cached_month", lambda month: (month_prefix, {"archive_sha256": "monthly-fixture"}))
    monkeypatch.setattr(module, "download_day", lambda day: {"parquet_path": "daily.parquet", "synthetic": True})
    return module


def test_first_day_comparison_exact_success(comparison_module):
    result = comparison_module.cross_check_first_day()
    assert result["rows_compared"] == 1440
    assert result["all_columns_exactly_equal"]


def test_daily_monthly_disagreement_fails_without_success_record(comparison_module, monkeypatch):
    path = data.repository_path("daily.parquet")
    frame = pd.read_parquet(path)
    frame.loc[10, "volume"] += 0.01
    frame.to_parquet(path, index=False)
    monkeypatch.setattr(sys, "argv", ["validate_monthly_data.py", "--cross-check-first-day"])
    assert comparison_module.main() == 1
    root = data.repository_path("results/monthly-download")
    assert len(list(root.glob("validation-failure-*.json"))) == 1
    assert not (root / "daily-cross-check.json").exists()
