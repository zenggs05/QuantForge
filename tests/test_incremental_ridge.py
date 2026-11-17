"""Synthetic mathematical oracles for fixed-scaler incremental Ridge."""
import copy
import hashlib
import json

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from quantforge.online.incremental_ridge import IncrementalRidge, NumericalStabilityError


def synthetic_problem(n=1200, p=14, kind="linear"):
    rng = np.random.default_rng(42)
    X = rng.normal(size=(n, p))
    if kind == "correlated":
        X[:, 1] = .999999 * X[:, 0] + rng.normal(scale=1e-5, size=n)
        X[:, 3] = 2 * X[:, 2] - .3 * X[:, 4]
    elif kind == "constant":
        X[:, 2], X[:, 7] = 3.0, -11.0
    elif kind == "shifted":
        X = X * np.linspace(.01, 100, p) + np.arange(p) * 50
    coef = rng.normal(size=p)
    y = X @ coef + 7.5 + rng.normal(scale=.01, size=n)
    return pd.DataFrame(X, columns=[f"synthetic_{i}" for i in range(p)]), pd.Series(y)


def assert_frozen_equivalence(model, X, y, probe=None):
    with threadpool_limits(limits=1):
        oracle = Ridge(alpha=1., fit_intercept=True, solver="svd").fit(model.transform(X), y)
        probe = X if probe is None else probe
        actual, expected = model.predict(probe), oracle.predict(model.transform(probe))
    assert (np.abs(model.coef_ - oracle.coef_) <= 1e-9 + 1e-8*np.abs(oracle.coef_)).all()
    assert abs(model.intercept_ - oracle.intercept_) <= 1e-10 + 1e-8*abs(oracle.intercept_)
    assert np.max(np.abs(actual - expected)) <= 1e-9
    assert np.linalg.norm(actual - expected)/max(np.linalg.norm(expected), 1e-12) <= 1e-8
    return oracle


@pytest.mark.parametrize("kind", ["linear", "correlated", "constant", "shifted"])
def test_synthetic_initial_pipeline_and_fixed_scaler_incremental_equivalence(kind):
    X, y = synthetic_problem(kind=kind)
    with threadpool_limits(limits=1):
        model = IncrementalRidge().initialize_from_batch(X.iloc[:300], y.iloc[:300])
        pipeline = Pipeline([("scale", StandardScaler()), ("ridge", Ridge(alpha=1., solver="svd"))]).fit(X.iloc[:300], y.iloc[:300])
        np.testing.assert_array_equal(model.scaler_mean_, pipeline["scale"].mean_)
        np.testing.assert_array_equal(model.scaler_scale_, pipeline["scale"].scale_)
        np.testing.assert_array_equal(model.scaler_var_, pipeline["scale"].var_)
        assert_frozen_equivalence(model, X.iloc[:300], y.iloc[:300], X.iloc[:300])
        original_scaler = (model.scaler_mean_, model.scaler_scale_, model.scaler_var_)
        for row, target in zip(X.iloc[300:].to_numpy(), y.iloc[300:]):
            model.update_one(row, target)
        assert model.dirty
        model.solve()
        assert_frozen_equivalence(model, X, y)
    for actual, before in zip((model.scaler_mean_, model.scaler_scale_, model.scaler_var_), original_scaler):
        np.testing.assert_array_equal(actual, before)
    if kind == "constant":
        assert model.scaler_scale_[2] == model.scaler_scale_[7] == 1.
        assert abs(model.coef_[2]) < 1e-12 and abs(model.coef_[7]) < 1e-12


@pytest.mark.parametrize("order", ["forward", "reverse", "permuted"])
@pytest.mark.parametrize("batch_size", [1, 37, 900])
def test_synthetic_update_order_and_batch_grouping_share_the_same_ridge_objective(order, batch_size):
    X, y = synthetic_problem()
    model = IncrementalRidge().initialize_from_batch(X.iloc[:300], y.iloc[:300])
    indices = np.arange(300, len(X))
    if order == "reverse": indices = indices[::-1]
    elif order == "permuted": indices = np.random.default_rng(7).permutation(indices)
    for start in range(0, len(indices), batch_size):
        group = indices[start:start+batch_size]
        if batch_size == 1:
            model.update_one(X.iloc[group[0]], float(y.iloc[group[0]]))
        else:
            model.update_batch(X.iloc[group], y.iloc[group])
    model.solve()
    assert_frozen_equivalence(model, X, y)
    assert model.diagnostics()["updated_samples"] == 900
    assert model.diagnostics()["training_sample_visits"] == 1200
    assert model.diagnostics()["update_calls"] == (900+batch_size-1)//batch_size
    assert model.diagnostics()["solve_calls"] == 2


