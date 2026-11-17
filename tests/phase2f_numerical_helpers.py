"""Reusable synthetic-only numerical cases for tests and formal evidence."""
from __future__ import annotations

import itertools
import math
from time import perf_counter

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from quantforge.online.hardening_config import load_hardening_config
from quantforge.online.incremental_ridge import IncrementalRidge, NumericalStabilityError
from quantforge.online.qr_reference import OnlineQRReference


def stress_cases(config=None):
    config = load_hardening_config() if config is None else config
    synthetic = config["synthetic"]
    return [("mean_shift", k, ratio) for k, ratio in itertools.product(synthetic["mean_shifts"], synthetic["update_ratios"])] + [
        ("near_collinear", k, 1.0) for k in synthetic["extreme_shifts"]]


def stress_data(kind, shift, ratio, config=None):
    config = load_hardening_config() if config is None else config
    settings = config["synthetic"]
    rng = np.random.default_rng(settings["seed"])
    n0, p = settings["initial_rows"], settings["feature_count"]
    n1 = int(n0 * ratio)
    initial = rng.standard_normal((n0, p))
    later = rng.standard_normal((n1, p))
    if kind == "mean_shift":
        later[:, 0] += shift * initial[:, 0].std()
    elif kind == "near_collinear":
        # A shared direction dominates the later covariance, while the fixed
        # independent initial cloud keeps all other directions identifiable.
        later += rng.standard_normal((n1, 1)) * shift
    else:
        raise ValueError("Unknown frozen synthetic stress kind")
    raw = np.vstack([initial, later])
    coefficient = rng.standard_normal(p)
    targets = raw @ coefficient + rng.standard_normal(n0 + n1)
    return pd.DataFrame(raw, columns=[f"synthetic_{i}" for i in range(p)]), pd.Series(targets), n0


def evaluate_stress_case(kind, shift, ratio, config=None):
    """Evaluate one frozen case without filesystem output or market input."""
    config = load_hardening_config() if config is None else config
    tick = perf_counter()
    with threadpool_limits(limits=config["budget"]["numeric_threads"]):
        X, y, n0 = stress_data(kind, shift, ratio, config)
        model = IncrementalRidge().initialize_from_batch(X.iloc[:n0], y.iloc[:n0])
        for row, target in zip(X.iloc[n0:].to_numpy(), y.iloc[n0:].to_numpy()):
            model.update_one(row, target)
        z = model.transform(X)
        centered = z - z.mean(axis=0)
        singular = np.linalg.svd(centered, compute_uv=False)
        condition = float((singular[0] ** 2 + 1) / (singular[-1] ** 2 + 1))
        result = {"kind": kind, "shift": float(shift), "update_ratio": float(ratio),
                  "condition_number": condition, "rows": len(X), "initial_rows": n0,
                  "statistics_updates": len(X) - n0, "initial_solves": 1,
                  "cross_check": condition <= config["stability"]["condition_limit"]}
        if condition > config["stability"]["condition_limit"]:
            qr = OnlineQRReference(model.scaler_mean_, model.scaler_scale_).update_batch_from_raw(X.to_numpy(), y.to_numpy())
            errors = {}
            for name, solver in (("cholesky", model.solve), ("qr", qr.solve_ridge)):
                try:
                    solver()
                except NumericalStabilityError as error:
                    errors[name] = str(error)
                else:
                    raise AssertionError(f"{name} accepted centered condition above frozen limit")
            result.update(rejected=True, errors=errors, qr_condition_number=qr.condition_number,
                          batch_svd_fits=0, qr_row_updates=qr.n, qr_solves=qr.solve_count,
                          qr_solve_attempts=1, cholesky_solve_attempts=1,
                          relative_residual=None, comparisons=None, qr_state_bytes=qr.state_bytes())
        else:
            model.attach_cross_check_data(X, y).solve(cross_check=True)
            audit = model.cross_check_diagnostics
            result.update(rejected=False, **audit, qr_solve_attempts=1, cholesky_solve_attempts=1)
        counts = model.state_dict()["payload"]["counts"]
        result["model_operation_counts"] = counts
        result["initial_training_row_visits"] = n0
        result["elapsed_seconds"] = perf_counter() - tick
        return result


def evaluate_kahan(config=None):
    """Audit the actual scalar Kahan primitive and a plain-sum control."""
    config = load_hardening_config() if config is None else config
    settings = config["synthetic"]
    n0, p = settings["initial_rows"], settings["feature_count"]
    count, increment = settings["kahan_updates"], settings["kahan_increment"]
    initial = settings["kahan_initial_sum"]
    rng = np.random.default_rng(settings["seed"])
    X = pd.DataFrame(rng.standard_normal((n0, p)), columns=[f"synthetic_{i}" for i in range(p)])
    model = IncrementalRidge().initialize_from_batch(X, np.full(n0, initial / n0))
    total = float(model.state_dict()["payload"]["sums"]["sum_y"])
    assert total == initial
    compensation, plain = 0.0, initial
    tick = perf_counter()
    for _ in range(count):
        total, compensation = model._kahan(total, compensation, increment)
        plain += increment
    reference = math.fsum(itertools.chain([initial], itertools.repeat(increment, count)))
    unit = math.ulp(reference)
    compensated_ulp, plain_ulp = abs(total - reference) / unit, abs(plain - reference) / unit
    limit = max(config["acceptance_graded"]["kahan_ulp_multiplier"], p * np.finfo(np.float64).eps * count)
    return {"initial_rows": n0, "initial_sum": initial, "increment": increment, "increments": count,
            "compensated_sum": total, "plain_sum": plain, "fsum_reference": reference,
            "compensated_ulp": compensated_ulp, "plain_ulp": plain_ulp, "ulp_limit": limit,
            "plain_to_compensated_ulp_ratio": plain_ulp / max(1.0, compensated_ulp),
            "kahan_scalar_calls": count, "statistics_updates": 0, "initial_solves": 1,
            "elapsed_seconds": perf_counter() - tick}
