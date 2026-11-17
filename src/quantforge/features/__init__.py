"""Causal one-minute OHLCV features with explicit availability metadata.

All rolling references use only observed bars from the same continuous minute
segment. Undefined ratios, zero historical variance and warmup remain NaN.
No market observations are forward-filled or otherwise imputed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from quantforge.temporal import (
    ONE_MINUTE, continuous_segments, gap_diagnostics, validate_bars, validate_delay,
)

FEATURE_FORMULAS = {
    **{f"log_return_{h}m": f"log(close[t]) - log(close[t-{h}])" for h in (1, 5, 15, 60)},
    **{f"return_std_{w}m": f"sample std(ddof=1) of last {w} one-minute log returns, ending at t" for w in (5, 15, 60)},
    "volume_change_1m": "volume[t] / volume[t-1] - 1; prior volume must be positive",
    "volume_zscore_60m": "(volume[t] - mean(volume[t-60:t])) / sample_std(volume[t-60:t]); excludes current bar from reference",
    "taker_buy_volume_ratio": "taker_buy_base_asset_volume[t] / volume[t]; volume must be positive",
    "trades_change_60m": "number_of_trades[t] / mean(number_of_trades[t-60:t]) - 1; excludes current bar from reference",
    "log_high_low": "log(high[t]) - log(low[t])",
    "log_close_open": "log(close[t]) - log(open[t])",
    "close_range_position": "(close[t] - low[t]) / (high[t] - low[t]); range must be positive",
}
FEATURE_COLUMNS = tuple(FEATURE_FORMULAS)


@dataclass(frozen=True)
class FeatureResult:
    frame: pd.DataFrame
    metadata: pd.DataFrame
    diagnostics: dict


def _segment_features(segment: pd.DataFrame) -> pd.DataFrame:
    log_close = np.log(segment.close)
    output = pd.DataFrame(index=segment.index)
    for horizon in (1, 5, 15, 60):
        output[f"log_return_{horizon}m"] = log_close.diff(horizon)
    returns = log_close.diff()
    for window in (5, 15, 60):
        output[f"return_std_{window}m"] = returns.rolling(window, min_periods=window).std(ddof=1)
    previous_volume = segment.volume.shift(1)
    output["volume_change_1m"] = segment.volume.div(previous_volume.where(previous_volume.gt(0))).sub(1)
    volume_history = previous_volume.rolling(60, min_periods=60)
    historical_std = volume_history.std(ddof=1)
    output["volume_zscore_60m"] = (segment.volume - volume_history.mean()).div(historical_std.where(historical_std.gt(0)))
    output["taker_buy_volume_ratio"] = segment.taker_buy_base_asset_volume.div(segment.volume.where(segment.volume.gt(0)))
    trade_reference = segment.number_of_trades.shift(1).rolling(60, min_periods=60).mean()
    output["trades_change_60m"] = segment.number_of_trades.div(trade_reference.where(trade_reference.gt(0))).sub(1)
    output["log_high_low"] = np.log(segment.high) - np.log(segment.low)
    output["log_close_open"] = np.log(segment.close) - np.log(segment.open)
    price_range = segment.high - segment.low
    output["close_range_position"] = (segment.close - segment.low).div(price_range.where(price_range.gt(0)))
    return output.replace([np.inf, -np.inf], np.nan)


def build_features(bars: pd.DataFrame, availability_delay: pd.Timedelta = pd.Timedelta(seconds=1)) -> FeatureResult:
    """Build features known only at ``bar_end_exclusive + availability_delay``.

    The UTC bar-open index identifies a sample; it is NOT the decision time.
    The current bar's OHLCV is used only after that whole minute has finished.
    Longest windows require 60 previous consecutive bars (61 observations).
    """
    delay = validate_delay(availability_delay)
    normalized = validate_bars(bars)
    segments = continuous_segments(normalized.index)
    frame = pd.concat([_segment_features(group) for _, group in normalized.groupby(segments, sort=False)])
    frame = frame.loc[:, FEATURE_COLUMNS]
    valid = frame.notna().all(axis=1)
    position = normalized.groupby(segments, sort=False).cumcount()
    reasons = pd.Series("", index=frame.index, dtype="str")
    reasons.loc[~valid] = "undefined_ratio_or_variance"
    reasons.loc[position.lt(60)] = "insufficient_contiguous_history"
    metadata = pd.DataFrame(index=frame.index)
    metadata["bar_open_time"] = frame.index
    metadata["bar_end_exclusive"] = frame.index + ONE_MINUTE
    metadata["feature_available_time"] = metadata.bar_end_exclusive + delay
    metadata["decision_time"] = metadata.feature_available_time
    metadata["valid_features"] = valid
    metadata["feature_exclusion_reason"] = reasons
    diagnostics = {
        "rows": len(frame), "valid_rows": int(valid.sum()), "excluded_rows": int((~valid).sum()),
        "feature_count": len(frame.columns), "availability_delay_seconds": delay.total_seconds(),
        "exclusion_counts": reasons.loc[~valid].value_counts().to_dict(),
        **gap_diagnostics(frame.index),
    }
    return FeatureResult(frame=frame, metadata=metadata, diagnostics=diagnostics)
