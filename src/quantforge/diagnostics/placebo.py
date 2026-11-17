"""Deterministic, within-month placebos for frozen forecast information tests.

These routines never fit a model, read a file, or resample market rows. All
research choices are explicit arguments from the caller's frozen config.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from quantforge.diagnostics.forecast_tests import (
    batched_bartlett_variance,
    clark_west_differential,
    llsw_lag,
    newey_west_segment,
    pool_roles,
    validate_times,
)


def _integer(value, name, minimum=0):
    if (isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer))
            or value < minimum):
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return int(value)


def _array(value, name):
    raw = np.asarray(value)
    if raw.ndim != 1 or not raw.size or raw.dtype.kind not in "iuf":
        raise ValueError(f"{name} must be a nonempty real vector")
    out = np.asarray(raw, dtype=np.float64)
    if not np.isfinite(out).all():
        raise ValueError(f"{name} must be finite")
    return out


def _settings(settings, inference):
    _integer(settings["sign_flip_block_minutes"], "block minutes", 1)
    _integer(settings["sign_flip_draws"], "draws", 2)
    _integer(settings["time_shift_minutes"], "shift minutes", 1)
    if settings["pooling"] not in ("n_weighted", "month_equal"):
        raise ValueError("Unsupported placebo pooling")
    if type(settings["include_llsw"]) is not bool:
        raise ValueError("include_llsw must be boolean")
    lags = settings["lags"]
    if not isinstance(lags, (list, tuple)) or len(set(lags)) != len(lags):
        raise ValueError("Placebo lags must be a unique sequence")
    for lag in lags:
        _integer(lag, "lag", 0)
    if not lags and not settings["include_llsw"]:
        raise ValueError("At least one bandwidth is required")
    quantiles = np.asarray(settings["quantiles"], dtype=float)
    if (quantiles.ndim != 1 or not len(quantiles) or not np.isfinite(quantiles).all()
            or np.any(quantiles < 0) or np.any(quantiles > 1)
            or np.any(np.diff(quantiles) <= 0)):
        raise ValueError("Quantiles must be finite, strictly increasing, and within [0,1]")
    limits = [settings["mean_abs_limit"], settings["sd_min"], settings["sd_max"]]
    if (not np.isfinite(limits).all() or min(limits) < 0
            or settings["sd_min"] > settings["sd_max"]):
        raise ValueError("Invalid placebo diagnostic limits")
    # Validate the bandwidth constant even when the observed group is small.
    llsw_lag(1, inference["llsw_constant"])
    return [(str(lag), int(lag)) for lag in lags] + ([("LLSW", None)] if settings["include_llsw"] else [])


def _months(monthly):
    if not isinstance(monthly, (list, tuple)) or not monthly:
        raise ValueError("At least one monthly segment is required")
    output, indexes, last = [], [], None
    for source in monthly:
        y, f = _array(source["y"], "y"), _array(source["f"], "f")
        if len(y) != len(f):
            raise ValueError("Forecasts and targets must have equal lengths")
        times = pd.DatetimeIndex(source["times"])
        positions = validate_times(times, len(y), contiguous=False)
        if times.strftime("%Y-%m").nunique() != 1:
            raise ValueError("A placebo segment must stay within one UTC month")
        if last is not None and times[0] <= last:
            raise ValueError("Monthly inputs must be chronological and nonoverlapping")
        last = times[-1]
        month_index = _integer(source["month_index"], "month_index")
        indexes.append(month_index)
        month = source.get("month", times[0].strftime("%Y-%m"))
        if month != times[0].strftime("%Y-%m"):
            raise ValueError("Month label must agree with UTC decision times")
        breaks = np.r_[0, np.flatnonzero(np.diff(positions) != 1) + 1, len(y)]
        segments = [(int(a), int(b)) for a, b in zip(breaks[:-1], breaks[1:])]
        output.append(dict(times=times, y=y, f=f, month_index=month_index,
                           month=month, segments=segments))
    if len(set(indexes)) != len(indexes) or indexes != sorted(indexes):
        raise ValueError("Month indices must be distinct and chronological")
    return output


def utc_block_signs(times, *, block_minutes, seed, horizon, role_id, month_index, draw):
    """One seeded Rademacher sign per UTC-aligned block, never per row."""
    index = pd.DatetimeIndex(times)
    if not len(index):
        raise ValueError("Block signs require nonempty times")
    validate_times(index, len(index), contiguous=False)
    block_minutes = _integer(block_minutes, "block_minutes", 1)
    sequence = [_integer(value, name) for value, name in (
        (seed, "seed"), (horizon, "horizon"), (role_id, "role_id"),
        (month_index, "month_index"), (draw, "draw"))]
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    units = index.as_unit("ns").asi8 // int(pd.Timedelta(minutes=block_minutes).value)
    _, inverse = np.unique(units, return_inverse=True)
    rng = np.random.default_rng(np.random.SeedSequence(sequence))
    signs = rng.integers(0, 2, size=int(inverse.max()) + 1) * 2 - 1
    return signs[inverse].astype(np.float64)


def align_future_targets(times, y, f, *, shift_minutes):
    """Pair f(t) with y(t+shift) by exact timestamps within a monthly segment."""
    y, f = _array(y, "y"), _array(f, "f")
    if len(y) != len(f):
        raise ValueError("Forecasts and targets must have equal lengths")
    index = pd.DatetimeIndex(times)
    validate_times(index, len(y), contiguous=False)
    if index.strftime("%Y-%m").nunique() != 1:
        raise ValueError("Time-shift matching cannot cross monthly segments")
    shift = pd.Timedelta(minutes=_integer(shift_minutes, "shift_minutes", 1))
    target_indices = index.get_indexer(index + shift)
    source_indices = np.flatnonzero(target_indices >= 0)
    target_indices = target_indices[source_indices]
    return {"times": index[source_indices], "target_times": index[target_indices],
            "f": f[source_indices], "y": y[target_indices],
            "source_indices": source_indices, "target_indices": target_indices}


def _monthly_statistics(values, month, lag):
    """Preserve the old convention: independent continuous segments across gaps."""
    pieces = [newey_west_segment(values[a:b], lag, times=month["times"][a:b], alternative="greater")
              for a, b in month["segments"]]
    return pieces[0] if len(pieces) == 1 else pool_roles(pieces, pooling="n_weighted", alternative="greater")


def _draw_variances(matrix, segments, lag):
    n = matrix.shape[1]
    if any(b-a <= lag for a, b in segments):
        return None
    variance = np.zeros(matrix.shape[0], dtype=np.float64)
    for a, b in segments:
        variance += ((b-a)/n)**2 * batched_bartlett_variance(matrix[:, a:b], lag)
    return variance


def _metadata(horizon, role_id, seed, settings, evidence_level, kind, bandwidth, months, lags):
    for value, name in [(horizon, "horizon"), (role_id, "role_id"), (seed, "seed")]:
        _integer(value, name)
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    if evidence_level not in ("synthetic", "exploratory_post_hoc"):
        raise ValueError("Placebo evidence must be synthetic or exploratory_post_hoc")
    return {"evidence_level": evidence_level, "placebo": kind, "baseline": "zero",
            "horizon_minutes": horizon, "role_id": role_id, "seed": seed,
            "bandwidth": bandwidth, "pooling": settings["pooling"],
            "months_json": json.dumps([m["month"] for m in months]),
            "month_count": len(months), "n_samples": sum(len(m["y"]) for m in months),
            "actual_lags_json": json.dumps(lags), "cross_month_covariance": "assumed zero"}


def sign_flip_placebo(monthly, *, horizon, role_id, seed, settings, inference, evidence_level):
    """P1: UTC-block sign flips with exact frozen per-draw seed identities.

    The fast Bartlett calculation is algebraically identical to fixed-n HAC;
    it is independently tested against the legacy implementation. The full
    data matrix is transient, and only aggregate diagnostics are returned.
    """
    bands, months = _settings(settings, inference), _months(monthly)
    counts = np.array([len(m["y"]) for m in months], dtype=np.float64)
    weights = counts/counts.sum() if settings["pooling"] == "n_weighted" else np.ones(len(months))/len(months)
    draws = settings["sign_flip_draws"]
    observed, means, variances = {}, np.zeros(draws), {name: np.zeros(draws) for name, _ in bands}
    actual_lags, estimable = {}, {name: True for name, _ in bands}
    for name, fixed in bands:
        lags = [llsw_lag(len(m["y"]), inference["llsw_constant"]) if fixed is None else fixed for m in months]
        actual_lags[name] = lags
        stats = [_monthly_statistics(clark_west_differential(m["y"], m["f"], np.zeros(len(m["y"]))), m, lag)
                 for m, lag in zip(months, lags)]
        observed[name] = pool_roles(stats, pooling=settings["pooling"], alternative="greater")
    for j, month in enumerate(months):
        values = clark_west_differential(month["y"], month["f"], np.zeros(len(month["y"])))
        matrix = np.empty((draws, len(values)), dtype=np.float64)
        for draw in range(draws):
            matrix[draw] = values * utc_block_signs(
                month["times"], block_minutes=settings["sign_flip_block_minutes"],
                seed=seed, horizon=horizon, role_id=role_id, month_index=month["month_index"], draw=draw)
        means += weights[j] * matrix.mean(axis=1)
        for name, _ in bands:
            variance = _draw_variances(matrix, month["segments"], actual_lags[name][j])
            if variance is None:
                estimable[name] = False
            else:
                variances[name] += weights[j]**2 * variance
    rows = []
    for name, _ in bands:
        row = _metadata(horizon, role_id, seed, settings, evidence_level, "P1_sign_flip", name, months, actual_lags[name])
        row.update(draws=draws, block_minutes=settings["sign_flip_block_minutes"],
                   observed_t=observed[name]["t"], observed_p=observed[name]["p_value"],
                   placebo_t_mean=None, placebo_t_sd=None, empirical_one_sided_p=None,
                   quantiles_json=None, status="not_estimable", reason=None, pipeline_warning=True)
        if not estimable[name] or observed[name]["t"] is None:
            row["reason"] = "short_segment_or_degenerate_observed_statistic"
        elif np.any(variances[name] <= 0) or not np.isfinite(variances[name]).all():
            row["reason"] = "degenerate_or_nonfinite_placebo_variance"
        else:
            statistics = means/np.sqrt(variances[name])
            if not np.isfinite(statistics).all():
                raise ValueError("Nonfinite placebo statistic")
            average, sd = float(statistics.mean()), float(statistics.std(ddof=1))
            quantiles = {str(q): float(v) for q, v in zip(settings["quantiles"], np.quantile(statistics, settings["quantiles"]))}
            row.update(placebo_t_mean=average, placebo_t_sd=sd,
                       empirical_one_sided_p=float((1+np.count_nonzero(statistics >= row["observed_t"]))/(draws+1)),
                       quantiles_json=json.dumps(quantiles, sort_keys=True, allow_nan=False), status="estimable",
                       pipeline_warning=bool(abs(average) > settings["mean_abs_limit"] or
                                             sd < settings["sd_min"] or sd > settings["sd_max"]))
        rows.append(row)
    return rows


def time_shift_placebo(monthly, *, horizon, role_id, seed, settings, inference, evidence_level):
    """P2: forecast at t versus target at t+one configured day, without wrapping."""
    bands, original = _settings(settings, inference), _months(monthly)
    aligned = [align_future_targets(m["times"], m["y"], m["f"], shift_minutes=settings["time_shift_minutes"])
               for m in original]
    rows = []
    for name, fixed in bands:
        if any(not len(m["y"]) for m in aligned):
            row = _metadata(horizon, role_id, seed, settings, evidence_level, "P2_time_shift", name, original, [])
            row.update(status="not_estimable", reason="no_exact_shift_matches_in_a_month", shifted_t=None,
                       shifted_p=None, shifted_n_samples=sum(len(m["y"]) for m in aligned), shift_minutes=settings["time_shift_minutes"])
            rows.append(row)
            continue
        months = _months([{**m, "month_index": old["month_index"], "month": old["month"]}
                          for m, old in zip(aligned, original)])
        lags = [llsw_lag(len(m["y"]), inference["llsw_constant"]) if fixed is None else fixed for m in months]
        stats = [_monthly_statistics(clark_west_differential(m["y"], m["f"], np.zeros(len(m["y"]))), m, lag)
                 for m, lag in zip(months, lags)]
        result = pool_roles(stats, pooling=settings["pooling"], alternative="greater")
        row = _metadata(horizon, role_id, seed, settings, evidence_level, "P2_time_shift", name, original, lags)
        row.update(status=result["status"], reason=result.get("reason"), shifted_t=result["t"],
                   shifted_p=result["p_value"], shifted_mean=result["mean"], shifted_se=result["se"],
                   shifted_n_samples=sum(len(m["y"]) for m in months), shift_minutes=settings["time_shift_minutes"])
        rows.append(row)
    return rows
