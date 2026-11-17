"""Fixed monthly prediction research with audited maturity and source contracts.

This runner is intentionally separate from the Phase 2A one-day smoke runner.
Only March--June 2026 are evaluated; no configuration can extend that period.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from time import perf_counter
import tomllib
from uuid import uuid4

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from threadpoolctl import threadpool_limits

from quantforge import data
from quantforge.datasets.research_dataset import load_research_dataset
from quantforge.datasets.research_splits import make_research_splits
from quantforge.experiment import _provenance, _write_json
from quantforge.features import FEATURE_COLUMNS, FEATURE_FORMULAS, build_features
from quantforge.labels import build_labels, mature_labels
from quantforge.models.baselines import Baseline, regression_metrics
from quantforge.research_policy import DEFAULT_DEVELOPMENT_MANIFEST, load_period_policy
from quantforge.temporal import utc_index

FOLD_MONTHS = ("2026-03", "2026-04", "2026-05", "2026-06")
FOLD_ROLES = ("validation", "validation", "development_evaluation", "development_evaluation")
MODEL_NAMES = ("zero", "historical_mean", "ridge")
TIME_COLUMNS = (
    "bar_open_time", "bar_end_exclusive", "feature_available_time", "decision_time",
    "target_start_time", "target_end_time", "label_available_time",
)
LIMITATION = (
    "Fixed prediction-only development research; March/April are validation and May/June "
    "are development evaluation, not final holdout. Overlapping 5/15-minute labels are "
    "autocorrelated; descriptive metrics do not establish significance or tradable alpha. "
    "No tuning, orders, PnL, Sharpe or drawdown. July/August 2026 final holdout is not accessed. "
    "Logical model fit time is the month boundary; measured computation time is not modeled "
    "as execution latency. Close-to-close targets are not executable returns."
)


@dataclass(frozen=True)
class ResearchConfig:
    """Frozen scientific choices; only a descendant output directory may vary."""

    manifest_path: str = DEFAULT_DEVELOPMENT_MANIFEST
    period_policy_path: str = "configs/research-periods.toml"
    output_directory: str = "results/research-baseline"
    horizons_minutes: tuple[int, ...] = (15, 5)
    seed: int = 42
    availability_delay_seconds: float = 1.0
    ridge_alpha: float = 1.0

    def __post_init__(self) -> None:
        fixed = {
            "manifest_path": DEFAULT_DEVELOPMENT_MANIFEST,
            "period_policy_path": "configs/research-periods.toml",
            "horizons_minutes": (15, 5), "seed": 42,
            "availability_delay_seconds": 1.0, "ridge_alpha": 1.0,
        }
        for name, expected in fixed.items():
            actual = getattr(self, name)
            if isinstance(actual, bool) or actual != expected:
                raise ValueError(f"Frozen research configuration requires {name}={expected!r}")
        if not isinstance(self.seed, int):
            raise ValueError("Frozen seed must be integer 42")
        if not isinstance(self.horizons_minutes, tuple) or any(type(h) is not int for h in self.horizons_minutes):
            raise ValueError("Frozen horizons must be the integer tuple (15, 5)")
        if not isinstance(self.output_directory, str):
            raise ValueError("output_directory must be a repository-relative path string")
        output = data.repository_path(self.output_directory)
        if not output.is_relative_to(data.repository_path("results/research-baseline")):
            raise ValueError("Research outputs must remain under results/research-baseline")


def load_research_config(path: str | Path = "configs/research-baseline.toml") -> ResearchConfig:
    """Reject extra keys, alternate partitions and tuning before reading any data."""
    config_path = data.repository_path(path)
    if config_path.suffix != ".toml" or not config_path.is_relative_to(data.repository_path("configs")):
        raise ValueError("Research configuration must be a TOML file under configs, never a market-data file")
    raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    expected = {
        "experiment": {"mode", "seed", "horizons_minutes", "availability_delay_seconds"},
        "data": {"manifest_path", "period_policy_path"},
        "evaluation": {"fold_months", "roles", "label_cutoff"},
        "model": {"names", "ridge_alpha"}, "output": {"directory"},
    }
    if set(raw) != set(expected):
        raise ValueError("Research config sections must exactly match the frozen protocol")
    for section, keys in expected.items():
        if not isinstance(raw[section], dict) or set(raw[section]) != keys:
            raise ValueError(f"Unexpected or missing research keys in [{section}]")
    if raw["experiment"]["mode"] != "research_baseline":
        raise ValueError("Only fixed research_baseline mode is authorized")
    if raw["evaluation"] != {"fold_months": list(FOLD_MONTHS), "roles": list(FOLD_ROLES), "label_cutoff": "month_end"}:
        raise ValueError("Frozen evaluation folds/roles/cutoff cannot be overridden (holdout prohibited)")
    if raw["model"]["names"] != list(MODEL_NAMES):
        raise ValueError("Only the frozen Zero/Mean/Ridge baselines are authorized")
    return ResearchConfig(
        **raw["data"], output_directory=raw["output"]["directory"],
        horizons_minutes=tuple(raw["experiment"]["horizons_minutes"]),
        seed=raw["experiment"]["seed"], availability_delay_seconds=raw["experiment"]["availability_delay_seconds"],
        ridge_alpha=raw["model"]["ridge_alpha"],
    )


def _file_hash(path: Path) -> str:
    """Hash large prediction output without making a second in-memory copy."""
    with data.repository_path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _config_record(config: ResearchConfig) -> dict:
    return {**asdict(config), "fold_months": FOLD_MONTHS, "roles": FOLD_ROLES,
            "label_cutoff": "month_end", "models": MODEL_NAMES,
            "feature_columns": FEATURE_COLUMNS, "selection": "fixed; no tuning"}


def _record_model(model: Baseline, train_y: pd.Series, horizon: int, fold: int,
                  fit_time: pd.Timestamp, config_digest: str) -> dict:
    record = {**model.metadata(), "horizon_minutes": horizon, "fold": fold,
              "fit_time": fit_time.isoformat(), "config_sha256": config_digest}
    if model.name == "ridge":
        scaler = model.pipeline.named_steps["scaler"]
        estimator = model.pipeline.named_steps["model"]
        record["fitted_state"] = {
            "scaler_mean": scaler.mean_.tolist(), "scaler_scale": scaler.scale_.tolist(),
            "scaler_variance": scaler.var_.tolist(), "scaler_train_samples": int(scaler.n_samples_seen_),
            "coefficients": estimator.coef_.tolist(), "intercept": float(estimator.intercept_),
        }
    elif model.name == "historical_mean":
        record["fitted_state"] = {"training_target_mean": float(train_y.mean())}
    else:
        record["fitted_state"] = {"constant": 0.0}
    record["model_version"] = data.sha256(json.dumps(record, sort_keys=True, allow_nan=False).encode())
    return record


def _markdown_report(summary: dict) -> str:
    lines = ["# QuantForge Phase 2B fixed monthly prediction baseline", "", LIMITATION, "",
             f"Run: `{summary['run_id']}`; source: BTCUSDT Spot 1m; seed 42; Ridge alpha 1.0.",
             "", "Relative error change = (model error - Zero error) / Zero error; negative is better. "
             "IC is a within-month Pearson time-series correlation; constant predictions have null IC.",
             "", "| Month | Role | Horizon | Model | Train n | Eval n | MSE | MAE | IC | Relative MSE | Relative MAE | Fit s | Predict s |",
             "|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for m in summary["metrics"]:
        def fmt(value):
            return "null" if value is None else f"{value:.8g}"
        lines.append(f"| {m['month']} | {m['role']} | {m['horizon_minutes']} | {m['model']} | "
                     f"{m['train_samples']} | {m['n_samples']} | {fmt(m['mse'])} | {fmt(m['mae'])} | "
                     f"{fmt(m['pearson_ic'])} | {fmt(m['relative_mse_delta_zero'])} | "
                     f"{fmt(m['relative_mae_delta_zero'])} | {m['fit_seconds']:.6f} | {m['predict_seconds']:.6f} |")
    lines += ["", "Training uses only labels mature at the fixed month-start fit time. "
              "Evaluation labels must mature by the next month boundary, including the availability delay. "
              "Month-end exclusions are intentional; no next-partition data are added to complete labels.",
              "", "Predictions include all seven temporal fields, model version, fold/role, "
              "source month and monthly/overall manifest hashes. Fold and exclusion counts are in "
              "`fold-audit.json`; exact model/scaler state, source/config/code hashes and dependencies are in `summary.json`.",
              "", "Negative results are retained. Validation and development evaluation are reported separately; "
              "none of these observations is an untouched final-holdout result.", ""]
    return "\n".join(lines)


def run_research_baseline(config: ResearchConfig, config_path: str | Path | None = None) -> dict:
    """Run the frozen offline experiment; summary is written only after all outputs."""
    # Repeat validation in case a frozen instance was altered through low-level Python.
    config.__post_init__()
    if config_path is not None and load_research_config(config_path) != config:
        raise ValueError("Provided config object disagrees with source TOML")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
    output = data.repository_path(data.repository_path(config.output_directory) / run_id)
    output.mkdir(parents=True, exist_ok=False)
    provenance = None
    started = perf_counter()
    try:
        # This strict policy gate precedes the data loader and any market-file read.
        policy = load_period_policy(config.period_policy_path)
        provenance = _provenance()
        for relative in ("scripts/run_research_baseline.py", "configs/research-baseline.toml", config.period_policy_path):
            path = data.repository_path(relative)
            if path.is_file():
                provenance["source_sha256"][relative] = _file_hash(path)
        config_record = _config_record(config)
        config_digest = data.sha256(json.dumps(config_record, sort_keys=True, default=str, allow_nan=False).encode())
        _write_json(data.repository_path(output / "resolved-config.json"), config_record)
        _write_json(data.repository_path(output / "period-policy.json"), policy)
        config_file_digest = None
        if config_path is not None:
            config_bytes = data.repository_path(config_path).read_bytes()
            data.repository_path(output / "source-config.toml").write_bytes(config_bytes)
            config_file_digest = data.sha256(config_bytes)
        load_started = perf_counter()
        bars, source = load_research_dataset(config.manifest_path)
        load_seconds = perf_counter() - load_started
        # Defense in depth for alternative/test loaders: inspect timestamps before features.
        index = utc_index(bars.open_time)
        if (index < pd.Timestamp("2025-09-01T00:00:00Z")).any() or (index >= pd.Timestamp("2026-07-01T00:00:00Z")).any():
            raise data.DataValidationError("Research input exceeds frozen development range; holdout prohibited")
        source_hash = source["overall_manifest_sha256"]
        month_hashes = {item["month"]: item["manifest_sha256"] for item in source["month_sources"]}
        if not set(index.strftime("%Y-%m")).issubset(month_hashes):
            raise data.DataValidationError("Every input month requires source manifest provenance")
        delay = pd.Timedelta(seconds=config.availability_delay_seconds)
        feature_started = perf_counter()
        features = build_features(bars, availability_delay=delay)
        feature_seconds = perf_counter() - feature_started
        if tuple(features.frame.columns) != FEATURE_COLUMNS or len(FEATURE_COLUMNS) != 14:
            raise ValueError("Phase 2B requires the existing fixed 14-feature contract")
        metrics, audits, models, horizon_diagnostics = [], [], [], []
        prediction_count = 0
        predictions_partial = data.repository_path(output / "predictions.parquet.partial")
        writer = None
        try:
            with threadpool_limits(limits=1):
                for horizon in config.horizons_minutes:
                    labels = build_labels(bars, horizon, availability_delay=delay)
                    metadata = features.metadata.join(labels.frame.drop(columns="target_return"))
                    folds = make_research_splits(metadata, horizon_minutes=horizon, eval_cutoff="month_end")
                    if len(folds) != 4:
                        raise ValueError("Frozen research protocol requires exactly four monthly folds")
                    horizon_diagnostics.append({"horizon_minutes": horizon, "labels": labels.diagnostics})
                    for number, fold in enumerate(folds, 1):
                        month = fold.eval_start.strftime("%Y-%m")
                        if month != FOLD_MONTHS[number - 1] or fold.role != FOLD_ROLES[number - 1]:
                            raise ValueError("Splitter returned unauthorized month or role")
                        train_X = features.frame.loc[fold.train_indices]
                        train_y = mature_labels(labels.frame, as_of=fold.fit_time, indices=fold.train_indices)
                        eval_X = features.frame.loc[fold.eval_indices]
                        evaluation_metadata = metadata.loc[fold.eval_indices, list(TIME_COLUMNS)].copy()
                        if (evaluation_metadata.decision_time.lt(fold.eval_start).any()
                                or evaluation_metadata.decision_time.ge(fold.eval_end).any()
                                or evaluation_metadata.label_available_time.gt(fold.eval_end).any()):
                            raise ValueError("Evaluation samples violate decision-time or maturity boundaries")
                        audit = {**fold.audit, "fold": number, "month": month, "role": fold.role,
                                 "horizon_minutes": horizon, "fit_time": fold.fit_time.isoformat(),
                                 "eval_start": fold.eval_start.isoformat(), "eval_end": fold.eval_end.isoformat(),
                                 "evaluation_as_of": fold.eval_end.isoformat(),
                                 "train_samples": len(train_X), "eval_samples": len(eval_X)}
                        audits.append(audit)
                        zero_metrics = None
                        for name in MODEL_NAMES:
                            tick = perf_counter()
                            model = Baseline(name, ridge_alpha=config.ridge_alpha, random_seed=config.seed).fit(train_X, train_y)
                            fit_seconds = perf_counter() - tick
                            tick = perf_counter()
                            predicted = model.predict(eval_X)
                            predict_seconds = perf_counter() - tick
                            # Evaluation targets are accessed only after predictions are produced.
                            truth = mature_labels(labels.frame, as_of=fold.eval_end, indices=fold.eval_indices)
                            metric = regression_metrics(truth, predicted)
                            if name == "zero":
                                zero_metrics = metric
                            relative = {}
                            for key in ("mse", "mae"):
                                denominator = zero_metrics[key]
                                relative[f"relative_{key}_delta_zero"] = (metric[key] - denominator) / denominator if denominator > 0 else None
                                relative[f"relative_{key}_null_reason"] = None if denominator > 0 else "zero_baseline_error_is_zero"
                            metrics.append({"fold": number, "month": month, "role": fold.role,
                                            "horizon_minutes": horizon, "task": "primary" if horizon == 15 else "auxiliary",
                                            "model": name, "train_samples": len(train_X), **metric, **relative,
                                            "fit_seconds": fit_seconds, "predict_seconds": predict_seconds})
                            record = _record_model(model, train_y, horizon, number, fold.fit_time, config_digest)
                            models.append(record)
                            prediction = evaluation_metadata.copy()
                            prediction["target_return"] = truth
                            prediction["prediction"] = predicted
                            prediction["model"] = name
                            prediction["model_version"] = record["model_version"]
                            prediction["horizon_minutes"] = horizon
                            prediction["fold"] = number
                            prediction["month"] = month
                            prediction["role"] = fold.role
                            prediction["model_fit_time"] = fold.fit_time
                            prediction["evaluation_as_of"] = fold.eval_end
                            prediction["source_month"] = prediction.bar_open_time.dt.strftime("%Y-%m")
                            prediction["source_month_manifest_sha256"] = prediction.source_month.map(month_hashes)
                            prediction["development_manifest_sha256"] = source_hash
                            table = pa.Table.from_pandas(prediction, preserve_index=False)
                            if writer is None:
                                writer = pq.ParquetWriter(predictions_partial, table.schema, compression="zstd")
                            writer.write_table(table)
                            prediction_count += len(prediction)
                            del table, prediction, model
        finally:
            if writer is not None:
                writer.close()
        if writer is None or prediction_count == 0:
            raise ValueError("No research predictions were produced")
        if pq.read_metadata(predictions_partial).num_rows != prediction_count:
            raise ValueError("Prediction Parquet row count does not match completed folds")
        predictions_path = data.repository_path(output / "predictions.parquet")
        predictions_partial.replace(predictions_path)
        metrics_partial = data.repository_path(output / "metrics.csv.partial")
        pd.DataFrame(metrics).to_csv(metrics_partial, index=False)
        metrics_partial.replace(data.repository_path(output / "metrics.csv"))
        _write_json(data.repository_path(output / "fold-audit.json"), {
            "boundary_basis": "decision_time", "train_maturity_cutoff": "month_start",
            "evaluation_maturity_cutoff": "next_month_start", "folds": audits,
            "feature_diagnostics": features.diagnostics, "horizon_diagnostics": horizon_diagnostics,
        })
        summary = {
            "status": "completed_research_baseline", "run_id": run_id, "limitation": LIMITATION,
            "config": config_record, "config_sha256": config_digest, "config_file_sha256": config_file_digest,
            "period_policy": policy, "provenance": provenance, "source": source,
            "feature_formulas": FEATURE_FORMULAS, "feature_diagnostics": features.diagnostics,
            "horizon_diagnostics": horizon_diagnostics, "folds": audits,
            "models": models, "metrics": metrics, "prediction_rows": prediction_count,
            "timings_seconds": {"load": load_seconds, "features": feature_seconds,
                                "total_before_report": perf_counter() - started},
            "memory_bytes": {"bars_deep": int(bars.memory_usage(index=True, deep=True).sum()),
                             "features_deep": int(features.frame.memory_usage(index=True, deep=True).sum())},
            "output_directory": output.relative_to(data.REPOSITORY_ROOT).as_posix(),
        }
        data.repository_path(output / "report.md").write_text(_markdown_report(summary), encoding="utf-8")
        artifacts = ("predictions.parquet", "metrics.csv", "fold-audit.json", "report.md", "resolved-config.json", "period-policy.json")
        summary["artifacts_sha256"] = {name: _file_hash(output / name) for name in artifacts}
        if config_path is not None:
            summary["artifacts_sha256"]["source-config.toml"] = _file_hash(output / "source-config.toml")
        # The only success marker is committed last, after all required artifacts.
        _write_json(data.repository_path(output / "summary.json"), summary)
        return summary
    except Exception as exc:
        _write_json(data.repository_path(output / "failure.json"), {
            "status": "failed", "run_id": run_id, "error_type": type(exc).__name__,
            "error": str(exc), "config": _config_record(config), "provenance": provenance,
            "limitation": LIMITATION,
        })
        raise
