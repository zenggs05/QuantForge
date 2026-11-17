"""Descriptive diagnostics only: no fitting, recalibration or modified predictions."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

MODELS = ("zero", "historical_mean", "ridge")
QUANTILES = (0.0, .01, .05, .25, .5, .75, .95, .99, 1.0)


def _arrays(prediction, target):
    p, y = np.asarray(prediction, dtype=np.float64), np.asarray(target, dtype=np.float64)
    if p.ndim != 1 or y.ndim != 1 or p.size != y.size or not p.size:
        raise ValueError("Prediction and target must be nonempty aligned vectors")
    if not np.isfinite(p).all() or not np.isfinite(y).all():
        raise ValueError("Calibration inputs must be finite")
    return p, y


def calibration_metrics(prediction, target) -> dict:
    p, y = _arrays(prediction, target)
    residual = p - y
    pm, ym = float(p.mean()), float(y.mean())
    pv, yv = float(p.var()), float(y.var())
    cov = float(np.mean((p - pm) * (y - ym)))
    constant_p, constant_y = np.ptp(p) == 0, np.ptp(y) == 0
    ic = None if constant_p or constant_y else float(np.corrcoef(p, y)[0, 1])
    spearman = None if constant_p or constant_y else float(spearmanr(p, y).statistic)
    squared = residual ** 2
    if not np.isfinite([pm,ym,pv,yv,cov]).all() or not np.isfinite(squared).all():
        raise ValueError('Calibration arithmetic overflowed')
    slope = None if constant_p else cov / pv
    return {
        "n_samples": len(p), "prediction_mean": pm, "target_mean": ym,
        "prediction_std": float(np.sqrt(pv)), "target_std": float(np.sqrt(yv)),
        "std_ratio": None if constant_y else float(np.sqrt(pv / yv)),
        "prediction_bias": pm - ym, "mse": float(squared.mean()), "mae": float(np.abs(residual).mean()),
        "pearson_ic": ic, "spearman_correlation": spearman,
        "correlation_null_reason": "constant_prediction_or_target" if ic is None else None,
        "diagnostic_slope": slope, "diagnostic_intercept": None if slope is None else ym - slope * pm,
        "bias_squared": (pm - ym) ** 2, "prediction_variance": pv, "target_variance": yv,
        "covariance": cov, "mse_from_decomposition": (pm - ym) ** 2 + pv + yv - 2 * cov,
        "prediction_second_moment": float(np.mean(p ** 2)),
        "twice_prediction_target_moment": float(2 * np.mean(p * y)),
        "mse_delta_zero_from_moments": float(np.mean(p ** 2) - 2 * np.mean(p * y)),
        "std_ddof": 0, "slope_interpretation": "diagnostic only; not applied to predictions",
    }


def prediction_quantiles(prediction, target, bins: int = 10) -> pd.DataFrame:
    """Value-based bins preserve ties; no arbitrary rank splitting of constants."""
    p, y = _arrays(prediction, target)
    if isinstance(bins, bool) or not isinstance(bins, int) or bins < 1:
        raise ValueError("bins must be a positive integer")
    edges = np.unique(np.quantile(p, np.linspace(0, 1, bins + 1), method="linear"))
    if len(edges) == 1:
        codes = np.zeros(len(p), dtype=int)
    else:
        codes = np.searchsorted(edges[1:-1], p, side="left")
    rows = []
    for code in np.unique(codes):
        mask = codes == code
        rows.append({"bin": int(code), "n_samples": int(mask.sum()),
                     "lower_edge": float(edges[code]), "upper_edge": float(edges[min(code + 1, len(edges) - 1)]),
                     "prediction_min": float(p[mask].min()), "prediction_max": float(p[mask].max()),
                     "mean_prediction": float(p[mask].mean()), "mean_target": float(y[mask].mean()),
                     "actual_bins": len(np.unique(codes)), "requested_bins": bins})
    return pd.DataFrame(rows)


def run_calibration(paired: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summaries, bins, distributions = [], [], []
    for (month, role, horizon), group in paired.groupby(["month", "role", "horizon_minutes"], sort=True):
        y = group.target_return.to_numpy()
        for model in MODELS:
            p = group[f"prediction_{model}"].to_numpy()
            keys = {"month": month, "role": role, "horizon_minutes": int(horizon), "model": model}
            summaries.append({**keys, **calibration_metrics(p, y)})
            for row in prediction_quantiles(p, y).to_dict("records"):
                bins.append({**keys, **row})
            for name, values in (("prediction", p), ("target", y), ("residual", p-y), ("squared_error", (p-y)**2)):
                for q, value in zip(QUANTILES, np.quantile(values, QUANTILES), strict=True):
                    distributions.append({**keys, "quantity": name, "quantile": q, "value": float(value)})
    return pd.DataFrame(summaries), pd.DataFrame(bins), pd.DataFrame(distributions)


def loss_autocorrelation(paired: pd.DataFrame, max_lag: int = 240) -> pd.DataFrame:
    """Descriptive within-month ACF with no products across missing minutes."""
    rows = []
    for (month, role, horizon), group in paired.groupby(["month", "role", "horizon_minutes"], sort=True):
        group = group.sort_values("decision_time")
        dates = pd.DatetimeIndex(group.decision_time).as_unit("ns").asi8
        segments = np.r_[0, np.cumsum(np.diff(dates) != 60_000_000_000)]
        for base in ("zero", "historical_mean"):
            d = (group.squared_loss_ridge - group[f"squared_loss_{base}"]).to_numpy()
            centered = d - d.mean()
            denom = np.dot(centered, centered)
            for lag in range(min(max_lag, len(d)-1) + 1):
                if lag == 0:
                    value, count = (1.0 if denom > 0 else None), len(d)
                else:
                    valid = segments[lag:] == segments[:-lag]
                    count = int(valid.sum())
                    value = float(np.dot(centered[lag:][valid], centered[:-lag][valid]) / denom) if denom > 0 and count else None
                rows.append({"month":month, "role":role, "horizon_minutes":int(horizon), "baseline":base,
                             "lag":lag, "acf":value, "paired_products":count,
                             "null_reason":'no_contiguous_products' if not count else ('constant_loss_difference' if denom==0 else None),
                             "definition":"monthly centered biased ACF; no cross-gap products"})
    return pd.DataFrame(rows)
