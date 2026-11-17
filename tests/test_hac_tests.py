import numpy as np
import pandas as pd
import pytest

from quantforge.diagnostics.hac_tests import run_hac, segment_hac_variance, holm_adjust, Z975
from tests.test_block_bootstrap import paired


def test_fixed_shape_and_lag_contract():
    result = run_hac(paired())
    assert len(result) == 96 and result.lag.nunique() == 3
    assert set(result.loc[result.lag == 60, "status"]) == {"estimable"}
    assert set(result.loc[result.lag == 240, "status"]) == {"not_estimable"}
    assert result.equals(run_hac(paired()))


def test_constant_delta_is_degenerate_not_significant():
    frame = paired()
    frame["squared_loss_zero"] = 1.0
    frame["squared_loss_ridge"] = 0.875
    result = run_hac(frame)
    month = result[(result.lag == 60) & (result.scope_type == "month") & (result.baseline == "zero")]
    np.testing.assert_array_equal(month.estimate_mse_delta, -.125)
    assert set(month.reason) == {"degenerate_variance"}
    assert month.p_value.isna().all()
    np.testing.assert_array_equal(month.ci_low, -.125)
    np.testing.assert_array_equal(month.ci_high, -.125)
    np.testing.assert_array_equal(month.se, 0)


def test_frozen_override_rejected():
    with pytest.raises(ValueError, match="frozen"):
        run_hac(paired(), lags=(60,))
    from quantforge.diagnostics.block_bootstrap import run_bootstrap
    with pytest.raises(ValueError, match="frozen"):
        run_bootstrap(paired(), replicates=100)


def test_hac_matches_direct_manual_bartlett_formula_and_holm_counts_undefined():
    values = np.arange(20, dtype=float) / 10
    centered = values - values.mean()
    lag = 3
    expected_lrv = np.dot(centered, centered) / len(values)
    for k in range(1, lag + 1):
        expected_lrv += 2 * (1 - k / (lag + 1)) * np.dot(centered[k:], centered[:-k]) / len(values)
    assert segment_hac_variance(values, lag) == pytest.approx(expected_lrv / len(values))
    assert holm_adjust([0.001, 0.02, None, 0.5]) == [0.004, 0.06, None, 1.0]
    result = run_hac(paired())
    assert result.main32_family_size.eq(32).all()
    assert result.global96_family_size.eq(96).all()
    assert result.main32_undefined_count.eq((result.loc[result.lag == 60, "p_value"].isna()).sum()).all()


@pytest.mark.parametrize("p", [float("nan"), float("inf"), -0.01, 1.01, True])
def test_holm_rejects_invalid_values(p):
    with pytest.raises(ValueError, match="Holm"):
        holm_adjust([.01, p])


def test_holm_ties_order_and_all_undefined():
    assert holm_adjust([.04, .001, .04]) == [.08, .003, .08]
    assert holm_adjust([None] * 4) == [None] * 4
    assert holm_adjust([0.0, 1.0, None]) == [0.0, 1.0, None]


def test_lag_zero_iid_variance_short_segment_and_ar1_direction():
    rng = np.random.default_rng(44)
    white = rng.normal(size=3000)
    assert segment_hac_variance(white, 0) == pytest.approx(white.var(ddof=0) / len(white))
    assert segment_hac_variance(white[:60], 60) is None
    ar = np.zeros(3000)
    for i in range(1, len(ar)):
        ar[i] = .85 * ar[i - 1] + white[i]
    # A fixed synthetic autocorrelated process, not a market significance claim.
    assert segment_hac_variance(ar, 60) > segment_hac_variance(ar, 0) * 3


def test_normal_ci_and_role_variance_use_declared_weights():
    # Deliberately unequal month sizes and nonconstant paired differences.
    frame = paired(300)
    frame = frame.loc[~((frame.month == "2026-04") & (frame.decision_time >= pd.Timestamp("2026-04-01T04:10Z")))].copy()
    offset = np.arange(len(frame)) % 101
    frame["squared_loss_ridge"] = 1 + offset / 200
    result = run_hac(frame)
    selected = result[(result.horizon_minutes == 15) & (result.baseline == "zero") & (result.lag == 60)]
    march = selected[selected.scope_id == "2026-03"].iloc[0]
    april = selected[selected.scope_id == "2026-04"].iloc[0]
    for row in (march, april):
        assert row.ci_low == pytest.approx(row.estimate_mse_delta - Z975 * row.se)
        assert row.ci_high == pytest.approx(row.estimate_mse_delta + Z975 * row.se)
    role = selected[(selected.scope_type == "role") & (selected.role == "validation")]
    weighted = role[role.aggregation == "n_weighted"].iloc[0]
    equal = role[role.aggregation == "month_equal"].iloc[0]
    weights = np.array([300, 250]) / 550
    assert weighted.variance == pytest.approx(weights[0] ** 2 * march.variance + weights[1] ** 2 * april.variance)
    assert equal.variance == pytest.approx((march.variance + april.variance) / 4)
    assert equal.estimate_mse_delta == pytest.approx((march.estimate_mse_delta + april.estimate_mse_delta) / 2)


def test_gap_hac_is_segmented_and_does_not_bridge_gap():
    frame = paired(300)
    mask = frame.month.eq("2026-03") & frame.decision_time.ge(pd.Timestamp("2026-03-01T02:30Z"))
    frame.loc[mask, "decision_time"] += pd.Timedelta(minutes=20)
    frame["squared_loss_ridge"] = 2 + (np.arange(len(frame)) % 19) / 100
    result = run_hac(frame)
    row = result[(result.horizon_minutes == 15) & (result.baseline == "zero")
                 & (result.lag == 60) & (result.scope_id == "2026-03")].iloc[0]
    group = frame[(frame.horizon_minutes == 15) & (frame.month == "2026-03")]
    d = (group.squared_loss_ridge - group.squared_loss_zero).to_numpy()
    expected = .25 * segment_hac_variance(d[:150], 60) + .25 * segment_hac_variance(d[150:], 60)
    assert row.variance == pytest.approx(expected)
    short = result[(result.lag == 240) & (result.scope_id == "2026-03")]
    assert short.status.eq("not_estimable").all()
    assert short.p_value.isna().all()
