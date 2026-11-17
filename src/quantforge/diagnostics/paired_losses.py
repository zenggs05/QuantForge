"""Strict, offline pairing of the frozen Phase 2B prediction artifact.

The production loader accepts only the clean run frozen before Phase 2C.
``validate_and_pair`` is an in-memory contract checker for synthetic fixtures;
it never opens market data, fits a model, or estimates statistical uncertainty.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd

from quantforge.features import FEATURE_COLUMNS
from quantforge.research_policy import (
    ALLOWED_MONTHS, DEFAULT_DEVELOPMENT_MANIFEST, canonical_policy,
    fixed_project_path,
)

FROZEN_RUN_ID = "20261103T010111153259Z-a2cb0858"
FROZEN_COMMIT = "a431190283c7c508751d4175a45ec84bff5e4bcf"
FROZEN_SUMMARY_PATH = f"results/research-baseline/{FROZEN_RUN_ID}/summary.json"
FROZEN_SUMMARY_SHA256 = "7aa9f334a9e5b5f1085edd2362676d86d0898fcdd376f92bb1886efbc3dc6f12"
FROZEN_PREDICTION_SHA256 = "66f74e1367ed816455d8df2af5c8fdd89009c893aa5fe064df29fe646c86407a"
FROZEN_PLAN_PATH = "docs/phase2c-analysis-plan.md"
FROZEN_PLAN_SHA256 = "1dcb6c79d947a3c13e478efb367a1d77e8e3d671698fc0b6fb2c1864daac40d3"
PLAN_MANIFEST_PATH = "docs/phase2c-plan-manifest.json"
FROZEN_CONFIG_SHA256 = "dd53dc921d07fd4da33e555a31d175ad11b2363df0ecae2c6a83315e733aee04"
FROZEN_DEVELOPMENT_SHA256 = "6fc42cd53ab30e1282bc9202046edd1fb43e5bc3fa8c44595f1639958c66acb8"
MONTHS = ("2026-03", "2026-04", "2026-05", "2026-06")
ROLES = ("validation", "validation", "development_evaluation", "development_evaluation")
MODELS = ("zero", "historical_mean", "ridge")
HORIZONS = (15, 5)
TIME_COLUMNS = (
    "bar_open_time", "bar_end_exclusive", "feature_available_time", "decision_time",
    "target_start_time", "target_end_time", "label_available_time",
)
SHARED_COLUMNS = (
    *TIME_COLUMNS, "target_return", "horizon_minutes", "fold", "month", "role",
    "model_fit_time", "evaluation_as_of", "source_month",
    "source_month_manifest_sha256", "development_manifest_sha256",
)
PREDICTION_COLUMNS = (*SHARED_COLUMNS, "model", "model_version", "prediction")


class PairingError(ValueError):
    """Frozen provenance or exact paired-observation requirements were violated."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PairingError(message)


def _record_hash(record: dict) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _sha(value: object, label: str) -> str:
    _require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
             f"Invalid SHA-256: {label}")
    return value


def _positive_int(value: object, label: str) -> int:
    _require(type(value) is int and value > 0, f"Expected positive integer: {label}")
    return value


def _timestamp(value: object, label: str) -> pd.Timestamp:
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise PairingError(f"Invalid UTC timestamp: {label}") from exc
    _require(not pd.isna(stamp) and stamp.tzinfo is not None
             and stamp.utcoffset().total_seconds() == 0, f"Expected UTC timestamp: {label}")
    return stamp


