"""Full frozen synthetic workflow and independent aggregation contracts."""
import hashlib
import json
import socket

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from quantforge.features import build_features
from quantforge.labels import build_labels
from quantforge.phase3a_config import load_history_config
from quantforge.phase3a_synthetic import synthetic_history
from quantforge import phase3a_runner as runner


def test_full_56_month_workflow_is_byte_identical_and_retains_all_endpoints(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("Synthetic workflow attempted a network connection")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    config = load_history_config()
    summaries = []
    outputs = [tmp_path / "first", tmp_path / "reproduction"]
    for directory in outputs:
        directory.mkdir()
        summaries.append(runner._execute_synthetic(config, directory))
    first, second = summaries
    assert first == second
    names = {p.name for p in outputs[0].iterdir()}
    assert names == {p.name for p in outputs[1].iterdir()}
    for name in names - {"runtime.json"}:
        assert (outputs[0] / name).read_bytes() == (outputs[1] / name).read_bytes(), name
    for name, digest in first["artifacts_sha256"].items():
        assert hashlib.sha256((outputs[0] / name).read_bytes()).hexdigest() == digest
    assert first["ridge_fits"] == 224
    assert first["folds"] == 112
    assert first["synthetic_rows"] == 278528
    assert first["months"] == config["months"]["evaluate"]
    assert first["primary"]["month_count"] == 56
    assert first["primary"]["alternative"] == "greater"
    assert [row["test_id"] for row in first["secondary"]] == ["S1", "S2", "S3", "S4", "S5"]
    assert first["secondary"][0]["specification_evidence_level"] == config["specification"]["v2_evidence_tag"]
    monthly = pd.read_csv(outputs[0] / "monthly-tests.csv")
    pooled = pd.read_csv(outputs[0] / "pooled-tests.csv")
    assert len(monthly) == 56 * 2 * 2 * 2 * 3 * 3
    assert len(pooled) == 2 * 2 * 2 * 3 * 3 * 2
    assert set(monthly.bandwidth) == {"60", "275", "LLSW"}
    assert set(pooled.pooling) == {"n_weighted", "month_equal"}
    audit = json.loads((outputs[0] / "fold-audit.json").read_text())
    assert len({row["role_bucket"] for row in audit}) == 10
    assert "2025H2" in {row["role_bucket"] for row in audit}
    with np.load(outputs[0] / "training-indices.npz") as saved:
        assert len(saved.files) == 112
        for row in audit:
            ids = pd.DatetimeIndex(saved[f"h{row['horizon_minutes']}_{row['month']}"], tz="UTC")
            assert len(ids) == row["train_samples"]
            assert (ids < pd.Timestamp(row["fit_time"])).all()
    predictions = pq.read_table(outputs[0] / "predictions.parquet").to_pandas()
    assert len(predictions) == sum(row["eval_samples"] for row in audit) * 2
    assert (predictions.fit_time <= predictions.decision_time).all()
    assert predictions.model_version.nunique() == 224
    assert predictions.groupby(["month", "horizon_minutes", "specification"]).ngroups == 224
    instrument = pd.read_csv(outputs[0] / "gw-instrument.csv")
    assert len(instrument) == 56
    assert set(instrument.coverage_scope) == {"partial_episode_realized_volatility"}
    assert (pd.to_datetime(instrument.max_label_available_time) <= pd.to_datetime(instrument.fit_time)).all()
    gw = json.loads((outputs[0] / "conditional-gw.json").read_text())
    assert gw["evidence_level"] == "exploratory_not_in_holm"
    assert gw["n"] == 56
    fluctuation = pd.read_csv(outputs[0] / "stability.csv")
    assert len(fluctuation) == 45
    assert not any("p_value" in col for col in fluctuation)
    np.testing.assert_array_equal(fluctuation.absolute_mean, fluctuation.signed_mean.abs())
    dependence = json.loads((outputs[0] / "dependence.json").read_text())
    assert [r["block_length"] for r in dependence["S_dep2"]] == [3, 6, 12]
    assert all(r["draws"] == 2000 for r in dependence["S_dep2"])
    placebo = pd.read_csv(outputs[0] / "placebo.csv")
    assert len(placebo) == 6
    assert placebo.loc[placebo.placebo == "P1_sign_flip", "mc_interval_high"].notna().all()
    shifted = placebo.loc[placebo.placebo == "P2_time_shift"]
    assert (shifted.n_samples == shifted.shifted_n_samples + shifted.excluded_n_samples).all()
    assert len(pd.read_csv(outputs[0] / "power-planning.csv")) == 12
    assert first["network_requests"] == 0 and first["market_rows_read"] == 0
    for directory in outputs:
        runtime = json.loads((directory / "runtime.json").read_text())
        assert runtime["elapsed_seconds"] <= config["budget"]["run_seconds"]
        assert runtime["peak_process_bytes"] <= config["budget"]["process_memory_bytes"]


def test_synthetic_generator_reproduces_all_months_and_resets_at_gaps():
    config = load_history_config()
    first = synthetic_history(config)
    pd.testing.assert_frame_equal(first, synthetic_history(config), check_exact=True)
    assert len(first) == 278528
    assert first.open_time.is_unique
    assert first.groupby(first.open_time.dt.strftime("%Y-%m")).size().eq(4096).all()
    assert (first.high >= first[["open", "close"]].max(axis=1)).all()
    assert (first.low <= first[["open", "close"]].min(axis=1)).all()
    features = build_features(first)
    labels = build_labels(first, 15)
    for _, group in features.metadata.groupby(features.metadata.index.strftime("%Y-%m")):
        assert not group.valid_features.iloc[:60].any()
        assert group.valid_features.iloc[60:].all()
    for _, group in labels.frame.groupby(labels.frame.index.strftime("%Y-%m")):
        assert not group.valid_label.iloc[-15:].any()
        assert group.valid_label.iloc[:-15].all()


def test_pooling_uses_weight_squared_variances_and_sum_sse_r2():
    config = load_history_config()
    config["months"]["evaluate"] = ["2021-01", "2021-02"]
    common = dict(specification="v1", horizon_minutes=15, baseline="zero", metric="CW",
                  bandwidth="LLSW", alternative="greater", gamma0=1.0,
                  specification_evidence_level="design_frozen_v1")
    rows = [dict(common, month="2021-01", n=10, mean=1., variance=4.,
                 model_mse=2., baseline_mse=4., model_mae=1., baseline_mae=2.),
            dict(common, month="2021-02", n=30, mean=3., variance=9.,
                 model_mse=1., baseline_mse=2., model_mae=.5, baseline_mae=1.)]
    weighted, equal = runner._pool(rows, config)
    assert weighted["mean"] == 2.5
    assert weighted["variance"] == .25**2*4 + .75**2*9
    assert equal["mean"] == 2
    assert equal["variance"] == .5**2*(4+9)
    assert weighted["r2_oos"] == equal["r2_oos"] == 1-(10*2+30*1)/(10*4+30*2)


def test_secondary_undefined_member_keeps_its_holm_slot():
    config = load_history_config()
    endpoints = [("v2",15,"zero","CW"), ("v1",5,"zero","CW"),
                 ("v1",15,"historical_mean","CW"), ("v1",15,"zero","DM"), ("v1",15,"zero","IC")]
    rows = [dict(zip(("specification","horizon_minutes","baseline","metric"), endpoint),
                 bandwidth="LLSW", pooling="n_weighted", p_value=p)
            for endpoint,p in zip(endpoints,[.01,None,.04,.2,.3])]
    result = runner._secondary(rows,config)
    assert [r["holm_p"] for r in result] == pytest.approx([.05,1.,.16,.6,.6])
    assert result[1]["p_value"] is None
    assert not result[1]["holm_reject"]


def test_placebo_precision_uses_exceedances_and_retains_boundary_uncertainty():
    rows = [dict(placebo="P1_sign_flip", draws=100, empirical_one_sided_p=21/101),
            dict(placebo="P1_sign_flip", draws=100, empirical_one_sided_p=1/101),
            dict(placebo="P2_time_shift", n_samples=5000, shifted_n_samples=3560)]
    result = runner._placebo_audit(rows, .05)
    assert result[0]["mc_exceedance_count"] == 20
    assert result[0]["mc_standard_error"] == pytest.approx(.04)
    assert result[0]["mc_interval_low"] == pytest.approx(.1333669333310325)
    assert result[0]["mc_interval_high"] == pytest.approx(.2888291655931589)
    assert result[1]["mc_exceedance_count"] == 0
    assert result[1]["mc_interval_high"] > 0
    assert result[2]["excluded_n_samples"] == 1440
