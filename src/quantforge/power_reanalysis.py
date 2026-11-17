"""Frozen Phase 2E post-hoc inference; predictor fitting is a separate audit.

No market requests or evaluation-model fits are available in this module.
The initial-training specification audit is explicitly invoked only after
the frozen prediction comparisons and their historical anchors pass.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from time import perf_counter
import tomllib
from uuid import uuid4

import numpy as np
import pandas as pd
from scipy.stats import norm
from threadpoolctl import threadpool_info, threadpool_limits

from quantforge import data
from quantforge.experiment import _provenance
from quantforge.research_policy import fixed_project_path
from quantforge.diagnostics import forecast_tests as ft
from quantforge.diagnostics.hac_tests import holm_adjust, run_hac
from quantforge.diagnostics.paired_losses import load_paired_losses
from quantforge.diagnostics.window_comparison import pair_window_predictions, _hac

CONFIG_PATH = "configs/power-reanalysis.toml"
MANIFEST_PATH = "docs/phase2e-plan-manifest.json"
MANIFEST_SHA256 = "59bcd372809ae4ed3b77066102ec4ebdeab105bf2593b77bda30c8d9bd435129"
EXPLORATORY = "exploratory_post_hoc"


def file_hash(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def load_config(path=CONFIG_PATH):
    """Verify protocol bytes and exact configuration before artifact access."""
    path = fixed_project_path(path, CONFIG_PATH)
    manifest_path = fixed_project_path(MANIFEST_PATH, MANIFEST_PATH)
    if file_hash(manifest_path) != MANIFEST_SHA256:
        raise ValueError("Frozen Phase 2E manifest hash mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        p = fixed_project_path(name, name)
        if file_hash(p) != expected:
            raise ValueError(f"Frozen protocol/configuration hash mismatch: {name}")
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    if config != manifest["config_contract"]:
        raise ValueError("Runtime configuration disagrees with frozen contract")
    from quantforge.research_policy import HOLDOUT_A, HOLDOUT_B, SMOKE_DAY
    for key, bounds in (("holdout_a", HOLDOUT_A), ("holdout_b", HOLDOUT_B)):
        expected = tuple(pd.Timestamp(config["holdouts"][f"{key}_{suffix}"]) for suffix in ("start", "end"))
        if tuple(pd.Timestamp(x) for x in bounds) != expected:
            raise ValueError("Runtime holdout constants disagree with frozen configuration")
    if str(SMOKE_DAY)[:10] != config["holdouts"]["smoke_day"]:
        raise ValueError("Runtime smoke-day constant disagrees")
    return config, manifest


def verify_inputs(config):
    """Hash every allowlisted input before parsing any prediction or bar."""
    verified = {}
    for key, value in config["inputs"].items():
        if key.endswith("_path"):
            path = data.repository_path(value)
            expected = config["inputs"][key.removesuffix("_path")+"_sha256"]
            actual = file_hash(path)
            if actual != expected:
                raise ValueError(f"Frozen input hash mismatch: {key}")
            verified[value] = actual
    return verified


def load_inputs(config):
    verified = verify_inputs(config)
    paired, pairing = load_paired_losses(summary_path=config["inputs"]["phase2b_summary_path"])
    path = data.repository_path(config["inputs"]["phase2d_a_predictions_path"])
    with path.open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != config["inputs"]["phase2d_a_predictions_sha256"]:
            raise ValueError("Window prediction hash changed before parsing")
        handle.seek(0)
        windows, window_pairing = pair_window_predictions(pd.read_parquet(handle))
    if len(paired) != config["anchors"]["paired_observations"] or len(windows) != len(paired):
        raise ValueError("Frozen complete pair count changed")
    return paired, windows, {"evidence_level": EXPLORATORY, "hashes": verified,
                             "baseline_pairing": pairing, "window_pairing": window_pairing}


def bandwidths(config):
    return [*config["inference"]["lags"], "LLSW"]


def lag_for(bandwidth, n, config):
    return ft.llsw_lag(n, config["inference"]["llsw_constant"]) if bandwidth == "LLSW" else int(bandwidth)


def monthly_stat(values, times, lag, alternative):
    """Respect original contiguous segments without changing the legacy HAC."""
    positions = ft.validate_times(times, len(values))
    cuts = np.r_[0, np.flatnonzero(np.diff(positions) != 1)+1, len(values)]
    parts = [ft.newey_west_segment(values[a:b], lag, times=times[a:b], alternative=alternative)
             for a,b in zip(cuts[:-1],cuts[1:])]
    if len(parts) == 1:
        return parts[0]
    out = ft.pool_roles(parts, pooling="n_weighted", alternative=alternative)
    out["gamma0"] = float(sum(p["n"]*p["gamma0"] for p in parts)/len(values))
    out["lag"] = lag
    out["segment_count"] = len(parts)
    return out


def _losses(group, model, baseline):
    y = group.target_return.to_numpy(dtype=float)
    f = group[f"prediction_{model}"].to_numpy(dtype=float)
    b = group[f"prediction_{baseline}"].to_numpy(dtype=float)
    return y, f, b


def _metrics(y, f, b):
    mse, base = float(np.mean((y-f)**2)), float(np.mean((y-b)**2))
    mae, base_mae = float(np.mean(abs(y-f))), float(np.mean(abs(y-b)))
    return dict(model_mse=mse, baseline_mse=base, model_mae=mae, baseline_mae=base_mae,
                relative_mse_delta_pct=None if base==0 else 100*(mse-base)/base,
                relative_mae_delta_pct=None if base_mae==0 else 100*(mae-base_mae)/base_mae,
                r2_oos=ft.r2_oos(y,f,b), cw_correlation_denominator=float(2*np.std(f-b)*np.std(y-b)))


def _prefixed(stat, prefix):
    return {f"{prefix}_{key}":stat.get(key) for key in ("mean","variance","se","t","p_value","n_eff","reason")}


def analyze_comparison(wide, model, baseline, config, evidence_level):
    """Pure frozen-prediction analysis. Never instantiate or fit an estimator."""
    monthly, roles, concentration, mde_rows = [], [], [], []
    cached = {}
    nested = model == "ridge"
    test_names = ("dm", "cw", "ic") if nested else ("dm",)
    identity_max = 0.0
    for h in config["experiment"]["horizons"]:
        for month in config["inference"]["months"]:
            group = wide.loc[(wide.horizon_minutes==h)&(wide.month==month)].sort_values("decision_time")
            if group.empty:
                raise ValueError("Missing configured horizon/month")
            times = pd.DatetimeIndex(group.decision_time)
            y,f,b = _losses(group,model,baseline)
            d,c,z = ft.dm_differential(y,f,b),ft.clark_west_differential(y,f,b),ft.ic_product(y,f)
            identity_max=max(identity_max,float(np.max(abs(-d-(c-(f-b)**2)))))
            metrics=_metrics(y,f,b)
            for bandwidth in bandwidths(config):
                lag=lag_for(bandwidth,len(group),config)
                stats={"dm":monthly_stat(d,times,lag,"two-sided")}
                if nested:
                    stats["cw"] = monthly_stat(c,times,lag,"greater")
                    stats["ic"] = monthly_stat(z,times,lag,"greater") if z is not None else None
                key=(h,month,bandwidth)
                cached[key]=(stats,metrics,group)
                row=dict(evidence_level=evidence_level,model=model,baseline=baseline,horizon_minutes=h,
                         month=month,role=str(group.role.iloc[0]),bandwidth=str(bandwidth),lag=lag,
                         admissible=ft.admissible(lag,h,config["inference"]["admissible_min_lag_multiple"]),
                         n_samples=len(group),monthly_ic=None if z is None else float(z.mean()),**metrics)
                for name,stat in stats.items(): row.update(_prefixed(stat or {},name))
                monthly.append(row)
                concentration_series=[("dm",d,"two-sided")]
                if nested: concentration_series.append(("cw",c,"greater"))
                for name,values,alternative in concentration_series:
                    for result in ft.concentration(values,config["concentration"]["top_k"],lag,times=times,alternative=alternative):
                        concentration.append({**row,**result,"series":name})
        for role in config["inference"]["roles"]:
            months=[m for m in config["inference"]["months"] if str(cached[h,m,bandwidths(config)[0]][2].role.iloc[0])==role]
            for bandwidth in bandwidths(config):
                entries=[cached[h,m,bandwidth] for m in months]
                sizes=np.array([len(e[2]) for e in entries],dtype=float)
                weights_n=sizes/sizes.sum()
                reference_mse=sum(w*e[1]["baseline_mse"] for w,e in zip(weights_n,entries))
                r2=None if reference_mse==0 else 1-sum(w*e[1]["model_mse"] for w,e in zip(weights_n,entries))/reference_mse
                for pooling in config["inference"]["pooling"]:
                    weights=weights_n if pooling=="n_weighted" else np.ones(len(entries))/len(entries)
                    stats={name:ft.pool_roles([e[0][name] for e in entries],pooling=pooling,alternative="two-sided" if name=="dm" else "greater")
                           if all(e[0][name] is not None for e in entries) else None for name in test_names}
                    metrics={k:float(sum(w*e[1][k] for w,e in zip(weights,entries))) for k in ("model_mse","baseline_mse","model_mae","baseline_mae","cw_correlation_denominator")}
                    metrics.update(relative_mse_delta_pct=None if metrics["baseline_mse"]==0 else 100*(metrics["model_mse"]/metrics["baseline_mse"]-1),
                                   relative_mae_delta_pct=None if metrics["baseline_mae"]==0 else 100*(metrics["model_mae"]/metrics["baseline_mae"]-1),r2_oos=None if r2 is None else float(r2),
                                   relative_error_reason="zero_baseline_error" if metrics["baseline_mse"]==0 or metrics["baseline_mae"]==0 else None)
                    row=dict(evidence_level=evidence_level,model=model,baseline=baseline,horizon_minutes=h,role=role,
                             bandwidth=str(bandwidth),pooling=pooling,months=months,month_count=len(months),
                             n_samples=int(sizes.sum()),actual_lags=[lag_for(bandwidth,int(n),config) for n in sizes],
                             admissible=all(ft.admissible(lag_for(bandwidth,int(n),config),h,config["inference"]["admissible_min_lag_multiple"]) for n in sizes),
                             r2_pooling="sum_sse_ratio",tradability="not evaluated",**metrics)
                    for name,stat in stats.items(): row.update(_prefixed(stat or {},name))
                    for name in test_names:
                        if stats[name] is None: continue
                        q=ft.cochran_q([e[0][name]["mean"] for e in entries],[e[0][name]["variance"] for e in entries])
                        row[f"{name}_heterogeneity_q"]=q.get("q",q.get("Q")); row[f"{name}_heterogeneity_p"]=q.get("p_value")
                        if stats[name]["se"] is None or stats[name]["se"]==0: continue
                        benefit=-stats[name]["mean"] if name=="dm" else stats[name]["mean"]
                        alternative="two-sided" if name=="dm" else "greater"
                        for sensitivity,se in [("pooled",stats[name]["se"]),("worst_month",max(e[0][name]["se"] for e in entries))]:
                            threshold=ft.mde(se,config["inference"]["alpha"],config["inference"]["power"],alternative=alternative)
                            for factor in config["inference"]["effect_scenarios"]:
                                effect=factor*benefit
                                required=ft.months_required(effect,se,len(months),config["inference"]["alpha"],config["inference"]["power"],alternative=alternative) if effect>0 else None
                                mde_rows.append(dict(evidence_level=evidence_level,model=model,baseline=baseline,horizon_minutes=h,role=role,bandwidth=str(bandwidth),pooling=pooling,series=name,se_sensitivity=sensitivity,se=se,observed_months=len(months),n_eff=stats[name]["n_eff"],mde_native=threshold,mde_relative_mse_pct=100*threshold/metrics["baseline_mse"] if name=="dm" and metrics["baseline_mse"]>0 else None,mde_ic_units=threshold if name=="ic" else threshold/metrics["cw_correlation_denominator"] if name=="cw" and metrics["cw_correlation_denominator"]>0 else None,effect_factor=factor,beneficial_effect=effect,months_required=required,power=ft.normal_power(effect,se,config["inference"]["alpha"],alternative=alternative),reason="no_positive_beneficial_effect" if effect<=0 else None,heterogeneity_q=row.get(f"{name}_heterogeneity_q"),heterogeneity_p=row.get(f"{name}_heterogeneity_p"),assumptions="fixed SE; independent identically distributed months; observed effect selected post hoc; CW correlation equivalent assumes zero means"))
                                mde_rows[-1]["power_interpretation"]="two-sided difference detection, including worse forecasts" if name=="dm" else "one-sided positive information detection"
                    roles.append(row)
    return monthly,roles,concentration,mde_rows,identity_max


def _adjust(rows, indices, name, alpha, expected):
    if len(indices)!=expected: raise ValueError("Multiplicity family differs from frozen configuration")
    adjusted=holm_adjust([rows[i][name+"_p_value"] for i in indices])
    for i,p in zip(indices,adjusted):
        rows[i][name+"_holm_p"]=p
        rows[i][name+"_holm_reject"]=None if p is None else p<alpha
        rows[i][name+"_holm_family_size"]=expected


def _encompassing(wide, window_roles, window_monthly, config, evidence_level):
    calibration,grid=[],[]
    for row in [*window_monthly,*window_roles]:
        if row["bandwidth"] not in (str(config["inference"]["legacy_main_lag"]),"LLSW"): continue
        if "pooling" in row and row["pooling"]!="n_weighted": continue
        h,model=row["horizon_minutes"],row["model"]
        months=row.get("months",[row.get("month")])
        groups=[wide.loc[(wide.horizon_minutes==h)&(wide.month==m)] for m in months]
        # Center within month, then concatenate solely for covariance summaries.
        centered_delta=[];centered_error=[];centered_f=[]
        for g in groups:
            delta=g[f"prediction_{model}"].to_numpy()-g.prediction_expanding.to_numpy()
            error=g.target_return.to_numpy()-g.prediction_expanding.to_numpy()
            f=g.prediction_expanding.to_numpy()
            centered_delta.append(delta-delta.mean());centered_error.append(error-error.mean());centered_f.append(f-f.mean())
        approx=ft.encompassing_approx(np.concatenate(centered_delta),np.concatenate(centered_error),row["dm_n_eff"])
        actual=None if row["dm_t"] is None else -row["dm_t"]
        out={"evidence_level":evidence_level,"horizon_minutes":h,"window":model,"role":row["role"],"month":row.get("month"),"months":months,"bandwidth":row["bandwidth"],"dm_z":row["dm_t"],"actual_improvement_t":actual,**approx}
        out["approximation_error"]=None if actual is None or approx["t_approx"] is None else approx["t_approx"]-actual
        out["absolute_approximation_error"]=None if out["approximation_error"] is None else abs(out["approximation_error"])
        calibration.append(out)
        if "pooling" not in row: continue
        sigma_f=float(np.std(np.concatenate(centered_f)))
        for rho in config["encompassing"]["rho_grid"]:
            for ratio in config["encompassing"]["delta_ratio_grid"]:
                sigma_delta=ratio*sigma_f
                t=None if row["dm_n_eff"] is None or approx["sigma_error"]==0 else float(np.sqrt(row["dm_n_eff"])*(rho-sigma_delta/(2*approx["sigma_error"])))
                critical=float(norm.ppf(1-config["inference"]["alpha"])+norm.ppf(config["inference"]["power"]))
                required=len(months)*(critical/t)**2 if t is not None and t>0 else None
                grid.append(dict(evidence_level=evidence_level,horizon_minutes=h,role=row["role"],calibration_window=model,bandwidth=row["bandwidth"],rho=rho,delta_ratio=ratio,sigma_f=sigma_f,sigma_error=approx["sigma_error"],n_eff_d=row["dm_n_eff"],t_approx=t,target_t=critical,months_required=required,observed_months=len(months),interpretation="planning approximation; not a test; fixed observed dependence and SDs"))
    return calibration,grid


def analyze_predictions(paired, windows, config, *, evidence_level=EXPLORATORY):
    """All reanalysis is a pure function of already-frozen predictions."""
    monthly,roles,conc,mde_rows,identities=[],[],[],[],[]
    for baseline in config["inference"]["references"]:
        m,r,c,p,i=analyze_comparison(paired,"ridge",baseline,config,evidence_level)
        monthly+=m;roles+=r;conc+=c;mde_rows+=p;identities.append(i)
    selected=[i for i,r in enumerate(roles) if r["bandwidth"]==config["inference"]["primary_bandwidth"] and r["pooling"]==config["inference"]["primary_pooling"]]
    _adjust(roles,selected,"cw",config["inference"]["alpha"],config["inference"]["cw_holm_family_size"])
    evidence=[]
    for i in selected:
        row=roles[i].copy();row.update(family="F_CW",significance_status="exploratory, post-hoc, already-seen months",predictive_information="CW / IC-HAC",realized_error="MSE / MAE / two-sided DM",confirmatory_evidence="none",tradability="not evaluated")
        evidence.append(row)
    wm,wr=[],[]
    for window in config["inference"]["windows"]:
        m,r,c,p,i=analyze_comparison(windows,window,"expanding",config,evidence_level)
        wm+=m;wr+=r;conc+=c;mde_rows+=p;identities.append(i)
    for bandwidth in bandwidths(config):
        indices=[i for i,r in enumerate(wr) if r["horizon_minutes"]==config["inference"]["primary_horizon"] and r["pooling"]==config["inference"]["primary_pooling"] and r["bandwidth"]==str(bandwidth)]
        _adjust(wr,indices,"dm",config["inference"]["alpha"],config["inference"]["window_holm_family_size"])
    calibration,grid=_encompassing(windows,wr,wm,config,evidence_level)
    cost,baseline_metrics=[],[]
    for (h,month),g in paired.groupby(["horizon_minutes","month"],sort=True):
        values=np.abs(g.prediction_ridge.to_numpy())*config["cost_magnitude"]["return_to_bp"]
        row=dict(evidence_level=evidence_level,horizon_minutes=int(h),month=month,n_samples=len(g),max_bp=float(values.max()),tradability="not evaluated",threshold_status="illustrative; current Binance fees not verified")
        for q in config["cost_magnitude"]["quantiles"]:row[f"p{round(q*100)}_bp"]=float(np.quantile(values,q))
        for threshold in config["cost_magnitude"]["thresholds_bp"]:row[f"fraction_ge_{threshold}bp"]=float(np.mean(values>=threshold))
        cost.append(row)
        for model in ["ridge",*config["inference"]["references"]]:
            err=g.target_return.to_numpy()-g[f"prediction_{model}"].to_numpy()
            baseline_metrics.append(dict(evidence_level=evidence_level,horizon_minutes=int(h),month=month,model=model,n_samples=len(g),mse=float(np.mean(err**2)),mae=float(np.mean(abs(err)))))
    from quantforge.diagnostics.placebo import sign_flip_placebo,time_shift_placebo
    placebo=[]
    for h in config["experiment"]["horizons"]:
        for role in config["inference"]["roles"]:
            groups=[]
            for i,month in enumerate(config["inference"]["months"]):
                g=paired.loc[(paired.horizon_minutes==h)&(paired.month==month)&(paired.role==role)]
                if len(g):groups.append(dict(times=pd.DatetimeIndex(g.decision_time),y=g.target_return.to_numpy(),f=g.prediction_ridge.to_numpy(),month_index=config["placebo"]["month_indices"][i],month=month))
            kwargs=dict(horizon=h,role_id=config["placebo"]["role_ids"][role],seed=config["experiment"]["seed"],settings=config["placebo"],inference=config["inference"],evidence_level=evidence_level)
            for result in [*sign_flip_placebo(groups,**kwargs),*time_shift_placebo(groups,**kwargs)]:placebo.append({**result,"role":role,"horizon_minutes":h})
    legacy2c=run_hac(paired,tuple(config["inference"]["lags"]))
    legacy2da=_hac(windows)
    for frame in [legacy2c,legacy2da]:frame["evidence_level"]=evidence_level
    tables={"evidence-layers":evidence,"monthly-tests":monthly,"role-tests":roles,"window-tests":wr,"window-monthly":wm,"concentration":conc,"mde-power":mde_rows,"encompassing-calibration":calibration,"encompassing-grid":grid,"placebo":placebo,"cost-magnitude":cost,"baseline-metrics":baseline_metrics}
    tables={k:pd.DataFrame(v) for k,v in tables.items()}
    tables.update({"legacy-hac-2c":legacy2c,"legacy-hac-2da":legacy2da})
    return tables,{"evidence_level":evidence_level,"max_abs_error":max(identities),"tolerance":config["anchors"]["identity_atol"],"passed":max(identities)<=config["anchors"]["identity_atol"]}


def _clean(value):
    if isinstance(value,dict): return {str(k):_clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple,np.ndarray)):return [_clean(v) for v in value]
    if isinstance(value,(np.integer,)):return int(value)
    if isinstance(value,(np.bool_,)):return bool(value)
    if isinstance(value,(float,np.floating)):return None if not np.isfinite(value) else float(value)
    if isinstance(value,(pd.Timestamp,datetime)):return value.isoformat()
    return value


def write_json(path,value):
    temporary=path.with_suffix(path.suffix+".partial")
    temporary.write_text(json.dumps(_clean(value),indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8",newline="\n")
    temporary.replace(path)


def validate_anchors(tables,spec,config):
    """Hard acceptance gates; never change estimates to match a reference."""
    a=config["anchors"];checks=[]
    def check(name,actual,expected,tolerance):
        passed=bool(np.isfinite(actual) and abs(actual-expected)<=tolerance)
        checks.append(dict(evidence_level=EXPLORATORY,name=name,actual=actual,expected=expected,absolute_tolerance=tolerance,passed=passed))
        if not passed:raise ValueError(f"Anchor {name} failed: {actual} expected {expected} +/- {tolerance}")
    role=tables["role-tests"]
    select=lambda r:role.loc[(role.horizon_minutes==config["inference"]["primary_horizon"])&(role.role==r)&(role.baseline=="zero")&(role.bandwidth==str(config["inference"]["legacy_main_lag"]))&(role.pooling==config["inference"]["primary_pooling"])].iloc[0]
    v,d=select(config["inference"]["roles"][0]),select(config["inference"]["roles"][1])
    check("A1_relative_mse_pct",v.relative_mse_delta_pct,a["a1_relative_mse_pct"],a["a1_relative_mse_pct_atol"])
    check("A1_abs_dm_z",abs(v.dm_t),a["a1_dm_abs_z"],a["a1_dm_abs_z_atol"])
    if v.dm_t>=0:raise ValueError("A1 DM direction mismatch")
    check("A2_cw",v.cw_t,a["a2_cw_t"],a["a2_cw_t_atol"])
    check("A3_cw",d.cw_t,a["a3_cw_t"],a["a3_cw_t_atol"])
    check("A5_ic",v.ic_mean,a["a5_validation_ic"],a["a5_validation_ic_atol"])
    for table,key,countkey in [("legacy-hac-2c","phase2c_hac","a4_rows_2c"),("legacy-hac-2da","phase2d_a_hac","a4_rows_2da")]:
        observed=tables[table].drop(columns="evidence_level")
        expected=pd.read_csv(data.repository_path(config["inputs"][key+"_path"]))
        # Round-trip object fields through CSV for the same historical format.
        import io
        actual=pd.read_csv(io.StringIO(observed.to_csv(index=False)))
        pd.testing.assert_frame_equal(actual,expected,check_exact=False,rtol=a["a4_a7_rtol"],atol=0,check_dtype=False)
        compare_new_dm_to_legacy(tables,table,expected,a["a4_a7_rtol"])
        check("A4_"+table+"_rows",len(actual),a[countkey],0)
    check("A4_min_p",tables["legacy-hac-2c"].p_value.min(),a["a4_min_p"],a["a4_min_p_atol"])
    check("A4_window_nulls",int(tables["legacy-hac-2da"].p_value.isna().sum()),a["a4_nulls_2da"],0)
    expected=pd.read_csv(data.repository_path(config["inputs"]["phase2b_metrics_path"]))
    actual=tables["baseline-metrics"]
    keys=["horizon_minutes","month","model"]
    pd.testing.assert_frame_equal(actual.set_index(keys)[["mse","mae"]].sort_index(),expected.set_index(keys)[["mse","mae"]].sort_index(),check_exact=False,rtol=a["a4_a7_rtol"],atol=0)
    check("A7_metrics_rows",len(actual),len(expected),0)
    if spec is not None:
        validate_spec_anchors(spec,config,check)
    return {"evidence_level":EXPLORATORY,"status":"passed","checks":checks}


def compare_new_dm_to_legacy(tables, legacy_name, expected, rtol):
    """Anchor the new kernel/pooling path, not merely rerun historical code."""
    window=legacy_name=="legacy-hac-2da"
    for _,old in expected.iterrows():
        name=("window-monthly" if window else "monthly-tests") if old.scope_type=="month" else ("window-tests" if window else "role-tests")
        table=tables[name]
        mask=(table.horizon_minutes==old.horizon_minutes)&(table.bandwidth==str(old.lag))&(table.baseline==old.baseline)
        if window:mask &= table.model==old.window
        if old.scope_type=="month":mask &= table.month==old.scope_id
        else:mask &= (table.role==old.role)&(table.pooling==old.aggregation)
        new=table.loc[mask]
        if len(new)!=1:raise ValueError("New/legacy HAC key mismatch")
        new=new.iloc[0]
        for old_key,new_key in [("estimate_mse_delta","dm_mean"),("variance","dm_variance"),("se","dm_se"),("z","dm_t"),("p_value","dm_p_value")]:
            x,y=new[new_key],old[old_key]
            if pd.isna(x) and pd.isna(y):continue
            if not np.isclose(x,y,rtol=rtol,atol=0):raise ValueError(f"New-path historical HAC mismatch: {name} {old_key}")


def validate_spec_anchors(spec,config,check):
    """Filled against the specification module's explicit training-only schema."""
    for result in spec["horizons"]:
        h=result["horizon_minutes"]
        check(f"A6_train_n_{h}m",result["train_n"],config["anchors"][f"a6_train_n_{h}m"],0)
        # Schema details are asserted rather than inferred from arbitrary JSON.
        pair=next(p for p in result["correlation"]["duplicate_pairs"] if set([p["first"],p["later"]])=={"log_return_1m","log_close_open"})
        check(f"A6_duplicate_corr_{h}m",pair["correlation"],config["anchors"]["a6_duplicate_corr"],config["anchors"]["a6_duplicate_corr_atol"])
        if h==config["inference"]["primary_horizon"]:
            spectrum=next(w for w in result["windows"] if w["window"]=="six_month")
            check("A6_condition",spectrum["ridge_condition"],config["anchors"]["a6_condition_15m"],config["anchors"]["a6_condition_15m_atol"])
            check("A6_shrinkage",min(spectrum["shrinkage_factors"]),config["anchors"]["a6_weakest_shrinkage"],config["anchors"]["a6_weakest_shrinkage_atol"])


