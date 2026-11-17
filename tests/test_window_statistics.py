"""Synthetic pairing and equivalence checks; no market files or network."""
import numpy as np
import pandas as pd
import pytest

from quantforge.diagnostics.window_comparison import (
    MODELS, MONTHS, KEY, pair_window_predictions, analyze_window_predictions, _groups, _bootstrap, _hac,
)
from quantforge.diagnostics.block_bootstrap import run_bootstrap
from quantforge.diagnostics.hac_tests import run_hac, holm_adjust


def synthetic_predictions(n=300):
    rows = []
    rng = np.random.default_rng(42)
    for fold, month in enumerate(MONTHS, 1):
        for h in (15, 5):
            times = pd.date_range(f"{month}-01T00:00:01Z", periods=n, freq="min")
            y = rng.normal(size=n)
            for model in MODELS:
                p = .1 * y + .02 if model == "expanding" else (.2 * y if model.startswith("rolling") else np.zeros(n))
                for i, t in enumerate(times):
                    rows.append(dict(decision_time=t, horizon_minutes=h, fold=fold, month=month,
                        role="validation" if fold < 3 else "development_evaluation", model=model,
                        bar_open_time=t-pd.Timedelta(minutes=1, seconds=1), bar_end_exclusive=t-pd.Timedelta(seconds=1),
                        feature_available_time=t, target_start_time=t-pd.Timedelta(seconds=1),
                        target_end_time=t+pd.Timedelta(minutes=h, seconds=-1), label_available_time=t+pd.Timedelta(minutes=h),
                        prediction=p[i], target_return=y[i]))
    return pd.DataFrame(rows)


def test_synthetic_complete_pairs_sign_and_fixed_statistical_families():
    p = synthetic_predictions()
    out = analyze_window_predictions(p)
    assert out["pairing_audit"]["dropped_rows"] == 0
    assert out["pairing_audit"]["paired_rows"] * 6 == len(p)
    b, h = out["bootstrap_results"], out["hac_results"]
    assert len(b) == 192 and len(h) == 144 and len(out["paired_losses"]) == 48
    assert (b.estimate_mse_delta < 0).all()
    primary = h[(h.horizon_minutes == 15) & (h.scope_type == "role") & (h.aggregation == "n_weighted") & (h.lag == 60)]
    assert len(primary) == 6
    np.testing.assert_array_equal(primary.holm_primary6_p, holm_adjust(primary.p_value.tolist()))
    np.testing.assert_array_equal(h.holm_global144_p, holm_adjust(h.p_value.tolist()))
    assert h.loc[~h.index.isin(primary.index), "holm_primary6_p"].isna().all()


@pytest.mark.parametrize("failure", ["missing", "duplicate", "target", "role", "month", "horizon", "fold", "nan", "inf", "naive", "boundary"])
def test_synthetic_bad_pairing_cannot_drop_or_repair_rows(failure):
    p = synthetic_predictions(3)
    if failure == "missing": p = p.iloc[1:]
    elif failure == "duplicate": p = pd.concat([p, p.iloc[[0]]])
    elif failure == "target": p.loc[0, "target_return"] += 1
    elif failure == "role": p.loc[0, "role"] = "development_evaluation"
    elif failure == "month": p.loc[0, "month"] = "2026-07"
    elif failure == "horizon": p.loc[0, "horizon_minutes"] = 10
    elif failure == "fold": p.loc[0, "fold"] = 2
    elif failure == "nan": p.loc[0, "prediction"] = np.nan
    elif failure == "inf": p.loc[0, "prediction"] = np.inf
    elif failure == "naive": p["decision_time"] = p.decision_time.dt.tz_localize(None)
    elif failure == "boundary": p.loc[0, "label_available_time"] -= pd.Timedelta(seconds=1)
    with pytest.raises(ValueError): pair_window_predictions(p)


def test_synthetic_adapter_matches_unchanged_phase2c_low_level_pipeline():
    wide, _ = pair_window_predictions(synthetic_predictions(3000))
    alias = wide[["month", "role", "horizon_minutes", "decision_time"]].copy()
    alias["squared_loss_ridge"] = wide.squared_loss_rolling_1m
    alias["squared_loss_zero"] = wide.squared_loss_expanding
    alias["squared_loss_historical_mean"] = wide.squared_loss_expanding
    b, h = _bootstrap(wide), _hac(wide)
    assert b.status.eq("estimable").all() and h.status.eq("estimable").all()
    old_b, old_h = run_bootstrap(alias), run_hac(alias)
    actual = b.loc[b.window.eq("rolling_1m")].reset_index(drop=True)
    expected = old_b.loc[old_b.baseline.eq("zero")].reset_index(drop=True)
    cols = ["estimate_mse_delta", "ci_low", "ci_high", "relative_ci_low", "relative_ci_high", "status", "reason"]
    pd.testing.assert_frame_equal(actual[cols], expected[cols], check_exact=True)
    actual = h.loc[h.window.eq("rolling_1m")].reset_index(drop=True)
    expected = old_h.loc[old_h.baseline.eq("zero")].reset_index(drop=True)
    cols = ["estimate_mse_delta", "ci_low", "ci_high", "se", "variance", "p_value", "status", "reason"]
    pd.testing.assert_frame_equal(actual[cols], expected[cols], check_exact=True)


