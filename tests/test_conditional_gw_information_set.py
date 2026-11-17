"""Maturity spies and independent monthly OLS/HAC/Wald reference checks."""
from pathlib import Path
import tomllib

import numpy as np
import pandas as pd
import pytest
from scipy.stats import chi2

from quantforge.diagnostics.conditional_gw import build_instrument, conditional_gw


@pytest.fixture(scope="module")
def config():
    return tomllib.loads((Path(__file__).resolve().parents[1] /
                          "configs/history-extension.toml").read_text(encoding="utf-8"))


def _labels():
    index = pd.date_range("2021-01-31T23:54:00Z", periods=10, freq="min")
    start = index + pd.Timedelta(minutes=1)
    end = start + pd.Timedelta(minutes=1)
    return pd.DataFrame({"target_start_time": start, "target_end_time": end,
                         "label_available_time": end+pd.Timedelta(seconds=1),
                         "valid_label": True, "target_return": np.arange(1, 11)/100}, index=index)


class _GuardedLabels(pd.DataFrame):
    _metadata = ["cutoff", "reads"]

    @property
    def _constructor(self):
        return _GuardedLabels

    def __getitem__(self, key):
        if isinstance(key, str) and key in ("target_return", "valid_label"):
            availability = super().__getitem__("label_available_time")
            assert availability.le(self.cutoff).all(), "Immature target/validity flag accessed"
            self.reads.append((key, tuple(self.index)))
        return super().__getitem__(key)


def test_build_instrument_never_reads_immature_target_or_validity_flags():
    fit_time = pd.Timestamp("2021-02-01T00:00:00Z")
    guarded = _GuardedLabels(_labels())
    guarded.cutoff, guarded.reads = fit_time, []
    result = build_instrument(guarded, [fit_time])
    assert {name for name, _ in guarded.reads} == {"target_return", "valid_label"}
    assert result.iloc[0].n_labels == 4
    assert result.iloc[0].instrument == pytest.approx(np.sqrt(np.sum((np.arange(1, 5)/100)**2)))
    assert result.iloc[0].max_label_available_time <= fit_time
    assert result.iloc[0].source_month == "2021-01"
    # A plausible implementation mutation is killed for semantic reasons.
    with pytest.raises(AssertionError, match="Immature target/validity"):
        guarded["target_return"]
    with pytest.raises(AssertionError, match="Immature target/validity"):
        guarded["valid_label"]


def test_future_target_mutation_does_not_change_instrument():
    fit_time = pd.Timestamp("2021-02-01T00:00:00Z")
    labels = _labels()
    before = build_instrument(labels, [fit_time])
    future = labels.label_available_time > fit_time
    labels.loc[future, "target_return"] = np.nan
    labels.loc[future, "valid_label"] = False
    pd.testing.assert_frame_equal(before, build_instrument(labels, [fit_time]))


def test_invalid_mature_targets_excluded_and_previous_month_scope_is_exact():
    labels = _labels()
    fit_time = pd.Timestamp("2021-02-01T00:00:00Z")
    labels.loc[labels.index[0], "valid_label"] = False
    labels.loc[labels.index[0], "target_return"] = np.nan
    result = build_instrument(labels, [fit_time]).iloc[0]
    assert result.n_labels == 3
    assert result.instrument == pytest.approx(np.linalg.norm([.02, .03, .04]))
    labels.loc[:, "valid_label"] = False
    with pytest.raises(ValueError, match="No mature valid"):
        build_instrument(labels, [fit_time])


@pytest.mark.parametrize("mutation", ["horizon", "availability", "duplicate", "naive", "not_month_start"])
def test_instrument_rejects_invalid_time_contracts(mutation):
    labels = _labels()
    fit = pd.Timestamp("2021-02-01T00:00:00Z")
    if mutation == "horizon":
        labels["target_end_time"] += pd.Timedelta(minutes=1)
    elif mutation == "availability":
        labels["label_available_time"] = labels.target_start_time
    elif mutation == "duplicate":
        labels = pd.concat([labels, labels.iloc[[-1]]])
    elif mutation == "naive":
        labels.index = labels.index.tz_localize(None)
    elif mutation == "not_month_start":
        fit += pd.Timedelta(days=1)
    with pytest.raises(ValueError):
        build_instrument(labels, [fit])


def test_conditional_gw_matches_independent_ols_score_covariance_and_joint_wald(config):
    rng = np.random.default_rng(config["experiment"]["seed"])
    instrument = np.linspace(.1, 1.1, 56)
    loss = .03 - .08*instrument + rng.normal(scale=.02, size=56)
    lag = config["conditional_gw"]["hac_lag"]
    result = conditional_gw(loss, instrument, hac_lag=lag, bins=config["conditional_gw"]["bins"])
    design = np.column_stack((np.ones(len(loss)), instrument))
    coefficient = np.linalg.solve(design.T@design, design.T@loss)
    scores = design*(loss-design@coefficient)[:, None]
    kernel = np.fromfunction(lambda i,j: np.maximum(1-np.abs(i-j)/(lag+1), 0), (len(loss), len(loss)))
    bread = np.linalg.inv(design.T@design)
    covariance = bread @ scores.T @ kernel @ scores @ bread
    wald = coefficient @ np.linalg.solve(covariance, coefficient)
    np.testing.assert_allclose(result["beta"], coefficient, rtol=1e-13, atol=1e-15)
    np.testing.assert_allclose(result["covariance"], covariance, rtol=1e-13, atol=1e-16)
    np.testing.assert_allclose(result["se"], np.sqrt(np.diag(covariance)), rtol=1e-13)
    assert result["wald"] == pytest.approx(wald, rel=1e-13)
    assert result["p_value"] == pytest.approx(chi2.sf(wald, 2), rel=1e-12)
    assert result["evidence_level"] == "exploratory_not_in_holm"
    assert not result["finite_sample_correction"]


def test_conditional_gw_singular_or_short_data_stay_explicit_null(config):
    singular = conditional_gw(np.arange(56.), np.ones(56), hac_lag=config["conditional_gw"]["hac_lag"])
    assert singular["p_value"] is None and singular["reason"] == "singular_design"
    short = conditional_gw([1., 2.], [1., 2.], hac_lag=config["conditional_gw"]["hac_lag"])
    assert short["p_value"] is None and short["reason"] == "too_few_months"
    zero = conditional_gw(np.zeros(56), np.arange(56.), hac_lag=config["conditional_gw"]["hac_lag"])
    assert zero["p_value"] is None and zero["reason"] == "singular_coefficient_covariance"


def test_quantile_bins_retain_tied_empty_intervals_and_final_upper_edge(config):
    instrument = np.r_[np.zeros(42), np.ones(14)]
    loss = np.arange(56.)
    result = conditional_gw(loss, instrument, hac_lag=config["conditional_gw"]["hac_lag"],
                            bins=config["conditional_gw"]["bins"])
    bins = result["bins"]
    assert len(bins) == 4
    assert bins[0]["n"] == bins[1]["n"] == 0
    assert bins[-1]["n"] == 14 and bins[-1]["upper_inclusive"]
    assert sum(row["n"] for row in bins) == len(loss)
    assert bins[0]["mean_loss"] is None


def test_conditional_gw_rejects_nonfinite_or_unaligned_inputs():
    with pytest.raises(ValueError):
        conditional_gw([1., np.nan], [1., 2.])
    with pytest.raises(ValueError):
        conditional_gw([1., 2.], [1.])
    with pytest.raises(ValueError):
        conditional_gw([1., 2.], [1., 2.], bins=[0., .5, .5, 1.])
