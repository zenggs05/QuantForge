"""D-059 bounded acquisition and frozen Holdout-A confirmatory execution."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import http.client
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
import zipfile

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from quantforge import data, phase3a_runner as runner
from quantforge.archive_verification import _open_response
from quantforge.archive_verification_full import classify_network_error, checksum_digest
from quantforge.phase3a_config import load_history_config
from quantforge.phase3a_gate2_oracle import validity, index_sha, segments

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "configs/holdout-a-execution.toml"
CONTRACT_SHA = "02cea40915339da68ba40befcc893872f885444e46d100787ef91533c9e6c408"
RAW = ROOT / "data/raw/binance/spot/BTCUSDT/1m/holdout-a"
EVIDENCE = ROOT / "docs/holdout-a-evidence"
OUTPUT = ROOT / "results/holdout-a"
LAUNCH = ROOT / ".tmp/ha-real-data-launch-ledger.jsonl"
DEV_ROOT = Path("D:/zgs_project/QuantForge")
MONTHS = ("2026-07", "2026-08")
START = pd.Timestamp("2025-09-01T00:00:00Z")
END = pd.Timestamp("2026-09-01T00:00:00Z")
MINUTE = 60_000_000_000
AUTH = "D-059:38ef1aae687b6cd1a9b8b59709b84047250e2a9f90396cb9089244133b38f1b9"
LEVEL = "confirmatory_holdout_a_protocol_v1"


class HoldoutError(ValueError):
    """A fixed execution condition failed; preserve the current state."""


class PersistenceError(OSError):
    """A write failed: do not attempt a secondary evidence write."""


def require(ok, message):
    if not ok:
        raise HoldoutError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def json_bytes(value):
    return runner._json_bytes(value)


def safe(path, roots):
    path = Path(path).absolute()
    require(any(path.is_relative_to(Path(r).absolute()) for r in roots), "path outside authorized roots")
    require(path.resolve() == path, "redirected path")
    for part in (path, *path.parents):
        require(not part.is_symlink() and not part.is_junction(), "symlink or junction forbidden")
    return path


def write_bytes(path, payload):
    path = safe(path, (RAW, EVIDENCE, OUTPUT))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise PersistenceError(str(exc)) from exc


def write_json(path, value):
    write_bytes(path, json_bytes(value))


def append(path, event):
    path = safe(path, (EVIDENCE, ROOT / ".tmp"))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as handle:
            handle.write((json.dumps(event, ensure_ascii=True, sort_keys=True, allow_nan=False) + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise PersistenceError(str(exc)) from exc


def read_events(path):
    return [json.loads(line) for line in path.read_bytes().splitlines()] if path.exists() else []


def require_auth():
    require(os.environ.get("QF_HOLDOUT_A_AUTH") == AUTH, "D-059 authorization missing or incorrect")
    require("pytest" not in sys.modules, "real access forbidden in pytest")


def guard_month(month):
    require(month in MONTHS, "month outside Holdout-A authorization")


def guard_times(times):
    index = pd.DatetimeIndex(times)
    require(index.tz is not None and not index.hasnans, "timestamps must be aware and finite")
    require(len(index) > 0 and bool(((index >= START) & (index < END)).all()), "sealed or out-of-range timestamp")


def load_contract():
    require(digest(CONTRACT) == CONTRACT_SHA, "execution contract SHA mismatch")
    contract = tomllib.loads(CONTRACT.read_text(encoding="utf-8"))
    for name, expected in contract["frozen_sha256"].items():
        require(digest(ROOT / name) == expected, "frozen SHA mismatch: " + name)
    config = load_history_config()
    return contract, config


def resources(contract, started):
    gates = contract["gates"]
    require(time.perf_counter() - started <= gates["run_seconds_max"], "wall time budget exceeded")
    require(runner.peak_process_bytes() <= gates["peak_process_bytes_max"], "memory budget exceeded")
    require(shutil.disk_usage(ROOT).free >= gates["disk_free_bytes_min"], "disk reserve breached")


def url_info(url, contract):
    require(url in frozenset(contract["download"]["urls"]), "URL outside four-string allowlist")
    for month in MONTHS:
        base = contract["download"]["url_prefix"] + month + ".zip"
        if url in (base, base + ".CHECKSUM"):
            return month, "checksum" if url.endswith(".CHECKSUM") else "zip"
    raise HoldoutError("invalid URL contract")


def network_state(contract):
    events = read_events(EVIDENCE / "network-ledger.jsonl")
    intents = {e["id"]: e for e in events if e["event"] == "intent"}
    settled = {e["id"]: e for e in events if e["event"] == "settle"}
    require(len(intents) == sum(e["event"] == "intent" for e in events), "duplicate intent")
    require(set(intents) == set(settled), "unsettled intent; restart forbidden")
    attempts, used = {}, {"zip": 0, "checksum": 0}
    for key, intent in intents.items():
        url_info(intent["url"], contract)
        attempts[intent["url"]] = attempts.get(intent["url"], 0) + 1
        used[intent["kind"]] += settled[key]["bytes"]
    return dict(gets=len(intents), attempts=attempts, used=used)


def read_body(response, limit, remaining, recorded_length=None):
    """Read a bounded response without the daily reader's 1 MiB ZIP ceiling."""
    raw = response.headers.get("Content-Length")
    require(isinstance(raw, str) and re.fullmatch(r"[0-9]+", raw), "missing/non-numeric Content-Length")
    length = int(raw)
    require(0 < length <= limit and length <= remaining, "oversize Content-Length or transfer budget")
    require(recorded_length is None or length == recorded_length, "ZIP Content-Length != recorded")
    payload = b""
    transport = response
    for attr in ("fp", "raw", "_sock"):
        transport = getattr(transport, attr, None)
    if transport is not None:
        transport.settimeout(30)
    try:
        while len(payload) < length:
            chunk = response.read(min(65536, length - len(payload)))
            payload += chunk
            require(len(payload) <= length, "bytes > Content-Length")
            if not chunk:
                raise http.client.IncompleteRead(b"", length - len(payload))
        extra = response.read(1)
        payload += extra
        require(not extra, "bytes > Content-Length")
    except Exception as exc:
        payload += exc.partial if isinstance(exc, http.client.IncompleteRead) else b""
        exc.payload = payload
        exc.content_length = length
        raise
    return payload, length


