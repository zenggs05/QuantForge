"""Synthetic-only tests of audited calendar-window model comparisons."""

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from threadpoolctl import threadpool_limits

from quantforge import data
from quantforge.datasets.window_splits import make_window_splits
from quantforge.features import build_features
from quantforge.labels import build_labels, mature_labels
from quantforge.models.baselines import Baseline
from quantforge.research_experiment import ResearchConfig, run_research_baseline
from quantforge.window_experiment import PROGRAMS, RIDGE_PROGRAMS, load_window_config, run_window_comparison
import quantforge.research_experiment as baseline_experiment
import quantforge.window_experiment as experiment


def test_march_six_month_ridge_exactly_equals_expanding_first(minute_bars):
    """First Phase 2D-A model test: identical information yields identical models."""
    starts = ("2025-09-01T00:00:00Z", "2026-02-28T22:00:00Z",
              "2026-03-31T22:00:00Z", "2026-04-30T22:00:00Z",
              "2026-05-31T22:00:00Z", "2026-06-30T20:00:00Z")
    bars = pd.concat([minute_bars(240, start=start) for start in starts], ignore_index=True)
    features = build_features(bars)
    for horizon in (15, 5):
        labels = build_labels(bars, horizon)
        metadata = features.metadata.join(labels.frame.drop(columns="target_return"))
        windows = make_window_splits(metadata, horizon)
        expanding, rolling = windows["expanding"][0], windows["rolling_6m"][0]
        pd.testing.assert_index_equal(expanding.train_indices, rolling.train_indices, exact=True)
        pd.testing.assert_index_equal(expanding.eval_indices, rolling.eval_indices, exact=True)
        left_X = features.frame.loc[expanding.train_indices]
        right_X = features.frame.loc[rolling.train_indices]
        left_y = mature_labels(labels.frame, expanding.fit_time, expanding.train_indices)
        right_y = mature_labels(labels.frame, rolling.fit_time, rolling.train_indices)
        pd.testing.assert_frame_equal(left_X, right_X, check_exact=True)
        pd.testing.assert_series_equal(left_y, right_y, check_exact=True)
        with threadpool_limits(limits=1):
            left = Baseline("ridge").fit(left_X, left_y)
            right = Baseline("ridge").fit(right_X, right_y)
            for attr in ("mean_", "scale_", "var_"):
                np.testing.assert_array_equal(getattr(left.pipeline["scaler"], attr),
                                              getattr(right.pipeline["scaler"], attr))
            np.testing.assert_array_equal(left.pipeline["model"].coef_, right.pipeline["model"].coef_)
            assert left.pipeline["model"].intercept_ == right.pipeline["model"].intercept_
            np.testing.assert_array_equal(left.predict(features.frame.loc[expanding.eval_indices]),
                                          right.predict(features.frame.loc[rolling.eval_indices]))


