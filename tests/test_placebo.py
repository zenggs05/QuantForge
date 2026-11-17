"""Synthetic placebo tests; no real files, Git calls, network or model fitting."""
from __future__ import annotations

import copy
import json

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm
from threadpoolctl import threadpool_limits

from quantforge.diagnostics.hac_tests import segment_hac_variance
from quantforge.diagnostics.placebo import (
    align_future_targets,
    sign_flip_placebo,
    time_shift_placebo,
    utc_block_signs,
)


def synthetic_settings(draws=200):
    return {"sign_flip_block_minutes": 60, "sign_flip_draws": draws,
            "time_shift_minutes": 1440, "quantiles": [0.025, 0.05, 0.5, 0.95, 0.975],
            "mean_abs_limit": 0.3, "sd_min": 0.8, "sd_max": 1.25,
            "lags": [60], "include_llsw": True, "pooling": "n_weighted"}


def synthetic_months(n=5000):
    rng = np.random.default_rng(470)
    return [{"times": pd.date_range(f"2026-{month:02}-01T00:00:01Z", periods=n, freq="min"),
             "y": rng.normal(size=n), "f": rng.normal(size=n)*0.1,
             "month_index": month-3, "month": f"2026-{month:02}"}
            for month in (3, 4)]


def call(method, monthly, settings):
    with threadpool_limits(limits=1):
        return method(monthly, horizon=15, role_id=0, seed=42, settings=settings,
                      inference={"llsw_constant": 1.3}, evidence_level="synthetic")


def test_utc_hour_signs_are_deterministic_and_share_blocks():
    times = pd.date_range("2026-03-01T00:37:01Z", periods=240, freq="min")
    actual = utc_block_signs(times, block_minutes=60, seed=42, horizon=15, role_id=0, month_index=0, draw=7)
    rng = np.random.default_rng(np.random.SeedSequence([42, 15, 0, 0, 7]))
    blocks = times.as_unit("ns").asi8//pd.Timedelta(hours=1).value
    _, inverse = np.unique(blocks, return_inverse=True)
    expected = (rng.integers(0, 2, size=len(np.unique(blocks)))*2-1)[inverse]
    np.testing.assert_array_equal(actual, expected)
    np.testing.assert_array_equal(actual, utc_block_signs(times, block_minutes=60, seed=42, horizon=15, role_id=0, month_index=0, draw=7))
    assert set(actual) == {-1, 1}
    for block in np.unique(blocks):
        assert np.unique(actual[blocks == block]).size == 1


def test_seed_id_changes_reproducibly_change_sign_sequences():
    times = pd.date_range("2026-03-01T00:00:01Z", periods=6000, freq="min")
    common = dict(block_minutes=60, seed=42, horizon=15, role_id=0, month_index=0, draw=0)
    original = utc_block_signs(times, **common)
    for key in ("seed", "horizon", "role_id", "month_index", "draw"):
        changed = {**common, key: common[key]+1}
        assert not np.array_equal(original, utc_block_signs(times, **changed))


def test_sign_flip_results_byte_deterministic_and_source_immutable():
    monthly, settings = synthetic_months(2000), synthetic_settings(40)
    before = copy.deepcopy(monthly)
    a, b = call(sign_flip_placebo, monthly, settings), call(sign_flip_placebo, monthly, settings)
    assert json.dumps(a, sort_keys=True).encode() == json.dumps(b, sort_keys=True).encode()
    assert len(a) == 2 and {r["bandwidth"] for r in a} == {"60", "LLSW"}
    for row in a:
        assert row["evidence_level"] == "synthetic" and row["draws"] == 40
        assert row["status"] == "estimable" and 0 < row["empirical_one_sided_p"] <= 1
    for left, right in zip(monthly, before):
        np.testing.assert_array_equal(left["y"], right["y"])
        np.testing.assert_array_equal(left["f"], right["f"])
        pd.testing.assert_index_equal(left["times"], right["times"])


def test_synthetic_null_sign_flip_t_is_approximately_standard_normal():
    rows = call(sign_flip_placebo, synthetic_months(12000), synthetic_settings())
    for row in rows:
        assert abs(row["placebo_t_mean"]) < 0.3
        assert 0.8 <= row["placebo_t_sd"] <= 1.25
        assert row["pipeline_warning"] is False
        quantiles = json.loads(row["quantiles_json"])
        assert quantiles["0.025"] < -1
        assert quantiles["0.975"] > 1


def test_sign_flip_matches_explicit_legacy_hac_and_plus_one_p():
    months, settings = synthetic_months(1200), synthetic_settings(12)
    settings["include_llsw"] = False
    result = call(sign_flip_placebo, months, settings)[0]
    stats = []
    for draw in range(settings["sign_flip_draws"]):
        values = [2*m["f"]*m["y"]*utc_block_signs(m["times"], block_minutes=60, seed=42,
                   horizon=15, role_id=0, month_index=m["month_index"], draw=draw) for m in months]
        mean = sum(v.mean()/2 for v in values)
        variance = sum(segment_hac_variance(v, 60)/4 for v in values)
        stats.append(mean/np.sqrt(variance))
    np.testing.assert_allclose(result["placebo_t_mean"], np.mean(stats), rtol=1e-12)
    np.testing.assert_allclose(result["placebo_t_sd"], np.std(stats, ddof=1), rtol=1e-12)
    expected = (1+sum(t >= result["observed_t"] for t in stats))/(len(stats)+1)
    assert result["empirical_one_sided_p"] == expected
    assert result["observed_p"] == pytest.approx(norm.sf(result["observed_t"]), rel=1e-15)