def move_new(source, target):
    source, target = safe(source, (RAW,)), safe(target, (RAW,))
    require(not target.exists(), "final file already exists")
    try:
        os.replace(source, target)
    except OSError as exc:
        raise PersistenceError(str(exc)) from exc


def fetch(url, contract, started, *, opener=None):
    require_auth()
    month, kind = url_info(url, contract)
    cfg = contract["download"]
    recorded = next(x for x in cfg["recorded"] if x["month"] == month)
    limit = cfg["zip_bytes_max_each"] if kind == "zip" else cfg["checksum_bytes_max_each"]
    opener = _open_response if opener is None else opener
    while True:
        resources(contract, started)
        state = network_state(contract)
        attempt = state["attempts"].get(url, 0) + 1
        require(attempt <= cfg["attempts_per_url_max"] and state["gets"] < cfg["gets_max"], "GET/attempt budget exceeded")
        remaining = cfg["zip_bytes_total_max"] - state["used"]["zip"] if kind == "zip" else limit
        require(remaining >= (recorded["zip_content_length"] if kind == "zip" else recorded["checksum_bytes"]), "transfer budget exceeded")
        if kind == "zip":
            prior = RAW / (f"BTCUSDT-1m-{month}.zip.CHECKSUM")
            require(prior.read_bytes() == recorded["checksum_text"].encode("ascii"), "ZIP requires accepted CHECKSUM")
        identifier = f"H{state['gets'] + 1:04d}"
        url_info(url, contract)
        append(EVIDENCE / "network-ledger.jsonl", dict(event="intent", id=identifier, url=url,
               month=month, kind=kind, attempt=attempt, reserved_bytes=limit + 1, utc=now()))
        payload, response, error = b"", None, None
        status, final_url, length = None, None, None
        result = "accepted"
        try:
            try:
                response = opener(urllib.request.Request(url, method="GET"), timeout=30)
            except urllib.error.HTTPError as exc:
                response = exc
            status = response.getcode()
            final_url = response.geturl()
            length = response.headers.get("Content-Length")
            require(final_url == url, "final URL != requested URL")
            require(status == 200 or status == 429 or 500 <= status <= 599, f"forbidden HTTP status {status}")
            payload, length = read_body(response, limit, remaining,
                recorded["zip_content_length"] if kind == "zip" and status == 200 else None)
            if status != 200:
                raise urllib.error.HTTPError(url, status, "retryable HTTP status", response.headers, None)
            if kind == "checksum":
                require(payload == recorded["checksum_text"].encode("ascii"), "CHECKSUM text != recorded")
                require(len(payload) == recorded["checksum_bytes"], "CHECKSUM bytes != recorded")
                checksum_digest(payload, f"BTCUSDT-1m-{month}.zip")
            else:
                expected = checksum_digest(recorded["checksum_text"].encode(), f"BTCUSDT-1m-{month}.zip")
                require(hashlib.sha256(payload).hexdigest() == expected, "ZIP SHA-256 != CHECKSUM")
        except PermissionError:
            raise
        except Exception as exc:
            error = exc
            payload = getattr(exc, "payload", payload)
            length = getattr(exc, "content_length", length)
            result = "retry" if classify_network_error(exc) == "retry" and attempt < cfg["attempts_per_url_max"] else "stop"
        finally:
            if response is not None:
                response.close()
        partial = RAW / ".partial" / (identifier + ".bin")
        if payload:
            write_bytes(partial, payload)
        append(EVIDENCE / "network-ledger.jsonl", dict(event="settle", id=identifier, http_status=status,
               final_url=final_url, content_length=length, bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest(),
               error=None if error is None else str(error), error_type=None if error is None else type(error).__name__,
               result=result, file=str(partial.relative_to(ROOT)) if payload else None, utc=now()))
        if result == "stop":
            raise error
        if result == "retry":
            continue
        target = RAW / url.rsplit("/", 1)[1]
        move_new(partial, target)
        return dict(month=month, kind=kind, path=str(target.relative_to(ROOT)), bytes=len(payload), sha256=digest(target))