@pytest.fixture
def window_fixture(tmp_path, monkeypatch, minute_bars):
    """SYNTHETIC sparse data and independent original-runner reference, no markets."""
    repository = Path(__file__).resolve().parents[1]
    files = ("configs/rolling-windows.toml", "configs/research-baseline.toml", "configs/research-periods.toml",
             "docs/phase2d-a-analysis-plan.md", "docs/phase2d-a-plan-manifest.json", "scripts/run_window_comparison.py")
    for relative in files:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((repository / relative).read_bytes())
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    starts = ("2025-09-01T00:00:00Z", "2026-02-28T22:00:00Z", "2026-03-31T22:00:00Z",
              "2026-04-30T22:00:00Z", "2026-05-31T22:00:00Z", "2026-06-30T20:00:00Z")
    bars = pd.concat([minute_bars(240, start=start) for start in starts], ignore_index=True)
    months = sorted(set(bars.open_time.dt.strftime("%Y-%m")))
    source = {"fixture": "SYNTHETIC test data, not market evidence", "holdout_accessed": False,
              "overall_manifest_sha256": data.sha256(b"synthetic development manifest"),
              "month_sources": [{"month": month, "manifest_sha256": data.sha256(month.encode())} for month in months]}
    monkeypatch.setattr(baseline_experiment, "load_research_dataset", lambda manifest: (bars, source))
    baseline = run_research_baseline(ResearchConfig())
    baseline_path = tmp_path / baseline["output_directory"]
    predictions = pd.read_parquet(baseline_path / "predictions.parquet")
    key = ["horizon_minutes", "month", "decision_time"]
    wide = predictions.loc[predictions.model.eq("zero")].sort_values(key).reset_index(drop=True)
    wide = wide.drop(columns=["model", "model_version", "prediction"])
    for name in ("zero", "historical_mean", "ridge"):
        wide[f"prediction_{name}"] = predictions.loc[predictions.model.eq(name)].sort_values(key).prediction.to_numpy()
    hashes = {name: data.sha256((baseline_path / name).read_bytes()) for name in ("summary.json", "predictions.parquet")}
    audit = {"fixture": "SYNTHETIC", "status": "passed", "predictions_path":
             (baseline_path / "predictions.parquet").relative_to(tmp_path).as_posix()}
    monkeypatch.setattr(experiment, "_load_frozen_reference", lambda config: (wide.copy(), copy.deepcopy(baseline), audit))
    def verify_synthetic_reference(config, ignored):
        for name, digest in hashes.items():
            assert data.sha256((baseline_path / name).read_bytes()) == digest
    monkeypatch.setattr(experiment, "_verify_reference_unchanged", verify_synthetic_reference)
    monkeypatch.setattr(experiment, "load_research_dataset", lambda manifest: (bars, source))
    def clean_provenance():
        return {"git_commit": "a" * 40, "git_dirty": False, "git_status": "", "dependency_versions": {},
                "source_sha256": {}, "numeric_thread_limit": 1, "fixture": "SYNTHETIC provenance"}
    monkeypatch.setattr(experiment, "_provenance", clean_provenance)
    # Statistical computations remain real; figure completeness is tested with
    # explicit deterministic placeholders, never described as research figures.
    import quantforge.diagnostics.window_figures as figures
    def synthetic_figures(metrics, bootstrap, hac, output):
        output.mkdir(parents=True, exist_ok=True)
        for name in figures.FIGURE_NAMES:
            (output / name).write_bytes(b"SYNTHETIC figure contract fixture, not a research image")
        return list(figures.FIGURE_NAMES)
    monkeypatch.setattr(figures, "create_window_figures", synthetic_figures)
    return {"bars": bars, "source": source, "wide": wide, "baseline": baseline, "audit": audit,
            "root": tmp_path, "baseline_path": baseline_path, "hashes": hashes}


def test_runner_synthetic_exact_reference_determinism_and_complete_outputs(window_fixture):
    first = run_window_comparison()
    second = run_window_comparison(run_kind="reproduction")
    assert first["status"] == second["status"] == "completed_window_comparison"
    assert first["run_kind"] == "formal" and second["run_kind"] == "reproduction"
    assert first["models"] == second["models"]
    assert first["metrics"] == second["metrics"]
    assert first["budget"]["ridge_fits"] == second["budget"]["ridge_fits"] == 32
    assert first["budget"]["reference_fits"] == 16
    assert len(first["metrics"]) == len(first["models"]) == 48
    assert first["frozen_reference_exact"] and len(first["march_6m_equivalence"]) == 2
    assert first["provenance"]["git_dirty"] is False
    assert first["source"]["fixture"].startswith("SYNTHETIC")
    assert first["holdout_accessed"] is False and first["new_market_downloads"] is False
    output = data.repository_path(first["output_directory"])
    other = data.repository_path(second["output_directory"])
    predictions = pd.read_parquet(output / "predictions.parquet")
    pd.testing.assert_frame_equal(predictions, pd.read_parquet(other / "predictions.parquet"), check_exact=True)
    assert len(predictions) == 6 * len(window_fixture["wide"]) == first["prediction_rows"]
    assert set(predictions.model) == set(PROGRAMS)
    assert predictions.source_month_manifest_sha256.notna().all()
    for name in first["deterministic_artifacts"]:
        assert (output / name).read_bytes() == (other / name).read_bytes(), name
    for name, digest in first["artifacts_sha256"].items():
        assert data.sha256((output / name).read_bytes()) == digest
    assert not list(output.glob("*.partial")) and not (output / "failure.json").exists()
    assert (output / "summary.json").is_file()
    audit = json.loads((output / "window-audit.json").read_text())
    assert len(audit["windows"]) == 32
    for horizon in (15, 5):
        for month in ("2026-03", "2026-04", "2026-05", "2026-06"):
            subset = predictions.loc[predictions.horizon_minutes.eq(horizon) & predictions.month.eq(month)]
            for name in PROGRAMS:
                selected = subset.loc[subset.model.eq(name)]
                reference = subset.loc[subset.model.eq("expanding")]
                assert np.array_equal(selected.decision_time.to_numpy(), reference.decision_time.to_numpy())
                assert np.array_equal(selected.target_return.to_numpy(), reference.target_return.to_numpy())
    times = pd.read_csv(output / "model-timings.csv")
    assert (times[["fit_seconds", "predict_seconds"]] >= 0).all().all()
    assert "fit_seconds" not in pd.read_csv(output / "metrics.csv")
    assert first["budget"]["ridge_training_row_visits"] == sum(m["train_samples"] for m in first["models"] if m["name"] == "ridge")
    for name, digest in window_fixture["hashes"].items():
        assert data.sha256((window_fixture["baseline_path"] / name).read_bytes()) == digest


