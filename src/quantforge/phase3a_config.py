"""Immutable Phase 3A protocol gates, before synthetic construction or fitting."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tomllib

import pandas as pd

from quantforge import data, research_policy
from quantforge.features import FEATURE_COLUMNS
from quantforge.models.baselines import Baseline

DEFAULT_CONFIG = "configs/history-extension.toml"
MANIFEST_PATH = "docs/phase3a-plan-manifest.json"
MANIFEST_SHA256 = "ceb56c5683ff682240563c87991c96298b3f6c45baa93eee03134efc7d34c54a"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _exact_path(path: str | Path, expected: str) -> Path:
    """Reject alternate lexical paths before reading any payload."""
    root = data.REPOSITORY_ROOT.resolve()
    supplied = Path(path)
    lexical = supplied if supplied.is_absolute() else root / supplied
    canonical = root / expected
    if ".." in lexical.parts or lexical != canonical or lexical.resolve() != canonical:
        raise ValueError(f"Phase 3A requires the fixed path {expected}")
    return canonical


def verify_history_protocol() -> dict:
    """Verify raw bytes, including the pinned manifest and seven old sources."""
    manifest_path = _exact_path(MANIFEST_PATH, MANIFEST_PATH)
    if _hash(manifest_path) != MANIFEST_SHA256:
        raise ValueError("Phase 3A frozen manifest SHA256 mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    contracts = {**manifest["files"], **manifest["auxiliary"],
                 **manifest["prediction_code_sha256"],
                 "docs/confirmatory-protocol-v1.md": manifest["confirmatory_protocol_sha256"]}
    for relative, expected in contracts.items():
        if _hash(_exact_path(relative, relative)) != expected:
            raise ValueError(f"Phase 3A frozen fingerprint mismatch: {relative}")
    source = manifest["config_contract"]["power"]
    if _hash(_exact_path(source["source"], source["source"])) != source["source_sha256"]:
        raise ValueError("Phase 2E frozen evidence SHA256 mismatch")
    return manifest


def load_history_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    """Load the exact typed frozen configuration and check runtime contracts."""
    config_path = _exact_path(path, DEFAULT_CONFIG)
    manifest = verify_history_protocol()
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    if not research_policy._same_typed_value(config, manifest["config_contract"]):
        raise ValueError("Phase 3A configuration differs from frozen typed contract")
    if tuple(config["specification"]["v1_features"]) != FEATURE_COLUMNS:
        raise ValueError("Frozen feature order disagrees with code")
    experiment, model = config["experiment"], config["model"]
    default = Baseline("ridge")
    if (model["ridge_alpha"] != default.ridge_alpha or experiment["seed"] != default.random_seed
            or experiment["horizons"] != [15, 5] or experiment["precision"] != "float64"
            or experiment["cpu_threads"] != 1 or not experiment["synthetic_only"]
            or config["download"]["authorized"]):
        raise ValueError("Phase 3A runtime model or access constants disagree")
    if (model["solver"] != "svd" or model["fit_intercept"] is not True
            or experiment["availability_delay_seconds"] != 1):
        raise ValueError("Frozen solver/intercept/availability contract mismatch")
    for key, value in (("holdout_a", research_policy.HOLDOUT_A),
                       ("holdout_b", research_policy.HOLDOUT_B)):
        if tuple(pd.Timestamp(v) for v in config["holdouts"][key]) != value:
            raise ValueError("Frozen holdout constants disagree")
    expected_policy = json.loads(config["legacy_policy"]["canonical_json"])
    if not research_policy._same_typed_value(research_policy.canonical_policy(), expected_policy):
        raise ValueError("Legacy development policy changed")
    historical_contract = {
        "HISTORICAL_MONTHS": tuple(config["months"]["warm_up"] + config["months"]["evaluate"]),
        "HISTORICAL_WARMUP_MONTHS": tuple(config["months"]["warm_up"]),
        "HISTORICAL_EVALUATION_MONTHS": tuple(config["months"]["evaluate"]),
        "HISTORICAL_START": pd.Timestamp(config["months"]["start"]),
        "HISTORICAL_END": pd.Timestamp(config["months"]["end"]),
    }
    for name, expected in historical_contract.items():
        if getattr(research_policy, name, None) != expected:
            raise ValueError(f"Historical runtime constant disagrees with config: {name}")
    return copy.deepcopy(config)
