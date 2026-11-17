"""Month boundaries are fixed decision times, not observed sample offsets."""

import pandas as pd
import pytest

from quantforge.datasets.research_splits import make_research_splits
from quantforge.research_policy import DEVELOPMENT_END, DEVELOPMENT_START


def metadata(horizon=15):
    index = pd.date_range(DEVELOPMENT_START, DEVELOPMENT_END, freq="min", inclusive="left", name="bar_open_time")
    end = index + pd.Timedelta(minutes=1)
    result = pd.DataFrame({
        "bar_end_exclusive": end, "feature_available_time": end + pd.Timedelta(seconds=1),
        "decision_time": end + pd.Timedelta(seconds=1), "target_start_time": end,
        "target_end_time": end + pd.Timedelta(minutes=horizon),
        "label_available_time": end + pd.Timedelta(minutes=horizon, seconds=1),
        "valid_features": True, "valid_label": True,
    }, index=index)
    result.loc[index[:60], "valid_features"] = False
    result.loc[index[-horizon:], "valid_label"] = False
    return result


@pytest.mark.parametrize("horizon", [5, 15])
def test_four_fixed_folds_maturity_purge_roles_and_month_end_cutoffs(horizon):
    samples = metadata(horizon)
    folds = make_research_splits(samples, horizon)
    assert [fold.role for fold in folds] == ["validation", "validation", "development_evaluation", "development_evaluation"]
    eval_seen = pd.DatetimeIndex([], tz="UTC")
    for fold in folds:
        train, evaluation = samples.loc[fold.train_indices], samples.loc[fold.eval_indices]
        assert fold.fit_time == fold.eval_start == fold.eval_start.normalize().replace(day=1)
        assert (train.label_available_time <= fold.fit_time).all()
        assert (train.target_end_time <= fold.fit_time).all()
        assert (train.target_end_time <= evaluation.target_start_time.iloc[0]).all()
        assert (evaluation.decision_time >= fold.eval_start).all()
        assert (evaluation.decision_time < fold.eval_end).all()
        assert (evaluation.label_available_time <= fold.eval_end).all()
        assert fold.eval_indices.intersection(eval_seen).empty
        eval_seen = eval_seen.append(fold.eval_indices)
        assert fold.audit["boundary_basis"] == "decision_time"
        assert fold.audit["eval_labels_after_cutoff"] == horizon
        assert len(evaluation) == (fold.eval_end - fold.eval_start).days * 1440 - horizon
    assert folds[0].fit_time == pd.Timestamp("2026-03-01T00:00:00Z")
    assert folds[-1].eval_end == DEVELOPMENT_END
    # Final February bar belongs to March by decision time, not by bar-open.
    assert folds[0].eval_indices[0] == pd.Timestamp("2026-02-28T23:59:00Z")
    assert folds[0].eval_indices[0] not in folds[0].train_indices


def test_missing_month_start_does_not_delay_fit_or_expand_training_information():
    samples = metadata()
    march = pd.Timestamp("2026-03-01T00:00Z")
    remove = samples.decision_time.ge(march) & samples.decision_time.lt(march + pd.Timedelta(hours=2))
    samples = samples.loc[~remove]
    first = make_research_splits(samples, 15)[0]
    assert first.fit_time == march
    assert samples.loc[first.eval_indices, "decision_time"].iloc[0] > march + pd.Timedelta(hours=1)
    assert samples.loc[first.train_indices, "label_available_time"].max() <= march


def test_invalid_rows_are_excluded_and_audited_without_changing_boundaries():
    samples = metadata()
    feature_bad = pd.Timestamp("2026-03-10T12:00Z")
    label_bad = pd.Timestamp("2026-03-11T12:00Z")
    samples.loc[feature_bad, "valid_features"] = False
    samples.loc[label_bad, "valid_label"] = False
    first = make_research_splits(samples, 15)[0]
    assert feature_bad not in first.eval_indices and label_bad not in first.eval_indices
    assert first.audit["eval_invalid_features"] == first.audit["eval_invalid_labels"] == 1
    assert first.audit["train_invalid_features"] == 60


@pytest.mark.parametrize("failure", ["horizon", "cutoff", "decision", "delay", "label_delay", "naive", "duplicate", "outside"])
def test_research_contract_overrides_are_rejected(failure):
    samples = metadata()
    horizon, cutoff = 15, "month_end"
    if failure == "horizon":
        horizon = 60
    elif failure == "cutoff":
        cutoff = "latest_available"
    elif failure == "decision":
        samples.loc[samples.index[0], "decision_time"] += pd.Timedelta(seconds=1)
    elif failure == "delay":
        samples["feature_available_time"] += pd.Timedelta(seconds=1)
    elif failure == "label_delay":
        samples["label_available_time"] -= pd.Timedelta(seconds=1)
    elif failure == "naive":
        samples.index = samples.index.tz_localize(None)
    elif failure == "duplicate":
        samples = pd.concat([samples, samples.iloc[[-1]]])
    elif failure == "outside":
        # Only synthetic metadata; no final-holdout file is accessed.
        extra = samples.iloc[[-1]].copy()
        extra.index = pd.DatetimeIndex([DEVELOPMENT_END])
        samples = pd.concat([samples, extra])
    with pytest.raises(ValueError):
        make_research_splits(samples, horizon, eval_cutoff=cutoff)
