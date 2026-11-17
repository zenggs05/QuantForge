"""Frozen paired non-circular moving-block bootstrap for squared losses."""

from __future__ import annotations

import json
import numpy as np
import pandas as pd

MONTHS = ("2026-03", "2026-04", "2026-05", "2026-06")
ROLES = ("validation", "development_evaluation")
ROLE_BY_MONTH = dict(zip(MONTHS, ("validation", "validation", "development_evaluation", "development_evaluation")))
BASELINES = ("zero", "historical_mean")
BLOCK_LENGTHS = (60, 360, 1440, 2880)


def validate_paired(paired: pd.DataFrame) -> dict:
    """Validate frozen groups without silently dropping or sorting input rows."""
    required = {"month", "role", "horizon_minutes", "decision_time",
                "squared_loss_ridge", "squared_loss_zero", "squared_loss_historical_mean"}
    if not isinstance(paired, pd.DataFrame) or paired.empty or not required.issubset(paired):
        raise ValueError("Paired input must contain every required loss and metadata column")
    if paired.columns.has_duplicates:
        raise ValueError("Duplicate paired columns are forbidden")
    if paired[list(required)].isna().any().any():
        raise ValueError("Paired inputs cannot contain missing values")
    if not isinstance(paired.decision_time.dtype, pd.DatetimeTZDtype) or str(paired.decision_time.dt.tz) != "UTC":
        raise ValueError("decision_time must have timezone-aware UTC dtype")
    if (not paired.horizon_minutes.map(lambda h: isinstance(h, (int, np.integer)) and not isinstance(h, bool)).all()
            or set(paired.horizon_minutes) != {5, 15}):
        raise ValueError("Both integer horizons 15 and 5 are required")
    if set(paired.month) != set(MONTHS):
        raise ValueError("Only the complete frozen March--June months are permitted")
    if (paired.month.map(ROLE_BY_MONTH) != paired.role).any():
        raise ValueError("Month/role assignment violates the frozen scopes")
    if (paired.decision_time.dt.strftime("%Y-%m") != paired.month).any():
        raise ValueError("Evaluation month must follow decision_time")
    losses = ["squared_loss_ridge", "squared_loss_zero", "squared_loss_historical_mean"]
    if any(not pd.api.types.is_numeric_dtype(paired[c]) for c in losses):
        raise ValueError("Loss columns must be numeric")
    matrix = paired[losses].to_numpy(dtype=float)
    if not np.isfinite(matrix).all() or (matrix < 0).any():
        raise ValueError("Squared losses must be finite and nonnegative")
    groups = {}
    for horizon in (15, 5):
        for month in MONTHS:
            part = paired.loc[(paired.horizon_minutes == horizon) & (paired.month == month)]
            if part.empty:
                raise ValueError("Every frozen month/horizon must contain observations")
            times = pd.DatetimeIndex(part.decision_time)
            if times.has_duplicates or not times.is_monotonic_increasing:
                raise ValueError("Paired decision times must be sorted and unique within month/horizon")
            deltas = np.asarray((times[1:] - times[:-1]) / pd.Timedelta(minutes=1), dtype=float)
            if (deltas < 1).any() or (deltas != np.floor(deltas)).any():
                raise ValueError("Paired observations must lie on a common one-minute grid")
            boundaries = np.r_[0, np.flatnonzero(deltas != 1) + 1, len(part)]
            segments = tuple((int(a), int(b)) for a, b in zip(boundaries[:-1], boundaries[1:]))
            groups[(horizon, month)] = {
                "n": len(part), "segments": segments,
                "ridge": part.squared_loss_ridge.to_numpy(dtype=float),
                "zero": part.squared_loss_zero.to_numpy(dtype=float),
                "historical_mean": part.squared_loss_historical_mean.to_numpy(dtype=float),
            }
    return groups


