"""Frozen one-day engineering pilot with independent fixed-scaler batch oracles."""
from __future__ import annotations

from datetime import datetime, timezone
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
from time import perf_counter
import tomllib
from uuid import uuid4

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from threadpoolctl import threadpool_info, threadpool_limits

from quantforge import data
from quantforge.datasets.research_dataset import load_research_dataset
from quantforge.datasets.research_splits import make_research_splits
from quantforge.experiment import _provenance, _write_json
from quantforge.features import FEATURE_COLUMNS, build_features
from quantforge.labels import build_labels, mature_labels
from quantforge.research_policy import DEVELOPMENT_START, DEVELOPMENT_END, fixed_project_path, load_period_policy

PLAN_HASH = "7876d1da1d867fa91184bdcfa2f3c2144142acc2ee9d70bbe6cc54a271d18752"
CONFIG_HASH = "e823ec9d599892ba98085162de22c00d08691338d3d19598d9a1bbf0115afc78"
FIT_TIME = pd.Timestamp("2026-03-01T00:00:00Z")
PILOT_END = pd.Timestamp("2026-03-02T00:00:00Z")
CHECKPOINTS = (0, 360, 720, 1080, 1440)


def file_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def load_foundation_config(path="configs/online-foundation.toml") -> dict:
    # Reject caller-selected data/holdout paths before stat or read.
    p = fixed_project_path(path, "configs/online-foundation.toml")
    payload = p.read_bytes()
    if hashlib.sha256(payload).hexdigest() != CONFIG_HASH:
        raise ValueError("Frozen online configuration hash mismatch")
    plan = fixed_project_path("docs/phase2d-c1-analysis-plan.md", "docs/phase2d-c1-analysis-plan.md")
    if file_hash(plan) != PLAN_HASH:
        raise ValueError("Frozen online analysis plan hash mismatch")
    manifest = json.loads(data.repository_path("docs/phase2d-c1-plan-manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("status") != "frozen_before_algorithm_implementation"
            or manifest.get("plan_sha256") != PLAN_HASH or manifest.get("config_sha256") != CONFIG_HASH):
        raise ValueError("Online plan manifest disagrees")
    return tomllib.loads(payload.decode("utf-8"))


def peak_process_memory() -> dict:
    """OS process lifetime high-water RSS/working set, not Python allocations."""
    if os.name == "nt":
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        *[(name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize",
                          "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                          "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]]
        kernel, psapi = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        counters = Counters(); counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
        return {"peak_process_bytes": int(counters.PeakWorkingSetSize), "current_process_bytes": int(counters.WorkingSetSize),
                "method": "Windows GetProcessMemoryInfo.PeakWorkingSetSize; process lifetime"}
    import resource
    high = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {"peak_process_bytes": int(high * (1 if platform.system() == "Darwin" else 1024)),
            "method": "getrusage ru_maxrss; process lifetime"}


def _environment() -> dict:
    result = {"platform": platform.platform(), "cpu": platform.processor(), "logical_cpu_count": os.cpu_count(),
              "numeric_device": "CPU float64", "gpu_used": False}
    try:
        p = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=15)
        result["gpu_detection"] = p.stdout.strip() if p.returncode == 0 else "nvidia-smi unavailable"
    except (OSError, subprocess.TimeoutExpired):
        result["gpu_detection"] = "nvidia-smi unavailable"
    return result


