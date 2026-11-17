"""Audited window comparisons; reuse Phase 2C numerical kernels unchanged.

The private Phase 2C helpers below are numerical building blocks, not its
frozen analysis entry point. Equivalence tests protect this explicit adapter.
"""
from __future__ import annotations

import json
import numpy as np
import pandas as pd

from quantforge.diagnostics.block_bootstrap import (
    MONTHS, ROLES, ROLE_BY_MONTH, BLOCK_LENGTHS,
    _point, _combine_points, _month_draws, _make_row,
)
from quantforge.diagnostics.hac_tests import LAGS, _row, _segment_hac_per_month, holm_adjust

WINDOWS = ("expanding", "rolling_1m", "rolling_3m", "rolling_6m")
MODELS = (*WINDOWS, "zero", "historical_mean")
KEY = ("decision_time", "horizon_minutes", "fold", "month", "role")
TIME = ("bar_open_time", "bar_end_exclusive", "feature_available_time", "decision_time",
        "target_start_time", "target_end_time", "label_available_time")


def pair_window_predictions(predictions: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Require complete identical keys before alignment, without an inner join."""
    required = set(KEY) | set(TIME) | {"target_return", "prediction", "model"}
    if (not isinstance(predictions, pd.DataFrame) or predictions.empty
            or predictions.columns.has_duplicates or not required.issubset(predictions)):
        raise ValueError("Window predictions require a nonempty unique-column schema")
    p = predictions
    if p[list(required)].isna().any().any():
        raise ValueError("Missing predictions or metadata are prohibited")
    if set(p.model) != set(MODELS) or set(p.month) != set(MONTHS):
        raise ValueError("Complete frozen models and March--June months required; holdout prohibited")
    for c in ("fold", "horizon_minutes"):
        if not pd.api.types.is_integer_dtype(p[c]) or pd.api.types.is_bool_dtype(p[c]):
            raise ValueError("Integer fold and horizon required")
    if set(p.horizon_minutes) != {5, 15}:
        raise ValueError("Both fixed horizons are required")
    if (not p.role.eq(p.month.map(ROLE_BY_MONTH)).all()
            or not p.fold.eq(p.month.map(dict(zip(MONTHS, range(1, 5))))).all()):
        raise ValueError("Fold/role assignment must match frozen months")
    for c in TIME:
        if not isinstance(p[c].dtype, pd.DatetimeTZDtype) or str(p[c].dt.tz) != "UTC":
            raise ValueError("Temporal fields require explicit UTC dtype")
    for c in ("prediction", "target_return"):
        if (not pd.api.types.is_numeric_dtype(p[c]) or pd.api.types.is_bool_dtype(p[c])
                or pd.api.types.is_complex_dtype(p[c]) or not np.isfinite(p[c]).all()):
            raise ValueError("Targets and predictions must be finite real numbers")
    if p.duplicated([*KEY, "model"]).any():
        raise ValueError("Duplicate prediction key")
    minute, second = pd.Timedelta(minutes=1), pd.Timedelta(seconds=1)
    if (not p.bar_open_time.eq(p.bar_open_time.dt.floor("min")).all()
            or not p.bar_end_exclusive.eq(p.bar_open_time + minute).all()
            or not p.feature_available_time.eq(p.bar_end_exclusive + second).all()
            or not p.decision_time.eq(p.feature_available_time).all()
            or not p.target_start_time.eq(p.bar_end_exclusive).all()
            or not p.target_end_time.eq(p.target_start_time + pd.to_timedelta(p.horizon_minutes, unit="min")).all()
            or not p.label_available_time.eq(p.target_end_time + second).all()
            or not p.month.eq(p.decision_time.dt.strftime("%Y-%m")).all()):
        raise ValueError("Causal timestamp equations or decision-month membership violated")
    starts = p.month.map({m: pd.Timestamp(f"{m}-01", tz="UTC") for m in MONTHS})
    ends = p.month.map({m: pd.Timestamp(f"{m}-01", tz="UTC") + pd.offsets.MonthBegin(1) for m in MONTHS})
    if not p.label_available_time.le(ends).all():
        raise ValueError("Evaluation labels must mature by the fixed month cutoff")
    for c, expected in (("model_fit_time", starts), ("evaluation_as_of", ends)):
        if c in p and (not isinstance(p[c].dtype, pd.DatetimeTZDtype) or str(p[c].dt.tz) != "UTC"
                       or not p[c].eq(expected).all()):
            raise ValueError("Fit and evaluation cutoff must match canonical UTC month boundaries")
    shared = [c for c in (*TIME, "target_return", "model_fit_time", "evaluation_as_of",
                         "source_month", "source_month_manifest_sha256", "development_manifest_sha256") if c in p]
    frames = {m: p.loc[p.model.eq(m)].sort_values(list(KEY)).set_index(list(KEY), drop=False) for m in MODELS}
    reference = frames["expanding"]
    for name, frame in frames.items():
        if not reference.index.equals(frame.index):
            raise ValueError(f"Identical complete evaluation key sets required: {name}")
        if any(not reference[c].eq(frame[c]).all() for c in shared):
            raise ValueError(f"Paired truth or shared metadata differ: {name}")
    if reference.groupby(level=["month", "horizon_minutes"]).ngroups != 8:
        raise ValueError("Every month/horizon must be present")
    wide = reference[list(dict.fromkeys([*KEY, *shared]))].reset_index(drop=True).copy()
    for name, frame in frames.items():
        wide[f"prediction_{name}"] = frame.prediction.to_numpy(dtype=float)
        with np.errstate(over="ignore", invalid="ignore"):
            error = wide[f"prediction_{name}"] - wide.target_return
            wide[f"squared_loss_{name}"] = error ** 2
            wide[f"absolute_loss_{name}"] = error.abs()
        if not np.isfinite(wide[[f"squared_loss_{name}", f"absolute_loss_{name}"]]).all().all():
            raise ValueError("Loss arithmetic overflow")
    wide = wide.sort_values(["horizon_minutes", "month", "decision_time"]).reset_index(drop=True)
    return wide, {
        "status": "passed", "input_rows": len(p), "paired_rows": len(wide), "dropped_rows": 0,
        "pair_key": list(KEY), "models": list(MODELS), "groups": 8,
        "difference_definition": "Rolling loss minus Expanding loss; negative favors Rolling",
        "holdout_accessed": False,
    }


def _groups(wide: pd.DataFrame, window: str) -> dict:
    """Build generic groups with the legacy helper's model-slot alias 'ridge'."""
    result = {}
    for horizon in (15, 5):
        for month in MONTHS:
            group = wide.loc[wide.horizon_minutes.eq(horizon) & wide.month.eq(month)]
            times = pd.DatetimeIndex(group.decision_time)
            deltas = np.asarray((times[1:] - times[:-1]) / pd.Timedelta(minutes=1))
            if group.empty or times.has_duplicates or (deltas < 1).any() or (deltas != np.floor(deltas)).any():
                raise ValueError("Sorted unique minute grid required in every comparison group")
            cuts = np.r_[0, np.flatnonzero(deltas != 1) + 1, len(group)]
            result[horizon, month] = {
                "n": len(group), "segments": tuple((int(a), int(b)) for a, b in zip(cuts[:-1], cuts[1:])),
                "ridge": group[f"squared_loss_{window}"].to_numpy(),
                "expanding": group.squared_loss_expanding.to_numpy(),
            }
    return result


def _annotations(row: dict, window: str) -> dict:
    row["window"] = window
    row["comparison"] = f"{window}_minus_expanding"
    row["primary_contrast"] = (window == "rolling_3m" and row["horizon_minutes"] == 15
                               and row["scope_type"] == "role" and row["role"] == "validation"
                               and row["aggregation"] == "n_weighted")
    return row


def _bootstrap(wide: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for window_id, window in enumerate(WINDOWS[1:], 1):
        groups = _groups(wide, window)
        for horizon in (15, 5):
            points = {m: _point(groups[horizon, m], "expanding") for m in MONTHS}
            for length in BLOCK_LENGTHS:
                draws, diagnostics = {}, {}
                for month in MONTHS:
                    year, number = map(int, month.split("-"))
                    stream = np.random.SeedSequence([42, horizon, window_id, length, year, number])
                    group = groups[horizon, month]
                    draws[month], diagnostics[month] = _month_draws(group, "expanding", length, 2000, stream)
                    rows.append(_annotations(_make_row(
                        "month", month, ROLE_BY_MONTH[month], "month", horizon, "expanding", length,
                        points[month], draws[month], [group["n"]], diagnostics[month], 2000, 42), window))
                for role in ROLES:
                    months = [m for m in MONTHS if ROLE_BY_MONTH[m] == role]
                    sizes = [groups[horizon, m]["n"] for m in months]
                    for aggregation in ("n_weighted", "month_equal"):
                        weights = np.array(sizes if aggregation == "n_weighted" else [1, 1], dtype=float)
                        weights /= weights.sum()
                        combined = None if any(draws[m] is None for m in months) else sum(w * draws[m] for w, m in zip(weights, months))
                        point = _combine_points([points[m] for m in months], weights)
                        diagnostic = {"months": months, "weights": weights.tolist(), "monthly": {m: diagnostics[m] for m in months}}
                        rows.append(_annotations(_make_row(
                            "role", role, role, aggregation, horizon, "expanding", length,
                            point, combined, sizes, diagnostic, 2000, 42), window))
    return pd.DataFrame(rows)


def _hac(wide: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for window in WINDOWS[1:]:
        groups = _groups(wide, window)
        for horizon in (15, 5):
            for lag in LAGS:
                monthly = {}
                for month in MONTHS:
                    group = groups[horizon, month]
                    point = _point(group, "expanding")
                    variance = _segment_hac_per_month(group["ridge"] - group["expanding"], group, lag)
                    monthly[month] = (point, variance)
                    row = _row("month", month, ROLE_BY_MONTH[month], "month", horizon, "expanding", lag,
                               point["estimate_mse_delta"], variance, group["n"], [month],
                               "estimable" if variance is not None else "not_estimable",
                               None if variance is not None else "continuous_segment_not_longer_than_lag",
                               point["relative_mse_delta"])
                    row["diagnostics_json"] = json.dumps({"segments": group["segments"], "fixed_lag": lag,
                        "centering": "within each continuous segment", "cross_gap_covariance": "assumed zero"}, sort_keys=True)
                    rows.append(_annotations(row, window))
                for role in ROLES:
                    months = [m for m in MONTHS if ROLE_BY_MONTH[m] == role]
                    sizes = [groups[horizon, m]["n"] for m in months]
                    for aggregation in ("n_weighted", "month_equal"):
                        weights = np.array(sizes if aggregation == "n_weighted" else [1, 1], dtype=float)
                        weights /= weights.sum()
                        point = _combine_points([monthly[m][0] for m in months], weights)
                        variance = None if any(monthly[m][1] is None for m in months) else float(sum(w*w*monthly[m][1] for w, m in zip(weights, months)))
                        row = _row("role", role, role, aggregation, horizon, "expanding", lag,
                                   point["estimate_mse_delta"], variance, sum(sizes), months,
                                   "estimable" if variance is not None else "not_estimable",
                                   None if variance is not None else "a month contains a short continuous segment",
                                   point["relative_mse_delta"])
                        row["diagnostics_json"] = json.dumps({"months": months, "weights": weights.tolist(),
                            "cross_month_covariance": "assumed zero despite shared training history"}, sort_keys=True)
                        rows.append(_annotations(row, window))
    # Remove frozen Phase 2C family labels; the old entry point is never changed.
    for row in rows:
        for key in list(row):
            if key.startswith(("holm_", "main32_", "global96_")):
                del row[key]
        row.update(holm_primary6_p=None, holm_primary6_reject=None, primary_family_size=6,
                   holm_global144_p=None, holm_global144_reject=None, global_family_size=144)
    primary = [i for i, r in enumerate(rows) if r["horizon_minutes"] == 15 and r["scope_type"] == "role"
               and r["aggregation"] == "n_weighted" and r["lag"] == 60]
    if len(primary) != 6 or len(rows) != 144:
        raise AssertionError("Frozen HAC family sizes differ")
    for indices, name in ((primary, "primary6"), (list(range(len(rows))), "global144")):
        values = holm_adjust([rows[i]["p_value"] for i in indices])
        for i, value in zip(indices, values):
            rows[i][f"holm_{name}_p"] = value
            rows[i][f"holm_{name}_reject"] = None if value is None else value <= .05
    return pd.DataFrame(rows)


def analyze_window_predictions(predictions: pd.DataFrame) -> dict:
    """Return all frozen statistics and an explicit pairing audit."""
    wide, audit = pair_window_predictions(predictions)
    bootstrap, hac = _bootstrap(wide), _hac(wide)
    if len(bootstrap) != 192 or len(hac) != 144:
        raise AssertionError("Incomplete frozen statistical result grid")
    columns = ["scope_type", "scope_id", "role", "aggregation", "horizon_minutes", "window", "baseline",
               "n_samples", "estimate_mse_delta", "baseline_mse", "model_mse", "relative_mse_delta_pct"]
    descriptive = bootstrap.loc[bootstrap.block_length.eq(1440), columns].reset_index(drop=True).copy()
    descriptive["mse_improvement_pct"] = -descriptive.relative_mse_delta_pct
    audit.update(bootstrap_rows=len(bootstrap), hac_rows=len(hac), descriptive_rows=len(descriptive),
                 bootstrap_non_estimable=int(bootstrap.status.ne("estimable").sum()),
                 hac_non_estimable=int(hac.status.ne("estimable").sum()))
    return {"paired_losses": descriptive, "bootstrap_results": bootstrap,
            "hac_results": hac, "pairing_audit": audit}
