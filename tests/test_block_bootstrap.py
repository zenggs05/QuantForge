import json
import numpy as np
import pandas as pd
import pytest

from quantforge.diagnostics.block_bootstrap import run_bootstrap, block_resample_means


def paired(n=80):
    rows = []
    for month, role in zip(("2026-03", "2026-04", "2026-05", "2026-06"),
                           ("validation", "validation", "development_evaluation", "development_evaluation")):
        for horizon in (15, 5):
            for i, time in enumerate(pd.date_range(f"{month}-01", periods=n, freq="min", tz="UTC")):
                base = 1 + (i % 7) / 100
                rows.append({"month": month, "role": role, "horizon_minutes": horizon, "decision_time": time,
                             "squared_loss_zero": base, "squared_loss_historical_mean": base * 1.1,
                             "squared_loss_ridge": base - 0.1})
    return pd.DataFrame(rows)


def test_fixed_shape_reproducibility_and_constant_difference():
    one = run_bootstrap(paired())
    two = run_bootstrap(paired())
    assert len(one) == 128 and one.equals(two)
    assert set(one.status) == {"estimable", "not_estimable"}
    estimable = one[(one.status == "estimable") & (one.baseline == "zero")]
    np.testing.assert_allclose(estimable.estimate_mse_delta, -0.1)
    np.testing.assert_allclose(estimable.ci_low, -0.1)


