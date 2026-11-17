"""D-063 bounded acquisition and frozen Holdout-B confirmatory execution.

Implements docs/confirmatory-protocol-holdout-b.md (SHA-256 6843156f...). Adapted
from quantforge.holdout_a; the frozen feature, label, split, model and inference
code is reused unchanged. Real-data entry points require QF_HOLDOUT_B_AUTH and
refuse to run under pytest.
"""
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
import urllib.error
import urllib.request
import zipfile

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from quantforge import holdout_a as ha
from quantforge import phase3a_runner as runner
from quantforge.archive_verification import _open_response
from quantforge.archive_verification_full import checksum_digest, classify_network_error
from quantforge.diagnostics.hac_tests import holm_adjust
from quantforge.phase3a_config import load_history_config
from quantforge.phase3a_gate2_oracle import index_sha, segments, validity

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / "docs/confirmatory-protocol-holdout-b.md"
PROTOCOL_SHA = "6843156fdcd455956a3dc9cbc27cb5c213cf22b2fa7932ca49e6c51e13e2f315"
AUTH = "D-063:" + PROTOCOL_SHA
RAW = ROOT / "data/raw/binance/spot/BTCUSDT/1m/holdout-b"
EVIDENCE = ROOT / "docs/holdout-b-evidence"
OUTPUT = ROOT / "results/holdout-b"
LAUNCH = ROOT / ".tmp/hb-real-data-launch-ledger.jsonl"
DEV_ROOT = Path("D:/zgs_project/QuantForge")
HA_ROOT = Path("D:/zgs_project/QuantForge-3a")
HA_EVIDENCE = ROOT / "docs/holdout-a-evidence"
HA_MONTHS = ("2026-07", "2026-08")
MONTHS = ("2026-09", "2026-10")
START = pd.Timestamp("2025-09-01T00:00:00Z")
SMOKE = pd.Timestamp("2026-09-01T00:00:00Z")
EVAL_START = pd.Timestamp("2026-09-02T00:00:00Z")
END = pd.Timestamp("2026-11-01T00:00:00Z")
FIT_TIMES = {"2026-09": pd.Timestamp("2026-09-02T00:00:00Z"), "2026-10": pd.Timestamp("2026-10-01T00:00:00Z")}
MINUTE = 60_000_000_000
LEVEL = "confirmatory_holdout_b_protocol"
URL_PREFIX = "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-"
URLS = tuple(URL_PREFIX + m + s for m in MONTHS for s in (".zip.CHECKSUM", ".zip"))
BUDGET = dict(attempts_per_url_max=3, gets_max=12, zip_bytes_max_each=4194304, zip_bytes_total_max=16777216,
              checksum_bytes_max_each=4096)
GATES = dict(n_train_min=50_000, feature_undefined_ratio_max_per_month=0.01, run_seconds_max=7200,
             peak_process_bytes_max=17179869184, disk_free_bytes_min=8589934592, ridge_fits_per_run=6)
FROZEN = {
    "configs/history-extension.toml": "e9cbd19d884d046199f468f29e912cd6a9fbec1852e90ffc125b86d8df0bd9af",
    "src/quantforge/features/__init__.py": "857819a0c3c718cf2b1100ea72995db989ed149f65d1ad3d5e4cd3eda1fcbec1",
    "src/quantforge/labels/__init__.py": "b968218639387cb82ab1beccef5c5bf3ed3674e5975c3d2b25cb4d318037746e",
    "src/quantforge/datasets/research_splits.py": "22c862f6e8e232dedfe17c3a9832f5b7d78737de1593d6b904e2c29c5949919c",
    "src/quantforge/datasets/splits.py": "3459e5be5185eee2638709c3db4250e150d8e1deff140d1459882cb5257d93cd",
    "src/quantforge/datasets/window_splits.py": "24de7804527d3dc805f46ee969d17267927e345b94af434303cd19793675fc22",
    "src/quantforge/models/baselines.py": "a9269ca7f6147f653f608051746304596e898f49ec30f9e1714567e25a1ded83",
    "src/quantforge/temporal.py": "6c262bbd1c77e6696c7d310eb31b321f3f27ffeea02fbd64a70f3dcee9bd648c",
    "docs/confirmatory-protocol-holdout-b.md": PROTOCOL_SHA,
}
DEV = dict(manifest="data/processed/binance/spot/BTCUSDT/1m/development-manifest.json",
           manifest_sha256="6fc42cd53ab30e1282bc9202046edd1fb43e5bc3fa8c44595f1639958c66acb8",
           parquet_dir="data/processed/binance/spot/BTCUSDT/1m/monthly", rows=436320,
           months=["2025-09", "2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"])
