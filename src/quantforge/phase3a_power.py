"""Conditional Phase 3A power planning from unchanged Phase 2E aggregates.

This module reads one pinned evidence CSV. It never fits a model, requests
market data, or treats the conditional MDE calculation as achieved power.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
from statistics import NormalDist

from quantforge.research_policy import fixed_project_path


EVIDENCE_LEVEL = "conditional_planning_from_exploratory_evidence"
SOURCE_PATH = "docs/phase2e-evidence/evidence-layers.csv"
_ENDPOINTS = {"CW": ("cw_se", "cw_mean"), "DM": ("dm_se", "dm_mean"),
              "IC": ("ic_se", "ic_mean")}
_NUMERIC_FIELDS = ("se_2E", "se_56", "mde_56", "pct_obs", "obs_mean")
_SOURCE_KEYS = {(15, "validation"), (15, "development_evaluation"),
                (5, "validation"), (5, "development_evaluation")}


def _number(value: object, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite real number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite real number") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(f"{name} must be finite" + (" and positive" if positive else ""))
    return result


def _close(actual: float, expected: float, tolerance: float, name: str) -> None:
    if not math.isfinite(actual) or not math.isclose(actual, expected, rel_tol=tolerance, abs_tol=0):
        raise ValueError(f"Power anchor mismatch: {name}")


def _anchors(config: dict) -> dict[str, dict]:
    raw = config["power"]["anchors"]
    required = {f"{h}_{role}_{test}" for h, role in _SOURCE_KEYS for test in _ENDPOINTS}
    if not isinstance(raw, list) or len(raw) != len(required):
        raise ValueError("Exactly twelve power anchors are required")
    anchors = {}
    for row in raw:
        if not isinstance(row, dict) or row.get("key") not in required:
            raise ValueError("Unknown or missing power anchor key")
        if row["key"] in anchors:
            raise ValueError("Duplicate power anchor key")
        if set(row) != {"key", *_NUMERIC_FIELDS}:
            raise ValueError("Incomplete or unexpected power anchor fields")
        for field in _NUMERIC_FIELDS:
            _number(row[field], f"{row['key']}.{field}", positive=field != "obs_mean")
        anchors[row["key"]] = row
    if set(anchors) != required:
        raise ValueError("Power anchor keys do not cover the fixed twelve endpoints")
    return anchors


def _evidence(config: dict) -> dict[tuple[int, str], dict]:
    # Reject a changed source lexically before resolving or reading that path.
    if config["power"]["source"] != SOURCE_PATH:
        raise ValueError("Power source must be the frozen Phase 2E evidence table")
    path = fixed_project_path(SOURCE_PATH, SOURCE_PATH)
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != config["power"]["source_sha256"]:
        raise ValueError("Phase 2E evidence source SHA-256 mismatch")
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8"), newline=""))
    fields = reader.fieldnames or []
    required = {"model", "baseline", "bandwidth", "pooling", "horizon_minutes", "role",
                "cw_t", *(field for pair in _ENDPOINTS.values() for field in pair)}
    if len(fields) != len(set(fields)) or not required.issubset(fields):
        raise ValueError("Phase 2E evidence CSV has missing or duplicate columns")
    result = {}
    for row in reader:
        if row["baseline"] != "zero" or row["bandwidth"] != "LLSW":
            continue
        if row["model"] != "ridge" or row["pooling"] != "n_weighted":
            raise ValueError("Selected evidence row violates the frozen model/pooling")
        try:
            horizon = int(row["horizon_minutes"])
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid evidence horizon") from exc
        key = (horizon, row["role"])
        if key not in _SOURCE_KEYS or key in result:
            raise ValueError("Unexpected or duplicate selected Phase 2E evidence row")
        # Validate every one of the 24 mean/SE inputs before any arithmetic.
        for se_field, mean_field in _ENDPOINTS.values():
            _number(row[se_field], se_field, positive=True)
            mean = _number(row[mean_field], mean_field)
            if mean == 0:
                raise ValueError("Observed effect must be nonzero for relative MDE planning")
        result[key] = row
    if set(result) != _SOURCE_KEYS:
        raise ValueError("Expected four unique Phase 2E planning rows")
    return result


def _validate_ap2(rows: dict, anchors: dict, tolerance: float) -> dict:
    source = rows[(15, "validation")]
    cw, dm = anchors["15_validation_CW"], anchors["15_validation_DM"]
    expected = {"cw_mean": float(cw["obs_mean"]), "cw_se": float(cw["se_2E"]),
                "cw_t": float(cw["obs_mean"]) / float(cw["se_2E"]),
                "dm_se": float(dm["se_2E"]), "dm_mean": float(dm["obs_mean"])}
    actual = {field: _number(source[field], field) for field in expected}
    for field, value in actual.items():
        _close(value, expected[field], tolerance, f"AP2.{field}")
    return actual


def validate_ap2(config: dict) -> dict:
    """Check the five AP2 CSV fields against the frozen anchor inputs."""
    tolerance = _number(config["power"]["machine_relative_tolerance"], "tolerance", positive=True)
    return _validate_ap2(_evidence(config), _anchors(config), tolerance)


def power_planning(config: dict) -> list[dict]:
    """Validate all twelve frozen anchors and return deterministic flat rows.

    The caller supplies the exact config returned by ``load_history_config``.
    A source hash, row-key, numeric, AP2 or anchor mismatch fails closed.
    All reported display strings are checked separately from machine values.
    """
    power = config["power"]
    anchors = _anchors(config)
    rows = _evidence(config)
    tolerance = _number(power["machine_relative_tolerance"], "machine tolerance", positive=True)
    display_tolerance = _number(power["display_relative_tolerance"], "display tolerance", positive=True)
    observed = _number(power["observed_months"], "observed months", positive=True)
    future = _number(power["future_months"], "future months", positive=True)
    scale = math.sqrt(observed / future)
    z_one = _number(power["z_one_sided"], "one-sided z", positive=True)
    z_two = _number(power["z_two_sided"], "two-sided z", positive=True)
    z_power = _number(power["z_power"], "power z", positive=True)
    alpha = _number(config["inference"]["alpha"], "alpha", positive=True)
    target = _number(power["target_power"], "target power", positive=True)
    if alpha >= 1 or target >= 1:
        raise ValueError("Alpha and target power must be below one")
    normal = NormalDist()
    for actual, expected, name in ((z_one, normal.inv_cdf(1-alpha), "one-sided z"),
                                  (z_two, normal.inv_cdf(1-alpha/2), "two-sided z"),
                                  (z_power, normal.inv_cdf(target), "power z")):
        _close(actual, expected, tolerance, name)
    _validate_ap2(rows, anchors, tolerance)
    output = []
    # Preserve the frozen anchor order: 15m then 5m, each role and CW/DM/IC.
    for key, anchor in anchors.items():
        prefix, endpoint = key.rsplit("_", 1)
        horizon_text, role = prefix.split("_", 1)
        horizon = int(horizon_text)
        source = rows[(horizon, role)]
        se_field, mean_field = _ENDPOINTS[endpoint]
        se_2e, mean = float(source[se_field]), float(source[mean_field])
        se_56 = se_2e * scale
        threshold = ((z_two if endpoint == "DM" else z_one) + z_power) * se_56
        values = {"se_2E": se_2e, "se_56": se_56, "mde_56": threshold,
                  "pct_obs": 100*threshold/abs(mean), "obs_mean": mean}
        display = {}
        for field, value in values.items():
            _close(value, float(anchor[field]), tolerance, f"{key}.{field}")
            # Five significant digits stay inside the frozen 1e-4 display bound.
            display[f"display_{field}"] = format(value, ".5g")
            _close(float(display[f"display_{field}"]), value, display_tolerance,
                   f"{key}.display_{field}")
        output.append({"key": key, "horizon_minutes": horizon, "role": role,
                       "test": endpoint, "evidence_level": EVIDENCE_LEVEL,
                       "source_sha256": power["source_sha256"], **values, **display,
                       "assumption": "independent identically distributed months [H]",
                       "alternative": "two-sided" if endpoint == "DM" else "greater",
                       "native_units": "dimensionless" if endpoint == "IC" else "return_squared"})
    return output
