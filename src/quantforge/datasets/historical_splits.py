"""Parameterized monthly folds with a strictly bounded historical entry point.

The legacy research splitter remains unchanged. Its sample and temporal
semantics are preserved here and independently compared on synthetic overlap.
"""

from __future__ import annotations

import re

import pandas as pd

from quantforge import research_policy
from .research_splits import ResearchSplit


def _validate_samples_for_bounds(samples, horizon_minutes, start_bound, end_bound):
    """Apply the legacy metadata contract with explicit rather than global bounds."""
    if not isinstance(samples, pd.DataFrame) or samples.empty:
        raise ValueError("samples must be a nonempty DataFrame")
    index = samples.index
    if (not isinstance(index, pd.DatetimeIndex) or str(index.tz) != "UTC" or index.hasnans
            or not index.is_unique or not index.is_monotonic_increasing
            or not index.equals(index.floor("min"))):
        raise ValueError("samples index must be unique, sorted, minute-aligned UTC timestamps")
    if index[0] < start_bound or index[-1] >= end_bound:
        raise ValueError("Historical sample input exceeds the supplied frozen bounds")
    required = {"bar_end_exclusive", "feature_available_time", "decision_time", "target_start_time",
                "target_end_time", "label_available_time", "valid_features", "valid_label"}
    if not required.issubset(samples.columns):
        raise ValueError(f"samples missing research split metadata: {sorted(required - set(samples))}")
    for name in required - {"valid_features", "valid_label"}:
        values = samples[name]
        if (not isinstance(values.dtype, pd.DatetimeTZDtype) or str(values.dt.tz) != "UTC"
                or values.isna().any()):
            raise ValueError(f"{name} must contain nonmissing UTC timestamps")
    for name in ("valid_features", "valid_label"):
        if not pd.api.types.is_bool_dtype(samples[name]) or samples[name].isna().any():
            raise ValueError(f"{name} must contain explicit booleans")
    end = index + pd.Timedelta(minutes=1)
    if not samples.bar_end_exclusive.eq(end).all() or not samples.target_start_time.eq(end).all():
        raise ValueError("Research bar and target start boundaries disagree")
    if not samples.feature_available_time.eq(end + pd.Timedelta(seconds=1)).all():
        raise ValueError("Research feature availability delay is frozen at one second")
    if not samples.decision_time.eq(samples.feature_available_time).all():
        raise ValueError("Research decision time must equal feature availability")
    if not samples.target_end_time.eq(samples.target_start_time + pd.Timedelta(minutes=horizon_minutes)).all():
        raise ValueError("Research target interval does not match the fixed horizon")
    if not samples.label_available_time.eq(samples.target_end_time + pd.Timedelta(seconds=1)).all():
        raise ValueError("Research label availability delay is frozen at one second")


