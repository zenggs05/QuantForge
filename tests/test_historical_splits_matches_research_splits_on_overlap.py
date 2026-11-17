"""AP9: independent calendar kernel matches every legacy sample and audit field."""

import hashlib
import importlib.util
from pathlib import Path
import subprocess

import pandas as pd
import pytest

from quantforge.datasets.historical_splits import _make_splits_internal
from quantforge.datasets.research_splits import make_research_splits
from quantforge.research_policy import DEVELOPMENT_END, DEVELOPMENT_START, EVALUATION_MONTHS


def metadata(horizon=15, start=DEVELOPMENT_START, end=DEVELOPMENT_END):
    index = pd.date_range(start, end, freq="min", inclusive="left", name="bar_open_time")
    boundary = index + pd.Timedelta(minutes=1)
    result = pd.DataFrame({
        "bar_end_exclusive": boundary, "feature_available_time": boundary + pd.Timedelta(seconds=1),
        "decision_time": boundary + pd.Timedelta(seconds=1), "target_start_time": boundary,
        "target_end_time": boundary + pd.Timedelta(minutes=horizon),
        "label_available_time": boundary + pd.Timedelta(minutes=horizon, seconds=1),
        "valid_features": True, "valid_label": True,
    }, index=index)
    result.loc[index[:60], "valid_features"] = False
    result.loc[index[-horizon:], "valid_label"] = False
    return result


@pytest.mark.parametrize("horizon", [15, 5])
def test_historical_kernel_matches_research_splits_on_overlap(horizon):
    samples = metadata(horizon)
    # Invalid rows and a delayed first evaluation observation exercise the masks,
    # rather than merely comparing the ordinary uninterrupted calendar path.
    samples.loc[pd.Timestamp("2026-03-10T12:00Z"), "valid_features"] = False
    samples.loc[pd.Timestamp("2026-04-10T12:00Z"), "valid_label"] = False
    legacy = make_research_splits(samples, horizon)
    historical = _make_splits_internal(samples, EVALUATION_MONTHS, horizon, "month_end",
                                        DEVELOPMENT_START, DEVELOPMENT_END)
    assert len(legacy) == len(historical) == 4
    for old, new in zip(legacy, historical, strict=True):
        assert old.train_indices.equals(new.train_indices)
        assert old.eval_indices.equals(new.eval_indices)
        assert old.fit_time == new.fit_time
        assert old.eval_start == new.eval_start
        assert old.eval_end == new.eval_end
        assert old.audit == {k: v for k, v in new.audit.items() if k not in {"role", "role_bucket"}}
        assert new.role == "historical_rolling"
