"""Synthetic formal-tool smoke and clean-source gates without real Git calls."""
from types import SimpleNamespace
import hashlib
import json

import pytest

from quantforge import data
from quantforge.online.hardening_config import load_hardening_config
from tools import phase2f_validate


def test_dirty_formal_source_is_rejected_with_mocked_git(monkeypatch):
    calls=[]
    def fake(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=' M src/synthetic.py\n')
    monkeypatch.setattr(phase2f_validate.subprocess,'run',fake)
    with pytest.raises(ValueError,match='clean committed'):
        phase2f_validate._clean_commit()
    assert len(calls)==1


def test_validation_tool_synthetic_checkpoint_oracle_integration(noisy_minute_bars,tmp_path,monkeypatch):
    monkeypatch.setattr(data,'REPOSITORY_ROOT',tmp_path)
    monkeypatch.setattr(phase2f_validate.subprocess,'run',lambda *a,**k:pytest.fail('Real process call in synthetic test'))
    config=load_hardening_config()
    result=phase2f_validate._compatibility_and_oracle(noisy_minute_bars,15,tmp_path,config)
    assert result['v1_v2_continuation_equal']
    assert result['stale_max_abs']>=config['synthetic']['stale_margin']
    assert result['budget']['per_event_svd_oracle_fits']==config['synthetic']['replay_events']
    assert result['budget']['recovery_branches']==2
    assert len(result['maturity_audits'])==config['synthetic']['replay_events']
    assert sum(r['load_validation_eigendecompositions'] for r in result['budget']['recovery_actual_counts'])==4
    assert result['budget']['prediction_calls_including_stale_control']==2*config['synthetic']['replay_events']


@pytest.mark.parametrize('mismatch',['status','commit','hash'])
def test_reproduction_refuses_failed_or_different_formal_run(tmp_path,monkeypatch,mismatch):
    original=phase2f_validate.ROOT
    hashes={}
    for name,relative in {'plan':'docs/phase2f-plan.md','config':'configs/c1-engine-hardening.toml',
                          'manifest':'docs/phase2f-plan-manifest.json'}.items():
        raw=(original/relative).read_bytes()
        destination=tmp_path/relative
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(raw)
        hashes[name]=hashlib.sha256(raw).hexdigest()
    parent=tmp_path/'.tmp/phase2f-runs'
    prior=parent/'synthetic-prior'
    prior.mkdir(parents=True)
    summary={'status':'passed','git_commit':'synthetic-commit','frozen_hashes':hashes}
    if mismatch=='status': summary['status']='failed'
    elif mismatch=='commit': summary['git_commit']='different-commit'
    else: summary['frozen_hashes']['config']='0'*64
    (prior/'summary.json').write_text(json.dumps(summary))
    (parent/'formal.reservation.json').write_text(json.dumps({'output_directory':prior.relative_to(tmp_path).as_posix()}))
    monkeypatch.setattr(phase2f_validate,'ROOT',tmp_path)
    monkeypatch.setattr(phase2f_validate,'_clean_commit',lambda:'synthetic-commit')
    monkeypatch.setattr(phase2f_validate.subprocess,'run',lambda *a,**k:pytest.fail('Real Git forbidden'))
    with pytest.raises(ValueError,match='successful unchanged formal'):
        phase2f_validate.run('reproduction')
    assert not (parent/'reproduction.reservation.json').exists()