def _make_splits_internal(samples, evaluate_months, horizon_minutes, eval_cutoff,
                          start_bound, end_bound):
    """Build monthly folds within supplied bounds; not a public data-access API."""
    if (isinstance(horizon_minutes, bool) or not isinstance(horizon_minutes, int)
            or horizon_minutes not in (5, 15)):
        raise ValueError("Research horizons are frozen at 15m primary and 5m auxiliary")
    if eval_cutoff != "month_end":
        raise ValueError("Research evaluation cutoff is frozen at month_end")
    start_bound, end_bound = pd.Timestamp(start_bound), pd.Timestamp(end_bound)
    if (str(start_bound.tz) != "UTC" or str(end_bound.tz) != "UTC"
            or pd.isna(start_bound) or pd.isna(end_bound) or start_bound >= end_bound):
        raise ValueError("Split bounds must be ordered aware UTC timestamps")
    if (not isinstance(evaluate_months, tuple) or not evaluate_months
            or any(not isinstance(m, str) or re.fullmatch(r"\d{4}-\d{2}", m) is None
                   for m in evaluate_months)
            or list(evaluate_months) != sorted(set(evaluate_months))):
        raise ValueError("Evaluation months must be a nonempty sorted unique tuple of YYYY-MM strings")
    bounds = []
    for month in evaluate_months:
        try:
            start = pd.Timestamp(f"{month}-01T00:00:00Z")
        except (ValueError, OverflowError) as exc:
            raise ValueError("Invalid evaluation month") from exc
        end = start + pd.offsets.MonthBegin(1)
        if start < start_bound or end > end_bound:
            raise ValueError("Evaluation month exceeds supplied frozen bounds")
        bounds.append((month, start, end))
    _validate_samples_for_bounds(samples, horizon_minutes, start_bound, end_bound)
    valid = samples.valid_features & samples.valid_label
    splits = []
    for month, start, end in bounds:
        eval_candidate_mask = samples.decision_time.ge(start) & samples.decision_time.lt(end)
        eval_mature = samples.label_available_time.le(end)
        evaluation = samples.loc[eval_candidate_mask & valid & eval_mature]
        if evaluation.empty:
            raise ValueError(f"No valid mature evaluation samples for frozen month {month}")
        fit_time = start
        train_candidate_mask = samples.decision_time.ge(start_bound) & samples.decision_time.lt(start)
        mature = samples.label_available_time.le(fit_time)
        known = samples.feature_available_time.le(fit_time)
        nonoverlap = samples.target_end_time.le(evaluation.target_start_time.iloc[0])
        within_fit = samples.target_end_time.le(fit_time)
        training = samples.loc[train_candidate_mask & valid & mature & known & nonoverlap & within_fit]
        if training.empty:
            raise ValueError(f"No valid mature nonoverlapping training samples for {month}")
        eval_candidates, train_candidates = samples.loc[eval_candidate_mask], samples.loc[train_candidate_mask]
        audit = {
            "month": month, "horizon_minutes": horizon_minutes, "fit_time": fit_time.isoformat(),
            "evaluation_as_of": end.isoformat(), "train_samples": len(training), "eval_samples": len(evaluation),
            "candidate_train_samples": len(train_candidates), "candidate_eval_samples": len(eval_candidates),
            "train_invalid_features": int((train_candidate_mask & ~samples.valid_features).sum()),
            "train_invalid_labels": int((train_candidate_mask & ~samples.valid_label).sum()),
            "train_immature_labels": int((train_candidate_mask & valid & ~mature).sum()),
            "train_purged_overlap": int((train_candidate_mask & valid & ~nonoverlap).sum()),
            "train_target_after_fit": int((train_candidate_mask & valid & ~within_fit).sum()),
            "eval_invalid_features": int((eval_candidate_mask & ~samples.valid_features).sum()),
            "eval_invalid_labels": int((eval_candidate_mask & ~samples.valid_label).sum()),
            "eval_labels_after_cutoff": int((eval_candidate_mask & ~eval_mature).sum()),
            "train_removed_total": len(train_candidates) - len(training),
            "eval_removed_total": len(eval_candidates) - len(evaluation),
            "counts_may_overlap": True, "boundary_basis": "decision_time",
            "last_train_label_available": training.label_available_time.max().isoformat(),
            "first_eval_decision": evaluation.decision_time.iloc[0].isoformat(),
            "last_eval_label_available": evaluation.label_available_time.max().isoformat(),
            "fit_at": "month_start", "evaluation_cutoff": "month_end",
            "role_bucket": f"{start.year}H{1 if start.month <= 6 else 2}",
        }
        splits.append(ResearchSplit(pd.DatetimeIndex(training.index), pd.DatetimeIndex(evaluation.index),
                                    start, start, end, "historical_rolling", audit))
    return splits


def make_historical_splits(samples: pd.DataFrame, evaluate_months: tuple[str, ...],
                           horizon_minutes: int, eval_cutoff: str = "month_end") -> list[ResearchSplit]:
    """Build only historical folds, preserving all mature-label/availability gates."""
    if not isinstance(evaluate_months, tuple):
        raise ValueError("Evaluation months must be a tuple")
    for month in evaluate_months:
        research_policy.ensure_historical_month(month)
    return _make_splits_internal(samples, evaluate_months, horizon_minutes, eval_cutoff,
                                 research_policy.HISTORICAL_START, research_policy.HISTORICAL_END)
