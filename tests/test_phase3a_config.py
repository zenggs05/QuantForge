"""Frozen-byte, typed-contract and runtime gates reject drift before execution."""

import copy
import json
from pathlib import Path
import tomllib

import pandas as pd
import pytest

from quantforge import data, phase3a_config as config_module, phase3a_runner as runner


@pytest.fixture
def frozen_config():
    return tomllib.loads((data.REPOSITORY_ROOT / config_module.DEFAULT_CONFIG).read_text(encoding="utf-8"))


def test_load_verifies_frozen_contract_and_returns_independent_nested_values():
    first = config_module.load_history_config()
    original = copy.deepcopy(first)
    first["specification"]["v1_features"].reverse()
    first["power"]["anchors"][0]["se_2E"] = "changed"
    second = config_module.load_history_config()
    assert second == original
    assert second == config_module.verify_history_protocol()["config_contract"]


@pytest.mark.parametrize("path", [
    "configs/alternate-history.toml", "configs/../configs/history-extension.toml",
    "../configs/history-extension.toml", "history-extension.toml",
])
def test_alternate_config_path_rejected_before_any_payload_read(path, monkeypatch):
    monkeypatch.setattr(config_module, "verify_history_protocol",
                        lambda: pytest.fail("Protocol bytes read before rejecting config path"))
    with pytest.raises(ValueError, match="fixed path"):
        config_module.load_history_config(path)


def test_manifest_hash_checked_before_json_decoding(monkeypatch):
    monkeypatch.setattr(config_module, "_hash", lambda path: "0" * 64)
    monkeypatch.setattr(config_module.json, "loads",
                        lambda *args, **kwargs: pytest.fail("Unverified manifest parsed"))
    with pytest.raises(ValueError, match="manifest SHA256 mismatch"):
        config_module.verify_history_protocol()


@pytest.mark.parametrize("relative", [
    "configs/history-extension.toml", "docs/phase3a-analysis-plan.md",
    "docs/confirmatory-protocol-v1-history-addendum.md",
    "docs/phase3a-download-manifest-draft.json", "docs/confirmatory-protocol-v1.md",
    "src/quantforge/features/__init__.py", "src/quantforge/labels/__init__.py",
    "src/quantforge/models/baselines.py", "src/quantforge/temporal.py",
    "src/quantforge/datasets/research_splits.py", "src/quantforge/datasets/splits.py",
    "src/quantforge/datasets/window_splits.py", "docs/phase2e-evidence/evidence-layers.csv",
])
def test_each_frozen_fingerprint_is_checked_before_toml_decoding(relative, monkeypatch):
    real_hash = config_module._hash
    target = data.REPOSITORY_ROOT / relative
    monkeypatch.setattr(config_module, "_hash", lambda path: "0" * 64 if path == target else real_hash(path))
    monkeypatch.setattr(config_module.tomllib, "loads",
                        lambda *args, **kwargs: pytest.fail("Config parsed after failed fingerprint"))
    with pytest.raises(ValueError, match="fingerprint mismatch|evidence SHA256 mismatch"):
        config_module.load_history_config()


@pytest.mark.parametrize("section,key,value", [
    ("model", "ridge_alpha", 1),  # Equality of values must not hide a type change.
    ("experiment", "cpu_threads", True),
    ("experiment", "horizons", [5, 15]),
    ("experiment", "synthetic_only", False),
    ("download", "authorized", True),
    ("inference", "llsw_constant", 1.4),
    ("inference", "secondary_family", ["S1", "S2", "S3", "S4"]),
    ("model", "solver", "cholesky"),
    ("timestamp", "cross_validate", False),
    ("budget", "ridge_fits_per_run", 225),
])
def test_modified_typed_configuration_is_rejected(frozen_config, monkeypatch, section, key, value):
    frozen_config[section][key] = value
    monkeypatch.setattr(config_module.tomllib, "loads", lambda text: frozen_config)
    with pytest.raises(ValueError, match="frozen typed contract"):
        config_module.load_history_config()


def test_feature_code_order_drift_is_rejected(monkeypatch):
    monkeypatch.setattr(config_module, "FEATURE_COLUMNS", tuple(reversed(config_module.FEATURE_COLUMNS)))
    with pytest.raises(ValueError, match="feature order"):
        config_module.load_history_config()


@pytest.mark.parametrize("attribute,value", [("ridge_alpha", 2.0), ("random_seed", 43)])
def test_baseline_default_drift_is_rejected(monkeypatch, attribute, value):
    real_baseline = config_module.Baseline

    def changed_default(name):
        model = real_baseline(name)
        setattr(model, attribute, value)
        return model

    monkeypatch.setattr(config_module, "Baseline", changed_default)
    with pytest.raises(ValueError, match="runtime model or access"):
        config_module.load_history_config()


@pytest.mark.parametrize("name", ["HOLDOUT_A", "HOLDOUT_B"])
def test_holdout_constant_drift_is_rejected(monkeypatch, name):
    policy = config_module.research_policy
    bounds = getattr(policy, name)
    monkeypatch.setattr(policy, name, (bounds[0] + pd.Timedelta(days=1), bounds[1]))
    with pytest.raises(ValueError, match="holdout constants"):
        config_module.load_history_config()


