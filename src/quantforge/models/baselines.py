"""Train-only baseline estimators and descriptive prediction metrics."""

from __future__ import annotations

import numpy as np
import pandas as pd
import sklearn
from sklearn.dummy import DummyRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


class Baseline:
    """Fit zero, training-mean or standardized Ridge on explicitly supplied rows.

    The caller must obtain those rows from an audited temporal split. This class
    never combines training and evaluation arrays or tunes hyperparameters.
    """

    def __init__(self, name: str, ridge_alpha: float = 1.0, random_seed: int = 42):
        if name not in {"zero", "historical_mean", "ridge"}:
            raise ValueError("Baseline name must be zero, historical_mean or ridge")
        if not np.isfinite(ridge_alpha) or ridge_alpha <= 0:
            raise ValueError("ridge_alpha must be finite and positive")
        if isinstance(random_seed, bool) or not isinstance(random_seed, int) or random_seed < 0:
            raise ValueError("random_seed must be a nonnegative integer")
        self.name = name
        self.ridge_alpha = float(ridge_alpha)
        self.random_seed = random_seed
        self.pipeline: Pipeline | None = None
        self.feature_names_: list[str] | None = None
        self.n_train_samples_: int | None = None

    @staticmethod
    def _validate_features(X: pd.DataFrame) -> None:
        if not isinstance(X, pd.DataFrame) or X.empty or X.columns.has_duplicates:
            raise ValueError("Features must be a nonempty DataFrame with unique columns")
        if not all(isinstance(column, str) for column in X.columns):
            raise ValueError("Feature column names must be strings")
        if not all(pd.api.types.is_numeric_dtype(dtype) for dtype in X.dtypes):
            raise ValueError("Feature columns must be numeric")
        if not np.isfinite(X.to_numpy(dtype="float64")).all():
            raise ValueError("Feature values must be finite; exclude warm-up rows explicitly")

    def fit(self, X: pd.DataFrame, y: pd.Series) -> Baseline:
        self._validate_features(X)
        if not isinstance(y, pd.Series) or not y.index.equals(X.index):
            raise ValueError("Target must be a Series with exactly the training feature index")
        target = y.to_numpy(dtype="float64")
        if not np.isfinite(target).all():
            raise ValueError("Training targets must be finite and mature")
        if self.name == "ridge":
            estimator = Ridge(alpha=self.ridge_alpha, solver="svd", random_state=self.random_seed)
            pipeline = Pipeline([("scaler", StandardScaler()), ("model", estimator)])
        else:
            estimator = DummyRegressor(strategy="constant", constant=0.0) if self.name == "zero" else DummyRegressor(strategy="mean")
            pipeline = Pipeline([("model", estimator)])
        pipeline.fit(X, target)
        self.pipeline = pipeline
        self.feature_names_ = list(X.columns)
        self.n_train_samples_ = len(X)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if self.pipeline is None:
            raise ValueError("Fit the baseline before prediction")
        self._validate_features(X)
        if list(X.columns) != self.feature_names_:
            raise ValueError("Prediction feature names and order must match training")
        predicted = np.asarray(self.pipeline.predict(X), dtype="float64")
        if predicted.ndim != 1 or len(predicted) != len(X) or not np.isfinite(predicted).all():
            raise ValueError("Model produced invalid predictions")
        return predicted

    def metadata(self) -> dict:
        return {
            "model_contract_version": 1,
            "name": self.name, "ridge_alpha": self.ridge_alpha if self.name == "ridge" else None,
            "random_seed": self.random_seed, "scikit_learn_version": sklearn.__version__,
            "numpy_version": np.__version__, "fitted": self.pipeline is not None,
            "train_samples": self.n_train_samples_, "feature_names": self.feature_names_,
            "preprocessing": "StandardScaler fitted on supplied training rows" if self.name == "ridge" else "none",
            "solver": "svd" if self.name == "ridge" else None,
            "hyperparameter_selection": "fixed configuration; no tuning",
        }


def regression_metrics(y_true, y_pred) -> dict:
    """Descriptive pointwise error and Pearson time-series IC, without p-values."""
    if isinstance(y_true, pd.Series) and isinstance(y_pred, pd.Series) and not y_true.index.equals(y_pred.index):
        raise ValueError("Metric target/prediction Series indexes must match")
    truth = np.asarray(y_true, dtype="float64")
    predicted = np.asarray(y_pred, dtype="float64")
    if truth.ndim != 1 or predicted.ndim != 1 or len(truth) != len(predicted) or not len(truth):
        raise ValueError("Metrics require nonempty, aligned one-dimensional arrays")
    if not np.isfinite(truth).all() or not np.isfinite(predicted).all():
        raise ValueError("Metric inputs must contain only finite values")
    error = predicted - truth
    mse = float(np.mean(np.square(error)))
    mae = float(np.mean(np.abs(error)))
    if not np.isfinite(mse) or not np.isfinite(mae):
        raise ValueError("Metric arithmetic overflowed")
    reason = None
    if len(truth) < 2:
        reason = "fewer_than_two_observations"
    elif np.ptp(truth) == 0:
        reason = "constant_target"
    elif np.ptp(predicted) == 0:
        reason = "constant_prediction"
    ic = None if reason is not None else float(np.corrcoef(truth, predicted)[0, 1])
    if ic is not None and not np.isfinite(ic):
        ic, reason = None, "numerically_undefined_correlation"
    return {
        "n_samples": len(truth), "mse": mse, "mae": mae,
        "pearson_ic": ic, "pearson_ic_null_reason": reason,
        "interpretation": "Descriptive time-series prediction metrics; no significance or trading-performance claim.",
    }
