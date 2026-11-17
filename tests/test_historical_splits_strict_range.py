"""Strict public history boundaries and unchanged maturity/metadata semantics."""

import pandas as pd
import pytest

from quantforge.datasets.historical_splits import _make_splits_internal, make_historical_splits
from quantforge.datasets.research_splits import ResearchSplit, make_research_splits
from quantforge import research_policy as policy
from test_historical_splits_matches_research_splits_on_overlap import metadata


def sparse_history(horizon=15):
    # Month endpoints cover every month boundary without allocating full history.
    frames = []
    for month in policy.HISTORICAL_MONTHS:
        start, end = policy.historical_month_bounds(month)
        first = metadata(horizon, start, start + pd.Timedelta(minutes=90))
        last = metadata(horizon, end - pd.Timedelta(minutes=90), end)
        frames.append(pd.concat([first, last]))
    return pd.concat(frames)


def test_public_historical_splitter_rejects_legacy_scope():
    samples = metadata()
    with pytest.raises(policy.ResearchPolicyError, match="historical allowlist"):
        make_historical_splits(samples, policy.EVALUATION_MONTHS, 15)
    with pytest.raises(ValueError, match="supplied frozen bounds"):
        make_historical_splits(samples, policy.HISTORICAL_EVALUATION_MONTHS, 15)


@pytest.mark.parametrize("horizon", [15, 5])
def test_all_historical_folds_mature_month_end_and_roles(horizon):
    samples = sparse_history(horizon)
    folds = make_historical_splits(samples, policy.HISTORICAL_EVALUATION_MONTHS, horizon)
    assert len(folds) == 56
    assert all(isinstance(fold, ResearchSplit) and fold.role == "historical_rolling" for fold in folds)
    assert folds[-1].audit["role_bucket"] == "2025H2"
    for fold in folds:
        train, evaluation = samples.loc[fold.train_indices], samples.loc[fold.eval_indices]
        assert train.label_available_time.le(fold.fit_time).all()
        assert train.feature_available_time.le(fold.fit_time).all()
        assert train.target_end_time.le(evaluation.target_start_time.iloc[0]).all()
        assert evaluation.decision_time.ge(fold.eval_start).all()
        assert evaluation.decision_time.lt(fold.eval_end).all()
        assert evaluation.label_available_time.le(fold.eval_end).all()
    with pytest.raises(ValueError, match="frozen development data bounds"):
        make_research_splits(samples, horizon)


@pytest.mark.parametrize("failure", ["delay", "decision", "target", "label_delay", "bool", "duplicate", "naive", "horizon", "cutoff"])
def test_historical_splitter_rejects_contract_mutations(failure):
    samples = sparse_history()
    horizon, cutoff = 15, "month_end"
    if failure == "delay":
        samples["feature_available_time"] += pd.Timedelta(seconds=1)
    elif failure == "decision":
        samples["decision_time"] += pd.Timedelta(seconds=1)
    elif failure == "target":
        samples["target_end_time"] += pd.Timedelta(minutes=1)
    elif failure == "label_delay":
        samples["label_available_time"] -= pd.Timedelta(seconds=1)
    elif failure == "bool":
        samples["valid_label"] = 1
    elif failure == "duplicate":
        samples = pd.concat([samples, samples.iloc[[-1]]])
    elif failure == "naive":
        samples.index = samples.index.tz_localize(None)
    elif failure == "horizon":
        horizon = 1
    elif failure == "cutoff":
        cutoff = "latest_available"
    with pytest.raises(ValueError):
        make_historical_splits(samples, policy.HISTORICAL_EVALUATION_MONTHS, horizon, cutoff)


@pytest.mark.parametrize("months", [("2021-02", "2021-01"), ("2021-01", "2021-01"), (), ["2021-01"]])
def test_evaluation_month_sequence_must_be_unique_sorted_tuple(months):
    with pytest.raises(ValueError):
        make_historical_splits(sparse_history(), months, 15)
