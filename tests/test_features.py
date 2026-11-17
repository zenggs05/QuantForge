"""Causal features must be insensitive to future modifications or appends."""

import numpy as np
import pandas as pd
import pytest

from quantforge.features import FEATURE_COLUMNS, build_features
from quantforge.temporal import TemporalValidationError, validate_bars


def _assert_prefix_invariant(builder, original, changed, cutoff):
    before = builder(original)
    after = builder(changed)
    pd.testing.assert_frame_equal(before.loc[:cutoff], after.loc[:cutoff], check_exact=True)


def test_known_formulas_availability_and_no_input_mutation(minute_bars):
    bars = minute_bars()
    original = bars.copy(deep=True)
    result = build_features(bars)
    row = result.frame.iloc[80]
    assert tuple(result.frame.columns) == FEATURE_COLUMNS
    assert result.frame.index.name == "bar_open_time"
    assert str(result.frame.index.tz) == "UTC"
    assert all(pd.api.types.is_numeric_dtype(dtype) for dtype in result.frame.dtypes)
    assert row.log_return_5m == pytest.approx(np.log(bars.close.iloc[80] / bars.close.iloc[75]))
    assert row.return_std_5m == pytest.approx(np.diff(np.log(bars.close.iloc[75:81])).std(ddof=1))
    volume_reference = bars.volume.iloc[20:80]
    assert row.volume_zscore_60m == pytest.approx((bars.volume.iloc[80] - volume_reference.mean()) / volume_reference.std(ddof=1))
    assert row.volume_change_1m == pytest.approx(bars.volume.iloc[80] / bars.volume.iloc[79] - 1)
    assert row.trades_change_60m == pytest.approx(bars.number_of_trades.iloc[80] / bars.number_of_trades.iloc[20:80].mean() - 1)
    assert row.taker_buy_volume_ratio == pytest.approx(0.4)
    assert row.log_high_low == pytest.approx(np.log(bars.high.iloc[80] / bars.low.iloc[80]))
    assert row.log_close_open == pytest.approx(np.log(bars.close.iloc[80] / bars.open.iloc[80]))
    assert row.close_range_position == pytest.approx((bars.close.iloc[80] - bars.low.iloc[80]) / (bars.high.iloc[80] - bars.low.iloc[80]))
    assert result.metadata.bar_end_exclusive.iloc[0] == pd.Timestamp("2026-09-01T00:01:00Z")
    assert result.metadata.decision_time.iloc[0] == pd.Timestamp("2026-09-01T00:01:01Z")
    assert not result.metadata.valid_features.iloc[:60].any()
    assert result.metadata.valid_features.iloc[60:].all()
    assert result.diagnostics["valid_rows"] == len(bars) - 60
    pd.testing.assert_frame_equal(bars, original, check_exact=True)


def test_mutating_future_bars_preserves_past_features(minute_bars):
    bars = minute_bars()
    changed = bars.copy(deep=True)
    for column in ("open", "high", "low", "close"):
        changed.loc[101:, column] *= 4
    for column in ("volume", "taker_buy_base_asset_volume", "quote_asset_volume", "taker_buy_quote_asset_volume"):
        changed.loc[101:, column] *= 3
    changed.loc[101:, "number_of_trades"] += 100
    cutoff = bars.open_time.iloc[100]
    _assert_prefix_invariant(lambda data: build_features(data).frame, bars, changed, cutoff)
    _assert_prefix_invariant(lambda data: build_features(data).metadata, bars, changed, cutoff)


def test_appending_future_bars_preserves_prefix(minute_bars):
    full = minute_bars()
    prefix = full.iloc[:160].copy()
    _assert_prefix_invariant(lambda data: build_features(data).frame, prefix, full, prefix.open_time.iloc[-1])
    _assert_prefix_invariant(lambda data: build_features(data).metadata, prefix, full, prefix.open_time.iloc[-1])


def test_causality_check_detects_deliberate_future_shift(minute_bars):
    """Negative control: a known leaky implementation must fail our oracle."""
    bars = minute_bars()
    changed = bars.copy(deep=True)
    changed.loc[101, "close"] *= 2

    def deliberately_leaky(data):
        return pd.DataFrame({"future_close": data.close.shift(-1).to_numpy()}, index=data.open_time)

    with pytest.raises(AssertionError):
        _assert_prefix_invariant(deliberately_leaky, bars, changed, bars.open_time.iloc[100])


