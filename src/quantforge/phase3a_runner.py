"""Frozen historical-design infrastructure, executable on synthetic bars only."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
from datetime import datetime, timezone
from uuid import uuid4

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from scipy.stats import norm
from threadpoolctl import threadpool_limits

from quantforge import data
from quantforge.datasets.historical_splits import make_historical_splits
from quantforge.data_monthly import plan_historical_download
from quantforge.diagnostics import forecast_tests as ft
from quantforge.diagnostics.hac_tests import holm_adjust
from quantforge.diagnostics.cross_month_dependence import (
    monthly_mean_hac_nw1994, monthly_block_bootstrap, equal_vs_nweighted_comparison,
)
from quantforge.diagnostics.conditional_gw import build_instrument, conditional_gw
from quantforge.diagnostics.stability import giacomini_rossi_fluctuation
from quantforge.diagnostics.placebo import sign_flip_placebo, time_shift_placebo
from quantforge.features import build_features
from quantforge.labels import build_labels, mature_labels
from quantforge.models.baselines import Baseline
from quantforge.phase3a_config import DEFAULT_CONFIG, MANIFEST_SHA256, load_history_config
from quantforge.phase3a_synthetic import synthetic_history
from quantforge.phase3a_power import power_planning

EVIDENCE = "synthetic_engineering_only"


def _default(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    raise TypeError(f"Unserializable artifact value: {type(value)}")


def _json_bytes(value) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False,
                       ensure_ascii=False, default=_default) + "\n").encode("utf-8")


def _json(path, value):
    Path(path).write_bytes(_json_bytes(value))


def _csv(path, rows, config):
    frame = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    frame.to_csv(path, index=False, float_format=config["output"]["float_format"], lineterminator="\n")


def _digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def peak_process_bytes() -> int:
    """Read process lifetime peak resident memory, not Python allocation alone."""
    if os.name == "nt":
        class Counters(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong)] + [
                (name, ctypes.c_size_t) for name in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                    "PagefileUsage", "PeakPagefileUsage")]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        get_process = ctypes.windll.kernel32.GetCurrentProcess
        get_process.restype = ctypes.c_void_p
        get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
        get_memory.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
        if not get_memory(get_process(), ctypes.byref(counters), counters.cb):
            raise OSError("Cannot measure process memory")
        return int(counters.PeakWorkingSetSize)
    import resource
    import sys
    scale = 1 if sys.platform == "darwin" else 1024
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale)


def fit_predict_fold(features, labels, fold, columns, config):
    """Read training y only through the maturity validator; reveal eval y last."""
    train_X = features.frame.loc[fold.train_indices, columns]
    train_y = mature_labels(labels.frame, as_of=fold.fit_time, indices=fold.train_indices)
    eval_X = features.frame.loc[fold.eval_indices, columns]
    model = Baseline("ridge", ridge_alpha=config["model"]["ridge_alpha"],
                     random_seed=config["experiment"]["seed"]).fit(train_X, train_y)
    predicted = model.predict(eval_X)
    truth = mature_labels(labels.frame, as_of=fold.eval_end, indices=fold.eval_indices)
    scaler, estimator = model.pipeline.named_steps["scaler"], model.pipeline.named_steps["model"]
    state = {
        **model.metadata(), "fit_time": fold.fit_time.isoformat(),
        "scaler_mean": scaler.mean_.tolist(), "scaler_scale": scaler.scale_.tolist(),
        "scaler_variance": scaler.var_.tolist(), "coefficients": estimator.coef_.tolist(),
        "intercept": float(estimator.intercept_), "train_target_mean": float(train_y.mean()),
        "train_ids_sha256": hashlib.sha256(fold.train_indices.as_unit("ns").asi8.tobytes()).hexdigest(),
    }
    state["model_version"] = hashlib.sha256(_json_bytes(state)).hexdigest()
    return predicted, truth, state


def _bands(n, config):
    for name in config["inference"]["lags"]:
        lag = max(config["inference"]["llsw_min_lag"], ft.llsw_lag(n, config["inference"]["llsw_constant"])) if name == "LLSW" else name
        yield str(name), lag


def _monthly(y, f, b, times, key, config, *, mode="synthetic", evidence=EVIDENCE):
    sequences = {"CW": ft.clark_west_differential(y, f, b),
                 "DM": ft.dm_differential(y, f, b), "IC": ft.ic_product(y, f)}
    metrics = {"model_mse": float(np.mean((y-f)**2)), "baseline_mse": float(np.mean((y-b)**2)),
               "model_mae": float(np.mean(abs(y-f))), "baseline_mae": float(np.mean(abs(y-b))),
               "r2_oos": ft.r2_oos(y, f, b), "r2_pooling": "sum_sse_ratio"}
    rows = []
    for bandwidth, lag in _bands(len(y), config):
        for metric, values in sequences.items():
            alternative = config["inference"][{"CW": "primary_alternative", "DM": "dm_alternative", "IC": "ic_alternative"}[metric]]
            if mode != "synthetic":
                from quantforge.phase3a_gate2 import segment_monthly_stat
                stats = segment_monthly_stat(values, times, lag, alternative)
            elif values is None:
                stats = dict(n=len(y), n_samples=len(y), mean=None, variance=None, se=None,
                             t=None, p_value=None, gamma0=None, n_eff=None,
                             status="not_estimable", reason="constant_ic_input", alternative=alternative)
            else:
                stats = ft.newey_west_segment(values, lag, times=times, alternative=alternative)
            rows.append({**key, **stats, **metrics, "metric": metric, "bandwidth": bandwidth,
                         "lag": lag, "admissible": ft.admissible(lag, key["horizon_minutes"], config["inference"]["admissible_min_lag_multiple"]),
                         "evidence_level": evidence})
    return rows


def _pool(rows, config, *, evidence=EVIDENCE):
    result = []
    grouping = ("specification", "horizon_minutes", "baseline", "metric", "bandwidth")
    groups = {}
    for row in rows:
        groups.setdefault(tuple(row[k] for k in grouping), []).append(row)
    for keys, segments in groups.items():
        if [s["month"] for s in segments] != config["months"]["evaluate"]:
            raise AssertionError("All 56 months must contribute to every pooled candidate")
        sizes = np.array([s["n"] for s in segments])
        for pooling in config["inference"]["pooling"]:
            if any(s["mean"] is None for s in segments):
                stats = dict(mean=None, variance=None, se=None, t=None, p_value=None,
                             status="not_estimable", reason="undefined_month_statistic", n=int(sizes.sum()),
                             n_eff=None, alternative=segments[0]["alternative"],
                             cross_month_covariance="assumed zero")
            else:
                stats = ft.pool_roles(segments, pooling=pooling, alternative=segments[0]["alternative"])
            weights = sizes/sizes.sum() if pooling == "n_weighted" else np.ones(len(sizes))/len(sizes)
            metrics = {k: float(np.dot(weights, [s[k] for s in segments]))
                       for k in ("model_mse", "baseline_mse", "model_mae", "baseline_mae")}
            denominator = sum(s["n"]*s["baseline_mse"] for s in segments)
            r2 = None if denominator == 0 else 1-sum(s["n"]*s["model_mse"] for s in segments)/denominator
            result.append({**dict(zip(grouping, keys, strict=True)), **stats, **metrics,
                           "pooling": pooling, "r2_oos": r2, "r2_pooling": "sum_sse_ratio",
                           "month_count": len(segments), "evidence_level": evidence,
                           "specification_evidence_level": segments[0]["specification_evidence_level"]})
    return result


def _select(rows, spec, horizon, baseline, metric):
    matched = [r for r in rows if (r["specification"], r["horizon_minutes"], r["baseline"],
               r["metric"], r["bandwidth"], r["pooling"]) == (spec, horizon, baseline, metric, "LLSW", "n_weighted")]
    if len(matched) != 1:
        raise AssertionError("A frozen endpoint is missing or duplicated")
    return matched[0].copy()


def _secondary(pooled, config):
    specifications = [("v2",15,"zero","CW"), ("v1",5,"zero","CW"),
                      ("v1",15,"historical_mean","CW"), ("v1",15,"zero","DM"), ("v1",15,"zero","IC")]
    selected = [_select(pooled, *values) for values in specifications]
    adjusted = holm_adjust([1.0 if r["p_value"] is None else r["p_value"] for r in selected])
    for name, row, p in zip(config["inference"]["secondary_family"], selected, adjusted, strict=True):
        row.update(test_id=name, holm_family="F_sec", family_size=len(selected), holm_p=p,
                   holm_reject=None if p is None else p < config["inference"]["alpha"])
    return selected


def _descriptive_subperiods(primary_rows):
    rows = []
    for kind in ("month", "year", "half_year"):
        groups = {}
        for row in primary_rows:
            month = row["month"]
            key = month if kind == "month" else month[:4] if kind == "year" else month[:4]+("H1" if int(month[-2:])<=6 else "H2")
            groups.setdefault(key, []).append(row)
        for bucket, group in groups.items():
            n = sum(r["n"] for r in group)
            rows.append({"period_type": kind, "period": bucket, "n": n,
                         "month_count": len(group), "cw_mean": sum(r["n"]*r["mean"] for r in group)/n,
                         "model_mse": sum(r["n"]*r["model_mse"] for r in group)/n,
                         "baseline_mse": sum(r["n"]*r["baseline_mse"] for r in group)/n,
                         "evidence_level": "exploratory_descriptive"})
    return rows


def _placebo_audit(rows, alpha):
    """Report Monte Carlo precision separately from the plus-one p estimate."""
    critical = float(norm.ppf(1-alpha/2))
    for row in rows:
        if row["placebo"] == "P2_time_shift":
            row["excluded_n_samples"] = row["n_samples"] - row["shifted_n_samples"]
            continue
        row.update(mc_exceedance_count=None, mc_standard_error=None,
                   mc_interval_low=None, mc_interval_high=None,
                   mc_confidence=1-alpha, mc_interval_method="Wilson_binomial_exceedance_probability")
        p = row["empirical_one_sided_p"]
        if p is None:
            continue
        draws = row["draws"]
        count = int(round(p*(draws+1)-1))
        if not 0 <= count <= draws:
            raise AssertionError("Invalid placebo exceedance count")
        estimate = count/draws
        denominator = 1+critical**2/draws
        center = (estimate+critical**2/(2*draws))/denominator
        half = critical*np.sqrt(estimate*(1-estimate)/draws+critical**2/(4*draws**2))/denominator
        row.update(mc_exceedance_count=count,
                   mc_standard_error=float(np.sqrt(estimate*(1-estimate)/draws)),
                   mc_interval_low=float(max(0.,center-half)),
                   mc_interval_high=float(min(1.,center+half)))
    return rows


def _execute_synthetic(config, output: Path):
    """Internal execution seam; the public entry point owns scope/output gates."""
    with threadpool_limits(limits=config["experiment"]["cpu_threads"]):
        return _execute_synthetic_inner(config, output)


def _execute_synthetic_inner(config, output: Path, *, mode="synthetic", prepared=None, started_at=None):
    """Execute the fixed computation with the thread limit active throughout."""
    started = perf_counter() if started_at is None else started_at
    if mode not in ("synthetic", "rehearsal", "real"):
        raise ValueError("Unknown Phase 3A execution mode")
    adapter = None
    evidence, seconds_limit, memory_limit = EVIDENCE, config["budget"]["run_seconds"], config["budget"]["process_memory_bytes"]
    write_json, write_csv = _json, lambda p, rows: _csv(p, rows, config)
    if mode != "synthetic":
        from quantforge import phase3a_gate2 as adapter
        if prepared is None:
            raise ValueError("Historical execution requires a prevalidated context")
        contract = prepared.contract
        evidence = contract["evidence_labels"]["rehearsal" if mode == "rehearsal" else "core"]
        fraction = contract["resources"]["rehearsal_fraction_max"] if mode == "rehearsal" else 1
        seconds_limit = contract["resources"]["run_seconds_max"] * fraction
        memory_limit = contract["resources"]["peak_process_bytes_max"] * fraction
        write_json = adapter.write_json
        write_csv = lambda p, rows: adapter.write_csv(p, rows, config)
    planning = power_planning(config)
    bars = synthetic_history(config) if prepared is None else prepared.bars
    features = build_features(bars, pd.Timedelta(seconds=config["experiment"]["availability_delay_seconds"])) if prepared is None else prepared.features
    models, audits, monthly, concentration, encompassing, placebo_input = [], [], [], [], [], []
    train_ids = {}
    fits = 0
    writer = None
    prediction_handle = None
    try:
        with threadpool_limits(limits=config["experiment"]["cpu_threads"]):
            for horizon in config["experiment"]["horizons"]:
                labels = build_labels(bars, horizon, pd.Timedelta(seconds=config["experiment"]["availability_delay_seconds"])) if prepared is None else prepared.labels[horizon]
                metadata = features.metadata.join(labels.frame.drop(columns="target_return"))
                folds = make_historical_splits(metadata, tuple(config["months"]["evaluate"]), horizon, "month_end") if prepared is None else prepared.folds(horizon)
                if len(folds) != len(config["months"]["evaluate"]):
                    raise AssertionError("Historical run requires exactly 56 folds")
                for ordinal, fold in enumerate(folds):
                    month = fold.fit_time.strftime("%Y-%m")
                    if month != config["months"]["evaluate"][ordinal]:
                        raise AssertionError("Historical fold order changed")
                    audits.append({**fold.audit, "horizon_minutes": horizon, "role": fold.role} if prepared is None else
                                  next(r for r in prepared.audit["folds"] if r["month"] == month and r["horizon_minutes"] == horizon))
                    train_ids[f"h{horizon}_{month}"] = fold.train_indices.as_unit("ns").asi8
                    times = pd.DatetimeIndex(metadata.loc[fold.eval_indices, "decision_time"])
                    for spec in config["model"]["specifications"]:
                        columns = list(config["specification"]["v1_features"])
                        if spec == "v2":
                            columns = [col for col in columns if col not in config["specification"]["v2_drop_features"]]
                        if fits >= config["budget"]["ridge_fits_per_run"]:
                            raise RuntimeError("Ridge fit budget exceeded")
                        f, truth, state = fit_predict_fold(features, labels, fold, columns, config)
                        fits += 1
                        y = truth.to_numpy(dtype=np.float64)
                        state.update(specification=spec, horizon_minutes=horizon, month=month, evidence_level=evidence)
                        models.append(state)
                        record = pd.DataFrame({"bar_open_time": fold.eval_indices, "decision_time": times,
                                               "label_available_time": labels.frame.loc[fold.eval_indices, "label_available_time"].to_numpy(),
                                               "prediction": f, "target_return": y, "historical_mean": state["train_target_mean"],
                                               "model_version": state["model_version"], "fit_time": fold.fit_time,
                                               "month": month, "horizon_minutes": horizon, "specification": spec,
                                               "evidence_level": evidence})
                        table = pa.Table.from_pandas(record, preserve_index=False)
                        if writer is None:
                            if mode != "synthetic":
                                prediction_handle = (output/"predictions.parquet").open("xb")
                            writer = pq.ParquetWriter(output/"predictions.parquet" if prediction_handle is None else prediction_handle, table.schema, compression="zstd")
                        writer.write_table(table)
                        for baseline in config["model"]["baselines"]:
                            b = np.zeros(len(y)) if baseline == "zero" else np.full(len(y), state["train_target_mean"])
                            key = dict(month=month, specification=spec, horizon_minutes=horizon, baseline=baseline,
                                       specification_evidence_level=config["specification"]["v2_evidence_tag"] if spec=="v2" else "design_frozen_v1")
                            rows = _monthly(y, f, b, times, key, config, mode=mode, evidence=evidence)
                            monthly.extend(rows)
                            if spec == "v1" and horizon == 15 and baseline == "zero":
                                main = next(r for r in rows if r["metric"]=="CW" and r["bandwidth"]=="LLSW")
                                placebo_input.append(dict(y=y, f=f, times=times, month=month, month_index=ordinal))
                                for cr in ft.concentration(ft.clark_west_differential(y,f,b), config["concentration"]["top_k"], main["lag"], times=times):
                                    cr.pop("p_value", None)
                                    concentration.append({**key, **cr, "evidence_level":"exploratory_descriptive"})
                                approximation = ft.encompassing_approx(f-b, y-b, main["n_eff"])
                                dm = next(r for r in rows if r["metric"]=="DM" and r["bandwidth"]=="LLSW")
                                encompassing.append({**key, **approximation, "minus_dm_t": None if dm["t"] is None else -dm["t"], "evidence_level":"exploratory_descriptive"})
                    if perf_counter()-started > seconds_limit or peak_process_bytes()>memory_limit:
                        raise RuntimeError(f"{mode} computation resource budget exceeded")
                    if adapter is not None:
                        adapter.check_disk(contract)
    finally:
        if writer is not None:
            writer.close()
        if prediction_handle is not None:
            prediction_handle.close()
    if fits != config["budget"]["ridge_fits_per_run"]:
        raise AssertionError("Frozen fit count not reproduced")
    # Store exact consumed index vectors once per fold/horizon, independent of spec.
    if mode == "synthetic":
        np.savez_compressed(output/"training-indices.npz", **train_ids)
    else:
        with (output/"training-indices.npz").open("xb") as handle:
            np.savez_compressed(handle, **train_ids)
    del train_ids
    pooled = _pool(monthly, config, evidence=evidence)
    primary = _select(pooled,"v1",15,"zero","CW")
    if primary["p_value"] is None:
        raise RuntimeError("Primary endpoint is undefined; no subset may replace it")
    primary.update(test_id="F_main", family_size=1, reject=primary["p_value"]<config["inference"]["alpha"])
    primary_months = [r for r in monthly if (r["specification"],r["horizon_minutes"],r["baseline"],r["metric"],r["bandwidth"]) == ("v1",15,"zero","CW","LLSW")]
    means = [r["mean"] for r in primary_months]
    dep = config["dependence"]
    dependence = {"S_dep1":monthly_mean_hac_nw1994(means, L=dep["monthly_lag"], confidence=dep["confidence"]),
                  "S_dep2":monthly_block_bootstrap(means, dep["block_lengths"], B=dep["bootstrap_draws"], seed=config["experiment"]["seed"], confidence=dep["confidence"]),
                  "S_dep3":equal_vs_nweighted_comparison(means,[r["n"] for r in primary_months],month_variances=[r["variance"] for r in primary_months])}
    fit_times = pd.DatetimeIndex([pd.Timestamp(month+"-01",tz="UTC") for month in config["months"]["evaluate"]])
    instrument_labels = build_labels(bars, config["conditional_gw"]["instrument_horizon_minutes"],
                                     pd.Timedelta(seconds=config["experiment"]["availability_delay_seconds"])) if prepared is None else prepared.labels[config["conditional_gw"]["instrument_horizon_minutes"]]
    instrument = build_instrument(instrument_labels.frame,fit_times)
    instrument["coverage_scope"] = "partial_episode_realized_volatility" if adapter is None else contract["evidence_labels"]["gw_coverage_scope"]
    dm_means = [r["mean"] for r in monthly if (r["specification"],r["horizon_minutes"],r["baseline"],r["metric"],r["bandwidth"]) == ("v1",15,"zero","DM","LLSW")]
    gw = conditional_gw(dm_means,instrument["instrument"].to_numpy(),hac_lag=config["conditional_gw"]["hac_lag"],bins=config["conditional_gw"]["bins"])
    stability = giacomini_rossi_fluctuation(means,config["stability"]["window_months"],times=config["months"]["evaluate"])
    kwargs=dict(horizon=15,role_id=config["placebo"]["role_id"],seed=config["experiment"]["seed"],settings=config["placebo"],inference=config["inference"],evidence_level="synthetic" if adapter is None else contract["evidence_labels"]["placebo_call_label"])
    placebo = _placebo_audit(sign_flip_placebo(placebo_input,**kwargs)+time_shift_placebo(placebo_input,**kwargs),
                            config["inference"]["alpha"])
    if adapter is not None:
        placebo = adapter.relabel_placebo(placebo, contract)
    secondary = _secondary(pooled,config)
    for name, rows in (("monthly-tests.csv",monthly),("pooled-tests.csv",pooled),("secondary-tests.csv",secondary),
                       ("concentration.csv",concentration),("encompassing.csv",encompassing),("placebo.csv",placebo),
                       ("subperiods.csv",_descriptive_subperiods(primary_months)),("gw-instrument.csv",instrument),
                       ("stability.csv",stability),("power-planning.csv",planning)):
        write_csv(output/name,rows)
    gw_rows=[dict(coefficient=name,beta=gw["beta"][i],hac_se=gw["se"][i],hac_t=gw["t"][i],wald=gw["wald"],wald_p=gw["p_value"],evidence_level=gw["evidence_level"],reason=gw["reason"])
             for i,name in enumerate(gw["coefficient_names"])]
    write_csv(output/"conditional-gw.csv",gw_rows)
    for name, value in (("fold-audit.json",audits),("models.json",models),("dependence.json",dependence),
                        ("conditional-gw.json",gw),("download-plan.json",plan_historical_download())):
        write_json(output/name,value)
    extension = {}
    if adapter is not None:
        extension = adapter.complete_analysis(prepared, output, primary, dependence, placebo,
                                              secondary, concentration, primary_months, mode, started)
    artifacts={p.name:_digest(p) for p in sorted(output.iterdir()) if p.is_file()}
    summary={"status":"passed","evidence_level":evidence,"manifest_sha256":MANIFEST_SHA256,
             "months":config["months"]["evaluate"],"synthetic_rows":len(bars),"ridge_fits":fits,
             "folds":len(audits),"primary":primary,"secondary":secondary,"artifacts_sha256":artifacts,
             "feature_diagnostics":features.diagnostics,"network_requests":0,"market_rows_read":0,
             "holdout_accessed":False,"tradability":"not evaluated",
             "limitation":"Sparse synthetic engineering validation; no historical result or Monte Carlo power estimate."}
    if adapter is not None:
        summary.pop("synthetic_rows")
        summary.update(extension, source_rows=len(bars), market_rows_read=len(bars) if mode == "real" else 0,
                       limitation="L5 unassessed: tradability, transaction costs, PnL, future applicability, cross-asset and cross-period generalization.",
                       download_plan_note=contract["evidence_labels"]["download_plan_note"])
    elapsed, peak = perf_counter()-started, peak_process_bytes()
    if elapsed>seconds_limit or peak>memory_limit:
        raise RuntimeError(f"{mode} computation resource budget exceeded")
    runtime = dict(elapsed_seconds=elapsed,peak_process_bytes=peak,ridge_fits=fits,
                   measurement="process lifetime peak working set; timings excluded from deterministic comparison")
    if adapter is not None:
        runtime.update(adapter.runtime_fields(mode, output), sensitivity_ridge_fits=extension["sensitivity_ridge_fits"],
                       total_ridge_fits=extension["total_ridge_fits"])
    write_json(output/"runtime.json",runtime)
    # A success marker is written only after every artifact and budget check.
    write_json(output/"summary.json",summary)
    return summary


def run_phase3a(*, synthetic_only: bool, config_path=DEFAULT_CONFIG, output_directory=None) -> dict:
    """Run only the frozen generator; no market path or real-data switch exists."""
    if synthetic_only is not True:
        raise ValueError("Phase 3A supports --synthetic-only; real data access is not authorized")
    config = load_history_config(config_path)
    root = data.REPOSITORY_ROOT.resolve()
    base = root / config["output"]["directory"]
    if base.resolve() != base:
        raise ValueError("Phase 3A output base must not redirect through a symlink")
    if output_directory is None:
        output = base/(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")+"-"+uuid4().hex[:8])
    else:
        supplied = Path(output_directory)
        output = supplied if supplied.is_absolute() else root / supplied
    if ".." in output.parts or output == base or not output.is_relative_to(base):
        raise ValueError("New run directory must be a descendant of results/phase3a")
    if output.resolve() != output:
        raise ValueError("Phase 3A output directory must not redirect through a symlink")
    output.mkdir(parents=True,exist_ok=False)
    try:
        summary = _execute_synthetic(config,output)
    except Exception as exc:
        _json(output/"failure.json",dict(status="failed",error_type=type(exc).__name__,error=str(exc),evidence_level=EVIDENCE))
        raise
    return {"output_directory":str(output),**summary}