def test_time_shift_aligns_exact_timestamps_instead_of_rows():
    index = pd.date_range("2026-03-01T00:00:01Z", periods=3000, freq="min")
    index = index.delete([17, 1441, 1500])
    y = np.arange(len(index), dtype=float)+100
    f = -np.arange(len(index), dtype=float)
    result = align_future_targets(index, y, f, shift_minutes=1440)
    assert (result["target_times"] - result["times"] == pd.Timedelta(days=1)).all()
    np.testing.assert_array_equal(result["f"], f[result["source_indices"]])
    np.testing.assert_array_equal(result["y"], y[result["target_indices"]])
    assert not np.array_equal(result["target_indices"], result["source_indices"]+1440)
    assert result["times"][-1] == index[-1]-pd.Timedelta(days=1)
    assert result["times"].is_unique


def test_shift_is_not_circular_and_cannot_cross_month():
    times = pd.date_range("2026-03-31T23:50:01Z", periods=10, freq="min")
    result = align_future_targets(times, np.arange(10), np.arange(10), shift_minutes=1440)
    assert len(result["times"]) == 0
    with pytest.raises(ValueError, match="monthly"):
        align_future_targets(times.append(pd.date_range("2026-04-01T00:00:01Z", periods=2, freq="min")),
                             np.arange(12), np.arange(12), shift_minutes=1)


def test_time_shift_statistics_match_manual_hac():
    months, settings = synthetic_months(5000), synthetic_settings(10)
    row = call(time_shift_placebo, months, settings)[0]
    values = [2*m["f"][:-1440]*m["y"][1440:] for m in months]
    expected = sum(v.mean()/2 for v in values)/np.sqrt(sum(segment_hac_variance(v, 60)/4 for v in values))
    assert row["shifted_t"] == pytest.approx(expected, rel=1e-13)
    assert row["shifted_n_samples"] == 2*(5000-1440)
    assert row["n_samples"] == 10000


def test_short_shift_reports_undefined_without_dropping_month():
    rows = call(time_shift_placebo, synthetic_months(100), synthetic_settings(10))
    assert all(r["shifted_t"] is None and r["shifted_n_samples"] == 0 for r in rows)
    assert all(r["status"] == "not_estimable" for r in rows)


def test_degenerate_and_short_placebos_warn_without_fake_t():
    months = synthetic_months(1000)
    for m in months:
        m["f"][:] = 0
    rows = call(sign_flip_placebo, months, synthetic_settings(10))
    assert all(r["pipeline_warning"] and r["placebo_t_mean"] is None for r in rows)
    short = call(sign_flip_placebo, synthetic_months(3), synthetic_settings(10))
    assert all(r["status"] == "not_estimable" for r in short)


def test_gap_segments_are_not_compressed_in_hac():
    months = synthetic_months(2000)
    for m in months:
        m["times"] = m["times"].delete(slice(800, 900))
        m["y"] = np.delete(m["y"], slice(800, 900))
        m["f"] = np.delete(m["f"], slice(800, 900))
    settings = synthetic_settings(10)
    row = call(sign_flip_placebo, months, settings)[0]
    diffs = [2*m["y"]*m["f"] for m in months]
    variances = [(800/1900)**2*segment_hac_variance(x[:800], 60)+(1100/1900)**2*segment_hac_variance(x[800:], 60) for x in diffs]
    t = np.mean([x.mean() for x in diffs])/np.sqrt(sum(v/4 for v in variances))
    assert row["observed_t"] == pytest.approx(t, rel=1e-13)


@pytest.mark.parametrize("mutation", ["nan", "length", "unsorted", "naive", "duplicate", "wrong_month", "wrong_order", "nonminute"])
def test_invalid_month_inputs_are_rejected(mutation):
    months = synthetic_months(1000)
    m = months[0]
    if mutation == "nan": m["y"][1] = np.nan
    elif mutation == "length": m["f"] = m["f"][:-1]
    elif mutation == "unsorted": m["times"] = m["times"][::-1]
    elif mutation == "naive": m["times"] = m["times"].tz_localize(None)
    elif mutation == "duplicate": m["times"] = m["times"].take([0, 0, *range(2,1000)])
    elif mutation == "wrong_month": m["month"] = "2026-05"
    elif mutation == "wrong_order": months.reverse()
    elif mutation == "nonminute": m["times"] = pd.date_range("2026-03-01T00:00:01Z", periods=1000, freq="61s")
    with pytest.raises(ValueError):
        call(sign_flip_placebo, months, synthetic_settings(10))


@pytest.mark.parametrize("key,value", [("sign_flip_draws", 1), ("sign_flip_block_minutes", 0),
    ("time_shift_minutes", 0), ("pooling", "unknown"), ("quantiles", [0.9,0.1]),
    ("include_llsw", "yes"), ("lags", [-1]), ("sd_min", 5)])
def test_invalid_settings_are_rejected(key, value):
    settings = {**synthetic_settings(10), key: value}
    with pytest.raises(ValueError):
        call(sign_flip_placebo, synthetic_months(200), settings)
