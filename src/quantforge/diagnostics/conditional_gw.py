"""Exploratory monthly conditional loss regression on known-at-fit volatility.

This diagnostic uses monthly realized-loss differences, not CW adjusted
information products. The joint normal/HAC Wald reference is exploratory and
does not confer confirmatory validity on expanding nested forecast estimates.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import chi2

from quantforge.diagnostics.forecast_tests import _integer, _vector

EVIDENCE_LEVEL = "exploratory_not_in_holm"


def _utc_index(values, name):
    index = pd.DatetimeIndex(values)
    if index.hasnans or index.tz is None or str(index.tz) != "UTC":
        raise ValueError(f"{name} must contain nonmissing UTC timestamps")
    return index


def build_instrument(labels, fit_times) -> pd.DataFrame:
    """Use only mature valid 1m targets wholly inside the previous month.

    Only temporal metadata is inspected before selecting mature rows. Neither
    target values nor validity flags on future rows are accessed. Missing
    minutes are never filled; counts expose sparse synthetic episodes.
    """
    required = {"target_return", "valid_label", "target_start_time",
                "target_end_time", "label_available_time"}
    if (not isinstance(labels, pd.DataFrame) or labels.empty
            or labels.columns.has_duplicates or not required.issubset(labels.columns)):
        raise ValueError("labels must contain unique required target/time columns")
    index = _utc_index(labels.index, "label index")
    if index.has_duplicates or not index.is_monotonic_increasing:
        raise ValueError("label index must be sorted and unique")
    times = _utc_index(fit_times, "fit times")
    if (times.empty or times.has_duplicates or not times.is_monotonic_increasing
            or any(t != t.normalize().replace(day=1) for t in times)):
        raise ValueError("fit times must be sorted unique UTC month starts")
    # These three reads contain temporal metadata only, including future rows.
    starts = _utc_index(labels["target_start_time"], "target starts")
    ends = _utc_index(labels["target_end_time"], "target ends")
    available = _utc_index(labels["label_available_time"], "availability")
    if not (ends - starts == pd.Timedelta(minutes=1)).all():
        raise ValueError("volatility instrument requires exactly 1m target intervals")
    if (available < ends).any():
        raise ValueError("label availability cannot precede target end")
    rows = []
    for fit_time in times:
        previous_start = fit_time - pd.offsets.MonthBegin(1)
        eligible = ((available <= fit_time) & (starts >= previous_start)
                    & (ends <= fit_time))
        selected = labels.loc[index[eligible]]
        validity = selected["valid_label"]
        if not pd.api.types.is_bool_dtype(validity) or validity.isna().any():
            raise ValueError("mature validity flags must be nonmissing booleans")
        selected = selected.loc[validity]
        if selected.empty:
            raise ValueError(f"No mature valid previous-month labels for {fit_time.isoformat()}")
        targets = _vector(selected["target_return"], "mature previous-month targets")
        with np.errstate(over="ignore", invalid="ignore"):
            instrument = float(np.sqrt(np.dot(targets, targets)))
        if not np.isfinite(instrument):
            raise ValueError("volatility instrument arithmetic overflowed")
        maximum = pd.Timestamp(selected["label_available_time"].max())
        rows.append({
            "fit_time": fit_time,
            "month": fit_time.strftime("%Y-%m"),
            "instrument": instrument,
            "instrument_name": "realized_vol_prev_month",
            "source_month": previous_start.strftime("%Y-%m"),
            "source_start": previous_start,
            "source_end_exclusive": fit_time,
            "n_labels": len(targets),
            "calendar_minutes": int((fit_time - previous_start) / pd.Timedelta(minutes=1)),
            "max_label_available_time": maximum,
            "coverage": "observed_mature_valid_1m_targets_no_filling",
            "evidence_level": EVIDENCE_LEVEL,
        })
    return pd.DataFrame(rows, index=pd.DatetimeIndex(times, name="fit_time_index"))


def _descriptive_bins(loss, instrument, quantiles):
    quantiles = _vector(quantiles, "bin quantiles")
    if (len(quantiles) < 2 or quantiles[0] != 0 or quantiles[-1] != 1
            or np.any(np.diff(quantiles) <= 0)):
        raise ValueError("bin quantiles must increase strictly from zero to one")
    edges = np.quantile(instrument, quantiles, method="linear")
    rows = []
    for i, (low, high) in enumerate(zip(edges[:-1], edges[1:], strict=True)):
        selected = ((instrument >= low)
                    & ((instrument <= high) if i == len(edges) - 2 else (instrument < high)))
        rows.append({
            "bin": i, "quantile_low": float(quantiles[i]),
            "quantile_high": float(quantiles[i + 1]),
            "instrument_low": float(low), "instrument_high": float(high),
            "upper_inclusive": i == len(edges) - 2, "n": int(selected.sum()),
            "mean_loss": None if not selected.any() else float(loss[selected].mean()),
            "evidence_level": EVIDENCE_LEVEL,
        })
    return rows


def conditional_gw(monthly_loss, instrument, *, hac_lag=4,
                   bins=(0.0, 0.25, 0.5, 0.75, 1.0)) -> dict:
    """OLS on intercept/RV with score-Bartlett HAC and joint Wald chi-square.

    No finite-sample correction, regularization or pseudoinverse is used.
    Singular designs/covariances retain explicit nulls and a reason.
    """
    loss = _vector(monthly_loss, "monthly loss differences")
    instrument = _vector(instrument, "instrument")
    lag = _integer(hac_lag, "HAC lag")
    if len(loss) != len(instrument):
        raise ValueError("monthly losses and instruments must be aligned")
    n = len(loss)
    result = {
        "method": "conditional_GW_monthly_OLS_HAC",
        "evidence_level": EVIDENCE_LEVEL,
        "n": n, "hac_lag": lag,
        "coefficient_names": ["intercept", "realized_vol_prev_month"],
        "wald_null": "intercept_and_slope_zero", "wald_df": 2,
        "beta": [None, None], "se": [None, None], "t": [None, None],
        "covariance": None, "wald": None, "p_value": None,
        "status": "not_estimable", "reason": None,
        "finite_sample_correction": False,
        "bins": _descriptive_bins(loss, instrument, bins),
    }
    if n <= max(lag, 2):
        result["reason"] = "too_few_months"
        return result
    design = np.column_stack((np.ones(n), instrument))
    if np.linalg.matrix_rank(design) != 2:
        result["reason"] = "singular_design"
        return result
    beta = np.linalg.lstsq(design, loss, rcond=None)[0]
    residual = loss - design @ beta
    scores = design * residual[:, None]
    meat = scores.T @ scores
    for k in range(1, lag + 1):
        gamma = scores[k:].T @ scores[:-k]
        meat += (1 - k / (lag + 1)) * (gamma + gamma.T)
    bread = np.linalg.inv(design.T @ design)
    covariance = bread @ meat @ bread
    covariance = (covariance + covariance.T) / 2
    if not np.isfinite(beta).all() or not np.isfinite(covariance).all():
        raise ValueError("conditional GW arithmetic overflowed")
    result["beta"] = beta.tolist()
    result["covariance"] = covariance.tolist()
    if np.any(np.diag(covariance) <= 0) or np.linalg.matrix_rank(covariance) != 2:
        result["reason"] = "singular_coefficient_covariance"
        return result
    se = np.sqrt(np.diag(covariance))
    wald = float(beta @ np.linalg.solve(covariance, beta))
    if not np.isfinite(wald) or wald < 0:
        raise ValueError("conditional GW Wald statistic is invalid")
    result.update(se=se.tolist(), t=(beta / se).tolist(), wald=wald,
                  p_value=float(chi2.sf(wald, 2)), status="estimable", reason=None)
    return result