@pytest.mark.parametrize("field", ["late_label", "model_fit_time", "evaluation_as_of"])
def test_common_to_all_models_invalid_cutoff_is_rejected(field):
    p = synthetic_predictions(3)
    if field == "late_label":
        selected = p.month.eq("2026-03")
        for c in ("decision_time", "bar_open_time", "bar_end_exclusive", "feature_available_time",
                  "target_start_time", "target_end_time", "label_available_time"):
            p.loc[selected, c] += pd.Timedelta(days=30, hours=23, minutes=58)
    else:
        p[field] = pd.Timestamp("2026-01-01", tz="UTC")
    with pytest.raises(ValueError, match="cutoff|membership"):
        pair_window_predictions(p)


def test_synthetic_identical_models_zero_difference_degenerate_p_and_reproducibility():
    p = synthetic_predictions(80)
    p["prediction"] = 0.0
    one = analyze_window_predictions(p)
    two = analyze_window_predictions(p.sample(frac=1, random_state=42))
    for key in ("paired_losses", "bootstrap_results", "hac_results"):
        pd.testing.assert_frame_equal(one[key], two[key], check_exact=True)
    assert one["hac_results"].p_value.isna().all()
    assert one["hac_results"].holm_primary6_p.isna().all()
    assert (one["hac_results"].estimate_mse_delta == 0).all()


def test_synthetic_short_gap_segments_not_dropped_or_joined():
    p = synthetic_predictions(80)
    selected = p.decision_time.dt.minute.ge(40) & p.decision_time.dt.hour.eq(0)
    for name in ("decision_time", "bar_open_time", "bar_end_exclusive", "feature_available_time",
                 "target_start_time", "target_end_time", "label_available_time"):
        p.loc[selected, name] += pd.Timedelta(days=1)
    wide, _ = pair_window_predictions(p)
    result = _bootstrap(wide)
    assert result.status.eq("not_estimable").all()
    assert (result.n_samples > 0).all()


def test_synthetic_role_weighting_ratio_is_ratio_of_weighted_losses():
    p = synthetic_predictions(80)
    p = p.loc[~(p.month.eq("2026-04") & p.decision_time.dt.minute.ge(40))].copy()
    wide, _ = pair_window_predictions(p)
    groups = _groups(wide, "rolling_3m")
    result = _bootstrap(wide)
    row = result[(result.window == "rolling_3m") & (result.scope_id == "validation")
                 & (result.horizon_minutes == 15) & (result.aggregation == "n_weighted") & (result.block_length == 60)].iloc[0]
    differences = np.concatenate([groups[15, m]["ridge"]-groups[15, m]["expanding"] for m in MONTHS[:2]])
    bases = np.concatenate([groups[15, m]["expanding"] for m in MONTHS[:2]])
    assert row.estimate_mse_delta == pytest.approx(differences.mean())
    assert row.relative_mse_delta == pytest.approx(differences.mean()/bases.mean())


def test_synthetic_figures_are_complete_and_byte_deterministic(tmp_path):
    from quantforge.diagnostics.window_figures import create_window_figures, FIGURE_NAMES
    from quantforge.diagnostics.calibration import calibration_metrics
    from PIL import Image
    p = synthetic_predictions(1500)
    analysis = analyze_window_predictions(p)
    metrics = pd.DataFrame([{
        "month": month, "horizon_minutes": h, "model": model, "train_samples": 5000,
        **calibration_metrics(part.prediction, part.target_return),
    } for (month, h, model), part in p.groupby(["month", "horizon_minutes", "model"])])
    for name in ("one", "two"):
        assert set(create_window_figures(metrics, analysis["bootstrap_results"], analysis["hac_results"], tmp_path / name)) == set(FIGURE_NAMES)
    for name in FIGURE_NAMES:
        assert (tmp_path / "one" / name).read_bytes() == (tmp_path / "two" / name).read_bytes()
        with Image.open(tmp_path / "one" / name) as png:
            assert png.format == "PNG" and min(png.size) > 500