def _bounds(month: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(f"{month}-01T00:00:00Z")
    return start, start + pd.offsets.MonthBegin(1)


def _expected_config() -> dict:
    return {
        "manifest_path": DEFAULT_DEVELOPMENT_MANIFEST,
        "period_policy_path": "configs/research-periods.toml",
        "output_directory": "results/research-baseline", "horizons_minutes": list(HORIZONS),
        "seed": 42, "availability_delay_seconds": 1.0, "ridge_alpha": 1.0,
        "fold_months": list(MONTHS), "roles": list(ROLES), "label_cutoff": "month_end",
        "models": list(MODELS), "feature_columns": list(FEATURE_COLUMNS), "selection": "fixed; no tuning",
    }


def _summary_contract(summary: dict) -> tuple[dict, dict, dict]:
    """Validate provenance metadata without opening the files it references."""
    _require(isinstance(summary, dict), "Summary must be a dictionary")
    _require(summary.get("status") == "completed_research_baseline", "Incomplete baseline summary")
    config = summary.get("config", {})
    _require(config == _expected_config() and _record_hash(config) == FROZEN_CONFIG_SHA256
             and summary.get("config_sha256") == FROZEN_CONFIG_SHA256, "Frozen model/config contract changed")
    _require(summary.get("period_policy") == canonical_policy(), "Frozen period policy changed; holdout prohibited")
    source = summary.get("source", {})
    _require(source.get("status") == "passed" and source.get("holdout_accessed") is False,
             "Source must have passed without holdout access")
    _require(source.get("policy") == canonical_policy(), "Source policy changed; holdout prohibited")
    _require(source.get("rows") == 436320 and source.get("missing_minutes") == 0
             and source.get("duplicate_minutes") == 0, "Development source row/grid contract changed")
    _require(_timestamp(source.get("start"), "source start") == pd.Timestamp("2025-09-01T00:00:00Z")
             and _timestamp(source.get("end_exclusive"), "source end") == pd.Timestamp("2026-07-01T00:00:00Z"),
             "Development source dates changed; holdout prohibited")
    _require(source.get("overall_manifest_path") == DEFAULT_DEVELOPMENT_MANIFEST,
             "Unexpected development manifest path")
    _sha(source.get("overall_manifest_sha256"), "development manifest")
    items = source.get("month_sources", [])
    _require(isinstance(items, list) and [s.get("month") for s in items] == list(ALLOWED_MONTHS),
             "Source months must exactly equal the ten development months; holdout prohibited")
    source_map = {}
    for item in items:
        month = item["month"]
        expected_path = f"data/processed/binance/spot/BTCUSDT/1m/monthly/BTCUSDT-1m-{month}.json"
        _require(item.get("manifest_path") == expected_path, "Unexpected monthly manifest path")
        for key in ("manifest_sha256", "archive_sha256", "csv_sha256", "parquet_sha256"):
            _sha(item.get(key), f"{month}/{key}")
        start, end = _bounds(month)
        _require(item.get("rows") == int((end - start) / pd.Timedelta(minutes=1)), "Source month rows disagree")
        source_map[month] = item

    folds = summary.get("folds", [])
    _require(isinstance(folds, list) and len(folds) == 8, "Exactly eight fold/horizon records are required")
    fold_map = {}
    for item in folds:
        month, horizon = item.get("month"), item.get("horizon_minutes")
        _require(month in MONTHS and type(horizon) is int and horizon in HORIZONS, "Unauthorized fold/month/horizon")
        key = (month, horizon)
        _require(key not in fold_map, "Duplicate fold/horizon summary")
        number = MONTHS.index(month) + 1
        start, end = _bounds(month)
        _require(item.get("fold") == number and item.get("role") == ROLES[number - 1], "Fold role/number mismatch")
        for name, expected in (("fit_time", start), ("eval_start", start), ("eval_end", end), ("evaluation_as_of", end)):
            _require(_timestamp(item.get(name), name) == expected, "Fold fit/evaluation boundary changed")
        _require(item.get("boundary_basis") == "decision_time" and item.get("fit_at") == "month_start"
                 and item.get("evaluation_cutoff") == "month_end", "Fold availability contract changed")
        _positive_int(item.get("train_samples"), "train_samples")
        _positive_int(item.get("eval_samples"), "eval_samples")
        _require(_timestamp(item.get("last_train_label_available"), "last train label") <= start,
                 "Training labels were immature at fit time")
        _require(start <= _timestamp(item.get("first_eval_decision"), "first decision") < end
                 and _timestamp(item.get("last_eval_label_available"), "last eval label") <= end,
                 "Evaluation decision/label maturity outside fold")
        fold_map[key] = item
    _require(set(fold_map) == {(m, h) for m in MONTHS for h in HORIZONS}, "Missing fold/horizon summary")

    model_map = {}
    records = summary.get("models", [])
    _require(isinstance(records, list) and len(records) == 24, "Exactly 24 model records are required")
    for record in records:
        name, horizon, number = record.get("name"), record.get("horizon_minutes"), record.get("fold")
        _require(name in MODELS and type(horizon) is int and horizon in HORIZONS
                 and type(number) is int and 1 <= number <= 4, "Unexpected model identity")
        month = MONTHS[number - 1]
        key = (month, horizon, name)
        _require(key not in model_map, "Duplicate model summary record")
        version = _sha(record.get("model_version"), "model version")
        state_record = {k: v for k, v in record.items() if k != "model_version"}
        try:
            _require(_record_hash(state_record) == version, "Model version does not hash its fitted metadata")
        except (TypeError, ValueError) as exc:
            raise PairingError("Invalid model version or nonfinite model metadata") from exc
        _require(record.get("model_contract_version") == 1 and record.get("fitted") is True
                 and type(record.get("random_seed")) is int and record["random_seed"] == 42,
                 "Model contract/seed changed")
        _require(record.get("config_sha256") == FROZEN_CONFIG_SHA256
                 and record.get("feature_names") == list(FEATURE_COLUMNS)
                 and record.get("hyperparameter_selection") == "fixed configuration; no tuning",
                 "Model feature/config/selection contract changed")
        _require(_timestamp(record.get("fit_time"), "model fit") == _bounds(month)[0]
                 and record.get("train_samples") == fold_map[(month, horizon)]["train_samples"],
                 "Model fit time/training count differs from fold audit")
        state = record.get("fitted_state", {})
        if name == "ridge":
            _require(record.get("ridge_alpha") == 1.0 and record.get("solver") == "svd"
                     and record.get("preprocessing") == "StandardScaler fitted on supplied training rows",
                     "Ridge hyperparameters/preprocessing changed")
            for field in ("scaler_mean", "scaler_scale", "scaler_variance", "coefficients"):
                try:
                    array = np.asarray(state.get(field), dtype=float)
                except (TypeError, ValueError) as exc:
                    raise PairingError("Invalid Ridge fitted state") from exc
                _require(array.shape == (14,) and np.isfinite(array).all(), "Invalid Ridge fitted state")
                if field == "scaler_scale":
                    _require((array > 0).all(), "Scaler scales must be positive")
                if field == "scaler_variance":
                    _require((array >= 0).all(), "Scaler variances must be nonnegative")
            _require(state.get("scaler_train_samples") == record["train_samples"]
                     and isinstance(state.get("intercept"), (int, float)) and np.isfinite(state["intercept"]),
                     "Invalid Ridge scaler sample count/intercept")
        else:
            _require(record.get("ridge_alpha") is None and record.get("solver") is None
                     and record.get("preprocessing") == "none", "Constant model preprocessing changed")
            constant = state.get("constant") if name == "zero" else state.get("training_target_mean")
            _require(type(constant) in (int, float) and np.isfinite(constant), "Invalid constant model state")
            if name == "zero":
                _require(constant == 0.0, "Zero model must predict zero")
        model_map[key] = record
    _require(summary.get("prediction_rows") == 3 * sum(f["eval_samples"] for f in fold_map.values()),
             "Summary prediction rows disagree with folds")
    return source_map, fold_map, model_map


def validate_and_pair(predictions: pd.DataFrame, summary: dict) -> tuple[pd.DataFrame, dict]:
    """Validate exact pairs and calculate losses, using only in-memory inputs.

    Synthetic callers may use short evaluation sequences with matching fold
    counts. They still require all four months, both horizons and three models.
    Production additionally checks the immutable file hashes and full grids.
    """
    source_map, fold_map, model_map = _summary_contract(summary)
    _require(isinstance(predictions, pd.DataFrame) and not predictions.empty, "Predictions must be a nonempty DataFrame")
    _require(not predictions.columns.has_duplicates and set(predictions.columns) == set(PREDICTION_COLUMNS),
             "Prediction schema differs from the frozen contract")
    _require(not predictions.isna().to_numpy().any(), "Missing prediction values are prohibited")
    _require(len(predictions) == summary["prediction_rows"], "Prediction row count differs from summary")
    for column in (*TIME_COLUMNS, "model_fit_time", "evaluation_as_of"):
        dtype = predictions[column].dtype
        _require(isinstance(dtype, pd.DatetimeTZDtype) and str(dtype.tz) == "UTC",
                 f"Prediction timestamps must have explicit UTC dtype: {column}")
    for column in ("target_return", "prediction"):
        dtype = predictions[column].dtype
        _require(pd.api.types.is_numeric_dtype(dtype) and not pd.api.types.is_bool_dtype(dtype)
                 and not pd.api.types.is_complex_dtype(dtype)
                 and np.isfinite(predictions[column].to_numpy(dtype=float)).all(),
                 f"Prediction/target must be finite real numbers: {column}")
    for column in ("fold", "horizon_minutes"):
        _require(pd.api.types.is_integer_dtype(predictions[column].dtype)
                 and not pd.api.types.is_bool_dtype(predictions[column].dtype), "Fold/horizon must be integer columns")
    _require(set(predictions.model) == set(MODELS) and set(predictions.horizon_minutes) == set(HORIZONS)
             and set(predictions.month) == set(MONTHS), "Unauthorized model/month/horizon; holdout prohibited")
    key = ["bar_open_time", "horizon_minutes"]
    _require(not predictions.duplicated([*key, "model"]).any(), "Duplicate prediction key (model_version is not a key)")
    one_minute, delay = pd.Timedelta(minutes=1), pd.Timedelta(seconds=1)
    p = predictions
    _require(p.bar_open_time.eq(p.bar_open_time.dt.floor("min")).all(), "Bar opens must lie on the UTC minute grid")
    _require(p.bar_end_exclusive.eq(p.bar_open_time + one_minute).all()
             and p.feature_available_time.eq(p.bar_end_exclusive + delay).all()
             and p.decision_time.eq(p.feature_available_time).all(), "Feature/decision availability equation violated")
    _require(p.target_start_time.eq(p.bar_end_exclusive).all()
             and p.target_end_time.eq(p.target_start_time + pd.to_timedelta(p.horizon_minutes, unit="min")).all()
             and p.label_available_time.eq(p.target_end_time + delay).all(), "Target/label maturity equation violated")
    _require(p.month.eq(p.decision_time.dt.strftime("%Y-%m")).all(), "Month membership must use decision_time")
    _require(p.source_month.eq(p.bar_open_time.dt.strftime("%Y-%m")).all()
             and set(p.source_month).issubset(ALLOWED_MONTHS), "Source month mismatch or holdout source")
    expected_source = p.source_month.map({m: s["manifest_sha256"] for m, s in source_map.items()})
    _require(p.source_month_manifest_sha256.eq(expected_source).all()
             and p.development_manifest_sha256.eq(summary["source"]["overall_manifest_sha256"]).all(),
             "Prediction source manifest hashes disagree")

    group_audits = []
    for (month, horizon, model), group in p.groupby(["month", "horizon_minutes", "model"], sort=True):
        fold = fold_map[(month, horizon)]
        record = model_map[(month, horizon, model)]
        start, end = _bounds(month)
        _require(len(group) == fold["eval_samples"], "Model sample count differs from fold audit; no row dropping allowed")
        _require(group.fold.eq(fold["fold"]).all() and group.role.eq(fold["role"]).all(), "Prediction role/fold mismatch")
        _require(group.model_version.eq(record["model_version"]).all(), "Prediction model version mismatch")
        _require(group.model_fit_time.eq(start).all() and group.evaluation_as_of.eq(end).all(), "Prediction fit/evaluation cutoff mismatch")
        _require(group.decision_time.ge(start).all() and group.decision_time.lt(end).all()
                 and group.label_available_time.le(end).all(), "Prediction label maturity/month boundary violated")
        _require(group.decision_time.min() == _timestamp(fold["first_eval_decision"], "first decision")
                 and group.label_available_time.max() == _timestamp(fold["last_eval_label_available"], "last label"),
                 "Prediction extrema disagree with fold audit")
        if model in ("zero", "historical_mean"):
            value = 0.0 if model == "zero" else record["fitted_state"]["training_target_mean"]
            _require(group.prediction.eq(value).all(), "Constant model predictions disagree with fitted state")
        if model == "zero":
            group_audits.append({"month": month, "role": fold["role"], "fold": fold["fold"],
                                 "horizon_minutes": int(horizon), "paired_rows": len(group)})
    _require(len(group_audits) == 8, "Missing evaluation group")

    # Compare complete sorted key sets before aligning any values: no inner join.
    frames = {model: p.loc[p.model.eq(model)].sort_values(key).set_index(key, drop=False) for model in MODELS}
    reference = frames["zero"]
    for model in MODELS[1:]:
        _require(reference.index.equals(frames[model].index), "Three models must have identical one-to-one key sets")
        for column in SHARED_COLUMNS:
            _require(reference[column].eq(frames[model][column]).all(), f"Paired models disagree on {column}")
    wide = reference.loc[:, list(SHARED_COLUMNS)].reset_index(drop=True).copy()
    with np.errstate(over="ignore", invalid="ignore"):
        for model in MODELS:
            values = frames[model].prediction.to_numpy(dtype=float)
            wide[f"prediction_{model}"] = values
            residual = values - wide.target_return.to_numpy(dtype=float)
            wide[f"squared_loss_{model}"] = np.square(residual)
            wide[f"absolute_loss_{model}"] = np.abs(residual)
            _require(np.isfinite(wide[[f"squared_loss_{model}", f"absolute_loss_{model}"]].to_numpy()).all(),
                     "Loss arithmetic overflowed")
        for model in MODELS[:2]:
            for loss in ("squared", "absolute"):
                wide[f"{loss}_loss_difference_ridge_minus_{model}"] = wide[f"{loss}_loss_ridge"] - wide[f"{loss}_loss_{model}"]
    wide = wide.sort_values(["horizon_minutes", "month", "decision_time"]).reset_index(drop=True)
    audit = {
        "status": "passed", "input_rows": len(p), "paired_rows": len(wide), "groups": group_audits,
        "prediction_key": [*key, "model"], "pair_key": key, "dropped_rows": 0,
        "loss_difference_definition": "Ridge loss minus comparator loss; negative favors Ridge",
        "source_months": list(ALLOWED_MONTHS), "holdout_accessed": False,
        "model_versions": {f"{m}/{h}/{name}": rec["model_version"] for (m, h, name), rec in model_map.items()},
        "interpretation": "Artifact/metadata consistency, not an independent reconstruction of fitted models or market data.",
    }
    return wide, audit


def _verified_json(relative: str | Path, expected_path: str, digest: str | None = None) -> tuple[dict, str]:
    path = fixed_project_path(relative, expected_path)
    payload = path.read_bytes()
    actual = hashlib.sha256(payload).hexdigest()
    if digest is not None:
        _require(actual == digest, f"Frozen SHA-256 mismatch: {expected_path}")
    try:
        record = json.loads(payload)
    except (ValueError, UnicodeError) as exc:
        raise PairingError(f"Invalid JSON metadata: {expected_path}") from exc
    _require(isinstance(record, dict), f"Expected JSON object: {expected_path}")
    return record, actual


def _verify_source_manifests(summary: dict) -> dict:
    """Verify only allowlisted JSON manifests; never open raw/processed bars."""
    source = summary["source"]
    overall, digest = _verified_json(DEFAULT_DEVELOPMENT_MANIFEST, DEFAULT_DEVELOPMENT_MANIFEST,
                                     FROZEN_DEVELOPMENT_SHA256)
    _require(source["overall_manifest_sha256"] == digest and overall.get("status") == "passed"
             and overall.get("months") == list(ALLOWED_MONTHS) and overall.get("holdout_accessed") is False,
             "Development manifest identity/policy mismatch")
    expected_refs = [{key: s[key] for key in ("month", "manifest_path", "manifest_sha256")} for s in source["month_sources"]]
    _require(overall.get("month_manifests") == expected_refs
             and overall.get("validation", {}).get("month_sources") == source["month_sources"],
             "Development and baseline monthly provenance disagree")
    verified = {DEFAULT_DEVELOPMENT_MANIFEST: digest}
    for item in source["month_sources"]:
        # The summary contract checked the full whitelist before this loop.
        path = item["manifest_path"]
        month, actual = _verified_json(path, path, item["manifest_sha256"])
        _require(month.get("month") == item["month"] and month.get("status") == "passed"
                 and month.get("market") == "spot" and month.get("symbol") == "BTCUSDT"
                 and month.get("interval") == "1m", "Monthly manifest identity mismatch")
        for key in ("archive_sha256", "csv_sha256", "parquet_sha256"):
            _require(month.get(key) == item[key], "Monthly manifest source hash mismatch")
        _require(month.get("validation", {}).get("rows") == item["rows"], "Monthly manifest row count mismatch")
        verified[path] = actual
    return verified


def _read_verified_predictions(path: Path) -> tuple[pd.DataFrame, str]:
    """Hash before parsing, using the same handle to avoid an intervening reopen."""
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
        _require(digest == FROZEN_PREDICTION_SHA256, "Frozen prediction Parquet SHA-256 mismatch")
        handle.seek(0)
        return pd.read_parquet(handle), digest


def _validate_prediction_grid(wide: pd.DataFrame) -> None:
    for (month, horizon), group in wide.groupby(["month", "horizon_minutes"], sort=True):
        start, end = _bounds(month)
        count = int((end - start) / pd.Timedelta(minutes=1)) - int(horizon)
        expected = pd.date_range(start + pd.Timedelta(seconds=1), periods=count, freq="min")
        actual = pd.DatetimeIndex(group.decision_time)
        # pandas 3 Index.equals also compares ns/us storage resolution. UTC
        # instants must be exact, but equal instants need not share storage units.
        _require(len(actual) == len(expected) and (actual == expected).all(),
                 "Frozen production prediction minute grid changed")


def load_paired_losses(
    summary_path: str | Path = FROZEN_SUMMARY_PATH,
    plan_manifest_path: str | Path = PLAN_MANIFEST_PATH,
) -> tuple[pd.DataFrame, dict]:
    """Load only the hash-frozen clean run, then validate every paired row.

    Caller overrides cannot select another run or market-data/holdout file.
    All path/hash/provenance gates precede Parquet parsing. Existing manifests
    are checked as metadata; original source bars are deliberately not reread.
    """
    # Gate both caller-controlled paths before any file is opened.
    fixed_project_path(summary_path, FROZEN_SUMMARY_PATH)
    fixed_project_path(plan_manifest_path, PLAN_MANIFEST_PATH)
    manifest, manifest_hash = _verified_json(plan_manifest_path, PLAN_MANIFEST_PATH)
    clean = manifest.get("clean_baseline_check", {})
    _require(manifest.get("status") == "frozen_before_statistical_implementation"
             and manifest.get("base_git_commit") == FROZEN_COMMIT
             and manifest.get("plan_path") == FROZEN_PLAN_PATH
             and manifest.get("plan_sha256") == FROZEN_PLAN_SHA256, "Analysis plan manifest changed")
    _require(clean.get("status") == "passed" and clean.get("git_commit") == FROZEN_COMMIT
             and clean.get("git_dirty") is False and clean.get("clean_run_id") == FROZEN_RUN_ID
             and clean.get("clean_summary_path") == FROZEN_SUMMARY_PATH
             and clean.get("clean_summary_sha256") == FROZEN_SUMMARY_SHA256
             and clean.get("prediction_sha256") == FROZEN_PREDICTION_SHA256
             and clean.get("prediction_rows_exactly_equal") == 1053840,
             "Frozen clean-run provenance changed")
    plan = fixed_project_path(FROZEN_PLAN_PATH, FROZEN_PLAN_PATH)
    _require(hashlib.sha256(plan.read_bytes()).hexdigest() == FROZEN_PLAN_SHA256, "Analysis plan SHA-256 mismatch")
    summary, summary_hash = _verified_json(summary_path, FROZEN_SUMMARY_PATH, FROZEN_SUMMARY_SHA256)
    _summary_contract(summary)
    provenance = summary.get("provenance", {})
    _require(summary.get("run_id") == FROZEN_RUN_ID
             and summary.get("output_directory") == str(Path(FROZEN_SUMMARY_PATH).parent).replace("\\", "/")
             and provenance.get("git_commit") == FROZEN_COMMIT and provenance.get("git_dirty") is False
             and provenance.get("git_status") == "" and summary["prediction_rows"] == 1053840,
             "Baseline was not produced from the frozen clean commit")
    _require(summary.get("artifacts_sha256", {}).get("predictions.parquet") == FROZEN_PREDICTION_SHA256,
             "Baseline prediction digest differs from frozen plan")
    source_hashes = _verify_source_manifests(summary)
    prediction_relative = f"results/research-baseline/{FROZEN_RUN_ID}/predictions.parquet"
    prediction_path = fixed_project_path(prediction_relative, prediction_relative)
    predictions, prediction_hash = _read_verified_predictions(prediction_path)
    wide, audit = validate_and_pair(predictions, summary)
    _validate_prediction_grid(wide)
    audit.update({
        "mode": "frozen_production_artifact", "baseline_run_id": FROZEN_RUN_ID,
        "baseline_git_commit": FROZEN_COMMIT, "baseline_git_dirty": False,
        "summary_path": FROZEN_SUMMARY_PATH, "summary_sha256": summary_hash,
        "predictions_path": prediction_relative, "predictions_sha256": prediction_hash,
        "plan_path": FROZEN_PLAN_PATH, "plan_sha256": FROZEN_PLAN_SHA256,
        "plan_manifest_sha256": manifest_hash, "verified_source_manifests": source_hashes,
        "baseline_source_code_sha256": provenance["source_sha256"],
        "market_bar_payloads_read": False,
    })
    return wide, audit