def download(contract, started):
    require_auth()
    require(not RAW.exists() and not (EVIDENCE / "network-ledger.jsonl").exists(), "download output already exists")
    files = [fetch(url, contract, started) for url in contract["download"]["urls"]]
    summary = dict(status="passed", files=files, counters=network_state(contract), contract_sha256=CONTRACT_SHA)
    write_json(EVIDENCE / "download-summary.json", summary)
    return dict(status="download_passed", gets=summary["counters"]["gets"])


def decode_csv(payload):
    """Pure microsecond decoder; no I/O or authorization bypass entry point."""
    require(len(payload) <= 32 * 1024 * 1024, "CSV exceeds 32 MiB")
    frame = pd.read_csv(io.BytesIO(payload), header=None, dtype=str, keep_default_na=False)
    require(len(frame) > 0 and len(frame.columns) == len(data.COLUMNS), "invalid CSV shape")
    frame.columns = data.COLUMNS
    for column in ("open_time", "close_time", "number_of_trades"):
        frame[column] = data._integer_column(frame[column], column)
    require(frame[["open_time", "close_time"]].apply(lambda x: x.between(10**15, 10**17 - 1).all()).all(), "timestamps must use microseconds")
    numeric = data.PRICE_COLUMNS + data.VOLUME_COLUMNS + ["ignore"]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="raise").astype("float64")
    require(np.isfinite(frame[numeric].to_numpy()).all(), "nonfinite numeric field")
    bad_price = ((frame[data.PRICE_COLUMNS] <= 0).any(axis=1) | (frame.low > frame.high)
        | (frame.open < frame.low) | (frame.open > frame.high) | (frame.close < frame.low) | (frame.close > frame.high))
    bad_volume = ((frame[data.VOLUME_COLUMNS] < 0).any(axis=1)
        | (frame.taker_buy_base_asset_volume > frame.volume + 1e-8)
        | (frame.taker_buy_quote_asset_volume > frame.quote_asset_volume + 1e-8))
    require(not bad_price.any() and not bad_volume.any(), "invalid OHLC or volume")
    frame["open_time_raw"], frame["close_time_raw"] = frame.open_time, frame.close_time
    frame["open_time"] = pd.to_datetime(frame.open_time, unit="us", utc=True)
    frame["close_time"] = pd.to_datetime(frame.close_time, unit="us", utc=True)
    return frame


def parse_archive(payload, month):
    require_auth()
    guard_month(month)
    require(len(payload) <= 4194304, "ZIP exceeds 4 MiB")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = archive.infolist()
        require(len(members) == 1 and members[0].filename == f"BTCUSDT-1m-{month}.csv", "unexpected ZIP member")
        member = members[0]
        require(not member.is_dir() and not member.flag_bits & 1 and member.file_size <= 32 * 1024 * 1024, "invalid ZIP member")
        with archive.open(member) as handle:
            content = handle.read(32 * 1024 * 1024 + 1)
        require(len(content) == member.file_size, "CSV size mismatch")
    frame = decode_csv(content)
    audit_month(frame, month)
    return frame


