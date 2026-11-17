"""Frozen monthly runner tests use sparse synthetic segments, never downloads."""

from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quantforge import data
from quantforge.features import build_features
from quantforge.labels import build_labels
from quantforge.datasets.research_splits import make_research_splits
from quantforge.research_experiment import (
    FOLD_MONTHS, FOLD_ROLES, ResearchConfig, TIME_COLUMNS, load_research_config,
    run_research_baseline,
)
import quantforge.research_experiment as experiment


@pytest.fixture
def research_fixture(tmp_path, monkeypatch, minute_bars):
    """Inject small artificial development-only data into the verified loader seam."""
    repository = Path(__file__).resolve().parents[1]
    source_config = (repository / "configs/research-baseline.toml").read_text(encoding="utf-8")
    source_policy = (repository / "configs/research-periods.toml").read_text(encoding="utf-8")
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/research-baseline.toml").write_text(source_config, encoding="utf-8")
    (tmp_path / "configs/research-periods.toml").write_text(source_policy, encoding="utf-8")
    starts = ("2025-09-01T00:00:00Z", "2026-02-28T22:00:00Z", "2026-03-31T22:00:00Z",
              "2026-04-30T22:00:00Z", "2026-05-31T22:00:00Z", "2026-06-30T20:00:00Z")
    bars = pd.concat([minute_bars(240, start=start) for start in starts], ignore_index=True)
    months = sorted(set(bars.open_time.dt.strftime("%Y-%m")))
    source = {"fixture": "SYNTHETIC sparse test data, not real market research",
              "overall_manifest_sha256": data.sha256(b"synthetic development manifest"),
              "month_sources": [{"month": month, "manifest_sha256": data.sha256(month.encode()),
                                 "fixture": "synthetic"} for month in months]}
    monkeypatch.setattr(experiment, "load_research_dataset", lambda manifest: (bars, source))
    return {"bars": bars, "source": source, "config": ResearchConfig(), "root": tmp_path}


def _without_timing(rows):
    return [{key: value for key, value in row.items() if key not in ("fit_seconds", "predict_seconds")} for row in rows]


def test_research_runner_fixed_folds_reproducibility_and_complete_artifacts(research_fixture):
    config = research_fixture["config"]
    first = run_research_baseline(config, config_path="configs/research-baseline.toml")
    second = run_research_baseline(config, config_path="configs/research-baseline.toml")
    assert first["status"] == "completed_research_baseline"
    assert len(first["metrics"]) == 24
    assert _without_timing(first["metrics"]) == _without_timing(second["metrics"])
    assert first["models"] == second["models"]
    assert first["config_sha256"] == second["config_sha256"]
    output = data.repository_path(first["output_directory"])
    other = data.repository_path(second["output_directory"])
    predictions = pd.read_parquet(output / "predictions.parquet")
    pd.testing.assert_frame_equal(predictions, pd.read_parquet(other / "predictions.parquet"), check_exact=True)
    assert first["prediction_rows"] == len(predictions)
    assert len(predictions.model_version.unique()) == 24
    assert set(TIME_COLUMNS).issubset(predictions.columns)
    assert predictions.source_month_manifest_sha256.notna().all()
    assert (predictions.source_month == predictions.bar_open_time.dt.strftime("%Y-%m")).all()
    assert set(predictions.month) == set(FOLD_MONTHS)
    assert (predictions.model_fit_time <= predictions.decision_time).all()
    assert (predictions.label_available_time <= predictions.evaluation_as_of).all()
    assert (predictions.decision_time < predictions.evaluation_as_of).all()
    # Prior-month final bar belongs to the new month by decision time, with its true source retained.
    boundary_row = predictions[predictions.bar_open_time == pd.Timestamp("2026-02-28T23:59:00Z")]
    assert not boundary_row.empty
    assert set(boundary_row.month) == {"2026-03"}
    assert set(boundary_row.source_month) == {"2026-02"}
    assert first["feature_diagnostics"]["feature_count"] == 14
    assert first["source"]["fixture"].startswith("SYNTHETIC")
    for artifact, expected_hash in first["artifacts_sha256"].items():
        assert data.sha256((output / artifact).read_bytes()) == expected_hash
    assert {"predictions.parquet", "metrics.csv", "fold-audit.json", "summary.json", "report.md", "source-config.toml"}.issubset({p.name for p in output.iterdir()})
    assert not (output / "failure.json").exists()
    assert not list(output.glob("*.partial"))
    for metric in first["metrics"]:
        assert metric["fit_seconds"] >= 0 and metric["predict_seconds"] >= 0
        assert metric["role"] == FOLD_ROLES[metric["fold"] - 1]
        assert metric["relative_mse_delta_zero"] is not None
        assert not {"p_value", "pvalue", "net_return", "sharpe", "max_drawdown"}.intersection(metric)
        if metric["model"] in {"zero", "historical_mean"}:
            assert metric["pearson_ic"] is None
            assert metric["pearson_ic_null_reason"] == "constant_prediction"
        if metric["model"] == "zero":
            assert metric["relative_mse_delta_zero"] == metric["relative_mae_delta_zero"] == 0.0


