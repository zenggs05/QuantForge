"""Shared contracts for observed, UTC-aligned one-minute market bars."""

from __future__ import annotations

import numpy as np
import pandas as pd

ONE_MINUTE = pd.Timedelta(minutes=1)
REQUIRED_BAR_COLUMNS = (
    "open", "high", "low", "close", "volume", "number_of_trades",
    "taker_buy_base_asset_volume",
)


class TemporalValidationError(ValueError):
    """Market data violate an explicit time or numeric contract."""


def utc_timestamp(value: object, name: str = "timestamp") -> pd.Timestamp:
    """Require a nonmissing aware timestamp and normalize it to UTC."""
    try:
        result = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TemporalValidationError(f"{name} must be a timezone-aware timestamp") from exc
    if pd.isna(result) or result.tzinfo is None:
        raise TemporalValidationError(f"{name} must be a nonmissing timezone-aware timestamp")
    return result.tz_convert("UTC")


def utc_index(values: object, name: str = "bar_open_time") -> pd.DatetimeIndex:
    """Require aware timestamps; never infer a timezone for naive input."""
    try:
        index = pd.DatetimeIndex(values)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TemporalValidationError(f"{name} must contain timezone-aware timestamps") from exc
    if index.tz is None or index.hasnans:
        raise TemporalValidationError(f"{name} must contain nonmissing timezone-aware timestamps")
    return index.tz_convert("UTC").as_unit("ns").rename(name)


def validate_delay(value: object) -> pd.Timedelta:
    """Validate an explicit nonnegative data-availability delay."""
    try:
        delay = pd.Timedelta(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TemporalValidationError("availability_delay must be a nonnegative duration") from exc
    if pd.isna(delay) or delay < pd.Timedelta(0):
        raise TemporalValidationError("availability_delay must be a nonnegative duration")
    return delay


def continuous_segments(index: pd.DatetimeIndex) -> pd.Series:
    """Each missing minute starts a new segment; missing bars are not filled."""
    return index.to_series().diff().ne(ONE_MINUTE).cumsum().rename("segment")


def gap_diagnostics(index: pd.DatetimeIndex) -> dict[str, int]:
    differences = index.to_series().diff().dropna()
    return {
        "gap_count": int(differences.gt(ONE_MINUTE).sum()),
        "missing_minutes": int(((differences / ONE_MINUTE) - 1).sum()),
        "segments": int(differences.gt(ONE_MINUTE).sum()) + 1,
    }


def validate_bars(bars: pd.DataFrame, require_contiguous: bool = False) -> pd.DataFrame:
    """Return an independent frame indexed by UTC ``bar_open_time``.

    Accept the existing ingestion output's ``open_time`` column, an explicit
    ``bar_open_time`` column, or an aware DatetimeIndex. Observations must be
    strictly increasing, unique, and aligned to minute boundaries. Aware input
    in another timezone is normalized to UTC; naive/mixed timestamps fail.
    OHLC prices must be positive and finite, volume nonnegative, and trade
    counts nonnegative integers. Gaps are allowed by default, never imputed.
    """
    if not isinstance(bars, pd.DataFrame) or bars.empty:
        raise TemporalValidationError("bars must be a nonempty DataFrame")
    if not bars.columns.is_unique:
        raise TemporalValidationError("bar column names must be unique")
    missing = set(REQUIRED_BAR_COLUMNS).difference(bars.columns)
    if missing:
        raise TemporalValidationError(f"Missing required bar columns: {sorted(missing)}")
    timestamp_sources = [bars[column] for column in ("bar_open_time", "open_time") if column in bars]
    if isinstance(bars.index, pd.DatetimeIndex):
        timestamp_sources.append(bars.index)
    if not timestamp_sources:
        raise TemporalValidationError("Provide open_time, bar_open_time, or an aware DatetimeIndex")
    index = utc_index(timestamp_sources[0])
    if any(not index.equals(utc_index(source)) for source in timestamp_sources[1:]):
        raise TemporalValidationError("Timestamp columns and index disagree")
    if not index.is_unique:
        raise TemporalValidationError("Duplicate bar open times are not allowed")
    if not index.is_monotonic_increasing:
        raise TemporalValidationError("Bars must be sorted in strictly increasing time order")
    if not index.equals(index.floor("min")):
        raise TemporalValidationError("Bar open times must align to one-minute boundaries")
    if require_contiguous and gap_diagnostics(index)["gap_count"]:
        raise TemporalValidationError("Missing minutes violate the contiguous-bar requirement")

    frame = bars.copy(deep=True)
    frame.index = index
    numeric_columns = list(REQUIRED_BAR_COLUMNS)
    numeric_columns += [name for name in ("quote_asset_volume", "taker_buy_quote_asset_volume") if name in frame]
    for name in numeric_columns:
        try:
            if pd.api.types.is_bool_dtype(frame[name]):
                raise ValueError("Boolean values are not market observations")
            frame[name] = pd.to_numeric(frame[name], errors="raise").astype("float64")
        except (ValueError, TypeError, OverflowError) as exc:
            raise TemporalValidationError(f"{name} must contain finite numeric values") from exc
        if not np.isfinite(frame[name].to_numpy()).all():
            raise TemporalValidationError(f"{name} must contain finite numeric values")
    prices = frame[["open", "high", "low", "close"]]
    if prices.le(0).any().any():
        raise TemporalValidationError("OHLC prices must be positive")
    if (frame.low.gt(frame.high) | frame.open.lt(frame.low) | frame.open.gt(frame.high)
            | frame.close.lt(frame.low) | frame.close.gt(frame.high)).any():
        raise TemporalValidationError("OHLC range constraints failed")
    volumes = [name for name in numeric_columns if name not in ("open", "high", "low", "close")]
    if frame[volumes].lt(0).any().any():
        raise TemporalValidationError("Volumes and trade counts must be nonnegative")
    if frame.number_of_trades.mod(1).ne(0).any():
        raise TemporalValidationError("number_of_trades must contain integer counts")
    if frame.taker_buy_base_asset_volume.gt(frame.volume + 1e-8).any():
        raise TemporalValidationError("Taker buy base volume exceeds total volume")
    if {"quote_asset_volume", "taker_buy_quote_asset_volume"}.issubset(frame.columns):
        if frame.taker_buy_quote_asset_volume.gt(frame.quote_asset_volume + 1e-8).any():
            raise TemporalValidationError("Taker buy quote volume exceeds total volume")
    if "close_time" in frame:
        close_times = utc_index(frame.close_time, "close_time")
        # Binance archives use either ms or us precision before a boundary.
        remaining = index + ONE_MINUTE - close_times
        if not ((remaining > pd.Timedelta(0)) & (remaining <= pd.Timedelta(milliseconds=1))).all():
            raise TemporalValidationError("close_time must be the final timestamp tick of its minute")
    return frame