def equivalence_metrics(coef, intercept, predicted, batch_coef, batch_intercept, batch_predicted) -> dict:
    """Frozen combined componentwise and independent prediction-norm checks."""
    coef, batch_coef = np.asarray(coef, dtype=np.float64), np.asarray(batch_coef, dtype=np.float64)
    predicted, batch_predicted = np.asarray(predicted, dtype=np.float64), np.asarray(batch_predicted, dtype=np.float64)
    if (coef.shape != batch_coef.shape or predicted.shape != batch_predicted.shape or not coef.size or not predicted.size
            or any(not np.isfinite(a).all() for a in (coef, batch_coef, predicted, batch_predicted))
            or not np.isfinite([intercept, batch_intercept]).all()):
        raise ValueError("Equivalence requires finite aligned numeric arrays")
    dc, di, dp = np.abs(coef-batch_coef), abs(float(intercept)-float(batch_intercept)), predicted-batch_predicted
    pred_l2 = float(np.linalg.norm(dp)); relative = pred_l2 / max(float(np.linalg.norm(batch_predicted)), 1e-12)
    ok = bool((dc <= 1e-9 + 1e-8*np.abs(batch_coef)).all() and di <= 1e-10 + 1e-8*abs(batch_intercept)
              and np.max(np.abs(dp)) <= 1e-9 and relative <= 1e-8)
    return {"passed": ok, "coefficient_max_abs_error": float(dc.max()),
            "coefficient_max_relative_error": float(np.max(dc/np.maximum(np.abs(batch_coef), 1e-12))),
            "intercept_abs_error": di, "intercept_relative_error": di/max(abs(float(batch_intercept)), 1e-12),
            "prediction_max_abs_error": float(np.max(np.abs(dp))), "prediction_l2_error": pred_l2,
            "prediction_relative_l2_error": relative}


def _load_reference(config):
    expected = "results/research-baseline/20261103T010111153259Z-a2cb0858/summary.json"
    path = fixed_project_path(config["reference"]["summary_path"], expected)
    if file_hash(path) != config["reference"]["summary_sha256"]:
        raise ValueError("Frozen reference summary changed")
    prediction_path = fixed_project_path(str(Path(expected).parent / "predictions.parquet"), str(Path(expected).parent / "predictions.parquet"))
    if file_hash(prediction_path) != config["reference"]["prediction_sha256"]:
        raise ValueError("Frozen reference predictions changed")
    return json.loads(path.read_text(encoding="utf-8"))


def _hash_indices(indices):
    return hashlib.sha256(pd.DatetimeIndex(indices).as_unit("ns").asi8.astype("<i8").tobytes()).hexdigest()


def _csv(path, rows):
    pd.DataFrame(rows).to_csv(path, index=False)


def _batch_check(model, features, labels, indices, probe, as_of, horizon, event_count):
    """Audit refit deliberately rescans history; counted separately from update."""
    indices = pd.DatetimeIndex(indices).sort_values()
    if indices.has_duplicates:
        raise ValueError("Duplicate batch-oracle training index")
    X = features.loc[indices]
    y = mature_labels(labels.loc[indices], as_of)
    tick = perf_counter()
    batch = Ridge(alpha=1.0, fit_intercept=True, solver="svd").fit(model.transform(X), y.to_numpy())
    fit_seconds = perf_counter()-tick
    # Oracle inspection must not change the engine's prediction-call budget.
    z = model.transform(probe)
    predicted = z @ model.coef_ + model.intercept_
    reference = batch.predict(z)
    result = {"horizon_minutes": horizon, "completed_events": event_count, "as_of": as_of.isoformat(),
              "training_samples": len(indices), "train_index_sha256": _hash_indices(indices), "probe_samples": len(probe),
              "condition_number": model.diagnostics()["condition_number"],
              **equivalence_metrics(model.coef_, model.intercept_, predicted, batch.coef_, batch.intercept_, reference)}
    return result, {"horizon_minutes": horizon, "completed_events": event_count, "batch_fit_seconds": fit_seconds,
                    "batch_training_row_visits": len(indices), "batch_svd_fits": 1}


def _report(summary):
    return "\n".join(["# Delayed-feedback incremental Ridge foundation", "",
        "Engineering pilot only: numerical equivalence, logical event correctness and resource accounting. No alpha or execution claim.", "",
        f"Run {summary['run_id']}; kind {summary['run_kind']}; clean commit {summary['provenance']['git_commit']}.",
        f"Predictions: {summary['prediction_count']}; valid updates: {summary['update_count']}; checkpoints: {summary['batch_checkpoint_count']}.",
        "Fixed initial scaler, alpha=1, float64, no forgetting. Solve after matured updates and before prediction.",
        "Batch equivalence uses precisely the same fixed scaler and mature sample set. Original monthly re-scaling is a different later objective.",
        "Carry-in entries have no invented original prediction. Pending labels remain unconsumed at pilot end.",
        "See batch-equivalence.csv, update-audit.csv, prediction-audit.csv, budget.json and checkpoint-audit.json.",
        "Logical instant updates are not measured production zero latency. Peak process memory includes offline loading and audit overhead.", ""])


