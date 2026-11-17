"""Synthetic orchestration tests: frozen config, failure markers and final commit."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quantforge import data
from quantforge.diagnostics import block_bootstrap, experiment, paired_losses


def _synthetic_pairs():
    rows = []
    for month, role in (("2026-03", "validation"), ("2026-04", "validation"),
                        ("2026-05", "development_evaluation"), ("2026-06", "development_evaluation")):
        for horizon in (15, 5):
            for minute in range(4):
                target = .001 * (minute - 1)
                row = {"month": month, "role": role, "horizon_minutes": horizon,
                       "decision_time": pd.Timestamp(f"{month}-01T00:00:01Z") + pd.Timedelta(minutes=minute),
                       "target_return": target}
                for model, prediction in (("zero", 0.), ("historical_mean", .0005), ("ridge", target / 2)):
                    row[f"prediction_{model}"] = prediction
                    row[f"squared_loss_{model}"] = (prediction - target) ** 2
                    row[f"absolute_loss_{model}"] = abs(prediction - target)
                rows.append(row)
    return pd.DataFrame(rows)


@pytest.fixture
def diagnostic_workspace(tmp_path, monkeypatch):
    """Isolate all artifacts under pytest's project-local temporary tree."""
    original = data.REPOSITORY_ROOT
    for relative in ("configs/statistical-diagnostics.toml", "docs/phase2c-analysis-plan.md",
                     "docs/phase2c-plan-manifest.json", "scripts/run_statistical_diagnostics.py"):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((original / relative).read_bytes())
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(experiment, "_provenance", lambda: {
        "git_commit": "synthetic-test", "git_dirty": False, "source_sha256": {}, "dependency_versions": {},
    })
    monkeypatch.setattr(paired_losses, "load_paired_losses", lambda: (_synthetic_pairs(), {
        "status": "synthetic_test_only", "holdout_accessed": False, "market_bar_payloads_read": False,
    }))
    calls = []
    writer = experiment._write_json
    def recorded_write(path, value):
        path = Path(path)
        if path.name == "summary.json":
            assert not path.exists()
            assert not (path.parent / "failure.json").exists()
            assert value["artifacts_sha256"]
            for relative, expected in value["artifacts_sha256"].items():
                artifact = path.parent / relative
                assert artifact.is_file()
                assert hashlib.sha256(artifact.read_bytes()).hexdigest() == expected
        writer(path, value)
        calls.append(path.name)
    monkeypatch.setattr(experiment, "_write_json", recorded_write)
    def fake_figures(*args):
        output = Path(args[-1])
        output.mkdir(parents=True, exist_ok=True)
        paths = []
        for number in range(14):
            path = output / f"synthetic-figure-{number}.txt"
            path.write_text("Synthetic orchestration placeholder; not a figure or research result.", encoding="utf8")
            paths.append(path)
        return paths
    monkeypatch.setattr(experiment, "generate_figures", fake_figures)
    return tmp_path, calls


@pytest.mark.parametrize("old,new", [
    ("seed = 42", "seed = true"),
    ("replicates = 2000", "replicates = 100"),
    ("lags = [15, 60, 240]", "lags = [1, 15, 60]"),
    ("confidence = 0.95", "confidence = 0.9"),
    ('horizons = [15, 5]', 'horizons = [5, 15]'),
    ('"2026-05", "2026-06"', '"2026-07", "2026-08"'),
    ('directory = "results/statistical-diagnostics"', 'directory = "data/raw/forbidden"'),
    ('results/research-baseline/20261103T010111153259Z-a2cb0858/summary.json',
     'data/processed/binance/spot/BTCUSDT/1m/monthly/BTCUSDT-1m-2026-07.parquet'),
])
def test_config_overrides_fail_before_loading_predictions_or_creating_output(diagnostic_workspace, monkeypatch, old, new):
    root, calls = diagnostic_workspace
    path = root / "configs/statistical-diagnostics.toml"
    text = path.read_text(encoding="utf8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf8")
    monkeypatch.setattr(paired_losses, "load_paired_losses", lambda: pytest.fail("No input read is authorized"))
    with pytest.raises(ValueError, match="frozen"):
        experiment.run_diagnostics()
    assert not (root / "results").exists() and not calls


def test_config_file_cannot_be_a_market_artifact(diagnostic_workspace, monkeypatch):
    root, _ = diagnostic_workspace
    original_read = Path.read_text
    def forbid_market_read(path, *args, **kwargs):
        assert "data" not in path.relative_to(root).parts
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", forbid_market_read)
    with pytest.raises(ValueError, match="TOML inside configs"):
        experiment.run_diagnostics("data/processed/BTCUSDT-1m-2026-07.parquet")
    assert not (root / "results").exists()


def test_changed_plan_is_rejected_before_prediction_access(diagnostic_workspace, monkeypatch):
    root, calls = diagnostic_workspace
    with (root / "docs/phase2c-analysis-plan.md").open("ab") as handle:
        handle.write(b"\nSynthetic unauthorized plan change")
    monkeypatch.setattr(paired_losses, "load_paired_losses", lambda: pytest.fail("Plan is not frozen"))
    with pytest.raises(ValueError, match="plan SHA256 mismatch"):
        experiment.run_diagnostics()
    assert not calls and not (root / "results").exists()