KEEP = ["open_time", "open", "high", "low", "close", "volume", "quote_asset_volume", "number_of_trades",
        "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume"]
HB1_MESSAGE = "feat: holdout-b confirmatory runner (synthetic tests only)"
HB2_MESSAGE = "docs: holdout-b download and result-blind data audit (no fitting)"

HoldoutError = ha.HoldoutError
PersistenceError = ha.PersistenceError
require = ha.require
now = ha.now
digest = ha.digest
json_bytes = ha.json_bytes


def safe(path, roots):
    return ha.safe(path, roots)


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
    require(os.environ.get("QF_HOLDOUT_B_AUTH") == AUTH, "D-063 authorization missing or incorrect")
    require("pytest" not in sys.modules, "real access forbidden in pytest")


def guard_times(times):
    """Allowed: [2025-09-01, 2026-09-01) for training and [2026-09-02, 2026-11-01). The smoke day never passes."""
    index = pd.DatetimeIndex(times)
    require(index.tz is not None and not index.hasnans and len(index) > 0, "timestamps must be aware, finite, nonempty")
    inside = (index >= START) & (index < END)
    smoke = (index >= SMOKE) & (index < EVAL_START)
    require(bool(inside.all()) and not bool(smoke.any()), "sealed, smoke-day or out-of-range timestamp")


def drop_smoke_rows(frame):
    """Discard every row before 2026-09-02 from the September archive; return kept frame and count."""
    keep = frame.open_time >= EVAL_START
    return frame.loc[keep].reset_index(drop=True), int((~keep).sum())


def check_frozen():
    for name, expected in FROZEN.items():
        require(digest(ROOT / name) == expected, "frozen SHA mismatch: " + name)
    return load_history_config()


def resources(started):
    require(time.perf_counter() - started <= GATES["run_seconds_max"], "wall time budget exceeded")
    require(runner.peak_process_bytes() <= GATES["peak_process_bytes_max"], "memory budget exceeded")
    require(shutil.disk_usage(ROOT).free >= GATES["disk_free_bytes_min"], "disk reserve breached")


def url_info(url):
    require(url in frozenset(URLS), "URL outside four-string allowlist")
    for month in MONTHS:
        base = URL_PREFIX + month + ".zip"
        if url in (base, base + ".CHECKSUM"):
            return month, "checksum" if url.endswith(".CHECKSUM") else "zip"
    raise HoldoutError("invalid URL")


def network_state():
    events = read_events(EVIDENCE / "network-ledger.jsonl")
    intents = {e["id"]: e for e in events if e["event"] == "intent"}
    settled = {e["id"]: e for e in events if e["event"] == "settle"}
    require(len(intents) == sum(e["event"] == "intent" for e in events), "duplicate intent")
    require(set(intents) == set(settled), "unsettled intent; restart forbidden")
    attempts, used = {}, {"zip": 0, "checksum": 0}
    for key, intent in intents.items():
        url_info(intent["url"])
        attempts[intent["url"]] = attempts.get(intent["url"], 0) + 1
        used[intent["kind"]] += settled[key]["bytes"]
    return dict(gets=len(intents), attempts=attempts, used=used)


def validate_checksum(payload, month):
    require(0 < len(payload) <= BUDGET["checksum_bytes_max_each"], "CHECKSUM size out of range")
    return checksum_digest(payload, f"BTCUSDT-1m-{month}.zip")


