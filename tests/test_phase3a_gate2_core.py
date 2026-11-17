"""Synthetic, result-blind Gate 2 adapter checks."""
import pandas as pd
import numpy as np
import pytest
import builtins
import io
import socket
from pathlib import Path
import tomllib

from quantforge import phase3a_gate2 as g2

from quantforge.phase3a_gate2 import (
    Gate2Error, RealDataAccessError, audit_close_times, build_sensitivity_indices,
    interpretation, monthly_stat, project_keep_columns,
)
from quantforge.phase3a_gate2_oracle import conflict_mask, index_sha


@pytest.fixture(autouse=True)
def no_real_access(monkeypatch, tmp_path):
    """Redirect all adapter roots and reject accidental market-row/network IO."""
    root = g2.REPOSITORY_ROOT
    protected = [(root/"data").resolve(),
                 (root.parent/"QuantForge-verification").resolve()]
    original_builtin, original_io = builtins.open, io.open
    def guarded(original):
        def call(file, *args, **kwargs):
            if isinstance(file, (str, Path)):
                candidate = Path(file).resolve()
                permitted_metadata = candidate == (root/"data/processed/binance/spot/BTCUSDT/1m/historical/historical-manifest.json").resolve()
                assert permitted_metadata or not any(candidate.is_relative_to(p) for p in protected), str(candidate)
            return original(file, *args, **kwargs)
        return call
    monkeypatch.setattr(builtins, "open", guarded(original_builtin))
    monkeypatch.setattr(io, "open", guarded(original_io))
    def network(*args, **kwargs):
        pytest.fail("A test attempted a real network request")
    monkeypatch.setattr(socket.socket, "connect", network)
    monkeypatch.setattr(socket, "create_connection", network)
    for name in ("REPOSITORY_ROOT", "CONTRACT_PATH", "MANIFEST_PATH", "PARQUET_ROOT",
                 "EVIDENCE_DIR", "RESULTS_ROOT", "LAUNCH_LEDGER"):
        monkeypatch.setattr(g2, name, tmp_path/name)
    monkeypatch.delenv("QF_GATE2_REAL_DATA", raising=False)


def _contract(anomalies=()):
    return {"data": {"keep_columns": ["open_time", "open", "high", "low", "close", "volume", "quote_asset_volume", "number_of_trades", "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume"]},
            "close_time": {"anomalies": list(anomalies)}}


def test_c1_close_audit_accepts_contract_anomaly_and_projects_columns():
    anomaly = {"open": "2024-12-31T23:59:00Z", "close": "2024-12-31T23:59:32.286Z", "kind": "truncated_with_trades"}
    opened = pd.Timestamp(anomaly["open"], tz="UTC")
    row = {"open_time": opened, "close_time": pd.Timestamp(anomaly["close"], tz="UTC"), "open": 100., "high": 101., "low": 99., "close": 100., "volume": 1., "number_of_trades": 2., "quote_asset_volume": 100., "taker_buy_base_asset_volume": .5, "taker_buy_quote_asset_volume": 50., "ignored": 3}
    result = audit_close_times(pd.DataFrame([row]), _contract([anomaly]))
    assert result["anomaly_rows"] == 1
    projected = project_keep_columns(pd.DataFrame([row]), _contract([anomaly]))
    assert "ignored" not in projected


def test_c1_close_audit_rejects_boundary_violation():
    anomaly = {"open": "2024-12-31T23:59:00Z", "close": "2024-12-31T23:59:32.286Z", "kind": "truncated_with_trades"}
    opened = pd.Timestamp(anomaly["open"], tz="UTC")
    row = {"open_time": opened, "close_time": opened + pd.Timedelta(seconds=60), "open": 1., "high": 1., "low": 1., "close": 1., "volume": 1., "number_of_trades": 1.}
    with pytest.raises(Gate2Error):
        audit_close_times(pd.DataFrame([row]), _contract([anomaly]))