def test_missing_minute_resets_windows_without_imputation(minute_bars):
    bars = minute_bars().drop(index=90)
    result = build_features(bars)
    assert len(result.frame) == len(bars)
    assert pd.Timestamp("2026-09-01T01:30:00Z") not in result.frame.index
    first_after_gap = pd.Timestamp("2026-09-01T01:31:00Z")
    assert pd.isna(result.frame.loc[first_after_gap, "log_return_1m"])
    assert not result.metadata.loc[first_after_gap, "valid_features"]
    assert result.metadata.loc[first_after_gap + pd.Timedelta(minutes=59), "valid_features"] == False
    assert result.metadata.loc[first_after_gap + pd.Timedelta(minutes=60), "valid_features"] == True
    assert result.diagnostics["gap_count"] == result.diagnostics["missing_minutes"] == 1
    with pytest.raises(TemporalValidationError, match="Missing minutes"):
        validate_bars(bars, require_contiguous=True)


def test_zero_volume_variance_and_range_remain_explicitly_invalid(minute_bars):
    bars = minute_bars()
    bars["volume"] = 0.0
    bars["taker_buy_base_asset_volume"] = 0.0
    bars["quote_asset_volume"] = 0.0
    bars["taker_buy_quote_asset_volume"] = 0.0
    for column in ("open", "high", "low"):
        bars[column] = bars.close
    result = build_features(bars)
    assert result.frame.volume_zscore_60m.isna().all()
    assert result.frame.taker_buy_volume_ratio.isna().all()
    assert result.frame.close_range_position.isna().all()
    assert not result.metadata.valid_features.any()
    assert result.metadata.feature_exclusion_reason.iloc[60] == "undefined_ratio_or_variance"
    assert not np.isinf(result.frame.to_numpy()).any()


@pytest.mark.parametrize("failure", ["duplicate", "reversed", "naive", "unaligned", "nonfinite", "bad_ohlc", "fractional_trades", "negative_volume"])
def test_invalid_bar_contract_rejected(minute_bars, failure):
    bars = minute_bars()
    if failure == "duplicate":
        bars.loc[1, "open_time"] = bars.open_time.iloc[0]
    elif failure == "reversed":
        bars = bars.iloc[::-1]
    elif failure == "naive":
        bars["open_time"] = bars.open_time.dt.tz_localize(None)
    elif failure == "unaligned":
        bars["open_time"] += pd.Timedelta(seconds=1)
    elif failure == "nonfinite":
        bars.loc[2, "close"] = np.inf
    elif failure == "bad_ohlc":
        bars.loc[2, "high"] = 1.0
    elif failure == "fractional_trades":
        bars["number_of_trades"] = bars.number_of_trades.astype(float)
        bars.loc[2, "number_of_trades"] = 2.5
    elif failure == "negative_volume":
        bars.loc[2, "volume"] = -1.0
    with pytest.raises(TemporalValidationError):
        build_features(bars)


@pytest.mark.parametrize("delay", [pd.Timedelta(seconds=-1), pd.NaT])
def test_invalid_availability_delay_rejected(minute_bars, delay):
    with pytest.raises(TemporalValidationError, match="nonnegative"):
        build_features(minute_bars(), availability_delay=delay)


def test_aware_timezone_normalizes_and_ms_close_boundary_is_supported(minute_bars):
    utc_bars = minute_bars()
    local_bars = utc_bars.copy(deep=True)
    local_bars["open_time"] = local_bars.open_time.dt.tz_convert("Asia/Shanghai")
    local_bars["close_time"] = (utc_bars.open_time + pd.Timedelta(minutes=1) - pd.Timedelta(milliseconds=1)).dt.tz_convert("Asia/Shanghai")
    expected = build_features(utc_bars)
    actual = build_features(local_bars)
    pd.testing.assert_frame_equal(actual.frame, expected.frame, check_exact=True)
    pd.testing.assert_frame_equal(actual.metadata, expected.metadata, check_exact=True)


def test_timestamp_index_disagreement_and_bad_close_are_rejected(minute_bars):
    bars = minute_bars()
    bars.index = pd.DatetimeIndex(bars.open_time + pd.Timedelta(minutes=1))
    with pytest.raises(TemporalValidationError, match="disagree"):
        build_features(bars)
    bars = minute_bars()
    bars.loc[0, "close_time"] = bars.open_time.iloc[0] + pd.Timedelta(minutes=1)
    with pytest.raises(TemporalValidationError, match="final timestamp"):
        build_features(bars)
