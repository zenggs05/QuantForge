"""Synthetic end-to-end pilot and frozen-runner failure boundaries."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from threadpoolctl import threadpool_limits

from quantforge import data
from quantforge.features import build_features
from quantforge.labels import build_labels, mature_labels
from quantforge.datasets.research_splits import make_research_splits
from quantforge.models.baselines import Baseline
from quantforge.research_experiment import _record_model
from quantforge.online import foundation


@pytest.fixture
def synthetic_pilot(tmp_path, monkeypatch, minute_bars):
    root=Path(__file__).resolve().parents[1]
    for name in ('configs/online-foundation.toml','docs/phase2d-c1-analysis-plan.md',
                 'docs/phase2d-c1-plan-manifest.json','configs/research-periods.toml','scripts/run_online_foundation.py'):
        dest=tmp_path/name; dest.parent.mkdir(parents=True,exist_ok=True); dest.write_bytes((root/name).read_bytes())
    monkeypatch.setattr(data,'REPOSITORY_ROOT',tmp_path)
    # Full artificial pilot day plus disconnected later months solely so the
    # unchanged four-month research split remains testable. No market data.
    bars=pd.concat([minute_bars(240,'2025-09-01T00:00:00Z'),
        minute_bars(1800,'2026-02-28T20:00:00Z'),minute_bars(240,'2026-04-01T00:00:00Z'),
        minute_bars(240,'2026-05-01T00:00:00Z'),minute_bars(240,'2026-06-01T00:00:00Z')],ignore_index=True)
    source={'overall_manifest_sha256':'a'*64,'synthetic':True}
    features=build_features(bars); models=[]
    with threadpool_limits(limits=1):
        for h in (15,5):
            labels=build_labels(bars,h).frame
            split=make_research_splits(features.metadata.join(labels.drop(columns='target_return')),h)[0]
            X=features.frame.loc[split.train_indices]; y=mature_labels(labels,split.fit_time,split.train_indices)
            model=Baseline('ridge').fit(X,y)
            models.append(_record_model(model,y,h,1,split.fit_time,'b'*64))
    monkeypatch.setattr(foundation,'_load_reference',lambda _: {'source':source,'models':models})
    monkeypatch.setattr(foundation,'load_research_dataset',lambda _: (bars,source))
    monkeypatch.setattr(foundation,'_provenance',lambda: {'git_commit':'c'*40,'git_dirty':False,'source_sha256':{},'dependency_versions':{}})
    monkeypatch.setattr(foundation,'_environment',lambda: {'synthetic':True,'gpu_used':False})
    return tmp_path


def test_full_synthetic_day_counts_equivalence_recovery_and_reproduction(synthetic_pilot):
    one=foundation.run_online_foundation()
    two=foundation.run_online_foundation(run_kind='reproduction')
    assert one['prediction_count']==one['update_count']==2880
    assert one['batch_checkpoint_count']==10 and one['restored_continuation_exact']
    assert one['source']['synthetic']
    assert all(r['passed'] for r in one['batch_equivalence'])
    assert one['batch_equivalence']==two['batch_equivalence']
    for r in one['budget']['horizons']:
        assert r['carry_in']==r['horizon_minutes']
        assert r['formal']['pending_count']==r['horizon_minutes']
        assert r['formal']['model']['solve_calls']==1441
        assert r['formal']['valid_update_count']==1440
        assert r['restored_continuation_exact']
        assert r['formal']['runtime']['label_row_reads']==1440
    first=synthetic_pilot/one['output_directory']; second=synthetic_pilot/two['output_directory']
    for name in one['deterministic_artifacts']:
        assert (first/name).read_bytes()==(second/name).read_bytes(), name
    assert not (first/'failure.json').exists()


@pytest.mark.parametrize('dirty',[True,None])
def test_dirty_pilot_rejected_before_input_reads(synthetic_pilot,monkeypatch,dirty):
    calls=[]
    monkeypatch.setattr(foundation,'_provenance',lambda:{'git_dirty':dirty,'git_commit':'x'})
    monkeypatch.setattr(foundation,'_load_reference',lambda _: calls.append('reference'))
    monkeypatch.setattr(foundation,'load_research_dataset',lambda _: calls.append('data'))
    with pytest.raises(ValueError,match='clean'): foundation.run_online_foundation()
    assert calls==[]
    assert len(list(synthetic_pilot.rglob('failure.json')))==1
    assert not list(synthetic_pilot.rglob('summary.json'))


def test_config_and_holdout_path_rejected_before_open(synthetic_pilot,monkeypatch):
    calls=[]
    monkeypatch.setattr(Path,'read_bytes',lambda *args: calls.append(1))
    with pytest.raises(ValueError): foundation.load_foundation_config('data/2026-07.parquet')
    assert not calls


def test_config_hash_refuses_parameter_search(synthetic_pilot):
    path=synthetic_pilot/'configs/online-foundation.toml'
    path.write_text(path.read_text().replace('ridge_alpha = 1.0','ridge_alpha = 2.0'))
    with pytest.raises(ValueError,match='configuration hash'): foundation.run_online_foundation()
    assert list(synthetic_pilot.rglob('failure.json'))


def test_failed_batch_tolerance_never_writes_success(synthetic_pilot,monkeypatch):
    original=foundation._batch_check
    def bad(*args):
        row,timing=original(*args);row['passed']=False;return row,timing
    monkeypatch.setattr(foundation,'_batch_check',bad)
    with pytest.raises(ValueError,match='tolerance'): foundation.run_online_foundation()
    assert list(synthetic_pilot.rglob('failure.json')) and not list(synthetic_pilot.rglob('summary.json'))
    assert list(synthetic_pilot.rglob('batch-equivalence.csv'))


def test_frozen_tolerance_reports_relative_and_absolute_errors():
    exact=foundation.equivalence_metrics([1.],2.,[3.],[1.],2.,[3.])
    assert exact['passed'] and exact['prediction_relative_l2_error']==0
    wrong=foundation.equivalence_metrics([1.],2.,[3.1],[1.],2.,[3.])
    assert not wrong['passed'] and wrong['prediction_max_abs_error']==pytest.approx(.1)
    with pytest.raises(ValueError): foundation.equivalence_metrics([np.nan],2.,[3.],[1.],2.,[3.])


def test_peak_process_memory_uses_os_measurement():
    record=foundation.peak_process_memory()
    assert record['peak_process_bytes']>0 and 'process lifetime' in record['method']