def test_synthetic_checkpoint_compensations_restore_exact_continuation_even_if_dirty():
    X, y = synthetic_problem()
    original = IncrementalRidge().initialize_from_batch(X.iloc[:300], y.iloc[:300])
    for i in range(300, 700):
        original.update_one(X.iloc[i], y.iloc[i])
    saved = json.loads(json.dumps(original.state_dict(), allow_nan=False))
    restored = IncrementalRidge().load_state_dict(saved)
    assert saved == restored.state_dict()
    assert restored.dirty
    for i in range(700, len(X)):
        original.update_one(X.iloc[i], y.iloc[i])
        restored.update_one(X.iloc[i], y.iloc[i])
    original.solve()
    restored.solve()
    assert original.state_dict() == restored.state_dict()
    np.testing.assert_array_equal(original.predict(X), restored.predict(X))
    assert_frozen_equivalence(restored, X, y)
    assert restored.diagnostics()["state_validation_eigendecompositions"] == 2


def test_synthetic_large_accumulation_matches_independent_svd_oracle():
    X, y = synthetic_problem(n=40_000)
    model = IncrementalRidge().initialize_from_batch(X.iloc[:1000], y.iloc[:1000])
    for row, target in zip(X.iloc[1000:].to_numpy(), y.iloc[1000:]):
        model.update_one(row, target)
    model.solve()
    assert_frozen_equivalence(model, X, y, X.iloc[::97])
    assert model.n == 40_000 and model.initial_n == 1000 and model.solved_n == 40_000
    # Fixed-sized state proves that the numerical updater retained no history.
    assert not any(isinstance(v, (pd.DataFrame, pd.Series)) for v in model.__dict__.values())
    assert model.diagnostics()["numeric_array_bytes"] < 10_000


def test_synthetic_nonzero_intercept_is_unpenalized_and_constant_target_exact():
    X, _ = synthetic_problem(n=600)
    y = pd.Series(np.full(len(X), 123.))
    model = IncrementalRidge().initialize_from_batch(X.iloc[:200], y.iloc[:200])
    model.update_batch(X.iloc[200:], y.iloc[200:]).solve()
    np.testing.assert_allclose(model.coef_, 0., atol=1e-13, rtol=0)
    np.testing.assert_allclose(model.predict(X), 123., atol=1e-12, rtol=0)
    assert_frozen_equivalence(model, X, y)


def test_synthetic_fixed_scaler_and_later_refitted_scaler_are_distinct_objectives():
    rng = np.random.default_rng(9)
    X = pd.DataFrame({"synthetic": np.r_[rng.normal(size=40), rng.normal(scale=100, size=40)]})
    y = pd.Series(.03*X.synthetic + 1)
    model = IncrementalRidge().initialize_from_batch(X.iloc[:40], y.iloc[:40])
    model.update_batch(X.iloc[40:], y.iloc[40:]).solve()
    fixed = assert_frozen_equivalence(model, X, y)
    refitted = Pipeline([("scale", StandardScaler()), ("ridge", Ridge(alpha=1, solver="svd"))]).fit(X, y)
    assert np.max(np.abs(model.predict(X)-refitted.predict(X))) > .001
    assert model.coef_[0] == pytest.approx(fixed.coef_[0], abs=1e-9)


def test_dirty_state_rejects_prediction_and_versions_change_only_after_solve():
    X, y = synthetic_problem(n=500)
    model = IncrementalRidge().initialize_from_batch(X.iloc[:300], y.iloc[:300])
    version, coef = model.model_version, model.coef_
    model.update_one(X.iloc[300], y.iloc[300])
    assert model.model_version == version and model.solved_n == 300 and model.n == 301
    np.testing.assert_array_equal(model.coef_, coef)
    with pytest.raises(ValueError, match="dirty"):
        model.predict(X.iloc[[300]])
    model.solve()
    assert model.model_version != version and model.solved_n == 301
    solved_version = model.model_version
    model.solve()
    assert model.model_version == solved_version
    assert model.diagnostics()["solve_calls"] == 3
    assert model.diagnostics()["condition_evaluations"] == 3
    assert model.diagnostics()["cholesky_factorizations"] == 3


@pytest.mark.parametrize("invalid", ["nan_x", "inf_y", "bool_x", "bool_y", "wrong_width", "wrong_order", "mismatched_index"])
def test_invalid_update_is_rejected_without_statistics_or_version_mutation(invalid):
    X, y = synthetic_problem(n=400)
    model = IncrementalRidge().initialize_from_batch(X.iloc[:300], y.iloc[:300])
    before = model.state_dict()
    with pytest.raises(ValueError):
        if invalid == "nan_x": model.update_one(np.full(14,np.nan), 1.)
        elif invalid == "inf_y": model.update_one(X.iloc[300], np.inf)
        elif invalid == "bool_x": model.update_one(np.ones(14, dtype=bool), 1.)
        elif invalid == "bool_y": model.update_one(X.iloc[300], True)
        elif invalid == "wrong_width": model.update_one(np.ones(13), 1.)
        elif invalid == "wrong_order": model.update_batch(X.iloc[300:,::-1], y.iloc[300:])
        else: model.update_batch(X.iloc[300:], y.iloc[300:].iloc[::-1])
    assert before == model.state_dict()


