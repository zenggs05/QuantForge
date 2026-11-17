"""Adversarial checks for elapsed-time splits and delayed target access."""

import pandas as pd
import pytest

from quantforge.datasets.splits import SplitConfig, make_walk_forward_splits


def metadata(n=80, horizon=5, delay_seconds=1):
    index = pd.date_range("2026-09-01", periods=n, freq="min", tz="UTC", name="bar_open_time")
    end = index + pd.Timedelta(minutes=1)
    return pd.DataFrame({
        "bar_end_exclusive": end,
        "feature_available_time": end + pd.Timedelta(seconds=delay_seconds),
        "decision_time": end + pd.Timedelta(seconds=delay_seconds),
        "target_start_time": end,
        "target_end_time": end + pd.Timedelta(minutes=horizon),
        "label_available_time": end + pd.Timedelta(minutes=horizon, seconds=delay_seconds),
        "valid_features": True, "valid_label": True,
    }, index=index)


def config(**kwargs):
    return SplitConfig(**({"train_minutes": 30, "eval_minutes": 20, "step_minutes": 20, "rolling_train_minutes": 30, "min_train_samples": 5, "min_eval_samples": 5} | kwargs))


@pytest.mark.parametrize("mode", ["chronological", "expanding", "rolling"])
def test_temporal_splits_exclude_future_and_overlapping_labels(mode):
    samples = metadata()
    splits = make_walk_forward_splits(samples, config(mode=mode))
    assert len(splits) == (1 if mode == "chronological" else 3)
    all_eval = pd.DatetimeIndex([], tz="UTC")
    for fold in splits:
        training = samples.loc[fold.train_indices]
        evaluation = samples.loc[fold.eval_indices]
        assert (training.label_available_time <= fold.fit_time).all()
        assert (training.target_end_time <= evaluation.target_start_time.iloc[0]).all()
        assert (training.decision_time < fold.fit_time).all()
        assert fold.train_indices.intersection(fold.eval_indices).empty
        assert fold.eval_indices.intersection(all_eval).empty
        all_eval = all_eval.append(fold.eval_indices)
    first = splits[0]
    assert first.train_indices[-1] == samples.index[25]
    assert first.diagnostics["overlapping_target_samples"] == 4
    if mode != "chronological":
        assert splits[-1].diagnostics["eval_window_complete"] is False
        assert splits[-1].diagnostics["eval_samples"] == 10
    if mode == "rolling":
        assert splits[1].train_indices[0] == samples.index[20]
    elif mode == "expanding":
        assert splits[1].train_indices[0] == samples.index[0]


def test_label_maturity_is_checked_independently_of_interval_overlap():
    samples = metadata()
    samples.loc[samples.index[10], "label_available_time"] = samples.decision_time.iloc[40]
    first = make_walk_forward_splits(samples, config(mode="chronological"))[0]
    assert samples.index[10] not in first.train_indices
    assert first.diagnostics["immature_label_samples"] == 5


def test_embargo_extends_purge_by_elapsed_time():
    samples = metadata()
    first = make_walk_forward_splits(samples, config(mode="chronological", embargo_minutes=3))[0]
    assert first.train_indices[-1] == samples.index[22]
    assert first.diagnostics["embargo_excluded_samples"] == 3


def test_missing_rows_do_not_shift_calendar_boundaries():
    samples = metadata().drop(metadata().index[[3, 8, 9, 36]])
    first = make_walk_forward_splits(samples, config(mode="chronological"))[0]
    assert first.eval_start == pd.Timestamp("2026-09-01T00:30Z")
    assert len(first.eval_indices) == 19
    assert first.diagnostics["eval_window_complete"] is False


@pytest.mark.parametrize("failure", ["unsorted", "duplicate", "naive", "future_feature", "early_label", "invalid_row", "bad_bar_end"])
def test_bad_metadata_is_rejected(failure):
    samples = metadata()
    if failure == "unsorted":
        samples = samples.iloc[::-1]
    elif failure == "duplicate":
        samples = pd.concat([samples, samples.iloc[[-1]]])
    elif failure == "naive":
        samples.index = samples.index.tz_localize(None)
    elif failure == "future_feature":
        samples.loc[samples.index[0], "feature_available_time"] += pd.Timedelta(seconds=2)
    elif failure == "early_label":
        samples.loc[samples.index[0], "label_available_time"] = samples.target_start_time.iloc[0]
    elif failure == "invalid_row":
        samples.loc[samples.index[0], "valid_features"] = False
    elif failure == "bad_bar_end":
        samples.loc[samples.index[0], "bar_end_exclusive"] += pd.Timedelta(minutes=1)
    with pytest.raises(ValueError):
        make_walk_forward_splits(samples, config())


def test_insufficient_training_and_overlapping_evaluation_rejected():
    with pytest.raises(ValueError, match="Insufficient eligible"):
        make_walk_forward_splits(metadata(), config(min_train_samples=30))
    with pytest.raises(ValueError, match="step_minutes"):
        config(step_minutes=10)
    with pytest.raises(ValueError, match="No evaluation"):
        make_walk_forward_splits(metadata(n=20), config())
    with pytest.raises(ValueError, match="Insufficient evaluation"):
        make_walk_forward_splits(metadata(n=32), config())


def test_empty_calendar_evaluation_window_does_not_silently_disappear():
    samples = metadata()
    samples = samples.drop(samples.index[30:50])
    with pytest.raises(ValueError, match="Insufficient evaluation"):
        make_walk_forward_splits(samples, config())
