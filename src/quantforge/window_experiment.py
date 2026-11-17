"""Frozen, offline calendar-window comparison with exact baseline reconciliation.

Only the training-window program changes. Prediction rows, causal features,
label maturity, estimator and numerical thread count retain Phase 2B contracts.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
from time import perf_counter
import tomllib
from uuid import uuid4

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from threadpoolctl import threadpool_info, threadpool_limits

from quantforge import data
from quantforge.datasets.research_dataset import load_research_dataset
from quantforge.datasets.window_splits import make_window_splits
from quantforge.diagnostics.calibration import calibration_metrics
from quantforge.experiment import _provenance, _write_json
from quantforge.features import FEATURE_COLUMNS, FEATURE_FORMULAS, build_features
from quantforge.labels import build_labels, mature_labels
from quantforge.models.baselines import Baseline
from quantforge.research_experiment import FOLD_MONTHS, FOLD_ROLES, TIME_COLUMNS, _file_hash, _record_model
from quantforge.research_policy import DEVELOPMENT_END, DEVELOPMENT_START, load_period_policy
from quantforge.temporal import utc_index

PLAN_SHA256 = "263368b6153750ba314dcd024878c61214141976b056d30871a6d384ccba7617"
CONFIG_SHA256 = "9f3f75dd21b6bef5e41a309a7cdb73efc5ae908bc33b1d57d4e654d4d882acf4"
RIDGE_PROGRAMS = ("expanding", "rolling_1m", "rolling_3m", "rolling_6m")
REFERENCE_PROGRAMS = ("zero", "historical_mean")
PROGRAMS = (*RIDGE_PROGRAMS, *REFERENCE_PROGRAMS)
LIMITATION = (
    "Exploratory follow-up on four already observed development months, not new confirmatory OOS evidence. "
    "Only the fixed estimation window changes. Fixed alpha does not imply identical per-sample regularization; "
    "scaling, sample size and estimation variance also change. No drift trigger, online update, recalibration, "
    "market download, final-holdout access, orders or trading-performance claims. Month-start model readiness "
    "is logical; measured fit time is not simulated execution latency."
)


def load_window_config(path: str | Path = "configs/rolling-windows.toml") -> dict:
    """Require the committed bytes and plan before any market or reference read."""
    config_path = data.repository_path(path)
    if config_path.suffix != ".toml" or not config_path.is_relative_to(data.repository_path("configs")):
        raise ValueError("Window config must be a repository-local TOML under configs")
    payload = config_path.read_bytes()
    if data.sha256(payload) != CONFIG_SHA256:
        raise ValueError("Frozen window configuration SHA256 mismatch; overrides are forbidden")
    config = tomllib.loads(payload.decode("utf-8"))
    if _file_hash(data.repository_path(config["plan"]["path"])) != PLAN_SHA256:
        raise ValueError("Frozen Phase 2D-A analysis plan SHA256 mismatch")
    manifest = json.loads(data.repository_path("docs/phase2d-a-plan-manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("status") != "frozen_before_algorithm_implementation"
            or manifest.get("plan_sha256") != PLAN_SHA256 or manifest.get("config_sha256") != CONFIG_SHA256):
        raise ValueError("Frozen Phase 2D-A plan manifest disagrees")
    return config


def _load_frozen_reference(config: dict) -> tuple[pd.DataFrame, dict, dict]:
    """Reuse the unchanged Phase 2C reference identity and provenance gates."""
    from quantforge.diagnostics.paired_losses import load_paired_losses
    paired, audit = load_paired_losses(config["reference"]["summary_path"])
    summary_path = data.repository_path(config["reference"]["summary_path"])
    if _file_hash(summary_path) != config["reference"]["summary_sha256"]:
        raise ValueError("Frozen reference summary changed")
    return paired, json.loads(summary_path.read_text(encoding="utf-8")), audit


def _index_hash(index: pd.DatetimeIndex) -> str:
    return hashlib.sha256(index.as_unit("ns").asi8.astype("<i8").tobytes()).hexdigest()


def _verify_reference_unchanged(config: dict, reference_audit: dict) -> None:
    if _file_hash(data.repository_path(config["reference"]["summary_path"])) != config["reference"]["summary_sha256"]:
        raise ValueError("Frozen reference summary changed during run")
    if _file_hash(data.repository_path(reference_audit["predictions_path"])) != config["reference"]["prediction_sha256"]:
        raise ValueError("Frozen reference predictions changed during run")


def _assert_reference_prediction(prediction: pd.DataFrame, reference: pd.DataFrame, name: str) -> None:
    """Compare complete keys and values; never narrow either side by a join."""
    left = prediction.sort_values("decision_time").reset_index(drop=True)
    right = reference.sort_values("decision_time").reset_index(drop=True)
    if len(left) != len(right):
        raise ValueError("Expanding/frozen reference evaluation row count differs")
    shared = (*TIME_COLUMNS, "target_return", "horizon_minutes", "fold", "month", "role",
              "model_fit_time", "evaluation_as_of", "source_month", "source_month_manifest_sha256",
              "development_manifest_sha256")
    for column in shared:
        if not left[column].eq(right[column]).all():
            raise ValueError(f"Expanding/frozen reference mismatch in {column}")
    old_name = "ridge" if name == "expanding" else name
    if not np.array_equal(left.prediction.to_numpy(), right[f"prediction_{old_name}"].to_numpy()):
        raise ValueError(f"{name} does not exactly reproduce frozen reference predictions")


def _assert_model_state(record: dict, reference: dict) -> None:
    for key in ("name", "feature_names", "train_samples", "fitted_state", "solver", "ridge_alpha"):
        if record[key] != reference[key]:
            raise ValueError(f"Frozen/March 6M fitted-model equivalence failed: {key}")


def _comparison_metrics(truth, predicted, expanding_mse: float, zero_mse: float) -> dict:
    result = calibration_metrics(predicted, truth)
    for name, denominator in (("expanding", expanding_mse), ("zero", zero_mse)):
        delta = (result["mse"] - denominator) / denominator if denominator > 0 else None
        result[f"relative_mse_delta_{name}"] = delta
        result[f"mse_improvement_percent_{name}"] = None if delta is None else -100 * delta
        result[f"relative_mse_null_reason_{name}"] = None if delta is not None else "zero_reference_mse"
    result["diagnostic_slope_null_reason"] = "constant_prediction" if result["diagnostic_slope"] is None else None
    result["std_ratio_null_reason"] = "constant_target" if result["std_ratio"] is None else None
    return result


def _write_csv(output: Path, name: str, frame: pd.DataFrame) -> None:
    path = data.repository_path(output / name)
    temporary = path.with_name(path.name + ".partial")
    frame.to_csv(temporary, index=False)
    temporary.replace(path)


def _markdown_report(summary: dict, metrics: pd.DataFrame, bootstrap: pd.DataFrame, hac: pd.DataFrame) -> str:
    from quantforge.diagnostics.experiment import _markdown_table
    lines = ["# Phase 2D-A exploratory calendar-window comparison", "", LIMITATION, "",
             "Negative relative MSE delta favors the named model; improvement percentage is its negative times 100. "
             "All four observed months and both horizons are retained. Fit/predict times are in model-timings.csv.", "",
             "## Monthly prediction metrics", "",
             _markdown_table(metrics, ["month", "role", "horizon_minutes", "model", "train_samples", "n_samples",
                 "mse", "mae", "pearson_ic", "spearman_correlation", "prediction_bias", "prediction_std",
                 "std_ratio", "diagnostic_slope", "mse_improvement_percent_expanding", "mse_improvement_percent_zero"]),
             "", "## Main block length L=1440 (all candidates and aggregations)", "",
             bootstrap.loc[bootstrap.block_length.eq(1440)].to_csv(index=False), "",
             "## Main HAC lag=60 (all candidates and aggregations)", "",
             hac.loc[hac.lag.eq(60)].to_csv(index=False), "",
             "## Audit and limitations", "",
             f"Ridge fits: {summary['budget']['ridge_fits']}; Ridge training-row visits: {summary['budget']['ridge_training_row_visits']}. "
             "March Rolling 6M has exact training rows, targets, scaler, coefficients and predictions versus Expanding. "
             "All Expanding/Zero/Mean predictions exactly reproduce the frozen Phase 2B reference.", "",
             "The primary contrast is 15m Rolling 3M minus Expanding, Validation, observation weighted. "
             "The primary Holm family contains six 15m candidate-role tests at lag60. All other results remain sensitivities. "
             "Confidence intervals are marginal, not simultaneous; Holm correction does not repair prior observation. "
             "Only two adjacent months per role are available; role inference assumes zero cross-month covariance. "
             "Heavy tails, overlapping labels, finite blocks and nonstationarity limit inference. "
             "Fixed alpha, different scalers and different sample sizes prevent attributing every difference to drift. "
             "Positive IC or lower MSE does not establish tradable alpha or cross-regime generalization.", ""]
    return "\n".join(lines)


def run_window_comparison(config_path: str | Path = "configs/rolling-windows.toml", *, run_kind: str = "formal") -> dict:
    """Fit 32 Ridge models, reconcile frozen predictions, then write success last."""
    if run_kind not in ("formal", "reproduction"):
        raise ValueError("run_kind must be formal or the separately counted reproduction")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
    # Allocate an immutable run directory before validating the caller path so
    # configuration failures are auditable without opening market/reference data.
    output = data.repository_path(f"results/rolling-window-comparison/{run_id}")
    if not output.is_relative_to(data.repository_path("results/rolling-window-comparison")):
        raise ValueError("Window output must remain in its separate results subtree")
    output.mkdir(parents=True, exist_ok=False)
    provenance = None
    started = perf_counter()
    stages, metrics, audits, models, timings, equivalences = {}, [], [], [], [], []
    ridge_fits = reference_fits = prediction_count = 0
    config = None
    try:
        config = load_window_config(config_path)
        configured_root = data.repository_path(config["output"]["directory"])
        if configured_root != data.repository_path("results/rolling-window-comparison"):
            raise ValueError("Frozen output directory must be results/rolling-window-comparison")
        policy = load_period_policy(config["data"]["period_policy_path"])
        provenance = _provenance()
        if provenance.get("git_dirty") is not False or not provenance.get("git_commit"):
            raise ValueError("Formal and reproduction runs require a clean committed Git worktree before data reads")
        provenance["dependency_versions"]["matplotlib"] = importlib.metadata.version("matplotlib")
        for relative in (str(config_path).replace("\\", "/"), "scripts/run_window_comparison.py",
                         config["plan"]["path"], "docs/phase2d-a-plan-manifest.json", config["data"]["period_policy_path"]):
            path = data.repository_path(relative)
            provenance["source_sha256"][path.relative_to(data.REPOSITORY_ROOT).as_posix()] = _file_hash(path)
        code_hash = data.sha256(json.dumps(provenance["source_sha256"], sort_keys=True).encode())
        _write_json(output / "resolved-config.json", config)
        _write_json(output / "period-policy.json", policy)
        tick = perf_counter()
        reference, reference_summary, reference_audit = _load_frozen_reference(config)
        reference_models = {(r["fold"], r["horizon_minutes"], r["name"]): r for r in reference_summary["models"]}
        stages["reference_validation"] = perf_counter() - tick
        tick = perf_counter()
        bars, source = load_research_dataset(config["data"]["manifest_path"])
        stages["load"] = perf_counter() - tick
        index = utc_index(bars.open_time)
        if (index < DEVELOPMENT_START).any() or (index >= DEVELOPMENT_END).any():
            raise ValueError("Window input exceeds development bounds; holdout prohibited")
        if source["overall_manifest_sha256"] != reference_summary["source"]["overall_manifest_sha256"]:
            raise ValueError("Window source differs from frozen baseline development manifest")
        month_hashes = {item["month"]: item["manifest_sha256"] for item in source["month_sources"]}
        if not set(index.strftime("%Y-%m")).issubset(month_hashes):
            raise ValueError("Every development month needs source provenance")
        delay = pd.Timedelta(seconds=1)
        tick = perf_counter()
        features = build_features(bars, availability_delay=delay)
        stages["features"] = perf_counter() - tick
        if tuple(features.frame.columns) != FEATURE_COLUMNS or len(FEATURE_COLUMNS) != 14:
            raise ValueError("Existing 14-feature order must remain unchanged")
        horizon_diagnostics = []
        partial = output / "predictions.parquet.partial"
        writer = None
        try:
            with threadpool_limits(limits=1):
                pools = threadpool_info()
                if any(pool["num_threads"] != 1 for pool in pools):
                    raise ValueError("Numerical libraries did not honor the one-thread limit")
                for horizon in (15, 5):
                    labels = build_labels(bars, horizon, availability_delay=delay)
                    horizon_diagnostics.append({"horizon_minutes": horizon, **labels.diagnostics})
                    metadata = features.metadata.join(labels.frame.drop(columns="target_return"))
                    windows = make_window_splits(metadata, horizon)
                    if set(windows) != set(RIDGE_PROGRAMS) or any(len(v) != 4 for v in windows.values()):
                        raise ValueError("All four window programs and four folds are required")
                    for fold_number, expanding in enumerate(windows["expanding"], 1):
                        month = expanding.eval_start.strftime("%Y-%m")
                        if month != FOLD_MONTHS[fold_number - 1] or expanding.role != FOLD_ROLES[fold_number - 1]:
                            raise ValueError("Unauthorized evaluation month or role; holdout prohibited")
                        eval_X = features.frame.loc[expanding.eval_indices]
                        evaluation_metadata = metadata.loc[expanding.eval_indices, list(TIME_COLUMNS)].copy()
                        frozen = reference.loc[reference.month.eq(month) & reference.horizon_minutes.eq(horizon)]
                        expanding_state = expanding_prediction = expanding_train_y = None
                        for name in PROGRAMS:
                            fold = windows[name][fold_number - 1] if name in RIDGE_PROGRAMS else expanding
                            if not fold.eval_indices.equals(expanding.eval_indices):
                                raise ValueError("All window evaluation indices must be identical; no row dropping")
                            train_X = features.frame.loc[fold.train_indices]
                            train_y = mature_labels(labels.frame, fold.fit_time, fold.train_indices)
                            model_name = "ridge" if name in RIDGE_PROGRAMS else name
                            if model_name == "ridge":
                                ridge_fits += 1
                                if ridge_fits > config["experiment"]["max_ridge_fits"]:
                                    raise ValueError("Ridge fit budget exceeded")
                            else:
                                reference_fits += 1
                            tick = perf_counter()
                            model = Baseline(model_name, ridge_alpha=1.0, random_seed=42).fit(train_X, train_y)
                            fit_seconds = perf_counter() - tick
                            tick = perf_counter()
                            predicted = model.predict(eval_X)
                            predict_seconds = perf_counter() - tick
                            truth = mature_labels(labels.frame, fold.eval_end, fold.eval_indices)
                            record = _record_model(model, train_y, horizon, fold_number, fold.fit_time, CONFIG_SHA256)
                            if name in ("expanding", *REFERENCE_PROGRAMS):
                                _assert_model_state(record, reference_models[(fold_number, horizon, model_name)])
                            if name == "expanding":
                                expanding_state, expanding_prediction, expanding_train_y = record, predicted.copy(), train_y
                                expanding_mse = float(np.mean((predicted - truth.to_numpy()) ** 2))
                            if name == "rolling_6m" and fold_number == 1:
                                if not fold.train_indices.equals(expanding.train_indices):
                                    raise ValueError("March 6M/Expanding training indices disagree")
                                pd.testing.assert_series_equal(train_y, expanding_train_y, check_exact=True)
                                _assert_model_state(record, expanding_state)
                                if not np.array_equal(predicted, expanding_prediction):
                                    raise ValueError("March 6M/Expanding predictions disagree")
                                equivalences.append({"month": month, "horizon_minutes": horizon, "status": "exact",
                                    "checks": ["training_indices", "training_labels", "feature_order", "scaler_state",
                                               "ridge_coefficients", "ridge_intercept", "predictions"]})
                            train_metadata = metadata.loc[fold.train_indices]
                            record.update(program=name, window_name=name if name in RIDGE_PROGRAMS else "expanding",
                                train_start=fold.audit["train_start"], train_end_exclusive=fold.fit_time.isoformat(),
                                first_train_decision=train_metadata.decision_time.iloc[0].isoformat(),
                                last_train_decision=train_metadata.decision_time.iloc[-1].isoformat(),
                                train_index_sha256=_index_hash(fold.train_indices),
                                training_labels_sha256=data.sha256(train_y.to_numpy(dtype="float64").tobytes()),
                                development_manifest_sha256=source["overall_manifest_sha256"], code_manifest_sha256=code_hash,
                                numeric_threads=1)
                            record.pop("model_version")
                            record["model_version"] = data.sha256(json.dumps(record, sort_keys=True, allow_nan=False).encode())
                            models.append(record)
                            keys = {"fold": fold_number, "month": month, "role": fold.role, "horizon_minutes": horizon, "model": name}
                            zero_mse = float(np.mean(truth.to_numpy() ** 2))
                            metrics.append({**keys, "train_samples": len(train_X),
                                **_comparison_metrics(truth, predicted, expanding_mse, zero_mse)})
                            timings.append({**keys, "train_samples": len(train_X), "fit_seconds": fit_seconds,
                                            "predict_seconds": predict_seconds})
                            if name in RIDGE_PROGRAMS:
                                audits.append({**fold.audit, **keys, "train_index_sha256": _index_hash(fold.train_indices),
                                    "evaluation_index_sha256": _index_hash(fold.eval_indices)})
                            prediction = evaluation_metadata.copy()
                            prediction["target_return"] = truth
                            prediction["prediction"] = predicted
                            prediction["model"] = name
                            prediction["model_version"] = record["model_version"]
                            prediction["horizon_minutes"] = horizon
                            prediction["fold"] = fold_number
                            prediction["month"] = month
                            prediction["role"] = fold.role
                            prediction["model_fit_time"] = fold.fit_time
                            prediction["evaluation_as_of"] = fold.eval_end
                            prediction["source_month"] = prediction.bar_open_time.dt.strftime("%Y-%m")
                            prediction["source_month_manifest_sha256"] = prediction.source_month.map(month_hashes)
                            prediction["development_manifest_sha256"] = source["overall_manifest_sha256"]
                            prediction["window_name"] = record["window_name"]
                            prediction["train_start"] = pd.Timestamp(record["train_start"])
                            prediction["train_end_exclusive"] = fold.fit_time
                            if name in ("expanding", *REFERENCE_PROGRAMS):
                                _assert_reference_prediction(prediction, frozen, name)
                            table = pa.Table.from_pandas(prediction, preserve_index=False)
                            if writer is None:
                                writer = pq.ParquetWriter(partial, table.schema, compression="zstd")
                            writer.write_table(table)
                            prediction_count += len(prediction)
        finally:
            if writer is not None:
                writer.close()
        if ridge_fits != 32 or reference_fits != 16 or len(equivalences) != 2 or writer is None:
            raise ValueError("Incomplete model/equivalence grid")
        if prediction_count != 6 * len(reference) or pq.read_metadata(partial).num_rows != prediction_count:
            raise ValueError("Prediction row count is incomplete")
        partial.replace(output / "predictions.parquet")
        metric_frame = pd.DataFrame(metrics)
        _write_csv(output, "metrics.csv", metric_frame)
        _write_csv(output, "model-timings.csv", pd.DataFrame(timings))
        _write_json(output / "model-states.json", {"models": models})
        _write_json(output / "window-audit.json", {"boundary_basis": "decision_time", "windows": audits,
            "march_6m_equivalence": equivalences, "frozen_reference_exact": True, "reference_audit": reference_audit,
            "feature_diagnostics": features.diagnostics, "horizon_diagnostics": horizon_diagnostics})
        from quantforge.diagnostics.window_comparison import analyze_window_predictions
        from quantforge.diagnostics.window_figures import FIGURE_NAMES, create_window_figures
        tick = perf_counter()
        comparison_columns = [*TIME_COLUMNS, "target_return", "prediction", "model", "horizon_minutes", "fold",
                              "month", "role", "model_fit_time", "evaluation_as_of"]
        predictions = pd.read_parquet(output / "predictions.parquet", columns=comparison_columns)
        with threadpool_limits(limits=1):
            analysis = analyze_window_predictions(predictions)
        stages["statistics"] = perf_counter() - tick
        tables = {"paired-losses.csv": analysis["paired_losses"], "bootstrap-results.csv": analysis["bootstrap_results"],
                  "hac-results.csv": analysis["hac_results"]}
        if len(tables["bootstrap-results.csv"]) != 192 or len(tables["hac-results.csv"]) != 144:
            raise ValueError("Incomplete predeclared statistical grid")
        for name, frame in tables.items():
            _write_csv(output, name, frame)
        _write_json(output / "pairing-audit.json", analysis["pairing_audit"])
        tick = perf_counter()
        figure_names = create_window_figures(metric_frame, analysis["bootstrap_results"], analysis["hac_results"], output / "figures")
        if set(figure_names) != set(FIGURE_NAMES) or len(figure_names) != len(FIGURE_NAMES):
            raise ValueError("Incomplete required figure set")
        stages["figures"] = perf_counter() - tick
        budget = {"ridge_fits": ridge_fits, "reference_fits": reference_fits,
            "ridge_training_row_visits": sum(m["train_samples"] for m in models if m["name"] == "ridge"),
            "reference_training_row_visits": sum(m["train_samples"] for m in models if m["name"] != "ridge"),
            "by_program": {name: {"fits": sum(t["model"] == name for t in timings),
                "training_row_visits": sum(t["train_samples"] for t in timings if t["model"] == name),
                "fit_seconds": sum(t["fit_seconds"] for t in timings if t["model"] == name),
                "predict_seconds": sum(t["predict_seconds"] for t in timings if t["model"] == name)} for name in PROGRAMS},
            "ridge_fit_seconds": sum(t["fit_seconds"] for t in timings if t["model"] in RIDGE_PROGRAMS),
            "all_fit_seconds": sum(t["fit_seconds"] for t in timings),
            "all_predict_seconds": sum(t["predict_seconds"] for t in timings)}
        summary = {"status": "completed_window_comparison", "run_id": run_id, "run_kind": run_kind,
            "output_directory": output.relative_to(data.REPOSITORY_ROOT).as_posix(), "limitation": LIMITATION,
            "config": config, "config_sha256": CONFIG_SHA256, "plan_sha256": PLAN_SHA256,
            "provenance": provenance, "code_manifest_sha256": code_hash, "source": source, "period_policy": policy,
            "threadpools_during_fit": pools, "seed": 42, "budget": budget, "models": models, "metrics": metrics,
            "prediction_rows": prediction_count, "paired_observations": len(reference),
            "march_6m_equivalence": equivalences, "frozen_reference_exact": True,
            "feature_formulas": FEATURE_FORMULAS, "holdout_accessed": False, "new_market_downloads": False,
            "statistical_counts": {name: len(frame) for name, frame in tables.items()}, "timings_seconds": stages,
            "memory_bytes": {"bars_deep": int(bars.memory_usage(index=True, deep=True).sum()),
                "features_deep": int(features.frame.memory_usage(index=True, deep=True).sum())}}
        (output / "report.md").write_text(_markdown_report(summary, metric_frame, analysis["bootstrap_results"],
                                                         analysis["hac_results"]), encoding="utf-8")
        # Frozen files are read-only inputs; catch any unexpected concurrent mutation.
        _verify_reference_unchanged(config, reference_audit)
        load_window_config(config_path)
        artifacts = ["predictions.parquet", "metrics.csv", "model-timings.csv", "model-states.json", "window-audit.json",
            *tables, "pairing-audit.json", "resolved-config.json", "period-policy.json", "report.md",
            *[f"figures/{name}" for name in FIGURE_NAMES]]
        for name in artifacts:
            path = data.repository_path(output / name)
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError(f"Required result missing or empty: {name}")
        summary["artifacts_sha256"] = {name: _file_hash(output / name) for name in artifacts}
        summary["deterministic_artifacts"] = [name for name in artifacts if name != "model-timings.csv"]
        summary["elapsed_seconds"] = perf_counter() - started
        _write_json(output / "summary.json", summary)
        return summary
    except Exception as exc:
        _write_json(output / "failure.json", {"status": "failed", "run_id": run_id, "run_kind": run_kind,
            "error_type": type(exc).__name__, "error": str(exc),
            "config": config if config is not None else {"config_path": str(config_path)}, "provenance": provenance,
            "ridge_fits_attempted": ridge_fits, "reference_fits_attempted": reference_fits, "limitation": LIMITATION})
        raise