def block_resample_means(values: np.ndarray, block_length: int, replicates: int,
                         rng: np.random.Generator) -> np.ndarray:
    """Exact block-mean equivalent of explicit paired indices, bounded memory.

    Columns share every sampled start. Draw ceil(n/L) non-circular blocks and
    retain only the needed prefix of the final block. Prefix sums avoid creating
    a B-by-n copy of the observations.
    """
    values = np.asarray(values, dtype=float)
    if (values.ndim != 2 or not values.shape[1] or not np.isfinite(values).all()
            or isinstance(block_length, bool) or not isinstance(block_length, (int, np.integer))
            or block_length <= 0 or len(values) < block_length
            or isinstance(replicates, bool) or not isinstance(replicates, (int, np.integer)) or replicates <= 0):
        raise ValueError("Finite 2D values, n>=positive block_length and positive replicates required")
    n = len(values)
    full, remainder = divmod(n, int(block_length))
    blocks = full + bool(remainder)
    cumulative = np.vstack([np.zeros(values.shape[1]), np.cumsum(values, axis=0)])
    if not np.isfinite(cumulative).all():
        raise ValueError("Block cumulative sums overflowed")
    result = np.empty((replicates, values.shape[1]), dtype=float)
    for first in range(0, replicates, 128):
        last = min(first + 128, replicates)
        starts = rng.integers(0, n - block_length + 1, size=(last - first, blocks))
        ends = starts + block_length
        if remainder:
            ends[:, -1] = starts[:, -1] + remainder
        result[first:last] = (cumulative[ends] - cumulative[starts]).sum(axis=1) / n
    # Preserve exact degeneracy rather than magnifying prefix-sum cancellation.
    constant_columns = np.ptp(values, axis=0) == 0
    result[:, constant_columns] = values[0, constant_columns]
    return result


def _point(group: dict, baseline: str) -> dict:
    # Average paired differences directly to avoid subtracting large close means.
    delta = float(np.mean(group["ridge"] - group[baseline]))
    base = float(np.mean(group[baseline]))
    return {"estimate_mse_delta": delta, "baseline_mse": base,
            "model_mse": float(np.mean(group["ridge"])),
            "relative_mse_delta": None if base == 0 else delta / base}


def _combine_points(entries: list[dict], weights: np.ndarray) -> dict:
    delta = float(sum(w * e["estimate_mse_delta"] for w, e in zip(weights, entries)))
    base = float(sum(w * e["baseline_mse"] for w, e in zip(weights, entries)))
    return {"estimate_mse_delta": delta, "baseline_mse": base,
            "model_mse": float(sum(w * e["model_mse"] for w, e in zip(weights, entries))),
            "relative_mse_delta": None if base == 0 else delta / base}


