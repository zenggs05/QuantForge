"""Synthetic timestamp-policy boundary fixtures; no historical market rows."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from quantforge import data, data_monthly as monthly, research_policy as policy
from test_data import csv_bytes, synthetic_rows
from test_data_monthly import synthetic_csv


@pytest.mark.parametrize("day,unit", [(date(2024, 12, 31), "ms"), (date(2025, 1, 1), "us")])
def test_daily_boundary_exact_units(day, unit):
    frame, report = monthly.parse_historical_daily_klines(csv_bytes(synthetic_rows(day, unit)), day)
    assert report["timestamp_unit_raw"] == unit
    assert frame.open_time.iloc[0] == pd.Timestamp(day, tz="UTC")


@pytest.mark.parametrize("day,wrong,expected", [(date(2024, 12, 31), "us", "ms"), (date(2025, 1, 1), "ms", "us")])
def test_inverse_daily_unit_rejected_before_old_parser(day, wrong, expected, monkeypatch):
    monkeypatch.setattr(data, "parse_klines", lambda *args: pytest.fail("Inverse unit reached old parser"))
    with pytest.raises(data.DataValidationError, match=f"timestamp unit mismatch: expected {expected} for month {day:%Y-%m}, got {wrong}"):
        monthly.parse_historical_daily_klines(csv_bytes(synthetic_rows(day, wrong)), day)


@pytest.mark.parametrize("month,unit", [("2024-12", "ms"), ("2025-01", "us")])
def test_full_month_boundary_is_validated(month, unit):
    frame, report = monthly.parse_historical_monthly_klines(synthetic_csv(month), month)
    assert report["timestamp_unit_raw"] == unit
    assert report["rows"] == len(frame) == 31 * 1440


@pytest.mark.parametrize("value", [True, -1, 1.5, float("inf"), float("nan"), "1000000000000", 10**14])
def test_detect_unit_rejects_noninteger_or_ambiguous_values(value):
    with pytest.raises(data.DataValidationError):
        monthly.detect_unit(value)


def test_detect_unit_integer_magnitude_thresholds():
    assert monthly.detect_unit(np.int64(10**15)) == "us"
    assert monthly.detect_unit(10**14 - 1) == "ms"


def test_mixed_open_close_units_fail_before_delegate(monkeypatch):
    day = date(2025, 1, 1)
    rows = synthetic_rows(day, "us")
    rows[40][6] = str(int(rows[40][6]) // 1000)
    monkeypatch.setattr(data, "parse_klines", lambda *args: pytest.fail("Mixed units reached old parser"))
    with pytest.raises(data.DataValidationError, match="Mixed or ambiguous"):
        monthly.parse_historical_daily_klines(csv_bytes(rows), day)


@pytest.mark.parametrize("month", ["2026-07", "2026-08", "2026-09", "2026-10"])
def test_history_month_rejected_before_csv_or_config_reads(month, monkeypatch):
    monkeypatch.setattr(monthly.pd, "read_csv", lambda *args, **kwargs: pytest.fail("Forbidden month parsed"))
    with pytest.raises(policy.ResearchPolicyError, match="historical allowlist"):
        monthly.parse_historical_monthly_klines(b"never read", month)
