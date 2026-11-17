"""Frozen decision-time monthly folds with strict fit and evaluation cutoffs."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from quantforge.research_policy import DEVELOPMENT_END, DEVELOPMENT_START, EVALUATION_MONTHS, month_bounds


@dataclass(frozen=True)
class ResearchSplit:
    train_indices: pd.DatetimeIndex
    eval_indices: pd.DatetimeIndex
    fit_time: pd.Timestamp
    eval_start: pd.Timestamp
    eval_end: pd.Timestamp
    role: str
    audit: dict


def _validate_samples(samples: pd.DataFrame, horizon_minutes: int) -> None:
    if not isinstance(samples, pd.DataFrame) or samples.empty:
        raise ValueError("samples must be a nonempty DataFrame")
    index = samples.index
    if (not isinstance(index, pd.DatetimeIndex) or str(index.tz) != "UTC" or index.hasnans
            or not index.is_unique or not index.is_monotonic_increasing or not index.equals(index.floor("min"))):
        raise ValueError("samples index must be unique, sorted, minute-aligned UTC timestamps")
    if index[0] < DEVELOPMENT_START or index[-1] >= DEVELOPMENT_END:
        raise ValueError("Research sample input exceeds the frozen development data bounds")
    required = {"bar_end_exclusive", "feature_available_time", "decision_time", "target_start_time", "target_end_time",
                "label_available_time", "valid_features", "valid_label"}
    if not required.issubset(samples.columns):
        raise ValueError(f"samples missing research split metadata: {sorted(required - set(samples))}")
    for name in required - {"valid_features", "valid_label"}:
        values = samples[name]
        if not isinstance(values.dtype, pd.DatetimeTZDtype) or str(values.dt.tz) != "UTC" or values.isna().any():
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


def make_research_splits(samples: pd.DataFrame, horizon_minutes: int, eval_cutoff: str = "month_end") -> list[ResearchSplit]:
    """Partition by decision_time, with fit_at/month-end fixed before outcomes.

    Accept all rows so warmup/gap/tail exclusions remain visible in the audit.
    Training requires mature labels at the exact month-start fit timestamp and
    nonoverlap with the first actual evaluation target. Evaluation excludes
    labels that mature after its fixed month-end; it never extends the cutoff.
    """
    if isinstance(horizon_minutes, bool) or not isinstance(horizon_minutes, int) or horizon_minutes not in (5, 15):
        raise ValueError("Research horizons are frozen at 15m primary and 5m auxiliary")
    if eval_cutoff != "month_end":
        raise ValueError("Research evaluation cutoff is frozen at month_end")
    _validate_samples(samples, horizon_minutes)
    valid = samples.valid_features & samples.valid_label
    splits = []
    for month in EVALUATION_MONTHS:
        start, end = month_bounds(month)
        eval_candidate_mask = samples.decision_time.ge(start) & samples.decision_time.lt(end)
        eval_mature = samples.label_available_time.le(end)
        evaluation = samples.loc[eval_candidate_mask & valid & eval_mature]
        if evaluation.empty:
            raise ValueError(f"No valid mature evaluation samples for frozen month {month}")
        fit_time = start
        train_candidate_mask = samples.decision_time.ge(DEVELOPMENT_START) & samples.decision_time.lt(start)
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
        }
        splits.append(ResearchSplit(pd.DatetimeIndex(training.index), pd.DatetimeIndex(evaluation.index), start, start, end,
                                    "validation" if month in ("2026-03", "2026-04") else "development_evaluation", audit))
    return splits