def test_short_segments_are_explicit_and_invalid_input_rejected():
    short = run_bootstrap(paired(20))
    assert set(short.status) == {"not_estimable"}
    duplicate = pd.concat([paired(), paired().iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="sorted|unique"):
        run_bootstrap(duplicate)
    holdout = paired()
    holdout.loc[0, "month"] = "2026-07"
    with pytest.raises(ValueError, match="March"):
        run_bootstrap(holdout)


def test_gap_is_not_crossed():
    frame = paired()
    target = (frame.month == "2026-03") & (frame.horizon_minutes == 15)
    frame.loc[target, "decision_time"] = frame.loc[target, "decision_time"].where(
        frame.loc[target, "decision_time"] < pd.Timestamp("2026-03-01T00:40Z"),
        frame.loc[target, "decision_time"] + pd.Timedelta(minutes=2))
    result = run_bootstrap(frame)
    assert set(result.loc[(result.horizon_minutes == 15) & (result.scope_id == "2026-03"), "status"]) == {"not_estimable"}


def test_prefix_sum_sampler_equals_explicit_paired_indices_with_truncated_last_block():
    values = np.column_stack((np.arange(17) ** 2, np.arange(17) + 5)).astype(float)
    length, count = 5, 300
    rng = np.random.default_rng(123)
    actual = block_resample_means(values, length, count, rng)
    starts = np.random.default_rng(123).integers(0, 17 - length + 1, size=(count, 4))
    explicit = np.array([values[np.concatenate([np.arange(s, s + length) for s in draw])[:17]].mean(axis=0)
                         for draw in starts])
    np.testing.assert_allclose(actual, explicit, rtol=1e-14, atol=1e-14)
    changed_seed = block_resample_means(values, length, count, np.random.default_rng(124))
    assert not np.array_equal(actual, changed_seed)


def test_relative_interval_resamples_denominator_with_identical_block_indices():
    frame = paired(80)
    offsets = np.tile(np.arange(80), 8)
    frame["squared_loss_zero"] = 1.0 + offsets / 15
    frame["squared_loss_ridge"] = frame.squared_loss_zero + offsets ** 2 / 800
    actual = run_bootstrap(frame)
    row = actual[(actual.horizon_minutes == 15) & (actual.baseline == "zero")
                 & (actual.scope_id == "2026-03") & (actual.block_length == 60)].iloc[0]
    selected = frame[(frame.horizon_minutes == 15) & (frame.month == "2026-03")]
    d = (selected.squared_loss_ridge - selected.squared_loss_zero).to_numpy()
    base = selected.squared_loss_zero.to_numpy()
    rng = np.random.default_rng(np.random.SeedSequence([42, 15, 1, 60, 2026, 3]))
    starts = rng.integers(0, 21, size=(2000, 2))
    indices = np.array([np.r_[np.arange(a, a + 60), np.arange(b, b + 20)] for a, b in starts])
    ratios = d[indices].mean(axis=1) / base[indices].mean(axis=1)
    expected = np.quantile(ratios, [.025, .975], method="linear")
    np.testing.assert_allclose([row.relative_ci_low, row.relative_ci_high], expected, rtol=1e-13)
    wrong_fixed_denominator = np.quantile(d[indices].mean(axis=1), [.025, .975]) / base.mean()
    assert not np.allclose(expected, wrong_fixed_denominator)
    diagnostic = json.loads(row.diagnostics_json)
    assert diagnostic["legal_block_start_count"] == 21
    assert diagnostic["sampled_blocks_per_replicate"] == 2
    assert diagnostic["few_blocks"] is True


def test_full_fixed_spec_128_rows_all_lengths_estimable_without_real_data():
    frame = paired(3000)
    result = run_bootstrap(frame)
    assert len(result) == 128
    assert result.status.eq("estimable").all()
    assert result.ci_low.notna().all()
    assert result.ci_high.notna().all()
    assert result.block_length.unique().tolist() == [60, 360, 1440, 2880]
    assert result.replicates.eq(2000).all()
    assert result.seed.eq(42).all()


def test_identical_models_zero_difference_and_zero_baseline_null_ratio():
    frame = paired()
    frame["squared_loss_ridge"] = 0.0
    frame["squared_loss_zero"] = 0.0
    result = run_bootstrap(frame)
    zero = result[(result.baseline == "zero") & (result.status == "estimable")]
    np.testing.assert_array_equal(zero.estimate_mse_delta, 0)
    np.testing.assert_array_equal(zero.ci_low, 0)
    np.testing.assert_array_equal(zero.ci_high, 0)
    assert zero.relative_mse_delta.isna().all()
    assert zero.relative_ci_low.isna().all()
    assert zero.zero_denominator_replicates.eq(2000).all()
    assert zero.reason.eq("degenerate_bootstrap_difference").all()


def test_role_weighting_ratio_of_weighted_means_and_unrelated_scope_invariance():
    frame = paired(120)
    frame = frame.loc[~((frame.month == "2026-04") & (frame.decision_time >= pd.Timestamp("2026-04-01T01:00Z")))].copy()
    frame["squared_loss_zero"] = np.where(frame.month == "2026-03", 2.0, 4.0)
    frame["squared_loss_ridge"] = np.where(frame.month == "2026-03", 1.0, 6.0)
    result = run_bootstrap(frame)
    role = result[(result.horizon_minutes == 15) & (result.baseline == "zero")
                  & (result.block_length == 60) & (result.scope_type == "role") & (result.role == "validation")]
    weighted = role[role.aggregation == "n_weighted"].iloc[0]
    equal = role[role.aggregation == "month_equal"].iloc[0]
    assert weighted.estimate_mse_delta == pytest.approx(0)
    assert weighted.relative_mse_delta == pytest.approx(0)
    assert equal.estimate_mse_delta == pytest.approx(.5)
    assert equal.relative_mse_delta == pytest.approx(1 / 6)
    assert equal.relative_ci_low == pytest.approx(1 / 6)
    # Explicit month SeedSequence streams isolate validation from other roles.
    changed = frame.copy()
    changed.loc[changed.role == "development_evaluation", "squared_loss_ridge"] *= 4
    second = run_bootstrap(changed)
    pd.testing.assert_frame_equal(result.loc[result.role == "validation"].reset_index(drop=True),
                                  second.loc[second.role == "validation"].reset_index(drop=True), check_exact=True)


@pytest.mark.parametrize("failure", ["naive", "unsorted", "nan", "negative", "wrong_role", "wrong_month", "grid"])
def test_adversarial_metadata_and_losses_are_rejected(failure):
    frame = paired()
    if failure == "naive":
        frame["decision_time"] = frame.decision_time.dt.tz_localize(None)
    elif failure == "unsorted":
        frame = frame.iloc[::-1]
    elif failure == "nan":
        frame.loc[0, "squared_loss_ridge"] = np.nan
    elif failure == "negative":
        frame.loc[0, "squared_loss_zero"] = -1
    elif failure == "wrong_role":
        frame.loc[0, "role"] = "development_evaluation"
    elif failure == "wrong_month":
        frame.loc[0, "decision_time"] = pd.Timestamp("2026-02-28T23:59Z")
    elif failure == "grid":
        frame.loc[0, "decision_time"] += pd.Timedelta(seconds=2)
    with pytest.raises(ValueError):
        run_bootstrap(frame)


@pytest.mark.parametrize("lengths", [(1, 60), (60, 360, 1440), (60, 360, 1440, 2880.0)])
def test_production_block_lengths_cannot_be_selected_after_results(lengths):
    with pytest.raises(ValueError, match="frozen"):
        run_bootstrap(paired(), block_lengths=lengths)
