"""Synthetic calendar-window contracts; no market data or network access."""

import numpy as np
import pandas as pd
import pytest
from threadpoolctl import threadpool_limits

from quantforge.datasets.research_splits import make_research_splits
from quantforge.features import FEATURE_COLUMNS, build_features
from quantforge.labels import build_labels, mature_labels
from quantforge.models.baselines import Baseline


def synthetic_inputs(minute_bars, horizon):
    """Causal features across sparse synthetic development-month segments."""
    starts = ["2025-09-01T00:00:00Z"]
    starts += [str(value - pd.Timedelta(hours=2)) for value in
               pd.date_range("2025-10-01", "2026-06-01", freq="MS", tz="UTC")]
    starts += ["2026-06-30T20:00:00Z"]
    bars = pd.concat([minute_bars(240, start=start) for start in starts], ignore_index=True)
    features, labels = build_features(bars), build_labels(bars, horizon)
    samples = features.metadata.join(labels.frame.drop(columns="target_return"))
    return features, labels, samples


@pytest.mark.parametrize("horizon", [15, 5])
def test_march_6m_exact_indices_labels_scaler_coefficients_and_predictions(minute_bars, horizon):
    """The first Phase 2D-A test: changed code must preserve March equivalence."""
    from quantforge.datasets.window_splits import make_window_splits

    features, labels, samples = synthetic_inputs(minute_bars, horizon)
    original = make_research_splits(samples, horizon)[0]
    windows = make_window_splits(samples, horizon)
    folds = [original, windows["expanding"][0], windows["rolling_6m"][0]]
    states, predictions = [], []
    with threadpool_limits(limits=1):
        for fold in folds:
            pd.testing.assert_index_equal(fold.train_indices, original.train_indices, exact=True)
            pd.testing.assert_index_equal(fold.eval_indices, original.eval_indices, exact=True)
            X = features.frame.loc[fold.train_indices]
            y = mature_labels(labels.frame, fold.fit_time, fold.train_indices)
            pd.testing.assert_series_equal(y, labels.frame.loc[original.train_indices, "target_return"], check_exact=True)
            assert list(X.columns) == list(FEATURE_COLUMNS)
            model = Baseline("ridge", ridge_alpha=1.0, random_seed=42).fit(X, y)
            scaler, estimator = model.pipeline["scaler"], model.pipeline["model"]
            states.append((scaler.mean_, scaler.var_, scaler.scale_, scaler.n_samples_seen_,
                           estimator.coef_, estimator.intercept_))
            predictions.append(model.predict(features.frame.loc[fold.eval_indices]))
    for state, prediction in zip(states[1:], predictions[1:], strict=True):
        for actual, expected in zip(state, states[0], strict=True):
            np.testing.assert_array_equal(actual, expected)
        np.testing.assert_array_equal(prediction, predictions[0])


@pytest.mark.parametrize("fit,months,start,days", [
    ("2024-03-01T00:00Z", 1, "2024-02-01T00:00Z", 29),
    ("2025-03-01T00:00Z", 1, "2025-02-01T00:00Z", 28),
    ("2026-01-01T00:00Z", 1, "2025-12-01T00:00Z", 31),
    ("2026-03-01T00:00Z", 3, "2025-12-01T00:00Z", 90),
    ("2026-03-01T00:00Z", 6, "2025-09-01T00:00Z", 181),
])
def test_calendar_arithmetic_including_leap_year(fit, months, start, days):
    from quantforge.datasets.window_splits import calendar_window_start

    actual = calendar_window_start(fit, months)
    assert actual == pd.Timestamp(start)
    assert (pd.Timestamp(fit) - actual).days == days


@pytest.mark.parametrize("months", [0, -1, 2, 4, 12, True, 1.0, "3"])
def test_undeclared_window_rejected(months):
    from quantforge.datasets.window_splits import calendar_window_start

    with pytest.raises(ValueError, match="frozen"):
        calendar_window_start("2026-03-01T00:00Z", months)