def test_legacy_policy_drift_is_rejected(monkeypatch):
    policy = copy.deepcopy(config_module.research_policy.canonical_policy())
    policy["access"]["final_holdout_access"] = True
    monkeypatch.setattr(config_module.research_policy, "canonical_policy", lambda: policy)
    with pytest.raises(ValueError, match="Legacy development policy changed"):
        config_module.load_history_config()


@pytest.mark.parametrize("name", [
    "HISTORICAL_MONTHS", "HISTORICAL_WARMUP_MONTHS", "HISTORICAL_EVALUATION_MONTHS",
    "HISTORICAL_START", "HISTORICAL_END",
])
def test_each_historical_runtime_constant_is_checked(monkeypatch, name):
    policy = config_module.research_policy
    value = getattr(policy, name)
    changed = value[:-1] if isinstance(value, tuple) else value + pd.Timedelta(days=1)
    monkeypatch.setattr(policy, name, changed)
    with pytest.raises(ValueError, match=f"Historical runtime constant disagrees with config: {name}"):
        config_module.load_history_config()


@pytest.mark.parametrize("value", [False, None, 1, "true"])
def test_runner_requires_literal_synthetic_flag_before_config_or_execution(monkeypatch, value):
    def forbidden(*args, **kwargs):
        pytest.fail("A non-synthetic invocation reached loading or execution")

    monkeypatch.setattr(runner, "load_history_config", forbidden)
    monkeypatch.setattr(runner, "_execute_synthetic", forbidden)
    with pytest.raises(ValueError, match="synthetic-only"):
        runner.run_phase3a(synthetic_only=value)


@pytest.mark.parametrize("output", [
    "results/phase3a", "results", ".tmp/phase3a-output-guard", "results/phase3a/../../.tmp/guard",
])
def test_runner_output_must_be_a_new_descendant_of_frozen_output_root(monkeypatch, output):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid output path reached directory creation or execution")

    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr(runner, "_execute_synthetic", forbidden)
    with pytest.raises(ValueError, match="descendant of results/phase3a"):
        runner.run_phase3a(synthetic_only=True, output_directory=output)


@pytest.mark.parametrize("redirect", ["base", "child"])
def test_runner_rejects_redirected_output_before_creation(frozen_config, monkeypatch, redirect):
    root = data.REPOSITORY_ROOT.resolve()
    base = root / frozen_config["output"]["directory"]
    output = base / "redirected-test-run"
    target = base if redirect == "base" else output
    original_resolve = Path.resolve

    def resolve_spy(path, *args, **kwargs):
        if path == target:
            return root / ".tmp" / "redirect-destination"
        return original_resolve(path, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("A redirected path reached directory creation or execution")

    monkeypatch.setattr(runner, "load_history_config", lambda path: frozen_config)
    monkeypatch.setattr(Path, "resolve", resolve_spy)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr(runner, "_execute_synthetic", forbidden)
    with pytest.raises(ValueError, match="redirect through a symlink"):
        runner.run_phase3a(synthetic_only=True, output_directory=output)


def test_lexical_output_escape_is_rejected_before_resolving_that_path(frozen_config, monkeypatch):
    root = data.REPOSITORY_ROOT.resolve()
    output = root / "results/phase3a/../outside"
    original_resolve = Path.resolve

    def resolve_spy(path, *args, **kwargs):
        assert path != output, "Escaping path resolved before lexical rejection"
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(runner, "load_history_config", lambda path: frozen_config)
    monkeypatch.setattr(Path, "resolve", resolve_spy)
    monkeypatch.setattr(runner, "_execute_synthetic", lambda *args: pytest.fail("Execution attempted"))
    with pytest.raises(ValueError, match="descendant of results/phase3a"):
        runner.run_phase3a(synthetic_only=True, output_directory=output)


def test_failed_public_run_writes_failure_audit_without_success_summary(frozen_config, monkeypatch, tmp_path):
    output = tmp_path / "results/phase3a/failed-test-run"
    executed = []

    def fail_execution(config, destination):
        assert config is frozen_config
        assert destination == output
        executed.append(destination)
        raise RuntimeError("Synthetic test exception before computation")

    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(runner, "load_history_config", lambda path: frozen_config)
    monkeypatch.setattr(runner, "_execute_synthetic", fail_execution)
    monkeypatch.setattr(runner.Baseline, "fit", lambda *args: pytest.fail("Unexpected fit"))
    with pytest.raises(RuntimeError, match="Synthetic test exception"):
        runner.run_phase3a(synthetic_only=True, output_directory=output)
    assert executed == [output]
    assert {path.name for path in output.iterdir()} == {"failure.json"}
    audit = json.loads((output / "failure.json").read_text(encoding="utf-8"))
    assert audit == {
        "status": "failed", "error_type": "RuntimeError",
        "error": "Synthetic test exception before computation", "evidence_level": runner.EVIDENCE,
    }
