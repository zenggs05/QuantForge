"""Fixed-coordinate, no-forgetting Ridge using float64 sufficient statistics.

This numerical layer does not know when a label matures. The replay/queue
layer must release every target before passing it here. No historical rows
are retained or rescanned by an incremental update.
"""
from __future__ import annotations

import hashlib
import json
from time import perf_counter

import numpy as np
import pandas as pd
from scipy.linalg import cho_factor, cho_solve
from sklearn.preprocessing import StandardScaler

from .hardening_config import NumericalCrossCheckError


class NumericalStabilityError(ValueError):
    """The frozen numerical policy cannot safely solve the supplied state."""


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def _integer(value, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _finite_array(value, name: str, shape: tuple | None = None) -> np.ndarray:
    original = np.asarray(value)
    if original.dtype.kind not in "iuf" or original.dtype.kind == "b":
        raise ValueError(f"{name} must contain finite real numeric values")
    result = np.asarray(value, dtype=np.float64)
    if shape is not None and result.shape != shape:
        raise ValueError(f"{name} has incompatible shape")
    if not np.isfinite(result).all():
        raise ValueError(f"{name} must contain finite float64 values")
    return result.copy()


class IncrementalRidge:
    """Accumulate sufficient statistics; solve explicitly before prediction.

    ``update_one`` and ``update_batch`` only alter statistics. The last solved
    coefficients/version stay unchanged until ``solve`` succeeds, and dirty
    statistics cannot be predicted with accidentally stale coefficients.
    """

    SCHEMA_VERSION = 1
    MAX_CONDITION = 1e12
    SCATTER_NEGATIVE_TOLERANCE = 1e-10

    def __init__(self, alpha: float = 1.0):
        if (not isinstance(alpha, (int, float, np.integer, np.floating))
                or isinstance(alpha, (bool, np.bool_)) or not np.isfinite(alpha) or alpha != 1.0):
            raise ValueError("Foundation Ridge alpha is frozen at 1.0")
        self.alpha = 1.0
        self._initialized = False
        self._reset_runtime()

    def _reset_runtime(self):
        self._timings = {"initialize": 0.0, "update": 0.0, "solve": 0.0, "predict": 0.0,
                         "state_load": 0.0}
        self._prediction_calls = 0
        self._prediction_rows = 0
        self._state_validation_eigendecompositions = 0

    def _require_initialized(self):
        if not self._initialized:
            raise ValueError("Initialize the incremental model before use")

    @property
    def n(self):
        self._require_initialized()
        return self._n

    @property
    def initial_n(self):
        self._require_initialized()
        return self._initial_n

    @property
    def solved_n(self):
        self._require_initialized()
        return self._solved_n

    @property
    def feature_names(self):
        self._require_initialized()
        return tuple(self._feature_names)

    @property
    def coef_(self):
        self._require_initialized()
        return self._coef.copy()

    @property
    def intercept_(self):
        self._require_initialized()
        return self._intercept

    @property
    def scaler_mean_(self):
        self._require_initialized()
        return self._mean.copy()

    @property
    def scaler_scale_(self):
        self._require_initialized()
        return self._scale.copy()

    @property
    def scaler_var_(self):
        self._require_initialized()
        return self._variance.copy()

    @property
    def model_version(self):
        self._require_initialized()
        return self._model_version

    @property
    def version(self):
        return self.model_version

    @property
    def dirty(self):
        self._require_initialized()
        return self._solved_n != self._n

    def _feature_array(self, X, *, single: bool = False):
        self._require_initialized()
        if isinstance(X, pd.DataFrame):
            if tuple(X.columns) != self.feature_names:
                raise ValueError("Feature names and order differ from fixed initial schema")
            values = X.to_numpy()
        elif isinstance(X, pd.Series):
            if not single or tuple(X.index) != self.feature_names:
                raise ValueError("Single feature Series must follow the fixed feature order")
            values = X.to_numpy()
        else:
            values = X
        array = _finite_array(values, "features")
        if single:
            if array.shape != (len(self._feature_names),):
                raise ValueError("Single update requires a feature vector")
        elif array.ndim != 2 or array.shape[1] != len(self._feature_names) or not len(array):
            raise ValueError("Features require a nonempty matrix with the fixed width")
        return array

    def transform(self, X):
        """Apply the initial scaler without fitting or changing any state."""
        array = self._feature_array(X)
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            z = (array - self._mean) / self._scale
        if not np.isfinite(z).all():
            raise NumericalStabilityError("Fixed feature transformation overflowed")
        return z

    def initialize_from_batch(self, X, y):
        """Fit one initial scaler and initialize; targets must already be mature."""
        if self._initialized:
            raise ValueError("Initial scaler cannot be refitted on an initialized model")
        tick = perf_counter()
        if not isinstance(X, pd.DataFrame) or X.empty or X.columns.has_duplicates:
            raise ValueError("Initial features must be a nonempty uniquely named DataFrame")
        if not all(isinstance(name, str) and name for name in X.columns):
            raise ValueError("Feature names must be nonempty strings")
        if isinstance(y, pd.Series) and not X.index.equals(y.index):
            raise ValueError("Training target and feature indexes must agree")
        values = _finite_array(X.to_numpy(), "initial features")
        target = _finite_array(y, "initial targets", (len(X),))
        # Match the original Baseline's DataFrame input path as well as sklearn's
        # zero/near-constant behavior; copies with different memory order can
        # otherwise change reduction rounding despite identical mathematical data.
        scaler = StandardScaler().fit(X)
        with np.errstate(over="ignore", invalid="ignore"):
            z = scaler.transform(X)
            sums = {"sum_z": z.sum(axis=0), "sum_y": np.asarray(target.sum()),
                    "sum_zz": z.T @ z, "sum_zy": z.T @ target}
        if any(not np.isfinite(value).all() for value in sums.values()):
            raise NumericalStabilityError("Initial sufficient statistics overflowed")
        self._feature_names = tuple(X.columns)
        self._mean, self._scale, self._variance = scaler.mean_.copy(), scaler.scale_.copy(), scaler.var_.copy()
        self._initial_n = self._n = len(X)
        self._sums = sums
        self._compensation = {name: np.zeros_like(value) for name, value in sums.items()}
        self._updated_samples = self._update_calls = 0
        self._solve_calls = self._condition_evaluations = self._cholesky_factorizations = 0
        self._solved_n = 0
        self._coef, self._intercept = np.zeros(len(X.columns)), 0.0
        self._model_version = None
        self._solved_system = None
        self._condition_number = None
        self._min_scatter_eigenvalue = None
        self._initialized = True
        self._timings["initialize"] += perf_counter() - tick
        try:
            return self.solve()
        except Exception:
            # A failed initialization is not a usable fitted object.
            self._initialized = False
            raise

    @staticmethod
    def _kahan(total, compensation, increment):
        corrected = increment - compensation
        updated = total + corrected
        residual = (updated - total) - corrected
        return updated, residual

    def _accumulate(self, z, target):
        # Compute and validate all new moments before committing any mutation.
        with np.errstate(over="ignore", invalid="ignore"):
            changes = {"sum_z": z.sum(axis=0), "sum_y": np.asarray(target.sum()),
                       "sum_zz": z.T @ z, "sum_zy": z.T @ target}
            new, compensation = {}, {}
            for name, increment in changes.items():
                new[name], compensation[name] = self._kahan(self._sums[name], self._compensation[name], increment)
        if any(not np.isfinite(v).all() for v in [*new.values(), *compensation.values()]):
            raise NumericalStabilityError("Incremental sufficient statistics overflowed")
        self._sums, self._compensation = new, compensation
        self._n += len(target)
        self._updated_samples += len(target)
        self._update_calls += 1

    def update_one(self, x, y):
        """O(p^2) statistic update; does not rescan history or solve parameters."""
        tick = perf_counter()
        array = self._feature_array(x, single=True)
        target = _finite_array(y, "target", ())
        with np.errstate(over="ignore", invalid="ignore"):
            z = (array - self._mean) / self._scale
        if not np.isfinite(z).all():
            raise NumericalStabilityError("Fixed feature transformation overflowed")
        self._accumulate(z[None, :], np.asarray([target.item()], dtype=np.float64))
        self._timings["update"] += perf_counter() - tick
        return self

    def update_batch(self, X, y):
        """Accumulate a supplied matured batch without changing scaler or solving."""
        tick = perf_counter()
        if isinstance(X, pd.DataFrame) and isinstance(y, pd.Series) and not X.index.equals(y.index):
            raise ValueError("Training target and feature indexes must agree")
        z = self.transform(X)
        target = _finite_array(y, "targets", (len(z),))
        self._accumulate(z, target)
        self._timings["update"] += perf_counter() - tick
        return self

    @classmethod
    def _system(cls, sums, n):
        with np.errstate(over="ignore", invalid="ignore"):
            scatter = sums["sum_zz"] - np.outer(sums["sum_z"], sums["sum_z"]) / n
            A = (scatter + scatter.T) * .5 + np.eye(len(sums["sum_z"]))
            b = sums["sum_zy"] - sums["sum_z"] * float(sums["sum_y"]) / n
        if not np.isfinite(A).all() or not np.isfinite(b).all():
            raise NumericalStabilityError("Centered moment arithmetic overflowed")
        return A, b

    @classmethod
    def _spectrum(cls, A, sum_zz):
        eigenvalues = np.linalg.eigvalsh(A)
        if not np.isfinite(eigenvalues).all() or eigenvalues[0] <= 0:
            raise NumericalStabilityError("Ridge system must be finite positive definite")
        condition = float(eigenvalues[-1] / eigenvalues[0])
        if condition > cls.MAX_CONDITION:
            raise NumericalStabilityError("Ridge condition number exceeds frozen 1e12 limit")
        scatter_min = float(eigenvalues[0] - 1.0)
        with np.errstate(over="ignore", invalid="ignore"):
            scale = float(np.linalg.norm(sum_zz, ord="fro"))
        if not np.isfinite(scale):
            raise NumericalStabilityError("Scatter diagnostic norm overflowed")
        if scatter_min < -cls.SCATTER_NEGATIVE_TOLERANCE * max(1.0, scale):
            raise NumericalStabilityError("Centered scatter has materially negative eigenvalues")
        return condition, scatter_min

    def _version_record(self):
        return {"schema_version": self.SCHEMA_VERSION, "alpha": self.alpha,
                "feature_names": list(self._feature_names), "initial_n": self._initial_n,
                "scaler_mean": self._mean.tolist(), "scaler_scale": self._scale.tolist(),
                "scaler_variance": self._variance.tolist(), "solved_n": self._solved_n,
                "coefficients": self._coef.tolist(), "intercept": self._intercept}

    def solve(self, cross_check: bool = False):
        """Symmetric eigen diagnostics plus Cholesky; no inverse or jitter."""
        self._require_initialized()
        tick = perf_counter()
        A, b = self._system(self._sums, self._n)
        self._condition_evaluations += 1
        condition, scatter_min = self._spectrum(A, self._sums["sum_zz"])
        self._cholesky_factorizations += 1
        try:
            factor = cho_factor(A, lower=True, check_finite=True)
            coef = cho_solve(factor, b, check_finite=True)
        except (ValueError, np.linalg.LinAlgError) as exc:
            raise NumericalStabilityError("Cholesky Ridge solve failed without fallback") from exc
        intercept = float(self._sums["sum_y"]) / self._n - float(np.dot(self._sums["sum_z"] / self._n, coef))
        if not np.isfinite(coef).all() or not np.isfinite(intercept):
            raise NumericalStabilityError("Ridge coefficients are nonfinite")
        if cross_check:
            self._cross_check_raw_solution(coef, intercept, A, b, condition)
        self._coef, self._intercept = np.asarray(coef, dtype=np.float64), intercept
        self._solved_n = self._n
        self._condition_number, self._min_scatter_eigenvalue = condition, scatter_min
        self._solved_system = {"A": A.tolist(), "b": b.tolist(),
            "mean_z": (self._sums["sum_z"] / self._n).tolist(), "mean_y": float(self._sums["sum_y"]) / self._n}
        self._model_version = _digest(self._version_record())
        self._solve_calls += 1
        self._timings["solve"] += perf_counter() - tick
        return self

    def attach_cross_check_data(self, X, y):
        """Attach explicitly supplied mature raw rows for a subsequent audit.

        The caller owns maturity and sample-set provenance. This method checks
        schema, finiteness and the complete current row count; it cannot infer
        label availability from numeric arrays. Audit rows are never retained
        by default updates or serialized into a v1 checkpoint.
        """
        self._require_initialized()
        if isinstance(X, pd.DataFrame) and isinstance(y, pd.Series) and not X.index.equals(y.index):
            raise ValueError("Cross-check target and feature indexes must agree")
        raw = self._feature_array(X)
        targets = _finite_array(y, "cross-check targets", (len(raw),))
        if len(raw) != self.n:
            raise ValueError("Cross-check raw data must contain the complete current sample count")
        self._cross_check_data = (raw, targets, self.scaler_mean_, self.scaler_scale_)
        return self

    def _cross_check_raw_solution(self, coef, intercept, A, b, condition):
        from sklearn.linear_model import Ridge
        from .hardening_config import assert_legacy_equivalence_config, graded_limits, load_hardening_config
        from .qr_reference import OnlineQRReference

        config = load_hardening_config()
        assert_legacy_equivalence_config(config)
        if not hasattr(self, "_cross_check_data"):
            raise NumericalCrossCheckError("Cross-check requires explicitly attached complete raw data")
        raw, targets, mean, scale = self._cross_check_data
        if (len(raw) != self.n or not np.array_equal(mean, self.scaler_mean_)
                or not np.array_equal(scale, self.scaler_scale_)):
            raise NumericalCrossCheckError("Cross-check raw data or fixed scaler is stale")
        z = (raw - mean) / scale
        svd = Ridge(alpha=self.alpha, fit_intercept=True, solver="svd").fit(z, targets)
        qr = OnlineQRReference(mean, scale).update_batch_from_raw(raw, targets)
        qr_coef, qr_intercept = qr.solve_ridge(self.alpha)
        solutions = {"cholesky": (np.asarray(coef), float(intercept)),
                     "svd": (svd.coef_, float(svd.intercept_)), "qr": (qr_coef, qr_intercept)}
        comparisons = {}
        for left, right in (("cholesky", "svd"), ("cholesky", "qr"), ("qr", "svd")):
            actual, actual_intercept = solutions[left]
            reference, reference_intercept = solutions[right]
            limits = graded_limits(condition, reference, config=config)
            floor = limits["norm_denominator_floor"]
            delta = actual - reference
            coef_abs = float(np.max(np.abs(delta)))
            coef_relative = coef_abs / max(float(np.max(np.abs(reference))), floor)
            reference_prediction = z @ reference + reference_intercept
            prediction_delta = (z @ actual + actual_intercept) - reference_prediction
            prediction_relative = float(np.linalg.norm(prediction_delta) / max(np.linalg.norm(reference_prediction), floor))
            passed = (coef_relative <= limits["coefficient_relative"]
                      and prediction_relative <= limits["prediction_relative_l2"])
            comparisons[f"{left}_vs_{right}"] = {
                "coefficient_max_abs": coef_abs, "coefficient_relative": coef_relative,
                "prediction_max_abs": float(np.max(np.abs(prediction_delta))),
                "prediction_relative_l2": prediction_relative, "limits": limits, "passed": bool(passed)}
        floor = config["tolerance"]["norm_denominator_floor"]
        self.cross_check_diagnostics = {
            "condition_number": float(condition), "qr_condition_number": qr.condition_number,
            "relative_residual": float(np.linalg.norm(A @ coef - b) / max(np.linalg.norm(b), floor)),
            "comparisons": comparisons, "batch_svd_fits": 1, "qr_solves": qr.solve_count,
            "qr_row_updates": qr.n, "qr_state_bytes": qr.state_bytes(),
            "raw_audit_array_bytes": int(sum(v.nbytes for v in self._cross_check_data)),
            "raw_training_row_visits": len(raw),
        }
        if not all(row["passed"] for row in comparisons.values()):
            raise NumericalCrossCheckError("Independent raw-row numerical references exceed frozen graded limits")

    def predict(self, X):
        self._require_initialized()
        if self.dirty:
            raise ValueError("Statistics are dirty; explicitly solve before predicting")
        tick = perf_counter()
        z = self.transform(X)
        with np.errstate(over="ignore", invalid="ignore"):
            result = z @ self._coef + self._intercept
        if not np.isfinite(result).all():
            raise NumericalStabilityError("Prediction arithmetic overflowed")
        self._prediction_calls += 1
        self._prediction_rows += len(result)
        self._timings["predict"] += perf_counter() - tick
        return result

    def state_dict(self):
        """Deterministic numerical state; runtime clocks are audited separately."""
        self._require_initialized()
        body = {**self._version_record(), "dtype": "float64", "forgetting": False,
                "n": self._n, "dirty": self.dirty, "model_version": self._model_version,
                "sums": {k: v.tolist() for k, v in self._sums.items()},
                "compensation": {k: v.tolist() for k, v in self._compensation.items()},
                "solved_system": json.loads(json.dumps(self._solved_system)),
                "condition_number": self._condition_number, "min_scatter_eigenvalue": self._min_scatter_eigenvalue,
                "counts": {"updated_samples": self._updated_samples, "update_calls": self._update_calls,
                    "solve_calls": self._solve_calls, "condition_evaluations": self._condition_evaluations,
                    "cholesky_factorizations": self._cholesky_factorizations}}
        return {"payload": body, "sha256": _digest(body)}

    def load_state_dict(self, state):
        """Validate a detached candidate completely before replacing this model."""
        tick = perf_counter()
        if not isinstance(state, dict) or set(state) != {"payload", "sha256"} or not isinstance(state["payload"], dict):
            raise ValueError("Invalid model state envelope")
        body = state["payload"]
        try:
            if _digest(body) != state["sha256"]:
                raise ValueError("Model state checksum mismatch")
        except (TypeError, ValueError) as exc:
            raise ValueError("Model state checksum or JSON values invalid") from exc
        required = {"schema_version", "alpha", "feature_names", "initial_n", "scaler_mean", "scaler_scale",
                    "scaler_variance", "solved_n", "coefficients", "intercept", "dtype", "forgetting", "n", "dirty",
                    "model_version", "sums", "compensation", "solved_system", "condition_number", "min_scatter_eigenvalue", "counts"}
        if (set(body) != required or type(body["schema_version"]) is not int or body["schema_version"] != self.SCHEMA_VERSION
                or body["dtype"] != "float64" or body["forgetting"] is not False
                or type(body["dirty"]) is not bool):
            raise ValueError("Unknown or incompatible model state schema")
        candidate = IncrementalRidge(body["alpha"])
        names = body["feature_names"]
        if not isinstance(names, list) or not names or len(set(names)) != len(names) or not all(isinstance(v, str) and v for v in names):
            raise ValueError("Invalid feature schema in model state")
        p = len(names)
        candidate._feature_names = tuple(names)
        candidate._initial_n = _integer(body["initial_n"], "initial_n", 1)
        candidate._n = _integer(body["n"], "n", candidate._initial_n)
        candidate._solved_n = _integer(body["solved_n"], "solved_n", candidate._initial_n)
        if candidate._solved_n > candidate._n or body["dirty"] != (candidate._solved_n != candidate._n):
            raise ValueError("Solved count or dirty flag is inconsistent")
        candidate._mean = _finite_array(body["scaler_mean"], "scaler_mean", (p,))
        candidate._scale = _finite_array(body["scaler_scale"], "scaler_scale", (p,))
        candidate._variance = _finite_array(body["scaler_variance"], "scaler_variance", (p,))
        if (candidate._scale <= 0).any() or (candidate._variance < 0).any():
            raise ValueError("Scaler scale/variance invalid")
        # Reconstruct sklearn's near-constant criterion from the initial sample
        # count, mean and variance; it is stricter than merely checking var==0.
        eps = np.finfo(np.float64).eps
        constant = candidate._variance <= (candidate._initial_n * eps * candidate._variance
                                          + (candidate._initial_n * candidate._mean * eps) ** 2)
        expected_scale = np.sqrt(candidate._variance)
        expected_scale[constant] = 1.0
        if not np.array_equal(candidate._scale, expected_scale):
            raise ValueError("Frozen scaler scale disagrees with initial variance/constant-column rule")
        shapes = {"sum_z": (p,), "sum_y": (), "sum_zz": (p, p), "sum_zy": (p,)}
        candidate._sums, candidate._compensation = {}, {}
        for section, destination in (("sums", candidate._sums), ("compensation", candidate._compensation)):
            if not isinstance(body[section], dict) or set(body[section]) != set(shapes):
                raise ValueError("Sufficient-statistic schema differs")
            for name, shape in shapes.items():
                destination[name] = _finite_array(body[section][name], f"{section}.{name}", shape)
        if not np.allclose(candidate._sums["sum_zz"], candidate._sums["sum_zz"].T, rtol=0, atol=1e-10):
            raise ValueError("Second-moment matrix must be symmetric")
        A, b = self._system(candidate._sums, candidate._n)
        candidate._state_validation_eigendecompositions += 1
        self._spectrum(A, candidate._sums["sum_zz"])
        candidate._coef = _finite_array(body["coefficients"], "coefficients", (p,))
        candidate._intercept = float(_finite_array(body["intercept"], "intercept", ()))
        solved = body["solved_system"]
        if not isinstance(solved, dict) or set(solved) != {"A", "b", "mean_z", "mean_y"}:
            raise ValueError("Solved-system state invalid")
        old_A = _finite_array(solved["A"], "solved A", (p, p))
        old_b = _finite_array(solved["b"], "solved b", (p,))
        mean_z = _finite_array(solved["mean_z"], "solved mean_z", (p,))
        mean_y = float(_finite_array(solved["mean_y"], "solved mean_y", ()))
        if not np.allclose(old_A, old_A.T, rtol=0, atol=1e-10):
            raise ValueError("Solved matrix is not symmetric")
        candidate._state_validation_eigendecompositions += 1
        eigenvalues = np.linalg.eigvalsh(old_A)
        if eigenvalues[0] <= 0 or eigenvalues[-1] / eigenvalues[0] > self.MAX_CONDITION:
            raise ValueError("Solved state violates the condition/SPD policy")
        if not np.allclose(old_A @ candidate._coef, old_b, rtol=1e-8, atol=1e-10):
            raise ValueError("Coefficients do not solve the recorded system")
        if not np.isclose(candidate._intercept, mean_y - mean_z @ candidate._coef, rtol=1e-8, atol=1e-10):
            raise ValueError("Intercept does not match the unpenalized-intercept equation")
        if candidate._solved_n == candidate._n and (not np.array_equal(old_A, A) or not np.array_equal(old_b, b)
                or not np.array_equal(mean_z, candidate._sums["sum_z"] / candidate._n)
                or mean_y != float(candidate._sums["sum_y"]) / candidate._n):
            raise ValueError("Clean solved state does not match sufficient statistics")
        counts = body["counts"]
        count_names = {"updated_samples", "update_calls", "solve_calls", "condition_evaluations", "cholesky_factorizations"}
        if not isinstance(counts, dict) or set(counts) != count_names:
            raise ValueError("Model counters have an incompatible schema")
        for name in count_names:
            setattr(candidate, "_" + name, _integer(counts[name], name, 0))
        if (candidate._updated_samples != candidate._n - candidate._initial_n
                or not 0 <= candidate._update_calls <= candidate._updated_samples
                or (candidate._updated_samples > 0 and candidate._update_calls == 0)
                or not 1 <= candidate._solve_calls <= candidate._cholesky_factorizations <= candidate._condition_evaluations):
            raise ValueError("Model budget counters are inconsistent")
        candidate._condition_number = float(_finite_array(body["condition_number"], "condition_number", ()))
        candidate._min_scatter_eigenvalue = float(_finite_array(body["min_scatter_eigenvalue"], "min_scatter_eigenvalue", ()))
        if not np.isclose(candidate._condition_number, eigenvalues[-1]/eigenvalues[0], rtol=1e-12, atol=1e-12):
            raise ValueError("Stored condition diagnostic is inconsistent")
        if not np.isclose(candidate._min_scatter_eigenvalue, eigenvalues[0]-1., rtol=1e-12, atol=1e-12):
            raise ValueError("Stored scatter diagnostic is inconsistent")
        candidate._solved_system = json.loads(json.dumps(solved))
        candidate._model_version = body["model_version"]
        if candidate._model_version != _digest(candidate._version_record()):
            raise ValueError("Actual solved model version mismatch")
        candidate._initialized = True
        candidate._timings["state_load"] = perf_counter() - tick
        self.__dict__.clear()
        self.__dict__.update(candidate.__dict__)
        return self

    def diagnostics(self):
        self._require_initialized()
        arrays = [self._mean, self._scale, self._variance, self._coef,
                  *self._sums.values(), *self._compensation.values()]
        return {"initial_samples": self._initial_n, "n_samples": self._n, "solved_samples": self._solved_n,
                "dirty": self.dirty, "updated_samples": self._updated_samples, "update_calls": self._update_calls,
                "solve_calls": self._solve_calls, "condition_evaluations": self._condition_evaluations,
                "cholesky_factorizations": self._cholesky_factorizations,
                "training_sample_visits": self._n, "prediction_calls": self._prediction_calls,
                "prediction_rows": self._prediction_rows, "timings_seconds": dict(self._timings),
                "condition_number": self._condition_number, "min_scatter_eigenvalue": self._min_scatter_eigenvalue,
                "model_version": self._model_version, "state_bytes": len(json.dumps(self.state_dict(), sort_keys=True).encode()),
                "numeric_array_bytes": int(sum(a.nbytes for a in arrays)),
                "state_validation_eigendecompositions": self._state_validation_eigendecompositions,
                "precision": "float64", "forgetting": False, "intercept_penalized": False,
                "maturity_enforcement": "caller must release labels through the replay/queue gate"}