def run_online_foundation(config_path="configs/online-foundation.toml", *, run_kind="formal"):
    """Run only the frozen March 1 pilot; completion summary is written last."""
    if run_kind not in ("formal", "reproduction"):
        raise ValueError("Unknown run kind")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")+"-"+uuid4().hex[:8]
    output = data.repository_path(f"results/online-foundation/{run_id}")
    output.mkdir(parents=True, exist_ok=False)
    config, provenance = None, None
    started = perf_counter()
    try:
        config = load_foundation_config(config_path)
        load_period_policy(config["data"]["period_policy_path"])
        provenance = _provenance()
        if provenance.get("git_dirty") is not False or not provenance.get("git_commit"):
            raise ValueError("Online formal pilot requires a clean committed worktree before input reads")
        for name in ("scripts/run_online_foundation.py", "configs/online-foundation.toml", "docs/phase2d-c1-analysis-plan.md",
                     "docs/phase2d-c1-plan-manifest.json", "configs/research-periods.toml"):
            provenance["source_sha256"][name] = file_hash(data.repository_path(name))
        _write_json(output/"resolved-config.json", config)
        stages = {}
        tick=perf_counter(); frozen=_load_reference(config); stages["frozen_reference_validation_seconds"]=perf_counter()-tick
        tick=perf_counter(); bars, source=load_research_dataset(config["data"]["manifest_path"]); stages["data_load_seconds"]=perf_counter()-tick
        dates = pd.DatetimeIndex(bars.open_time)
        if dates.min()<DEVELOPMENT_START or dates.max()>=DEVELOPMENT_END:
            raise ValueError("Input exceeds development bounds; holdout prohibited")
        if source["overall_manifest_sha256"] != frozen["source"]["overall_manifest_sha256"]:
            raise ValueError("Input provenance differs from frozen baseline")
        tick=perf_counter(); features=build_features(bars); stages["feature_build_seconds"]=perf_counter()-tick
        if tuple(features.frame.columns)!=FEATURE_COLUMNS or len(FEATURE_COLUMNS)!=14:
            raise ValueError("Frozen feature contract changed")
        from quantforge.online.incremental_ridge import IncrementalRidge
        from quantforge.online.replay import ReplayEngine, GuardedFeatureAccessor, MatureLabelAccessor, event_from_metadata
        equivalences, batch_times, checkpoints, budgets = [], [], [], []
        all_predictions, all_updates, final_states, initial_checks = [], [], {}, []
        with threadpool_limits(limits=1):
            pools=threadpool_info()
            if any(p["num_threads"]!=1 for p in pools): raise ValueError("Single numeric thread requirement failed")
            for horizon in (15,5):
                labels=build_labels(bars,horizon).frame
                metadata=features.metadata.join(labels.drop(columns="target_return"))
                split=make_research_splits(metadata,horizon)[0]
                if split.fit_time!=FIT_TIME: raise ValueError("Initial fit boundary changed")
                train_X=features.frame.loc[split.train_indices]
                train_y=mature_labels(labels.loc[split.train_indices],FIT_TIME)
                model=IncrementalRidge().initialize_from_batch(train_X,train_y)
                initial_model_diagnostics = model.diagnostics()
                original=next(r for r in frozen["models"] if r["name"]=="ridge" and r["fold"]==1 and r["horizon_minutes"]==horizon)
                state=original["fitted_state"]
                if (original["train_samples"] != len(train_X) or state["scaler_train_samples"] != len(train_X)
                        or original["feature_names"] != list(FEATURE_COLUMNS)):
                    raise ValueError("Initial sample count or feature order differs from frozen March")
                for actual,key in ((model.scaler_mean_,"scaler_mean"),(model.scaler_scale_,"scaler_scale"),(model.scaler_var_,"scaler_variance")):
                    if not np.array_equal(actual,np.asarray(state[key])): raise ValueError("Initial fixed scaler differs from frozen March")
                probe=train_X.iloc[-256:]
                z=model.transform(probe)
                initial={"horizon_minutes":horizon,**equivalence_metrics(model.coef_,model.intercept_,z@model.coef_+model.intercept_,
                         state["coefficients"],state["intercept"],z@np.asarray(state["coefficients"])+state["intercept"])}
                if not initial["passed"]: raise ValueError("Initial model differs from frozen March tolerance")
                initial_checks.append(initial)
                engine=ReplayEngine(model=model,horizon_minutes=horizon,initial_fit_time=FIT_TIME,
                                    source_sha256=source["overall_manifest_sha256"],config_sha256=CONFIG_HASH)
                feature_access=GuardedFeatureAccessor(features.frame,features.metadata)
                label_access=MatureLabelAccessor(labels)
                carry_mask=(metadata.decision_time.lt(FIT_TIME)&metadata.feature_available_time.le(FIT_TIME)
                            &metadata.valid_features&metadata.label_available_time.gt(FIT_TIME))
                carry_rows=metadata.loc[carry_mask]
                if len(carry_rows)!=horizon: raise ValueError("Unexpected production carry-in count")
                for _,row in carry_rows.iterrows(): engine.register_carry_in(event_from_metadata(row,horizon),feature_access)
                events=[event_from_metadata(row,horizon) for _,row in metadata.loc[
                    metadata.decision_time.ge(FIT_TIME)&metadata.decision_time.lt(PILOT_END)].iterrows()]
                if len(events)!=1440: raise ValueError("Frozen pilot requires 1440 decision events per horizon")
                # The batch audit sees labels only at its own current clock.
                def audit_checkpoint(count):
                    as_of=FIT_TIME if count==0 else pd.Timestamp(events[count-1].decision_time)
                    released=[pd.Timestamp(r["feature_reference"]) for r in engine.updates if r["status"]=="updated"]
                    indices=split.train_indices.append(pd.DatetimeIndex(released)) if released else split.train_indices
                    probe_rows=train_X.iloc[-256:] if count==0 else features.frame.loc[
                        [pd.Timestamp(e.feature_reference) for e in events[:count]]]
                    row,timing=_batch_check(engine.model,features.frame,labels,indices,probe_rows,as_of,horizon,count)
                    equivalences.append(row); batch_times.append(timing)
                    if not row["passed"]:
                        _csv(output/"batch-equivalence.csv",equivalences)
                        raise ValueError("Frozen batch equivalence tolerance failed")
                    path=output/f"checkpoints/h{horizon}-event{count}.json"
                    tick=perf_counter(); engine.save_checkpoint(path); save_seconds=perf_counter()-tick
                    checkpoints.append({"horizon_minutes":horizon,"completed_events":count,
                        "path":path.relative_to(output).as_posix(),"sha256":file_hash(path),"save_seconds":save_seconds})
                audit_checkpoint(0)
                tick=perf_counter()
                for count,event in enumerate(events,1):
                    engine.step(event,feature_access,label_access)
                    if count in CHECKPOINTS[1:]: audit_checkpoint(count)
                formal_event_seconds=perf_counter()-tick
                first_state=engine.state_dict()
                all_predictions.extend(engine.predictions); all_updates.extend(engine.updates)
                final_states[str(horizon)]=first_state
                formal_diagnostics=engine.diagnostics()
                restore_path=output/f"checkpoints/h{horizon}-event720.json"
                tick=perf_counter()
                restored=ReplayEngine.load_checkpoint(restore_path,source_sha256=source["overall_manifest_sha256"],
                                                     config_sha256=CONFIG_HASH,horizon_minutes=horizon)
                load_seconds=perf_counter()-tick
                restored_before = restored.model.diagnostics()
                restored_feature=GuardedFeatureAccessor(features.frame,features.metadata)
                restored_label=MatureLabelAccessor(labels)
                before_duplicate=restored.state_dict()
                restored.step(events[719],restored_feature,restored_label)
                if restored.state_dict()!=before_duplicate: raise ValueError("Duplicate replay modified deterministic state")
                tick=perf_counter()
                for event in events[720:]: restored.step(event,restored_feature,restored_label)
                recovery_seconds=perf_counter()-tick
                if restored.state_dict()!=first_state: raise ValueError("Restored continuation differs from uninterrupted replay")
                if len(engine.predictions)!=1440 or len(engine.updates)!=1440 or engine.queue.pending_count()!=horizon:
                    raise ValueError("Unexpected production event/queue counts")
                if engine.model.n!=len(train_X)+1440: raise ValueError("Model sample count mismatch")
                restored_after = restored.model.diagnostics()
                recovery_delta = {key: restored_after[key]-restored_before[key] for key in (
                    "updated_samples", "update_calls", "solve_calls", "condition_evaluations", "cholesky_factorizations",
                    "training_sample_visits", "prediction_calls", "prediction_rows")}
                if recovery_delta["updated_samples"] != 720 or recovery_delta["solve_calls"] != 720:
                    raise ValueError("Recovery branch extra computation budget mismatch")
                budgets.append({"horizon_minutes":horizon,"initial_training_samples":len(train_X),"carry_in":len(carry_rows),
                    "initial_model": initial_model_diagnostics,
                    "formal_event_loop_including_checkpoints_seconds":formal_event_seconds,"formal":formal_diagnostics,
                    "formal_feature_access":{"row_reads": feature_access.row_reads},"formal_label_access":{"row_reads": label_access.row_reads},
                    "recovery":restored.diagnostics(),"recovery_continuation_seconds":recovery_seconds,
                    "recovery_extra_model_operations": recovery_delta,
                    "formal_queue": engine.queue.diagnostics(),
                    "recovery_feature_reads": restored_feature.row_reads, "recovery_label_reads": restored_label.row_reads,
                    "checkpoint_load_seconds":load_seconds,"recovery_extra_events":720,"duplicate_replay_attempts":1,
                    "restored_continuation_exact":True})
        _csv(output/"prediction-audit.csv",all_predictions); _csv(output/"update-audit.csv",all_updates)
        _csv(output/"batch-equivalence.csv",equivalences); _csv(output/"batch-timings.csv",batch_times)
        _write_json(output/"final-state.json",final_states)
        _write_json(output/"initial-equivalence.json",{"comparisons":initial_checks})
        _write_json(output/"checkpoint-audit.json",{"checkpoints":checkpoints,"recovery_exact":True})
        budget={"horizons":budgets,"batch_oracle_fits":len(batch_times),"batch_training_row_visits":sum(r["batch_training_row_visits"] for r in batch_times),
                "batch_fit_seconds":sum(r["batch_fit_seconds"] for r in batch_times),"checkpoint_save_seconds":sum(r["save_seconds"] for r in checkpoints),
                "stage_timings":stages,"memory":peak_process_memory(),"environment":_environment(),"threadpools":pools}
        budget["operation_totals"] = {
            "initial_fits": 2, "formal_updates": sum(r["formal"]["valid_update_count"] for r in budgets),
            "formal_solves_including_initial": sum(r["formal"]["model"]["solve_calls"] for r in budgets),
            "formal_condition_eigendecompositions": sum(r["formal"]["model"]["condition_evaluations"] for r in budgets),
            "formal_cholesky_factorizations": sum(r["formal"]["model"]["cholesky_factorizations"] for r in budgets),
            "formal_training_row_visits": sum(r["formal"]["model"]["training_sample_visits"] for r in budgets),
            "recovery_extra_updates": sum(r["recovery_extra_model_operations"]["updated_samples"] for r in budgets),
            "recovery_extra_solves": sum(r["recovery_extra_model_operations"]["solve_calls"] for r in budgets),
            "checkpoint_load_validation_eigendecompositions": sum(r["recovery"]["model"]["state_validation_eigendecompositions"] for r in budgets),
            "duplicate_replay_attempts": sum(r["recovery"]["runtime"]["duplicate_replay_attempts"] for r in budgets),
            "formal_rejected_queue_operations": sum(r["formal_queue"]["rejected_count"] for r in budgets),
        }
        _write_json(output/"budget.json",budget)
        if len(equivalences)!=10 or not all(r["passed"] for r in equivalences): raise ValueError("Missing or failed batch checkpoints")
        _load_reference(config); load_foundation_config(config_path)
        summary={"status":"completed_online_foundation","run_id":run_id,"run_kind":run_kind,"output_directory":output.relative_to(data.REPOSITORY_ROOT).as_posix(),
            "provenance":provenance,"source":source,"config_sha256":CONFIG_HASH,"plan_sha256":PLAN_HASH,
            "prediction_count":len(all_predictions),"update_count":len(all_updates),"batch_checkpoint_count":len(equivalences),
            "initial_equivalence":initial_checks,"batch_equivalence":equivalences,"budget":budget,"restored_continuation_exact":True,
            "holdout_accessed":False,"new_market_downloads":False,"alpha_evaluated":False,"trading_implemented":False,
            "limitations":"One-day already-seen engineering pilot, fixed-scaler raw-moment limitations, logical instantaneous updates; no predictive or trading advantage claim."}
        (output/"report.md").write_text(_report(summary),encoding="utf-8")
        artifacts=["prediction-audit.csv","update-audit.csv","batch-equivalence.csv","batch-timings.csv","final-state.json",
                   "initial-equivalence.json","checkpoint-audit.json","budget.json","resolved-config.json","report.md",
                   *[r["path"] for r in checkpoints]]
        for name in artifacts:
            if not (output/name).is_file() or not (output/name).stat().st_size: raise ValueError(f"Missing result {name}")
        summary["artifacts_sha256"]={name:file_hash(output/name) for name in artifacts}
        summary["deterministic_artifacts"]=[n for n in artifacts if n not in ("batch-timings.csv","checkpoint-audit.json","budget.json","report.md")]
        summary["elapsed_seconds"]=perf_counter()-started
        _write_json(output/"summary.json",summary)
        return summary
    except Exception as exc:
        _write_json(output/"failure.json",{"status":"failed","run_id":run_id,"run_kind":run_kind,"error_type":type(exc).__name__,
                    "error":str(exc),"config":config,"provenance":provenance,"elapsed_seconds":perf_counter()-started})
        raise