def audit_month(frame, month):
    index = pd.DatetimeIndex(frame.open_time).as_unit("ns")
    guard_times(index)
    start = pd.Timestamp(month + "-01", tz="UTC")
    end = start + pd.offsets.MonthBegin(1)
    require(index.is_unique and index.is_monotonic_increasing and index.equals(index.floor("min")), "duplicate/unsorted/nonminute timestamps")
    require(bool(((index >= start) & (index < end)).all()), "row outside month")
    require((frame.close_time < frame.open_time + pd.Timedelta(minutes=1)).all(), "close_time >= open + 60s")
    for column in ("open_time", "close_time"):
        require(frame[column + "_raw"].between(10**15, 10**17 - 1).all(), "raw timestamp unit mismatch")
        require(pd.DatetimeIndex(pd.to_datetime(frame[column + "_raw"], unit="us", utc=True)).equals(pd.DatetimeIndex(frame[column])), "raw timestamp mismatch")
    missing = pd.date_range(start, end, inclusive="left", freq="min").difference(index)
    gaps = [dict(start=x[0].isoformat(), end=x[-1].isoformat(), minutes=len(x)) for x in segments(missing)]
    anomalies = []
    for row in frame.loc[frame.close_time != frame.open_time + pd.Timedelta(microseconds=59_999_999)].itertuples():
        kind = "zero_trade_placeholder" if row.number_of_trades == 0 and row.volume == 0 and row.open == row.high == row.low == row.close else "truncated_with_trades"
        anomalies.append(dict(open=row.open_time.isoformat(), close=row.close_time.isoformat(), kind=kind))
    return dict(month=month, rows=len(frame), expected_rows=len(index) + len(missing), missing_minutes=len(missing),
                gaps=gaps, close_time_anomalies=anomalies, timestamp_unit="us", duplicate_rows=0)


def load_bars(contract):
    require_auth()
    dev = contract["development"]
    manifest_path = safe(DEV_ROOT / dev["manifest"], (DEV_ROOT,))
    require(digest(manifest_path) == dev["manifest_sha256"], "development manifest SHA mismatch")
    manifest = json.loads(manifest_path.read_bytes())
    entries = manifest["validation"]["month_sources"]
    require([x["month"] for x in entries] == dev["months"], "development month order mismatch")
    require(manifest["expected_rows"] == dev["rows"], "development expected row count mismatch")
    hashes = {str(manifest_path): digest(manifest_path)}
    frames, audits = [], []
    for entry in entries:
        path = safe(DEV_ROOT / dev["parquet_dir"] / f"BTCUSDT-1m-{entry['month']}.parquet", (DEV_ROOT,))
        require(digest(path) == entry["parquet_sha256"], "development parquet SHA mismatch")
        hashes[str(path)] = entry["parquet_sha256"]
        frame = pd.read_parquet(path)
        require(len(frame) == entry["rows"], "development month row mismatch")
        audits.append(audit_month(frame, entry["month"]))
        frames.append(frame)
    require(sum(map(len, frames)) == dev["rows"], "PA-1 development rows mismatch")
    summary = json.loads((EVIDENCE / "download-summary.json").read_bytes())
    require(summary["status"] == "passed" and summary["contract_sha256"] == CONTRACT_SHA, "download summary mismatch")
    expected_files = {x["path"]: x for x in summary["files"]}
    require(len(expected_files) == 4, "download file count mismatch")
    for month in MONTHS:
        recorded = next(x for x in contract["download"]["recorded"] if x["month"] == month)
        paths = [RAW / (f"BTCUSDT-1m-{month}.zip" + suffix) for suffix in (".CHECKSUM", "")]
        for path in paths:
            safe(path, (RAW,))
            expected = expected_files[str(path.relative_to(ROOT))]
            require(path.stat().st_size == expected["bytes"] and digest(path) == expected["sha256"], "Holdout file SHA or size changed")
            hashes[str(path.relative_to(ROOT))] = expected["sha256"]
        require(paths[0].read_bytes() == recorded["checksum_text"].encode(), "stored CHECKSUM mismatch")
        require(paths[1].stat().st_size == recorded["zip_content_length"], "stored ZIP length mismatch")
        require(digest(paths[1]) == checksum_digest(paths[0].read_bytes(), paths[1].name), "stored ZIP checksum mismatch")
        frame = parse_archive(paths[1].read_bytes(), month)
        audits.append(audit_month(frame, month))
        frames.append(frame)
    bars = pd.concat(frames, ignore_index=True)
    guard_times(bars.open_time)
    boundary = frames[10].open_time.iloc[0] - frames[9].open_time.iloc[-1]
    meta = dict(months=audits, input_sha256=hashes, rows=len(bars), development_rows=sum(map(len, frames[:10])),
                june_july_boundary_minutes=boundary.total_seconds() / 60,
                contract_sha256=CONTRACT_SHA, frozen_sha256=contract["frozen_sha256"])
    meta["execution_code_sha256"] = {name: digest(ROOT / name) for name in (
        "src/quantforge/holdout_a.py", "scripts/run_holdout_a.py", "src/quantforge/phase3a_runner.py",
        "src/quantforge/phase3a_gate2.py", "src/quantforge/phase3a_gate2_oracle.py",
        "src/quantforge/datasets/historical_splits.py", "src/quantforge/power_reanalysis.py",
        "src/quantforge/diagnostics/forecast_tests.py", "src/quantforge/diagnostics/hac_tests.py",
        "src/quantforge/archive_verification.py", "src/quantforge/archive_verification_full.py",
        "src/quantforge/data.py", "src/quantforge/phase3a_config.py")}
    return bars.loc[:, contract["data"]["keep_columns"]].copy(), meta


