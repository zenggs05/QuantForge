"""Chronological splits that audit target maturity and overlapping intervals."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import pandas as pd


@dataclass(frozen=True)
class SplitConfig:
    """Window lengths refer to elapsed UTC minutes, never row counts."""

    mode: Literal["chronological", "expanding", "rolling"] = "expanding"
    train_minutes: int = 720
    eval_minutes: int = 240
    step_minutes: int = 240
    rolling_train_minutes: int = 720
    min_train_samples: int = 100
    min_eval_samples: int = 20
    embargo_minutes: int = 0

    def __post_init__(self) -> None:
        if self.mode not in {"chronological", "expanding", "rolling"}:
            raise ValueError("mode must be chronological, expanding or rolling")
        for name in ("train_minutes", "eval_minutes", "step_minutes", "rolling_train_minutes", "min_train_samples", "min_eval_samples"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if isinstance(self.embargo_minutes, bool) or not isinstance(self.embargo_minutes, int) or self.embargo_minutes < 0:
            raise ValueError("embargo_minutes must be a nonnegative integer")
        if self.step_minutes < self.eval_minutes:
            raise ValueError("step_minutes must be >= eval_minutes to avoid repeated evaluation rows")


@dataclass(frozen=True)
class TemporalSplit:
    train_indices: pd.DatetimeIndex
    eval_indices: pd.DatetimeIndex
    fit_time: pd.Timestamp
    eval_start: pd.Timestamp
    eval_end: pd.Timestamp
    diagnostics: dict = field(default_factory=dict)


def _validate_metadata(samples: pd.DataFrame) -> None:
    if not isinstance(samples.index, pd.DatetimeIndex) or str(samples.index.tz) != "UTC":
        raise ValueError("Sample index must be a timezone-aware UTC DatetimeIndex")
    if samples.empty or samples.index.hasnans or samples.index.has_duplicates or not samples.index.is_monotonic_increasing:
        raise ValueError("Sample index must be nonempty, unique, sorted and free of NaT")
    if not (samples.index == samples.index.floor("min")).all():
        raise ValueError("Sample bar_open_time index must align to UTC minutes")
    columns = (
        "bar_end_exclusive", "feature_available_time", "decision_time",
        "target_start_time", "target_end_time", "label_available_time",
    )
    for name in columns:
        if name not in samples:
            raise ValueError(f"Missing split metadata column: {name}")
        values = samples[name]
        if not isinstance(values.dtype, pd.DatetimeTZDtype) or str(values.dt.tz) != "UTC" or values.isna().any():
            raise ValueError(f"{name} must contain nonmissing timezone-aware UTC timestamps")
    for flag in ("valid_features", "valid_label"):
        if flag in samples and (not pd.api.types.is_bool_dtype(samples[flag]) or not samples[flag].fillna(False).all()):
            raise ValueError(f"All {flag} rows must be valid before splitting")
    if not (samples.bar_end_exclusive == samples.index + pd.Timedelta(minutes=1)).all():
        raise ValueError("bar_end_exclusive must equal bar_open_time plus one minute")
    if (samples.feature_available_time < samples.bar_end_exclusive).any():
        raise ValueError("Features cannot be available before their bar has ended")
    if (samples.feature_available_time > samples.decision_time).any():
        raise ValueError("Future features are unavailable at decision_time")
    if not samples.decision_time.is_monotonic_increasing:
        raise ValueError("Decision times must be chronological")
    if not (samples.target_start_time == samples.bar_end_exclusive).all():
        raise ValueError("target_start_time must equal bar_end_exclusive")
    if (samples.target_end_time <= samples.target_start_time).any():
        raise ValueError("Target intervals must have positive duration")
    if (samples.label_available_time < samples.target_end_time).any():
        raise ValueError("A label cannot be available before its target interval ends")


def make_walk_forward_splits(samples: pd.DataFrame, config: SplitConfig) -> list[TemporalSplit]:
    """Return disjoint chronological evaluation windows and eligible training rows.

    Targets use half-open intervals [start, end); sharing a boundary is allowed.
    Training labels must be available at fit_time and finish no later than the
    first evaluation target's start minus the explicit embargo. The embargo here
    is a conservative pre-evaluation purge extension, not a post-test retraining
    embargo. A later fold may reuse earlier evaluation rows once their labels
    have matured, as in a predeclared walk-forward protocol.

    A final nonempty partial evaluation window is retained and marked in its
    diagnostics. No shuffled fold, implicit row-count gap or fitting occurs here.
    """
    _validate_metadata(samples)
    start = samples.index[0]
    boundary = start + pd.Timedelta(minutes=config.train_minutes)
    observed_end = samples.index[-1] + pd.Timedelta(minutes=1)
    splits = []
    while boundary < observed_end:
        end = boundary + pd.Timedelta(minutes=config.eval_minutes)
        evaluation = samples.loc[(samples.index >= boundary) & (samples.index < end)]
        if len(evaluation) < config.min_eval_samples:
            raise ValueError(
                f"Insufficient evaluation samples for {boundary.isoformat()}: "
                f"{len(evaluation)} < {config.min_eval_samples}"
            )
        if not evaluation.empty:
            first = evaluation.iloc[0]
            fit_time = first.decision_time
            train_start = start if config.mode != "rolling" else max(
                start, boundary - pd.Timedelta(minutes=config.rolling_train_minutes)
            )
            candidates = samples.loc[(samples.index >= train_start) & (samples.index < boundary)]
            decision_known = candidates.decision_time < fit_time
            features_known = candidates.feature_available_time <= fit_time
            mature = candidates.label_available_time <= fit_time
            nonoverlap = candidates.target_end_time <= first.target_start_time
            embargo_cutoff = first.target_start_time - pd.Timedelta(minutes=config.embargo_minutes)
            outside_embargo = candidates.target_end_time <= embargo_cutoff
            allowed = decision_known & features_known & mature & nonoverlap & outside_embargo
            training = candidates.loc[allowed]
            if len(training) < config.min_train_samples:
                raise ValueError(
                    f"Insufficient eligible training samples for {boundary.isoformat()}: "
                    f"{len(training)} < {config.min_train_samples} after maturity/purge/embargo"
                )
            splits.append(TemporalSplit(
                train_indices=pd.DatetimeIndex(training.index.copy()),
                eval_indices=pd.DatetimeIndex(evaluation.index.copy()),
                fit_time=fit_time, eval_start=boundary, eval_end=end,
                diagnostics={
                    "fold": len(splits), "mode": config.mode,
                    "candidate_train_samples": len(candidates), "train_samples": len(training),
                    "eval_samples": len(evaluation), "removed_train_samples": int((~allowed).sum()),
                    "immature_label_samples": int((~mature).sum()),
                    "overlapping_target_samples": int((~nonoverlap).sum()),
                    "embargo_excluded_samples": int((nonoverlap & ~outside_embargo).sum()),
                    "future_feature_or_decision_samples": int((~features_known | ~decision_known).sum()),
                    "train_window_start": train_start.isoformat(),
                    "target_end_cutoff": embargo_cutoff.isoformat(),
                    "eval_window_complete": bool(
                        len(evaluation) == config.eval_minutes
                        and evaluation.index[0] == boundary
                        and evaluation.index[-1] + pd.Timedelta(minutes=1) == end
                    ),
                    "counts_may_overlap": True,
                },
            ))
        if config.mode == "chronological":
            break
        boundary += pd.Timedelta(minutes=config.step_minutes)
    if not splits:
        raise ValueError("No evaluation samples after the configured training window")
    return splits
