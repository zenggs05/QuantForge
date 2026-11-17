"""Synthetic integration of the shared full workflow and strict entry points."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from threadpoolctl import threadpool_limits

from quantforge import phase3a_gate2 as g2, phase3a_runner as runner
from quantforge.phase3a_synthetic import synthetic_history
from test_phase3a_gate2_core import no_real_access, _history_config, _execution_contract, _bars


def test_formal_reproduction_synthetic_inputs_are_byte_identical(tmp_path, monkeypatch):
    config, contract = _history_config(), _execution_contract()
    config["synthetic"]["minutes_per_month"] = 384
    contract["gates"]["n_train_min"] = 1
    bars = synthetic_history(config).drop(columns="close_time")
    prepared = g2.prepare_fold_audits(bars, config, contract, pd.DatetimeIndex([], tz="UTC"))
    g2.EVIDENCE_DIR.mkdir()
    g2.write_json(g2.EVIDENCE_DIR/"prefit-audit.json", prepared.audit)
    monkeypatch.setattr(g2, "runtime_fields", lambda mode, output: dict(mode=output.name, output_directory=str(output)))
    counts = dict(fits=0, CW=0, DM=0, IC=0)
    original_fit = runner.fit_predict_fold
    def fit(*args, **kwargs):
        counts["fits"] += 1
        return original_fit(*args, **kwargs)
    monkeypatch.setattr(runner, "fit_predict_fold", fit)
    for name, key in (("clark_west_differential","CW"),("dm_differential","DM"),("ic_product","IC")):
        original = getattr(runner.ft, name)
        def counted(*args, _fn=original, _key=key, **kwargs):
            counts[_key] += 1
            return _fn(*args, **kwargs)
        monkeypatch.setattr(runner.ft, name, counted)
    outputs = [tmp_path/"formal", tmp_path/"reproduction"]
    summaries = []
    for output in outputs:
        output.mkdir()
        with threadpool_limits(limits=1):
            summaries.append(runner._execute_synthetic_inner(config, output, mode="real", prepared=prepared))
    assert counts == dict(fits=560, CW=1120, DM=1008, IC=1008)
    assert summaries[0] == summaries[1]
    assert summaries[0]["main_ridge_fits"] == 224
    assert summaries[0]["sensitivity_ridge_fits"] == 56
    for first in outputs[0].iterdir():
        if first.name == "runtime.json":
            continue
        assert first.read_bytes() == (outputs[1]/first.name).read_bytes(), first.name
        if first.suffix in (".csv", ".json") and first.name != "download-plan.json":
            content = first.read_text(encoding="utf-8")
            assert "synthetic" not in content and "not_yet_downloaded" not in content
    audit = json.loads((outputs[0]/"fold-audit.json").read_text())
    assert len(audit) == 112 and audit == prepared.audit["folds"]
    dq = json.loads((outputs[0]/"sensitivity-fold-audit.json").read_text())
    assert len(dq) == 56 and dq == prepared.sensitivity["folds"]
    monthly = pd.read_csv(outputs[0]/"monthly-tests.csv")
    assert len(monthly) == 4032 and "pooling" not in monthly
    assert len(pd.read_csv(outputs[0]/"sensitivity-dq-monthly.csv")) == 56


def _script(name):
    path = Path(__file__).resolve().parents[1]/"scripts"/(name+".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name,args,entry", [
    ("run_phase3a_gate2",["--mode","formal"],"run_real"),
    ("run_phase3a_gate2",["--mode","reproduction"],"run_real"),
    ("phase3a_gate2_prefit_audit",["--contract","configs/phase3a-gate2-execution.toml"],"run_real"),
    ("phase3a_gate2_rehearsal",[],"run_rehearsal")])
def test_cli_console_ascii_even_with_unicode_result(name, args, entry, capsys, monkeypatch):
    module = _script(name)
    monkeypatch.setattr(module, entry, lambda *a, **k: {"status":"\u6838\u9a8c", "fits":0})
    module.main(args)
    captured = capsys.readouterr().out
    assert captured.isascii()
    assert json.loads(captured)["fits"] == 0


@pytest.mark.parametrize("args", [["--mode","debug"], ["--mode","formal","--month","2021-01"],
                                 ["--mode","formal","--skip"], ["--mode","formal","--mod","formal"]])
def test_cli_rejects_unapproved_switches(args):
    with pytest.raises(SystemExit) as exc:
        _script("run_phase3a_gate2").main(args)
    assert exc.value.code == 2


def test_prepared_rehearsal_adjusts_structural_sets_by_dstar(tmp_path):
    from quantforge import phase3a_gate2_oracle as oracle
    config, contract = _history_config(), _execution_contract()
    config["months"]["warm_up"] = ["2020-12"]
    config["months"]["evaluate"] = ["2021-01"]
    contract["gates"]["n_train_min"] = 1
    times = pd.date_range("2020-12-30", periods=7500, freq="min", tz="UTC").delete(slice(7000,7015))
    bars = _bars(times)
    bars.loc[2900, ["open","high","low"]] = bars.loc[2900, "close"]
    state = oracle.validity(bars)
    table = []
    for h in (15,5):
        tr, ev, audit = oracle.fold_sets(state, "2021-01", h, structural=True)
        table.append(dict(month="2021-01", horizon=h, **{k:audit[k] for k in
                           ("candidate_eval_samples","candidate_train_samples","eval_invalid_features","eval_invalid_labels",
                            "eval_labels_after_cutoff","eval_samples","train_invalid_features","train_invalid_labels",
                            "train_immature_labels","train_purged_overlap","train_target_after_fit","train_samples")},
                          eval_segment_lengths=[len(s) for s in oracle.segments(ev)],
                          llsw_lag=oracle.lag(len(ev),config), train_ids_sha256=oracle.index_sha(tr), eval_ids_sha256=oracle.index_sha(ev)))
    # The compact synthetic contract has no source conflicts or short S-DQ segments.
    contract["sensitivity"]["expected_structural"] = dict(masked_valid_bars_all_months=0, masked_eval_bars=0,
        eval_months_affected=0, short_segments_dropped=0)
    prepared = g2.prepare_fold_audits(bars, config, contract, pd.DatetimeIndex([], tz="UTC"), structural=table)
    assert all(row["D_star_eval"] == 1 for row in prepared.audit["folds"])
    assert len(prepared.audit["undefined_rows"]) == 1
    assert prepared.audit["gates"]["PF-1"]


def test_rehearsal_generator_units_gaps_and_zero_placeholder(tmp_path):
    from quantforge.phase3a_gate2_rehearsal import make_rehearsal_bars
    config, contract = _history_config(), _execution_contract()
    config["months"]["warm_up"], config["months"]["evaluate"] = ["2020-12"], ["2021-01"]
    contract["close_time"]["anomalies"] = [a for a in contract["close_time"]["anomalies"] if a["open"][:7] == "2020-12"]
    manifest = {"months":[{"month":"2020-12","gap_ranges":[]},
                          {"month":"2021-01","gap_ranges":[{"start_utc":"2021-01-03T00:00:00Z",
                                                          "end_utc":"2021-01-03T00:14:00Z"}]}]}
    bars = make_rehearsal_bars(config, manifest=manifest, contract=contract)
    assert len(bars) == 2*31*1440-15
    audit = g2.audit_close_times(bars, contract)
    assert audit["anomaly_rows"] == 1
    assert audit["anomalies"][0]["kind"] == "zero_trade_placeholder"