def grid_sets(index, month, horizon, start_bound=START):
    """Independent timestamp-only structural oracle; no frozen builders called."""
    index = pd.DatetimeIndex(index).as_unit("ns")
    stamps = index.asi8
    starts = np.r_[0, np.flatnonzero(np.diff(stamps) != MINUTE) + 1]
    ends = np.r_[starts[1:], len(index)]
    history = np.arange(len(index)) - np.repeat(starts, ends - starts)
    future = np.repeat(ends, ends - starts) - 1 - np.arange(len(index))
    feature = history >= 60
    label = future >= horizon
    decision = stamps + MINUTE + 1_000_000_000
    target_end = stamps + (horizon + 1) * MINUTE
    available = target_end + 1_000_000_000
    start = pd.Timestamp(month + "-01", tz="UTC")
    end = start + pd.offsets.MonthBegin(1)
    ec = (decision >= start.value) & (decision < end.value)
    tc = (decision >= start_bound.value) & (decision < start.value)
    ev = ec & feature & label & (available <= end.value)
    require(ev.any(), "structural evaluation empty")
    tr = tc & feature & label & (available <= start.value) & (target_end <= start.value) & (target_end <= stamps[ev][0] + MINUTE)
    counts = dict(candidate_eval_samples=int(ec.sum()), candidate_train_samples=int(tc.sum()),
                  eval_invalid_labels=int((ec & ~label).sum()), train_invalid_labels=int((tc & ~label).sum()),
                  eval_labels_after_cutoff=int((ec & (available > end.value)).sum()),
                  train_samples=int(tr.sum()), eval_samples=int(ev.sum()))
    return index[tr], index[ev], counts


def bandwidth(n, config):
    return max(config["inference"]["llsw_min_lag"], math.ceil(config["inference"]["llsw_constant"] * math.sqrt(n)))


def prune_eval(index, horizon, config):
    if horizon != 15:
        return index, []
    lag = bandwidth(len(index), config)
    kept, dropped = [], []
    for part in segments(index):
        if len(part) <= lag:
            dropped.append(dict(start=part[0].isoformat(), end=part[-1].isoformat(), length=len(part), threshold=lag,
                                reason="D059_h15_short_segment"))
        else:
            kept.extend(part.asi8)
    return pd.to_datetime(np.asarray(kept, dtype=np.int64), utc=True), dropped