def test_scaler_fits_exact_legal_window_rows_and_training_labels(window_fixture):
    summary = run_window_comparison()
    bars = window_fixture["bars"]
    features = build_features(bars)
    for horizon in (15, 5):
        labels = build_labels(bars, horizon)
        metadata = features.metadata.join(labels.frame.drop(columns="target_return"))
        windows = make_window_splits(metadata, horizon)
        for name in RIDGE_PROGRAMS:
            for number, fold in enumerate(windows[name], 1):
                train = features.frame.loc[fold.train_indices]
                record = next(m for m in summary["models"] if m["program"] == name and m["fold"] == number
                              and m["horizon_minutes"] == horizon)
                state = record["fitted_state"]
                np.testing.assert_allclose(state["scaler_mean"], train.mean().to_numpy(), rtol=0, atol=1e-12)
                np.testing.assert_allclose(state["scaler_variance"], train.var(ddof=0).to_numpy(), rtol=0, atol=1e-12)
                assert state["scaler_train_samples"] == len(train)
                assert metadata.loc[fold.train_indices].label_available_time.le(fold.fit_time).all()
                assert metadata.loc[fold.train_indices].decision_time.lt(fold.fit_time).all()


@pytest.mark.parametrize("dirty", [True, None])
def test_dirty_or_unknown_worktree_fails_before_any_reference_or_market_read(window_fixture, monkeypatch, dirty):
    touched = []
    monkeypatch.setattr(experiment, "_provenance", lambda: {"git_dirty": dirty, "git_commit": "a" * 40})
    monkeypatch.setattr(experiment, "_load_frozen_reference", lambda *args: touched.append("reference"))
    monkeypatch.setattr(experiment, "load_research_dataset", lambda *args: touched.append("market"))
    with pytest.raises(ValueError, match="clean committed"):
        run_window_comparison()
    assert touched == []
    root = window_fixture["root"] / "results/rolling-window-comparison"
    assert len(list(root.rglob("failure.json"))) == 1
    assert not list(root.rglob("summary.json"))


@pytest.mark.parametrize("old,new", [
    ('"2026-06"', '"2026-07"'), ('ridge_alpha = 1.0', 'ridge_alpha = 2.0'),
    ('seed = 42', 'seed = 43'), ('cpu_threads = 1', 'cpu_threads = 2'),
    ('"rolling_3m"', '"rolling_2m"'), ('development-manifest.json', 'holdout-manifest.json'),
    ('results/rolling-window-comparison', 'results/research-baseline'),
    ('replicates = 2000', 'replicates = 100'),
])
def test_frozen_configuration_refuses_changes_before_reads(window_fixture, monkeypatch, old, new):
    touched = []
    monkeypatch.setattr(experiment, "_load_frozen_reference", lambda *args: touched.append("reference"))
    monkeypatch.setattr(experiment, "load_research_dataset", lambda *args: touched.append("market"))
    path = window_fixture["root"] / "configs/rolling-windows.toml"
    contents = path.read_text(encoding="utf-8")
    assert old in contents
    path.write_text(contents.replace(old, new), encoding="utf-8")
    with pytest.raises(ValueError, match="configuration SHA256"):
        run_window_comparison()
    assert touched == []
    root = window_fixture["root"] / "results/rolling-window-comparison"
    assert len(list(root.rglob("failure.json"))) == 1
    assert not list(root.rglob("summary.json"))


def test_unapproved_path_rejected_before_open(window_fixture, monkeypatch):
    touched = []
    original = Path.read_bytes
    def guarded_read(path):
        touched.append(str(path))
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", guarded_read)
    with pytest.raises(ValueError, match="configs"):
        load_window_config("data/processed/binance/spot/BTCUSDT/1m/monthly/BTCUSDT-1m-2026-07.parquet")
    assert touched == []


