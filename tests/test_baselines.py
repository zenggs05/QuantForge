"""Baseline preprocessing must be learned only from explicit training arrays."""

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from quantforge.models.baselines import Baseline, regression_metrics


def training():
    index = pd.date_range("2026-09-01", periods=5, freq="min", tz="UTC")
    X = pd.DataFrame({"return_1": [-2.0, -1, 0, 1, 2], "volume": [1.0, 2, 3, 4, 5]}, index=index)
    return X, pd.Series([-.02, -.01, 0, .01, .02], index=index)


@pytest.mark.parametrize("name", ["zero", "historical_mean", "ridge"])
def test_baselines_are_deterministic_and_report_configuration(name):
    X, y = training()
    first = Baseline(name).fit(X, y)
    second = Baseline(name).fit(X, y)
    np.testing.assert_array_equal(first.predict(X), second.predict(X))
    assert first.metadata()["fitted"] is True
    assert first.metadata()["train_samples"] == len(X)
    assert first.metadata()["scikit_learn_version"]
    if name == "zero":
        np.testing.assert_array_equal(first.predict(X), np.zeros(len(X)))
    elif name == "historical_mean":
        np.testing.assert_array_equal(first.predict(X), np.full(len(X), y.mean()))


def test_ridge_scaler_is_train_only_and_prediction_does_not_refit():
    X, y = training()
    model = Baseline("ridge").fit(X, y)
    scaler = model.pipeline.named_steps["scaler"]
    expected = X.mean().to_numpy()
    np.testing.assert_array_equal(scaler.mean_, expected)
    extreme_future = X + 1_000_000
    model.predict(extreme_future)
    np.testing.assert_array_equal(scaler.mean_, expected)
    assert scaler.n_samples_seen_ == len(X)
    assert not np.allclose(scaler.mean_, pd.concat([X, extreme_future]).mean().to_numpy())


def assert_train_only_scaler(pipeline, X_train):
    """An independent moment oracle detects an intentionally leaky control."""
    scaler = pipeline.named_steps["scaler"]
    np.testing.assert_allclose(scaler.mean_, X_train.to_numpy().mean(axis=0), rtol=0, atol=1e-12)
    np.testing.assert_allclose(scaler.var_, X_train.to_numpy().var(axis=0), rtol=0, atol=1e-12)
    assert scaler.n_samples_seen_ == len(X_train)


def test_intentionally_leaky_full_data_scaler_is_caught_by_oracle():
    X_train, y_train = training()
    X_test = X_train.copy() + 5000.0
    X_test.index += pd.Timedelta(hours=1)
    valid = Baseline("ridge").fit(X_train, y_train)
    assert_train_only_scaler(valid.pipeline, X_train)
    leaky = Pipeline([("scaler", StandardScaler()), ("model", Ridge())])
    combined = pd.concat([X_train, X_test])
    leaky.fit(combined, np.concatenate([y_train.to_numpy(), np.full(len(X_test), -99.0)]))
    with pytest.raises(AssertionError):
        assert_train_only_scaler(leaky, X_train)


def test_ridge_learns_known_linear_signal_on_independent_synthetic_test_data():
    rng = np.random.default_rng(20261102)
    index = pd.date_range("2026-09-01", periods=600, freq="min", tz="UTC")
    features = rng.normal(size=(600, 3))
    coefficients = np.array([0.8, -0.4, 0.2])
    target = features @ coefficients + rng.normal(scale=0.01, size=600)
    X = pd.DataFrame(features, index=index, columns=["x1", "x2", "x3"])
    y = pd.Series(target, index=index)
    X_train, y_train = X.iloc[:400], y.iloc[:400]
    X_test, y_test = X.iloc[400:], y.iloc[400:]
    assert X_train.index.intersection(X_test.index).empty
    learned = Baseline("ridge", ridge_alpha=0.1).fit(X_train, y_train)
    zero = Baseline("zero").fit(X_train, y_train)
    learned_metrics = regression_metrics(y_test, learned.predict(X_test))
    zero_metrics = regression_metrics(y_test, zero.predict(X_test))
    assert learned_metrics["mse"] < 0.001
    assert learned_metrics["mse"] < zero_metrics["mse"] * 0.01
    assert learned_metrics["pearson_ic"] > 0.99
    assert_train_only_scaler(learned.pipeline, X_train)


def test_independent_noise_metrics_do_not_claim_significance_or_trading_performance():
    rng = np.random.default_rng(7321)
    X = pd.DataFrame(rng.normal(size=(400, 3)), columns=["x1", "x2", "x3"])
    y = pd.Series(rng.normal(size=400))
    trained = Baseline("ridge").fit(X.iloc[:250], y.iloc[:250])
    metrics = regression_metrics(y.iloc[250:], trained.predict(X.iloc[250:]))
    assert metrics["n_samples"] == 150
    assert np.isfinite(metrics["mse"])
    assert metrics["pearson_ic"] is None or -1.0 <= metrics["pearson_ic"] <= 1.0
    assert not {"p_value", "pvalue", "significant", "alpha_detected", "net_return"}.intersection(metrics)
    assert "no significance or trading-performance claim" in metrics["interpretation"]
    zero_metrics = regression_metrics(y.iloc[250:], np.zeros(150))
    assert zero_metrics["pearson_ic"] is None
    assert zero_metrics["pearson_ic_null_reason"] == "constant_prediction"


def test_historical_mean_does_not_use_evaluation_targets():
    X, y = training()
    model = Baseline("historical_mean").fit(X, y + .1)
    prediction = model.predict(X + 10000)
    np.testing.assert_allclose(prediction, .1)


def test_alignment_nonfinite_and_schema_errors_are_rejected():
    X, y = training()
    with pytest.raises(ValueError, match="index"):
        Baseline("ridge").fit(X, y.iloc[::-1])
    with pytest.raises(ValueError, match="finite"):
        Baseline("ridge").fit(X.assign(volume=float("inf")), y)
    model = Baseline("ridge").fit(X, y)
    with pytest.raises(ValueError, match="order"):
        model.predict(X[["volume", "return_1"]])
    with pytest.raises(ValueError, match="before"):
        Baseline("ridge").predict(X)


def test_prediction_metrics_match_hand_computation():
    actual = regression_metrics([1.0, 2.0, 3.0], [2.0, 3.0, 4.0])
    assert actual["mse"] == 1.0
    assert actual["mae"] == 1.0
    assert actual["pearson_ic"] == pytest.approx(1.0)
    assert actual["pearson_ic_null_reason"] is None
    assert "no significance" in actual["interpretation"]


@pytest.mark.parametrize("truth,prediction,reason", [([1, 2], [0, 0], "constant_prediction"), ([1, 1], [1, 2], "constant_target"), ([1], [2], "fewer_than_two_observations")])
def test_undefined_ic_is_null_with_reason(truth, prediction, reason):
    result = regression_metrics(truth, prediction)
    assert result["pearson_ic"] is None
    assert result["pearson_ic_null_reason"] == reason


def test_metrics_reject_misaligned_or_nonfinite_arrays():
    with pytest.raises(ValueError):
        regression_metrics([1, 2], [1])
    with pytest.raises(ValueError):
        regression_metrics([1, float("nan")], [1, 2])
    with pytest.raises(ValueError, match="indexes"):
        regression_metrics(pd.Series([1, 2], index=[0, 1]), pd.Series([1, 2], index=[1, 0]))