def prepare(bars, config, contract, source_audit):
    from quantforge.features import build_features
    from quantforge.labels import build_labels
    from quantforge.datasets.historical_splits import _make_splits_internal
    guard_times(bars.open_time)
    features = build_features(bars)
    independent = validity(bars)
    require(np.array_equal(features.metadata.valid_features.to_numpy(), independent["valid"]), "feature validity oracle mismatch")
    labels, folds, audits = {}, {}, []
    undefined = []
    for month in MONTHS:
        rows = [x for x in independent["reasons"] if x["bar_open"].startswith(month)]
        n = int((bars.open_time.dt.strftime("%Y-%m") == month).sum())
        require(n > 0 and len(rows) / n <= contract["gates"]["feature_undefined_ratio_max_per_month"], "PA-3 undefined feature ratio exceeded")
        undefined.append(dict(month=month, present=n, undefined=len(rows), rows=rows))
    for h in (15, 5):
        labels[h] = build_labels(bars, h)
        meta = features.metadata.join(labels[h].frame.drop(columns="target_return"))
        built = _make_splits_internal(meta, MONTHS, h, "month_end", START, END)
        folds[h] = []
        for fold in built:
            month = fold.fit_time.strftime("%Y-%m")
            train, evaluation, counters = grid_sets(meta.index, month, h)
            for key in ("candidate_eval_samples", "candidate_train_samples", "eval_invalid_labels", "train_invalid_labels", "eval_labels_after_cutoff"):
                require(counters[key] == fold.audit[key], "PA-6 structural counter mismatch: " + key)
            valid = features.metadata.valid_features
            expected_train = train[valid.loc[train].to_numpy()]
            expected_eval = evaluation[valid.loc[evaluation].to_numpy()]
            require(fold.train_indices.equals(expected_train) and fold.eval_indices.equals(expected_eval), "PA-6 structural minus D* mismatch")
            final, removed = prune_eval(fold.eval_indices, h, config)
            lag = bandwidth(len(final), config)
            require(len(fold.train_indices) >= contract["gates"]["n_train_min"], "PA-4 insufficient training")
            require(len(final) > 0, "PA-5 empty evaluation")
            if h == 15:
                require(all(len(x) > lag for x in segments(final)), "PA-5 short segment remains")
            row = {**fold.audit, "eval_samples": len(final), "raw_eval_samples": len(fold.eval_indices),
                   "structural": counters, "D_star_train": len(train) - len(expected_train),
                   "D_star_eval": len(evaluation) - len(expected_eval), "removed_segments": removed,
                   "llsw_lag": lag, "segment_lengths": [len(x) for x in segments(final)],
                   "train_ids_sha256": index_sha(fold.train_indices), "eval_ids_sha256": index_sha(final),
                   "D_star_eval_rows": [x for x in independent["reasons"] if pd.Timestamp(x["bar_open"]) in evaluation and not valid.loc[pd.Timestamp(x["bar_open"])]],
                   "structural_train_ids_sha256": index_sha(train), "structural_eval_ids_sha256": index_sha(evaluation)}
            audits.append(row)
            folds[h].append(replace(fold, eval_indices=final))
    audit = {**source_audit, "undefined_features": undefined, "folds": audits,
             "expected_prediction_rows": sum(x["eval_samples"] * (2 if x["horizon_minutes"] == 15 else 1) for x in audits),
             "gates": {"PA-" + str(i): True for i in range(1, 7)}}
    return features, labels, folds, audit


def execution_config(config):
    """Select only the contract's LLSW/n-weighted endpoints in memory."""
    import copy
    result = copy.deepcopy(config)
    result["months"]["evaluate"] = list(MONTHS)
    result["inference"]["lags"] = ["LLSW"]
    result["inference"]["pooling"] = ["n_weighted"]
    return result