def test_mask_window_inclusive_boundaries():
    origin = pd.Timestamp("2021-01-01", tz="UTC")
    index = pd.date_range(origin - pd.Timedelta(minutes=61), periods=139, freq="min", tz="UTC")
    conflict = [origin]
    mask = conflict_mask(index, conflict)
    assert mask[index.get_loc(origin + pd.Timedelta(minutes=60))]
    assert mask[index.get_loc(origin - pd.Timedelta(minutes=15))]
    assert not mask[index.get_loc(origin + pd.Timedelta(minutes=61))]
    assert not mask[index.get_loc(origin - pd.Timedelta(minutes=16))]


def test_sensitivity_pf6_drops_short_segment_and_recomputes_lag():
    config = {"inference": {"llsw_min_lag": 60, "llsw_constant": 1.3}}
    index = pd.date_range("2021-01-01", periods=1000, freq="min", tz="UTC")
    result = build_sensitivity_indices(index, [index[30]], config=config)
    assert result["train_masked"] == 76
    assert len(result["evaluation_pre_pf6"]) == 924
    assert len(result["evaluation"]) == 909
    assert result["removed_segments"][0]["length"] == 15
    assert result["lag"] == 60


def test_segment_hac_keeps_segments_and_fixed_schema():
    first = pd.date_range("2021-01-01", periods=20, freq="min", tz="UTC")
    second = pd.date_range("2021-01-01 00:30", periods=20, freq="min", tz="UTC")
    out = monthly_stat(np.arange(40, dtype=float), first.append(second), 3)
    assert out["segment_count"] == 2
    assert out["segment_rule"].startswith("independent_continuous_segments")
    assert "month_count" not in out
    assert "pooling" not in out


def test_interpretation_never_changes_primary_decision():
    out = interpretation({"p_value": .01}, {"p_value": .1, "mean": 1.}, [{"ci_low": -.1}], {"month_equal": {"t": 2., "mean": 1.}, "n_weighted": {"mean": 1.}}, {"reject": False})
    assert out["classification"] == "R-B"
    assert out["changes_primary_decision"] is False


def test_real_loader_requires_explicit_process_gate(monkeypatch):
    monkeypatch.delenv("QF_GATE2_REAL_DATA", raising=False)
    from quantforge.phase3a_gate2 import load_historical_bars
    with pytest.raises(RealDataAccessError):
        load_historical_bars()


def test_index_sha_is_deterministic():
    idx = pd.date_range("2021-01-01", periods=3, freq="min", tz="UTC")
    assert index_sha(idx) == index_sha(pd.DatetimeIndex(idx))


def _bars(times):
    """Finite irregular values with all denominators positive."""
    rng = np.random.default_rng(74)
    n = len(times)
    close = 100*np.exp(np.cumsum(rng.normal(0, .0001, n)))
    volume = rng.lognormal(3, .2, n)
    return pd.DataFrame(dict(open_time=times, open=close, close=close,
                             high=close*1.001, low=close*.999, volume=volume,
                             number_of_trades=rng.integers(10, 100, n),
                             quote_asset_volume=volume*close,
                             taker_buy_base_asset_volume=volume*.5,
                             taker_buy_quote_asset_volume=volume*close*.5))


def _history_config():
    path = Path(__file__).resolve().parents[1]/"configs/history-extension.toml"
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _execution_contract():
    path = Path(__file__).resolve().parents[1]/"configs/phase3a-gate2-execution.toml"
    return tomllib.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("horizon", [5, 15])