def fetch(url, started, *, opener=None):
    require_auth()
    month, kind = url_info(url)
    limit = BUDGET["zip_bytes_max_each"] if kind == "zip" else BUDGET["checksum_bytes_max_each"]
    opener = _open_response if opener is None else opener
    while True:
        resources(started)
        state = network_state()
        attempt = state["attempts"].get(url, 0) + 1
        require(attempt <= BUDGET["attempts_per_url_max"] and state["gets"] < BUDGET["gets_max"], "GET/attempt budget exceeded")
        remaining = BUDGET["zip_bytes_total_max"] - state["used"]["zip"] if kind == "zip" else limit
        expected = None
        if kind == "zip":
            prior = RAW / f"BTCUSDT-1m-{month}.zip.CHECKSUM"
            require(prior.exists(), "ZIP requires accepted CHECKSUM")
            expected = validate_checksum(prior.read_bytes(), month)
        identifier = f"B{state['gets'] + 1:04d}"
        append(EVIDENCE / "network-ledger.jsonl", dict(event="intent", id=identifier, url=url, month=month, kind=kind,
               attempt=attempt, reserved_bytes=limit + 1, utc=now()))
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
            payload, length = ha.read_body(response, limit, remaining)
            if status != 200:
                raise urllib.error.HTTPError(url, status, "retryable HTTP status", response.headers, None)
            if kind == "checksum":
                validate_checksum(payload, month)
            else:
                require(hashlib.sha256(payload).hexdigest() == expected, "ZIP SHA-256 != CHECKSUM")
        except PermissionError:
            raise
        except Exception as exc:
            error = exc
            payload = getattr(exc, "payload", payload)
            length = getattr(exc, "content_length", length)
            result = "retry" if classify_network_error(exc) == "retry" and attempt < BUDGET["attempts_per_url_max"] else "stop"
        finally:
            if response is not None:
                response.close()
        partial = RAW / ".partial" / (identifier + ".bin")
        if payload:
            write_bytes(partial, payload)
        append(EVIDENCE / "network-ledger.jsonl", dict(event="settle", id=identifier, http_status=status, final_url=final_url,
               content_length=length, bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest(),
               error=None if error is None else str(error), error_type=None if error is None else type(error).__name__,
               result=result, file=str(partial.relative_to(ROOT)) if payload else None, utc=now()))
        if result == "stop":
            raise error
        if result == "retry":
            continue
        target = RAW / url.rsplit("/", 1)[1]
        source, target = safe(partial, (RAW,)), safe(target, (RAW,))
        require(not target.exists(), "final file already exists")
        try:
            os.replace(source, target)
        except OSError as exc:
            raise PersistenceError(str(exc)) from exc
        return dict(month=month, kind=kind, path=str(target.relative_to(ROOT)), bytes=len(payload), sha256=digest(target))


def download(started):
    require_auth()
    require(not RAW.exists() and not (EVIDENCE / "network-ledger.jsonl").exists(), "download output already exists")
    files = [fetch(url, started) for url in URLS]
    summary = dict(status="passed", files=files, counters=network_state(), protocol_sha256=PROTOCOL_SHA)
    write_json(EVIDENCE / "download-summary.json", summary)
    return dict(status="download_passed", gets=summary["counters"]["gets"])


def read_zip_csv(payload, month):
    """Pure: return the decoded frame of the single monthly CSV member (no authorization, no I/O)."""
    require(len(payload) <= BUDGET["zip_bytes_max_each"], "ZIP exceeds 4 MiB")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = archive.infolist()
        require(len(members) == 1 and members[0].filename == f"BTCUSDT-1m-{month}.csv", "unexpected ZIP member")
        member = members[0]
        require(not member.is_dir() and not member.flag_bits & 1 and member.file_size <= 32 * 1024 * 1024, "invalid ZIP member")
        with archive.open(member) as handle:
            content = handle.read(32 * 1024 * 1024 + 1)
        require(len(content) == member.file_size, "CSV size mismatch")
    return ha.decode_csv(content)


def audit_frame(frame, month, start=None):
    """Result-blind month audit; for September the window starts on 2 September."""
    index = pd.DatetimeIndex(frame.open_time).as_unit("ns")
    guard_times(index)
    start = pd.Timestamp(month + "-01", tz="UTC") if start is None else start
    end = pd.Timestamp(month + "-01", tz="UTC") + pd.offsets.MonthBegin(1)
    require(index.is_unique and index.is_monotonic_increasing and index.equals(index.floor("min")), "duplicate/unsorted/nonminute timestamps")
    require(bool(((index >= start) & (index < end)).all()), "row outside audited window")
    require((frame.close_time < frame.open_time + pd.Timedelta(minutes=1)).all(), "close_time >= open + 60s")
    missing = pd.date_range(start, end, inclusive="left", freq="min").difference(index)
    gaps = [dict(start=x[0].isoformat(), end=x[-1].isoformat(), minutes=len(x)) for x in segments(missing)]
    anomalies = []
    for row in frame.loc[frame.close_time != frame.open_time + pd.Timedelta(microseconds=59_999_999)].itertuples():
        kind = "zero_trade_placeholder" if row.number_of_trades == 0 and row.volume == 0 and row.open == row.high == row.low == row.close else "truncated_with_trades"
        anomalies.append(dict(open=row.open_time.isoformat(), close=row.close_time.isoformat(), kind=kind))
    return dict(month=month, window_start=start.isoformat(), rows=len(frame), expected_rows=len(index) + len(missing),
                missing_minutes=len(missing), gaps=gaps, close_time_anomalies=anomalies, timestamp_unit="us")


