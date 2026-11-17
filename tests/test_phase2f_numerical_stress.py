"""Frozen raw-row numerical stress, compensation and opt-in audit guards."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pytest
from threadpoolctl import threadpool_limits

from quantforge.online.hardening_config import load_hardening_config
from quantforge.online.incremental_ridge import IncrementalRidge, NumericalCrossCheckError
from quantforge.online.qr_reference import OnlineQRReference
from tests.phase2f_numerical_helpers import evaluate_kahan, evaluate_stress_case, stress_cases, stress_data


@pytest.mark.parametrize("kind,shift,ratio", stress_cases())
def test_three_way_fixed_mean_shift_and_extreme_rejection(kind, shift, ratio):
    result = evaluate_stress_case(kind, shift, ratio)
    limit = load_hardening_config()["stability"]["condition_limit"]
    if result["condition_number"] > limit:
        assert result["rejected"]
        assert set(result["errors"]) == {"cholesky", "qr"}
    else:
        assert not result["rejected"]
        assert all(row["passed"] for row in result["comparisons"].values())
        assert np.isfinite(result["relative_residual"])
        assert result["qr_row_updates"] == result["rows"]


def test_frozen_stress_cases_include_condition_rejection_branch():
    config = load_hardening_config()
    extreme = [case for case in stress_cases(config) if case[0] == "near_collinear"]
    assert len(extreme) >= 2
    results = [evaluate_stress_case(*case, config) for case in extreme]
    assert any(row["condition_number"] > config["stability"]["condition_limit"] for row in results)
    assert all(row["rejected"] for row in results)


def test_kahan_million_increments_matches_fsum_and_plain_sum_is_worse():
    result = evaluate_kahan()
    assert result["compensated_ulp"] <= result["ulp_limit"], "Kahan compensated sum exceeds frozen ULP bound"
    assert result["plain_to_compensated_ulp_ratio"] >= 100, "Kahan control must be at least 100 times worse"


def _model():
    config = load_hardening_config()
    X, y, n0 = stress_data("mean_shift", config["synthetic"]["mean_shifts"][0],
                           config["synthetic"]["update_ratios"][0], config)
    model = IncrementalRidge().initialize_from_batch(X.iloc[:n0], y.iloc[:n0])
    return model, X, y, n0


def test_actual_updates_store_kahan_compensation_and_exact_restart():
    config = load_hardening_config()
    model, X, _, n0 = _model()
    large = config["synthetic"]["kahan_initial_sum"]
    model = IncrementalRidge().initialize_from_batch(X.iloc[:n0], np.full(n0, large / n0))
    increment = config["synthetic"]["kahan_increment"]
    row = X.iloc[0].to_numpy()
    model.update_one(row, increment)
    state = model.state_dict()
    assert state["payload"]["compensation"]["sum_y"] != 0, "Kahan compensation must persist after a small increment"
    restored = IncrementalRidge().load_state_dict(state)
    for _ in range(n0):
        model.update_one(row, increment)
        restored.update_one(row, increment)
    assert model.state_dict() == restored.state_dict()


def test_default_solve_never_reads_config_or_retains_raw_rows(monkeypatch):
    import quantforge.online.hardening_config as hardening
    model, X, y, n0 = _model()
    restored = IncrementalRidge().load_state_dict(model.state_dict())
    monkeypatch.setattr(hardening, "load_hardening_config", lambda *a, **k: pytest.fail("Default solve read new config"))
    model.update_batch(X.iloc[n0:], y.iloc[n0:]).solve()
    restored.update_batch(X.iloc[n0:], y.iloc[n0:]).solve(cross_check=False)
    assert json.dumps(model.state_dict(), sort_keys=True) == json.dumps(restored.state_dict(), sort_keys=True)
    assert not hasattr(model, "_cross_check_data")
    assert not hasattr(model, "cross_check_diagnostics")


def test_explicit_cross_check_is_external_to_v1_state():
    model, X, y, n0 = _model()
    plain = IncrementalRidge().load_state_dict(model.state_dict())
    for candidate in (plain, model):
        candidate.update_batch(X.iloc[n0:], y.iloc[n0:])
    before = copy.deepcopy(model.state_dict())
    model.attach_cross_check_data(X, y)
    assert model.state_dict() == before
    with threadpool_limits(limits=1):
        plain.solve()
        model.solve(cross_check=True)
    assert plain.state_dict() == model.state_dict()
    assert model.cross_check_diagnostics["batch_svd_fits"] == 1
    assert model.cross_check_diagnostics["qr_solves"] == 1
    assert model.cross_check_diagnostics["raw_audit_array_bytes"] > 0


def test_cross_check_without_complete_raw_data_rejects():
    model, *_ = _model()
    version = model.model_version
    with pytest.raises(NumericalCrossCheckError, match="explicitly attached"):
        model.solve(cross_check=True)
    assert model.model_version == version


@pytest.mark.parametrize("case", ["count", "order", "index", "nonfinite"])
def test_attach_cross_check_schema_and_complete_count_guards(case):
    model, X, y, n0 = _model()
    X, y = X.iloc[:n0].copy(), y.iloc[:n0].copy()
    if case == "count":
        X, y = X.iloc[:-1], y.iloc[:-1]
    elif case == "order":
        X = X.iloc[:, ::-1]
    elif case == "index":
        y = y.iloc[::-1]
    else:
        y.iloc[0] = np.nan
    with pytest.raises(ValueError):
        model.attach_cross_check_data(X, y)
    assert not hasattr(model, "_cross_check_data")


def test_attached_raw_data_cannot_be_mutated_by_the_caller():
    model, X, y, n0 = _model()
    model.attach_cross_check_data(X.iloc[:n0], y.iloc[:n0])
    X.iloc[:, :] = 0
    y.iloc[:] = 0
    model.solve(cross_check=True)
    assert all(row["passed"] for row in model.cross_check_diagnostics["comparisons"].values())


@pytest.mark.parametrize("case", ["new_update", "scaler"])
def test_stale_attached_raw_or_scaler_rejects(case):
    model, X, y, n0 = _model()
    model.attach_cross_check_data(X.iloc[:n0], y.iloc[:n0])
    if case == "new_update":
        model.update_one(X.iloc[n0], y.iloc[n0])
    else:
        model._cross_check_data[2][0] += 1
    with pytest.raises(NumericalCrossCheckError, match="stale"):
        model.solve(cross_check=True)


def test_independent_raw_target_disagreement_rejects():
    model, X, y, n0 = _model()
    model.attach_cross_check_data(X.iloc[:n0], -y.iloc[:n0])
    with pytest.raises(NumericalCrossCheckError, match="graded limits"):
        model.solve(cross_check=True)


def test_failed_raw_cross_check_preserves_last_solution_and_counts_attempt():
    model, X, y, n0 = _model()
    model.update_batch(X.iloc[n0:], y.iloc[n0:])
    before = copy.deepcopy(model.state_dict()["payload"])
    assert before["dirty"]
    model.attach_cross_check_data(X, -y)
    with pytest.raises(NumericalCrossCheckError, match="Independent raw-row numerical references exceed frozen graded limits"):
        model.solve(cross_check=True)
    after = model.state_dict()["payload"]
    for field in ("coefficients", "intercept", "solved_n", "model_version", "solved_system"):
        assert after[field] == before[field], f"Failed audit changed last solved {field}"
    assert after["dirty"] and after["n"] == before["n"]
    assert after["counts"]["solve_calls"] == before["counts"]["solve_calls"]
    assert after["counts"]["condition_evaluations"] == before["counts"]["condition_evaluations"] + 1
    assert after["counts"]["cholesky_factorizations"] == before["counts"]["cholesky_factorizations"] + 1
    audit = model.cross_check_diagnostics
    assert audit["batch_svd_fits"] == audit["qr_solves"] == 1
    assert audit["qr_row_updates"] == len(X)
    for name in ("cholesky_vs_svd", "cholesky_vs_qr"):
        comparison = audit["comparisons"][name]
        assert not comparison["passed"], f"Wrong targets did not fail {name}"
        assert comparison["coefficient_relative"] > comparison["limits"]["coefficient_relative"]
        assert comparison["prediction_relative_l2"] > comparison["limits"]["prediction_relative_l2"]
    assert audit["comparisons"]["qr_vs_svd"]["passed"], "Independent raw references must agree on the same wrong targets"


def test_qr_state_is_fixed_size_and_scaler_input_is_copied():
    model, X, y, _ = _model()
    mean, scale = model.scaler_mean_, model.scaler_scale_
    qr = OnlineQRReference(mean, scale)
    before = qr.state_bytes()
    mean[:] = 0
    scale[:] = 1
    qr.update_batch_from_raw(X.to_numpy(), y.to_numpy())
    assert qr.state_bytes() == before
    qr.solve_ridge()
    assert qr.n == len(X) and qr.solve_count == 1


@pytest.mark.parametrize("path", ["../outside.toml", "data/processed/binance/spot/BTCUSDT/1m/2026-07.parquet",
                                  "configs/../configs/c1-engine-hardening.toml"])
def test_config_rejects_nonfixed_paths_before_read(monkeypatch, path):
    monkeypatch.setattr(Path, "read_bytes", lambda *a, **k: pytest.fail("Unexpected file byte access"))
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: pytest.fail("Unexpected file text access"))
    with pytest.raises(ValueError, match="fixed project configuration"):
        load_hardening_config(path)


def test_config_rejects_fixed_path_symlink_before_read(monkeypatch):
    real_is_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda p: p.name == "c1-engine-hardening.toml" or real_is_symlink(p))
    monkeypatch.setattr(Path, "read_bytes", lambda *a, **k: pytest.fail("Unexpected symlink content read"))
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: pytest.fail("Unexpected manifest read"))
    with pytest.raises(ValueError, match="symlinks or redirects"):
        load_hardening_config()
