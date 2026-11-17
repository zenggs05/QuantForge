"""Synthetic D-059 scope, transport, audit, inference and reproduction tests."""
import builtins
import copy
import hashlib
import http.client
import importlib.util
import io
import json
from pathlib import Path
import socket
import ssl
import time
import tomllib
import urllib.error
import urllib.request
import zipfile

import numpy as np
import pandas as pd
import pytest
from threadpoolctl import threadpool_limits

from quantforge import holdout_a as ha
from quantforge import data_monthly
from quantforge.power_reanalysis import monthly_stat
from quantforge.diagnostics.forecast_tests import pool_roles
from quantforge.diagnostics.hac_tests import holm_adjust

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Redirect every I/O root and fail any real data or network access."""
    monkeypatch.delenv("QF_HOLDOUT_A_AUTH", raising=False)
    for name, path in {"ROOT": tmp_path, "RAW": tmp_path / "data", "EVIDENCE": tmp_path / "evidence",
                       "OUTPUT": tmp_path / "outputs", "LAUNCH": tmp_path / ".tmp/ha-real-data-launch-ledger.jsonl",
                       "DEV_ROOT": tmp_path / "development"}.items():
        monkeypatch.setattr(ha, name, path)
    def forbidden(*args, **kwargs):
        pytest.fail("real network attempt in synthetic test")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(ha, "_open_response", forbidden)
    original_open = builtins.open
    original_io_open = io.open
    roots = [str(REPO / "data").lower(), "d:/zgs_project/quantforge/", "d:/zgs_project/quantforge-2f/"]
    def check(path):
        if isinstance(path, (str, Path)):
            name = str(Path(path).absolute()).replace("\\", "/").lower()
            assert not any(name.startswith(r.replace("\\", "/")) for r in roots), "real data opened"
    def checked_open(path, *args, **kwargs):
        check(path)
        return original_open(path, *args, **kwargs)
    def checked_io_open(path, *args, **kwargs):
        check(path)
        return original_io_open(path, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", checked_open)
    monkeypatch.setattr(io, "open", checked_io_open)


@pytest.fixture
def config():
    return tomllib.loads((REPO / "configs/history-extension.toml").read_text(encoding="utf-8"))


@pytest.fixture
def contract():
    return tomllib.loads((REPO / "configs/holdout-a-execution.toml").read_text(encoding="utf-8"))


@pytest.fixture
def synthetic_auth(monkeypatch):
    # Only this new module's gate is replaced; the authorization env is never set.
    monkeypatch.setattr(ha, "require_auth", lambda: None)


def bars(times):
    rng = np.random.default_rng(42)
    n = len(times)
    price = 100 * np.exp(np.cumsum(rng.normal(0, .0002, n)))
    volume = rng.uniform(1, 20, n)
    return pd.DataFrame(dict(open_time=times, open=price, high=price * 1.001, low=price * .999,
        close=price * 1.0001, volume=volume, number_of_trades=rng.integers(5, 100, n),
        quote_asset_volume=volume * price, taker_buy_base_asset_volume=volume / 2,
        taker_buy_quote_asset_volume=volume * price / 2))


def csv_payload(month, count=None):
    start = pd.Timestamp(month + "-01", tz="UTC")
    end = start + pd.offsets.MonthBegin(1)
    n = int((end - start) / pd.Timedelta(minutes=1)) if count is None else count
    first = start.value // 1000
    return ("\n".join(f"{first+i*60000000},100,102,99,101,10,{first+(i+1)*60000000-1},1005,20,4,402,0" for i in range(n)) + "\n").encode()


def archive(payload, month="2026-07", member=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member or f"BTCUSDT-1m-{month}.csv", payload)
    return stream.getvalue()


class Response(io.BytesIO):
    def __init__(self, payload, url, status=200, length="auto"):
        super().__init__(payload)
        self.url, self.status = url, status
        self.headers = {} if length is None else {"Content-Length": str(len(payload)) if length == "auto" else length}

    def geturl(self):
        return self.url

    def getcode(self):
        return self.status


def synthetic_record(contract, payload=b"synthetic ZIP bytes"):
    c = copy.deepcopy(contract)
    for row in c["download"]["recorded"]:
        row["zip_content_length"] = len(payload)
        row["checksum_text"] = hashlib.sha256(payload).hexdigest() + "  BTCUSDT-1m-" + row["month"] + ".zip"
    return c


@pytest.mark.parametrize("action", [lambda c: ha.require_auth(), lambda c: ha.download(c, time.perf_counter()),
    lambda c: ha.load_bars(c), lambda c: ha.parse_archive(b"never parse", "2026-07"), lambda c: ha.run("formal")])
def test_authorization_absent_rejects_before_io(contract, action):
    with pytest.raises(ha.HoldoutError, match="authorization"):
        action(contract)


def test_wrong_authorization_and_pytest_gate(monkeypatch):
    # Intercept lookup, never create the forbidden variable in pytest's environment.
    original = ha.os.environ.get
    monkeypatch.setattr(ha.os.environ, "get", lambda key, default=None: "wrong" if key == "QF_HOLDOUT_A_AUTH" else original(key, default))
    with pytest.raises(ha.HoldoutError, match="authorization"):
        ha.require_auth()
    monkeypatch.setattr(ha.os.environ, "get", lambda key, default=None: ha.AUTH if key == "QF_HOLDOUT_A_AUTH" else original(key, default))
    with pytest.raises(ha.HoldoutError, match="pytest"):
        ha.require_auth()


@pytest.mark.parametrize("month", ["2026-09", "2026-09-01", "2026-10", "2025-09", "2026-06", "2026-07/../09"])
def test_month_boundary_closed(month):
    with pytest.raises(ha.HoldoutError):
        ha.guard_month(month)


@pytest.mark.parametrize("stamp", ["2026-09-01", "2026-09-02", "2026-10-01", "2025-08-31"])
def test_sealed_timestamps(stamp):
    with pytest.raises(ha.HoldoutError):
        ha.guard_times(pd.DatetimeIndex([stamp], tz="UTC"))


def test_exact_four_urls(contract):
    urls = contract["download"]["urls"]
    assert len(urls) == len(set(urls)) == 4
    assert [ha.url_info(u, contract) for u in urls] == [(m, k) for m in ha.MONTHS for k in ("checksum", "zip")]
    for url in [urls[0] + "?x=1", urls[0].replace("2026-07", "2026-09"), urls[0].replace("monthly", "daily")]:
        with pytest.raises(ha.HoldoutError):
            ha.url_info(url, contract)


@pytest.mark.parametrize("length", [None, "bad", "0", "-1", "1000"])
def test_bad_content_length(length):
    with pytest.raises(ha.HoldoutError):
        ha.read_body(Response(b"x", "test", length=length), 100, 100)


def test_remaining_budget_and_recorded_length():
    with pytest.raises(ha.HoldoutError, match="budget"):
        ha.read_body(Response(b"abc", "test"), 100, 2)
    with pytest.raises(ha.HoldoutError, match="recorded"):
        ha.read_body(Response(b"abc", "test"), 100, 100, 4)


@pytest.mark.parametrize("payload,length,error", [(b"abc", "2", ha.HoldoutError), (b"a", "2", http.client.IncompleteRead)])
def test_overlong_and_short_read(payload, length, error):
    with pytest.raises(error) as exc:
        ha.read_body(Response(payload, "test", length=length), 100, 100)
    assert exc.value.payload == payload


@pytest.mark.parametrize("exc,retry", [(TimeoutError(), True), (ConnectionResetError(), True),
    (ConnectionAbortedError(), True), (http.client.RemoteDisconnected(), True),
    (http.client.IncompleteRead(b"x"), True), (ssl.SSLEOFError(), True), (ssl.SSLZeroReturnError(), True),
    (socket.gaierror(), True), (PermissionError(), False), (ssl.SSLCertVerificationError(), False),
    (RuntimeError("unknown"), False)])
def test_error_classification(exc, retry):
    assert ha.classify_network_error(exc) == ("retry" if retry else "stop")


@pytest.mark.parametrize("status", [301, 302, 307, 403, 404])
def test_status_stop_without_retry(status, contract, synthetic_auth):
    url = contract["download"]["urls"][0]
    calls = []
    def opener(req, timeout):
        calls.append(req.full_url)
        return Response(b"x", req.full_url, status)
    with pytest.raises(ha.HoldoutError, match="status"):
        ha.fetch(url, contract, time.perf_counter(), opener=opener)
    assert len(calls) == 1
    assert ha.read_events(ha.EVIDENCE / "network-ledger.jsonl")[-1]["result"] == "stop"


def test_redirect_final_url(contract, synthetic_auth):
    with pytest.raises(ha.HoldoutError, match="final URL"):
        ha.fetch(contract["download"]["urls"][0], contract, time.perf_counter(),
                 opener=lambda req, timeout: Response(b"x", "https://other.invalid"))


@pytest.mark.parametrize("status", [429, 500, 503])
def test_http_retry_exhaustion(status, contract, synthetic_auth):
    calls = []
    def opener(req, timeout):
        calls.append(1)
        return Response(b"x", req.full_url, status)
    with pytest.raises(urllib.error.HTTPError):
        ha.fetch(contract["download"]["urls"][0], contract, time.perf_counter(), opener=opener)
    assert len(calls) == 3
    assert ha.network_state(contract)["gets"] == 3
    assert len(list((ha.RAW / ".partial").iterdir())) == 3


def test_timeout_then_success(contract, synthetic_auth):
    calls = []
    def opener(req, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("test timeout")
        return Response(contract["download"]["recorded"][0]["checksum_text"].encode(), req.full_url)
    result = ha.fetch(contract["download"]["urls"][0], contract, time.perf_counter(), opener=opener)
    assert result["bytes"] == 88 and len(calls) == 2
    assert not list((ha.RAW / ".partial").iterdir())


@pytest.mark.parametrize("failure", ["text", "zip_sha", "zip_length"])
def test_recorded_integrity_failures(failure, contract, synthetic_auth):
    payload = b"synthetic ZIP bytes"
    c = synthetic_record(contract, payload)
    checksum = c["download"]["recorded"][0]["checksum_text"].encode()
    if failure == "text":
        url, content = c["download"]["urls"][0], checksum + b"\n"
    else:
        ha.write_bytes(ha.RAW / "BTCUSDT-1m-2026-07.zip.CHECKSUM", checksum)
        url = c["download"]["urls"][1]
        content = payload.replace(b"s", b"t", 1) if failure == "zip_sha" else payload + b"x"
    with pytest.raises(ha.HoldoutError):
        ha.fetch(url, c, time.perf_counter(), opener=lambda req, timeout: Response(content, url))
    assert not (ha.RAW / url.rsplit("/", 1)[1]).exists() if failure == "text" else not (ha.RAW / "BTCUSDT-1m-2026-07.zip").exists()


def test_download_order_and_counts(contract, synthetic_auth, monkeypatch):
    payload = b"synthetic ZIP bytes"
    c = synthetic_record(contract, payload)
    calls = []
    def opener(req, timeout):
        calls.append(req.full_url)
        month, kind = ha.url_info(req.full_url, c)
        rec = next(x for x in c["download"]["recorded"] if x["month"] == month)
        return Response(rec["checksum_text"].encode() if kind == "checksum" else payload, req.full_url)
    monkeypatch.setattr(ha, "_open_response", opener)
    assert ha.download(c, time.perf_counter())["gets"] == 4
    assert calls == c["download"]["urls"]
    summary = json.loads((ha.EVIDENCE / "download-summary.json").read_bytes())
    assert summary["counters"]["used"] == {"zip": 2 * len(payload), "checksum": 176}
    assert len(summary["files"]) == 4


@pytest.mark.parametrize("gets,attempts,used", [(12, 0, 0), (0, 3, 0), (0, 0, 16777216)])
def test_budget_before_intent(gets, attempts, used, contract, synthetic_auth, monkeypatch):
    url = contract["download"]["urls"][1]
    monkeypatch.setattr(ha, "network_state", lambda c:dict(gets=gets, attempts={url:attempts}, used={"zip":used,"checksum":0}))
    with pytest.raises(ha.HoldoutError, match="budget"):
        ha.fetch(url, contract, time.perf_counter())
    assert not (ha.EVIDENCE / "network-ledger.jsonl").exists()


def test_unsettled_intent_stops(contract):
    ha.append(ha.EVIDENCE / "network-ledger.jsonl", dict(event="intent", id="H0001", url=contract["download"]["urls"][0]))
    with pytest.raises(ha.HoldoutError, match="unsettled"):
        ha.network_state(contract)


def test_exclusive_files_and_move(tmp_path):
    source, target = ha.RAW / ".partial/x", ha.RAW / "x"
    ha.write_bytes(source, b"x")
    ha.move_new(source, target)
    assert target.read_bytes() == b"x" and not source.exists()
    with pytest.raises(ha.PersistenceError):
        ha.write_bytes(target, b"y")
    ha.write_bytes(source, b"z")
    with pytest.raises(ha.HoldoutError, match="already exists"):
        ha.move_new(source, target)
    assert source.read_bytes() == b"z" and target.read_bytes() == b"x"


def test_move_permission_failure_once(monkeypatch):
    source = ha.RAW / ".partial/x"
    ha.write_bytes(source, b"x")
    calls = []
    def refuse(*args):
        calls.append(1)
        raise PermissionError("synthetic refusal")
    monkeypatch.setattr(ha.os, "replace", refuse)
    with pytest.raises(ha.PersistenceError, match="synthetic refusal"):
        ha.move_new(source, ha.RAW / "x")
    assert calls == [1] and source.exists()


def test_parser_exact_development_equivalence(tmp_path):
    payload = csv_payload("2025-12")
    legacy, _ = data_monthly.parse_monthly_klines(payload, "2025-12")
    actual = ha.decode_csv(payload)
    pd.testing.assert_frame_equal(actual, legacy, check_exact=True)


@pytest.mark.parametrize("month", ["2026-07", "2026-08"])
def test_holdout_parser_member_boundary_and_schema(month, synthetic_auth):
    payload = csv_payload(month)
    frame = ha.parse_archive(archive(payload, month), month)
    assert len(frame) == 44640 and frame.open_time.iloc[-1].strftime("%Y-%m") == month
    assert ha.audit_month(frame, month)["timestamp_unit"] == "us"
    with pytest.raises(ha.HoldoutError, match="ZIP member"):
        ha.parse_archive(archive(payload, month, "other.csv"), month)
    other = "2026-08" if month == "2026-07" else "2026-07"
    with pytest.raises(ha.HoldoutError, match="outside month"):
        ha.parse_archive(archive(csv_payload(other, 10), month), month)


@pytest.mark.parametrize("kind", ["truncated", "placeholder", "late", "duplicate", "units", "sealed"])
def test_close_and_calendar_audit(kind):
    frame = ha.decode_csv(csv_payload("2026-07", 100))
    if kind in ("truncated", "placeholder", "late"):
        offset = 60_000_000 if kind == "late" else 20_000_000
        frame.loc[0, "close_time_raw"] = frame.loc[0, "open_time_raw"] + offset
        frame.loc[0, "close_time"] = frame.loc[0, "open_time"] + pd.Timedelta(microseconds=offset)
        if kind == "placeholder":
            frame.loc[0, ["volume", "number_of_trades"]] = 0
            frame.loc[0, ["open", "high", "low", "close"]] = 100
    elif kind == "duplicate":
        frame.loc[1, "open_time"] = frame.loc[0, "open_time"]
    elif kind == "units":
        frame["open_time_raw"] //= 1000
    else:
        frame.loc[0, "open_time"] = ha.END
    if kind in ("truncated", "placeholder"):
        audit = ha.audit_month(frame, "2026-07")
        assert audit["close_time_anomalies"][0]["kind"] == ("truncated_with_trades" if kind == "truncated" else "zero_trade_placeholder")
    else:
        with pytest.raises(ha.HoldoutError):
            ha.audit_month(frame, "2026-07")


@pytest.mark.parametrize("h,length,removed", [(15,20,True),(15,400,False),(5,20,False)])
def test_short_segment_rule(config, h, length, removed):
    first = pd.date_range("2026-07-01", periods=length, freq="min", tz="UTC")
    second = pd.date_range("2026-07-03", periods=600, freq="min", tz="UTC")
    result, dropped = ha.prune_eval(first.append(second), h, config)
    assert bool(dropped) == removed
    assert len(result) == 600 if removed else len(result) == length + 600
    assert ha.bandwidth(len(result), config) == max(60, int(np.ceil(1.3 * np.sqrt(len(result)))))


def prepared_synthetic(config, contract):
    times = pd.date_range("2026-06-28", "2026-08-03T23:59:00", freq="min", tz="UTC")
    # A real calendar gap and an undefined ratio; no real market file is read.
    times = times.delete(slice(15000, 15020))
    frame = bars(times)
    frame.loc[10000, ["open", "high", "low"]] = frame.loc[10000, "close"]
    c = copy.deepcopy(contract)
    c["gates"]["n_train_min"] = 1
    return ha.prepare(frame, config, c, dict(input_sha256={"synthetic":"fixture"})), c


def test_oracle_and_training_maturity(config, contract):
    prepared, _ = prepared_synthetic(config, contract)
    features, labels, folds, audit = prepared
    assert len(audit["folds"]) == 4 and all(audit["gates"].values())
    assert sum(x["D_star_eval"] for x in audit["folds"]) == 2
    for h in (15,5):
        for fold in folds[h]:
            assert labels[h].frame.loc[fold.train_indices, "label_available_time"].le(fold.fit_time).all()
            assert labels[h].frame.loc[fold.eval_indices, "label_available_time"].le(fold.eval_end).all()
    for row in audit["folds"]:
        assert row["raw_eval_samples"] == row["structural"]["eval_samples"] - row["D_star_eval"]
        assert row["train_samples"] == row["structural"]["train_samples"] - row["D_star_train"]


def test_six_fits_reproduction_and_direct_statistics(config, contract, tmp_path, monkeypatch):
    prepared, c = prepared_synthetic(config, contract)
    original = ha.runner.fit_predict_fold
    calls = []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(ha.runner, "fit_predict_fold", counted)
    paths = [ha.OUTPUT / "formal", ha.OUTPUT / "reproduction"]
    with threadpool_limits(limits=1):
        for path in paths:
            assert ha.execute(prepared, config, path, c, time.perf_counter())["ridge_fits"] == 6
    assert len(calls) == 12
    for path in paths[0].iterdir():
        assert path.read_bytes() == (paths[1] / path.name).read_bytes()
    ha.compare_outputs(*paths)
    assert json.loads((paths[1] / "determinism-check.json").read_bytes())["all_byte_identical"]
    predictions = pd.read_parquet(paths[0] / "predictions.parquet")
    assert predictions.row_sha256.str.fullmatch("[0-9a-f]{64}").all()
    monthly = []
    for month in ha.MONTHS:
        subset = predictions[(predictions.spec == "v1") & (predictions.horizon == 15) & (predictions.decision_time.dt.strftime("%Y-%m") == month)]
        monthly.append(monthly_stat(2 * subset.prediction.to_numpy() * subset.target.to_numpy(),
                      pd.DatetimeIndex(subset.decision_time), ha.bandwidth(len(subset), config), "greater"))
    direct = pool_roles(monthly, pooling="n_weighted", alternative="greater")
    primary = json.loads((paths[0] / "primary.json").read_bytes())["pooled"]
    for field in ("mean", "variance", "se", "t", "p_value"):
        assert primary[field] == pytest.approx(direct[field], rel=1e-12, abs=0)
    secondary = pd.read_csv(paths[0] / "secondary.csv")
    np.testing.assert_allclose(secondary.holm_p, holm_adjust(secondary.p_value.tolist()), rtol=1e-14)
    assert len(secondary) == 5 and len(json.loads((paths[0] / "models.json").read_bytes())) == 6
    decomposition = json.loads((paths[0] / "descriptive-drift.json").read_bytes())
    selected = predictions[(predictions.spec == "v1") & (predictions.horizon == 15)]
    assert decomposition["pooled"]["twice_mean_f_mean_y"] == pytest.approx(2 * selected.prediction.mean() * selected.target.mean(), rel=1e-13)


def test_cli_ascii_and_only_fixed_modes(monkeypatch, capsys):
    path = REPO / "scripts/run_holdout_a.py"
    spec = importlib.util.spec_from_file_location("holdout_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "run", lambda mode: {"status":"\u5b8c\u6210"})
    module.main(["audit"])
    assert capsys.readouterr().out.isascii()
    with pytest.raises(SystemExit):
        module.main(["formal", "--skip"])


@pytest.mark.parametrize("error", [PermissionError("synthetic permission"), RuntimeError("synthetic unknown")])
def test_transport_error_not_retried(error, contract, synthetic_auth):
    calls = []
    def opener(req, timeout):
        calls.append(1)
        raise error
    with pytest.raises(type(error), match="synthetic"):
        ha.fetch(contract["download"]["urls"][0], contract, time.perf_counter(), opener=opener)
    assert calls == [1]
    events = ha.read_events(ha.EVIDENCE / "network-ledger.jsonl")
    assert len(events) == (1 if isinstance(error, PermissionError) else 2)


@pytest.mark.parametrize("count,mode", [(4,"download"),(0,"audit"),(1,"reproduction")])
def test_launch_budget_and_order(count, mode, contract, config, synthetic_auth, monkeypatch):
    monkeypatch.setattr(ha, "load_contract", lambda: (contract, config))
    events = []
    for i in range(count):
        events += [dict(event="start", launch=i+1), dict(event="end", launch=i+1, exit_code=0)]
    monkeypatch.setattr(ha, "read_events", lambda path: events)
    with pytest.raises(ha.HoldoutError, match="limit/order"):
        ha.run(mode)
    assert not ha.LAUNCH.exists()


def test_write_failure_produces_no_fallback(contract, config, synthetic_auth, monkeypatch):
    monkeypatch.setattr(ha, "load_contract", lambda: (contract, config))
    def git(*args):
        if args[0] == "status":
            return ""
        if args[0] == "log":
            return "feat: holdout-a confirmatory runner (synthetic tests only)"
        return "synthetic-head"
    monkeypatch.setattr(ha, "git", git)
    def fail(*args):
        raise ha.PersistenceError("synthetic write failure")
    monkeypatch.setattr(ha, "download", fail)
    with pytest.raises(ha.PersistenceError, match="synthetic write failure"):
        ha.run("download")
    assert len(ha.read_events(ha.LAUNCH)) == 1
    assert not ha.EVIDENCE.exists()


def test_runtime_resource_gates(contract, monkeypatch):
    c = copy.deepcopy(contract)
    c["gates"]["run_seconds_max"] = 0
    with pytest.raises(ha.HoldoutError, match="wall time"):
        ha.resources(c, time.perf_counter() - 1)
    monkeypatch.setattr(ha.runner, "peak_process_bytes", lambda: contract["gates"]["peak_process_bytes_max"] + 1)
    with pytest.raises(ha.HoldoutError, match="memory"):
        ha.resources(contract, time.perf_counter())


def test_constant_ic_null_keeps_holm_slot(config):
    cfg = ha.execution_config(config)
    times = pd.date_range("2026-07-01", periods=500, freq="min", tz="UTC")
    rows = []
    for spec, h in (("v1",15),("v2",15),("v1",5)):
        for month in ha.MONTHS:
            t = times + (pd.Timestamp(month + "-01", tz="UTC") - times[0])
            for baseline in ("zero", "historical_mean"):
                key = dict(month=month, specification=spec, horizon_minutes=h, baseline=baseline,
                           specification_evidence_level="synthetic")
                rows += ha.runner._monthly(np.linspace(-.01,.01,500), np.ones(500)*.001, np.zeros(500), t, key, cfg, mode="real")
    secondary = ha.runner._secondary(ha.runner._pool(rows,cfg),cfg)
    ic = secondary[-1]
    assert ic["test_id"] == "S5" and ic["p_value"] is None and ic["holm_p"] == 1
    assert ic["reason"]


def test_both_h15_specs_share_pruned_index(config, contract):
    times = pd.date_range("2026-06-28", "2026-08-03T23:59:00", freq="min", tz="UTC")
    frame = bars(times)
    point = frame.index[frame.open_time.eq(pd.Timestamp("2026-07-01T00:20:00Z"))][0]
    frame.loc[point, ["open", "high", "low"]] = frame.loc[point, "close"]
    c = copy.deepcopy(contract)
    c["gates"]["n_train_min"] = 1
    features, labels, folds, audit = ha.prepare(frame, config, c, dict(input_sha256={}))
    h15 = next(x for x in audit["folds"] if x["month"] == "2026-07" and x["horizon_minutes"] == 15)
    h5 = next(x for x in audit["folds"] if x["month"] == "2026-07" and x["horizon_minutes"] == 5)
    assert len(h15["removed_segments"]) == 1 and h15["removed_segments"][0]["length"] == 21
    assert h5["removed_segments"] == []
    assert len(folds[15][0].train_indices) == h15["train_samples"]
    assert index_equal(folds[15][0].eval_indices, h15["eval_ids_sha256"])


def index_equal(index, expected):
    return ha.index_sha(index) == expected


@pytest.mark.parametrize("tamper", [False, True])
def test_assembly_twelve_months_and_sha_chain(contract, synthetic_auth, monkeypatch, tamper):
    c = copy.deepcopy(contract)
    dev = c["development"]
    entries = []
    for month in dev["months"]:
        frame = ha.decode_csv(csv_payload(month, 10))
        path = ha.DEV_ROOT / dev["parquet_dir"] / f"BTCUSDT-1m-{month}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False)
        entries.append(dict(month=month, rows=10, parquet_sha256=ha.digest(path)))
    dev["rows"] = 100
    manifest = ha.DEV_ROOT / dev["manifest"]
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_bytes(ha.json_bytes(dict(expected_rows=100, validation=dict(month_sources=entries))))
    dev["manifest_sha256"] = ha.digest(manifest)
    files = []
    for month in ha.MONTHS:
        payload = archive(csv_payload(month, 10), month)
        checksum = hashlib.sha256(payload).hexdigest() + f"  BTCUSDT-1m-{month}.zip"
        rec = next(x for x in c["download"]["recorded"] if x["month"] == month)
        rec.update(checksum_text=checksum, zip_content_length=len(payload))
        for suffix, content in ((".CHECKSUM", checksum.encode()), ("", payload)):
            path = ha.RAW / (f"BTCUSDT-1m-{month}.zip" + suffix)
            ha.write_bytes(path, content)
            files.append(dict(path=str(path.relative_to(ha.ROOT)), bytes=len(content), sha256=ha.digest(path)))
    if tamper:
        files[-1]["sha256"] = "0" * 64
    ha.write_json(ha.EVIDENCE / "download-summary.json", dict(status="passed", contract_sha256=ha.CONTRACT_SHA, files=files))
    original_digest = ha.digest
    monkeypatch.setattr(ha, "digest", lambda p: "synthetic-code" if Path(p).is_relative_to(ha.ROOT / "src") or Path(p).is_relative_to(ha.ROOT / "scripts") else original_digest(p))
    if tamper:
        with pytest.raises(ha.HoldoutError, match="SHA or size"):
            ha.load_bars(c)
    else:
        frame, audit = ha.load_bars(c)
        assert len(frame) == 120 and len(audit["months"]) == 12
        assert list(frame.columns) == c["data"]["keep_columns"]
        assert len(audit["input_sha256"]) == 15
        assert audit["development_rows"] == 100
