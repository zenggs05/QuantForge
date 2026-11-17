"""Offline, provenance-checked prediction smoke tests with delayed-label guards."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import tomllib
from uuid import uuid4

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from quantforge import data
from quantforge.datasets.splits import SplitConfig, make_walk_forward_splits
from quantforge.features import FEATURE_FORMULAS, build_features
from quantforge.labels import build_labels, mature_labels
from quantforge.models.baselines import Baseline, regression_metrics
from quantforge.temporal import validate_bars

SMOKE_LIMITATION = (
    "SMOKE TEST ONLY: one-day functional verification, not evidence of alpha, "
    "statistical significance, executable returns or trading performance. "
    "No hyperparameter tuning, orders, Sharpe, drawdown or profitability evaluation."
)


@dataclass(frozen=True)
class ExperimentConfig:
    parquet_path: str
    manifest_path: str
    output_directory: str = "results/baseline-smoke"
    horizons_minutes: tuple[int, ...] = (15, 5)
    availability_delay_seconds: float = 1.0
    seed: int = 42
    ridge_alpha: float = 1.0
    min_total_samples: int = 300
    split: SplitConfig = SplitConfig(mode="chronological")

    def __post_init__(self) -> None:
        if (not self.horizons_minutes or len(set(self.horizons_minutes)) != len(self.horizons_minutes)
                or any(isinstance(h, bool) or not isinstance(h, int) or h <= 0 for h in self.horizons_minutes)):
            raise ValueError("horizons_minutes must be unique positive integers")
        if (isinstance(self.availability_delay_seconds, bool)
                or not np.isfinite(self.availability_delay_seconds) or self.availability_delay_seconds < 0):
            raise ValueError("availability_delay_seconds must be finite and nonnegative")
        if isinstance(self.min_total_samples, bool) or not isinstance(self.min_total_samples, int) or self.min_total_samples < 2:
            raise ValueError("min_total_samples must be an integer >= 2")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or not 0 <= self.seed < 2**32:
            raise ValueError("seed must be an integer in [0, 2**32)")
        if isinstance(self.ridge_alpha, bool) or not np.isfinite(self.ridge_alpha) or self.ridge_alpha <= 0:
            raise ValueError("ridge_alpha must be finite and positive")
        for name in ("parquet_path", "manifest_path", "output_directory"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"{name} must be a repository-relative path string")
            data.repository_path(getattr(self, name))
        output = data.repository_path(self.output_directory)
        allowed = data.repository_path("results/baseline-smoke")
        if not output.is_relative_to(allowed):
            raise ValueError("Experiment outputs must remain under results/baseline-smoke")


def load_config(path: str | Path) -> ExperimentConfig:
    """Load a strict TOML configuration; never silently ignore misspelled keys."""
    raw = tomllib.loads(data.repository_path(path).read_text(encoding="utf-8"))
    required = {"experiment", "data", "split", "model", "output"}
    if set(raw) != required:
        raise ValueError(f"Config sections must be exactly {sorted(required)}")
    expected = {
        "experiment": {"mode", "seed", "horizons_minutes", "availability_delay_seconds", "min_total_samples"},
        "data": {"parquet_path", "manifest_path"}, "split": set(SplitConfig.__dataclass_fields__),
        "model": {"ridge_alpha"}, "output": {"directory"},
    }
    for section, keys in expected.items():
        if not isinstance(raw[section], dict) or set(raw[section]) != keys:
            raise ValueError(f"Invalid/missing keys in [{section}]; expected {sorted(keys)}")
    if raw["experiment"]["mode"] != "smoke":
        raise ValueError("Phase 2A runner supports only explicitly labeled smoke mode")
    return ExperimentConfig(
        **raw["data"], output_directory=raw["output"]["directory"],
        horizons_minutes=tuple(raw["experiment"]["horizons_minutes"]),
        availability_delay_seconds=raw["experiment"]["availability_delay_seconds"],
        seed=raw["experiment"]["seed"], min_total_samples=raw["experiment"]["min_total_samples"],
        ridge_alpha=raw["model"]["ridge_alpha"], split=SplitConfig(**raw["split"]),
    )


def load_verified_sample(config: ExperimentConfig) -> tuple[pd.DataFrame, dict]:
    """Revalidate the local one-day source and exact Parquet content, offline.

    This intentionally reuses the existing strict daily parser. It is not a
    monthly loader. Both local files must match the recorded provenance hashes.
    """
    data.ensure_daily_path_allowed(config.manifest_path)
    data.ensure_daily_path_allowed(config.parquet_path)
    manifest_path = data.repository_path(config.manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    data.ensure_daily_allowed(date.fromisoformat(manifest["date_utc"]))
    for key in ("raw_path", "parquet_path"):
        data.ensure_daily_path_allowed(manifest[key])
    declared = manifest.get("validation", {})
    if "first_open_utc" in declared and "last_open_utc" in declared:
        data.ensure_daily_range_allowed([declared["first_open_utc"], declared["last_open_utc"]])
    for key, expected in (("status", "passed"), ("symbol", "BTCUSDT"), ("market", "spot"), ("interval", "1m")):
        if manifest.get(key) != expected:
            raise data.DataValidationError(f"Source manifest {key} must equal {expected}")
    parquet = data.repository_path(config.parquet_path)
    if parquet != data.repository_path(manifest["parquet_path"]):
        raise data.DataValidationError("Configured Parquet does not match source manifest path")
    if parquet.stat().st_size > 32 * 1024 * 1024:
        raise data.DataValidationError("One-day Parquet exceeds 32 MiB smoke-test limit")
    if data.sha256(parquet.read_bytes()) != manifest["parquet_sha256"]:
        raise data.DataValidationError("Parquet SHA256 does not match validated manifest")
    raw = data.repository_path(manifest["raw_path"])
    if raw.stat().st_size > 8 * 1024 * 1024:
        raise data.DataValidationError("One-day ZIP exceeds 8 MiB limit")
    archive = raw.read_bytes()
    if data.sha256(archive) != manifest["archive_sha256"]:
        raise data.DataValidationError("Archive SHA256 does not match source manifest")
    checksum_path = data.repository_path(str(raw) + ".CHECKSUM")
    data.verify_checksum(archive, checksum_path.read_text(encoding="ascii"), raw.name)
    csv = data.extract_csv(archive, raw.name)
    if data.sha256(csv) != manifest["csv_sha256"]:
        raise data.DataValidationError("CSV SHA256 does not match source manifest")
    expected, validation = data.parse_klines(csv, date.fromisoformat(manifest["date_utc"]))
    frame = pd.read_parquet(parquet)
    data.ensure_daily_range_allowed(frame.open_time)
    pd.testing.assert_frame_equal(expected, frame, check_exact=True)
    validate_bars(frame, require_contiguous=True)
    return frame, {"manifest": manifest, "manifest_sha256": data.sha256(manifest_path.read_bytes()),
                   "validation_rechecked": validation, "source_verification": "offline archive checksum + CSV/Parquet hashes + exact content equality"}


def _provenance() -> dict:
    def git(*args: str) -> str | None:
        try:
            result = subprocess.run(["git", *args], cwd=data.REPOSITORY_ROOT,
                                    capture_output=True, text=True, timeout=15)
            return result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None
    status = git("status", "--porcelain")
    versions = {}
    for name in ("quantforge", "numpy", "pandas", "pyarrow", "scikit-learn", "scipy", "pytest", "threadpoolctl"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            # Runtime installations need not include the optional test extra.
            versions[name] = None
    source_paths = sorted((data.REPOSITORY_ROOT / "src" / "quantforge").rglob("*.py"))
    source_paths += [data.REPOSITORY_ROOT / p for p in ("scripts/run_baseline.py", "pyproject.toml", "uv.lock", "requirements-lock.txt")]
    hashes = {p.relative_to(data.REPOSITORY_ROOT).as_posix(): data.sha256(p.read_bytes()) for p in source_paths if p.is_file()}
    return {"git_commit": git("rev-parse", "HEAD"), "git_dirty": status != "" if status is not None else None,
            "git_status": status, "python": platform.python_version(), "platform": platform.platform(),
            "dependency_versions": versions, "source_sha256": hashes, "numeric_thread_limit": 1}


def _write_json(path: Path, value: dict) -> None:
    temporary = data.repository_path(path.with_name(path.name + ".partial"))
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _markdown_report(summary: dict) -> str:
    lines = ["# QuantForge prediction baseline — SMOKE TEST", "", SMOKE_LIMITATION, "",
             f"- Run ID: `{summary['run_id']}`", f"- Git commit: `{summary['provenance']['git_commit']}`; dirty: `{summary['provenance']['git_dirty']}`",
             f"- Dataset: BTCUSDT Spot 1m, {summary['source']['manifest']['date_utc']} UTC, {summary['source']['validation_rechecked']['rows']} bars.",
             f"- Seed: {summary['config']['seed']}; fixed Ridge alpha: {summary['config']['ridge_alpha']}; no tuning.",
             f"- Feature availability: bar_end_exclusive + {summary['config']['availability_delay_seconds']} seconds.",
             "- IC is Pearson correlation over time within each evaluation window, not cross-sectional IC.",
             "- Constant predictions have undefined IC (null); overlapping targets invalidate IID significance assumptions.", "",
             "| Horizon (min) | Fold | Model | Train rows | Evaluation rows | MSE | MAE | Time-series IC |",
             "|---|---|---|---|---|---|---|---|"]
    for row in summary["metrics"]:
        ic = "null (" + row["pearson_ic_null_reason"] + ")" if row["pearson_ic"] is None else f"{row['pearson_ic']:.6f}"
        lines.append(f"| {row['horizon_minutes']} | {row['fold']} | {row['model']} | {row['train_samples']} | {row['n_samples']} | {row['mse']:.9g} | {row['mae']:.9g} | {ic} |")
    lines += ["", "## Data exclusions and split audit", "",
              f"Feature diagnostics: `{json.dumps(summary['feature_diagnostics'])}`.", ""]
    for horizon in summary["horizon_diagnostics"]:
        lines += [f"- Horizon {horizon['horizon_minutes']} min: {horizon['eligible_samples']} eligible samples; label exclusions: `{horizon['labels']['exclusion_counts']}`."]
        for fold in horizon["folds"]:
            lines.append(f"  - Fold {fold['fold']}: fit at {fold['fit_time']}; train {fold['train_samples']}, evaluation {fold['eval_samples']}, removed {fold['removed_train_samples']}; complete evaluation window: {fold['eval_window_complete']}.")
    lines += ["", "## Interpretation and artifacts", "",
              "The label is log(close[t+h]/close[t]). Its start is the earlier bar boundary, before the decision by the availability delay; it is not an executable trade return.",
              "The runner predicts before accessing evaluation targets and evaluates only at their declared maturity cutoff. It fits each model/scaler using eligible training rows only.",
              "Prediction metrics are descriptive, correlated observations from one day. No p-values, confidence claims or trading metrics are produced.",
              "`predictions.parquet` stores predictions, targets, all seven time fields and fit/evaluation times. `sample-audit.parquet` records all warmup/tail exclusions. `summary.json` records config, hashes, sources, versions, model/scaler state and split diagnostics.", ""]
    return "\n".join(lines)


def run_baseline(config: ExperimentConfig, config_path: str | Path | None = None) -> dict:
    """Run deterministic CPU baselines; failures never produce a success report."""
    output_root = data.repository_path(config.output_directory)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
    output_dir = data.repository_path(output_root / run_id)
    output_dir.mkdir(parents=True, exist_ok=False)
    provenance = None
    try:
        provenance = _provenance()
        bars, source = load_verified_sample(config)
        delay = pd.Timedelta(seconds=config.availability_delay_seconds)
        features = build_features(bars, availability_delay=delay)
        metrics, predictions, diagnostics, audits, model_records = [], [], [], [], []
        with threadpool_limits(limits=1):
            for horizon in config.horizons_minutes:
                labels = build_labels(bars, horizon, availability_delay=delay)
                metadata = features.metadata.join(labels.frame)
                valid = metadata.valid_features & metadata.valid_label
                audit = metadata.copy()
                audit["horizon_minutes"] = horizon
                audit["eligible_sample"] = valid
                audits.append(audit.reset_index(drop=True))
                samples = metadata.loc[valid].drop(columns="target_return")
                if len(samples) < config.min_total_samples:
                    raise ValueError(f"Insufficient eligible samples for {horizon}m smoke test: {len(samples)} < {config.min_total_samples}")
                folds = make_walk_forward_splits(samples, config.split)
                fold_records = []
                for number, fold in enumerate(folds):
                    train_X = features.frame.loc[fold.train_indices]
                    train_y = mature_labels(labels.frame, as_of=fold.fit_time, indices=fold.train_indices)
                    eval_X = features.frame.loc[fold.eval_indices]
                    fold_records.append({**fold.diagnostics, "fit_time": fold.fit_time.isoformat(),
                                         "eval_start": fold.eval_start.isoformat(), "eval_end": fold.eval_end.isoformat()})
                    for name in ("zero", "historical_mean", "ridge"):
                        model = Baseline(name, ridge_alpha=config.ridge_alpha, random_seed=config.seed).fit(train_X, train_y)
                        predicted = model.predict(eval_X)
                        # This read happens after prediction; evaluation targets never enter fit().
                        evaluated_as_of = labels.frame.loc[fold.eval_indices, "label_available_time"].max()
                        truth = mature_labels(labels.frame, as_of=evaluated_as_of, indices=fold.eval_indices)
                        metric = regression_metrics(truth, predicted)
                        metrics.append({"horizon_minutes": horizon, "fold": number, "model": name,
                                        "train_samples": len(train_X), **metric})
                        prediction = metadata.loc[fold.eval_indices].copy()
                        prediction["prediction"] = predicted
                        prediction["model"] = name
                        prediction["horizon_minutes"] = horizon
                        prediction["fold"] = number
                        prediction["model_fit_time"] = fold.fit_time
                        prediction["evaluation_as_of"] = evaluated_as_of
                        predictions.append(prediction.reset_index(drop=True))
                        model_state = {**model.metadata(), "horizon_minutes": horizon, "fold": number}
                        if name == "ridge":
                            scaler = model.pipeline.named_steps["scaler"]
                            estimator = model.pipeline.named_steps["model"]
                            model_state["fitted_state"] = {"scaler_mean": scaler.mean_.tolist(), "scaler_scale": scaler.scale_.tolist(),
                                                           "scaler_train_samples": int(scaler.n_samples_seen_),
                                                           "coefficients": estimator.coef_.tolist(), "intercept": float(estimator.intercept_)}
                        elif name == "historical_mean":
                            model_state["fitted_state"] = {"training_target_mean": float(train_y.mean())}
                        model_records.append(model_state)
                diagnostics.append({"horizon_minutes": horizon, "eligible_samples": len(samples),
                                    "labels": labels.diagnostics, "folds": fold_records})
        predictions_path = data.repository_path(output_dir / "predictions.parquet")
        audit_path = data.repository_path(output_dir / "sample-audit.parquet")
        pd.concat(predictions, ignore_index=True).to_parquet(predictions_path, index=False)
        pd.concat(audits, ignore_index=True).to_parquet(audit_path, index=False)
        summary = {"status": "completed_smoke_test", "run_id": run_id, "limitation": SMOKE_LIMITATION,
                   "config": asdict(config), "feature_formulas": FEATURE_FORMULAS,
                   "config_file_sha256": data.sha256(data.repository_path(config_path).read_bytes()) if config_path else None,
                   "provenance": provenance, "source": source, "feature_diagnostics": features.diagnostics,
                   "horizon_diagnostics": diagnostics, "metrics": metrics, "models": model_records,
                   "artifacts": {"predictions_sha256": data.sha256(predictions_path.read_bytes()),
                                 "sample_audit_sha256": data.sha256(audit_path.read_bytes())},
                   "output_directory": output_dir.relative_to(data.REPOSITORY_ROOT).as_posix()}
        # Write human-readable artifacts first. The JSON summary is the final
        # success marker after every preceding artifact has been completed.
        data.repository_path(output_dir / "report.md").write_text(_markdown_report(summary), encoding="utf-8")
        _write_json(data.repository_path(output_dir / "summary.json"), summary)
        return summary
    except Exception as exc:
        _write_json(data.repository_path(output_dir / "failure.json"), {
            "status": "failed", "run_id": run_id, "error_type": type(exc).__name__, "error": str(exc),
            "limitation": SMOKE_LIMITATION, "config": asdict(config), "provenance": provenance,
        })
        raise
