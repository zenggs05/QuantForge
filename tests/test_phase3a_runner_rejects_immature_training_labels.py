"""The historical runner must obtain training targets through the maturity gate."""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from quantforge import phase3a_runner as runner
from quantforge.datasets.research_splits import ResearchSplit
from quantforge.features import FeatureResult
from quantforge.labels import LabelAvailabilityError, mature_labels
from quantforge.phase3a_config import load_history_config


class _MaturityOnlyFrame:
    """Opaque spy input: only the installed maturity reader may unwrap it."""

    def __getattr__(self, name):
        raise AssertionError(f"Label frame accessed outside mature_labels: {name}")

    def __getitem__(self, key):
        raise AssertionError("Label frame accessed outside mature_labels: item")


@pytest.fixture
def small_fold():
    config = load_history_config()
    columns = config["specification"]["v1_features"]
    train = pd.date_range("2020-12-31T23:40:00Z", periods=3, freq="min")
    evaluation = pd.date_range("2021-01-01T00:00:00Z", periods=2, freq="min")
    index = train.append(evaluation)
    values = np.arange(len(index) * len(columns), dtype=np.float64).reshape(len(index), -1)
    values[len(train):] += 10000.0
    frame = pd.DataFrame(values, index=index, columns=columns)
    start = index + pd.Timedelta(minutes=1)
    end = start + pd.Timedelta(minutes=5)
    labels = pd.DataFrame({
        "target_return": [0.01, -0.02, 0.03, 0.04, -0.05],
        "target_start_time": start, "target_end_time": end,
        "label_available_time": end + pd.Timedelta(seconds=1), "valid_label": True,
    }, index=index)
    metadata = pd.DataFrame({
        "bar_end_exclusive": start,
        "feature_available_time": start + pd.Timedelta(seconds=1),
        "decision_time": start + pd.Timedelta(seconds=1), "valid_features": True,
    }, index=index)
    fit = pd.Timestamp("2021-01-01T00:00:00Z")
    fold = ResearchSplit(train, evaluation, fit, fit, pd.Timestamp("2021-02-01T00:00:00Z"),
                         "historical_rolling", {})
    return FeatureResult(frame, metadata, {}), labels, fold, columns, config


def test_reader_spy_requires_train_maturity_before_one_fit_and_truth_after_prediction(small_fold, monkeypatch):
    features, label_frame, fold, columns, config = small_fold
    opaque = _MaturityOnlyFrame()
    events, returned_targets = [], []
    original_fit, original_predict = runner.Baseline.fit, runner.Baseline.predict

    def guarded_reader(frame, *, as_of, indices):
        assert frame is opaque
        phase = "train_mature" if not returned_targets else "eval_mature"
        expected_indices = fold.train_indices if phase == "train_mature" else fold.eval_indices
        expected_as_of = fold.fit_time if phase == "train_mature" else fold.eval_end
        assert indices.equals(expected_indices)
        assert as_of == expected_as_of
        target = mature_labels(label_frame, as_of=as_of, indices=indices)
        events.append(phase)
        returned_targets.append(target)
        return target

    def fit_spy(model, X, y):
        assert events == ["train_mature"]
        assert y is returned_targets[0]
        pd.testing.assert_frame_equal(X, features.frame.loc[fold.train_indices, columns])
        events.append("fit")
        return original_fit(model, X, y)

    def predict_spy(model, X):
        assert events == ["train_mature", "fit"]
        pd.testing.assert_frame_equal(X, features.frame.loc[fold.eval_indices, columns])
        events.append("predict")
        return original_predict(model, X)

    monkeypatch.setattr(runner, "mature_labels", guarded_reader)
    monkeypatch.setattr(runner.Baseline, "fit", fit_spy)
    monkeypatch.setattr(runner.Baseline, "predict", predict_spy)
    prediction, truth, state = runner.fit_predict_fold(
        features, SimpleNamespace(frame=opaque), fold, columns, config)

    assert events == ["train_mature", "fit", "predict", "eval_mature"]
    assert truth is returned_targets[1]
    assert len(prediction) == len(fold.eval_indices)
    assert np.isfinite(prediction).all()
    train_X = features.frame.loc[fold.train_indices, columns]
    np.testing.assert_allclose(state["scaler_mean"], train_X.mean(), rtol=0, atol=0)
    np.testing.assert_allclose(state["scaler_variance"], train_X.var(ddof=0), rtol=1e-15)
    assert not np.allclose(state["scaler_mean"], features.frame.mean())
    assert state["train_samples"] == len(fold.train_indices)
    assert state["train_target_mean"] == pytest.approx(returned_targets[0].mean())


def test_any_immature_training_label_raises_before_fit(small_fold, monkeypatch):
    features, label_frame, fold, columns, config = small_fold
    label_frame["label_available_time"] = label_frame.label_available_time.dt.as_unit("ns")
    label_frame.loc[fold.train_indices[-1], "label_available_time"] = (
        fold.fit_time + pd.Timedelta(nanoseconds=1))
    reads, fits = [], []

    def reader(frame, *, as_of, indices):
        reads.append((as_of, indices.copy()))
        return mature_labels(frame, as_of=as_of, indices=indices)

    def forbidden_fit(*args, **kwargs):
        fits.append(True)
        pytest.fail("An immature training label reached Baseline.fit")

    monkeypatch.setattr(runner, "mature_labels", reader)
    monkeypatch.setattr(runner.Baseline, "fit", forbidden_fit)
    with pytest.raises(LabelAvailabilityError, match="immature"):
        runner.fit_predict_fold(features, SimpleNamespace(frame=label_frame), fold, columns, config)
    assert len(reads) == 1
    assert reads[0][0] == fold.fit_time
    assert reads[0][1].equals(fold.train_indices)
    assert fits == []


@pytest.mark.parametrize("bypass", ["loc", "target_return", "getitem"])
def test_maturity_spy_itself_rejects_direct_label_reads(bypass):
    opaque = _MaturityOnlyFrame()
    with pytest.raises(AssertionError, match="outside mature_labels"):
        if bypass == "getitem":
            opaque["target_return"]
        else:
            getattr(opaque, bypass)