def _report(tables,identity,anchors,spec):
    lines=["# Phase 2E frozen-prediction reanalysis","","Evidence: exploratory_post_hoc. Confirmatory evidence: none. Tradability: not evaluated.","",
           "Clark-West/IC test predictive information; DM/MSE/MAE test realized forecast error. They are not substitutes.","",
           f"Identity passed: {identity['passed']}. Anchor status: {anchors['status']}.","",
           "All bandwidths, pooling schemes and comparisons are retained in the accompanying CSV tables.",
           "Power extrapolations assume fixed dependence/variance and independent identically distributed months; two observed months cannot establish stationarity.",
           "Training specification variants are diagnostic only and have no evaluation-period forecasts.",""]
    if bool(tables["placebo"].get("pipeline_warning",pd.Series(dtype=bool)).fillna(False).any()):
        lines.insert(4,"PIPELINE PROBLEM: placebo mean/SD gate failed; real Clark-West results must not be interpreted.")
    return "\n".join(lines)


def run_power_reanalysis(config_path=CONFIG_PATH,*,run_kind="formal"):
    """One bounded clean-run analysis, with success marker written last."""
    config,manifest=load_config(config_path)
    if run_kind not in ("formal","reproduction"):raise ValueError("Unknown run kind")
    output_root=fixed_project_path(config["output"]["directory"],"results/power-reanalysis")
    run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")+"-"+uuid4().hex[:8]
    output=output_root/run_id;output.mkdir(parents=True,exist_ok=False)
    started=perf_counter();provenance=None
    try:
        provenance=_provenance()
        if provenance["git_dirty"] is not False:raise ValueError("Formal reanalysis requires a clean worktree")
        provenance.setdefault("source_sha256",{})["scripts/run_power_reanalysis.py"]=file_hash(data.repository_path("scripts/run_power_reanalysis.py"))
        with threadpool_limits(limits=config["experiment"]["cpu_threads"]):
            paired,windows,sources=load_inputs(config)
            loaded=perf_counter()
            tables,identity=analyze_predictions(paired,windows,config)
            if not identity["passed"]:raise ValueError("Clark-West algebra identity failed")
            anchors=validate_anchors(tables,None,config)
            analyzed=perf_counter()
            from quantforge.diagnostics.spec_audit import run_specification_audit
            spec=run_specification_audit(config)
            validate_spec_anchors(spec,config,lambda name,actual,expected,tolerance:_append_anchor(anchors,name,actual,expected,tolerance))
            spec_done=perf_counter()
            from quantforge.online.foundation import peak_process_memory
            memory=peak_process_memory()
            if memory["peak_process_bytes"]>config["resource_limits"]["max_memory_gb"]*config["resource_limits"]["bytes_per_gb"]:
                raise ValueError("Frozen memory budget exceeded")
            for name,table in tables.items():
                if "evidence_level" not in table or table.evidence_level.isna().any():raise ValueError("Missing evidence label")
                table.to_csv(output/(name+".csv"),index=False,lineterminator="\n")
            for name,value in [("identity",identity),("anchors",anchors),("specification-audit",spec),("resolved-config",{"evidence_level":EXPLORATORY,"config":config})]:write_json(output/(name+".json"),value)
            (output/"report.md").write_text(_report(tables,identity,anchors,spec),encoding="utf-8",newline="\n")
            deterministic=sorted(p.name for p in output.iterdir())
            hashes={p:file_hash(output/p) for p in deterministic}
            budget={"evidence_level":EXPLORATORY,"input_seconds":loaded-started,"prediction_reanalysis_seconds":analyzed-loaded,"specification_audit_seconds":spec_done-analyzed,"elapsed_seconds":perf_counter()-started,"memory":memory,"threadpools":threadpool_info(),"new_evaluation_fits":0,"new_evaluation_predictions":0,"training_specification_fits":config["spec_audit"]["model_fit_count"],"network_requests":0}
            if budget["elapsed_seconds"]>config["resource_limits"]["max_run_minutes"]*60:raise ValueError("Frozen time budget exceeded")
            write_json(output/"budget.json",budget)
            warning=bool(tables["placebo"].get("pipeline_warning",pd.Series(dtype=bool)).fillna(False).any())
            summary={"evidence_level":EXPLORATORY,"status":"completed_power_reanalysis","run_id":run_id,"run_kind":run_kind,"output_directory":output.relative_to(data.REPOSITORY_ROOT).as_posix(),"provenance":provenance,"sources":sources,"protocol_hashes":manifest["files"],"deterministic_artifacts":hashes,"table_rows":{k:len(v) for k,v in tables.items()},"anchors":anchors,"identity":identity,"placebo_pipeline_warning":warning,"cw_interpretable":not warning,"holdout_accessed":False,"network_requests":0,"new_evaluation_models":0,"confirmatory_evidence":"none","tradability":"not evaluated","elapsed_seconds":budget["elapsed_seconds"]}
            write_json(output/"summary.json",summary)
            return summary
    except Exception as exc:
        write_json(output/"failure.json",{"evidence_level":EXPLORATORY,"status":"failed","run_id":run_id,"run_kind":run_kind,"error_type":type(exc).__name__,"error":str(exc),"provenance":provenance,"elapsed_seconds":perf_counter()-started})
        raise


def _append_anchor(anchors,name,actual,expected,tolerance):
    passed=bool(np.isfinite(actual) and abs(actual-expected)<=tolerance)
    row=dict(evidence_level="training_period_diagnostic",name=name,actual=actual,expected=expected,absolute_tolerance=tolerance,passed=passed)
    anchors["checks"].append(row)
    if not passed:raise ValueError(f"Anchor {name} failed: {actual} expected {expected} +/- {tolerance}")
