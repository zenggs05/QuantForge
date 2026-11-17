"""Frozen Bartlett/Newey-West inference and Holm multiplicity correction."""

from __future__ import annotations

import math
import json
import numpy as np
import pandas as pd

from quantforge.diagnostics.block_bootstrap import BASELINES, MONTHS, ROLE_BY_MONTH, ROLES, validate_paired

LAGS = (15, 60, 240)
Z975 = 1.95996398454


def segment_hac_variance(values: np.ndarray, lag: int):
    """Newey-West variance of the mean, fixed n denominator and no correction."""
    values = np.asarray(values, dtype=float)
    if (values.ndim != 1 or not len(values) or not np.isfinite(values).all()
            or type(lag) is not int or lag < 0):
        raise ValueError("HAC requires a nonempty finite vector and nonnegative integer lag")
    n = len(values)
    if n <= lag:
        return None
    if np.ptp(values) == 0:
        return 0.0
    centered = values - values.mean()
    lrv = float(np.dot(centered, centered) / n)
    for k in range(1, lag + 1):
        gamma = float(np.dot(centered[k:], centered[:-k]) / n)
        lrv += 2 * (1.0 - k / (lag + 1)) * gamma
    if not np.isfinite(lrv):
        raise ValueError("HAC arithmetic overflowed")
    if lrv < 0:
        raise ValueError("Bartlett HAC long-run variance is unexpectedly negative")
    return float(lrv / n)


def _stats(delta: float, variance: float | None, status: str, reason: str | None):
    if status != "estimable" or variance is None:
        return None, None, None, None, None, reason
    if variance < 0 or not np.isfinite(variance):
        return None, None, None, None, None, "invalid_long_run_variance"
    if variance == 0:
        return 0.0, None, None, delta, delta, "degenerate_variance"
    se = math.sqrt(variance)
    z = delta / se
    p = math.erfc(abs(z) / math.sqrt(2.0))
    return se, z, p, delta - Z975 * se, delta + Z975 * se, reason


def holm_adjust(p_values: list[float | None]) -> list[float | None]:
    """Holm step-down; undefined members stay null but count in family size."""
    for p in p_values:
        if p is not None and (isinstance(p, bool) or not np.isfinite(p) or p < 0 or p > 1):
            raise ValueError("Holm p-values must be None or finite numbers in [0,1]")
    indexes = [i for i, p in enumerate(p_values) if p is not None]
    ordered = sorted(indexes, key=lambda i: p_values[i])
    adjusted_values = [None] * len(p_values)
    previous = 0.0
    for rank, index in enumerate(ordered):
        adjusted = min(1.0, (len(p_values) - rank) * p_values[index])
        adjusted = max(previous, adjusted)
        previous = adjusted
        adjusted_values[index] = adjusted
    return adjusted_values


def _holm(rows: list[dict], p_column: str, reject_column: str, selector, expected_size, alpha=0.05):
    indexes = [i for i, row in enumerate(rows) if selector(row)]
    if len(indexes) != expected_size:
        raise AssertionError("Multiplicity family differs from the frozen size")
    adjusted = holm_adjust([rows[i]["p_value"] for i in indexes])
    undefined = sum(value is None for value in adjusted)
    for index, value in zip(indexes, adjusted):
        rows[index][p_column] = value
        rows[index][reject_column] = None if value is None else value <= alpha
    return undefined


def _row(scope_type, scope_id, role, aggregation, horizon, baseline, lag, delta, variance,
         n_samples, months, status, reason, relative):
    se, z, p, low, high, reason = _stats(delta, variance, status, reason)
    return {
        "method": "HAC_Newey_West_Bartlett", "scope_type": scope_type, "scope_id": scope_id,
        "role": role, "aggregation": aggregation, "horizon_minutes": horizon, "baseline": baseline,
        "metric": "squared_loss", "lag": lag, "n_samples": n_samples, "month_count": len(months),
        "months": tuple(months), "estimate_mse_delta": delta, "relative_mse_delta": relative,
        "relative_mse_delta_pct": None if relative is None else relative * 100,
        "variance": variance, "se": se, "z": z, "p_value": p, "ci_low": low, "ci_high": high,
        "status": status, "reason": reason, "holm_main32_p": None, "holm_global96_p": None,
        "holm_main32_reject": None, "holm_global96_reject": None, "main32_family_size": 32,
        "global96_family_size": 96, "cross_month_covariance": "assumed zero" if len(months) > 1 else None,
        "primary_lag": lag == 60, "interval_type": "marginal_95_normal_not_simultaneous",
        "relative_null_reason": "zero_baseline_mse" if relative is None else None,
    }