def test_runner_scaler_uses_only_mature_training_rows_and_no_eval_refit(research_fixture, monkeypatch):
    bars = research_fixture["bars"]
    features = build_features(bars)
    labels = build_labels(bars, 15)
    metadata = features.metadata.join(labels.frame.drop(columns="target_return"))
    fold = make_research_splits(metadata, horizon_minutes=15)[0]
    expected_train = features.frame.loc[fold.train_indices]
    first = run_research_baseline(research_fixture["config"])
    record = next(m for m in first["models"] if m["name"] == "ridge" and m["fold"] == 1 and m["horizon_minutes"] == 15)
    fitted = record["fitted_state"]
    np.testing.assert_allclose(fitted["scaler_mean"], expected_train.mean().to_numpy(), rtol=0, atol=1e-12)
    np.testing.assert_allclose(fitted["scaler_variance"], expected_train.var(ddof=0).to_numpy(), rtol=0, atol=1e-12)
    assert fitted["scaler_train_samples"] == len(expected_train)
    assert labels.frame.loc[fold.train_indices].label_available_time.le(fold.fit_time).all()
    changed = bars.copy(deep=True)
    future = changed.open_time >= pd.Timestamp("2026-03-01T00:00:00Z")
    for name in ("open", "high", "low", "close"):
        changed.loc[future, name] *= 3.0
    for name in ("volume", "taker_buy_base_asset_volume", "quote_asset_volume", "taker_buy_quote_asset_volume"):
        changed.loc[future, name] *= 10.0
    monkeypatch.setattr(experiment, "load_research_dataset", lambda manifest: (changed, research_fixture["source"]))
    second = run_research_baseline(research_fixture["config"])
    new_record = next(m for m in second["models"] if m["name"] == "ridge" and m["fold"] == 1 and m["horizon_minutes"] == 15)
    assert record == new_record


@pytest.mark.parametrize("old,new", [
    ('"2026-06"', '"2026-07"'),
    ('ridge_alpha = 1.0', 'ridge_alpha = 2.0'),
    ('seed = 42', 'seed = 43'),
    ('horizons_minutes = [15, 5]', 'horizons_minutes = [60]'),
    ('availability_delay_seconds = 1.0', 'availability_delay_seconds = 0.0'),
    ('development-manifest.json', 'holdout-manifest.json'),
    ('"configs/research-periods.toml"', '"configs/other-periods.toml"'),
    ('label_cutoff = "month_end"', 'label_cutoff = "next_month"'),
])
def test_config_overrides_rejected_before_any_data_read(research_fixture, monkeypatch, old, new):
    touched = []
    monkeypatch.setattr(experiment, "load_research_dataset", lambda *args: touched.append(args))
    path = data.repository_path("configs/bad.toml")
    text = data.repository_path("configs/research-baseline.toml").read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")
    with pytest.raises(ValueError):
        run_research_baseline(load_research_config(path))
    assert touched == []


def test_changed_period_policy_rejected_before_dataset_read(research_fixture, monkeypatch):
    touched = []
    monkeypatch.setattr(experiment, "load_research_dataset", lambda *args: touched.append(args))
    policy = data.repository_path("configs/research-periods.toml")
    policy.write_text(policy.read_text(encoding="utf-8").replace("2026-07-01T00:00:00Z", "2026-09-01T00:00:00Z"), encoding="utf-8")
    with pytest.raises(ValueError, match="frozen"):
        run_research_baseline(research_fixture["config"])
    assert touched == []
    root = data.repository_path("results/research-baseline")
    assert len(list(root.rglob("failure.json"))) == 1
    assert not list(root.rglob("summary.json"))


def test_out_of_scope_injected_data_rejected_before_features(research_fixture, monkeypatch, minute_bars):
    bad = minute_bars(100, start="2026-07-01T00:00:00Z")
    touched = []
    monkeypatch.setattr(experiment, "load_research_dataset", lambda manifest: (bad, research_fixture["source"]))
    monkeypatch.setattr(experiment, "build_features", lambda *args, **kwargs: touched.append(1))
    with pytest.raises(ValueError, match="holdout prohibited"):
        run_research_baseline(research_fixture["config"])
    assert touched == []


@pytest.mark.parametrize("stage", ["report", "parquet"])
def test_incomplete_outputs_never_have_success_summary(research_fixture, monkeypatch, stage):
    if stage == "report":
        def broken_report(summary):
            raise OSError("Synthetic report failure")
        monkeypatch.setattr(experiment, "_markdown_report", broken_report)
    else:
        original = experiment.pq.ParquetWriter
        class FailingWriter:
            def __init__(self, *args, **kwargs):
                self.writer = original(*args, **kwargs)
                self.calls = 0
            def write_table(self, table):
                self.calls += 1
                if self.calls == 2:
                    raise OSError("Synthetic parquet failure")
                self.writer.write_table(table)
            def close(self):
                self.writer.close()
        monkeypatch.setattr(experiment.pq, "ParquetWriter", FailingWriter)
    with pytest.raises(OSError, match="Synthetic"):
        run_research_baseline(research_fixture["config"])
    root = data.repository_path("results/research-baseline")
    assert not list(root.rglob("summary.json"))
    failure = json.loads(next(root.rglob("failure.json")).read_text(encoding="utf-8"))
    assert failure["status"] == "failed"
    assert failure["error_type"] == "OSError"


def test_config_source_mismatch_output_escape_and_extra_keys_rejected(research_fixture):
    with pytest.raises(ValueError, match="results/research-baseline"):
        replace(research_fixture["config"], output_directory="results/baseline-smoke")
    with pytest.raises(ValueError):
        replace(research_fixture["config"], output_directory="../outside")
    config = replace(research_fixture["config"], output_directory="results/research-baseline/other")
    with pytest.raises(ValueError, match="disagrees"):
        run_research_baseline(config, config_path="configs/research-baseline.toml")
    path = data.repository_path("configs/bad.toml")
    path.write_text(data.repository_path("configs/research-baseline.toml").read_text(encoding="utf-8") + '\n[holdout]\nallowed = true\n', encoding="utf-8")
    with pytest.raises(ValueError, match="sections"):
        load_research_config(path)