# The Phase 2F audit is a separate entry point; the frozen C1 runner above is unchanged.
from quantforge.online.batch_oracle import OracleIndependenceError


def audit_checkpoint_v2(engine, features_frame, labels_frame, as_of,
                        initial_sample_ids: frozenset[str]) -> dict:
    """Compare independently derived release/update identities with the engine.

    Invalid labels consume queue identities without changing model statistics.
    The two independently hashed channels intentionally preserve that distinction.
    No sample membership is supplied to the oracle from engine state.
    """
    from quantforge.online.batch_oracle import derive_mature_sets, sample_ids_sha256
    from quantforge.online.hardening_config import load_hardening_config, assert_legacy_equivalence_config

    config = load_hardening_config()
    assert_legacy_equivalence_config(config)
    all_ids, all_sha, valid_ids, valid_sha = derive_mature_sets(
        features_frame, labels_frame, as_of, initial_sample_ids)
    engine_released_sha = sample_ids_sha256(engine.queue.released_ids)
    engine_updated_sha = sample_ids_sha256(
        row["sample_id"] for row in engine.updates if row["status"] == "updated")
    if engine_released_sha != all_sha:
        raise OracleIndependenceError("released_ids sha mismatch")
    if engine_updated_sha != valid_sha:
        raise OracleIndependenceError("valid update sha mismatch")
    return {"released_ids_sha256": all_sha, "valid_update_ids_sha256": valid_sha,
            "released_count": len(all_ids), "valid_update_count": len(valid_ids),
            "passed": True}
