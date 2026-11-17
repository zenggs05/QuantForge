"""Exploratory trailing-window forecast means; no p-values or decisions.

The output timestamp is the final observation of each complete window:
r_t = mean(c_{t-W+1}, ..., c_t). Signed and absolute curves describe time
variation only; they do not implement a calibrated fluctuation test.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantforge.diagnostics.forecast_tests import _integer, _vector


def giacomini_rossi_fluctuation(series, window=12, *, times=None) -> pd.DataFrame:
    """Return complete trailing-window signed/absolute mean curves, without p."""
    values = _vector(series)
    window = _integer(window, "window", 1)
    if window > len(values):
        raise ValueError("window exceeds the available observations")
    if times is None:
        labels = np.arange(1, len(values) + 1)
    else:
        labels = pd.Index(times)
        if (len(labels) != len(values) or labels.hasnans or labels.has_duplicates
                or not labels.is_monotonic_increasing):
            raise ValueError("times must be aligned, sorted, unique and nonmissing")
    windows = np.lib.stride_tricks.sliding_window_view(values, window)
    signed = windows.mean(axis=1)
    if not np.isfinite(signed).all():
        raise ValueError("rolling mean arithmetic overflowed")
    return pd.DataFrame({
        "window_start": labels[:len(signed)],
        "window_end": labels[window - 1:],
        "window_observations": window,
        "signed_mean": signed,
        "absolute_mean": np.abs(signed),
        "window_label": "trailing_window_end",
        "evidence_level": "exploratory_descriptive",
    })