@pytest.mark.parametrize("fit", ["2026-03-01", "2026-03-01T00:00:01Z", "2026-03-02T00:00Z", pd.NaT])
def test_fit_requires_explicit_month_start_midnight(fit):
    from quantforge.datasets.window_splits import calendar_window_start

    with pytest.raises(ValueError):
        calendar_window_start(fit, 1)


def test_expanding_cannot_start_before_development():
    from quantforge.datasets.window_splits import calendar_window_start

    with pytest.raises(ValueError, match="follow"):
        calendar_window_start("2025-09-01T00:00Z")


@pytest.mark.parametrize("horizon", [15, 5])
def test_all_windows_are_ordered_legal_subsets_with_identical_eval_times(minute_bars, horizon):
    from quantforge.datasets.window_splits import make_window_splits

    _, _, samples = synthetic_inputs(minute_bars, horizon)
    originals = make_research_splits(samples, horizon)
    windows = make_window_splits(samples, horizon)
    expected_starts = {
        "expanding": ["2025-09"] * 4,
        "rolling_1m": ["2026-02", "2026-03", "2026-04", "2026-05"],
        "rolling_3m": ["2025-12", "2026-01", "2026-02", "2026-03"],
        "rolling_6m": ["2025-09", "2025-10", "2025-11", "2025-12"],
    }
    assert list(windows) == list(expected_starts)
    for name, splits in windows.items():
        assert len(splits) == 4
        for number, (split, original) in enumerate(zip(splits, originals, strict=True)):
            start = pd.Timestamp(expected_starts[name][number] + "-01T00:00Z")
            training = samples.loc[split.train_indices]
            pd.testing.assert_index_equal(split.eval_indices, original.eval_indices, exact=True)
            assert split.train_indices.isin(original.train_indices).all()
            assert split.train_indices.is_monotonic_increasing
            assert training.decision_time.ge(start).all()
            assert training.decision_time.lt(split.fit_time).all()
            assert training.feature_available_time.le(split.fit_time).all()
            assert training.label_available_time.le(split.fit_time).all()
            assert training.target_end_time.le(samples.loc[split.eval_indices].target_start_time.iloc[0]).all()
            assert training.valid_features.all() and training.valid_label.all()
            assert split.audit["train_start"] == start.isoformat()
            assert split.audit["train_end_exclusive"] == split.fit_time.isoformat()
            assert split.audit["train_samples"] == len(training)
            assert split.audit["eval_samples"] == len(original.eval_indices)
            assert split.audit["train_removed_total"] == split.audit["candidate_train_samples"] - len(training)
            assert split.audit["window"] == name and split.audit["expanding_subset"]
            assert split.audit["eval_indices_sha256"] == windows["expanding"][number].audit["eval_indices_sha256"]


@pytest.mark.parametrize("horizon", [15, 5])
def test_month_start_one_second_decision_and_maturity_boundaries(minute_bars, horizon):
    from quantforge.datasets.window_splits import make_window_splits

    _, _, samples = synthetic_inputs(minute_bars, horizon)
    split = make_window_splits(samples, horizon)["rolling_1m"][0]
    # January's final bar is eligible for the February decision-time window.
    assert pd.Timestamp("2026-01-31T23:59Z") in split.train_indices
    assert pd.Timestamp("2026-01-31T23:58Z") not in split.train_indices
    # February's final bar is a March decision, never February training.
    assert pd.Timestamp("2026-02-28T23:59Z") in split.eval_indices
    assert pd.Timestamp("2026-02-28T23:59Z") not in split.train_indices
    # A label ending exactly at fit_time remains unavailable for one second.
    last_immature = split.fit_time - pd.Timedelta(minutes=horizon + 1)
    assert samples.loc[last_immature, "target_end_time"] == split.fit_time
    assert samples.loc[last_immature, "label_available_time"] == split.fit_time + pd.Timedelta(seconds=1)
    assert last_immature not in split.train_indices
    assert last_immature - pd.Timedelta(minutes=1) in split.train_indices
    assert split.audit["train_immature_labels"] == horizon


