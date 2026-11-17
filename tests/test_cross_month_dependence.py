"""Synthetic monthly dependence checks against independent definitions."""
from pathlib import Path
import tomllib

import numpy as np
import pytest
from scipy.stats import norm

from quantforge.diagnostics.cross_month_dependence import (
    equal_vs_nweighted_comparison, monthly_block_bootstrap,
    monthly_mean_hac_nw1994,
)


@pytest.fixture(scope="module")
def config():
    return tomllib.loads((Path(__file__).resolve().parents[1] /
                          "configs/history-extension.toml").read_text(encoding="utf-8"))


def test_monthly_hac_matches_direct_bartlett_quadratic_form(config):
    values = np.array([0.2, -0.3, 0.9, 0.1, -0.1, 0.7, 0.4, 0.3, -0.2])
    lag = config["dependence"]["monthly_lag"]
    centered = values - values.mean()
    kernel = np.fromfunction(lambda i, j: np.maximum(1 - np.abs(i-j)/(lag+1), 0),
                             (len(values), len(values)))
    expected = centered @ kernel @ centered / len(values)**2
    result = monthly_mean_hac_nw1994(values, lag,
                                    confidence=config["dependence"]["confidence"])
    assert result["variance"] == pytest.approx(expected, rel=2e-15)
    assert result["mean"] == values.mean()
    assert result["p_value"] == norm.sf(result["t"])
    assert result["ci_low"] < result["mean"] < result["ci_high"]
    assert not result["changes_primary_decision"]


def test_monthly_ar1_hac_coverage_fixed_seed_and_stopping_band(config):
    settings = config["synthetic_validation"]
    reps, n, burn = (settings[k] for k in
                     ("coverage_replications", "coverage_months", "coverage_burnin"))
    phi = settings["coverage_ar_phi"]
    covered = 0
    for replicate in range(reps):
        rng = np.random.default_rng(np.random.SeedSequence([config["experiment"]["seed"], replicate]))
        state = rng.normal(scale=1/np.sqrt(1-phi**2))
        values = []
        for step, innovation in enumerate(rng.normal(size=burn+n)):
            state = phi*state + innovation
            if step >= burn:
                values.append(state)
        result = monthly_mean_hac_nw1994(values, config["dependence"]["monthly_lag"],
                                        confidence=config["dependence"]["confidence"])
        covered += result["ci_low"] <= 0 <= result["ci_high"]
    coverage = covered / reps
    mc_se = np.sqrt(coverage*(1-coverage)/reps)
    print(f"S_dep1 coverage={coverage:.6f}, MC_SE={mc_se:.6f}, replications={reps}")
    assert settings["coverage_min"] <= coverage <= settings["coverage_max"], (
        f"Frozen monthly AR(1) coverage failed: {coverage}; no seed/DGP retry permitted")


def test_hac_short_and_constant_series_are_explicit(config):
    lag = config["dependence"]["monthly_lag"]
    short = monthly_mean_hac_nw1994(np.arange(lag), lag)
    assert short["status"] == "not_estimable" and short["se"] is None
    constant = monthly_mean_hac_nw1994(np.full(20, 0.2), lag)
    assert constant["status"] == "degenerate" and constant["t"] is None
    assert constant["ci_low"] == constant["ci_high"] == constant["mean"]


def test_circular_bootstrap_matches_explicit_draw_indices_and_is_repeatable(config):
    values = np.sin(np.arange(56)/7) + np.arange(56)/100
    settings = config["dependence"]
    args = (values, settings["block_lengths"], settings["bootstrap_draws"],
            config["experiment"]["seed"])
    first = monthly_block_bootstrap(*args, confidence=settings["confidence"])
    assert first == monthly_block_bootstrap(*args, confidence=settings["confidence"])
    for row in first:
        length = row["block_length"]
        rng = np.random.default_rng(np.random.SeedSequence([args[3], length]))
        starts = rng.integers(0, len(values), size=(args[2], int(np.ceil(len(values)/length))))
        expected = []
        for draw in starts:
            indices = [int((start + offset) % len(values)) for start in draw for offset in range(length)]
            expected.append(np.mean(values[indices[:len(values)]]))
        quantiles = [(1-settings["confidence"])/2, (1+settings["confidence"])/2]
        low, high = np.quantile(expected, quantiles, method="linear")
        assert row["ci_low"] == low and row["ci_high"] == high
        assert row["bootstrap_sd"] == np.std(expected, ddof=1)
        assert row["estimand"] == "equal_month_mean"


def test_circular_bootstrap_wraps_and_retains_constant_degeneracy(config):
    rows = monthly_block_bootstrap(np.full(56, 0.123), config["dependence"]["block_lengths"],
                                    config["dependence"]["bootstrap_draws"], config["experiment"]["seed"])
    assert all(row["ci_low"] == row["ci_high"] == 0.123 for row in rows)
    assert all(row["stream_id"] == row["block_length"] for row in rows)


def test_equal_vs_nweighted_means_and_variance_have_closed_form():
    means = np.array([-1., 1., 2.])
    counts = [10, 20, 30]
    variances = np.array([0.1, 0.4, 0.9])
    result = equal_vs_nweighted_comparison(means, counts, month_variances=variances)
    for name, weights in (("month_equal", np.ones(3)/3), ("n_weighted", np.array(counts)/60)):
        expected_mean = weights @ means
        expected_variance = weights**2 @ variances
        row = result[name]
        assert row["mean"] == pytest.approx(expected_mean)
        assert row["variance"] == pytest.approx(expected_variance)
        assert row["t"] == pytest.approx(expected_mean/np.sqrt(expected_variance))
        assert "p_value" not in row
    assert result["mean_difference_equal_minus_nweighted"] == pytest.approx(-0.5)


def test_equal_counts_match_and_missing_variances_do_not_invent_t():
    result = equal_vs_nweighted_comparison([1., 2.], [10, 10], month_variances=[0.1, 0.2])
    assert result["month_equal"] == result["n_weighted"]
    assert result["t_difference_equal_minus_nweighted"] == 0
    missing = equal_vs_nweighted_comparison([1., 2.], [10, 20])
    assert missing["month_equal"]["mean"] == 1.5
    assert missing["n_weighted"]["t"] is None
    assert missing["n_weighted"]["reason"] == "monthly_mean_variances_not_supplied"


@pytest.mark.parametrize("values,blocks,B", [([], [3], 20), ([1., np.nan], [1], 20),
                                          ([1., 2.], [3], 20), ([1., 2.], [1, 1], 20),
                                          ([1., 2.], [True], 20), ([1., 2.], [1], 1)])
def test_bootstrap_invalid_inputs_fail_before_resampling(values, blocks, B):
    with pytest.raises(ValueError):
        monthly_block_bootstrap(values, blocks, B=B)


def test_pooling_invalid_counts_and_variances_are_rejected():
    for counts in ([0, 1], [True, 1], [1.2, 2], [1]):
        with pytest.raises(ValueError):
            equal_vs_nweighted_comparison([1., 2.], counts)
    with pytest.raises(ValueError):
        equal_vs_nweighted_comparison([1., 2.], [1, 2], month_variances=[0.1, -0.1])
