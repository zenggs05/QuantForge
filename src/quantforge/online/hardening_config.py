"""Frozen configuration and numerical contracts for opt-in C1 audits."""
from __future__ import annotations

import ast
import hashlib
import inspect
import json
from pathlib import Path
import tomllib

import numpy as np


class NumericalCrossCheckError(ValueError):
    """Independent raw-row references disagree beyond the frozen limits."""


def load_hardening_config(path=None) -> dict:
    """Read the hashed new configuration without changing legacy defaults."""
    root = Path(__file__).resolve().parents[3]
    relative = Path("configs/c1-engine-hardening.toml")
    config_path = root / relative if path is None else Path(path)
    if ".." in config_path.parts:
        raise ValueError("Hardening configuration path must be the fixed project configuration")
    if not config_path.is_absolute():
        config_path = root / config_path
    if config_path != root / relative:
        raise ValueError("Hardening configuration path must be the fixed project configuration")
    for candidate in (config_path, root / "docs/phase2f-plan-manifest.json", root / "docs/phase2f-plan.md"):
        if candidate.is_symlink() or candidate.parent.is_symlink() or candidate.resolve() != candidate:
            raise ValueError("Hardening configuration paths cannot contain symlinks or redirects")
    manifest = json.loads((root / "docs/phase2f-plan-manifest.json").read_text("utf-8"))
    raw = config_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest["config_sha256"]:
        raise ValueError("Hardening configuration SHA-256 differs from frozen manifest")
    if manifest.get("plan_path") != "docs/phase2f-plan.md" or manifest.get("config_path") != relative.as_posix():
        raise ValueError("Hardening manifest paths differ from the fixed project artifacts")
    plan = root / "docs/phase2f-plan.md"
    if hashlib.sha256(plan.read_bytes()).hexdigest() != manifest["plan_sha256"]:
        raise ValueError("Hardening plan SHA-256 differs from frozen manifest")
    config = tomllib.loads(raw.decode("utf-8"))
    expected = {"tolerance", "stability", "acceptance_graded", "checkpoint", "oracle", "synthetic", "budget"}
    if set(config) != expected:
        raise ValueError("Hardening configuration sections are incompatible")
    if (config["stability"]["cross_check_default"] is not False
            or config["stability"]["kahan_enabled"] is not True
            or config["checkpoint"]["format_version_default_for_save"] != 1
            or config["acceptance_graded"]["enabled"] is not True):
        raise ValueError("Hardening configuration changes frozen default behavior")
    return config


def graded_limits(condition, coef_ref, *, config=None) -> dict:
    """Return the predeclared relative-error limits for a centered system."""
    config = load_hardening_config() if config is None else config
    reference = np.asarray(coef_ref, dtype=np.float64)
    if (reference.ndim != 1 or not reference.size or not np.isfinite(reference).all()
            or not np.isfinite(condition) or condition < 1):
        raise ValueError("Graded limits require a finite reference and condition >= 1")
    eps = np.finfo(np.float64).eps
    multiplier = config["acceptance_graded"]["expected_floor_multiplier"]
    expected_floor = float(eps * condition * np.max(np.abs(reference)))
    return {
        "expected_floor": expected_floor,
        "coefficient_relative": max(config["tolerance"]["coef_atol"], multiplier * expected_floor),
        "prediction_relative_l2": max(config["tolerance"]["pred_rel_l2"], multiplier * eps * condition),
        "norm_denominator_floor": config["tolerance"]["norm_denominator_floor"],
    }


def assert_legacy_equivalence_config(config=None) -> None:
    """Check existing inline tolerances without modifying their frozen code."""
    config = load_hardening_config() if config is None else config
    from .foundation import equivalence_metrics
    from .incremental_ridge import IncrementalRidge

    found = {}
    tree = ast.parse(inspect.getsource(equivalence_metrics))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.comparators) != 1:
            continue
        left, right = node.left, node.comparators[0]
        if isinstance(left, ast.Name) and left.id in ("dc", "di"):
            prefix = "coef" if left.id == "dc" else "intercept"
            try:
                found[prefix + "_atol"] = ast.literal_eval(right.left)
                found[prefix + "_rtol"] = ast.literal_eval(right.right.left)
            except (AttributeError, ValueError) as exc:
                raise ValueError("Legacy equivalence tolerance expression changed") from exc
        elif isinstance(left, ast.Name) and left.id == "relative":
            found["pred_rel_l2"] = ast.literal_eval(right)
        elif (isinstance(left, ast.Call) and isinstance(left.func, ast.Attribute)
              and left.func.attr == "max"):
            found["pred_max_abs"] = ast.literal_eval(right)
    keys = {"coef_atol", "coef_rtol", "intercept_atol", "intercept_rtol", "pred_max_abs", "pred_rel_l2"}
    if set(found) != keys or any(found[key] != config["tolerance"][key] for key in keys):
        raise ValueError("Legacy equivalence tolerances differ from frozen hardening configuration")
    if IncrementalRidge.MAX_CONDITION != config["stability"]["condition_limit"]:
        raise ValueError("Legacy condition limit differs from frozen hardening configuration")