def run_hac(paired: pd.DataFrame, lags: tuple[int, ...] = LAGS) -> pd.DataFrame:
    """Return all 96 planned month/role, horizon, baseline and lag rows."""
    if tuple(lags) != LAGS:
        raise ValueError("HAC lags are frozen to (15,60,240)")
    if any(type(lag) is not int or lag <= 0 for lag in lags):
        raise ValueError("HAC lags must be positive integers")
    groups = validate_paired(paired)
    rows = []
    for horizon in (15, 5):
        for baseline in BASELINES:
            for lag in lags:
                monthly = {}
                for month in MONTHS:
                    group = groups[horizon, month]
                    difference = group["ridge"] - group[baseline]
                    point = float(difference.mean())
                    base = float(group[baseline].mean())
                    monthly[month] = (point, base, _segment_hac_per_month(difference, group, lag))
                    variance = monthly[month][2]
                    rows.append(_row("month", month, ROLE_BY_MONTH[month], "month", horizon, baseline, lag,
                                     point, variance, group["n"], [month],
                                     "estimable" if variance is not None else "not_estimable",
                                     None if variance is not None else "continuous_segment_not_longer_than_lag",
                                     None if base == 0 else point / base))
                    sizes = [end - start for start, end in group["segments"]]
                    rows[-1]["diagnostics_json"] = json.dumps({
                        "segment_lengths": sizes, "segment_count": len(sizes),
                        "min_segment_length": min(sizes), "fixed_lag": lag,
                        "centering": "separately within each continuous segment",
                        "gamma_denominator": "fixed segment n", "finite_sample_correction": False,
                        "segment_covariance": "assumed zero across gaps",
                    }, sort_keys=True)
                for role in ROLES:
                    months = [month for month in MONTHS if ROLE_BY_MONTH[month] == role]
                    entries = [monthly[month] for month in months]
                    weights_n = np.asarray([groups[horizon, month]["n"] for month in months], dtype=float)
                    for aggregation, weights in (("n_weighted", weights_n), ("month_equal", np.ones(2))):
                        weights = weights / weights.sum()
                        delta = float(sum(w * e[0] for w, e in zip(weights, entries)))
                        base = float(sum(w * e[1] for w, e in zip(weights, entries)))
                        variance = None if any(e[2] is None for e in entries) else float(sum(w * w * e[2] for w, e in zip(weights, entries)))
                        rows.append(_row("role", role, role, aggregation, horizon, baseline, lag, delta, variance,
                                         int(sum(groups[horizon, month]["n"] for month in months)), months,
                                         "estimable" if variance is not None else "not_estimable",
                                         None if variance is not None else "a month contains a short continuous segment",
                                         None if base == 0 else delta / base))
                        rows[-1]["diagnostics_json"] = json.dumps({
                            "months": months, "weights": weights.tolist(), "fixed_lag": lag,
                            "cross_month_covariance": "assumed zero despite shared training windows",
                            "conditioned_on_observed_months": True,
                        }, sort_keys=True)
    if len(rows) != 96:
        raise AssertionError("Frozen HAC must produce 96 rows")
    undefined_main = _holm(rows, "holm_main32_p", "holm_main32_reject", lambda row: row["lag"] == 60, 32)
    undefined_global = _holm(rows, "holm_global96_p", "holm_global96_reject", lambda row: True, 96)
    for row in rows:
        row["main32_undefined_count"] = undefined_main
        row["global96_undefined_count"] = undefined_global
        row["main32_defined_count"] = 32 - undefined_main
        row["global96_defined_count"] = 96 - undefined_global
    return pd.DataFrame(rows)


def _segment_hac_per_month(difference: np.ndarray, group: dict, lag: int):
    sizes = [end - start for start, end in group["segments"]]
    if any(size <= lag for size in sizes):
        return None
    total = len(difference)
    variance = 0.0
    for (start, end), size in zip(group["segments"], sizes):
        segment_variance = segment_hac_variance(difference[start:end], lag)
        variance += (size / total) ** 2 * segment_variance
    return float(variance)