def load_bars():
    require_auth()
    hashes, frames, audits = {}, [], []
    manifest_path = safe(DEV_ROOT / DEV["manifest"], (DEV_ROOT,))
    require(digest(manifest_path) == DEV["manifest_sha256"], "development manifest SHA mismatch")
    manifest = json.loads(manifest_path.read_bytes())
    entries = manifest["validation"]["month_sources"]
    require([x["month"] for x in entries] == DEV["months"], "development month order mismatch")
    hashes[str(manifest_path)] = DEV["manifest_sha256"]
    for entry in entries:
        path = safe(DEV_ROOT / DEV["parquet_dir"] / f"BTCUSDT-1m-{entry['month']}.parquet", (DEV_ROOT,))
        require(digest(path) == entry["parquet_sha256"], "development parquet SHA mismatch")
        hashes[str(path)] = entry["parquet_sha256"]
        frame = pd.read_parquet(path)
        require(len(frame) == entry["rows"], "development month row mismatch")
        audits.append(audit_frame(frame, entry["month"]))
        frames.append(frame)
    require(sum(map(len, frames)) == DEV["rows"], "development rows mismatch")
    # Holdout A archives (read-only from the 3A worktree), verified against the recorded download summary
    ha_summary = json.loads((HA_EVIDENCE / "download-summary.json").read_bytes())
    ha_files = {x["path"].replace("\\", "/"): x for x in ha_summary["files"]}
    for month in HA_MONTHS:
        for suffix in (".zip.CHECKSUM", ".zip"):
            rel = f"data/raw/binance/spot/BTCUSDT/1m/holdout-a/BTCUSDT-1m-{month}{suffix}"
            path = safe(HA_ROOT / rel, (HA_ROOT / "data/raw/binance/spot/BTCUSDT/1m/holdout-a",))
            require(digest(path) == ha_files[rel]["sha256"] and path.stat().st_size == ha_files[rel]["bytes"], "Holdout-A file changed")
            hashes[str(path)] = ha_files[rel]["sha256"]
        zpath = HA_ROOT / f"data/raw/binance/spot/BTCUSDT/1m/holdout-a/BTCUSDT-1m-{month}.zip"
        frame = read_zip_csv(zpath.read_bytes(), month)
        audits.append(audit_frame(frame, month))
        frames.append(frame)
    # Holdout B archives
    summary = json.loads((EVIDENCE / "download-summary.json").read_bytes())
    require(summary["status"] == "passed" and summary["protocol_sha256"] == PROTOCOL_SHA, "download summary mismatch")
    files = {x["path"].replace("\\", "/"): x for x in summary["files"]}
    require(len(files) == 4, "download file count mismatch")
    dropped = 0
    for month in MONTHS:
        paths = [RAW / f"BTCUSDT-1m-{month}.zip{suffix}" for suffix in (".CHECKSUM", "")]
        for path in paths:
            safe(path, (RAW,))
            rec = files[str(path.relative_to(ROOT)).replace("\\", "/")]
            require(path.stat().st_size == rec["bytes"] and digest(path) == rec["sha256"], "Holdout-B file SHA or size changed")
            hashes[str(path.relative_to(ROOT)).replace("\\", "/")] = rec["sha256"]
        require(digest(paths[1]) == validate_checksum(paths[0].read_bytes(), month), "stored ZIP checksum mismatch")
        frame = read_zip_csv(paths[1].read_bytes(), month)
        if month == "2026-09":
            frame, dropped = drop_smoke_rows(frame)
        audits.append(audit_frame(frame, month, start=EVAL_START if month == "2026-09" else None))
        frames.append(frame)
    bars = pd.concat(frames, ignore_index=True)
    guard_times(bars.open_time)
    meta = dict(months=audits, input_sha256=hashes, rows=len(bars), smoke_rows_discarded_unread=dropped,
                protocol_sha256=PROTOCOL_SHA, frozen_sha256=FROZEN,
                execution_code_sha256={n: digest(ROOT / n) for n in ("src/quantforge/holdout_b.py", "scripts/run_holdout_b.py",
                                       "src/quantforge/holdout_a.py", "src/quantforge/phase3a_runner.py",
                                       "src/quantforge/phase3a_gate2_oracle.py", "src/quantforge/datasets/historical_splits.py",
                                       "src/quantforge/power_reanalysis.py", "src/quantforge/diagnostics/forecast_tests.py",
                                       "src/quantforge/diagnostics/hac_tests.py")})
    return bars.loc[:, KEEP].copy(), meta


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
                                reason="protocol_b_h15_short_segment"))
        else:
            kept.extend(part.asi8)
    return pd.to_datetime(np.asarray(kept, dtype=np.int64), utc=True), dropped


