"""Fixed monthly dependence sensitivities for historical forecast evidence.

These functions neither choose a bandwidth nor alter the primary monthly
pooling. Inputs and all research settings come from the frozen caller.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.stats import norm

from quantforge.diagnostics.forecast_tests import (
    _integer, _number, _vector, newey_west_segment, pool_roles,
)


def _confidence(value):
    value = _number(value, "confidence", minimum=0, strict=True)
    if value >= 1:
        raise ValueError("confidence must be below one")
    return value


def monthly_mean_hac_nw1994(series, L=4, *, confidence=0.95) -> dict:
    """Fixed-L Bartlett HAC on ordered monthly means, with normal intervals.

    L is a frozen monthly bandwidth, not a fitted automatic bandwidth. The
    autocovariance and mean-variance denominators use the full month count.
    """
    values = _vector(series, "monthly means")
    lag = _integer(L, "monthly lag")
    confidence = _confidence(confidence)
    result = newey_west_segment(values, lag, alternative="greater")
    critical = float(norm.ppf((1 + confidence) / 2))
    se = result["se"]
    return {
        **result,
        "method": "monthly_mean_HAC_fixed_Bartlett",
        "sensitivity": "S_dep1",
        "evidence_level": "prespecified_robustness_not_in_holm",
        "bandwidth_selection": "frozen_L_not_estimated",
        "confidence": confidence,
        "ci_low": None if se is None else float(result["mean"] - critical * se),
        "ci_high": None if se is None else float(result["mean"] + critical * se),
        "interval_type": "normal_marginal_not_simultaneous",
        "changes_primary_decision": False,
    }


def monthly_block_bootstrap(series, block_lengths, B=2000, seed=42,
                            *, confidence=0.95) -> list[dict]:
    """Circular monthly blocks, equal-month mean, linear percentile intervals."""
    values = _vector(series, "monthly means")
    draws = _integer(B, "bootstrap draws", 2)
    seed = _integer(seed, "seed")
    confidence = _confidence(confidence)
    lengths = [_integer(v, "block length", 1) for v in block_lengths]
    if not lengths or len(set(lengths)) != len(lengths):
        raise ValueError("block lengths must be nonempty and unique")
    n = len(values)
    if any(length > n for length in lengths):
        raise ValueError("block length exceeds the number of months")
    tails = [(1 - confidence) / 2, (1 + confidence) / 2]
    rows = []
    for length in lengths:
        rng = np.random.default_rng(np.random.SeedSequence([seed, length]))
        blocks = math.ceil(n / length)
        starts = rng.integers(0, n, size=(draws, blocks))
        indices = ((starts[:, :, None] + np.arange(length)) % n).reshape(draws, -1)[:, :n]
        sampled_means = values[indices].mean(axis=1)
        if np.ptp(values) == 0:
            sampled_means.fill(values[0])
        if not np.isfinite(sampled_means).all():
            raise ValueError("bootstrap arithmetic overflowed")
        low, high = np.quantile(sampled_means, tails, method="linear")
        rows.append({
            "method": "circular_monthly_block_bootstrap",
            "sensitivity": "S_dep2",
            "evidence_level": "prespecified_robustness_not_in_holm",
            "estimand": "equal_month_mean",
            "mean": float(values.mean()),
            "month_count": n,
            "block_length": length,
            "blocks_per_draw": blocks,
            "draws": draws,
            "seed": seed,
            "stream_id": length,
            "confidence": confidence,
            "ci_low": float(low),
            "ci_high": float(high),
            "bootstrap_mean": float(sampled_means.mean()),
            "bootstrap_sd": float(sampled_means.std(ddof=1)),
            "quantile_method": "linear",
            "interval_type": "percentile_marginal_not_simultaneous",
            "changes_primary_decision": False,
        })
    return rows


def equal_vs_nweighted_comparison(month_means, month_counts,
                                  *, month_variances=None) -> dict:
    """Compare pooling rules using supplied within-month mean variances.

    Counts alone cannot identify a mean variance. A standalone call without
    variances therefore reports means but no fabricated standard errors/t.
    """
    means = _vector(month_means, "monthly means")
    counts = [_integer(v, "month count", 1) for v in month_counts]
    if len(counts) != len(means):
        raise ValueError("month means and counts must be aligned")
    if month_variances is None:
        variances = [None] * len(means)
    else:
        if len(month_variances) != len(means):
            raise ValueError("month variances must be aligned")
        variances = [None if v is None else _number(v, "month variance", minimum=0)
                     for v in month_variances]
    segments = [dict(mean=float(mean), n=n, variance=variance)
                for mean, n, variance in zip(means, counts, variances, strict=True)]
    outputs = {}
    for pooling in ("month_equal", "n_weighted"):
        pooled = pool_roles(segments, pooling=pooling, alternative="greater")
        outputs[pooling] = {key: pooled[key] for key in
                            ("mean", "variance", "se", "t", "status", "reason", "weights")}
        if any(v is None for v in variances):
            outputs[pooling]["reason"] = "monthly_mean_variances_not_supplied"
    equal, weighted = outputs["month_equal"], outputs["n_weighted"]
    return {
        "sensitivity": "S_dep3",
        "evidence_level": "prespecified_robustness_not_in_holm",
        "month_count": len(means),
        "n_samples": sum(counts),
        "cross_month_covariance": "assumed zero",
        **outputs,
        "mean_difference_equal_minus_nweighted": equal["mean"] - weighted["mean"],
        "t_difference_equal_minus_nweighted": None if equal["t"] is None or weighted["t"] is None
        else equal["t"] - weighted["t"],
        "changes_primary_decision": False,
    }