def execute(prepared, config, output, contract, started):
    features, labels, folds, audit = prepared
    config = execution_config(config)
    monthly, models, predictions, drift = [], [], [], []
    fits = 0
    for h in (15, 5):
        for fold in folds[h]:
            for spec in (("v1", "v2") if h == 15 else ("v1",)):
                resources(contract, started)
                require(fits < contract["fits"]["ridge_fits_per_run"], "seventh fit forbidden")
                columns = config["specification"]["v1_features"]
                if spec == "v2":
                    columns = [x for x in columns if x not in config["specification"]["v2_drop_features"]]
                predicted, truth, state = runner.fit_predict_fold(features, labels[h], fold, columns, config)
                fits += 1
                month = fold.fit_time.strftime("%Y-%m")
                times = pd.DatetimeIndex(features.metadata.loc[fold.eval_indices, "decision_time"])
                y = truth.to_numpy(dtype=np.float64)
                state.update(specification=spec, horizon_minutes=h, month=month)
                models.append(state)
                frame = pd.DataFrame(dict(decision_time=times, horizon=h, spec=spec, prediction=predicted, target=y))
                frame["row_sha256"] = [hashlib.sha256(json_bytes(dict(decision_time=t.isoformat(), horizon=h, spec=spec,
                    prediction=float(p), target=float(v)))).hexdigest() for t, p, v in zip(times, predicted, y, strict=True)]
                predictions.append(frame)
                for baseline in ("zero", "historical_mean"):
                    b = np.zeros(len(y)) if baseline == "zero" else np.full(len(y), state["train_target_mean"])
                    key = dict(month=month, specification=spec, horizon_minutes=h, baseline=baseline,
                               specification_evidence_level="frozen_protocol_v1" if spec == "v1" else "frozen_secondary_spec_v2")
                    monthly.extend(runner._monthly(y, predicted, b, times, key, config, mode="real", evidence=LEVEL))
                if h == 15 and spec == "v1":
                    cw = float(np.mean(2 * predicted * y))
                    location = float(2 * np.mean(predicted) * np.mean(y))
                    drift.append(dict(month=month, n=len(y), mean_c=cw, twice_mean_f_mean_y=location,
                                      difference=cw - location, mean_f=float(np.mean(predicted)), mean_y=float(np.mean(y))))
    require(fits == 6, "fit count differs from six")
    pooled = runner._pool(monthly, config, evidence=LEVEL)
    primary = runner._select(pooled, "v1", 15, "zero", "CW")
    require(primary["p_value"] is not None, "primary is undefined")
    primary.update(reject=primary["p_value"] < config["inference"]["alpha"], family_size=1)
    secondary = runner._secondary(pooled, config)
    primary_months = []
    for row in monthly:
        if (row["specification"], row["horizon_minutes"], row["baseline"], row["metric"]) == ("v1", 15, "zero", "CW"):
            fold = next(x for x in audit["folds"] if x["month"] == row["month"] and x["horizon_minutes"] == 15)
            primary_months.append({**row, "maturity_excluded": fold["eval_labels_after_cutoff"], "removed_segments": fold["removed_segments"]})
    total = sum(x["n"] for x in drift)
    mean_f = sum(x["n"] * x["mean_f"] for x in drift) / total
    mean_y = sum(x["n"] * x["mean_y"] for x in drift) / total
    mean_c = sum(x["n"] * x["mean_c"] for x in drift) / total
    decomposition = dict(evidence_level="exploratory_post_hoc", months=drift,
        pooled=dict(n=total, mean_c=mean_c, twice_mean_f_mean_y=2 * mean_f * mean_y, difference=mean_c - 2 * mean_f * mean_y),
        pooling_note="pooled drift product uses pooled mean(f) and mean(y), not the mean of monthly products")
    prediction_frame = pd.concat(predictions, ignore_index=True)
    require(len(prediction_frame) == audit["expected_prediction_rows"], "prediction row count mismatch")
    output = safe(output, (OUTPUT,))
    try:
        output.mkdir(parents=True, exist_ok=False)
        with (output / "predictions.parquet").open("xb") as handle:
            prediction_frame.to_parquet(handle, index=False, compression="zstd")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise PersistenceError(str(exc)) from exc
    write_json(output / "primary.json", dict(months=primary_months, pooled=primary))
    write_json(output / "descriptive-drift.json", decomposition)
    write_json(output / "models.json", models)
    write_json(output / "fold-audit.json", audit["folds"])
    for name, rows in (("monthly.csv", monthly), ("secondary.csv", secondary)):
        write_bytes(output / name, pd.DataFrame(rows).to_csv(index=False, float_format=config["output"]["float_format"], lineterminator="\n").encode())
    summary = dict(status="passed", ridge_fits=fits, prediction_rows=len(prediction_frame), primary=primary,
                   evidence_level=LEVEL, contract_sha256=CONTRACT_SHA, protocol_sha256=contract["contract"]["protocol_sha256"],
                   required_exposures=contract["report"]["required_exposures"], input_sha256=audit["input_sha256"], network_requests=0)
    write_json(output / "summary.json", summary)
    write_json(output / "artifact-index.json", {p.name:dict(bytes=p.stat().st_size, sha256=digest(p)) for p in sorted(output.iterdir())})
    resources(contract, started)
    return summary


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()


@contextmanager
def offline():
    originals = (socket.socket.connect, socket.create_connection, urllib.request.urlopen)
    def blocked(*args, **kwargs):
        raise HoldoutError("network forbidden outside download")
    socket.socket.connect = socket.create_connection = urllib.request.urlopen = blocked
    try:
        yield
    finally:
        socket.socket.connect, socket.create_connection, urllib.request.urlopen = originals