def prepare(bars, config, source_audit):
    from quantforge.features import build_features
    from quantforge.labels import build_labels
    from quantforge.datasets.historical_splits import _make_splits_internal
    guard_times(bars.open_time)
    features = build_features(bars)
    independent = validity(bars)
    require(np.array_equal(features.metadata.valid_features.to_numpy(), independent["valid"]), "feature validity oracle mismatch")
    labels, folds, audits, undefined = {}, {}, [], []
    for month in MONTHS:
        rows = [x for x in independent["reasons"] if x["bar_open"].startswith(month)]
        n = int((bars.open_time.dt.strftime("%Y-%m") == month).sum())
        require(n > 0 and len(rows) / n <= GATES["feature_undefined_ratio_max_per_month"], "undefined feature ratio exceeded")
        undefined.append(dict(month=month, present=n, undefined=len(rows), rows=rows))
    for h in (15, 5):
        labels[h] = build_labels(bars, h)
        meta = features.metadata.join(labels[h].frame.drop(columns="target_return"))
        built = _make_splits_internal(meta, MONTHS, h, "month_end", START, END)
        folds[h] = []
        for fold in built:
            month = fold.fit_time.strftime("%Y-%m")
            train, evaluation, counters = ha.grid_sets(meta.index, month, h, start_bound=START)
            for key in ("candidate_eval_samples", "candidate_train_samples", "eval_invalid_labels", "train_invalid_labels", "eval_labels_after_cutoff"):
                require(counters[key] == fold.audit[key], "structural counter mismatch: " + key)
            valid = features.metadata.valid_features
            expected_train = train[valid.loc[train].to_numpy()]
            expected_eval = evaluation[valid.loc[evaluation].to_numpy()]
            require(fold.train_indices.equals(expected_train) and fold.eval_indices.equals(expected_eval), "structural minus D* mismatch")
            fit_time = FIT_TIMES[month]
            if fit_time != fold.fit_time:
                # September: the protocol fit time is 2 September; no bar exists on the smoke day, so the legal
                # training set must be identical. Verify, then carry the protocol fit time.
                decision = features.metadata.loc[fold.train_indices, "decision_time"]
                require(bool((decision < fit_time).all()), "training decision after protocol fit time")
                between = (meta.decision_time >= fold.fit_time) & (meta.decision_time < fit_time)
                eligible = between & meta.valid_features & meta.valid_label
                require(not bool(eligible.any()), "valid samples exist between kernel and protocol fit times")
                fold = replace(fold, fit_time=fit_time)
            eval_decisions = features.metadata.loc[fold.eval_indices, "decision_time"]
            require(bool((eval_decisions >= EVAL_START).all()), "evaluation decision before 2026-09-02")
            final, removed = prune_eval(fold.eval_indices, h, config)
            lag = bandwidth(len(final), config)
            require(len(fold.train_indices) >= GATES["n_train_min"], "insufficient training")
            require(len(final) > 0, "empty evaluation")
            if h == 15:
                require(all(len(x) > lag for x in segments(final)), "short segment remains")
            audits.append({**fold.audit, "fit_time": fit_time.isoformat(), "eval_samples": len(final),
                           "raw_eval_samples": len(fold.eval_indices), "structural": counters,
                           "D_star_train": len(train) - len(expected_train), "D_star_eval": len(evaluation) - len(expected_eval),
                           "removed_segments": removed, "llsw_lag": lag, "segment_lengths": [len(x) for x in segments(final)],
                           "train_ids_sha256": index_sha(fold.train_indices), "eval_ids_sha256": index_sha(final)})
            folds[h].append(replace(fold, eval_indices=final))
    audit = {**source_audit, "undefined_features": undefined, "folds": audits,
             "expected_prediction_rows": sum(x["eval_samples"] * (2 if x["horizon_minutes"] == 15 else 1) for x in audits),
             "gates": {"undefined_ratio": True, "n_train": True, "short_segments": True, "structural_oracle": True}}
    return features, labels, folds, audit


