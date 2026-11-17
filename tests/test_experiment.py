"""End-to-end smoke tests use local synthetic archives, never market downloads."""

from dataclasses import replace
import io
import json
from pathlib import Path
import zipfile

import pandas as pd
import pytest

from quantforge import data
from quantforge.experiment import ExperimentConfig, load_config, load_verified_sample, run_baseline
from quantforge.datasets.splits import SplitConfig
import quantforge.experiment as experiment


@pytest.fixture
def sample_config(tmp_path, monkeypatch, minute_bars):
    """Create a truthful synthetic manifest inside pytest's repository-local tmp."""
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    bars = minute_bars(1440)
    raw = bars.copy()
    raw["open_time"] = raw.open_time.astype("int64") // (1000 if str(raw.open_time.dtype).startswith("datetime64[ns") else 1)
    raw["close_time"] = raw.close_time.astype("int64") // (1000 if str(raw.close_time.dtype).startswith("datetime64[ns") else 1)
    raw["ignore"] = 0
    raw["number_of_trades"] = raw.number_of_trades.astype("int64")
    csv = raw[data.COLUMNS].to_csv(index=False, header=False).encode()
    name = "BTCUSDT-1m-2026-09-01.zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name.replace(".zip", ".csv"), csv)
    zipped = buffer.getvalue()
    (tmp_path / "data/raw").mkdir(parents=True)
    (tmp_path / "data/processed").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    raw_path = tmp_path / "data/raw" / name
    raw_path.write_bytes(zipped)
    Path(str(raw_path) + ".CHECKSUM").write_text(f"{data.sha256(zipped)}  {name}\n", encoding="ascii")
    parsed, validation = data.parse_klines(csv, pd.Timestamp("2026-09-01").date())
    parquet_path = tmp_path / "data/processed/sample.parquet"
    parsed.to_parquet(parquet_path, index=False)
    manifest = {"status": "passed", "symbol": "BTCUSDT", "market": "spot", "interval": "1m",
                "date_utc": "2026-09-01", "raw_path": raw_path.relative_to(tmp_path).as_posix(),
                "parquet_path": parquet_path.relative_to(tmp_path).as_posix(),
                "archive_sha256": data.sha256(zipped), "csv_sha256": data.sha256(csv),
                "parquet_sha256": data.sha256(parquet_path.read_bytes()), "validation": validation,
                "fixture": "SYNTHETIC, not a download or market result"}
    (tmp_path / "docs/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return ExperimentConfig(parquet_path="data/processed/sample.parquet", manifest_path="docs/manifest.json")


def test_runner_reproducible_predictions_metadata_and_exclusions(sample_config):
    first = run_baseline(sample_config)
    second = run_baseline(sample_config)
    assert first["status"] == "completed_smoke_test"
    assert first["metrics"] == second["metrics"]
    assert len(first["metrics"]) == 6
    assert {m["train_samples"] for m in first["metrics"] if m["horizon_minutes"] == 15} == {706}
    assert first["feature_diagnostics"]["excluded_rows"] == 60
    assert first["horizon_diagnostics"][0]["labels"]["excluded_rows"] == 15
    path = data.repository_path(first["output_directory"])
    predictions = pd.read_parquet(path / "predictions.parquet")
    again = pd.read_parquet(data.repository_path(second["output_directory"]) / "predictions.parquet")
    pd.testing.assert_frame_equal(predictions, again, check_exact=True)
    assert len(predictions) == 6 * 240
    assert (predictions.decision_time >= predictions.feature_available_time).all()
    assert (predictions.evaluation_as_of >= predictions.label_available_time).all()
    assert "SMOKE TEST" in (path / "report.md").read_text(encoding="utf-8")
    assert first["artifacts"]["predictions_sha256"] == data.sha256((path / "predictions.parquet").read_bytes())
    assert all("scikit_learn_version" in model for model in first["models"])
    assert not (path / "failure.json").exists()


@pytest.mark.parametrize("mode", ["expanding", "rolling"])
def test_runner_walk_forward_modes_are_supported(sample_config, mode):
    config = replace(sample_config, horizons_minutes=(15,), split=SplitConfig(mode=mode))
    result = run_baseline(config)
    folds = result["horizon_diagnostics"][0]["folds"]
    assert len(folds) == 3
    assert not folds[-1]["eval_window_complete"]
    assert folds[-1]["eval_samples"] == 165
    if mode == "expanding":
        assert folds[-1]["train_samples"] > folds[0]["train_samples"]
    else:
        assert folds[-1]["train_samples"] == folds[0]["train_samples"]


def test_manifest_hash_tampering_rejected(sample_config):
    path = data.repository_path(sample_config.parquet_path)
    content = bytearray(path.read_bytes())
    content[len(content) // 2] ^= 1
    path.write_bytes(content)
    with pytest.raises(data.DataValidationError, match="Parquet SHA256"):
        load_verified_sample(sample_config)


def test_insufficient_samples_produce_failure_not_success(sample_config):
    with pytest.raises(ValueError, match="Insufficient eligible samples"):
        run_baseline(replace(sample_config, min_total_samples=2000))
    root = data.repository_path(sample_config.output_directory)
    assert not list(root.rglob("summary.json"))
    failure = json.loads(next(root.rglob("failure.json")).read_text(encoding="utf-8"))
    assert failure["status"] == "failed"
    assert "SMOKE TEST ONLY" in failure["limitation"]


def test_report_failure_does_not_leave_success_summary(sample_config, monkeypatch):
    def broken_report(summary):
        raise OSError("Simulated report-writing failure")
    monkeypatch.setattr(experiment, "_markdown_report", broken_report)
    with pytest.raises(OSError, match="report-writing"):
        run_baseline(sample_config)
    root = data.repository_path(sample_config.output_directory)
    assert not list(root.rglob("summary.json"))
    assert len(list(root.rglob("failure.json"))) == 1


def test_provenance_failure_is_recorded(sample_config, monkeypatch):
    def broken_provenance():
        raise OSError("Simulated metadata failure")
    monkeypatch.setattr(experiment, "_provenance", broken_provenance)
    with pytest.raises(OSError, match="metadata failure"):
        run_baseline(sample_config)
    assert len(list(data.repository_path(sample_config.output_directory).rglob("failure.json"))) == 1


def test_configuration_rejects_escape_and_non_smoke_mode(sample_config):
    with pytest.raises(ValueError, match="inside"):
        replace(sample_config, parquet_path="../outside.parquet")
    with pytest.raises(ValueError, match="results/baseline-smoke"):
        replace(sample_config, output_directory="src")
    config_path = data.repository_path("bad.toml")
    config_path.write_text('[experiment]\nmode="research"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="sections"):
        load_config(config_path)
