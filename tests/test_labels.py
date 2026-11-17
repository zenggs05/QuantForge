"""Label maturity, horizon integrity and future-endpoint independence."""

import numpy as np
import pandas as pd
import pytest

from quantforge.labels import LabelAvailabilityError, build_labels, mature_labels, select_mature_labels
from quantforge.temporal import TemporalValidationError


@pytest.mark.parametrize("horizon", [5, 15, 60])
def test_label_formula_interval_and_tail(minute_bars, horizon):
    bars = minute_bars()
    original = bars.copy(deep=True)
    result = build_labels(bars, horizon)
    row = result.frame.iloc[20]
    assert row.target_return == pytest.approx(np.log(bars.close.iloc[20 + horizon] / bars.close.iloc[20]))
    assert row.target_start_time == bars.open_time.iloc[20] + pd.Timedelta(minutes=1)
    assert row.target_end_time == row.target_start_time + pd.Timedelta(minutes=horizon)
    assert row.label_available_time == row.target_end_time + pd.Timedelta(seconds=1)
    assert result.frame.valid_label.iloc[:-horizon].all()
    assert not result.frame.valid_label.iloc[-horizon:].any()
    assert result.frame.target_return.iloc[-horizon:].isna().all()
    assert result.frame.label_exclusion_reason.iloc[-horizon:].eq("insufficient_future_data").all()
    pd.testing.assert_frame_equal(bars, original, check_exact=True)


def test_strict_maturity_boundary_and_explicit_filter(minute_bars):
    labels = build_labels(minute_bars(), 5).frame
    index = labels.index[10]
    available = labels.loc[index, "label_available_time"]
    with pytest.raises(LabelAvailabilityError, match="immature"):
        mature_labels(labels, available - pd.Timedelta(nanoseconds=1), indices=[index])
    mature = mature_labels(labels, available, indices=[index])
    assert mature.loc[index] == labels.loc[index, "target_return"]
    filtered = select_mature_labels(labels, available)
    assert filtered.index.equals(labels.index[:11])
    with pytest.raises(LabelAvailabilityError, match="immature"):
        mature_labels(labels, available, indices=[index, labels.index[11]])
    with pytest.raises(LabelAvailabilityError, match="invalid"):
        mature_labels(labels, labels.label_available_time.max())


def test_any_missing_intermediate_bar_invalidates_horizon(minute_bars):
    bars = minute_bars().drop(index=50)
    labels = build_labels(bars, 5).frame
    # Both prices at 00:48 and 00:53 exist; missing 00:50 still invalidates it.
    index = pd.Timestamp("2026-09-01T00:48:00Z")
    assert index in labels.index and index + pd.Timedelta(minutes=5) in labels.index
    assert not labels.loc[index, "valid_label"]
    assert labels.loc[index, "label_exclusion_reason"] == "gap_in_label_horizon"
    assert labels.loc[pd.Timestamp("2026-09-01T00:44:00Z"), "valid_label"]
    assert labels.loc[pd.Timestamp("2026-09-01T00:51:00Z"), "valid_label"]


def test_changes_after_target_endpoint_do_not_change_label(minute_bars):
    bars = minute_bars()
    changed = bars.copy(deep=True)
    for column in ("open", "high", "low", "close"):
        changed.loc[36:, column] *= 5
    baseline = build_labels(bars, 15).frame
    modified = build_labels(changed, 15).frame
    # The label for t=20 consumes t=35 close, but no t>35 data.
    pd.testing.assert_frame_equal(baseline.iloc[:21], modified.iloc[:21], check_exact=True)
    assert not baseline.target_return.iloc[21:36].equals(modified.target_return.iloc[21:36])


def test_append_only_matures_previous_tail_without_changing_prior_valid_labels(minute_bars):
    full = minute_bars()
    before = build_labels(full.iloc[:100], 15).frame
    after = build_labels(full, 15).frame
    pd.testing.assert_frame_equal(before.iloc[:85], after.iloc[:85], check_exact=True)
    assert not before.valid_label.iloc[85:].any()
    assert after.valid_label.iloc[85:100].all()


@pytest.mark.parametrize("horizon", [0, -1, 1.5, True])
def test_invalid_horizons_rejected(minute_bars, horizon):
    with pytest.raises(TemporalValidationError, match="positive integer"):
        build_labels(minute_bars(), horizon)


def test_naive_fit_time_and_inconsistent_label_metadata_rejected(minute_bars):
    labels = build_labels(minute_bars(), 5).frame
    index = labels.index[0]
    with pytest.raises(TemporalValidationError, match="timezone-aware"):
        mature_labels(labels, "2026-09-02", indices=[index])
    broken = labels.copy(deep=True)
    broken.loc[index, "label_available_time"] = broken.loc[index, "target_end_time"] - pd.Timedelta(seconds=1)
    with pytest.raises(LabelAvailabilityError, match="inconsistent"):
        mature_labels(broken, pd.Timestamp("2026-09-02T00:00:00Z"), indices=[index])


def test_maturity_guard_catches_deliberate_future_label_selection(minute_bars):
    labels = build_labels(minute_bars(), 60).frame
    first_decision = labels.target_start_time.iloc[0] + pd.Timedelta(seconds=1)
    # A malicious consumer that requests its just-created target must fail.
    with pytest.raises(LabelAvailabilityError, match="immature"):
        mature_labels(labels, first_decision, indices=[labels.index[0]])