@pytest.mark.parametrize("stage", ["pairing", "inference", "calibration", "figures", "report", "artifact_hash", "summary"])
def test_failure_records_never_publish_success_summary(diagnostic_workspace, monkeypatch, stage):
    root, calls = diagnostic_workspace
    def fail(*args, **kwargs):
        raise OSError(f"synthetic {stage} failure")
    if stage == "pairing":
        monkeypatch.setattr(paired_losses, "load_paired_losses", fail)
    elif stage == "inference":
        monkeypatch.setattr(block_bootstrap, "run_bootstrap", fail)
    elif stage == "calibration":
        monkeypatch.setattr(experiment, "run_calibration", fail)
    elif stage == "figures":
        monkeypatch.setattr(experiment, "generate_figures", fail)
    elif stage == "report":
        monkeypatch.setattr(experiment, "report", fail)
    elif stage == "artifact_hash":
        original_hash = experiment.file_hash
        def hash_fail(path):
            if Path(path).name == "bootstrap-results.csv":
                fail()
            return original_hash(path)
        monkeypatch.setattr(experiment, "file_hash", hash_fail)
    else:
        original_writer = experiment._write_json
        def summary_fail(path, value):
            if Path(path).name == "summary.json":
                fail()
            original_writer(path, value)
        monkeypatch.setattr(experiment, "_write_json", summary_fail)
    with pytest.raises(OSError, match=f"synthetic {stage} failure"):
        experiment.run_diagnostics()
    outputs = list((root / "results/statistical-diagnostics").iterdir())
    assert len(outputs) == 1
    directory = outputs[0]
    assert not (directory / "summary.json").exists()
    failure = json.loads((directory / "failure.json").read_text(encoding="utf8"))
    assert failure["status"] == "failed" and failure["error_type"] == "OSError"
    assert failure["plan_sha256"] == experiment.PLAN_HASH
    assert calls[-1] == "failure.json"


def test_success_summary_is_last_and_hashes_all_completed_artifacts(diagnostic_workspace):
    root, calls = diagnostic_workspace
    summary = experiment.run_diagnostics()
    assert summary["status"] == "completed_statistical_diagnostics"
    assert summary["holdout_accessed"] is False and summary["predictors_modified"] is False
    assert summary["new_market_downloads"] is False and summary["paired_observations"] == 32
    assert calls[-1] == "summary.json"
    assert summary["counts"]["bootstrap-results.csv"] == 128
    assert summary["counts"]["hac-results.csv"] == 96
    assert summary["counts"]["calibration-summary.csv"] == 24
    directory = root / summary["output_directory"]
    assert not (directory / "failure.json").exists()
    bootstrap = pd.read_csv(directory / "bootstrap-results.csv")
    assert bootstrap.status.eq("not_estimable").all()  # Four synthetic rows cannot estimate long blocks.
    assert len(summary["artifacts_sha256"]) == 24


def test_incomplete_analysis_grid_fails_without_summary(diagnostic_workspace, monkeypatch):
    root, calls = diagnostic_workspace
    monkeypatch.setattr(block_bootstrap, "run_bootstrap", lambda *args, **kwargs: pd.DataFrame({"synthetic": [1]}))
    with pytest.raises(ValueError, match="Incomplete fixed analysis grid"):
        experiment.run_diagnostics()
    directory = next((root / "results/statistical-diagnostics").iterdir())
    assert (directory / "failure.json").exists() and not (directory / "summary.json").exists()


def test_role_aggregation_uses_ratio_of_weighted_losses_and_keeps_roles_separate():
    # Unequal sample counts and base errors distinguish all plausible formulas.
    rows = []
    for month, role, count, baseline, ridge in (
        ("2026-03", "validation", 1, 1., .5), ("2026-04", "validation", 3, 4., 5.),
        ("2026-05", "development_evaluation", 1, 100., 1.),
        ("2026-06", "development_evaluation", 1, 100., 1.),
    ):
        for _ in range(count):
            row = {"month": month, "role": role, "horizon_minutes": 15}
            for model, value in (("zero", baseline), ("historical_mean", baseline), ("ridge", ridge)):
                row[f"squared_loss_{model}"] = value
                row[f"absolute_loss_{model}"] = np.sqrt(value)
            rows.append(row)
    summary = experiment.paired_summary(pd.DataFrame(rows))
    selected = summary[(summary.scope_type == "role") & (summary.role == "validation") & (summary.baseline == "zero")]
    n_weighted = selected[selected.aggregation == "n_weighted"].iloc[0]
    equal = selected[selected.aggregation == "month_equal"].iloc[0]
    assert n_weighted.mse_delta == pytest.approx(.625)
    assert n_weighted.relative_mse_delta == pytest.approx(.625 / 3.25)
    assert equal.mse_delta == pytest.approx(.25)
    assert equal.relative_mse_delta == pytest.approx(.25 / 2.5)
    assert set(summary[summary.scope_type == "role"].scope_id) == {"validation", "development_evaluation"}
