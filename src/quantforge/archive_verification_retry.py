"""D-043 bounded retries and append-only source-quality verification v2."""
from __future__ import annotations

import csv
import gzip
import io
import itertools
import http.client
import json
import os
import re
import shutil
import socket
import ssl
import errno
import subprocess
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from quantforge import data, research_policy
from quantforge.archive_verification import sha256, _raw_rows, _open_response
from quantforge.archive_verification_full import (
    classify_network_error, read_response, checksum_digest, parse_daily_csv,
    ParsedDay, RawBar, compare_day, _extend_interval, merge_episodes, _csv_bytes,
    json_bytes, contamination_artifacts, check_memory, unit_of, day_start_us,
    utc_us, _anomaly, StopTask, RetryableTransfer, ALL_DAYS, D039_DAYS, D041_DAYS,
    START_US, MINUTE_US, DAY_US, ZIP_LIMIT, CHECKSUM_LIMIT, MAX_ATTEMPTS,
    BACKOFF_SECONDS, CLASSIFICATIONS, SEVERITIES, CENSUS_NOTE, COLUMNS,
)

ROOT = Path(__file__).resolve().parents[2]
D041_EVIDENCE_RO = ROOT / "docs/phase3a-full-verification-evidence"
D041_LEDGER_RO = D041_EVIDENCE_RO / "network-ledger.jsonl"
D041_VERIFY_RO = Path(r"D:\zgs_project\QuantForge-verification\binance-spot-daily-1m-full-d041")
D039_DIR_RO = Path(r"D:\zgs_project\QuantForge-verification\binance-spot-daily-1m-20261110")
D039_EVIDENCE_RO = ROOT / "docs/phase3a-daily-verification-evidence"
RAW_MONTHLY_RO = ROOT / "data/raw/binance/spot/BTCUSDT/1m/historical"
MANIFEST_RO = ROOT / "data/processed/binance/spot/BTCUSDT/1m/historical/historical-manifest.json"
VERIFY_DIR_RETRY = Path(r"D:\zgs_project\QuantForge-verification\binance-spot-daily-1m-retry-d043")
EVIDENCE_DIR_RETRY = ROOT / "docs/phase3a-retry-verification-evidence"
OTHER_WORKTREES_RO = (Path(r"D:\zgs_project\QuantForge"), Path(r"D:\zgs_project\QuantForge-2f"))
RETRY_DAYS = (
    "2020-02-13", "2020-02-14", "2020-02-15", "2020-02-16", "2020-02-17", "2020-02-18",
    "2020-02-19", "2020-02-20", "2020-02-21", "2020-02-22", "2020-02-23", "2020-02-24",
    "2020-12-09", "2024-03-17",
)
URL_PREFIX = "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-"
URLS = frozenset(URL_PREFIX + day + suffix for day in RETRY_DAYS for suffix in (".zip.CHECKSUM", ".zip"))
TOTAL_GET_LIMIT, NEW_GET_LIMIT, DISTINCT_URL_LIMIT = 4600, 84, 4118
TOTAL_LIMITS = {"zip": 201326592, "checksum": 1048576}
BASE_COUNTS = {"gets": 4217, "distinct_urls": 4107, "zip_bytes": 148197038, "checksum_bytes": 186368}
DISK_RESERVE, EVIDENCE_LIMIT = 8 * 1024**3, 100 * 1024**2
R1_MESSAGE = "feat: D-043 retry tool for 14 unverified days (synthetic tests only)"
COHORTS = ("D-041", "D-043", "census_non_selected", "D-039", "combined")
SELECTION_NOTES = {
    "D-041": CENSUS_NOTE,
    "D-043": CENSUS_NOTE + " These 14 dates were selected because earlier network requests failed.",
    "census_non_selected": CENSUS_NOTE + " D-041 and D-043 form the 2,059-date non-purposively-selected census.",
    "D-039": CENSUS_NOTE + " The 11 dates were purposively selected and cannot estimate the overall defect rate.",
    "combined": CENSUS_NOTE,
}
OLD_CHECKSUMS = {
    "2020-02-13": ("000088.bin", "0ceba65b3adbcb5d1811f20609f2656b3467447ef11b2c055bc2801f995929a1"),
    "2020-12-09": ("000713.bin", "a902b0410f04b913b7ca9d5f06f9da9f9d53bb7dbd160736eca33918db8e22ec"),
    "2024-03-17": ("003133.bin", "051bffaad17856717344874804fc48cf6414ccd5aeba71bf7f6c75ab2135504f"),
}
D041_LEDGER_SHA = "93c0f0bb6faf942011710aab029bf8bec5eb27822987fbe0c41efa7e0af47177"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def emit(value):
    """Keep all CLI output ASCII, including non-ASCII exception messages."""
    print(json.dumps(value, ensure_ascii=True, allow_nan=False), flush=True)


def ensure_day(day):
    if not isinstance(day, str) or day not in RETRY_DAYS:
        raise StopTask("date is outside the D-043 whitelist")
    data.ensure_daily_allowed(date.fromisoformat(day))
    research_policy.ensure_historical_month(day[:7])
    return day


def urls_for_day(day):
    base = URL_PREFIX + ensure_day(day) + ".zip"
    return base + ".CHECKSUM", base


def allowed_url(url):
    if url not in URLS:
        raise StopTask("URL is outside the 28-string D-043 whitelist")
    day = url[len(URL_PREFIX):len(URL_PREFIX) + 10]
    return ensure_day(day), "checksum" if url.endswith(".CHECKSUM") else "zip"


