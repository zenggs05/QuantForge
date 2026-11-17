"""Synthetic pairing/provenance adversaries; no market rows or network reads."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quantforge.diagnostics import paired_losses as pairing
from quantforge.features import FEATURE_COLUMNS
from quantforge.research_policy import canonical_policy


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _fake_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture
def paired_fixture():
    """Four artificial observations per fold, with an intentional valid month boundary."""
    sources = []
    for month in pairing.ALLOWED_MONTHS:
        start = pd.Timestamp(f"{month}-01T00:00:00Z")
        end = start + pd.offsets.MonthBegin(1)
        sources.append({
            "month": month,
            "manifest_path": f"data/processed/binance/spot/BTCUSDT/1m/monthly/BTCUSDT-1m-{month}.json",
            "manifest_sha256": _fake_hash(month), "archive_sha256": _fake_hash(month + "zip"),
            "csv_sha256": _fake_hash(month + "csv"), "parquet_sha256": _fake_hash(month + "parquet"),
            "rows": int((end - start) / pd.Timedelta(minutes=1)),
        })
    source_hashes = {item["month"]: item["manifest_sha256"] for item in sources}
    config = {
        "manifest_path": "data/processed/binance/spot/BTCUSDT/1m/development-manifest.json",
        "period_policy_path": "configs/research-periods.toml", "output_directory": "results/research-baseline",
        "horizons_minutes": [15, 5], "seed": 42, "availability_delay_seconds": 1.0, "ridge_alpha": 1.0,
        "fold_months": ["2026-03", "2026-04", "2026-05", "2026-06"],
        "roles": ["validation", "validation", "development_evaluation", "development_evaluation"],
        "label_cutoff": "month_end", "models": ["zero", "historical_mean", "ridge"],
        "feature_columns": list(FEATURE_COLUMNS), "selection": "fixed; no tuning",
    }
    summary = {
        "status": "completed_research_baseline", "config": config, "config_sha256": _digest(config),
        "period_policy": canonical_policy(), "folds": [], "models": [], "prediction_rows": 96,
        "source": {
            "status": "passed", "holdout_accessed": False, "policy": canonical_policy(),
            "rows": 436320, "missing_minutes": 0, "duplicate_minutes": 0,
            "start": "2025-09-01T00:00:00Z", "end_exclusive": "2026-07-01T00:00:00Z",
            "overall_manifest_path": config["manifest_path"], "overall_manifest_sha256": _fake_hash("development"),
            "month_sources": sources,
        },
    }
    rows = []
    for number, (month, role) in enumerate(zip(config["fold_months"], config["roles"], strict=True), 1):
        start = pd.Timestamp(f"{month}-01T00:00:00Z")
        end = start + pd.offsets.MonthBegin(1)
        for horizon in (15, 5):
            summary["folds"].append({
                "month": month, "horizon_minutes": horizon, "fold": number, "role": role,
                "fit_time": start.isoformat(), "eval_start": start.isoformat(), "eval_end": end.isoformat(),
                "evaluation_as_of": end.isoformat(), "train_samples": 100, "eval_samples": 4,
                "boundary_basis": "decision_time", "fit_at": "month_start", "evaluation_cutoff": "month_end",
                "last_train_label_available": (start - pd.Timedelta(seconds=59)).isoformat(),
                "first_eval_decision": (start + pd.Timedelta(seconds=1)).isoformat(),
                "last_eval_label_available": (start + pd.Timedelta(minutes=horizon + 3, seconds=1)).isoformat(),
            })
            for model in ("zero", "historical_mean", "ridge"):
                state = {"constant": 0.0} if model == "zero" else {"training_target_mean": .0005}
                if model == "ridge":
                    state = {"scaler_mean": [0.] * 14, "scaler_scale": [1.] * 14, "scaler_variance": [1.] * 14,
                             "scaler_train_samples": 100, "coefficients": [.001] * 14, "intercept": .0001}
                record = {
                    "model_contract_version": 1, "name": model, "ridge_alpha": 1.0 if model == "ridge" else None,
                    "random_seed": 42, "scikit_learn_version": "synthetic", "numpy_version": "synthetic",
                    "fitted": True, "train_samples": 100, "feature_names": list(FEATURE_COLUMNS),
                    "preprocessing": "StandardScaler fitted on supplied training rows" if model == "ridge" else "none",
                    "solver": "svd" if model == "ridge" else None, "hyperparameter_selection": "fixed configuration; no tuning",
                    "horizon_minutes": horizon, "fold": number, "fit_time": start.isoformat(),
                    "config_sha256": summary["config_sha256"], "fitted_state": state,
                }
                record["model_version"] = _digest(record)
                summary["models"].append(record)
                for step, target in enumerate((.001, .002, -.003, 0.)):
                    bar_open = start - pd.Timedelta(minutes=1) + pd.Timedelta(minutes=step)
                    bar_end = bar_open + pd.Timedelta(minutes=1)
                    target_end = bar_end + pd.Timedelta(minutes=horizon)
                    source_month = bar_open.strftime("%Y-%m")
                    rows.append({
                        "bar_open_time": bar_open, "bar_end_exclusive": bar_end,
                        "feature_available_time": bar_end + pd.Timedelta(seconds=1),
                        "decision_time": bar_end + pd.Timedelta(seconds=1), "target_start_time": bar_end,
                        "target_end_time": target_end, "label_available_time": target_end + pd.Timedelta(seconds=1),
                        "target_return": target, "prediction": 0. if model == "zero" else .0005 if model == "historical_mean" else target / 2,
                        "model": model, "model_version": record["model_version"], "horizon_minutes": horizon,
                        "fold": number, "month": month, "role": role, "model_fit_time": start,
                        "evaluation_as_of": end, "source_month": source_month,
                        "source_month_manifest_sha256": source_hashes[source_month],
                        "development_manifest_sha256": summary["source"]["overall_manifest_sha256"],
                    })
    return pd.DataFrame(rows), summary


def test_pairing_preserves_valid_previous_month_bar_and_loss_sign(paired_fixture):
    predictions, summary = paired_fixture
    wide, audit = pairing.validate_and_pair(predictions, summary)
    assert len(wide) == 32 and audit["input_rows"] == 96 and audit["dropped_rows"] == 0
    assert audit["holdout_accessed"] is False
    first = wide.iloc[0]
    assert first.month == "2026-03" and first.source_month == "2026-02"
    assert first.bar_open_time == pd.Timestamp("2026-02-28T23:59:00Z")
    assert first.decision_time == pd.Timestamp("2026-03-01T00:00:01Z")
    np.testing.assert_allclose(wide.squared_loss_zero, wide.target_return ** 2)
    np.testing.assert_allclose(wide.squared_loss_ridge, wide.target_return ** 2 / 4)
    np.testing.assert_allclose(wide.squared_loss_difference_ridge_minus_zero, -.75 * wide.target_return ** 2)
    np.testing.assert_allclose(wide.absolute_loss_difference_ridge_minus_zero, -.5 * abs(wide.target_return))
    assert wide.groupby(["month", "horizon_minutes"]).size().eq(4).all()


def test_pairing_is_order_independent_and_does_not_modify_input(paired_fixture):
    predictions, summary = paired_fixture
    before = predictions.copy(deep=True)
    left, _ = pairing.validate_and_pair(predictions, summary)
    right, _ = pairing.validate_and_pair(predictions.sample(frac=1, random_state=12), summary)
    pd.testing.assert_frame_equal(left, right)
    pd.testing.assert_frame_equal(predictions, before)


def test_missing_row_rejected_instead_of_inner_join(paired_fixture):
    predictions, summary = paired_fixture
    with pytest.raises(pairing.PairingError, match="row count"):
        pairing.validate_and_pair(predictions.iloc[1:], summary)


def test_duplicate_key_is_rejected_even_with_different_model_version(paired_fixture):
    predictions, summary = paired_fixture
    predictions.loc[1] = predictions.loc[0]
    predictions.loc[1, "model_version"] = "a" * 64
    with pytest.raises(pairing.PairingError, match="Duplicate prediction key"):
        pairing.validate_and_pair(predictions, summary)


@pytest.mark.parametrize("column,value,error", [
    ("prediction", np.inf, "finite"), ("target_return", np.nan, "Missing"),
    ("target_return", .012, "target_return"), ("role", "development_evaluation", "role/fold"),
    ("fold", 2, "role/fold"), ("model_version", "a" * 64, "model version"),
    ("source_month_manifest_sha256", "a" * 64, "source manifest"),
    ("development_manifest_sha256", "b" * 64, "source manifest"),
    ("source_month", "2026-07", "Source month"), ("month", "2026-07", "holdout"),
])
def test_bad_values_or_cross_model_disagreement_rejected(paired_fixture, column, value, error):
    predictions, summary = paired_fixture
    predictions.loc[8, column] = value  # A Ridge row in the first fold.
    with pytest.raises(pairing.PairingError, match=error):
        pairing.validate_and_pair(predictions, summary)


@pytest.mark.parametrize("column,error", [
    ("feature_available_time", "availability"), ("decision_time", "availability"),
    ("target_start_time", "maturity"), ("target_end_time", "maturity"),
    ("label_available_time", "maturity"), ("model_fit_time", "cutoff"),
    ("evaluation_as_of", "cutoff"),
])
def test_temporal_equations_and_boundaries_rejected(paired_fixture, column, error):
    predictions, summary = paired_fixture
    predictions.loc[8, column] += pd.Timedelta(seconds=1)
    with pytest.raises(pairing.PairingError, match=error):
        pairing.validate_and_pair(predictions, summary)


def test_naive_timestamp_rejected_without_guessing_timezone(paired_fixture):
    predictions, summary = paired_fixture
    predictions["decision_time"] = predictions.decision_time.dt.tz_localize(None)
    with pytest.raises(pairing.PairingError, match="explicit UTC"):
        pairing.validate_and_pair(predictions, summary)


def test_exact_key_sets_are_checked_and_gaps_are_preserved(paired_fixture):
    predictions, summary = paired_fixture
    # Artificially stretch each four-observation series to minutes 0, 2, 4, 6.
    starts = pd.to_datetime(predictions.month + "-01", utc=True)
    delta = predictions.decision_time - starts - pd.Timedelta(seconds=1)
    for column in pairing.TIME_COLUMNS:
        predictions[column] += delta
    for fold in summary["folds"]:
        fold["last_eval_label_available"] = (pd.Timestamp(fold["last_eval_label_available"]) + pd.Timedelta(minutes=3)).isoformat()
    wide, audit = pairing.validate_and_pair(predictions, summary)
    assert len(wide) == 32 and audit["dropped_rows"] == 0
    # Change only an interior Ridge observation, preserving count/min/max.
    for column in pairing.TIME_COLUMNS:
        predictions.loc[9, column] += pd.Timedelta(minutes=1)
    with pytest.raises(pairing.PairingError, match="identical one-to-one key sets"):
        pairing.validate_and_pair(predictions, summary)


def test_mature_label_cutoff_rejected_even_with_consistent_equations(paired_fixture):
    predictions, summary = paired_fixture
    indices = predictions.index[(predictions.month == "2026-06") & (predictions.horizon_minutes == 15)]
    for column in pairing.TIME_COLUMNS:
        predictions.loc[indices, column] += pd.Timedelta(days=29, hours=23, minutes=50)
    predictions.loc[indices, "source_month"] = predictions.loc[indices, "bar_open_time"].dt.strftime("%Y-%m")
    hashes = {item["month"]: item["manifest_sha256"] for item in summary["source"]["month_sources"]}
    predictions.loc[indices, "source_month_manifest_sha256"] = predictions.loc[indices, "source_month"].map(hashes)
    with pytest.raises(pairing.PairingError, match="Month membership|maturity/month"):
        pairing.validate_and_pair(predictions, summary)


@pytest.mark.parametrize("mutation,error", [
    (lambda s: s["source"].update(holdout_accessed=True), "holdout"),
    (lambda s: s["source"]["month_sources"][0].update(month="2026-07"), "ten development"),
    (lambda s: s["source"]["month_sources"][0].update(manifest_path="data/raw/secret.zip"), "manifest path"),
    (lambda s: s["folds"][0].update(last_train_label_available="2026-03-01T00:00:01Z"), "immature"),
    (lambda s: s["folds"].__setitem__(1, deepcopy(s["folds"][0])), "Duplicate fold"),
    (lambda s: s["models"].__setitem__(1, deepcopy(s["models"][0])), "Duplicate model"),
    (lambda s: s["models"][0].update(train_samples=999), "model metadata"),
    (lambda s: s["config"].update(seed=41), "config contract"),
])
def test_summary_provenance_rejections(paired_fixture, mutation, error):
    predictions, summary = paired_fixture
    mutation(summary)
    with pytest.raises(pairing.PairingError, match=error):
        pairing.validate_and_pair(predictions, summary)


def test_rehashed_model_state_cannot_override_fixed_hyperparameters(paired_fixture):
    predictions, summary = paired_fixture
    record = summary["models"][2]
    record["ridge_alpha"] = 99.
    record["model_version"] = _digest({k: v for k, v in record.items() if k != "model_version"})
    predictions.loc[:11, "model_version"] = record["model_version"]
    with pytest.raises(pairing.PairingError, match="hyperparameters"):
        pairing.validate_and_pair(predictions, summary)


def test_constant_model_cannot_use_evaluation_target(paired_fixture):
    predictions, summary = paired_fixture
    predictions.loc[4, "prediction"] = predictions.loc[4, "target_return"]
    with pytest.raises(pairing.PairingError, match="Constant model predictions"):
        pairing.validate_and_pair(predictions, summary)


def test_finite_inputs_that_overflow_loss_are_rejected(paired_fixture):
    predictions, summary = paired_fixture
    predictions.loc[[0, 4, 8], "target_return"] = 1e308
    with pytest.raises(pairing.PairingError, match="overflowed"):
        pairing.validate_and_pair(predictions, summary)


@pytest.mark.parametrize("path", [
    "data/raw/binance/spot/BTCUSDT/1m/monthly/BTCUSDT-1m-2026-07.zip",
    "results/research-baseline/latest/summary.json", "../summary.json",
])
def test_production_path_gate_runs_before_any_read(monkeypatch, path):
    def forbidden(*args, **kwargs):
        pytest.fail("Rejected path must never be opened")
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(pd, "read_parquet", forbidden)
    with pytest.raises(ValueError, match="fixed development path"):
        pairing.load_paired_losses(summary_path=path)


def test_manifest_cannot_redirect_production_run_before_parquet_read(monkeypatch):
    def fake_json(*args, **kwargs):
        return {"status": "frozen_before_statistical_implementation", "clean_baseline_check": {
            "clean_summary_path": "data/processed/BTCUSDT-1m-2026-07.parquet",
        }}, "a" * 64
    monkeypatch.setattr(pairing, "_verified_json", fake_json)
    monkeypatch.setattr(pd, "read_parquet", lambda *args, **kwargs: pytest.fail("No Parquet read authorized"))
    with pytest.raises(pairing.PairingError, match="plan manifest changed"):
        pairing.load_paired_losses()


def test_json_digest_checked_before_parsing(tmp_path, monkeypatch):
    metadata = tmp_path / "synthetic.json"
    metadata.write_bytes(b"not even valid JSON")
    monkeypatch.setattr(pairing, "fixed_project_path", lambda *args: metadata)
    with pytest.raises(pairing.PairingError, match="SHA-256 mismatch"):
        pairing._verified_json("synthetic.json", "synthetic.json", "a" * 64)


def test_parquet_digest_failure_cannot_trigger_parser(tmp_path, monkeypatch):
    artifact = tmp_path / "synthetic.parquet"
    artifact.write_bytes(b"synthetic corrupt prediction bytes")
    monkeypatch.setattr(pd, "read_parquet", lambda *args: pytest.fail("Hash must be checked before parsing rows"))
    with pytest.raises(pairing.PairingError, match="Parquet SHA-256 mismatch"):
        pairing._read_verified_predictions(artifact)


def test_parquet_hash_and_read_share_one_file_handle(tmp_path, monkeypatch):
    artifact = tmp_path / "synthetic.parquet"
    payload = b"synthetic bytes for an instrumented parser"
    artifact.write_bytes(payload)
    monkeypatch.setattr(pairing, "FROZEN_PREDICTION_SHA256", hashlib.sha256(payload).hexdigest())
    def parser(handle):
        assert handle.tell() == 0
        assert handle.read() == payload
        return pd.DataFrame({"synthetic": [1]})
    monkeypatch.setattr(pd, "read_parquet", parser)
    frame, digest = pairing._read_verified_predictions(artifact)
    assert frame.synthetic.tolist() == [1] and digest == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize("unit", ["ns", "us"])
def test_exact_production_grid_accepts_equivalent_timestamp_storage_units(unit):
    decisions = pd.date_range("2026-04-01T00:00:01Z", periods=43200 - 15, freq="min").as_unit(unit)
    wide = pd.DataFrame({"month": "2026-04", "horizon_minutes": 15, "decision_time": decisions})
    pairing._validate_prediction_grid(wide)
    wide.loc[1, "decision_time"] += pd.Timedelta(microseconds=1)
    with pytest.raises(pairing.PairingError, match="minute grid changed"):
        pairing._validate_prediction_grid(wide)
