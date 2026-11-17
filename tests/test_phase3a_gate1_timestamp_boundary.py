"""Gate 1 timestamp transition checks use synthetic rows only."""

from datetime import date
import socket
import urllib.request

import pandas as pd
import pytest

from quantforge import data, data_monthly as monthly
from test_data import csv_bytes, synthetic_rows


@pytest.fixture(autouse=True)
def no_market_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("A synthetic Gate 1 test attempted network access")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.mark.parametrize("day,unit", [
    (date(2024, 12, 31), "ms"),
    (date(2025, 1, 1), "us"),
])
def test_gate1_synthetic_cutover_day_preserves_every_minute(day, unit):
    frame, validation = monthly.parse_historical_daily_klines(
        csv_bytes(synthetic_rows(day, unit)), day)
    expected = pd.date_range(pd.Timestamp(day, tz="UTC"), periods=1440, freq="min")
    assert pd.DatetimeIndex(frame.open_time).equals(expected)
    assert validation["timestamp_unit_raw"] == unit
    assert monthly.detect_unit(int(frame.open_time_raw.iloc[0])) == unit
    assert frame.close_time.iloc[-1] == expected[-1] + pd.Timedelta(minutes=1) - pd.Timedelta(1, unit=unit)


@pytest.mark.parametrize("day,wrong_unit,expected_unit", [
    (date(2024, 12, 31), "us", "ms"),
    (date(2025, 1, 1), "ms", "us"),
])
def test_gate1_inverse_cutover_unit_is_rejected_before_parser(
        day, wrong_unit, expected_unit, monkeypatch):
    monkeypatch.setattr(data, "parse_klines", lambda *args, **kwargs: pytest.fail(
        "The daily parser ran before the timestamp policy gate"))
    with pytest.raises(data.DataValidationError, match=(
            f"timestamp unit mismatch: expected {expected_unit} for month {day:%Y-%m}, got {wrong_unit}")):
        monthly.parse_historical_daily_klines(csv_bytes(synthetic_rows(day, wrong_unit)), day)