def test_gap_and_warmup_exclusions_preserved_without_truncating_feature_history(minute_bars):
    from quantforge.datasets.window_splits import make_window_splits

    features, labels, samples = synthetic_inputs(minute_bars, 15)
    windows = make_window_splits(samples, 15)
    invalid = samples.index[~samples.valid_features | ~samples.valid_label]
    assert features.diagnostics["gap_count"] > 0
    assert labels.diagnostics["exclusion_counts"]["gap_in_label_horizon"] > 0
    for splits in windows.values():
        for split in splits:
            assert split.train_indices.intersection(invalid).empty
            assert split.eval_indices.intersection(invalid).empty
    # Prior-January observed features remain legal in the February window;
    # resetting a 60-bar warm-up at the rolling boundary would lose this row.
    split = windows["rolling_1m"][0]
    first = pd.Timestamp("2026-01-31T23:59Z")
    assert first in split.train_indices
    assert features.frame.loc[first].notna().all()
    assert split.audit["train_invalid_samples"] > 0
    assert split.audit["train_invalid_samples"] <= (
        split.audit["train_invalid_features"] + split.audit["train_invalid_labels"])


def test_empty_rolling_training_fails_instead_of_dropping_evaluation_rows(minute_bars):
    from quantforge.datasets.window_splits import make_window_splits

    _, _, samples = synthetic_inputs(minute_bars, 15)
    in_february = samples.decision_time.ge(pd.Timestamp("2026-02-01T00:00Z")) & samples.decision_time.lt(pd.Timestamp("2026-03-01T00:00Z"))
    samples.loc[in_february, "valid_features"] = False
    assert len(make_research_splits(samples, 15)[0].train_indices) > 0
    with pytest.raises(ValueError, match="No legal training samples for rolling_1m"):
        make_window_splits(samples, 15)


def test_no_input_mutation_or_expanding_audit_aliasing(minute_bars):
    from quantforge.datasets.window_splits import make_window_splits

    _, _, samples = synthetic_inputs(minute_bars, 15)
    original = samples.copy(deep=True)
    windows = make_window_splits(samples, 15)
    pd.testing.assert_frame_equal(samples, original, check_exact=True)
    windows["rolling_1m"][0].audit["train_samples"] = -1
    assert windows["expanding"][0].audit["train_samples"] > 0
    assert "window" not in make_research_splits(samples, 15)[0].audit


def test_window_hashes_do_not_depend_on_datetime_storage_precision(minute_bars):
    from quantforge.datasets.window_splits import make_window_splits

    _, _, samples = synthetic_inputs(minute_bars, 15)
    microseconds = samples.copy()
    microseconds.index = microseconds.index.as_unit("us")
    for name in microseconds:
        if isinstance(microseconds[name].dtype, pd.DatetimeTZDtype):
            microseconds[name] = microseconds[name].dt.as_unit("us")
    original, converted = make_window_splits(samples, 15), make_window_splits(microseconds, 15)
    for name in original:
        for left, right in zip(original[name], converted[name], strict=True):
            assert left.audit == right.audit


@pytest.mark.parametrize("corruption", ["decision", "feature_delay", "label_delay", "future_index", "duplicate", "unsorted"])
def test_original_temporal_validation_is_retained(minute_bars, corruption):
    from quantforge.datasets.window_splits import make_window_splits

    _, _, samples = synthetic_inputs(minute_bars, 15)
    if corruption == "decision":
        samples.loc[samples.index[0], "decision_time"] += pd.Timedelta(seconds=1)
    elif corruption == "feature_delay":
        samples["feature_available_time"] -= pd.Timedelta(seconds=1)
    elif corruption == "label_delay":
        samples["label_available_time"] -= pd.Timedelta(seconds=1)
    elif corruption == "future_index":
        # Synthetic metadata only, no final-holdout file or payload is accessed.
        extra = samples.iloc[[-1]].copy()
        extra.index = pd.DatetimeIndex([pd.Timestamp("2026-07-01T00:00Z")])
        samples = pd.concat([samples, extra])
    elif corruption == "duplicate":
        samples = pd.concat([samples, samples.iloc[[-1]]])
    else:
        samples = samples.iloc[::-1]
    with pytest.raises(ValueError):
        make_window_splits(samples, 15)
