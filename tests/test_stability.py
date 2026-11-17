"""Trailing windows use their final month; analytic regime means are the oracle."""
from pathlib import Path
import tomllib

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from quantforge.diagnostics.stability import giacomini_rossi_fluctuation


@pytest.fixture(scope="module")
def config():
    return tomllib.loads((Path(__file__).resolve().parents[1] /
                          "configs/history-extension.toml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case_index", [0, 1])
def test_regime_change_trailing_curve_matches_analytic_means_and_transition(config, case_index):
    settings = config["synthetic_validation"]
    seed = config["experiment"]["seed"]
    beta = np.asarray(settings["stability_beta_pairs"][case_index])
    means = 2*beta
    noise_sd = settings["stability_noise_sd"]
    streams = settings["stability_stream_ids"]
    n, change, window = (settings[k] for k in
                          ("stability_months", "stability_break_month", "stability_window_months"))
    # AP10 calibrates each single-regime trailing-window standard deviation
    # from an independent long run. The decimal seed labels are frozen D-023
    # stream identifiers and are intentionally distinct from the break path.
    sigma_r = []
    for regime, calibration_seed in enumerate((202611051, 202611052)):
        rng = np.random.default_rng(calibration_seed)
        calibration_c = 2*(beta[regime] + rng.normal(
            scale=noise_sd, size=settings["stability_mc_samples"]))
        calibration_r = np.lib.stride_tricks.sliding_window_view(
            calibration_c, window).mean(axis=1)
        sigma_r.append(float(calibration_r.std(ddof=1)))
        assert np.isfinite(sigma_r[-1]) and sigma_r[-1] > 0
    for regime in range(2):
        rng = np.random.default_rng(np.random.SeedSequence([seed, case_index, streams[regime]]))
        draws = 2*(beta[regime] + rng.normal(scale=noise_sd, size=settings["stability_mc_samples"]))
        se = draws.std(ddof=1)/np.sqrt(len(draws))
        assert se <= settings["stability_max_relative_mean_se"]*abs(means[regime])
        assert abs(draws.mean()-means[regime]) <= settings["stability_pointwise_se_multiple"]*se
        print(f"stability case={case_index} regime={regime} MC_mean={draws.mean():.12g} MC_SE={se:.12g}")
    months = np.arange(1, n+1)
    rng = np.random.default_rng(np.random.SeedSequence([seed, case_index, streams[2]]))
    c = 2*(np.where(months < change, beta[0], beta[1]) + rng.normal(scale=noise_sd, size=n))
    result = giacomini_rossi_fluctuation(c, window, times=months)
    ending = result.window_end.to_numpy()
    before = np.clip(change-ending+window-1, 0, window)
    expectation = (before*means[0]+(window-before)*means[1])/window
    error = np.abs(result.signed_mean.to_numpy()-expectation)
    before_weight = before/window
    after_weight = 1-before_weight
    mixed_se = np.sqrt(before_weight**2*sigma_r[0]**2
                       + after_weight**2*sigma_r[1]**2)
    T_valid = len(result)
    bound = float(norm.ppf(1-0.01/(2*T_valid)))
    z = error/mixed_se
    exceedances = np.flatnonzero(z > bound)
    print(f"AP10 case={case_index} T={T_valid} bound={bound:.17g} "
          f"max_abs_error_over_SE={z.max():.17g} "
          f"sigma_r={sigma_r!r} "
          f"exceedance_count={len(exceedances)} "
          f"exceedance_months={ending[exceedances].tolist()} "
          f"midpoint_error={abs(ending[np.argmin(np.abs(result.signed_mean.to_numpy()-means.mean()))]-change)} "
          f"abs_square_maxdiff={np.max(np.abs(result.absolute_mean.to_numpy()**2-result.signed_mean.to_numpy()**2)):.3g}")
    assert not len(exceedances)
    midpoint = means.mean()
    transition = ending[np.argmin(np.abs(result.signed_mean.to_numpy()-midpoint))]
    assert abs(transition-change) <= window/2
    np.testing.assert_allclose(result.absolute_mean.to_numpy()**2,
                               result.signed_mean.to_numpy()**2,
                               rtol=0, atol=settings["stability_abs_square_tolerance"])
    assert result.window_end.iloc[0] == window
    assert result.window_start.iloc[0] == 1
    assert not any("p_value" in column for column in result)
    assert set(result.evidence_level) == {"exploratory_descriptive"}


def test_trailing_means_match_hand_convolution_and_keep_timestamp_labels():
    values = np.array([2., 2., -2., -2., -2.])
    times = pd.date_range("2021-01-01", periods=5, freq="MS", tz="UTC")
    result = giacomini_rossi_fluctuation(values, 2, times=times)
    np.testing.assert_array_equal(result.signed_mean, [2., 0., -2., -2.])
    np.testing.assert_array_equal(result.absolute_mean, [2., 0., 2., 2.])
    assert list(result.window_end) == list(times[1:])
    assert list(result.window_start) == list(times[:-1])


@pytest.mark.parametrize("values,window,times", [([], 2, None), ([1., 2.], 3, None),
                                                ([1., np.nan], 1, None), ([1., 2.], True, None),
                                                ([1., 2.], 1, [2, 1]), ([1., 2.], 1, [1, 1])])
def test_invalid_windows_or_time_labels_are_rejected(values, window, times):
    with pytest.raises(ValueError):
        giacomini_rossi_fluctuation(values, window, times=times)