def compare_outputs(formal, reproduction):
    first = {p.name for p in formal.iterdir() if p.name != "runtime.json"}
    second = {p.name for p in reproduction.iterdir() if p.name != "runtime.json"}
    require(first == second, "reproduction artifact set differs")
    rows = {name: dict(formal_sha256=digest(formal / name), reproduction_sha256=digest(reproduction / name)) for name in sorted(first)}
    for row in rows.values():
        row["byte_identical"] = row["formal_sha256"] == row["reproduction_sha256"]
    result = dict(all_byte_identical=all(x["byte_identical"] for x in rows.values()), files=rows)
    write_json(reproduction / "determinism-check.json", result)
    require(result["all_byte_identical"], "reproduction differs")


def run(mode):
    require_auth()
    modes = ("download", "audit", "formal", "reproduction")
    require(mode in modes, "invalid mode")
    contract, config = load_contract()
    started = time.perf_counter()
    resources(contract, started)
    events = read_events(LAUNCH)
    starts = [x for x in events if x["event"] == "start"]
    ends = [x for x in events if x["event"] == "end"]
    require(len(starts) == len(ends) and all(x["exit_code"] == 0 for x in ends), "prior run unfinished or failed")
    require(len(starts) < 4 and mode == modes[len(starts)], "real process limit/order violated")
    head = git("rev-parse", "HEAD")
    status = git("status", "--porcelain")
    if mode in ("download", "formal", "reproduction"):
        require(not status, "worktree is not clean")
    else:
        require(all(x == "?? docs/holdout-a-evidence/" for x in status.splitlines()), "unexpected audit worktree changes")
    message = git("log", "-1", "--format=%s")
    expected_message = "feat: holdout-a confirmatory runner (synthetic tests only)" if mode in ("download", "audit") else "docs: holdout-a download and result-blind data audit (no fitting)"
    require(message == expected_message, "HEAD is not required H1/H2 commit")
    if mode == "audit":
        require(starts[0]["head"] == head, "audit HEAD differs from download H1")
    if mode in ("formal", "reproduction"):
        require(git("rev-parse", "HEAD^") == starts[1]["head"], "H2 parent differs from audited H1")
    if mode == "reproduction":
        require(head == starts[2]["head"], "reproduction HEAD differs from formal")
    launch = dict(mode=mode, launch=len(starts) + 1, head=head, argv=sys.argv)
    append(LAUNCH, dict(**launch, event="start", utc=now(), exit_code=None))
    try:
        if mode == "download":
            result = download(contract, started)
        else:
            with offline(), threadpool_limits(limits=config["experiment"]["cpu_threads"]):
                bars, source_audit = load_bars(contract)
                prepared = prepare(bars, config, contract, source_audit)
                if mode == "audit":
                    write_json(EVIDENCE / "data-audit.json", prepared[3])
                    result = dict(status="audit_passed", gates=prepared[3]["gates"], ridge_fits=0)
                else:
                    previous = json.loads((EVIDENCE / "data-audit.json").read_bytes())
                    require(json_bytes(previous) == json_bytes(prepared[3]), "data audit changed after H2")
                    output = OUTPUT / (mode + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + head[:7])
                    result = execute(prepared, config, output, contract, started)
                    write_json(output / "runtime.json", dict(mode=mode, head=head, pid=os.getpid(), utc=now(), output=str(output),
                               elapsed_seconds=time.perf_counter() - started, peak_process_bytes=runner.peak_process_bytes(),
                               disk_free_bytes=shutil.disk_usage(ROOT).free, ridge_fits=6))
                    if mode == "reproduction":
                        formal = list(OUTPUT.glob("formal-*"))
                        require(len(formal) == 1, "formal directory is not unique")
                        compare_outputs(formal[0], output)
        resources(contract, started)
    except (PersistenceError, PermissionError):
        raise
    except Exception as exc:
        write_json(EVIDENCE / ("stop-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json"),
                   dict(mode=mode, utc=now(), error_type=type(exc).__name__, error=str(exc), head=head))
        append(LAUNCH, dict(**launch, event="end", utc=now(), exit_code=1))
        raise
    append(LAUNCH, dict(**launch, event="end", utc=now(), exit_code=0))
    return {k: result[k] for k in ("status", "ridge_fits", "gets", "gates") if k in result}
