"""Delayed close-to-close prediction labels, never executable PnL targets."""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Integral
from typing import Iterable

import numpy as np
import pandas as pd

from quantforge.temporal import (
    ONE_MINUTE, TemporalValidationError, continuous_segments, gap_diagnostics,
    utc_index, utc_timestamp, validate_bars, validate_delay,
)


@dataclass(frozen=True)
class LabelResult:
    frame: pd.DataFrame
    diagnostics: dict


class LabelAvailabilityError(ValueError):
    """A requested target is invalid or was not observable at the fit time."""


def build_labels(bars: pd.DataFrame, horizon_minutes: int,
                 availability_delay: pd.Timedelta = pd.Timedelta(seconds=1)) -> LabelResult:
    """Compute log(close[t+h]/close[t]) over [bar_end[t], bar_end[t+h]).

    ``target_start_time`` uses the exclusive bar boundary, not Binance's final
    microsecond tick. The target matures at ``target_end_time + delay``. A
    feature decision occurs at start+delay, so these are statistical prediction
    labels: the starting close is not an executable price at that decision.
    Any missing bar in the horizon makes the label invalid, even if both price
    endpoints exist. Tail rows remain present with explicit exclusion reasons.
    """
    if isinstance(horizon_minutes, bool) or not isinstance(horizon_minutes, Integral) or horizon_minutes <= 0:
        raise TemporalValidationError("horizon_minutes must be a positive integer")
    delay = validate_delay(availability_delay)
    normalized = validate_bars(bars)
    try:
        horizon = pd.Timedelta(minutes=int(horizon_minutes))
    except (ValueError, OverflowError) as exc:
        raise TemporalValidationError("horizon_minutes is outside supported timestamp range") from exc
    segments = continuous_segments(normalized.index)
    future_close = normalized.close.groupby(segments, sort=False).shift(-int(horizon_minutes))
    target = np.log(future_close) - np.log(normalized.close)
    valid = target.notna() & np.isfinite(target)
    reasons = pd.Series("", index=normalized.index, dtype="str")
    reasons.loc[~valid] = "gap_in_label_horizon"
    reasons.loc[normalized.index + horizon > normalized.index[-1]] = "insufficient_future_data"
    frame = pd.DataFrame(index=normalized.index)
    frame["target_return"] = target
    frame["target_start_time"] = normalized.index + ONE_MINUTE
    frame["target_end_time"] = frame.target_start_time + horizon
    frame["label_available_time"] = frame.target_end_time + delay
    frame["valid_label"] = valid
    frame["label_exclusion_reason"] = reasons
    return LabelResult(frame=frame, diagnostics={
        "rows": len(frame), "valid_rows": int(valid.sum()), "excluded_rows": int((~valid).sum()),
        "horizon_minutes": int(horizon_minutes), "availability_delay_seconds": delay.total_seconds(),
        "exclusion_counts": reasons.loc[~valid].value_counts().to_dict(),
        "target_semantics": "close-to-close log-return prediction; not executable PnL",
        **gap_diagnostics(frame.index),
    })


def _validate_label_frame(label_frame: pd.DataFrame) -> None:
    required = {"target_return", "target_start_time", "target_end_time", "label_available_time", "valid_label"}
    if not isinstance(label_frame, pd.DataFrame) or not required.issubset(label_frame.columns):
        raise LabelAvailabilityError("Label frame is missing required target or availability columns")
    index = utc_index(label_frame.index)
    if not index.is_unique or not index.is_monotonic_increasing:
        raise LabelAvailabilityError("Label frame index must be unique and increasing")
    if not pd.api.types.is_bool_dtype(label_frame.valid_label) or label_frame.valid_label.isna().any():
        raise LabelAvailabilityError("valid_label must contain nonmissing booleans")
    for name in ("target_start_time", "target_end_time", "label_available_time"):
        utc_index(label_frame[name], name)


def mature_labels(label_frame: pd.DataFrame, as_of: object,
                  indices: Iterable[pd.Timestamp] | None = None) -> pd.Series:
    """Return requested targets only if EVERY selected label is valid/mature.

    ``indices=None`` deliberately checks all rows, including an invalid tail.
    Call ``select_mature_labels`` for explicit filtering rather than silently
    discarding invalid or not-yet-observable requested training labels.
    """
    _validate_label_frame(label_frame)
    fit_time = utc_timestamp(as_of, "as_of")
    selected = label_frame if indices is None else label_frame.loc[list(indices)]
    if not selected.index.is_unique:
        raise LabelAvailabilityError("Selected label indices must be unique")
    if (not selected.valid_label.all() or not np.isfinite(selected.target_return.to_numpy(dtype=float)).all()
            or selected.target_start_time.ge(selected.target_end_time).any()
            or selected.label_available_time.lt(selected.target_end_time).any()):
        raise LabelAvailabilityError("Selected labels include invalid targets or inconsistent label intervals")
    if selected.label_available_time.gt(fit_time).any():
        raise LabelAvailabilityError("Selected labels are immature at as_of; future labels cannot be consumed")
    return selected.target_return.copy()


def select_mature_labels(label_frame: pd.DataFrame, as_of: object) -> pd.Series:
    """Explicitly select valid labels whose information has arrived by as_of."""
    _validate_label_frame(label_frame)
    fit_time = utc_timestamp(as_of, "as_of")
    mask = label_frame.valid_label & label_frame.label_available_time.le(fit_time)
    return mature_labels(label_frame, fit_time, indices=label_frame.index[mask])