@pytest.mark.parametrize("stage", ["report", "figure", "statistics", "reference", "parquet"])
def test_incomplete_or_disagreeing_results_never_mark_success(window_fixture, monkeypatch, stage):
    if stage == "report":
        def broken(*args):
            raise OSError("SYNTHETIC report failure")
        monkeypatch.setattr(experiment, "_markdown_report", broken)
    elif stage == "figure":
        import quantforge.diagnostics.window_figures as figures
        monkeypatch.setattr(figures, "create_window_figures", lambda *args: list(figures.FIGURE_NAMES))
    elif stage == "statistics":
        import quantforge.diagnostics.window_comparison as statistics
        original = statistics.analyze_window_predictions
        def incomplete(frame):
            result = original(frame)
            result["hac_results"] = result["hac_results"].iloc[:-1]
            return result
        monkeypatch.setattr(statistics, "analyze_window_predictions", incomplete)
    elif stage == "reference":
        window_fixture["wide"].loc[0, "prediction_ridge"] += 0.1
    else:
        original = experiment.pq.ParquetWriter
        class BrokenWriter:
            def __init__(self, *args, **kwargs):
                self.writer = original(*args, **kwargs)
            def write_table(self, table):
                raise OSError("SYNTHETIC parquet failure")
            def close(self):
                self.writer.close()
        monkeypatch.setattr(experiment.pq, "ParquetWriter", BrokenWriter)
    with pytest.raises((ValueError, OSError)):
        run_window_comparison()
    root = window_fixture["root"] / "results/rolling-window-comparison"
    assert not list(root.rglob("summary.json"))
    failure = json.loads(next(root.rglob("failure.json")).read_text())
    assert failure["status"] == "failed"
    assert failure["ridge_fits_attempted"] <= 32


def test_injected_out_of_scope_data_rejected_before_features(window_fixture, monkeypatch, minute_bars):
    touched = []
    # Artificial timestamps only; no final-holdout file is opened.
    bad = minute_bars(120, "2026-07-01T00:00:00Z")
    monkeypatch.setattr(experiment, "load_research_dataset", lambda *args: (bad, window_fixture["source"]))
    monkeypatch.setattr(experiment, "build_features", lambda *args, **kwargs: touched.append(1))
    with pytest.raises(ValueError, match="holdout prohibited"):
        run_window_comparison()
    assert touched == []


@pytest.mark.parametrize("structure_change", [False, True], ids=["synthetic_stationary", "synthetic_known_break"])
def test_synthetic_window_comparison_has_expected_bias_variance_direction(structure_change):
    """Known artificial conditional relation; not evidence of actual market drift."""
    rng = np.random.default_rng(42)
    index = pd.date_range("2025-09-01", "2026-07-01", inclusive="left", freq="h", tz="UTC")
    end = index + pd.Timedelta(minutes=1)
    metadata = pd.DataFrame({"bar_end_exclusive": end, "feature_available_time": end + pd.Timedelta(seconds=1),
        "decision_time": end + pd.Timedelta(seconds=1), "target_start_time": end,
        "target_end_time": end + pd.Timedelta(minutes=15), "label_available_time": end + pd.Timedelta(minutes=15, seconds=1),
        "valid_features": True, "valid_label": True}, index=index)
    X = pd.DataFrame({"synthetic_signal": rng.normal(size=len(index))}, index=index)
    coefficient = np.ones(len(index))
    if structure_change:
        coefficient[index >= pd.Timestamp("2026-02-01T00:00:00Z")] = -1
    y = pd.Series(coefficient * X.synthetic_signal.to_numpy(), index=index)
    windows = make_window_splits(metadata, 15)
    losses = {}
    with threadpool_limits(limits=1):
        for name in RIDGE_PROGRAMS:
            fold = windows[name][0]
            model = Baseline("ridge").fit(X.loc[fold.train_indices], y.loc[fold.train_indices])
            predicted = model.predict(X.loc[fold.eval_indices])
            losses[name] = np.mean((predicted - y.loc[fold.eval_indices].to_numpy()) ** 2)
    assert losses["rolling_6m"] == losses["expanding"]
    if structure_change:
        assert losses["rolling_1m"] < losses["rolling_3m"] < losses["expanding"]
    else:
        # In the noiseless stationary case, more rows reduce fixed-alpha shrinkage.
        assert losses["expanding"] < losses["rolling_3m"] < losses["rolling_1m"] < 1e-5


def test_constant_prediction_and_linear_signal_metrics_are_explicit():
    y = np.array([-2., -1., 0., 1., 2.])
    zero = experiment._comparison_metrics(y, np.zeros(5), float(np.mean(y*y)), float(np.mean(y*y)))
    assert zero["pearson_ic"] is zero["spearman_correlation"] is zero["diagnostic_slope"] is None
    assert zero["diagnostic_slope_null_reason"] == "constant_prediction"
    assert zero["mse_improvement_percent_expanding"] == 0
    exact = experiment._comparison_metrics(y, y, float(np.mean(y*y)), float(np.mean(y*y)))
    assert exact["mse"] == exact["mae"] == 0
    assert exact["diagnostic_slope"] == 1
    assert exact["mse_improvement_percent_expanding"] == 100