def fold_month(fold):
    """D-064: label a fold by its fit-time month (as in holdout_a), never by its first evaluation bar.

    The first evaluation bar of a month can open at 23:59 on the last day of the previous month
    (decision time 00:00:01 of the new month), so the bar-open month would mislabel the fold.
    """
    return fold.fit_time.strftime("%Y-%m")


def execution_config(config):
    import copy
    result = copy.deepcopy(config)
    result["months"]["evaluate"] = list(MONTHS)
    result["inference"]["lags"] = ["LLSW"]
    result["inference"]["pooling"] = ["n_weighted"]
    return result


def holm_family(pooled):
    """Primary (IC v1/15m) and the five protocol secondaries with Holm adjustment."""
    primary = runner._select(pooled, "v1", 15, "zero", "IC")
    require(primary["p_value"] is not None, "primary is undefined")
    primary.update(test_id="primary_IC_v1_15m", reject=primary["p_value"] < 0.05, family_size=1)
    spec = [("S1", "v1", 15, "zero", "CW"), ("S2", "v1", 15, "demeaned_zero", "CW"), ("S3", "v1", 15, "historical_mean", "CW"),
            ("S4", "v1", 15, "zero", "DM"), ("S5", "v1", 5, "zero", "IC")]
    rows = [runner._select(pooled, *s[1:]) for s in spec]
    adjusted = holm_adjust([1.0 if r["p_value"] is None else r["p_value"] for r in rows])
    for (name, *_), row, p in zip(spec, rows, adjusted, strict=True):
        row.update(test_id=name, holm_family="protocol_B_secondary", family_size=len(rows), holm_p=p,
                   holm_reject=None if p is None else p < 0.05)
    return primary, rows