def test_independent_oracle_matches_every_frozen_counter_and_sha(horizon, tmp_path):
    from quantforge import phase3a_gate2_oracle as oracle
    from quantforge.features import build_features
    from quantforge.labels import build_labels
    from quantforge.datasets.historical_splits import make_historical_splits
    times = pd.date_range("2020-12-30", "2021-01-04", freq="min", inclusive="left", tz="UTC")
    times = times.delete(slice(4500, 4517))
    bars = _bars(times)
    bars.loc[3000, ["volume", "taker_buy_base_asset_volume", "quote_asset_volume",
                    "taker_buy_quote_asset_volume"]] = 0.
    bars.loc[3100, ["open", "high", "low"]] = bars.loc[3100, "close"]
    f, lab = build_features(bars), build_labels(bars, horizon)
    fold = make_historical_splits(f.metadata.join(lab.frame.drop(columns="target_return")), ("2021-01",), horizon)[0]
    state = oracle.validity(bars)
    tr, ev, audit = oracle.fold_sets(state, "2021-01", horizon)
    assert audit == fold.audit
    assert tr.equals(fold.train_indices) and ev.equals(fold.eval_indices)
    np.testing.assert_array_equal(state["valid"], f.metadata.valid_features)
    assert g2.index_sha(tr) == oracle.index_sha(fold.train_indices)
    assert len(state["reasons"]) == 3


@pytest.mark.parametrize("horizon,short,removed", [(15, 20, True), (15, 400, False), (5, 20, False)])
def test_d057_pruning_preserves_training_and_v1_v2_indices(horizon, short, removed, tmp_path):
    from quantforge import phase3a_gate2_oracle as oracle
    from quantforge.features import build_features
    from quantforge.labels import build_labels
    from quantforge.datasets.historical_splits import make_historical_splits
    times = pd.date_range("2020-12-31", periods=5000, freq="min", tz="UTC")
    bars = _bars(times)
    invalid = 1439 + short
    bars.loc[invalid, ["open", "high", "low"]] = bars.loc[invalid, "close"]
    f, lab = build_features(bars), build_labels(bars, horizon)
    fold = make_historical_splits(f.metadata.join(lab.frame.drop(columns="target_return")), ("2021-01",), horizon)[0]
    config = _history_config()
    final, excluded = g2.prune(fold.eval_indices, config, "D057_undefined_feature_short_segment") if horizon == 15 else (fold.eval_indices, [])
    row, _ = oracle.audit_fold(oracle.validity(bars), "2021-01", horizon, config, [])
    assert bool(excluded) is removed
    assert g2.index_sha(final) == row["eval_ids_sha256"]
    assert g2.index_sha(fold.train_indices) == row["train_ids_sha256"]
    assert all((t + pd.Timedelta(seconds=61)).year == 2021 for t in final)
    if excluded:
        assert excluded[0]["length"] == short
    assert g2.prune(final, config, "test")[0].equals(final) if horizon == 15 else True


