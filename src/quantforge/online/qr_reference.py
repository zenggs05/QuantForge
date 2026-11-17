"""ENG-4 INDEPENDENCE CONTRACT:
This module operates solely on raw (x, y) tuples passed by the caller plus
the fixed initial scaler (mean, scale). It does not read any sufficient
statistic from IncrementalRidge. See docs/phase2f-plan.md for the AST-based
independence guard (BP7).
"""
from __future__ import annotations

import numpy as np
from scipy.linalg import solve_triangular

from .hardening_config import load_hardening_config
from .incremental_ridge import NumericalStabilityError


def _finite(value, shape, name):
    original = np.asarray(value)
    if original.dtype.kind not in "iuf":
        raise ValueError(f"QR {name} must contain real numeric values")
    result = np.array(value, dtype=np.float64, copy=True)
    if result.shape != shape or not np.isfinite(result).all():
        raise ValueError(f"QR {name} has invalid shape or nonfinite values")
    return result


def _append_row(matrix, transformed_target, row, target):
    """Apply Givens rotations to one independent augmented observation."""
    row = row.copy()
    remainder = float(target)
    for column in range(len(row)):
        diagonal, incoming = matrix[column, column], row[column]
        radius = np.hypot(diagonal, incoming)
        if radius == 0:
            continue
        cosine, sine = diagonal / radius, incoming / radius
        old = matrix[column, column:].copy()
        tail = row[column:].copy()
        matrix[column, column:] = cosine * old + sine * tail
        row[column:] = -sine * old + cosine * tail
        old_target = transformed_target[column]
        transformed_target[column] = cosine * old_target + sine * remainder
        remainder = -sine * old_target + cosine * remainder


class OnlineQRReference:
    """Fixed-scaler, raw-row QR reference with an unpenalized intercept."""

    def __init__(self, mean, scale):
        shape = np.asarray(mean).shape
        if len(shape) != 1 or not shape[0]:
            raise ValueError("QR scaler must have a nonempty fixed width")
        self._mean = _finite(mean, shape, "mean")
        self._scale = _finite(scale, shape, "scale")
        if (self._scale <= 0).any():
            raise ValueError("QR scaler scale must be positive")
        self._mean.setflags(write=False)
        self._scale.setflags(write=False)
        width = len(self._mean) + 1
        self.R = np.zeros((width, width), dtype=np.float64)
        self.Qt_y = np.zeros(width, dtype=np.float64)
        self.n = 0
        self.solve_count = 0
        self.condition_number = None

    def update_from_raw(self, x, y):
        raw = _finite(x, self._mean.shape, "features")
        target = _finite(y, (), "target").item()
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            row = np.r_[(raw - self._mean) / self._scale, 1.0]
        if not np.isfinite(row).all():
            raise NumericalStabilityError("QR fixed feature transformation overflowed")
        matrix, transformed = self.R.copy(), self.Qt_y.copy()
        with np.errstate(over="ignore", invalid="ignore"):
            _append_row(matrix, transformed, row, target)
        if not np.isfinite(matrix).all() or not np.isfinite(transformed).all():
            raise NumericalStabilityError("QR Givens update overflowed")
        self.R, self.Qt_y = matrix, transformed
        self.n += 1
        return self

    def update_batch_from_raw(self, X, y):
        raw = np.asarray(X)
        if raw.ndim != 2 or not len(raw):
            raise ValueError("QR raw batch must be a nonempty matrix")
        raw = _finite(raw, (len(raw), len(self._mean)), "features")
        targets = _finite(y, (len(raw),), "targets")
        for row, target in zip(raw, targets):
            self.update_from_raw(row, target)
        return self

    def solve_ridge(self, alpha: float = 1.0):
        if not isinstance(alpha, (int, float)) or isinstance(alpha, bool) or alpha != 1.0:
            raise ValueError("QR reference alpha is frozen at 1.0")
        if self.n == 0:
            raise ValueError("QR reference requires raw observations before solving")
        config = load_hardening_config()
        p = len(self._mean)
        # Orthogonal column reordering puts the intercept first. Eliminating
        # that column centers the predictors without subtracting raw moments.
        permutation = [p, *range(p)]
        _, centered = np.linalg.qr(self.R[:, permutation])
        singular = np.linalg.svd(centered[1:, 1:], compute_uv=False)
        spectrum = singular * singular + alpha
        condition = float(spectrum.max() / spectrum.min())
        self.condition_number = condition
        if not np.isfinite(condition) or condition > config["stability"]["condition_limit"]:
            raise NumericalStabilityError("QR centered Ridge condition number exceeds frozen 1e12 limit")
        matrix, transformed = self.R.copy(), self.Qt_y.copy()
        for column in range(p):
            penalty = np.zeros(p + 1)
            penalty[column] = np.sqrt(alpha)
            _append_row(matrix, transformed, penalty, 0.0)
        try:
            solution = solve_triangular(matrix, transformed, lower=False, check_finite=True)
        except (ValueError, np.linalg.LinAlgError) as exc:
            raise NumericalStabilityError("QR Ridge triangular solve failed") from exc
        if not np.isfinite(solution).all():
            raise NumericalStabilityError("QR Ridge solution is nonfinite")
        self.solve_count += 1
        return solution[:p].copy(), float(solution[p])

    def state_bytes(self) -> int:
        """Count retained float64 arrays, excluding Python object overhead."""
        return int(sum(value.nbytes for value in (self._mean, self._scale, self.R, self.Qt_y)))