def breakeven(times, f, y, horizon, k_bp):
    """Non-overlapping rule sign(f)1{|f|>k} at bar-open minute == 14 (mod 15); per-side break-even fee in bp."""
    stamps = pd.DatetimeIndex(times).as_unit("ns").asi8
    bar_min = ((stamps - 61_000_000_000) // MINUTE) % 1440
    sel = (bar_min % horizon) == horizon - 1
    t, f, y = stamps[sel], np.asarray(f)[sel], np.asarray(y)[sel]
    p = np.sign(f) * (np.abs(f) > k_bp * 1e-4)
    cont = np.concatenate([[False], np.diff(t) == horizon * MINUTE])
    prev = np.concatenate([[0.0], p[:-1]]) * cont
    nxt = np.concatenate([cont[1:], [False]])
    turnover = np.abs(p - prev) + np.abs(p) * (~nxt)
    r = p * y
    return dict(k_bp=k_bp, periods=len(r), traded_share=float(np.mean(p != 0)) if len(p) else None,
                gross_mean_bp_per_period=float(r.mean() * 1e4) if len(r) else None,
                breakeven_fee_bp_per_side=float(r.sum() / turnover.sum() * 1e4) if turnover.sum() > 0 else None)


def combine_ic(a, b):
    """Descriptive n-weighted pooling of two independent pooled IC estimates."""
    n = a["n"] + b["n"]
    wa, wb = a["n"] / n, b["n"] / n
    mean = wa * a["mean"] + wb * b["mean"]
    se = math.sqrt((wa * a["se"]) ** 2 + (wb * b["se"]) ** 2)
    from scipy.stats import norm
    return dict(n=n, mean=mean, se=se, t=mean / se, p_one_sided=float(norm.sf(mean / se)), label="descriptive_only")


def execute(prepared, config, output, started):
    features, labels, folds, audit = prepared
    config = execution_config(config)
    monthly, models, predictions, drift = [], [], [], []
    fits = 0
    for h in (15, 5):
        for fold in folds[h]:
            for spec in (("v1", "v2") if h == 15 else ("v1",)):
                resources(started)
                require(fits < GATES["ridge_fits_per_run"], "seventh fit forbidden")
                columns = config["specification"]["v1_features"]
                if spec == "v2":
                    columns = [x for x in columns if x not in config["specification"]["v2_drop_features"]]
                predicted, truth, state = runner.fit_predict_fold(features, labels[h], fold, columns, config)
                fits += 1
                month = fold_month(fold)
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
                               specification_evidence_level="frozen_v1" if spec == "v1" else "secondary_spec_v2")
                    monthly.extend(runner._monthly(y, predicted, b, times, key, config, mode="real", evidence=LEVEL))
                if h == 15 and spec == "v1":
                    key = dict(month=month, specification=spec, horizon_minutes=h, baseline="demeaned_zero",
                               specification_evidence_level="frozen_v1")
                    monthly.extend(runner._monthly(y - y.mean(), predicted - predicted.mean(), np.zeros(len(y)), times, key,
                                                   config, mode="real", evidence=LEVEL))
                    cw = float(np.mean(2 * predicted * y))
                    location = float(2 * np.mean(predicted) * np.mean(y))
                    drift.append(dict(month=month, n=len(y), mean_c=cw, twice_mean_f_mean_y=location, difference=cw - location))
    require(fits == GATES["ridge_fits_per_run"], "fit count differs from six")
    pooled = runner._pool(monthly, config, evidence=LEVEL)
    primary, secondary = holm_family(pooled)
    primary_months = [r for r in monthly if (r["specification"], r["horizon_minutes"], r["baseline"], r["metric"]) == ("v1", 15, "zero", "IC")]
    pred = pd.concat(predictions, ignore_index=True)
    require(len(pred) == audit["expected_prediction_rows"], "prediction row count mismatch")
    v1 = pred[(pred.spec == "v1") & (pred.horizon == 15)].sort_values("decision_time")
    dm = next(r for r in secondary if r["test_id"] == "S4")
    base = dm["baseline_mse"]
    r2 = None if dm["mean"] is None else -dm["mean"] / base
    se = dm.get("se")
    ha_ic = next(r for r in pd.read_csv(HA_EVIDENCE / "secondary.csv").to_dict(orient="records") if r["test_id"] == "S5")
    descriptive = dict(label="prespecified_descriptive_not_tests",
        v2_15m=dict(ic=runner._select(pooled, "v2", 15, "zero", "IC"), cw=runner._select(pooled, "v2", 15, "zero", "CW")),
        economics=dict(abs_f_bp_quantiles=dict(zip(["p50", "p90", "p99", "p999", "max"],
                       (np.quantile(np.abs(v1.prediction.to_numpy()), [0.5, 0.9, 0.99, 0.999, 1.0]) * 1e4).tolist())),
                       mean_f_bp=float(v1.prediction.mean() * 1e4),
                       rules=[breakeven(v1.decision_time, v1.prediction, v1.target, 15, k) for k in (0.0, 2.0, 5.0)]),
        r2_oos=dict(estimate=r2, ci95=None if se is None else [-(dm["mean"] + 1.96 * se) / base, -(dm["mean"] - 1.96 * se) / base],
                    one_sided_upper95=None if se is None else -(dm["mean"] - 1.645 * se) / base),
        drift_decomposition_S1=drift,
        pooled_holdout_a_and_b_ic=combine_ic(dict(n=int(ha_ic["n"]), mean=float(ha_ic["mean"]), se=float(ha_ic["se"])),
                                            dict(n=primary["n"], mean=primary["mean"], se=primary["se"])))
    output = safe(output, (OUTPUT,))
    try:
        output.mkdir(parents=True, exist_ok=False)
        with (output / "predictions.parquet").open("xb") as handle:
            pred.to_parquet(handle, index=False, compression="zstd")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise PersistenceError(str(exc)) from exc
    write_json(output / "primary.json", dict(months=primary_months, pooled=primary))
    write_json(output / "descriptive.json", descriptive)
    write_json(output / "models.json", models)
    write_json(output / "fold-audit.json", audit["folds"])
    for name, rows in (("monthly.csv", monthly), ("secondary.csv", secondary)):
        write_bytes(output / name, pd.DataFrame(rows).to_csv(index=False, float_format=config["output"]["float_format"], lineterminator="\n").encode())
    summary = dict(status="passed", ridge_fits=fits, prediction_rows=len(pred), primary=primary, evidence_level=LEVEL,
                   protocol_sha256=PROTOCOL_SHA, input_sha256=audit["input_sha256"], network_requests=0)
    write_json(output / "summary.json", summary)
    write_json(output / "artifact-index.json", {p.name: dict(bytes=p.stat().st_size, sha256=digest(p)) for p in sorted(output.iterdir())})
    resources(started)
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
    second = {p.name for p in reproduction.iterdir() if p.name not in ("runtime.json", "determinism-check.json")}
    require(first == second, "reproduction artifact set differs")
    rows = {n: dict(formal_sha256=digest(formal / n), reproduction_sha256=digest(reproduction / n)) for n in sorted(first)}
    for row in rows.values():
        row["byte_identical"] = row["formal_sha256"] == row["reproduction_sha256"]
    result = dict(all_byte_identical=all(x["byte_identical"] for x in rows.values()), files=rows)
    write_json(reproduction / "determinism-check.json", result)
    require(result["all_byte_identical"], "reproduction differs")