def resign(state):
    state["sha256"] = hashlib.sha256(json.dumps(state["payload"], sort_keys=True,
        separators=(",", ":"),allow_nan=False).encode()).hexdigest()
    return state


@pytest.mark.parametrize("corrupt", ["checksum", "version", "schema", "coef", "intercept", "scaler", "counts", "shape", "symmetry", "dirty"])
def test_corrupted_checkpoint_fails_atomically_even_with_resigned_outer_checksum(corrupt):
    X, y = synthetic_problem(n=300)
    model = IncrementalRidge().initialize_from_batch(X, y)
    before = model.state_dict()
    wrong = copy.deepcopy(before)
    p = wrong["payload"]
    if corrupt == "checksum": wrong["sha256"] = "0"*64
    elif corrupt == "version": p["model_version"] = "0"*64
    elif corrupt == "schema": p["schema_version"] = 99
    elif corrupt == "coef": p["coefficients"][0] += 1
    elif corrupt == "intercept": p["intercept"] += 1
    elif corrupt == "scaler": p["scaler_scale"][0] *= 2
    elif corrupt == "counts": p["counts"]["updated_samples"] = 1
    elif corrupt == "shape": p["sums"]["sum_z"].pop()
    elif corrupt == "symmetry": p["sums"]["sum_zz"][0][1] += 1
    elif corrupt == "dirty": p["dirty"] = True
    if corrupt != "checksum": resign(wrong)
    with pytest.raises(ValueError): model.load_state_dict(wrong)
    assert model.state_dict() == before


def test_extreme_future_shift_is_rejected_by_frozen_condition_policy_without_inverse(monkeypatch):
    X, y = synthetic_problem(n=300)
    model = IncrementalRidge().initialize_from_batch(X, y)
    monkeypatch.setattr(np.linalg, "inv", lambda *args: pytest.fail("Matrix inverse must never be used"))
    huge = np.zeros(14)
    huge[0] = 1e10
    model.update_one(huge, 1.)
    version = model.model_version
    with pytest.raises(NumericalStabilityError, match="condition"):
        model.solve()
    assert model.model_version == version and model.dirty


def test_overflow_update_does_not_partially_mutate_state():
    X, y = synthetic_problem(n=300)
    model = IncrementalRidge().initialize_from_batch(X, y)
    before = model.state_dict()
    with pytest.raises(NumericalStabilityError, match="overflow"):
        model.update_one(np.full(14, 1e308), 1.)
    assert model.state_dict() == before


def test_readonly_properties_and_state_export_cannot_mutate_live_scaler_or_coefficients():
    X, y = synthetic_problem(n=300)
    model = IncrementalRidge().initialize_from_batch(X, y)
    before = model.state_dict()
    model.coef_[:] = 1000
    model.scaler_mean_[:] = 1000
    model.state_dict()["payload"]["sums"]["sum_z"][0] += 1
    assert model.state_dict() == before
    with pytest.raises(ValueError, match="refitted"):
        model.initialize_from_batch(X,y)


def test_sklearn_near_constant_scale_rule_is_preserved_on_checkpoint_load():
    X, y = synthetic_problem(n=300)
    X["synthetic_0"] = 1e10 + np.linspace(-1e-5, 1e-5, len(X))
    model = IncrementalRidge().initialize_from_batch(X, y)
    oracle = StandardScaler().fit(X)
    assert oracle.var_[0] > 0 and oracle.scale_[0] == 1
    np.testing.assert_array_equal(model.scaler_scale_, oracle.scale_)
    restored = IncrementalRidge().load_state_dict(model.state_dict())
    assert restored.state_dict() == model.state_dict()
    assert_frozen_equivalence(restored, X, y)


def test_materially_negative_centered_scatter_rejected_even_if_penalized_matrix_spd():
    X, y = synthetic_problem(n=300)
    model = IncrementalRidge().initialize_from_batch(X, y)
    wrong = model.state_dict()
    wrong["payload"]["sums"]["sum_z"] = [0.] * 14
    wrong["payload"]["sums"]["sum_zz"] = (np.eye(14)*-.1).tolist()
    resign(wrong)
    with pytest.raises(NumericalStabilityError, match="negative"):
        IncrementalRidge().load_state_dict(wrong)


def test_statistics_snapshot_does_not_change_when_predicting_or_transforming():
    X, y = synthetic_problem(n=300)
    model = IncrementalRidge().initialize_from_batch(X, y)
    before = model.state_dict()
    model.predict(X)
    model.transform(X)
    assert model.state_dict() == before
    assert model.diagnostics()["prediction_calls"] == 1
    assert model.diagnostics()["prediction_rows"] == len(X)


@pytest.mark.parametrize("alpha", [0., 2., True, np.nan, np.inf, "1", None])
def test_frozen_alpha_cannot_change(alpha):
    with pytest.raises(ValueError, match="frozen"):
        IncrementalRidge(alpha)