def _month_draws(group: dict, baseline: str, length: int, replicates: int, seed_sequence):
    sizes = [end - start for start, end in group["segments"]]
    diagnostics = {
        "segment_lengths": sizes, "segment_count": len(sizes), "min_segment_length": min(sizes),
        "n_over_block_length": group["n"] / length, "few_blocks": group["n"] / length < 20,
        "sampled_blocks_per_replicate": sum((n + length - 1) // length for n in sizes),
        "legal_block_start_count": sum(max(n - length + 1, 0) for n in sizes),
        "segment_legal_starts": [max(n - length + 1, 0) for n in sizes],
    }
    if min(sizes) < length:
        return None, diagnostics
    rng = np.random.default_rng(seed_sequence)
    combined = np.zeros((replicates, 2))
    paired_values = np.column_stack((group["ridge"] - group[baseline], group[baseline]))
    for (start, end), size in zip(group["segments"], sizes):
        combined += block_resample_means(paired_values[start:end], length, replicates, rng) * (size / group["n"])
    return combined, diagnostics


def _make_row(scope_type, scope_id, role, aggregation, horizon, baseline, length,
              point, draws, sizes, diagnostics, replicates, seed):
    relative = point["relative_mse_delta"]
    row = {
        "method": "moving_block_bootstrap", "scope_type": scope_type, "scope_id": scope_id,
        "role": role, "aggregation": aggregation, "horizon_minutes": horizon, "baseline": baseline,
        "metric": "squared_loss", "block_length": length, "primary_block": length == 1440,
        "replicates": replicates, "seed": seed, "n_samples": sum(sizes), "month_count": len(sizes),
        **point, "relative_mse_delta_pct": None if relative is None else relative * 100,
        "ci_low": None, "ci_high": None, "relative_ci_low": None, "relative_ci_high": None,
        "relative_ci_low_pct": None, "relative_ci_high_pct": None,
        "status": "estimable" if draws is not None else "not_estimable",
        "reason": None if draws is not None else "continuous_segment_shorter_than_block",
        "relative_null_reason": "zero_baseline_mse" if relative is None else None,
        "zero_denominator_replicates": None,
        "diagnostics_json": json.dumps(diagnostics, sort_keys=True, allow_nan=False),
        "interval_type": "marginal_95_percentile_not_simultaneous",
        "cross_month_assumption": "zero covariance; conditioned on the observed months" if len(sizes) > 1 else None,
    }
    if draws is None:
        return row
    row["ci_low"], row["ci_high"] = np.quantile(draws[:, 0], [0.025, 0.975], method="linear").tolist()
    if np.ptp(draws[:, 0]) == 0:
        row["reason"] = "degenerate_bootstrap_difference"
    # Never drop zero-denominator draws and report a conditional interval.
    invalid = int((draws[:, 1] <= 0).sum())
    row["zero_denominator_replicates"] = invalid
    if not invalid and relative is not None:
        ratios = draws[:, 0] / draws[:, 1]
        row["relative_ci_low"], row["relative_ci_high"] = np.quantile(ratios, [0.025, 0.975], method="linear").tolist()
        row["relative_ci_low_pct"] = row["relative_ci_low"] * 100
        row["relative_ci_high_pct"] = row["relative_ci_high"] * 100
    elif invalid:
        row["relative_null_reason"] = "zero_baseline_in_bootstrap_replicate"
    return row


def run_bootstrap(paired: pd.DataFrame, replicates: int = 2000, seed: int = 42,
                  block_lengths: tuple[int, ...] = BLOCK_LENGTHS) -> pd.DataFrame:
    """Return all 128 planned scopes, including explicit non-estimable rows."""
    if type(replicates) is not int or replicates != 2000 or type(seed) is not int or seed != 42:
        raise ValueError("Bootstrap is frozen to integer replicates=2000 and seed=42")
    if tuple(block_lengths) != BLOCK_LENGTHS or any(type(n) is not int for n in block_lengths):
        raise ValueError("Block lengths are frozen to (60,360,1440,2880)")
    groups = validate_paired(paired)
    rows = []
    for horizon in (15, 5):
        for baseline_id, baseline in enumerate(BASELINES, 1):
            points = {month: _point(groups[horizon, month], baseline) for month in MONTHS}
            for length in block_lengths:
                draws, diagnostics = {}, {}
                for month in MONTHS:
                    year, number = map(int, month.split("-"))
                    stream = np.random.SeedSequence([seed, horizon, baseline_id, length, year, number])
                    draws[month], diagnostics[month] = _month_draws(groups[horizon, month], baseline, length, replicates, stream)
                    rows.append(_make_row("month", month, ROLE_BY_MONTH[month], "month", horizon, baseline,
                                          length, points[month], draws[month], [groups[horizon, month]["n"]],
                                          diagnostics[month], replicates, seed))
                for role in ROLES:
                    months = [m for m in MONTHS if ROLE_BY_MONTH[m] == role]
                    sizes = [groups[horizon, m]["n"] for m in months]
                    for aggregation in ("n_weighted", "month_equal"):
                        weights = np.asarray(sizes if aggregation == "n_weighted" else [1, 1], dtype=float)
                        weights /= weights.sum()
                        combined = None if any(draws[m] is None for m in months) else sum(w * draws[m] for w, m in zip(weights, months))
                        point = _combine_points([points[m] for m in months], weights)
                        diag = {"months": months, "weights": weights.tolist(), "monthly": {m: diagnostics[m] for m in months}}
                        rows.append(_make_row("role", role, role, aggregation, horizon, baseline, length,
                                              point, combined, sizes, diag, replicates, seed))
    if len(rows) != 128:
        raise AssertionError("Frozen bootstrap must produce 128 rows")
    return pd.DataFrame(rows)