LAUNCH2 = ROOT / ".tmp/hb-real-data-launch-ledger-attempt2.jsonl"
HB1B_MESSAGE = "fix: holdout-b fold month key (result-blind; D-064)"
HB2B_MESSAGE = "docs: holdout-b re-audit after D-064 fix (no fitting)"


def audit_equal_except_code(first, second):
    """D-064: the re-audit must equal the HB2 audit in every field except the recorded execution-code hashes."""
    strip = lambda d: {k: v for k, v in d.items() if k != "execution_code_sha256"}
    return json_bytes(strip(first)) == json_bytes(strip(second))


def run(mode):
    """Attempt 2 under D-064 (attempt 1 crashed in formal before writing any result; its ledger and stop file are kept)."""
    require_auth()
    modes = ("audit", "formal", "reproduction")
    require(mode in modes, "invalid mode for attempt 2")
    config = check_frozen()
    started = time.perf_counter()
    resources(started)
    first = read_events(LAUNCH)
    require([x["mode"] for x in first if x["event"] == "start"] == ["download", "audit", "formal"]
            and [x["exit_code"] for x in first if x["event"] == "end"] == [0, 0, 1], "attempt-1 ledger is not the recorded D-064 state")
    events = read_events(LAUNCH2)
    starts = [x for x in events if x["event"] == "start"]
    ends = [x for x in events if x["event"] == "end"]
    require(len(starts) == len(ends) and all(x["exit_code"] == 0 for x in ends), "prior attempt-2 run unfinished or failed")
    require(len(starts) < 3 and mode == modes[len(starts)], "attempt-2 process limit/order violated")
    head = git("rev-parse", "HEAD")
    require(not git("status", "--porcelain"), "worktree is not clean")
    message = git("log", "-1", "--format=%s")
    require(message == (HB1B_MESSAGE if mode == "audit" else HB2B_MESSAGE), "HEAD is not the required HB1b/HB2b commit")
    if mode in ("formal", "reproduction"):
        require(git("rev-parse", "HEAD^") == starts[0]["head"], "HB2b parent differs from re-audited HB1b")
    if mode == "reproduction":
        require(head == starts[1]["head"], "reproduction HEAD differs from formal")
    launch = dict(mode=mode, launch=len(starts) + 1, attempt=2, head=head, argv=sys.argv)
    append(LAUNCH2, dict(**launch, event="start", utc=now(), exit_code=None))
    try:
        with offline(), threadpool_limits(limits=config["experiment"]["cpu_threads"]):
            bars, source_audit = load_bars()
            prepared = prepare(bars, config, source_audit)
            if mode == "audit":
                committed = json.loads((EVIDENCE / "data-audit.json").read_bytes())
                require(audit_equal_except_code(committed, prepared[3]), "re-audit differs from HB2 audit beyond the code hash")
                write_json(EVIDENCE / "data-audit-attempt2.json", prepared[3])
                result = dict(status="reaudit_passed", gates=prepared[3]["gates"], ridge_fits=0)
            else:
                previous = json.loads((EVIDENCE / "data-audit-attempt2.json").read_bytes())
                require(json_bytes(previous) == json_bytes(prepared[3]), "data audit changed after HB2b")
                output = OUTPUT / (mode + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + head[:7])
                result = execute(prepared, config, output, started)
                write_json(output / "runtime.json", dict(mode=mode, head=head, pid=os.getpid(), utc=now(), output=str(output),
                           elapsed_seconds=time.perf_counter() - started, peak_process_bytes=runner.peak_process_bytes(),
                           disk_free_bytes=shutil.disk_usage(ROOT).free, ridge_fits=6))
                if mode == "reproduction":
                    formal = list(OUTPUT.glob("formal-*"))
                    require(len(formal) == 1, "formal directory is not unique")
                    compare_outputs(formal[0], output)
        resources(started)
    except (PersistenceError, PermissionError):
        raise
    except Exception as exc:
        write_json(EVIDENCE / ("stop-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json"),
                   dict(mode=mode, attempt=2, utc=now(), error_type=type(exc).__name__, error=str(exc), head=head))
        append(LAUNCH2, dict(**launch, event="end", utc=now(), exit_code=1))
        raise
    append(LAUNCH2, dict(**launch, event="end", utc=now(), exit_code=0))
    return {k: result[k] for k in ("status", "ridge_fits", "gates") if k in result}
