"""Calendar-window subsets of the frozen, maturity-safe research folds.

Features are computed once on all observed development history. This module
limits eligible *training samples* by decision time; it never truncates feature
history, changes evaluation rows, or reimplements the original split contract.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib

import pandas as pd

from quantforge.datasets.research_splits import ResearchSplit, make_research_splits
from quantforge.research_policy import DEVELOPMENT_START
from quantforge.temporal import utc_timestamp

WINDOW_MONTHS = {"expanding": None, "rolling_1m": 1, "rolling_3m": 3, "rolling_6m": 6}


def calendar_window_start(fit_time: object, window_months: int | None = None) -> pd.Timestamp:
    """Subtract whole calendar months from a UTC month-start fit timestamp.

    ``None`` denotes expanding history from the frozen development start. Only
    the predeclared 1/3/6 month candidates are accepted, with no 30-day shortcut.
    The arithmetic helper also accepts synthetic dates for leap-year tests;
    actual research data bounds are enforced by ``make_research_splits``.
    """
    fit = utc_timestamp(fit_time, "fit_time")
    if fit.day != 1 or fit != fit.normalize():
        raise ValueError("fit_time must be a UTC month-start midnight")
    if window_months is None:
        if fit <= DEVELOPMENT_START:
            raise ValueError("Expanding fit_time must follow the development start")
        return DEVELOPMENT_START
    if isinstance(window_months, bool) or not isinstance(window_months, int) or window_months not in (1, 3, 6):
        raise ValueError("Window months are frozen at 1, 3, 6 or None for expanding")
    return fit - pd.DateOffset(months=window_months)


def _index_digest(index: pd.DatetimeIndex) -> str:
    """Hash ordered UTC nanoseconds, independent of pandas storage precision."""
    return hashlib.sha256(index.as_unit("ns").asi8.astype("<i8").tobytes()).hexdigest()


def make_window_splits(samples: pd.DataFrame, horizon_minutes: int) -> dict[str, list[ResearchSplit]]:
    """Return all four frozen windows, each with the same four evaluation folds.

    Rolling sets are filtered from the corresponding original legal expanding
    set, preserving original order and exact maturity/overlap/gap exclusions.
    Empty training windows fail explicitly; evaluation rows are never dropped
    to compensate for a window with insufficient training information.
    """
    originals = make_research_splits(samples, horizon_minutes)
    output: dict[str, list[ResearchSplit]] = {name: [] for name in WINDOW_MONTHS}
    valid = samples.valid_features & samples.valid_label
    for original in originals:
        evaluation = samples.loc[original.eval_indices]
        legal_training = samples.loc[original.train_indices]
        for name, months in WINDOW_MONTHS.items():
            start = calendar_window_start(original.fit_time, months)
            if start < DEVELOPMENT_START:
                raise ValueError("Rolling history would precede the frozen development data")
            indices = pd.DatetimeIndex(legal_training.index[legal_training.decision_time.ge(start)])
            if indices.empty:
                raise ValueError(f"No legal training samples for {name} in {original.audit['month']}")
            candidate = samples.decision_time.ge(start) & samples.decision_time.lt(original.fit_time)
            mature = samples.label_available_time.le(original.fit_time)
            known = samples.feature_available_time.le(original.fit_time)
            nonoverlap = samples.target_end_time.le(evaluation.target_start_time.iloc[0])
            within_fit = samples.target_end_time.le(original.fit_time)
            # This redundant membership check guards the adapter contract rather
            # than trusting an independently recomputed rolling split.
            expected = samples.index[candidate & valid & mature & known & nonoverlap & within_fit]
            if not indices.equals(expected) or not indices.isin(original.train_indices).all():
                raise ValueError("Window training rows disagree with the legal expanding subset")
            training = samples.loc[indices]
            audit = {
                **original.audit,
                "window": name, "window_months": months,
                "train_start": start.isoformat(), "train_end_exclusive": original.fit_time.isoformat(),
                "train_samples": len(indices), "candidate_train_samples": int(candidate.sum()),
                "train_invalid_features": int((candidate & ~samples.valid_features).sum()),
                "train_invalid_labels": int((candidate & ~samples.valid_label).sum()),
                "train_invalid_samples": int((candidate & ~valid).sum()),
                "train_immature_labels": int((candidate & valid & ~mature).sum()),
                "train_unavailable_features": int((candidate & valid & ~known).sum()),
                "train_purged_overlap": int((candidate & valid & ~nonoverlap).sum()),
                "train_target_after_fit": int((candidate & valid & ~within_fit).sum()),
                "train_removed_total": int(candidate.sum()) - len(indices),
                "train_first_decision": training.decision_time.iloc[0].isoformat(),
                "train_last_decision": training.decision_time.iloc[-1].isoformat(),
                "last_train_label_available": training.label_available_time.max().isoformat(),
                "train_indices_sha256": _index_digest(indices),
                "eval_indices_sha256": _index_digest(original.eval_indices),
                "index_hash_encoding": "ordered UTC nanoseconds, little-endian signed int64",
                "expanding_train_samples": len(original.train_indices),
                "expanding_subset": True,
                "feature_history": "full observed development history; not truncated at window start",
            }
            output[name].append(replace(original, train_indices=indices, audit=audit))
    return output
