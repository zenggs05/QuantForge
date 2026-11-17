"""Synthetic frozen-prediction integration; no market files, Git or network."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tomllib

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from quantforge import data
from quantforge import power_reanalysis as pr
from quantforge.diagnostics import spec_audit


def config_copy():
    return tomllib.loads((Path(__file__).resolve().parents[1]/pr.CONFIG_PATH).read_text(encoding="utf-8"))


def synthetic_wide(config,n=320):
    rng=np.random.default_rng(400)
    rows=[]
    for h in config["experiment"]["horizons"]:
        for i,month in enumerate(config["inference"]["months"]):
            y=rng.normal(0,.001,n); f=.1*y+rng.normal(0,.0001,n)
            g=pd.DataFrame(dict(decision_time=pd.date_range(month+"-01T00:00:01Z",periods=n,freq="min"),month=month,
                                role=config["inference"]["roles"][i//2],horizon_minutes=h,target_return=y))
            for name,pred in {"ridge":f,"zero":np.zeros(n),"historical_mean":np.full(n,.00001),"expanding":f,
                              "rolling_1m":f+rng.normal(0,.00002,n),"rolling_3m":f+rng.normal(0,.00001,n),"rolling_6m":f if i==0 else f+rng.normal(0,.000005,n)}.items():
                g[f"prediction_{name}"]=pred
                g[f"squared_loss_{name}"]=(y-pred)**2
                g[f"absolute_loss_{name}"]=abs(y-pred)
            rows.append(g)
    return pd.concat(rows,ignore_index=True).sort_values(["horizon_minutes","month","decision_time"]).reset_index(drop=True)


@pytest.fixture
def synthetic_config():
    c=config_copy()
    c["placebo"]["time_shift_minutes"]=60
    c["placebo"]["sign_flip_draws"]=8
    return c


def test_prediction_reanalysis_has_no_fit_and_keeps_all_evidence_layers(synthetic_config,monkeypatch):
    def forbidden(*a,**kw):raise AssertionError("fit prohibited in frozen-prediction reanalysis")
    monkeypatch.setattr(Ridge,"fit",forbidden);monkeypatch.setattr(StandardScaler,"fit",forbidden)
    wide=synthetic_wide(synthetic_config)
    with threadpool_limits(limits=1):
        tables,identity=pr.analyze_predictions(wide,wide,synthetic_config,evidence_level="synthetic")
    assert identity["passed"] and identity["evidence_level"]=="synthetic"
    assert len(tables["evidence-layers"])==8
    assert len(tables["legacy-hac-2c"])==96
    assert len(tables["legacy-hac-2da"])==144
    assert len(tables["monthly-tests"])==64
    assert len(tables["role-tests"])==64
    assert len(tables["window-tests"])==96
    for table in tables.values():
        assert table.evidence_level.eq("synthetic").all()
    layer=tables["evidence-layers"]
    assert layer.tradability.eq("not evaluated").all()
    assert layer.confirmatory_evidence.eq("none").all()
    assert layer.cw_holm_family_size.eq(8).all()
    for bandwidth in ["15","60","240","LLSW"]:
        r=tables["window-tests"]
        assert r.loc[(r.bandwidth==bandwidth)&r.dm_holm_p.notna()].shape[0]==6
    assert tables["monthly-tests"].loc[lambda x:x.bandwidth=="15","admissible"].eq(False).all()


def test_all_runtime_parameters_pass_through_config(synthetic_config):
    c=synthetic_config
    assert pr.lag_for("LLSW",100,c)==13
    c["inference"]["llsw_constant"]=2
    assert pr.lag_for("LLSW",100,c)==20
    assert pr.bandwidths(c)==[15,60,240,"LLSW"]


@pytest.fixture
def synthetic_runner(tmp_path,monkeypatch,synthetic_config):
    config=synthetic_config;wide=synthetic_wide(config)
    monkeypatch.setattr(data,"REPOSITORY_ROOT",tmp_path)
    monkeypatch.setattr(pr,"load_config",lambda path:(deepcopy(config),{"files":{"plan":"synthetic"}}))
    monkeypatch.setattr(pr,"_provenance",lambda:{"git_dirty":False,"git_commit":"synthetic"})
    script=tmp_path/"scripts/run_power_reanalysis.py";script.parent.mkdir();script.write_text("# synthetic CLI\n",encoding="utf-8")
    monkeypatch.setattr(pr,"load_inputs",lambda c:(wide.copy(),wide.copy(),{"evidence_level":"synthetic"}))
    original=pr.analyze_predictions
    monkeypatch.setattr(pr,"analyze_predictions",lambda p,w,c:original(p,w,c,evidence_level="synthetic"))
    monkeypatch.setattr(pr,"validate_anchors",lambda *a:{"evidence_level":"synthetic","status":"passed","checks":[]})
    monkeypatch.setattr(pr,"validate_spec_anchors",lambda *a:None)
    monkeypatch.setattr(spec_audit,"run_specification_audit",lambda c:{"evidence_level":"synthetic","horizons":[],"model_fit_count":0})
    import subprocess
    monkeypatch.setattr(subprocess,"run",lambda *a,**k:pytest.fail("Real Git/process call prohibited"))
    return tmp_path,config


def test_synthetic_runner_reproduction_and_summary_last(synthetic_runner,monkeypatch):
    root,_=synthetic_runner
    writes=[];original=pr.write_json
    def record(path,value):
        writes.append(path.name)
        return original(path,value)
    monkeypatch.setattr(pr,"write_json",record)
    a=pr.run_power_reanalysis();assert writes[-1]=="summary.json"
    b=pr.run_power_reanalysis(run_kind="reproduction");assert writes[-1]=="summary.json"
    assert a["deterministic_artifacts"]==b["deterministic_artifacts"]
    for name in a["deterministic_artifacts"]:
        assert (root/a["output_directory"]/name).read_bytes()==(root/b["output_directory"]/name).read_bytes()
    assert a["new_evaluation_models"]==0 and not a["holdout_accessed"]
    assert not list(root.rglob("failure.json"))


def test_failure_writes_no_success(synthetic_runner,monkeypatch):
    root,_=synthetic_runner
    monkeypatch.setattr(pr,"analyze_predictions",lambda *a:(_ for _ in ()).throw(ValueError("deliberate synthetic failure")))
    with pytest.raises(ValueError,match="deliberate"):
        pr.run_power_reanalysis()
    assert len(list(root.rglob("failure.json")))==1
    assert not list(root.rglob("summary.json"))


def test_dirty_run_rejected_before_inputs(synthetic_runner,monkeypatch):
    root,_=synthetic_runner
    monkeypatch.setattr(pr,"_provenance",lambda:{"git_dirty":True})
    monkeypatch.setattr(pr,"load_inputs",lambda *a:pytest.fail("input read before clean gate"))
    with pytest.raises(ValueError,match="clean"):pr.run_power_reanalysis()
    assert list(root.rglob("failure.json"))


def test_input_hash_rejected_before_pairing_or_parsing(tmp_path,monkeypatch):
    monkeypatch.setattr(data,"REPOSITORY_ROOT",tmp_path)
    p=tmp_path/"frozen.bin";p.write_bytes(b"synthetic")
    config={"inputs":{"fake_path":"frozen.bin","fake_sha256":"0"*64}}
    monkeypatch.setattr(pr,"load_paired_losses",lambda **k:pytest.fail("parse before hash gate"))
    with pytest.raises(ValueError,match="hash mismatch"):pr.load_inputs(config)


def test_frozen_config_mutation_rejected_before_inputs(tmp_path,monkeypatch):
    root=Path(__file__).resolve().parents[1]
    for name in [pr.MANIFEST_PATH,pr.CONFIG_PATH,"docs/phase2e-analysis-plan.md","docs/confirmatory-protocol-v1.md"]:
        dest=tmp_path/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((root/name).read_bytes())
    monkeypatch.setattr(data,"REPOSITORY_ROOT",tmp_path)
    p=tmp_path/pr.CONFIG_PATH;p.write_bytes(p.read_bytes()+b"\n# synthetic corruption\n")
    with pytest.raises(ValueError,match="hash mismatch"):pr.load_config()


def test_real_config_is_frozen_and_paths_cannot_escape():
    config,manifest=pr.load_config()
    assert config==manifest["config_contract"]
    with pytest.raises(ValueError):pr.load_config("../outside.toml")


def test_historical_anchor_mismatch_is_a_hard_error(synthetic_config):
    row=dict(horizon_minutes=15,role="validation",baseline="zero",bandwidth="60",pooling="n_weighted",relative_mse_delta_pct=99,dm_t=-1,cw_t=1,ic_mean=0)
    dev={**row,"role":"development_evaluation"}
    with pytest.raises(ValueError,match="A1_relative"):
        pr.validate_anchors({"role-tests":pd.DataFrame([row,dev])},None,synthetic_config)


def test_new_hac_path_is_independently_anchored_to_legacy(synthetic_config):
    wide=synthetic_wide(synthetic_config)
    with threadpool_limits(limits=1):
        tables,_=pr.analyze_predictions(wide,wide,synthetic_config,evidence_level="synthetic")
    for name in ("legacy-hac-2c","legacy-hac-2da"):
        pr.compare_new_dm_to_legacy(tables,name,tables[name],synthetic_config["anchors"]["a4_a7_rtol"])
    tables["role-tests"].loc[0,"dm_t"]+=1
    with pytest.raises(ValueError,match="historical HAC mismatch"):
        pr.compare_new_dm_to_legacy(tables,"legacy-hac-2c",tables["legacy-hac-2c"],synthetic_config["anchors"]["a4_a7_rtol"])
