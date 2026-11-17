"""Initial-Train-only specification diagnostics; never evaluation forecasts."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from quantforge.datasets.research_dataset import load_research_dataset
from quantforge.datasets.research_splits import make_research_splits
from quantforge.features import build_features
from quantforge.labels import build_labels, mature_labels
from quantforge.research_policy import DEVELOPMENT_END, DEVELOPMENT_START, validate_development_bounds

EVIDENCE = "training_period_diagnostic"


def _frame(X: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(X, pd.DataFrame) or X.empty or X.columns.has_duplicates:
        raise ValueError("Features must be a nonempty DataFrame with unique columns")
    if (not isinstance(X.index, pd.DatetimeIndex) or X.index.tz is None or X.index.hasnans
            or not X.index.is_unique or not X.index.is_monotonic_increasing):
        raise ValueError("Feature index must be ordered unique UTC timestamps")
    if str(X.index.tz) != "UTC" or not np.isfinite(X.to_numpy(dtype=np.float64)).all():
        raise ValueError("Features must be finite float64 with UTC index")
    return X


def duplicate_features(X: pd.DataFrame, threshold: float) -> dict:
    """Return all threshold-exceeding pairs and the later canonical columns."""
    _frame(X)
    if not np.isfinite(threshold) or not 0 < threshold <= 1:
        raise ValueError("Correlation threshold must be in (0,1]")
    corr = X.corr().to_numpy(dtype=np.float64)
    pairs, deleted = [], set()
    for i, left in enumerate(X.columns):
        for j in range(i + 1, len(X.columns)):
            value = corr[i, j]
            if np.isfinite(value) and abs(value) > threshold:
                right = X.columns[j]
                deleted.add(right)
                pairs.append({"evidence_level": EVIDENCE, "first": left, "later": right,
                              "correlation": float(value)})
    matrix = [[None if not np.isfinite(value) else float(value) for value in row] for row in corr]
    return {"evidence_level": EVIDENCE, "features": list(X.columns), "correlation_matrix": matrix,
            "duplicate_pairs": pairs, "deleted_features": [c for c in X.columns if c in deleted],
            "retained_features": [c for c in X.columns if c not in deleted]}


def spectral_diagnostics(X: pd.DataFrame, alpha: float) -> dict:
    """Population covariance in freshly standardized coordinates and Ridge shrinkage."""
    _frame(X)
    if not np.isfinite(alpha) or alpha <= 0:
        raise ValueError("alpha must be finite and positive")
    z = StandardScaler().fit_transform(X)
    centered = z - z.mean(axis=0)
    covariance = centered.T @ centered / len(z)
    covariance = (covariance + covariance.T) / 2
    eigenvalues = np.linalg.eigvalsh(covariance)
    penalized = len(z) * eigenvalues + alpha
    if not np.isfinite(eigenvalues).all() or penalized.min() <= 0:
        raise ValueError("Invalid covariance/Ridge eigenspectrum")
    shrinkage = len(z) * eigenvalues / penalized
    return {"evidence_level": EVIDENCE, "n": len(X), "feature_count": X.shape[1],
            "features": list(X.columns), "covariance_ddof": 0, "eigenvalues": eigenvalues.tolist(),
            "covariance_condition": None if eigenvalues.min() <= 0 else float(eigenvalues.max()/eigenvalues.min()),
            "covariance_condition_reason": "nonpositive_minimum_eigenvalue" if eigenvalues.min() <= 0 else None,
            "ridge_condition": float(penalized.max()/penalized.min()),
            "shrinkage_factors": shrinkage.tolist(), "weakest_shrinkage": float(shrinkage[0]),
            "alpha": float(alpha)}


def gap_identity(X: pd.DataFrame, bars: pd.DataFrame, tolerance: float) -> dict:
    """Check the algebraic bar-gap identity on already selected training rows."""
    _frame(X)
    if not np.isfinite(tolerance) or tolerance < 0:
        raise ValueError("Gap tolerance must be nonnegative finite")
    if not {"open_time", "open", "close"}.issubset(bars.columns):
        raise ValueError("Gap identity needs open_time, open and close")
    indexed = bars.set_index("open_time")
    if not indexed.index.is_unique or not indexed.index.is_monotonic_increasing:
        raise ValueError("Bars must have sorted unique open times")
    # Restrict arithmetic to the audited training rows and their preceding bars.
    # The loader may supply later development rows, but they are not audited.
    current_open = indexed.open.reindex(X.index).to_numpy(dtype=np.float64)
    prior_close = indexed.close.reindex(X.index-pd.Timedelta(minutes=1)).to_numpy(dtype=np.float64)
    expected = np.log(current_open/prior_close)
    gap = (X.log_return_1m - X.log_close_open).to_numpy(dtype=np.float64)
    if not np.isfinite(expected).all() or not np.isfinite(gap).all():
        raise ValueError("Gap identity requires a known contiguous previous bar")
    error = float(np.max(np.abs(gap - expected)))
    if error > tolerance:
        raise ValueError(f"Gap identity exceeds frozen tolerance: {error}")
    return {"evidence_level": EVIDENCE, "max_absolute_error": error, "tolerance": tolerance,
            "passed": True, "gap": gap}


def leverage_shares(gap, y, times, *, top_k, quantiles, top_timestamps_count, crash_start, crash_end) -> dict:
    """Independent stable |gap| and |gap*y| rankings; signed cross-product shares."""
    gap, y = np.asarray(gap, dtype=np.float64), np.asarray(y, dtype=np.float64)
    times = pd.DatetimeIndex(times)
    if (gap.ndim != 1 or gap.shape != y.shape or len(gap) != len(times) or not len(gap)
            or not np.isfinite(gap).all() or not np.isfinite(y).all()
            or times.tz is None or str(times.tz) != "UTC" or times.hasnans
            or not times.is_unique or not times.is_monotonic_increasing):
        raise ValueError("Leverage inputs must be finite aligned vectors and ordered UTC times")
    if any(type(k) is not int or k <= 0 for k in top_k) or type(top_timestamps_count) is not int or top_timestamps_count <= 0:
        raise ValueError("top counts must be positive integers")
    if any(not np.isfinite(q) or not 0 <= q <= 1 for q in quantiles):
        raise ValueError("Quantiles must be finite probabilities")
    products = gap*y
    sums = {"gap_squared": float(np.dot(gap, gap)), "gap_y": float(products.sum())}
    orders = {"gap_squared": np.argsort(-np.abs(gap), kind="stable"),
              "gap_y": np.argsort(-np.abs(products), kind="stable")}
    vectors = {"gap_squared": gap**2, "gap_y": products}
    rows = []
    for name, order in orders.items():
        for k in top_k:
            selected = order[:k]
            numerator = float(vectors[name][selected].sum())
            rows.append({"evidence_level": EVIDENCE, "quantity": name, "requested_k": k,
                         "selected_count": len(selected), "numerator": numerator, "denominator": sums[name],
                         "share": None if sums[name] == 0 else numerator/sums[name],
                         "reason": "zero_denominator" if sums[name] == 0 else None})
    mask = (times >= pd.Timestamp(crash_start)) & (times < pd.Timestamp(crash_end))
    crash = [{"evidence_level": EVIDENCE, "quantity": name, "n": int(mask.sum()),
              "numerator": float(vector[mask].sum()), "denominator": sums[name],
              "share": None if sums[name] == 0 else float(vector[mask].sum()/sums[name]),
              "reason": "zero_denominator" if sums[name] == 0 else None} for name, vector in vectors.items()]
    return {"evidence_level": EVIDENCE, "zero_fraction": float(np.mean(gap == 0)),
            "quantiles": [{"evidence_level": EVIDENCE, "q": q, "value": float(np.quantile(gap,q))} for q in quantiles],
            "top_shares": rows, "crash_shares": crash,
            "top_timestamps": [{"evidence_level": EVIDENCE, "ranking": name,
                                "times": [times[i].isoformat() for i in order[:top_timestamps_count]]}
                               for name, order in orders.items()]}


def _assert_initial_train(X, y, metadata, settings):
    _frame(X)
    if not X.index.equals(y.index) or not X.index.equals(metadata.index):
        raise ValueError("Training feature, target and metadata indices must be identical")
    if not np.isfinite(y.to_numpy(dtype=np.float64)).all():
        raise ValueError("Training labels must be finite")
    fit_time, start = pd.Timestamp(settings["initial_fit_time"]), pd.Timestamp(settings["initial_start"])
    for key in ("decision_time", "feature_available_time", "label_available_time"):
        values = pd.DatetimeIndex(metadata[key])
        if values.tz is None or str(values.tz) != "UTC" or values.hasnans:
            raise ValueError("Training metadata times must be explicit UTC")
    if (metadata.decision_time.lt(start).any() or metadata.decision_time.ge(fit_time).any()
            or metadata.feature_available_time.gt(fit_time).any() or metadata.label_available_time.gt(fit_time).any()):
        raise ValueError("Specification audit is restricted to mature Initial Train, before any evaluation forecast")


def audit_training_set(X, y, metadata, bars, config, horizon) -> dict:
    """Audit only a preselected legal Initial Train set; no evaluation matrix accepted."""
    settings, experiment = config["spec_audit"], config["experiment"]
    _assert_initial_train(X,y,metadata,settings)
    if X.shape[1] != settings["feature_count"]:
        raise ValueError("Initial feature count differs from frozen specification")
    duplicates = duplicate_features(X, settings["duplicate_corr_threshold"])
    windows = []
    for window in settings["windows"]:
        subset = X.loc[metadata.decision_time.ge(pd.Timestamp(settings["window_starts"][window]))]
        windows.append({"window": window, **spectral_diagnostics(subset,experiment["ridge_alpha"])})
    gap_result = gap_identity(X,bars,settings["gap_max_abs_tolerance"])
    gap = gap_result.pop("gap")
    gap_result.update(leverage_shares(gap,y.to_numpy(),metadata.decision_time,
                       top_k=settings["top_k"],quantiles=settings["gap_quantiles"],
                       top_timestamps_count=settings["top_timestamps_count"],
                       crash_start=settings["crash_session_start"],crash_end=settings["crash_session_end"]))
    fits = []
    for specification in settings["specifications"]:
        selected = X
        if specification == "v1_crash_excluded":
            crash = metadata.decision_time.ge(pd.Timestamp(settings["crash_session_start"])) & metadata.decision_time.lt(pd.Timestamp(settings["crash_session_end"]))
            selected = X.loc[~crash]
        elif specification == "v2":
            selected = X.loc[:,duplicates["retained_features"]]
        elif specification != "v1":
            raise ValueError("Unknown frozen audit specification")
        labels, audit_metadata = y.loc[selected.index], metadata.loc[selected.index]
        _assert_initial_train(selected,labels,audit_metadata,settings)
        model = Pipeline([("scaler",StandardScaler()),("ridge",Ridge(alpha=experiment["ridge_alpha"],
                         solver=experiment["solver"],fit_intercept=experiment["fit_intercept"]))])
        model.fit(selected,labels)
        # A second pre-predict guard makes the training-only contract observable.
        _assert_initial_train(selected,labels,audit_metadata,settings)
        predicted = model.predict(selected)
        scale_to_bp = config["cost_magnitude"]["return_to_bp"]
        fits.append({"evidence_level": EVIDENCE, "specification": specification,
                     "n":len(selected),"features":list(selected.columns),
                     "first_decision":audit_metadata.decision_time.iloc[0].isoformat(),
                     "last_decision":audit_metadata.decision_time.iloc[-1].isoformat(),
                     "last_label_available":audit_metadata.label_available_time.max().isoformat(),
                     "coefficients_bp_per_sd":dict(zip(selected.columns,(model.named_steps["ridge"].coef_*scale_to_bp).tolist())),
                     "intercept_bp":float(model.named_steps["ridge"].intercept_*scale_to_bp),
                     "in_sample_prediction_sd":float(np.std(predicted,ddof=settings["correlation_ddof"])),
                     "in_sample_prediction_sd_bp":float(np.std(predicted,ddof=settings["correlation_ddof"])*scale_to_bp),
                     "spectral":spectral_diagnostics(selected,experiment["ridge_alpha"]),
                     "evaluation_predictions":0})
    return {"evidence_level":EVIDENCE,"horizon_minutes":horizon,"train_n":len(X),
            "initial_fit_time":settings["initial_fit_time"],"correlation":duplicates,
            "windows":windows,"gap":gap_result,"fits":fits,
            "model_fit_count":len(fits),"evaluation_predictions":0}


def run_specification_audit(config, *, bars=None, source=None) -> dict:
    """Reuse the original verified loader and March split, then restrict every fit."""
    validate_development_bounds(DEVELOPMENT_START, DEVELOPMENT_END)
    if bars is None:
        bars, source = load_research_dataset(config["inputs"]["development_manifest_path"])
    feature_result = build_features(bars,pd.Timedelta(seconds=config["experiment"]["availability_delay_seconds"]))
    results = []
    for horizon in config["experiment"]["horizons"]:
        label_result = build_labels(bars,horizon,pd.Timedelta(seconds=config["experiment"]["availability_delay_seconds"]))
        metadata = feature_result.metadata.join(label_result.frame)
        split = make_research_splits(metadata,horizon)[0]
        if split.fit_time != pd.Timestamp(config["spec_audit"]["initial_fit_time"]):
            raise ValueError("Original March fit time differs from frozen audit config")
        indices = split.train_indices
        X = feature_result.frame.loc[indices]
        y = mature_labels(label_result.frame,split.fit_time,indices=indices)
        results.append(audit_training_set(X,y,metadata.loc[indices],bars,config,horizon))
    count = sum(row["model_fit_count"] for row in results)
    if count != config["spec_audit"]["model_fit_count"]:
        raise ValueError("Specification fit budget differs from frozen count")
    return {"evidence_level":EVIDENCE,"horizons":results,"model_fit_count":count,
            "evaluation_predictions":0,"source_manifest_sha256":(source or {}).get("overall_manifest_sha256"),
            "scope":"Initial Train only; no validation/development evaluation predictions"}