@pytest.mark.parametrize("n,split,lag", [(200, False, 60), (400, True, 60), (20, False, 60), (60, True, 60)])
def test_c2_matches_frozen_kernel_and_legacy_variances(n, split, lag, tmp_path):
    from quantforge.power_reanalysis import monthly_stat as frozen
    from quantforge.diagnostics.hac_tests import segment_hac_variance
    rng = np.random.default_rng(91)
    values = rng.normal(size=n)
    times = pd.date_range("2021-01-01", periods=n, freq="min", tz="UTC")
    if split:
        times = times[:n//2].append(times[n//2:] + pd.Timedelta(minutes=3))
    expected = frozen(values, times, lag, "greater")
    actual = g2.segment_monthly_stat(values, times, lag, "greater")
    for key in ("mean", "variance", "se", "t", "p_value", "n_eff"):
        assert actual[key] == expected[key]
    pieces = np.split(values, [n//2]) if split else [values]
    variances = [segment_hac_variance(p, lag) for p in pieces]
    if any(v is None for v in variances):
        assert actual["variance"] is None and actual["reason"]
    else:
        expected_var = sum((len(p)/n)**2 * v for p, v in zip(pieces, variances))
        assert actual["variance"] == pytest.approx(expected_var, rel=1e-12)
    null = g2.segment_monthly_stat(None, times, lag, "greater")
    assert actual.keys() == null.keys()
    assert not {"pooling", "month_count", "weights"} & actual.keys()
    assert null["reason"] == "constant_ic_input"


@pytest.mark.parametrize("p,fr1,fr2,fr3,classification", [
    (.01, False, False, False, "R-A"), (.01, True, False, False, "R-B"),
    (.01, False, True, False, "R-B"), (.01, False, False, True, "R-A"),
    (.5, False, False, False, "R-C"), (None, True, True, True, "R-C")])
@pytest.mark.parametrize("dq", [.01, .5, None])
def test_interpretation_table_including_undefined(p, fr1, fr2, fr3, classification, dq):
    result = interpretation({"p_value":p}, {"p_value":.5 if fr1 else .01, "mean":1},
                            [{"ci_low":None if fr2 else .1}],
                            {"month_equal":{"t":None if fr3 else 2., "mean":1}, "n_weighted":{"mean":1}},
                            {"p_value":dq})
    assert result["classification"] == classification
    assert (result["FR1"], result["FR2"], result["FR3"]) == (fr1, fr2, fr3)
    assert result["primary_reject"] == (p is not None and p < .05)
    assert result["changes_primary_decision"] is False
    assert result["sensitivity"] == ("data_source_sensitive" if dq is None or
           (dq < .05) != result["primary_reject"] else "data_source_robust")


@pytest.mark.parametrize("when", ["2019-12-31", "2025-09-01", "2026-07-01", "2026-09-01", "2026-10-01"])
def test_timestamp_and_path_guards(when):
    with pytest.raises((Gate2Error, ValueError)):
        g2.guard_times(pd.date_range(when, periods=2, freq="min", tz="UTC"))
    with pytest.raises((Gate2Error, ValueError)):
        g2._month_path(when[:7])


def test_all_eight_close_anomalies_raw_units_and_projection(tmp_path):
    contract = _execution_contract()
    times = pd.DatetimeIndex([pd.Timestamp(a["open"]) for a in contract["close_time"]["anomalies"]])
    bars = _bars(times)
    bars["close_time"] = pd.to_datetime([a["close"] for a in contract["close_time"]["anomalies"]], utc=True)
    for i, a in enumerate(contract["close_time"]["anomalies"]):
        if a["kind"] == "zero_trade_placeholder":
            bars.loc[i, ["volume", "number_of_trades", "taker_buy_base_asset_volume",
                         "quote_asset_volume", "taker_buy_quote_asset_volume"]] = 0
            bars.loc[i, ["open", "high", "low"]] = bars.loc[i, "close"]
    bars["open_time_raw"] = times.as_unit("ns").asi8//1_000_000
    bars["close_time_raw"] = pd.DatetimeIndex(bars.close_time).as_unit("ns").asi8//1_000_000
    audit = g2.audit_close_times(bars, contract, require_raw=True)
    assert [a["kind"] for a in audit["anomalies"]] == [a["kind"] for a in contract["close_time"]["anomalies"]]
    assert sum(a["close_before_open"] for a in audit["anomalies"]) == 1
    projected = g2.project_keep_columns(bars, contract)
    pd.testing.assert_frame_equal(projected, bars[contract["data"]["keep_columns"]])
    bars.loc[0, "close_time_raw"] += 1
    with pytest.raises(Gate2Error, match="Raw timestamp"):
        g2.audit_close_times(bars, contract, require_raw=True)


@pytest.mark.parametrize("date,offset", [("2024-12-31",59999000), ("2025-01-01",59999999)])
def test_normal_close_ms_us_boundary(date, offset):
    bars = _bars(pd.date_range(date, periods=2, freq="min", tz="UTC"))
    bars["close_time"] = bars.open_time + pd.Timedelta(microseconds=offset)
    assert g2.audit_close_times(bars, _contract())["normal_rows"] == 2


def test_minimum_training_and_pf6_fail_closed():
    with pytest.raises(Gate2Error, match="n_train"):
        g2.assert_n_train([dict(month="2021-01", train_samples=49999)])
    with pytest.raises(Gate2Error, match="PF-6"):
        g2.assert_pf6([dict(month="2021-01", horizon_minutes=15, eval_segment_lengths=[60],
                           llsw_lag=60)], {})


def test_placebo_label_retains_original_call_label():
    c = _execution_contract()
    rows = [dict(evidence_level=c["evidence_labels"]["placebo_call_label"], value=3)]
    result = g2.relabel_placebo(rows, c)
    assert result[0]["evidence_level"] == c["evidence_labels"]["placebo"]
    assert result[0]["placebo_call_label"] == c["evidence_labels"]["placebo_call_label"]


@pytest.mark.parametrize("bad_hash", [False, True])
def test_loader_reads_only_hashed_synthetic_parquet_and_keeps_all_values(tmp_path, monkeypatch, bad_hash):
    root = tmp_path/"isolated"
    root.mkdir()
    parquet_root = root/"parquet"
    parquet_root.mkdir()
    monkeypatch.setattr(g2, "REPOSITORY_ROOT", root)
    monkeypatch.setattr(g2, "PARQUET_ROOT", parquet_root)
    monkeypatch.setattr(g2, "_require_real_gate", lambda: None)
    bars = _bars(pd.date_range("2020-01-01", periods=2, freq="min", tz="UTC"))
    bars["close_time"] = bars.open_time+pd.Timedelta(milliseconds=59999)
    bars["open_time_raw"] = pd.DatetimeIndex(bars.open_time).as_unit("ns").asi8//1_000_000
    bars["close_time_raw"] = pd.DatetimeIndex(bars.close_time).as_unit("ns").asi8//1_000_000
    bars["ignore"] = 0
    path = parquet_root/"2020-01.parquet"
    bars.to_parquet(path, index=False)
    contract = _contract()
    contract["data"].update(rows=2, start="2020-01-01T00:00:00Z", end_exclusive="2020-01-01T00:02:00Z",
                            gap_ranges=0, missing_minutes=0)
    manifest = dict(months=[dict(month="2020-01", rows=2, gap_ranges=[],
                                parquet_sha256="0"*64 if bad_hash else g2._hash(path))])
    monkeypatch.setattr(g2, "verify_contract_inputs", lambda path: ({}, contract, manifest, {}))
    if bad_hash:
        with pytest.raises(Gate2Error, match="parquet SHA"):
            g2.load_historical_bars()
    else:
        actual, audit = g2.load_historical_bars()
        pd.testing.assert_frame_equal(actual, bars[contract["data"]["keep_columns"]])
        assert audit["close_time"]["rows"] == 2
    assert "QF_GATE2_REAL_DATA" not in __import__("os").environ


def test_real_process_ledger_requires_exact_three_stages(tmp_path, monkeypatch):
    monkeypatch.setattr(g2, "_require_real_gate", lambda: None)
    monkeypatch.setattr(g2, "git", lambda *args: "unchanged-head")
    for stage in ("prefit", "formal", "reproduction"):
        event = g2.launch_begin(stage)
        g2.launch_end(event, 0)
    with pytest.raises(Gate2Error, match="count or order"):
        g2.launch_begin("formal")
    import json
    events = [json.loads(line) for line in g2.LAUNCH_LEDGER.read_text().splitlines()]
    assert len(events) == 6 and [r["event"] for r in events] == ["start","end"]*3


def test_incomplete_real_process_is_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(g2, "_require_real_gate", lambda: None)
    monkeypatch.setattr(g2, "git", lambda *args: "head")
    g2.launch_begin("prefit")
    with pytest.raises(Gate2Error, match="did not finish"):
        g2.launch_begin("prefit")


def test_exclusive_evidence_writer_keeps_first_bytes(tmp_path):
    path = tmp_path/"evidence.json"
    g2.write_json(path, {"value":1})
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        g2.write_json(path, {"value":2})
    assert path.read_bytes() == original
