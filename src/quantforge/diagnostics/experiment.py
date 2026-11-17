"""Offline diagnostics runner for a fixed, hash-identified prediction dataset."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import time
import tomllib
from uuid import uuid4

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from quantforge import data
from quantforge.experiment import _provenance, _write_json
from quantforge.research_policy import fixed_project_path
from quantforge.diagnostics.calibration import run_calibration, loss_autocorrelation
from quantforge.diagnostics.figures import generate_figures

PLAN_HASH = '1dcb6c79d947a3c13e478efb367a1d77e8e3d671698fc0b6fb2c1864daac40d3'
FROZEN_CONFIG = {
    'input': {'summary_path':'results/research-baseline/20261103T010111153259Z-a2cb0858/summary.json',
              'summary_sha256':'7aa9f334a9e5b5f1085edd2362676d86d0898fcdd376f92bb1886efbc3dc6f12',
              'prediction_sha256':'66f74e1367ed816455d8df2af5c8fdd89009c893aa5fe064df29fe646c86407a'},
    'plan': {'path':'docs/phase2c-analysis-plan.md','sha256':PLAN_HASH},
    'analysis': {'seed':42,'horizons':[15,5],'baselines':['zero','historical_mean'],'confidence':.95,
                 'roles':['validation','development_evaluation'],'months':['2026-03','2026-04','2026-05','2026-06']},
    'bootstrap': {'replicates':2000,'main_block_length':1440,'block_lengths':[60,360,1440,2880],
                  'method':'non_circular_moving_block_percentile'},
    'hac': {'main_lag':60,'lags':[15,60,240],'kernel':'bartlett','main_family_size':32,'global_family_size':96},
    'output': {'directory':'results/statistical-diagnostics'},
}


def file_hash(path: Path) -> str:
    with data.repository_path(path).open('rb') as handle:
        return hashlib.file_digest(handle,'sha256').hexdigest()


def load_config(path='configs/statistical-diagnostics.toml') -> dict:
    p=data.repository_path(path)
    if not p.is_relative_to(data.repository_path('configs')) or p.suffix!='.toml':
        raise ValueError('Diagnostics config must be a TOML inside configs')
    config=tomllib.loads(p.read_text(encoding='utf-8'))
    # Serialized equality also distinguishes booleans from integer scientific choices.
    if json.dumps(config,sort_keys=True)!=json.dumps(FROZEN_CONFIG,sort_keys=True):
        raise ValueError('Diagnostics plan and parameters are frozen; overrides are forbidden')
    if file_hash(data.repository_path(config['plan']['path']))!=PLAN_HASH:
        raise ValueError('Frozen analysis plan SHA256 mismatch')
    return config


def paired_summary(paired):
    rows=[]
    for (h,role),group in paired.groupby(['horizon_minutes','role'],sort=True):
        for base in ('zero','historical_mean'):
            monthly=[]
            for month,frame in group.groupby('month',sort=True):
                r={'horizon_minutes':int(h),'role':role,'baseline':base,'scope_type':'month','scope_id':month,
                   'aggregation':'month','n_samples':len(frame),'month_count':1}
                for loss,label in (('squared_loss','mse'),('absolute_loss','mae')):
                    ridge=float(frame[f'{loss}_ridge'].mean()); baseline=float(frame[f'{loss}_{base}'].mean())
                    r[f'ridge_{label}']=ridge; r[f'baseline_{label}']=baseline
                    r[f'{label}_delta']=float((frame[f'{loss}_ridge']-frame[f'{loss}_{base}']).mean())
                    r[f'relative_{label}_delta']=r[f'{label}_delta']/baseline if baseline>0 else None
                    r[f'relative_{label}_null_reason']=None if baseline>0 else 'zero_baseline_loss'
                rows.append(r); monthly.append(r)
            for aggregation in ('n_weighted','month_equal'):
                weights=np.array([m['n_samples'] for m in monthly],dtype=float) if aggregation=='n_weighted' else np.ones(len(monthly))
                weights/=weights.sum()
                r={'horizon_minutes':int(h),'role':role,'baseline':base,'scope_type':'role','scope_id':role,
                   'aggregation':aggregation,'n_samples':len(group),'month_count':len(monthly)}
                for label in ('mse','mae'):
                    for field in (f'ridge_{label}',f'baseline_{label}',f'{label}_delta'):
                        r[field]=float(sum(w*m[field] for w,m in zip(weights,monthly,strict=True)))
                    r[f'relative_{label}_delta']=r[f'{label}_delta']/r[f'baseline_{label}'] if r[f'baseline_{label}']>0 else None
                    r[f'relative_{label}_null_reason']=None if r[f'baseline_{label}']>0 else 'zero_baseline_loss'
                rows.append(r)
    return pd.DataFrame(rows)


def _markdown_table(frame, columns):
    lines=['| '+' | '.join(columns)+' |','|'+'|'.join(['---']*len(columns))+'|']
    for row in frame.loc[:,columns].itertuples(index=False,name=None):
        values=[]
        for x in row:
            if pd.isna(x): values.append('null')
            elif isinstance(x,float): values.append(f'{x:.8g}')
            else: values.append(str(x))
        lines.append('| '+' | '.join(values)+' |')
    return '\n'.join(lines)


def report(summary, bootstrap, hac, calibration):
    lines=['# QuantForge Phase 2C statistical diagnostics','',
      'Post-observation follow-up diagnostics of frozen predictions. All fixed sensitivities are retained. '
      'No final-holdout access, predictor changes, recalibration, orders or profitability claims.','',
      f"Run `{summary['run_id']}`; plan SHA256 `{PLAN_HASH}`; seed 42; 2,000 MBB draws.",'',
      '## Main moving-block intervals (L=1440)','',
      'Negative paired squared-loss difference favors Ridge. Relative intervals resample their denominator. '
      'Intervals are marginal percentile intervals, not simultaneous confidence bands.','',
      _markdown_table(bootstrap[bootstrap.block_length==1440],['scope_id','aggregation','horizon_minutes','baseline',
          'estimate_mse_delta','ci_low','ci_high','relative_mse_delta','relative_ci_low','relative_ci_high']),'',
      '## Main HAC (lag=60)','',
      'Bartlett/Newey-West with fixed n autocovariance denominator, normal approximation, no finite-sample correction. '
      'Role aggregation assumes zero cross-month covariance and conditions on two observed months.','',
      _markdown_table(hac[hac.lag==60],['scope_id','aggregation','horizon_minutes','baseline','estimate_mse_delta',
          'se','ci_low','ci_high','p_value','holm_main32_p','holm_global96_p']),'',
      '## Monthly Ridge calibration','',
      _markdown_table(calibration[calibration.model=='ridge'],['month','role','horizon_minutes','pearson_ic','spearman_correlation',
          'prediction_mean','target_mean','std_ratio','prediction_bias','diagnostic_slope']),'',
      '## Limitations','',
      'Only four adjacent months of a single asset are observed. All four were already seen in Phase 2B. '
      'Overlapping targets, heavy tails, nonstationarity and shared expanding training windows limit statistical inference. '
      'MBB never crosses a month or gap; non-circular edge sampling has finite-sample bias. '
      'Long blocks have few effective blocks. HAC p-values rely on weak-dependence/asymptotic assumptions; '
      'Holm controls multiplicity only under valid constituent p-values, not post-selection bias. '
      'Diagnostic calibration slopes are not applied to evaluation predictions. '
      'Neither nominal significance nor positive IC establishes cross-regime generalization or trading profitability.','',
      'All 128 Bootstrap rows, all 96 HAC rows (including main32/global96 Holm), distribution quantiles, '
      'calibration bins, ACF and 14 static figures are preserved with artifact hashes.','']
    return '\n'.join(lines)


def run_diagnostics(config_path='configs/statistical-diagnostics.toml') -> dict:
    config=load_config(config_path)
    run_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-'+uuid4().hex[:8]
    output_relative=f"{config['output']['directory']}/{run_id}"
    output=fixed_project_path(output_relative,output_relative)
    output.mkdir(parents=True,exist_ok=False)
    started=time.perf_counter(); stages={}; provenance=None
    try:
        from quantforge.diagnostics.paired_losses import load_paired_losses
        from quantforge.diagnostics.block_bootstrap import run_bootstrap
        from quantforge.diagnostics.hac_tests import run_hac
        provenance=_provenance()
        provenance['dependency_versions']['matplotlib']=importlib.metadata.version('matplotlib')
        for path in (config_path,'docs/phase2c-analysis-plan.md','docs/phase2c-plan-manifest.json','scripts/run_statistical_diagnostics.py'):
            provenance['source_sha256'][path]=file_hash(data.repository_path(path))
        _write_json(output/'resolved-config.json',config)
        tick=time.perf_counter(); paired,audit=load_paired_losses()
        stages['pairing']=time.perf_counter()-tick
        pairs=paired_summary(paired)
        with threadpool_limits(limits=1):
            tick=time.perf_counter(); bootstrap=run_bootstrap(paired,replicates=2000,seed=42,block_lengths=(60,360,1440,2880)); stages['bootstrap']=time.perf_counter()-tick
            tick=time.perf_counter(); hac=run_hac(paired,lags=(15,60,240)); stages['hac']=time.perf_counter()-tick
            tick=time.perf_counter(); calibration,bins,distributions=run_calibration(paired); acf=loss_autocorrelation(paired); stages['calibration_acf']=time.perf_counter()-tick
        if len(bootstrap)!=128 or len(hac)!=96 or len(calibration)!=24:
            raise ValueError('Incomplete fixed analysis grid')
        outputs={'paired-loss-summary.csv':pairs,'bootstrap-results.csv':bootstrap,'hac-results.csv':hac,
                 'calibration-summary.csv':calibration,'calibration-quantiles.csv':bins,'distribution-quantiles.csv':distributions,
                 'loss-autocorrelation.csv':acf}
        for name,frame in outputs.items():
            temporary=data.repository_path(output/(name+'.partial')); frame.to_csv(temporary,index=False); temporary.replace(output/name)
        _write_json(output/'pairing-audit.json',audit)
        tick=time.perf_counter(); figures=generate_figures(paired,calibration,bins,bootstrap,acf,output/'figures'); stages['figures']=time.perf_counter()-tick
        if len(figures)!=14: raise ValueError('All seven figure types for two horizons are required')
        summary={'status':'completed_statistical_diagnostics','run_id':run_id,'analysis_time_utc':datetime.now(timezone.utc).isoformat(),
            'analysis_client_timezone':'Asia/Shanghai','output_directory':output.relative_to(data.REPOSITORY_ROOT).as_posix(),
            'config':config,'config_sha256':file_hash(data.repository_path(config_path)),'plan_sha256':PLAN_HASH,
            'provenance':provenance,'input_audit':audit,'paired_observations':len(paired),
            'counts':{name:len(frame) for name,frame in outputs.items()},'timings_seconds':stages,
            'holdout_accessed':False,'predictors_modified':False,'new_market_downloads':False,
            'inference_scope':'post-observation diagnostics, conditional on observed months; not preregistered confirmation or profit',
            'failures':[],'limitations':['Two months per role; zero cross-month covariance assumption',
               'Weak-dependence and moment assumptions may fail in financial tails',
               'Nominal and Holm p-values do not correct prior observation or generalization bias',
               'No new calibration fitted or applied; all fixed sensitivities retained']}
        (output/'report.md').write_text(report(summary,bootstrap,hac,calibration),encoding='utf-8')
        summary['elapsed_seconds']=time.perf_counter()-started
        artifacts=[*outputs,'pairing-audit.json','resolved-config.json','report.md']+[p.relative_to(output).as_posix() for p in figures]
        summary['artifacts_sha256']={name:file_hash(output/name) for name in artifacts}
        _write_json(output/'summary.json',summary)
        return summary
    except Exception as exc:
        _write_json(output/'failure.json',{'status':'failed','run_id':run_id,'error_type':type(exc).__name__,
            'error':str(exc),'config':config,'provenance':provenance,'plan_sha256':PLAN_HASH})
        raise
