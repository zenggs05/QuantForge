"""Generate the two authorized clean synthetic engine-validation runs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT)]

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_info, threadpool_limits

from quantforge import data
from quantforge.labels import mature_labels
from quantforge.online.audit_log import verify_chain
from quantforge.online.batch_oracle import derive_mature_sets
from quantforge.online.foundation import audit_checkpoint_v2, peak_process_memory
from quantforge.online.hardening_config import graded_limits, load_hardening_config
from quantforge.online.replay import ReplayEngine
from tests.conftest import noisy_minute_bars
from tests.phase2f_helpers import noisy_replay, oracle_metadata, sample_ids
from tests.phase2f_numerical_helpers import evaluate_kahan, evaluate_stress_case, stress_cases


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False)+'\n',
                    encoding='utf-8', newline='\n')


def _clean_commit():
    status = subprocess.run(['git','status','--porcelain'],cwd=ROOT,check=True,
                            capture_output=True,text=True).stdout
    if status.strip():
        raise ValueError('Formal Phase 2F validation requires a clean committed worktree')
    return subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,check=True,
                           capture_output=True,text=True).stdout.strip()


def _compatibility_and_oracle(factory, horizon, output, config):
    engine, fx, ly, events, features, labels, initial, attrs = noisy_replay(factory,horizon)
    feature_meta, label_meta = oracle_metadata(features,labels,horizon)
    initial_ids = sample_ids(initial,horizon)
    lookup = {f'{horizon}:{value.isoformat()}':value for value in features.frame.index}
    fixed = StandardScaler().fit(features.frame.loc[initial])
    stale_deltas, errors = [], []
    checkpoint_at = len(events)//2
    v1_path = Path(f'results/online-foundation/h{horizon}/v1.json')
    v2_core = Path(f'results/online-foundation/h{horizon}/v2/core.json')
    v2_log = Path(f'results/online-foundation/h{horizon}/v2/events.jsonl')
    as_of_audits = []
    oracle_row_visits = 0
    for number,event in enumerate(events,1):
        x = fx(event,event.decision_time)
        stale = float(engine.model.predict(x.reshape(1,-1))[0])
        _,_,valid_ids,_ = derive_mature_sets(feature_meta,label_meta,event.decision_time,initial_ids)
        indices = pd.DatetimeIndex([lookup[value] for value in initial_ids|valid_ids]).sort_values()
        oracle_row_visits += len(indices)
        z = fixed.transform(features.frame.loc[indices])
        targets = mature_labels(labels.frame,event.decision_time,indices)
        oracle = Ridge(alpha=1.,fit_intercept=True,solver='svd').fit(z,targets.to_numpy())
        probe = fixed.transform(features.frame.loc[[pd.Timestamp(event.feature_reference)]])
        expected = float(oracle.predict(probe)[0])
        predicted = engine.step(event,fx,ly)
        centered = z-z.mean(axis=0)
        condition = float(np.linalg.cond(centered.T@centered+np.eye(z.shape[1])))
        limits = graded_limits(condition,oracle.coef_,config=config)
        error = abs(predicted['prediction']-expected)
        relative = error/max(abs(expected),limits['norm_denominator_floor'])
        if relative>limits['prediction_relative_l2']:
            raise ValueError('Synthetic per-decision independent oracle mismatch')
        stale_deltas.append(abs(predicted['prediction']-stale))
        errors.append({'event':number,'absolute':error,'relative':relative,'condition':condition})
        as_of_audits.append(audit_checkpoint_v2(engine,feature_meta,label_meta,event.decision_time,initial_ids))
        if number==checkpoint_at:
            engine.save_checkpoint(v1_path)
            engine.save_checkpoint_v2(v2_core,v2_log)
    if max(stale_deltas)<config['synthetic']['stale_margin']:
        raise ValueError('Synthetic stale-versus-correct margin failed')
    recovered, recovery_budget = [], []
    for path in (v1_path,v2_core):
        restored = ReplayEngine.load_checkpoint(path,source_sha256='a'*64,config_sha256='b'*64,horizon_minutes=horizon)
        before_counts = restored.model.state_dict()['payload']['counts']
        for event in events[checkpoint_at:]:
            restored.step(event,fx,ly)
        if restored.state_dict()!=engine.state_dict():
            raise ValueError('Synthetic checkpoint continuation differs from uninterrupted v1')
        recovered.append(restored)
        after_counts = restored.model.state_dict()['payload']['counts']
        recovery_budget.append({'format_version':1 if path==v1_path else 2,
            'model_count_deltas':{name:after_counts[name]-before_counts[name] for name in after_counts},
            'load_validation_eigendecompositions':restored.model.diagnostics()['state_validation_eigendecompositions'],
            'recovery_prediction_calls':restored.model.diagnostics()['prediction_calls']})
    if recovered[0].state_dict()!=recovered[1].state_dict():
        raise ValueError('Synthetic v1/v2 restored payload mismatch')
    final_path = Path(f'results/online-foundation/h{horizon}/final-v1.json')
    engine.save_checkpoint(final_path)
    return {
        'horizon':horizon,'events':len(events),'initial_samples':len(initial),
        'stale_max_abs':max(stale_deltas),'oracle_max_abs':max(row['absolute'] for row in errors),
        'oracle_max_relative':max(row['relative'] for row in errors),
        'per_event_oracle_errors':errors,'maturity_audits':as_of_audits,
        'ols_sanity':attrs['phase2f_sanity'],'final_state_sha256':engine.state_dict()['sha256'],
        'v1_v2_continuation_equal':True,'audit_chain':verify_chain(v2_log),
        'budget':{'initial_ridge_solves':1,'initial_scaler_fits':2,
                  'formal_statistics_updates':engine.model.diagnostics()['updated_samples'],
                  'formal_ridge_solves':engine.model.diagnostics()['solve_calls']-1,
                  'actual_model_counts':engine.model.state_dict()['payload']['counts'],
                  'prediction_calls_including_stale_control':engine.model.diagnostics()['prediction_calls'],
                  'stale_prediction_calls':len(stale_deltas),
                  'per_event_svd_oracle_fits':len(errors),'oracle_training_row_visits':oracle_row_visits,
                  'recovery_branches':len(recovered),'recovery_actual_counts':recovery_budget,
                  'recovery_statistics_updates':sum(r['model_count_deltas']['updated_samples'] for r in recovery_budget),
                  'recovery_ridge_solves':sum(r['model_count_deltas']['solve_calls'] for r in recovery_budget),
                  'checkpoint_loads':2,'v1_saves':2,'v2_saves':1},
    }


def run(kind):
    config = load_hardening_config()
    commit = _clean_commit()
    parent = ROOT/'.tmp'/'phase2f-runs'
    parent.mkdir(parents=True,exist_ok=True)
    if kind not in ('formal','reproduction'):
        raise ValueError('Unknown validation run kind')
    hashes = {name:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for name,path in {
        'plan':'docs/phase2f-plan.md','config':'configs/c1-engine-hardening.toml',
        'manifest':'docs/phase2f-plan-manifest.json'}.items()}
    formal = None
    if kind=='reproduction':
        reservation=json.loads((parent/'formal.reservation.json').read_text('utf-8'))
        formal_path=ROOT/reservation['output_directory']/'summary.json'
        formal=json.loads(formal_path.read_text('utf-8'))
        if (formal['status']!='passed' or formal['git_commit']!=commit
                or formal['frozen_hashes']!=hashes):
            raise ValueError('Reproduction requires the successful unchanged formal commit and hashes')
    output = parent/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-'+uuid4().hex[:8])
    marker = parent/(kind+'.reservation.json')
    # A failed attempt stays reserved and reported; no hidden budget retries.
    with marker.open('x',encoding='utf-8') as handle:
        json.dump({'kind':kind,'commit':commit,'frozen_hashes':hashes,
                   'output_directory':output.relative_to(ROOT).as_posix()},handle)
    output.mkdir()
    started = perf_counter()
    old_root = data.REPOSITORY_ROOT
    try:
        data.REPOSITORY_ROOT = output/'synthetic'
        data.REPOSITORY_ROOT.mkdir()
        timings, stress = [], []
        with threadpool_limits(limits=config['budget']['numeric_threads']):
            pools = threadpool_info()
            for case in stress_cases(config):
                row = evaluate_stress_case(*case,config)
                timings.append({'case':list(case),'seconds':row.pop('elapsed_seconds')})
                stress.append(row)
            kahan = evaluate_kahan(config)
            timings.append({'case':'kahan','seconds':kahan.pop('elapsed_seconds')})
            if (kahan['compensated_ulp']>kahan['ulp_limit']
                    or kahan['plain_to_compensated_ulp_ratio']<100):
                raise ValueError('Frozen Kahan acceptance failed')
            if not any(row['rejected'] for row in stress):
                raise ValueError('Missing condition rejection coverage')
            factory = noisy_minute_bars.__wrapped__()
            replay = [_compatibility_and_oracle(factory,h,output,config) for h in config['synthetic']['horizons']]
        _write(output/'numerical-stress.json',stress)
        _write(output/'kahan.json',kahan)
        _write(output/'replay-equivalence.json',replay)
        budget = {'stress_initial_solves':sum(r['initial_solves'] for r in stress),
                  'stress_statistics_updates':sum(r['statistics_updates'] for r in stress),
                  'stress_svd_oracle_fits':sum(r['batch_svd_fits'] for r in stress),
                  'stress_qr_row_updates':sum(r['qr_row_updates'] for r in stress),
                  'stress_qr_solves':sum(r['qr_solves'] for r in stress),
                  'stress_qr_solve_attempts':sum(r['qr_solve_attempts'] for r in stress),
                  'stress_cholesky_solve_attempts':sum(r['cholesky_solve_attempts'] for r in stress),
                  'kahan_scalar_calls':kahan['kahan_scalar_calls'],'kahan_initial_solves':1,
                  'replay':[r['budget'] for r in replay]}
        _write(output/'computation-budget.json',budget)
        deterministic = {p.relative_to(output).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in sorted(output.rglob('*')) if p.is_file()}
        if formal is not None and deterministic!=formal['deterministic_artifacts']:
            raise ValueError('Reproduction deterministic artifacts differ from formal run')
        if _clean_commit()!=commit:
            raise ValueError('Source commit changed during formal validation')
        elapsed = perf_counter()-started
        memory = peak_process_memory()
        if elapsed>config['budget']['formal_max_seconds'] or memory['peak_process_bytes']>config['budget']['formal_max_peak_bytes']:
            raise ValueError('Frozen formal runtime/memory budget exceeded')
        summary = {'status':'passed','kind':kind,'git_commit':commit,'git_dirty':False,
                   'frozen_hashes':hashes,'python_version':sys.version,
                   'dependency_versions':{name:importlib.metadata.version(name) for name in
                       ['numpy','pandas','scipy','scikit-learn','pyarrow','matplotlib','pytest','threadpoolctl']},
                   'evidence_level':'synthetic_engineering_only','holdout_accessed':False,
                   'market_rows_read':0,'market_network_requests':0,'historical_compatibility':'not enabled; default skip',
                   'deterministic_artifacts':deterministic,'elapsed_seconds':elapsed,
                   'process_memory':memory,'threadpools':pools,'timings':timings,
                   'output_directory':output.relative_to(ROOT).as_posix()}
        _write(output/'summary.json',summary)
        print(json.dumps(summary,indent=2))
        return summary
    except Exception as exc:
        _write(output/'failure.json',{'kind':kind,'error':str(exc),'type':type(exc).__name__,
                                      'elapsed_seconds':perf_counter()-started})
        raise
    finally:
        data.REPOSITORY_ROOT = old_root


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind',required=True,choices=['formal','reproduction'])
    run(parser.parse_args().kind)