def safe_path(path, root):
    root = Path(root).absolute()
    if root not in (VERIFY_DIR_RETRY.absolute(), EVIDENCE_DIR_RETRY.absolute()):
        raise StopTask("write root is outside D-043")
    target = Path(path)
    if not target.is_absolute():
        target = root / target
    for part in (target, *target.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if part.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise StopTask("output path contains a symlink or junction")
    resolved = target.resolve(strict=False)
    if not resolved.is_relative_to(root.resolve(strict=False)):
        raise StopTask("output path escapes its write root")
    return resolved


def evidence_size():
    return sum(p.stat().st_size for p in EVIDENCE_DIR_RETRY.rglob("*") if p.is_file())


def write_bytes(path, payload, *, root=None):
    root = EVIDENCE_DIR_RETRY if root is None else root
    target = safe_path(path, root)
    if root == EVIDENCE_DIR_RETRY and evidence_size() + len(payload) > EVIDENCE_LIMIT:
        raise StopTask("evidence directory would exceed 100 MiB")
    safe_path(target.parent, root).mkdir(parents=True, exist_ok=True)
    with safe_path(target, root).open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def write_json(name, value):
    write_bytes(EVIDENCE_DIR_RETRY / name, json_bytes(value))


def move_new(source, target):
    source = safe_path(source, VERIFY_DIR_RETRY)
    target = safe_path(target, VERIFY_DIR_RETRY)
    if target.exists():
        raise StopTask("move destination already exists")
    safe_path(target.parent, VERIFY_DIR_RETRY).mkdir(parents=True, exist_ok=True)
    if safe_path(target, VERIFY_DIR_RETRY).exists():
        raise StopTask("move destination already exists")
    os.replace(source, target)


def read_events(path):
    if not path.exists():
        return []
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise StopTask("incomplete ledger line")
    return [json.loads(line) for line in raw.splitlines()]


def append_event(event):
    target = safe_path(EVIDENCE_DIR_RETRY / "network-ledger.jsonl", EVIDENCE_DIR_RETRY)
    raw = (json.dumps(event, ensure_ascii=True, separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")
    if evidence_size() + len(raw) > EVIDENCE_LIMIT:
        raise StopTask("evidence directory would exceed 100 MiB")
    safe_path(target.parent, EVIDENCE_DIR_RETRY).mkdir(parents=True, exist_ok=True)
    with target.open("ab") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def prior_state(events=None):
    """Rebuild D-041 transfer accounting without importing its executor."""
    events = read_events(D041_LEDGER_RO) if events is None else events
    intents, settles, verified, attempts = {}, {}, set(), Counter()
    for row in events:
        if row["event"] == "intent":
            if row["id"] in intents:
                raise StopTask("duplicate D-041 intent")
            intents[row["id"]] = row
            attempts[row["url"]] += 1
        elif row["event"] == "settle":
            if row["id"] not in intents or row["id"] in settles:
                raise StopTask("invalid D-041 settle")
            settles[row["id"]] = row
        elif row["event"] == "verified" and row["summary"]["status"] in ("verified", "passed"):
            verified.add(row["day"])
    if set(intents) != set(settles):
        raise StopTask("D-041 has unresolved intents")
    used = {kind: sum(settles[i]["bytes"] for i, r in intents.items() if r["kind"] == kind) for kind in TOTAL_LIMITS}
    return {"gets": len(intents), "urls": set(attempts), "attempts": attempts, "verified": verified,
            "zip_bytes": used["zip"], "checksum_bytes": used["checksum"]}


def retry_state(events=None):
    events = read_events(EVIDENCE_DIR_RETRY / "network-ledger.jsonl") if events is None else events
    intents, settles, dates, files, attempts, terminal = {}, {}, {}, {}, Counter(), set()
    complete, commit = False, None
    for row in events:
        event = row["event"]
        if complete:
            raise StopTask("event after run_complete")
        if event == "run_start":
            if commit is not None or intents:
                raise StopTask("duplicate run_start")
            commit = row["commit"]
        elif event == "intent":
            day, kind = allowed_url(row["url"])
            if (row["id"] != f"R{len(intents)+1:04d}" or row["url"] in terminal
                    or row["day"] != day or row["kind"] != kind):
                raise StopTask("invalid retry intent sequence")
            attempts[row["url"]] += 1
            if row["attempt"] != attempts[row["url"]] or row["attempt"] > MAX_ATTEMPTS:
                raise StopTask("invalid retry attempt")
            if row["reserved_bytes"] != (ZIP_LIMIT if kind == "zip" else CHECKSUM_LIMIT) + 1:
                raise StopTask("invalid retry reservation")
            intents[row["id"]] = row
        elif event == "settle":
            ident = row["id"]
            if ident not in intents or ident in settles or row["result"] not in ("accepted", "retry", "terminal", "stop"):
                raise StopTask("invalid retry settle")
            if not isinstance(row["bytes"], int) or row["bytes"] < 0:
                raise StopTask("invalid settled byte count")
            settles[ident] = row
            if row["result"] != "retry":
                terminal.add(intents[ident]["url"])
            files[row["file"]["path"]] = row["file"]
        elif event == "verified":
            if row["day"] in dates:
                raise StopTask("repeated terminal date")
            dates[row["day"]] = row["summary"]
            for item in row["files"]:
                if item["partial_path"] not in files:
                    raise StopTask("verified file lacks accepted payload")
                files.pop(item["partial_path"])
                files[item["path"]] = {k: v for k, v in item.items() if k != "partial_path"}
        elif event == "file_move":
            if row["source"] not in files:
                raise StopTask("move source absent from ledger")
            files.pop(row["source"])
            files[row["file"]["path"]] = row["file"]
        elif event == "day_terminal":
            if row["day"] in dates:
                raise StopTask("repeated terminal date")
            dates[row["day"]] = row["summary"]
        elif event == "run_complete":
            complete = True
        elif event != "checksum_compare":
            raise StopTask("unknown retry ledger event")
    unsettled = set(intents) - set(settles)
    used = {kind: sum(settles[i]["bytes"] if i in settles else r["reserved_bytes"]
                      for i, r in intents.items() if r["kind"] == kind) for kind in TOTAL_LIMITS}
    return {"gets": len(intents), "urls": set(attempts), "attempts": attempts, "intents": intents,
            "settles": settles, "unsettled": unsettled, "terminal": terminal, "files": files,
            "dates": dates, "complete": complete, "commit": commit,
            "zip_bytes": used["zip"], "checksum_bytes": used["checksum"]}


def counters(prior, state):
    return {"new_gets": state["gets"], "cumulative_gets": prior["gets"] + state["gets"],
            "cumulative_distinct_urls": len(prior["urls"] | state["urls"]),
            **{prefix + kind + "_bytes": state[kind + "_bytes"] + (prior[kind + "_bytes"] if prefix == "cumulative_" else 0)
               for prefix in ("new_", "cumulative_") for kind in TOTAL_LIMITS}}


def request_budget(url, prior, state):
    day, kind = allowed_url(url)
    if day in prior["verified"] or state["unsettled"] or state["complete"] or url in state["terminal"]:
        raise StopTask("request is not eligible or execution cannot restart")
    attempt = state["attempts"].get(url, 0) + 1
    reserve = (ZIP_LIMIT if kind == "zip" else CHECKSUM_LIMIT) + 1
    remaining = TOTAL_LIMITS[kind] - prior[kind + "_bytes"] - state[kind + "_bytes"]
    if (prior["gets"] + state["gets"] + 1 > TOTAL_GET_LIMIT or state["gets"] + 1 > NEW_GET_LIMIT
            or attempt > MAX_ATTEMPTS or len(prior["urls"] | state["urls"] | {url}) > DISTINCT_URL_LIMIT
            or remaining < reserve):
        raise StopTask("request would exceed the D-043 cumulative or new budget")
    return attempt, reserve, remaining


def check_resources():
    check_memory()
    probe = VERIFY_DIR_RETRY
    while not probe.exists():
        probe = probe.parent
    if shutil.disk_usage(probe).free < DISK_RESERVE:
        raise StopTask("free disk below 8 GiB")
    if evidence_size() > EVIDENCE_LIMIT:
        raise StopTask("evidence directory exceeds 100 MiB")


def old_checksums():
    result = {}
    for day, (name, expected) in OLD_CHECKSUMS.items():
        payload = (D041_VERIFY_RO / "failed" / name).read_bytes()
        if sha256(payload) != expected:
            raise StopTask("old CHECKSUM bytes differ: " + day)
        checksum_digest(payload, "BTCUSDT-1m-" + day + ".zip")
        result[day] = payload
    return result


def is_refusal(exc):
    """Refusals must propagate without any subsequent write or fallback."""
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, BaseException):
        return is_refusal(exc.reason)
    return (isinstance(exc, (PermissionError, ssl.SSLCertVerificationError, ssl.CertificateError))
            or getattr(exc, "winerror", None) in (5, 10013)
            or getattr(exc, "errno", None) in (errno.EACCES, errno.EPERM))


def file_entry(path, payload):
    return {"path": path.relative_to(VERIFY_DIR_RETRY).as_posix(), "bytes": len(payload), "sha256": sha256(payload)}


def download_url(day, kind, prior, old, *, expected_sha256=None, opener=None, clock=time.monotonic, sleep=time.sleep):
    if kind not in TOTAL_LIMITS:
        raise StopTask("invalid payload kind")
    url = urls_for_day(day)[0 if kind == "checksum" else 1]
    opener = _open_response if opener is None else opener
    while True:
        state = retry_state()
        attempt, reserve, remaining = request_budget(url, prior, state)
        check_resources()
        allowed_url(url)
        ident = f"R{state['gets']+1:04d}"
        append_event({"event": "intent", "id": ident, "url": url, "day": day, "kind": kind,
                      "attempt": attempt, "d041_prior_attempts": prior["attempts"].get(url, 0),
                      "reserved_bytes": reserve, "utc": utc_now()})
        started = clock()
        outcome, payload, error = "accepted", b"", None
        status = final = length = modified = equal = None
        try:
            try:
                response = opener(urllib.request.Request(url, method="GET"), timeout=30)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                status = getattr(response, "status", getattr(response, "code", None))
                final = response.geturl()
                modified = response.headers.get("Last-Modified")
                if final != url or status is None or 300 <= status < 400:
                    raise StopTask("redirect or invalid final URL")
                if status not in (200, 404, 429) and not 500 <= status <= 599:
                    raise StopTask(f"HTTP status outside allowed classes: {status}")
                payload, length = read_response(response, kind, remaining, started + 120, clock=clock)
            if status == 404:
                if kind == "checksum" and day in old:
                    raise StopTask("previously accepted CHECKSUM returned 404")
                outcome = "terminal"
            elif status == 429 or 500 <= status <= 599:
                raise RetryableTransfer(f"HTTP {status}")
            elif kind == "checksum":
                checksum_digest(payload, f"BTCUSDT-1m-{day}.zip")
                if day in old:
                    equal = payload == old[day]
                    if not equal:
                        raise StopTask("new CHECKSUM bytes differ from previously accepted bytes")
            elif expected_sha256 is None or sha256(payload) != expected_sha256:
                raise StopTask("full-length ZIP SHA-256 differs from CHECKSUM")
        except BaseException as exc:
            if is_refusal(exc):
                raise
            payload = getattr(exc, "payload", exc.partial if isinstance(exc, http.client.IncompleteRead) else payload)
            length = getattr(exc, "content_length", length)
            error = exc
            outcome = "retry" if classify_network_error(exc) == "retry" else "stop"
            if outcome == "retry" and attempt == MAX_ATTEMPTS:
                outcome = "terminal"
        # Filesystem failures below propagate unchanged; no fallback write or retry.
        partial = safe_path(VERIFY_DIR_RETRY / ".partial" / (ident + ".bin"), VERIFY_DIR_RETRY)
        write_bytes(partial, payload, root=VERIFY_DIR_RETRY)
        target = partial if outcome == "accepted" else VERIFY_DIR_RETRY / "failed" / (ident + ".bin")
        if equal is not None:
            append_event({"event": "checksum_compare", "id": ident, "day": day, "byte_equal": equal,
                          "old_sha256": sha256(old[day]), "new_sha256": sha256(payload), "utc": utc_now()})
        append_event({"event": "settle", "id": ident, "utc": utc_now(), "http_status": status,
                      "final_url": final, "content_length": length, "bytes": len(payload), "sha256": sha256(payload),
                      "last_modified": modified, "error_category": type(error).__name__ if error else None,
                      "error": str(error) if error else None, "result": outcome, "file": file_entry(target, payload)})
        if target != partial:
            move_new(partial, target)
        if outcome == "stop":
            # Network stops are not recoverable. Preserve the original exception text.
            failure = StopTask(str(error))
            failure.original_error_type = type(error).__name__
            failure.url = url
            raise failure from error
        if outcome == "accepted":
            return {"status": "accepted", "path": partial, "payload": payload, "sha256": sha256(payload)}
        if outcome == "terminal":
            return {"status": "unverifiable_missing_file" if status == 404 else "unverifiable_network",
                    "error": str(error) if error else "HTTP 404"}
        sleep(BACKOFF_SECONDS[attempt - 1])


def retire_partial(path):
    target = VERIFY_DIR_RETRY / "failed" / path.name
    append_event({"event": "file_move", "source": path.relative_to(VERIFY_DIR_RETRY).as_posix(),
                  "file": file_entry(target, path.read_bytes()), "utc": utc_now()})
    move_new(path, target)


def acquire_day(day, prior, old, **transport):
    ensure_day(day)
    if day in retry_state()["dates"]:
        raise StopTask("date is already terminal")
    checksum = download_url(day, "checksum", prior, old, **transport)
    if checksum["status"] != "accepted":
        summary = {"day": day, **checksum}
        append_event({"event": "day_terminal", "day": day, "summary": summary, "utc": utc_now()})
        return summary
    expected = checksum_digest(checksum["payload"], f"BTCUSDT-1m-{day}.zip")
    archive = download_url(day, "zip", prior, old, expected_sha256=expected, **transport)
    if archive["status"] != "accepted":
        retire_partial(checksum["path"])
        summary = {"day": day, **archive, "checksum_sha256": checksum["sha256"]}
        append_event({"event": "day_terminal", "day": day, "summary": summary, "utc": utc_now()})
        return summary
    summary = {"day": day, "status": "verified", "zip_sha256": archive["sha256"],
               "checksum_sha256": checksum["sha256"], "csv_sha256": None, "rows": 0, "unit": None, "anomalies": []}
    try:
        payload = data.extract_csv(archive["payload"], f"BTCUSDT-1m-{day}.zip")
        parsed = parse_daily_csv(payload, day)
        summary.update(csv_sha256=sha256(payload), rows=len(parsed.rows), unit=parsed.unit, anomalies=parsed.anomalies)
        if parsed.abnormal:
            summary["status"] = "unverifiable_abnormal_file"
    except (data.DataValidationError, ValueError, UnicodeError, csv.Error, zipfile.BadZipFile,
            RuntimeError, NotImplementedError, EOFError, zlib.error) as exc:
        if isinstance(exc, StopTask):
            raise
        summary.update(status="unverifiable_abnormal_file", error=f"{type(exc).__name__}: {exc}",
                       anomalies=[_anomaly(day, "daily", None, "", "unreadable_archive: " + str(exc))])
    files = []
    for kind, item in (("checksum", checksum), ("zip", archive)):
        target = VERIFY_DIR_RETRY / (f"BTCUSDT-1m-{day}.zip" + (".CHECKSUM" if kind == "checksum" else ""))
        if safe_path(target, VERIFY_DIR_RETRY).exists():
            raise StopTask("final archive already exists")
        files.append({**file_entry(target, item["payload"]), "partial_path": item["path"].relative_to(VERIFY_DIR_RETRY).as_posix()})
    append_event({"event": "verified", "day": day, "summary": summary, "files": files, "utc": utc_now()})
    for item in files:
        move_new(VERIFY_DIR_RETRY / item["partial_path"], VERIFY_DIR_RETRY / item["path"])
    return summary


FROZEN = {
    "docs/phase3a-analysis-plan.md": "fea667f7689d2c45ab0697b0dd2e8877cf0894ae27e4a5f0277efae48e50cb3c",
    "configs/history-extension.toml": "e9cbd19d884d046199f468f29e912cd6a9fbec1852e90ffc125b86d8df0bd9af",
    "docs/confirmatory-protocol-v1-history-addendum.md": "a4a99cfbfb9e33105d98ad44b439a57020a050b64b46b5a8e4a0bbc4b204fc07",
    "docs/confirmatory-protocol-v1.md": "38ef1aae687b6cd1a9b8b59709b84047250e2a9f90396cb9089244133b38f1b9",
    "docs/phase3a-plan-manifest.json": "ceb56c5683ff682240563c87991c96298b3f6c45baa93eee03134efc7d34c54a",
    "docs/phase3a-download-manifest-draft.json": "1a865966ff55094a5fa54fc245ed7f0e7543360fbd8a0a4da8459dda306ab0f6",
    "data/processed/binance/spot/BTCUSDT/1m/historical/historical-manifest.json": "71920d90d95a3d6600db3b57540865949b8784ce8753b0dccfbea93d7bb1fa02",
    "src/quantforge/features/__init__.py": "857819a0c3c718cf2b1100ea72995db989ed149f65d1ad3d5e4cd3eda1fcbec1",
    "src/quantforge/labels/__init__.py": "b968218639387cb82ab1beccef5c5bf3ed3674e5975c3d2b25cb4d318037746e",
    "src/quantforge/datasets/research_splits.py": "22c862f6e8e232dedfe17c3a9832f5b7d78737de1593d6b904e2c29c5949919c",
    "src/quantforge/datasets/splits.py": "3459e5be5185eee2638709c3db4250e150d8e1deff140d1459882cb5257d93cd",
    "src/quantforge/datasets/window_splits.py": "24de7804527d3dc805f46ee969d17267927e345b94af434303cd19793675fc22",
    "src/quantforge/models/baselines.py": "a9269ca7f6147f653f608051746304596e898f49ec30f9e1714567e25a1ded83",
    "src/quantforge/temporal.py": "6c262bbd1c77e6696c7d310eb31b321f3f27ffeea02fbd64a70f3dcee9bd648c",
}

D041_EVIDENCE_SHA = {
    "network-ledger.jsonl": "93c0f0bb6faf942011710aab029bf8bec5eb27822987fbe0c41efa7e0af47177",
    "download-summary.json": "525f1420aa789baa9c24fa20bce317d36147d45663013262128b9f76ff38b7fb",
    "verify-dir-inventory.json": "2cd0e6d55743f53d50a52ae7bd5b21c282398ad483e6901d6fdd0ac325c9c76d",
}

D041_DERIVED = ("minute-flags.csv.gz", 15_109_550, "71283ab3b619bb370f22dae62db5111ff5f5accb3d5e226b6b7e068372ebf984")

D039_SUMMARY_SHA = "f82ec90ec19b91d6ec8fa31450a618a5d27f26863c29e39a0f1963f45ce95d09"

PRICE_FIELDS = ("open", "high", "low", "close")


def _bp(a, b):
    return Decimal(0) if a == b == 0 else ((b - a) / a * Decimal(10000) if a else Decimal("Infinity"))


def in_cohort(row, cohort):
    return (cohort == "combined" or row["source"] == cohort
            or cohort == "census_non_selected" and row["source"] in ("D-041", "D-043"))


def source_for_day(day):
    return "D-039" if day in D039_DAYS else "D-043" if day in RETRY_DAYS else "D-041"


def snapshot_tree(root, *, hashes=True):
    rows = []
    paths = [root] if root.is_file() else sorted(root.rglob("*"))
    for path in paths:
        if not path.is_file():
            continue
        before = path.stat()
        row = {"path": str(path), "bytes": before.st_size, "mtime_ns": before.st_mtime_ns}
        if hashes:
            row["sha256"] = sha256(path.read_bytes())
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise StopTask("protected file changed during snapshot")
        rows.append(row)
    return rows


def isolation_snapshot():
    return {
        "data": snapshot_tree(ROOT / "data"),
        "d041_external": snapshot_tree(D041_VERIFY_RO),
        "d041_evidence": snapshot_tree(D041_EVIDENCE_RO),
        "d041_code": {name: snapshot_tree(ROOT / name) for name in (
            "src/quantforge/archive_verification_full.py", "scripts/verify_daily_archives_full.py",
            "tests/test_archive_verification_full.py", "docs/phase3a-full-verification-record.md")},
        "d039_external": snapshot_tree(D039_DIR_RO),
        "d039_evidence": snapshot_tree(D039_EVIDENCE_RO),
        "frozen": {name: sha256((ROOT / name).read_bytes()) for name in FROZEN},
        "other_worktrees": {str(root): {leaf: snapshot_tree(root / leaf, hashes=False)
                            for leaf in (".venv", "results", "data")} for root in OTHER_WORKTREES_RO},
    }


def assert_isolation():
    before = (EVIDENCE_DIR_RETRY / "isolation-before.json").read_bytes()
    after = isolation_snapshot()
    if sha256(before) != sha256(json_bytes(after)):
        raise StopTask("protected isolation snapshot changed")
    return after


def inventory(*, partial=False, derived=False):
    rows = []
    for path in sorted(VERIFY_DIR_RETRY.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(VERIFY_DIR_RETRY)
        if rel.parts[0] == "derived" and not derived:
            continue
        if rel.parts[0] == ".partial" and not partial:
            raise StopTask("retry .partial is not empty")
        rows.append(file_entry(path, safe_path(path, VERIFY_DIR_RETRY).read_bytes()))
    return rows


def verify_inventory(state, *, recorded=False):
    if state["unsettled"]:
        raise StopTask("unsettled retry intent; restart forbidden")
    actual = inventory()
    if actual != sorted(state["files"].values(), key=lambda r: r["path"]):
        raise StopTask("retry inventory differs from ledger")
    if recorded and actual != json.loads((EVIDENCE_DIR_RETRY / "verify-dir-inventory.json").read_bytes()):
        raise StopTask("retry inventory differs from recorded inventory")
    return actual


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, encoding="utf-8").strip()


def require_r1(*, initial=False):
    """Runtime identity guard; the initial fixed precheck is never replaced."""
    if ROOT != Path(r"D:\zgs_project\QuantForge-3a") or Path.cwd().resolve() != ROOT:
        raise StopTask("wrong worktree")
    if git("branch", "--show-current") != "phase3a-protocol-and-impl":
        raise StopTask("wrong branch")
    if git("show", "-s", "--format=%s", "HEAD") != R1_MESSAGE:
        raise StopTask("HEAD is not R1")
    files = set(git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines())
    if files != {"src/quantforge/archive_verification_retry.py", "scripts/verify_daily_archives_retry.py",
                 "tests/test_archive_verification_retry.py"}:
        raise StopTask("R1 file set differs")
    if git("diff", "--name-only") or git("diff", "--cached", "--name-only"):
        raise StopTask("tracked files changed after R1")
    unknown = git("ls-files", "--others", "--exclude-standard").splitlines()
    if any(initial or not p.startswith("docs/phase3a-retry-verification-evidence/") for p in unknown):
        raise StopTask("unexpected untracked file")
    return git("rev-parse", "HEAD")


def verify_runtime_inputs():
    """Recheck D-043 c-i within the authorized acquisition process."""
    check_resources()
    for name, expected in FROZEN.items():
        if sha256((ROOT / name).read_bytes()) != expected:
            raise StopTask("frozen fingerprint differs: " + name)
    manifest = json.loads(MANIFEST_RO.read_bytes())
    if len(manifest["months"]) != 68 or len(snapshot_tree(ROOT / "data", hashes=False)) != 275:
        raise StopTask("Gate 1 file/month count changed")
    for entry in manifest["months"]:
        month = research_policy.ensure_historical_month(entry["month"])
        for path, expected in ((RAW_MONTHLY_RO / (month + ".zip"), entry["zip_sha256"]),
                               (MANIFEST_RO.parent / (month + ".parquet"), entry["parquet_sha256"])):
            if sha256(path.read_bytes()) != expected:
                raise StopTask("Gate 1 hash changed: " + str(path))
    if git("diff", "main", "--", "src/quantforge/archive_verification_full.py", "scripts/verify_daily_archives_full.py",
           "tests/test_archive_verification_full.py", "docs/phase3a-full-verification-evidence", "docs/phase3a-full-verification-record.md"):
        raise StopTask("D-041 differs from main")
    for name, expected in D041_EVIDENCE_SHA.items():
        if sha256((D041_EVIDENCE_RO / name).read_bytes()) != expected:
            raise StopTask("D-041 evidence hash changed: " + name)
    for root, evidence, field, count in ((D041_VERIFY_RO, D041_EVIDENCE_RO, "path", 4217),
                                          (D039_DIR_RO, D039_EVIDENCE_RO, "name", 23)):
        expected = {r[field].replace("\\", "/"): (r["bytes"], r["sha256"])
                    for r in json.loads((evidence / "verify-dir-inventory.json").read_bytes())}
        actual = {p.relative_to(root).as_posix(): (p.stat().st_size, sha256(p.read_bytes()))
                  for p in sorted(root.rglob("*")) if p.is_file() and "derived" not in p.relative_to(root).parts}
        if len(actual) != count or actual != expected:
            raise StopTask("old inventory changed: " + str(root))
    name, size, digest = D041_DERIVED
    derived = [p for p in (D041_VERIFY_RO / "derived").rglob("*") if p.is_file()]
    if (len(derived) != 1 or derived[0].name != name or derived[0].stat().st_size != size
            or sha256(derived[0].read_bytes()) != digest or list((D041_VERIFY_RO / ".partial").iterdir())):
        raise StopTask("D-041 derived or partial directory changed")
    if sha256((D039_EVIDENCE_RO / "download-summary.json").read_bytes()) != D039_SUMMARY_SHA:
        raise StopTask("D-039 summary changed")
    old = old_checksums()
    neighbours = {"2020-02-12", "2020-02-25", "2020-12-08", "2020-12-10", "2024-03-16", "2024-03-18"}
    for name in ("conflict-minutes.csv", "timestamp-anomalies.csv"):
        if any(r["day"] in neighbours for r in csv_rows((D041_EVIDENCE_RO / name).read_bytes())):
            raise StopTask("neighbour day has a pre-existing conflict or time anomaly")
    return manifest, old


def run_retry():
    started, start_utc = time.monotonic(), utc_now()
    commit = require_r1(initial=True)
    if VERIFY_DIR_RETRY.exists() or EVIDENCE_DIR_RETRY.exists():
        raise StopTask("retry outputs already exist; restart forbidden")
    _, old = verify_runtime_inputs()
    prior = prior_state()
    if ({k: prior[k] for k in BASE_COUNTS if k != "distinct_urls"}
            != {k: v for k, v in BASE_COUNTS.items() if k != "distinct_urls"}
            or len(prior["urls"]) != BASE_COUNTS["distinct_urls"]):
        raise StopTask("D-041 accounting differs from the fixed baseline")
    if (len(RETRY_DAYS) != 14 or len(URLS) != 28 or not set(RETRY_DAYS) <= set(D041_DAYS)
            or set(RETRY_DAYS) & (prior["verified"] | set(D039_DAYS))):
        raise StopTask("retry scope invariant failed")
    before = isolation_snapshot()
    safe_path(VERIFY_DIR_RETRY, VERIFY_DIR_RETRY).mkdir(parents=True, exist_ok=False)
    write_json("isolation-before.json", before)
    append_event({"event": "run_start", "commit": commit, "utc": utc_now()})
    for day in RETRY_DAYS:
        result = acquire_day(day, prior, old)
        emit({"day": day, "status": result["status"], **counters(prior, retry_state())})
    state = retry_state()
    actual = verify_inventory(state)
    if set(state["dates"]) != set(RETRY_DAYS):
        raise StopTask("incomplete retry date coverage")
    if state["urls"] == URLS and len(prior["urls"] | state["urls"]) != DISTINCT_URL_LIMIT:
        raise StopTask("full retry URL union differs from 4,118")
    assert_isolation()
    summary = {"dates": state["dates"], "counters": counters(prior, state),
               "selection_note": SELECTION_NOTES["D-043"], "commit": commit,
               "retry_count": sum(max(0, n - 1) for n in state["attempts"].values())}
    write_json("retry-summary.json", summary)
    consistency = checksum_consistency(state, old, read_events(EVIDENCE_DIR_RETRY / "network-ledger.jsonl"))
    write_json("checksum-consistency.json", consistency)
    write_json("verify-dir-inventory.json", actual)
    write_json("runtime-retry.json", runtime(started, start_utc))
    append_event({"event": "run_complete", "utc": utc_now(), "commit": commit})
    return summary


def checksum_consistency(state, old, events):
    comparisons = {r["day"]: r for r in events if r["event"] == "checksum_compare"}
    consistency = []
    for day in RETRY_DAYS:
        result = state["dates"].get(day, {"status": "not_fetched"})
        previous = old.get(day)
        accepted = result.get("checksum_sha256")
        comparison = comparisons.get(day)
        if previous and accepted and (not comparison or not comparison["byte_equal"]
                                     or comparison["new_sha256"] != accepted):
            raise StopTask("accepted old CHECKSUM lacks byte comparison evidence")
        consistency.append({"day": day,
                            "old_file": str(D041_VERIFY_RO / "failed" / OLD_CHECKSUMS[day][0]) if day in OLD_CHECKSUMS else None,
                            "old_bytes": len(previous) if previous else None,
                            "old_sha256": sha256(previous) if previous else None,
                            "new_sha256": comparison["new_sha256"] if comparison else accepted,
                            "byte_equal": comparison["byte_equal"] if comparison else None,
                            "status": "accepted" if accepted else result["status"]})
    return consistency


def csv_rows(payload):
    return list(csv.DictReader(io.StringIO(payload.decode("utf-8"), newline="")))


WINDOWS = (("2020-02-12", "2020-02-25"), ("2020-12-08", "2020-12-10"), ("2024-03-16", "2024-03-18"))
CHANGED_MONTHS = {"2020-02", "2020-12", "2024-03"}


def overlapping(row):
    return any(int(row["start_us"]) < day_start_us(b) + DAY_US and int(row["end_us"]) >= day_start_us(a)
               for a, b in WINDOWS)


def allowed_delta(name, row, *, added):
    if name == "daily-integrity.csv":
        return row["day"] in RETRY_DAYS
    if name in ("conflict-minutes.csv", "conflict-fields.csv", "timestamp-anomalies.csv"):
        return added and row["day"] in RETRY_DAYS
    if name in ("intervals.csv", "consistent-gaps.csv", "episodes.csv"):
        return overlapping(row)
    if name in ("summary-by-month.csv", "summary-by-year.csv", "owner-categories.csv"):
        if row["source"] == "D-039":
            return False
        if row["source"] in ("D-043", "census_non_selected"):
            return added
        if name == "owner-categories.csv":
            period, value = row["period"], row["value"]
        else:
            period = "month" if name == "summary-by-month.csv" else "year"
            value = row[period]
        return value in (set(RETRY_DAYS) if period == "day" else CHANGED_MONTHS if period == "month" else {"2020", "2024"})
    if name == "contamination-by-month.csv":
        return row["decision_month"] in CHANGED_MONTHS
    if name == "gw-instrument-exposure.csv":
        return row["evaluation_month"] in {"2021-01", "2024-04"}
    raise StopTask("unexpected delta artifact: " + name)


def row_identity(name, row):
    keys = {
        "daily-integrity.csv": ("day",), "conflict-minutes.csv": ("day", "open_time_us"),
        "conflict-fields.csv": ("day", "open_time_us", "field"),
        "timestamp-anomalies.csv": ("day", "side", "row_number", "open_time_us", "reason"),
        "intervals.csv": ("start_us", "end_us"), "consistent-gaps.csv": ("start_us", "end_us"),
        "episodes.csv": ("start_us", "end_us", "conflict_minutes"),
        "summary-by-month.csv": ("month", "source"), "summary-by-year.csv": ("year", "source"),
        "owner-categories.csv": ("period", "value", "source"),
        "contamination-by-month.csv": ("decision_month", "horizon", "scope", "population"),
        "gw-instrument-exposure.csv": ("evaluation_month",),
    }[name]
    return {k: row[k] for k in keys}


def delta_rows(name, old_rows, new_rows):
    if name == "episodes.csv":
        keys = ("start_us", "end_us", "conflict_minutes")
        old_rows = [{k: r[k] for k in keys} for r in old_rows]
        new_rows = [{k: r[k] for k in keys} for r in new_rows]
    encode = lambda r: json.dumps(r, sort_keys=True, ensure_ascii=True)
    old, new = Counter(map(encode, old_rows)), Counter(map(encode, new_rows))
    changes = []
    for added, diff in ((False, old - new), (True, new - old)):
        for text, count in sorted(diff.items()):
            row = json.loads(text)
            permitted = allowed_delta(name, row, added=added)
            changes.append({"key": row_identity(name, row), "old": None if added else row,
                            "new": row if added else None, "count": count, "allowed": permitted,
                            "allowed_scope": "D-043 section 2.8: " + name if permitted else None})
    return {"changes": changes, "all_within_allowed": all(r["allowed"] for r in changes)}


def flag_delta(old_payload, new_payload):
    changes = []
    with gzip.GzipFile(fileobj=io.BytesIO(old_payload)) as old, gzip.GzipFile(fileobj=io.BytesIO(new_payload)) as new:
        old_text, new_text = io.TextIOWrapper(old, encoding="utf-8"), io.TextIOWrapper(new, encoding="utf-8")
        before, after = csv.DictReader(old_text), csv.DictReader(new_text)
        if before.fieldnames != after.fieldnames:
            raise StopTask("minute flag schema changed")
        windows = [(day_start_us(d) - 15 * MINUTE_US, day_start_us(d) + DAY_US + 60 * MINUTE_US) for d in RETRY_DAYS]
        for a, b in itertools.zip_longest(before, after):
            if a == b:
                continue
            same_key = a is not None and b is not None and a["open_time_us"] == b["open_time_us"]
            stamp = int((a or b)["open_time_us"])
            allowed = same_key and any(lo <= stamp < hi for lo, hi in windows)
            changes.append({"key": {"open_time_us": stamp},
                            "old_sha256": sha256(json_bytes(a)), "new_sha256": sha256(json_bytes(b)),
                            "allowed": allowed, "allowed_scope": "retry day -15/+60 minutes" if allowed else None})
    return {"changes": changes, "all_within_allowed": all(r["allowed"] for r in changes)}


def delta_artifacts(artifacts):
    out = {}
    for name, payload in sorted(artifacts.items()):
        if name.startswith("derived/"):
            out[name] = flag_delta((D041_VERIFY_RO / "derived/minute-flags.csv.gz").read_bytes(), payload)
        elif name.endswith(".csv"):
            out[name] = delta_rows(name, csv_rows((D041_EVIDENCE_RO / name).read_bytes()), csv_rows(payload))
        else:
            old = (D041_EVIDENCE_RO / name).read_bytes()
            out[name] = {"old_sha256": sha256(old), "new_sha256": sha256(payload),
                         "allowed_scope": "aggregate JSON changes by design", "all_within_allowed": True}
    return {"all_within_allowed": all(r["all_within_allowed"] for r in out.values()), "files": out}


def block_network(*args, **kwargs):
    raise StopTask("offline operation attempted network access")


def disable_network():
    socket.create_connection = block_network
    socket.socket.connect = block_network
    urllib.request.urlopen = block_network
    urllib.request.OpenerDirector.open = block_network


def offline_inputs():
    disable_network()
    commit = require_r1()
    state = retry_state()
    if state["commit"] != commit or not state["complete"] or list(EVIDENCE_DIR_RETRY.glob("stop-*.json")):
        raise StopTask("offline phase requires unchanged R1 and a completed unstopped retry")
    verify_inventory(state, recorded=True)
    if sha256(D041_LEDGER_RO.read_bytes()) != D041_LEDGER_SHA:
        raise StopTask("D-041 ledger changed")
    check_resources()
    assert_isolation()
    retry_summary = json.loads((EVIDENCE_DIR_RETRY / "retry-summary.json").read_bytes())
    if retry_summary["dates"] != state["dates"] or retry_summary["counters"] != counters(prior_state(), state):
        raise StopTask("retry summary differs from ledger")
    d041 = json.loads((D041_EVIDENCE_RO / "download-summary.json").read_bytes())["dates"]
    d039 = json.loads((D039_EVIDENCE_RO / "download-summary.json").read_bytes())["dates"]
    return json.loads(MANIFEST_RO.read_bytes()), {**d041, **d039, **state["dates"]}


def prepared_artifacts(artifacts):
    out = {}
    for name, payload in sorted(artifacts.items()):
        if len(payload) > 20 * 1024**2 and not name.endswith(".gz"):
            name, payload = name + ".gz", gzip.compress(payload, mtime=0)
        out[name] = payload
    return out


def runtime(started, start_utc):
    return {"started_at_utc": start_utc, "finished_at_utc": utc_now(),
            "elapsed_seconds": time.monotonic() - started, "peak_process_bytes": check_memory(),
            "disk_free_bytes": shutil.disk_usage(VERIFY_DIR_RETRY).free,
            "evidence_bytes": evidence_size(), "research_computations": 0}


def final_artifacts(manifest, statuses):
    artifacts = build_final_v2(manifest, statuses)
    delta = delta_artifacts(artifacts)
    if not delta["all_within_allowed"]:
        failure = StopTask("v2 changes extend outside the predeclared allowed ranges")
        failure.delta = delta
        raise failure
    artifacts["delta-vs-d041.json"] = json_bytes(delta)
    return prepared_artifacts(artifacts)


def finalize():
    started, start_utc = time.monotonic(), utc_now()
    manifest, statuses = offline_inputs()
    artifacts = final_artifacts(manifest, statuses)
    after = assert_isolation()
    projected = evidence_size() + sum(len(b) for n, b in artifacts.items() if not n.startswith("derived/")) + len(json_bytes(after))
    if projected > EVIDENCE_LIMIT:
        raise StopTask("final evidence would exceed 100 MiB")
    index = {}
    for name, payload in artifacts.items():
        external = name.startswith("derived/")
        root = VERIFY_DIR_RETRY if external else EVIDENCE_DIR_RETRY
        rel = name if external else "final-v2/" + name
        write_bytes(root / rel, payload, root=root)
        index[name] = {"root": "external" if external else "evidence", "path": rel,
                       "bytes": len(payload), "sha256": sha256(payload)}
        if name == "owner-categories.csv":
            index[name]["cohort_notes"] = SELECTION_NOTES
    write_json("artifact-index.json", index)
    write_json("isolation-after.json", after)
    write_json("runtime-finalize.json", {**runtime(started, start_utc), "network_requests": 0})
    emit({"status": "finalized", "artifacts": len(index), "all_within_allowed": True})


def replay_check():
    started, start_utc = time.monotonic(), utc_now()
    manifest, statuses = offline_inputs()
    artifacts = final_artifacts(manifest, statuses)
    index = json.loads((EVIDENCE_DIR_RETRY / "artifact-index.json").read_bytes())
    if set(index) != set(artifacts):
        raise StopTask("replay artifact set differs")
    checks = {}
    for name, payload in artifacts.items():
        row = index[name]
        root = VERIFY_DIR_RETRY if row["root"] == "external" else EVIDENCE_DIR_RETRY
        stored = safe_path(root / row["path"], root).read_bytes()
        if stored != payload or sha256(payload) != row["sha256"] or len(payload) != row["bytes"]:
            raise StopTask("replay bytes differ: " + name)
        checks[name] = {"sha256": sha256(payload), "byte_identical": True}
    assert_isolation()
    write_json("determinism-check.json", {"passed": True, "artifacts": checks, "isolation_unchanged": True})
    write_json("runtime-replay.json", {**runtime(started, start_utc), "network_requests": 0})
    emit({"status": "replay_passed", "artifacts": len(checks)})


def write_stop(exc, phase):
    name = datetime.now(timezone.utc).strftime("stop-%Y%m%dT%H%M%SZ.json")
    write_json(name, {"phase": phase, "utc": utc_now(), "error_type": type(exc).__name__,
                      "reason": str(exc), "original_error_type": getattr(exc, "original_error_type", None),
                      "url": getattr(exc, "url", None), "delta": getattr(exc, "delta", None)})
    write_json("isolation-at-stop.json", isolation_snapshot())
    write_json("inventory-at-stop.json", inventory(partial=True, derived=True))
    return name


def monthly_days(raw_root: Path, entry: dict) -> dict[str, ParsedDay]:
    month = research_policy.ensure_historical_month(entry["month"])
    payload = (raw_root / f"{month}.zip").read_bytes()
    if sha256(payload) != entry["zip_sha256"]:
        raise StopTask(f"monthly ZIP SHA differs: {month}")
    csv_payload = data.extract_csv(payload, f"BTCUSDT-1m-{month}.zip")
    if sha256(csv_payload) != entry["csv_sha256"]:
        raise StopTask("monthly CSV hash changed")
    grouped = {day: [] for day in ALL_DAYS if day.startswith(month)}
    for row in _raw_rows(csv_payload):
        if re.fullmatch(r"[0-9]+", row[0]) is None:
            raise StopTask("monthly open timestamp is not an integer")
        raw = int(row[0])
        stamp = raw * (1000 if unit_of(raw) == "ms" else 1)
        day = utc_us(stamp)[:10]
        if day not in grouped:
            raise StopTask("monthly ZIP contains a row outside its month")
        grouped[day].append(row)
    result = {}
    for day, rows in grouped.items():
        if not rows:
            result[day] = ParsedDay({}, "ms" if day < "2025-01-01" else "us", [], False)
        else:
            buffer = io.StringIO(newline="")
            csv.writer(buffer, lineterminator="\n").writerows(rows)
            result[day] = parse_daily_csv(buffer.getvalue().encode("utf8"), day, "monthly")
            if result[day].abnormal:
                raise StopTask(f"verified monthly archive has abnormal rows: {day}")
    return result

def load_daily(day: str, status: dict, source: str) -> tuple[ParsedDay | None, dict]:
    summary = {"daily_zip_sha256": status.get("zip_sha256", status.get("sha256")),
               "daily_checksum_sha256": status.get("checksum_sha256"), "daily_csv_sha256": status.get("csv_sha256")}
    if status["status"] not in ("verified", "passed", "unverifiable_abnormal_file"):
        return None, summary
    root = {"D-039": D039_DIR_RO, "D-041": D041_VERIFY_RO, "D-043": VERIFY_DIR_RETRY}[source]
    name = f"BTCUSDT-1m-{day}.zip"
    z = (root / name).read_bytes()
    c = (root / (name + ".CHECKSUM")).read_bytes()
    digest = sha256(z)
    if digest != checksum_digest(c, name) or digest != summary["daily_zip_sha256"]:
        raise StopTask(f"saved daily checksum chain changed: {day}")
    if summary["daily_checksum_sha256"] and sha256(c) != summary["daily_checksum_sha256"]:
        raise StopTask(f"saved CHECKSUM bytes changed: {day}")
    if status["status"] == "unverifiable_abnormal_file":
        # The accepted payload remains unverifiable, but its provenance chain
        # must still be checked during both offline passes.
        return None, summary
    try:
        csv_payload = data.extract_csv(z, name)
    except (data.DataValidationError, zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError) as exc:
        raise StopTask(f"previously verified daily ZIP cannot be parsed: {day}") from exc
    parsed = parse_daily_csv(csv_payload, day)
    if parsed.abnormal:
        raise StopTask(f"previously verified daily CSV became abnormal: {day}")
    summary.update(daily_zip_sha256=digest, daily_checksum_sha256=sha256(c), daily_csv_sha256=sha256(csv_payload))
    if status.get("csv_sha256") and summary["daily_csv_sha256"] != status["csv_sha256"]:
        raise StopTask(f"saved daily CSV hash changed: {day}")
    return parsed, summary

def _linear_quantiles(values: list[Decimal]) -> dict:
    ordered = sorted(values)
    result = {}
    for quantile in (Decimal("0.5"), Decimal("0.9"), Decimal("0.99"), Decimal(1)):
        if not ordered:
            result[str(quantile)] = None
            continue
        location = (len(ordered) - 1) * quantile
        lower = int(location)
        upper = min(lower + 1, len(ordered) - 1)
        result[str(quantile)] = str(ordered[lower] + (ordered[upper] - ordered[lower]) * (location - lower))
    return result

def _histogram(values: list[Decimal], edges: tuple[str, ...]) -> dict:
    limits = tuple(Decimal(value) for value in edges)
    counts = [0] * (len(limits) - 1)
    for value in values:
        for i, (left, right) in enumerate(zip(limits, limits[1:])):
            if left <= value < right or i == len(counts) - 1 and value == right:
                counts[i] += 1
                break
    return {"edges": list(edges), "counts": counts, "final_bin_includes_right": edges[-1] != "Infinity"}

def distribution_rows(conflicts: list[dict], daily_rows=()) -> dict:
    out = {}
    for cohort in COHORTS:
        subset = [r for r in conflicts if in_cohort(r, cohort)]
        metrics = {k: [] for k in (*[f"{p}_signed_bp" for p in PRICE_FIELDS], "price_bp_max", "volume_log_ratio", "trades_log_ratio", "trades_difference", "vol_rel", "trades_rel")}
        zeros = {"volume": 0, "trades": 0}
        selected = [r for r in subset if r["severity"] in ("minor", "moderate", "major")]
        for row in selected:
            m, d = row["monthly_raw_row"].split(","), row["daily_raw_row"].split(",")
            for field in PRICE_FIELDS:
                i = COLUMNS.index(field)
                metrics[field + "_signed_bp"].append(_bp(Decimal(m[i]), Decimal(d[i])))
            for key, index in (("volume", 5), ("trades", 8)):
                a, b = Decimal(m[index]), Decimal(d[index])
                if a == 0 or b == 0:
                    zeros[key] += 1
                else:
                    metrics[key + "_log_ratio"].append((b / a).ln())
            metrics["trades_difference"].append(Decimal(d[8]) - Decimal(m[8]))
            for key in ("price_bp_max", "vol_rel", "trades_rel"):
                metrics[key].append(Decimal(row[key]))
        summary = {key: {"n": len(values), "quantiles": _linear_quantiles(values)} for key, values in metrics.items()}
        for key, edges in (("price_bp_max", ("0", "1", "2", "5", "10", "20", "50", "100", "200", "500", "Infinity")),
                           ("vol_rel", ("0", "0.001", "0.01", "0.05", "0.1", "0.25", "0.5", "0.9", "1.0")),
                           ("trades_rel", ("0", "0.001", "0.01", "0.05", "0.1", "0.25", "0.5", "0.9", "1.0"))):
            summary[key]["histogram"] = _histogram(metrics[key], edges)
        out[cohort] = {"note": CENSUS_NOTE, "numeric_conflict_minutes_minor_moderate_major": len(selected),
                       "placeholder_related_minutes": sum(bool(r["monthly_placeholder"] or r["daily_placeholder"]) for r in subset),
                       "zero_side_log_ratio_cases": zeros, "metrics": summary}
    out["D-043"]["selection_note"] = SELECTION_NOTES["D-043"]
    out["census_non_selected"]["selection_note"] = SELECTION_NOTES["census_non_selected"]
    for cohort in ("D-043", "census_non_selected"):
        subset = [r for r in daily_rows if in_cohort(r, cohort)]
        out[cohort].update(dates=len(subset), verified_dates=sum(r["status"] == "verified" for r in subset))
    return out

def summary_groups(daily_rows: list[dict], period: str) -> list[dict]:
    periods = sorted({r["day"][:{"day": 10, "month": 7, "year": 4}[period]] for r in daily_rows})
    keys = (*CLASSIFICATIONS, *SEVERITIES, "numeric_value_conflict", "time_semantics_events", "time_semantics_rows", "time_semantics_minutes", "unit_incomparable_minutes",
            "unverifiable_missing_file", "unverifiable_network", "unverifiable_abnormal_file", "checksum_failure")
    result = []
    for key in periods:
        for cohort in COHORTS:
            subset = [r for r in daily_rows if r["day"].startswith(key) and (in_cohort(r, cohort))]
            row = {period: key, "source": cohort, "dates": len(subset), "verified_dates": sum(r["status"] == "verified" for r in subset), "note": CENSUS_NOTE}
            if cohort in ("D-043", "census_non_selected"):
                row["note"] = SELECTION_NOTES[cohort]
            row.update({metric: sum(r.get(metric, 0) for r in subset) for metric in keys})
            result.append(row)
    return result

def build_final_v2(manifest: dict, statuses: dict, *, contamination_fn=contamination_artifacts, days=ALL_DAYS) -> dict[str, bytes]:
    """Read each monthly ZIP once and compare all authorized days offline."""
    if set(statuses) != set(days):
        raise StopTask("comparison requires one terminal status for every authorized day")
    # Keep timestamp indices on the fixed historical grid even when a synthetic
    # caller asks for a small subset of dates.
    timeline = ALL_DAYS
    presence = bytearray(len(timeline) * 1440)
    conflict_sets = {key: set() for key in ("struct", "close", "cvt", "bar", "any")}
    daily_rows, conflicts, field_rows, anomalies, intervals, gaps = [], [], [], [], [], []
    verified_days, d039_results = set(), {}
    # All day statuses are known before the offline comparison starts.  Keep
    # the corresponding global minute indices so each flag row can identify
    # whether its dependency window touches an unverified date.
    verified_scope_days = {day for day, state in statuses.items()
                           if state.get("status") in ("verified", "passed")}
    unverified_scope_days = set(days) - verified_scope_days
    flag_buffer = io.BytesIO()
    flag_gzip = gzip.GzipFile(filename="", mode="wb", fileobj=flag_buffer, mtime=0, compresslevel=9)
    flag_text = io.TextIOWrapper(flag_gzip, encoding="utf8", newline="")
    flag_fields = ("day", "open_time_us", "open_time_utc", "verified", "classification", "severity", "status",
                   "monthly_present", "daily_present", "monthly_placeholder", "daily_placeholder", "monthly_time_anomalies", "daily_time_anomalies", "monthly_unit", "daily_unit",
                   "feature_window_touches_unverified", "label_5_window_touches_unverified", "label_15_window_touches_unverified")
    flag_writer = csv.DictWriter(flag_text, fieldnames=flag_fields, lineterminator="\n", extrasaction="ignore")
    flag_writer.writeheader()

    def touches_unverified(index: int, horizon: int) -> bool:
        lo, hi = max(0, index), min(len(timeline) * 1440 - 1, index + horizon)
        first_day, last_day = lo // 1440, hi // 1440
        return any(timeline[offset] in unverified_scope_days for offset in range(first_day, last_day + 1))

    def flag_row(row: dict, index: int, *, verified: bool, status: str) -> dict:
        row = dict(row)
        row.update(verified=verified, status=row.get("status", status),
                   feature_window_touches_unverified=touches_unverified(index - 60, 60),
                   label_5_window_touches_unverified=touches_unverified(index, 5),
                   label_15_window_touches_unverified=touches_unverified(index, 15))
        return row

    def iter_days():
        for entry in manifest["months"]:
            parsed_month = monthly_days(RAW_MONTHLY_RO, entry)
            for day in sorted(set(parsed_month) & set(days)):
                source = source_for_day(day)
                daily, hashes = load_daily(day, statuses[day], source)
                yield day, parsed_month[day], daily, source, hashes, entry["zip_sha256"]
            check_memory()

    iterator = iter(iter_days())
    previous = None
    current = next(iterator, None)
    while current is not None:
        following = next(iterator, None)
        day, monthly, daily, source, hashes, month_sha = current
        start_index = (day_start_us(day) - START_US) // MINUTE_US
        for ts in monthly.rows:
            presence[(ts - START_US) // MINUTE_US] = 1
        base = {"day": day, "month": day[:7], "role": "warmup" if day[:4] == "2020" else "evaluate", "source": source,
                "status": "verified" if daily is not None else statuses[day]["status"], **hashes,
                "monthly_zip_sha256": month_sha, "monthly_rows": len(monthly.rows), "daily_rows": len(daily.rows) if daily else None,
                "monthly_unit": monthly.unit, "daily_unit": daily.unit if daily else statuses[day].get("unit")}
        if daily is None:
            for metric in (*CLASSIFICATIONS, *SEVERITIES, "numeric_value_conflict", "time_semantics_events", "time_semantics_rows", "time_semantics_minutes", "unit_incomparable_minutes"):
                base[metric] = 0
            daily_anomalies = statuses[day].get("anomalies", [])
            if base["status"] == "unverifiable_abnormal_file" and not daily_anomalies:
                daily_anomalies = [_anomaly(day, "daily", None, "", "unreadable_abnormal_file")]
            recorded = list(monthly.anomalies) + list(daily_anomalies)
            anomalies.extend(recorded)
            base["time_semantics_events"] = len(recorded)
            base["time_semantics_rows"] = len({(r.get("side"), r.get("row_number"), r.get("open_time_us")) for r in recorded})
            base["time_semantics_minutes"] = len({r.get("open_time_us") for r in recorded if r.get("open_time_us") is not None})
            for i in range(1440):
                ts = day_start_us(day) + i * MINUTE_US
                flag_writer.writerow(flag_row({"day": day, "open_time_us": ts, "open_time_utc": utc_us(ts),
                                               "monthly_present": bool(presence[start_index + i])},
                                              start_index + i, verified=False, status=base["status"]))
        else:
            verified_days.add(day)
            previous_pair = ((previous[1], previous[2]) if previous and previous[2] is not None
                             and day_start_us(day) - day_start_us(previous[0]) == DAY_US else None)
            next_pair = ((following[1], following[2]) if following and following[2] is not None
                         and day_start_us(following[0]) - day_start_us(day) == DAY_US else None)
            compared = compare_day(day, monthly, daily, previous=previous_pair, following=next_pair)
            base.update(compared["counts"])
            base.update(compared["severity_counts"])
            base.update(compared["owner_categories"])
            base["unit_incomparable_minutes"] = compared["unit_incomparable_minutes"]
            base.update({"conflict_" + k: v for k, v in compared["field_conflicts"].items()})
            anomalies.extend(compared["timestamp_anomalies"])
            if source == "D-039":
                d039_results[day] = {k: compared[k] for k in ("counts", "field_conflicts", "placeholder_crosstab", "conflict_minutes")}
            for i, row in enumerate(compared["minutes"]):
                flag_writer.writerow(flag_row(row, start_index + i, verified=True, status="verified"))
                _extend_interval(intervals, row)
                if row["classification"] == "both_missing":
                    _extend_interval(gaps, row)
            for row in compared["conflict_minutes"]:
                row["source"] = source
                conflicts.append(row)
                index = (row["open_time_us"] - START_US) // MINUTE_US
                if row["severity"] == "structural":
                    conflict_sets["struct"].add(index)
                if row["severity"] not in ("timestamp_only", "format_only"):
                    conflict_sets["any"].add(index)
                    numeric = set(row["numeric_fields"])
                    if "close" in numeric:
                        conflict_sets["close"].add(index)
                    if numeric & {"close", "volume", "number_of_trades"}:
                        conflict_sets["cvt"].add(index)
                    if numeric & {"open", "high", "low", "taker_buy_base_asset_volume"}:
                        conflict_sets["bar"].add(index)
            field_rows.extend(compared["field_differences"])
        for terminal in ("unverifiable_missing_file", "unverifiable_network", "unverifiable_abnormal_file", "checksum_failure"):
            base[terminal] = int(base["status"] == terminal)
        daily_rows.append(base)
        previous, current = current, following
        check_memory()
    flag_text.flush()
    flag_text.detach()
    flag_gzip.close()
    if len(daily_rows) != len(days):
        raise StopTask("daily comparison coverage is incomplete")
    month_rows = summary_groups(daily_rows, "month")
    year_rows = summary_groups(daily_rows, "year")
    category_keys = ("numeric_value_conflict", "extra_in_monthly", "missing_in_monthly", "time_semantics_events", "time_semantics_rows", "time_semantics_minutes", "unverifiable_missing_file", "unverifiable_network", "unverifiable_abnormal_file", "checksum_failure", "timestamp_only", "format_only")
    owner_rows = []
    for period, rows in (("day", summary_groups(daily_rows, "day")), ("month", month_rows), ("year", year_rows)):
        for row in rows:
            owner_rows.append({"period": period, "value": row[period], "source": row["source"], **{k: row[k] for k in category_keys}})
    combined = {}
    for cohort in COHORTS:
        subset = [row for row in daily_rows if in_cohort(row, cohort)]
        combined[cohort] = {"dates": len(subset), "verified_dates": sum(r["status"] == "verified" for r in subset),
                            **{key: sum(r.get(key, 0) for r in subset) for key in (*CLASSIFICATIONS, *SEVERITIES, *category_keys)}, "note": CENSUS_NOTE}
    combined["D-043"]["selection_note"] = SELECTION_NOTES["D-043"]
    combined["census_non_selected"]["selection_note"] = SELECTION_NOTES["census_non_selected"]
    combined["selection_note"] = "The 11 D-039 dates were purposively selected and cannot be used to infer the overall defect rate."
    coverage = {"dates": len(daily_rows), "verified_dates": len(verified_days), "unverified_dates": [{"day": r["day"], "status": r["status"]} for r in daily_rows if r["status"] != "verified"],
                "grid_minutes": len(days) * 1440, "monthly_present_bars": sum(presence), "unit_incomparable_minutes": sum(r["unit_incomparable_minutes"] for r in daily_rows), "note": CENSUS_NOTE}
    conflict_columns = ("day", "source", "open_time_us", "open_time_utc", "classification", "severity", "status", "conflict_fields", "numeric_fields", "monthly_placeholder", "daily_placeholder", "monthly_time_anomalies", "daily_time_anomalies", "monthly_unit", "daily_unit", "price_bp_max", "vol_rel", "trades_rel", "confidence", "third_source_status", "monthly_previous_gap_bp", "monthly_next_gap_bp", "daily_previous_gap_bp", "daily_next_gap_bp", "monthly_ohlc_consistent", "monthly_taker_base_consistent", "monthly_taker_quote_consistent", "daily_ohlc_consistent", "daily_taker_base_consistent", "daily_taker_quote_consistent", "monthly_raw_row", "daily_raw_row")
    daily_columns = list(daily_rows[0])
    for row in daily_rows:
        daily_columns.extend(key for key in row if key not in daily_columns)
    result = {"daily-integrity.csv": _csv_bytes(daily_rows, daily_columns), "conflict-minutes.csv": _csv_bytes(conflicts, conflict_columns),
              "conflict-fields.csv": _csv_bytes(field_rows, ("day", "open_time_us", "open_time_utc", "field", "monthly", "daily", "difference", "difference_bp", "daily_monthly_ratio")),
              "consistent-gaps.csv": _csv_bytes(gaps, ("start_us", "end_us", "start_utc", "end_utc", "minutes", "classification", "severity")),
              "timestamp-anomalies.csv": _csv_bytes(anomalies, ("day", "side", "row_number", "open_time_us", "open_time_utc", "reason", "raw_row")),
              "intervals.csv": _csv_bytes(intervals, ("start_us", "end_us", "start_utc", "end_utc", "minutes", "classification", "severity")),
              "episodes.csv": _csv_bytes(merge_episodes(conflicts, verified_days), ("episode", "start_us", "end_us", "start_utc", "end_utc", "conflict_minutes")),
              "owner-categories.csv": _csv_bytes(owner_rows, ("period", "value", "source", *category_keys)),
              "summary-by-month.csv": _csv_bytes(month_rows, list(month_rows[0])), "summary-by-year.csv": _csv_bytes(year_rows, list(year_rows[0])),
              "distributions.json": json_bytes(distribution_rows(conflicts, daily_rows)), "coverage.json": json_bytes(coverage),
              "combined-summary.json": json_bytes(combined),
              "derived/minute-flags-v2.csv.gz": flag_buffer.getvalue()}
    if contamination_fn is not None:
        result.update(contamination_fn(presence, conflict_sets, verified_days))
    return result
