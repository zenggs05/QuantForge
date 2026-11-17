"""D-041 bounded archive acquisition and offline source-quality verification."""
from __future__ import annotations

import csv
import errno
import gzip
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
import ssl
import stat
import subprocess
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from quantforge import data, research_policy
from quantforge.archive_verification import sha256, _raw_rows, _open_response

ROOT = Path(__file__).resolve().parents[2]
VERIFY_DIR_FULL = Path(r"D:\zgs_project\QuantForge-verification\binance-spot-daily-1m-full-d041")
EVIDENCE_DIR = ROOT / "docs/phase3a-full-verification-evidence"
D039_DIR = Path(r"D:\zgs_project\QuantForge-verification\binance-spot-daily-1m-20261110")
D039_EVIDENCE = ROOT / "docs/phase3a-daily-verification-evidence"
MANIFEST = ROOT / "data/processed/binance/spot/BTCUSDT/1m/historical/historical-manifest.json"
MANIFEST_PATH = MANIFEST
RAW_MONTHLY = ROOT / "data/raw/binance/spot/BTCUSDT/1m/historical"
MANIFEST_SHA256 = "71920d90d95a3d6600db3b57540865949b8784ce8753b0dccfbea93d7bb1fa02"
BASE_URL = "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m"
D039_DAYS = ("2020-11-30", "2020-12-21", "2021-01-11", "2021-01-21", "2021-02-11", "2021-04-22", "2021-04-23", "2021-09-23", "2021-09-29", "2022-04-13", "2023-03-24")
ALL_DAYS = tuple((date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(2070))
D041_DAYS = tuple(day for day in ALL_DAYS if day not in D039_DAYS)
ALL_DATES = ALL_DAYS
D039_DATES = D039_DAYS
MINUTE_US = 60_000_000
DAY_US = 86_400_000_000
START_US = (date(2020, 1, 1) - date(1970, 1, 1)).days * DAY_US
END_US = START_US + len(ALL_DAYS) * DAY_US
ZIP_LIMIT = 1_048_576
CHECKSUM_LIMIT = 4_096
TOTAL_ZIP_LIMIT = 201_326_592
TOTAL_CHECKSUM_LIMIT = 1_048_576
GET_LIMIT = 4_600
URL_LIMIT = 4_118
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (1, 4, 16)
DISK_RESERVE = 8 * 1024**3
MEMORY_LIMIT = 4_000_000_000
LEDGER_NAME = "network-ledger.jsonl"


class StopTask(RuntimeError):
    """A D-041 hard boundary requires stopping the entire task."""


class RetryableTransfer(RuntimeError):
    """A bounded transport attempt may be repeated under its URL budget."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def ensure_day(value: str | date, *, include_d039: bool = False) -> date:
    if isinstance(value, datetime):
        raise StopTask("day must be a calendar date")
    if isinstance(value, date):
        day = value
    elif isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            day = date.fromisoformat(value)
        except ValueError as exc:
            raise StopTask("invalid calendar date") from exc
    else:
        raise StopTask("invalid calendar date")
    key = day.isoformat()
    if key not in (ALL_DAYS if include_d039 else D041_DAYS):
        raise StopTask(f"date is outside D-041 scope: {key}")
    data.ensure_daily_allowed(day)
    research_policy.ensure_historical_month(key[:7])
    return day


def generate_dates() -> tuple[str, ...]:
    if (len(ALL_DAYS) != 2070 or len(D041_DAYS) != 2059 or len(set(D041_DAYS)) != 2059
            or set(D041_DAYS) & set(D039_DAYS) or max(D041_DAYS) != "2025-08-31"):
        raise StopTask("date whitelist invariant failed")
    for day in D041_DAYS:
        ensure_day(day)
    urls = {url for day in D041_DAYS for url in urls_for_day(day)}
    if len(urls) != URL_LIMIT:
        raise StopTask("URL whitelist count mismatch")
    return D041_DAYS


def urls_for_day(day: str | date) -> tuple[str, str]:
    key = ensure_day(day).isoformat()
    base = f"{BASE_URL}/BTCUSDT-1m-{key}.zip"
    return base + ".CHECKSUM", base


def allowed_url(url: str) -> tuple[str, str]:
    match = re.fullmatch(re.escape(BASE_URL) + r"/BTCUSDT-1m-(\d{4}-\d{2}-\d{2})\.zip(\.CHECKSUM)?", url)
    if match is None:
        raise StopTask("URL is outside D-041 whitelist")
    day = ensure_day(match.group(1)).isoformat()
    return day, "checksum" if match.group(2) else "zip"


def safe_path(path: str | Path, root: Path | None = None) -> Path:
    root = VERIFY_DIR_FULL if root is None else Path(root)
    if root.absolute() not in (VERIFY_DIR_FULL.absolute(), EVIDENCE_DIR.absolute()):
        raise StopTask("write root is not a D-041 output root")
    lexical = Path(path)
    if not lexical.is_absolute():
        lexical = root.absolute() / lexical
    component = lexical
    while True:
        try:
            st = component.lstat()
        except FileNotFoundError:
            pass
        else:
            if component.is_symlink() or getattr(st, "st_file_attributes", 0) & 0x400:
                raise StopTask("output path contains a symbolic link or junction")
        if component == component.parent:
            break
        component = component.parent
    resolved = lexical.resolve(strict=False)
    if not resolved.is_relative_to(root.resolve(strict=False)):
        raise StopTask("write path escapes D-041 output root")
    return resolved


def write_bytes(path: Path, payload: bytes, *, root: Path | None = None, exclusive: bool = False) -> None:
    root = EVIDENCE_DIR if root is None else root
    target = safe_path(path, root)
    safe_path(target.parent, root).mkdir(parents=True, exist_ok=True)
    target = safe_path(target, root)
    with target.open("xb" if exclusive else "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def write_json(name: str, value: Any, *, exclusive: bool = False) -> None:
    write_bytes(EVIDENCE_DIR / name, json_bytes(value), exclusive=exclusive)


def check_memory() -> int:
    from quantforge.phase3a_runner import peak_process_bytes
    peak = peak_process_bytes()
    if peak > MEMORY_LIMIT:
        raise StopTask(f"peak memory exceeds {MEMORY_LIMIT}: {peak}")
    return peak


def check_resources() -> None:
    check_memory()
    probe = VERIFY_DIR_FULL
    while not probe.exists():
        probe = probe.parent
    if shutil.disk_usage(probe).free < DISK_RESERVE:
        raise StopTask("free disk is below 8 GiB")


def stop_filename(now: datetime | None = None) -> str:
    instant = datetime.now(timezone.utc) if now is None else now.astimezone(timezone.utc)
    return instant.strftime("stop-%Y%m%dT%H%M%SZ.json")


def ledger_events(path: Path | None = None) -> list[dict]:
    path = EVIDENCE_DIR / LEDGER_NAME if path is None else path
    if not path.exists():
        return []
    payload = path.read_bytes()
    if payload and not payload.endswith(b"\n"):
        raise StopTask("ledger has an incomplete final line")
    try:
        return [json.loads(line) for line in payload.splitlines()]
    except (ValueError, UnicodeError) as exc:
        raise StopTask("ledger is not intact UTF-8 JSONL") from exc


def append_event(event: dict) -> None:
    target = safe_path(EVIDENCE_DIR / LEDGER_NAME, EVIDENCE_DIR)
    safe_path(target.parent, EVIDENCE_DIR).mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
    with safe_path(target, EVIDENCE_DIR).open("ab") as stream:
        stream.write(line)
        stream.flush()
        os.fsync(stream.fileno())


def rebuild_ledger(events: list[dict] | Path | None = None) -> dict:
    records = ledger_events(events) if isinstance(events, Path) or events is None else events
    intents, settles, dates, attempts, batches, terminal_urls = {}, {}, {}, {}, [], set()
    run_commit = None
    stopped = False
    files = {}
    for event in records:
        kind = event.get("event")
        if kind == "run_start":
            if run_commit is not None or intents:
                raise StopTask("duplicate or misplaced run_start")
            run_commit = event["commit"]
        elif kind == "intent":
            ident = event["id"]
            if ident in intents:
                raise StopTask("duplicate ledger intent")
            allowed_url(event["url"])
            intents[ident] = event
            attempts[event["url"]] = attempts.get(event["url"], 0) + 1
            if event["url"] in terminal_urls:
                raise StopTask("ledger repeats a terminal URL")
            if event["attempt"] != attempts[event["url"]]:
                raise StopTask("ledger URL attempt sequence mismatch")
            if event["reserved_bytes"] != (ZIP_LIMIT if event["kind"] == "zip" else CHECKSUM_LIMIT) + 1:
                raise StopTask("ledger reservation mismatch")
        elif kind == "settle":
            ident = event["id"]
            if ident not in intents or ident in settles:
                raise StopTask("unmatched or duplicate settle")
            settles[ident] = event
            if event["result"] in ("accepted", "terminal", "stop"):
                terminal_urls.add(intents[ident]["url"])
            if event.get("file"):
                files[event["file"]["path"]] = event["file"]
        elif kind == "verified":
            dates[event["day"]] = event["summary"]
            for item in event["files"]:
                files.pop(item["partial_path"], None)
                files[item["path"]] = {k: v for k, v in item.items() if k != "partial_path"}
        elif kind == "day_terminal":
            dates[event["day"]] = event["summary"]
        elif kind == "file_move":
            files.pop(event["source"], None)
            files[event["file"]["path"]] = event["file"]
        elif kind == "batch_complete":
            batches.append(event["batch"])
        elif kind == "stop":
            stopped = True
        else:
            raise StopTask(f"unknown ledger event: {kind}")
    used = {"zip": 0, "checksum": 0}
    unresolved = []
    for ident, intent in intents.items():
        if ident in settles:
            amount = settles[ident]["bytes"]
            if not isinstance(amount, int) or amount < 0:
                raise StopTask("invalid ledger byte count")
        else:
            amount = intent["reserved_bytes"]
            unresolved.append(ident)
        used[intent["kind"]] += amount
    unverifiable = sum(item["status"].startswith("unverifiable_") for item in dates.values())
    return {"gets": len(intents), "distinct_urls": len(attempts), "zip_bytes": used["zip"], "checksum_bytes": used["checksum"],
            "attempts": attempts, "unsettled_intents": unresolved, "intents": intents, "settles": settles,
            "dates": dates, "batch_complete": batches, "terminal_urls": terminal_urls,
            "files": files, "unverifiable_dates": unverifiable, "run_commit": run_commit, "stopped": stopped}


def resolve_cached_accepted(day: str, kind: str, state: dict | None = None) -> dict:
    """Resolve a previously accepted payload without issuing a request.

    This is deliberately read-only and is only useful after a future owner
    decision authorizes continuation. Terminal URLs remain forbidden to
    request; callers may use this helper to validate a cached CHECKSUM before
    requesting a still-missing ZIP.
    """
    if kind not in ("checksum", "zip"):
        raise StopTask("invalid cached payload kind")
    ensure_day(day)
    state = rebuild_ledger() if state is None else state
    suffix = ".CHECKSUM" if kind == "checksum" else ""
    filename = f"BTCUSDT-1m-{day}.zip{suffix}"
    candidates = []
    for row in state.get("files", {}).values():
        path = row.get("path")
        if path and Path(path).name == filename:
            candidates.append(row)
    if not candidates:
        wanted = urls_for_day(day)[0 if kind == "checksum" else 1]
        for ident, intent in state.get("intents", {}).items():
            settled = state.get("settles", {}).get(ident)
            if intent["url"] != wanted or not settled or settled["result"] != "accepted":
                continue
            entry = settled.get("file")
            if entry and entry["path"] in state["files"]:
                candidates.append(state["files"][entry["path"]])
    if len(candidates) != 1:
        raise StopTask(f"no unique cached accepted {kind} for {day}")
    row = candidates[0]
    target = safe_path(VERIFY_DIR_FULL / row["path"])
    payload = target.read_bytes()
    if len(payload) != row["bytes"] or sha256(payload) != row["sha256"]:
        raise StopTask("cached accepted payload changed")
    if kind == "checksum":
        checksum_digest(payload, f"BTCUSDT-1m-{day}.zip")
    return {"status": "accepted", "path": target, "payload": payload, "sha256": row["sha256"]}


def classify_network_error(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.URLError) and not isinstance(exc, urllib.error.HTTPError):
        return classify_network_error(exc.reason) if isinstance(exc.reason, BaseException) else "stop"
    if (isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in (5, 10013)
            or getattr(exc, "errno", None) in (errno.EACCES, errno.EPERM)
            or isinstance(exc, (ssl.SSLCertVerificationError, ssl.CertificateError))):
        return "stop"
    if isinstance(exc, urllib.error.HTTPError):
        return "retry" if exc.code == 429 or 500 <= exc.code <= 599 else "stop"
    if isinstance(exc, (TimeoutError, socket.timeout, ConnectionResetError, ConnectionAbortedError,
                        http.client.RemoteDisconnected, http.client.IncompleteRead,
                        ssl.SSLEOFError, ssl.SSLZeroReturnError, socket.gaierror, RetryableTransfer)):
        return "retry"
    return "stop"


def _set_read_timeout(response: Any, timeout: float) -> None:
    # urllib HTTPResponse exposes its transport through fp.raw._sock. A fake
    # response needs no socket; the injected clock still checks its deadline.
    current = response
    for name in ("fp", "raw", "_sock"):
        current = getattr(current, name, None)
        if current is None:
            return
    current.settimeout(timeout)


def read_response(response: Any, kind: str, remaining: int, deadline: float,
                  *, clock=time.monotonic) -> tuple[bytes, int]:
    limit = ZIP_LIMIT if kind == "zip" else CHECKSUM_LIMIT
    raw_length = response.headers.get("Content-Length")
    if not isinstance(raw_length, str) or re.fullmatch(r"[0-9]+", raw_length) is None:
        exc = StopTask("missing or nonnumeric Content-Length")
        exc.content_length = raw_length
        raise exc
    length = int(raw_length)
    if not 0 < length <= limit or length > remaining:
        exc = StopTask("Content-Length exceeds file or remaining transfer budget")
        exc.content_length = length
        raise exc
    payload = b""
    try:
        read = getattr(response, "read1", response.read)
        while len(payload) < length:
            left = deadline - clock()
            if left <= 0:
                raise TimeoutError("attempt exceeded 120 seconds")
            _set_read_timeout(response, min(30.0, left))
            chunk = read(min(65_536, length - len(payload)))
            payload += chunk
            if len(payload) > length:
                raise StopTask("response exceeds Content-Length")
            if not chunk:
                raise http.client.IncompleteRead(b"", length - len(payload))
        if clock() > deadline:
            raise TimeoutError("attempt exceeded 120 seconds")
        _set_read_timeout(response, min(30.0, max(0.001, deadline - clock())))
        extra = read(1)
        if extra:
            payload += extra
            raise StopTask("response exceeds Content-Length")
        if clock() > deadline:
            raise TimeoutError("attempt exceeded 120 seconds")
    except BaseException as exc:
        partial = exc.partial if isinstance(exc, http.client.IncompleteRead) else b""
        # A transport exception carries only bytes not returned by read.
        # Our explicit empty-read exception therefore carries no extra bytes.
        payload += partial
        exc.payload = payload
        exc.content_length = length
        raise
    return payload, length


def checksum_digest(payload: bytes, filename: str) -> str:
    try:
        text = payload.decode("ascii").strip()
    except UnicodeError as exc:
        raise StopTask("CHECKSUM is not ASCII") from exc
    match = re.fullmatch(r"([0-9a-fA-F]{64})[ \t]+\*?([^\s]+)", text)
    if match is None or match.group(2) != filename:
        raise StopTask("CHECKSUM format or filename mismatch")
    return match.group(1).lower()


def _file_entry(path: Path, payload: bytes) -> dict:
    return {"path": path.relative_to(VERIFY_DIR_FULL).as_posix(), "bytes": len(payload), "sha256": sha256(payload)}


def download_url(day: str, kind: str, batch: str, *, opener=None, clock=time.monotonic,
                 sleep=time.sleep, expected_sha256: str | None = None) -> dict:
    if kind not in ("checksum", "zip"):
        raise StopTask("invalid archive payload kind")
    urls = urls_for_day(day)
    url = urls[0 if kind == "checksum" else 1]
    opener = _open_response if opener is None else opener
    while True:
        state = rebuild_ledger()
        if state["unsettled_intents"]:
            raise StopTask("unsettled request intent: abnormal prior execution cannot restart")
        if url in state["terminal_urls"]:
            raise StopTask("terminal URL may not be requested again")
        attempt = state["attempts"].get(url, 0) + 1
        reservation = (ZIP_LIMIT if kind == "zip" else CHECKSUM_LIMIT) + 1
        total = TOTAL_ZIP_LIMIT if kind == "zip" else TOTAL_CHECKSUM_LIMIT
        used = state[kind + "_bytes"]
        if (state["gets"] >= GET_LIMIT or attempt > MAX_ATTEMPTS or used + reservation > total
                or (url not in state["attempts"] and state["distinct_urls"] >= URL_LIMIT)):
            raise StopTask("request would exceed D-041 budget")
        check_resources()
        allowed_url(url)
        ident = f"{state['gets'] + 1:06d}"
        append_event({"event": "intent", "id": ident, "batch": batch, "url": url, "day": day,
                      "kind": kind, "attempt": attempt, "reserved_bytes": reservation, "utc": utc_now()})
        started = clock()
        outcome, payload, error, status, final, length, modified = "accepted", b"", None, None, None, None, None
        try:
            data.ensure_daily_allowed(date.fromisoformat(day))
            research_policy.ensure_historical_month(day[:7])
            request = urllib.request.Request(url, method="GET")
            try:
                response = opener(request, timeout=30)
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
                payload, length = read_response(response, kind, total - used, started + 120, clock=clock)
            if status == 404:
                outcome = "terminal"
            elif status == 429 or 500 <= status <= 599:
                raise RetryableTransfer(f"HTTP {status}")
            elif kind == "checksum":
                checksum_digest(payload, f"BTCUSDT-1m-{day}.zip")
            elif expected_sha256 is None or sha256(payload) != expected_sha256:
                raise StopTask("full-length ZIP SHA-256 differs from CHECKSUM")
        except BaseException as exc:
            payload = getattr(exc, "payload", payload)
            length = getattr(exc, "content_length", length)
            error = exc
            outcome = "retry" if classify_network_error(exc) == "retry" else "stop"
            if outcome == "retry" and attempt == MAX_ATTEMPTS:
                outcome = "terminal"
        partial = safe_path(VERIFY_DIR_FULL / ".partial" / (ident + ".bin"))
        write_bytes(partial, payload, root=VERIFY_DIR_FULL, exclusive=True)
        target = partial if outcome == "accepted" else safe_path(VERIFY_DIR_FULL / "failed" / (ident + ".bin"))
        entry = _file_entry(target, payload)
        settle = {"event": "settle", "id": ident, "utc": utc_now(), "http_status": status,
                  "final_url": final, "content_length": length, "bytes": len(payload), "sha256": sha256(payload),
                  "last_modified": modified, "error_category": None if error is None else type(error).__name__,
                  "error": None if error is None else str(error), "result": outcome, "file": entry}
        append_event(settle)
        if target != partial:
            safe_path(target.parent).mkdir(parents=True, exist_ok=True)
            os.replace(safe_path(partial), safe_path(target))
        if outcome == "stop":
            failure = StopTask(str(error))
            failure.url = url
            failure.original_error_type = type(error).__name__
            raise failure from error
        if outcome == "accepted":
            return {"status": "accepted", "path": partial, "payload": payload, "sha256": sha256(payload), "intent_id": ident}
        if outcome == "terminal":
            return {"status": "unverifiable_missing_file" if status == 404 else "unverifiable_network", "path": target, "error": str(error) if error else "HTTP 404"}
        sleep(BACKOFF_SECONDS[attempt - 1])


def _retire_partial(path: Path) -> None:
    payload = path.read_bytes()
    target = safe_path(VERIFY_DIR_FULL / "failed" / path.name)
    append_event({"event": "file_move", "source": path.relative_to(VERIFY_DIR_FULL).as_posix(), "file": _file_entry(target, payload), "utc": utc_now()})
    safe_path(target.parent).mkdir(parents=True, exist_ok=True)
    os.replace(safe_path(path), safe_path(target))


def acquire_day(day: str, batch: str, *, opener=None, clock=time.monotonic, sleep=time.sleep) -> dict:
    ensure_day(day)
    current = rebuild_ledger()
    if day in current["dates"]:
        return current["dates"][day]
    checksum_url, _ = urls_for_day(day)
    if checksum_url in current["terminal_urls"]:
        # Never re-request an accepted CHECKSUM. The batch-level stop guard
        # still prevents continuation after an abnormal run without a new
        # decision naming that stop file.
        checksum = resolve_cached_accepted(day, "checksum", current)
    else:
        checksum = download_url(day, "checksum", batch, opener=opener, clock=clock, sleep=sleep)
    if checksum["status"] != "accepted":
        summary = {"day": day, "status": checksum["status"], "error": checksum["error"]}
        append_event({"event": "day_terminal", "day": day, "summary": summary, "utc": utc_now()})
        return summary
    digest = checksum_digest(checksum["payload"], f"BTCUSDT-1m-{day}.zip")
    archive = download_url(day, "zip", batch, opener=opener, clock=clock, sleep=sleep, expected_sha256=digest)
    if archive["status"] != "accepted":
        _retire_partial(checksum["path"])
        summary = {"day": day, "status": archive["status"], "error": archive["error"], "checksum_sha256": checksum["sha256"]}
        append_event({"event": "day_terminal", "day": day, "summary": summary, "utc": utc_now()})
        return summary
    summary = {"day": day, "status": "verified", "zip_sha256": archive["sha256"], "checksum_sha256": checksum["sha256"], "csv_sha256": None, "rows": 0, "unit": None, "anomalies": []}
    try:
        csv_payload = data.extract_csv(archive["payload"], f"BTCUSDT-1m-{day}.zip")
        summary["csv_sha256"] = sha256(csv_payload)
        parsed = parse_daily_csv(csv_payload, day, side="daily")
        summary.update(rows=len(parsed.rows), unit=parsed.unit, anomalies=parsed.anomalies)
        if parsed.abnormal:
            summary["status"] = "unverifiable_abnormal_file"
    except (data.DataValidationError, ValueError, UnicodeError, csv.Error, zipfile.BadZipFile,
            RuntimeError, NotImplementedError, EOFError, zlib.error) as exc:
        if isinstance(exc, StopTask):
            raise
        summary.update(status="unverifiable_abnormal_file", error=f"{type(exc).__name__}: {exc}")
        summary["anomalies"] = [{"day": day, "side": "daily", "reason": "unreadable_archive", "detail": str(exc)}]
    files = []
    for kind, item in (("checksum", checksum), ("zip", archive)):
        final_name = f"BTCUSDT-1m-{day}.zip" + (".CHECKSUM" if kind == "checksum" else "")
        target = safe_path(VERIFY_DIR_FULL / final_name)
        if target.exists():
            raise StopTask("refusing to replace an existing archive")
        files.append({**_file_entry(target, item["payload"]), "partial_path": item["path"].relative_to(VERIFY_DIR_FULL).as_posix()})
    append_event({"event": "verified", "day": day, "summary": summary, "files": files, "utc": utc_now()})
    for item in files:
        os.replace(safe_path(VERIFY_DIR_FULL / item["partial_path"]), safe_path(VERIFY_DIR_FULL / item["path"]))
    return summary


def inventory(*, include_partial: bool = False, include_derived: bool = False) -> list[dict]:
    rows = []
    if not VERIFY_DIR_FULL.exists():
        return rows
    for path in sorted(VERIFY_DIR_FULL.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(VERIFY_DIR_FULL)
        if rel.parts[0] == "derived" and not include_derived:
            continue
        if rel.parts[0] == ".partial" and not include_partial:
            raise StopTask(".partial is not empty")
        safe_path(path)
        rows.append(_file_entry(path, path.read_bytes()))
    return rows


def verify_download_inventory(*, acquisition_only: bool = True) -> None:
    state = rebuild_ledger()
    if state["unsettled_intents"]:
        raise StopTask("previous batch has unsettled intents; restart is forbidden")
    derived = VERIFY_DIR_FULL / "derived"
    derived_paths = sorted(p for p in derived.rglob("*") if p.is_file()) if derived.exists() else []
    if acquisition_only and derived_paths:
        raise StopTask("unexpected derived file before acquisition is complete")
    if not acquisition_only:
        index_path = EVIDENCE_DIR / "artifact-index.json"
        index = json.loads(index_path.read_bytes()) if index_path.exists() else {}
        expected_derived = sorted((item for item in index.values() if item["root"] == "external"), key=lambda row: row["path"])
        actual_derived = sorted(({"path": p.relative_to(VERIFY_DIR_FULL).as_posix(),
                                  "bytes": p.stat().st_size, "sha256": sha256(safe_path(p).read_bytes())}
                                 for p in derived_paths), key=lambda row: row["path"])
        expected_derived = [{key: item[key] for key in ("path", "bytes", "sha256")} for item in expected_derived]
        if actual_derived != expected_derived:
            raise StopTask("derived file inventory differs from artifact-index")
    expected = sorted(state["files"].values(), key=lambda row: row["path"])
    actual = sorted(inventory(include_derived=not acquisition_only), key=lambda row: row["path"])
    if not acquisition_only:
        for item in index.values():
            if item["root"] == "external":
                expected.append({"path": item["path"], "bytes": item["bytes"], "sha256": item["sha256"]})
        expected = sorted(expected, key=lambda row: row["path"])
        actual = sorted(actual, key=lambda row: row["path"])
    if actual != expected:
        raise StopTask("VERIFY_DIR_FULL differs from ledger records")


def _snapshot_tree(root: Path, *, hashes: bool, exclude: Path | None = None) -> list[dict]:
    rows = []
    if not root.exists():
        return rows
    for path in sorted(root.rglob("*")):
        if exclude is not None and (path == exclude or path.is_relative_to(exclude)):
            continue
        if not path.is_file():
            continue
        before = path.stat()
        row = {"path": str(path), "bytes": before.st_size, "mtime_ns": before.st_mtime_ns}
        if hashes:
            row["sha256"] = sha256(path.read_bytes())
        after = path.stat()
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise StopTask("protected file changed during isolation snapshot")
        rows.append(row)
    return rows


def isolation_snapshot() -> dict:
    result = {"phase3a_data": _snapshot_tree(ROOT / "data", hashes=True),
              "external_verification": _snapshot_tree(VERIFY_DIR_FULL.parent, hashes=True, exclude=VERIFY_DIR_FULL),
              "other_worktree_metadata": {}}
    for name, worktree in (("main", Path(r"D:\zgs_project\QuantForge")), ("phase2f", Path(r"D:\zgs_project\QuantForge-2f"))):
        result["other_worktree_metadata"][name] = {leaf: _snapshot_tree(worktree / leaf, hashes=False) for leaf in (".venv", "results", "data")}
    return result


def assert_isolation(snapshot: dict, *, hashes: bool = False) -> None:
    if hashes:
        if isolation_snapshot() != snapshot:
            raise StopTask("protected isolation snapshot changed")
        return
    actual = {"phase3a_data": _snapshot_tree(ROOT / "data", hashes=False),
              "external_verification": _snapshot_tree(VERIFY_DIR_FULL.parent, hashes=False, exclude=VERIFY_DIR_FULL),
              "other_worktree_metadata": {}}
    for name, worktree in (("main", Path(r"D:\zgs_project\QuantForge")), ("phase2f", Path(r"D:\zgs_project\QuantForge-2f"))):
        actual["other_worktree_metadata"][name] = {leaf: _snapshot_tree(worktree / leaf, hashes=False) for leaf in (".venv", "results", "data")}
    expected = {**snapshot, "phase3a_data": [{k: v for k, v in r.items() if k != "sha256"} for r in snapshot["phase3a_data"]],
                "external_verification": [{k: v for k, v in r.items() if k != "sha256"} for r in snapshot["external_verification"]]}
    if actual != expected:
        raise StopTask("protected file size/mtime inventory changed")


def git_preflight(*, initial: bool) -> str:
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
    if ROOT != Path(r"D:\zgs_project\QuantForge-3a") or Path.cwd().resolve() != ROOT:
        raise StopTask("wrong worktree")
    if git("branch", "--show-current") != "phase3a-protocol-and-impl":
        raise StopTask("wrong branch")
    if git("diff", "--name-only") or git("diff", "--cached", "--name-only"):
        raise StopTask("tracked files changed")
    untracked = git("ls-files", "--others", "--exclude-standard").splitlines()
    if any(initial or not p.startswith("docs/phase3a-full-verification-evidence/") for p in untracked):
        raise StopTask("unexpected untracked file")
    if subprocess.call(["git", "merge-base", "--is-ancestor", "bf3da33281d205c4a05a6ef82b4ce0dbbfba3aa7", "main"], cwd=ROOT) != 0:
        raise StopTask("main does not contain D-039")
    merges = git("log", "main", "--merges", "--format=%H %P %s")
    if not any("D-039" in line and len(line.split()) >= 4 for line in merges.splitlines()):
        raise StopTask("D-039 no-ff merge is absent from main")
    return git("rev-parse", "HEAD")


def preflight(*, initial: bool = True) -> dict:
    git_preflight(initial=initial)
    import sys
    if Path(sys.executable).resolve() != ROOT / ".venv/Scripts/python.exe":
        raise StopTask("use this worktree's virtual environment")
    decisions = (ROOT / "research/DECISION_LOG.md").read_text(encoding="utf8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf8")
    attrs = (ROOT / ".gitattributes").read_text(encoding="utf8")
    if ("## D-041" not in decisions or "D-041" not in agents or "refus" not in agents.lower()
            or "docs/phase3a-full-verification-record.md" not in attrs or "docs/phase3a-full-verification-evidence/*" not in attrs):
        raise StopTask("D-041 policy prerequisites missing")
    raw = MANIFEST.read_bytes()
    if sha256(raw) != MANIFEST_SHA256:
        raise StopTask("historical manifest fingerprint mismatch")
    manifest = json.loads(raw)
    if len(manifest["months"]) != 68:
        raise StopTask("historical manifest month count mismatch")
    for entry in manifest["months"]:
        month = entry["month"]
        for path, digest in ((RAW_MONTHLY / f"{month}.zip", entry["zip_sha256"]), (MANIFEST.parent / f"{month}.parquet", entry["parquet_sha256"])):
            if sha256(path.read_bytes()) != digest:
                raise StopTask(f"historical artifact fingerprint mismatch: {path}")
    pairs = re.findall(r"\| `(src/[^`]+)` \| `([0-9a-f]{64})` \|", (ROOT / "docs/confirmatory-protocol-v1.md").read_text(encoding="utf8"))
    if len(pairs) != 7:
        raise StopTask("expected seven code fingerprints")
    for path, digest in pairs:
        if sha256((ROOT / path).read_bytes()) != digest:
            raise StopTask("predictor fingerprint changed")
    expected = json.loads((D039_EVIDENCE / "verify-dir-inventory.json").read_bytes())
    actual = [{"name": p.relative_to(D039_DIR).as_posix(), "bytes": p.stat().st_size, "sha256": sha256(p.read_bytes())} for p in sorted(D039_DIR.rglob("*")) if p.is_file()]
    if len(expected) != 23 or actual != expected:
        raise StopTask("D-039 inventory changed")
    safe_path(VERIFY_DIR_FULL)
    safe_path(EVIDENCE_DIR, EVIDENCE_DIR)
    if initial and ((VERIFY_DIR_FULL.exists() and any(VERIFY_DIR_FULL.iterdir())) or EVIDENCE_DIR.exists()):
        raise StopTask("D-041 output roots are not fresh")
    generate_dates()
    check_resources()
    return manifest


def download_summary() -> dict:
    state = rebuild_ledger()
    return {"dates": state["dates"], "counters": {k: state[k] for k in ("gets", "distinct_urls", "zip_bytes", "checksum_bytes", "unverifiable_dates")},
            "batch_complete": state["batch_complete"], "retry_count": sum(max(0, n - 1) for n in state["attempts"].values())}


def acquire_batch(year: int, *, opener=None, clock=time.monotonic, sleep=time.sleep) -> dict:
    if year not in range(2020, 2026):
        raise StopTask("invalid batch year")
    batch = f"B{year}"
    if EVIDENCE_DIR.exists() and (any(EVIDENCE_DIR.glob("stop-*.json")) or rebuild_ledger()["stopped"]):
        raise StopTask("a stop marker exists; a new decision naming it is required")
    if year == 2020:
        manifest = preflight(initial=True)
        reconciliation = reconcile_d039()
        if reconciliation.get("passed") is not True:
            raise StopTask("D-039 reconciliation did not pass")
        before = isolation_snapshot()
        safe_path(VERIFY_DIR_FULL).mkdir(parents=True, exist_ok=True)
        safe_path(EVIDENCE_DIR, EVIDENCE_DIR).mkdir(parents=True, exist_ok=True)
        write_json("isolation-before.json", before, exclusive=True)
        write_json("d039-reconciliation.json", reconciliation, exclusive=True)
        append_event({"event": "run_start", "commit": git_preflight(initial=False), "utc": utc_now()})
    else:
        commit = git_preflight(initial=False)
        state = rebuild_ledger()
        if commit != state["run_commit"]:
            raise StopTask("acquisition code commit differs from C1")
        if state["batch_complete"] != [f"B{value}" for value in range(2020, year)]:
            raise StopTask("previous batch did not complete normally")
        verify_download_inventory()
        before = json.loads((EVIDENCE_DIR / "isolation-before.json").read_bytes())
    assert_isolation(before)
    started, start_utc = clock(), utc_now()
    days = [day for day in D041_DAYS if day.startswith(str(year))]
    for offset, day in enumerate(days, 1):
        acquire_day(day, batch, opener=opener, clock=clock, sleep=sleep)
        if rebuild_ledger()["unverifiable_dates"] > 20:
            raise StopTask("more than 20 dates are unverifiable")
        if offset % 100 == 0:
            assert_isolation(before)
        check_memory()
    verify_download_inventory()
    assert_isolation(before)
    append_event({"event": "batch_complete", "batch": batch, "utc": utc_now(), "days": len(days)})
    summary = download_summary()
    write_json("download-summary.json", summary)
    write_json(f"runtime-acquire-{batch}.json", {"batch": batch, "started_at_utc": start_utc, "finished_at_utc": utc_now(), "elapsed_seconds": clock() - started, "peak_process_bytes": check_memory(), "research_computations": 0}, exclusive=True)
    return summary


def write_stop(exc: BaseException, phase: str) -> Path:
    """Record a stop once; a denied write propagates without a fallback."""
    name = stop_filename()
    details = {"phase": phase, "utc": utc_now(), "error_type": type(exc).__name__, "reason": str(exc), "url": getattr(exc, "url", None), "restart_requires_decision_for": name}
    write_json(name, details, exclusive=True)
    append_event({"event": "stop", "stop_file": name, **details})
    write_json("isolation-at-stop.json", isolation_snapshot(), exclusive=True)
    write_json("verify-dir-inventory-at-stop.json", inventory(include_partial=True), exclusive=True)
    return EVIDENCE_DIR / name


# D041 COMPARISON SECTION

COLUMNS = tuple(data.COLUMNS)
VALUE_FIELDS = tuple(field for field in COLUMNS[1:] if field != "close_time")
PRICE_FIELDS = ("open", "high", "low", "close")
VOLUME_FIELDS = ("volume", "quote_asset_volume", "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume")
CLASSIFICATIONS = ("both_equal", "value_conflict", "missing_in_monthly", "extra_in_monthly", "both_missing")
SEVERITIES = ("structural", "major", "moderate", "minor", "timestamp_only", "format_only")
CENSUS_NOTE = "Census counts for verified dates; not estimates for unverified dates."


@dataclass(frozen=True)
class RawBar:
    raw: tuple[str, ...]
    unit: str
    open_us: int
    close_end_us: int
    placeholder: bool
    anomalies: tuple[str, ...]
    numbers: tuple[Decimal, ...]


@dataclass
class ParsedDay:
    rows: dict[int, RawBar]
    unit: str | None
    anomalies: list[dict]
    abnormal: bool


def unit_of(value: int) -> str:
    if value >= 10**15:
        return "us"
    if 0 <= value < 10**14:
        return "ms"
    raise ValueError("ambiguous timestamp magnitude")


def day_start_us(day: str) -> int:
    return (ensure_day(day, include_d039=True) - date(1970, 1, 1)).days * DAY_US


def utc_us(value: int) -> str:
    return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=value)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _anomaly(day: str, side: str, row_number: int | None, raw: tuple | str,
             reason: str, open_us: int | None = None) -> dict:
    try:
        open_utc = utc_us(open_us) if open_us is not None else None
    except OverflowError:
        open_utc = None
    return {"day": day, "side": side, "row_number": row_number,
            "open_time_us": open_us, "open_time_utc": open_utc,
            "reason": reason, "raw_row": ",".join(raw) if isinstance(raw, tuple) else raw}


def parse_daily_csv(payload: bytes, day: str, side: str = "daily") -> ParsedDay:
    """Parse raw values with integer timestamp conversion and explicit anomalies."""
    start = day_start_us(day)
    expected = "ms" if day < "2025-01-01" else "us"
    try:
        rows = _raw_rows(payload)
    except (data.DataValidationError, csv.Error, UnicodeError, ValueError) as exc:
        return ParsedDay({}, None, [_anomaly(day, side, None, "", f"syntax_error: {exc}")], True)
    if not rows:
        return ParsedDay({}, None, [_anomaly(day, side, None, "", "empty_file")], True)
    results, anomalies, all_units = {}, [], set()
    abnormal = False
    for number, row in enumerate(rows, 1):
        open_us = None
        try:
            if any(re.fullmatch(r"[0-9]+", row[i]) is None for i in (0, 6, 8)):
                raise ValueError("invalid integer timestamp/trade count")
            values = tuple(Decimal(value) for value in row)
            if not all(value.is_finite() for value in values):
                raise ValueError("nonfinite numeric field")
            op, cl = int(row[0]), int(row[6])
            unit, close_unit = unit_of(op), unit_of(cl)
            all_units.update((unit, close_unit))
            scale = 1000 if unit == "ms" else 1
            open_us = op * scale
            if unit != close_unit:
                raise ValueError("mixed_timestamp_units")
            if not start <= open_us < start + DAY_US:
                raise ValueError("outside_requested_day")
            if (open_us - start) % MINUTE_US:
                raise ValueError("off_minute_grid")
            if open_us in results:
                raise ValueError("duplicate_open_time")
            reasons = []
            if unit != expected:
                reasons.append("policy_unit_mismatch")
            if cl - op != (59_999 if unit == "ms" else 59_999_999):
                reasons.append("native_close_span_anomaly")
            if cl < op:
                reasons.append("close_before_open")
            placeholder = values[8] == 0 and values[5] == 0 and len(set(values[1:5])) == 1
            results[open_us] = RawBar(row, unit, open_us, cl * scale + scale,
                                      placeholder, tuple(reasons), values)
            anomalies.extend(_anomaly(day, side, number, row, reason, open_us) for reason in reasons)
        except (ValueError, InvalidOperation) as exc:
            abnormal = True
            anomalies.append(_anomaly(day, side, number, row, str(exc), open_us))
    if len(all_units) > 1:
        abnormal = True
        anomalies.extend(_anomaly(day, side, number, row, "mixed_units_file")
                         for number, row in enumerate(rows, 1))
    unit = next(iter(all_units)) if len(all_units) == 1 else None
    return ParsedDay(results, unit, anomalies, abnormal)


def _number(bar: RawBar, field: str) -> Decimal:
    return bar.numbers[COLUMNS.index(field)]


def _relative(a: Decimal, b: Decimal) -> Decimal:
    denominator = max(a, b)
    return Decimal(0) if a == b == 0 else abs(a - b) / denominator


def _bp(a: Decimal, b: Decimal) -> Decimal:
    return Decimal(0) if a == b == 0 else ((b - a) / a * Decimal(10000) if a else Decimal("Infinity"))


def _consistency(bar: RawBar | None) -> dict:
    if bar is None:
        return {"ohlc_consistent": None, "taker_base_consistent": None, "taker_quote_consistent": None}
    n = bar.numbers
    return {"ohlc_consistent": n[3] <= n[1] <= n[2] and n[3] <= n[4] <= n[2],
            "taker_base_consistent": n[9] <= n[5], "taker_quote_consistent": n[10] <= n[7]}


def classify_rows(monthly: RawBar | None, daily: RawBar | None, day: str) -> dict:
    """Classify one minute without inferring which source is correct."""
    ensure_day(day, include_d039=True)
    expected = "ms" if day < "2025-01-01" else "us"
    result = {"classification": "", "severity": "", "status": "compared", "conflict_fields": [],
              "numeric_fields": [], "price_bp_max": None, "vol_rel": None, "trades_rel": None,
              "confidence": "mixed", "third_source_status": "not_checked"}
    m, d = monthly, daily
    if m is None and d is None:
        result["classification"] = "both_missing"
    elif m is None:
        result.update(classification="missing_in_monthly", severity="structural", confidence="daily_only_row")
    elif d is None:
        result.update(classification="extra_in_monthly", severity="structural",
                      confidence="monthly_only_placeholder" if m.placeholder else "monthly_only_traded")
    elif m.unit != d.unit or m.unit != expected or d.unit != expected:
        result["status"] = "time_semantics_uncomparable"
    else:
        changed = [field for field in VALUE_FIELDS if m.raw[COLUMNS.index(field)] != d.raw[COLUMNS.index(field)]]
        if m.close_end_us != d.close_end_us:
            changed.append("close_time")
        numeric = [field for field in VALUE_FIELDS if _number(m, field) != _number(d, field)]
        result.update(classification="value_conflict" if changed else "both_equal",
                      conflict_fields=[field for field in COLUMNS[1:] if field in changed], numeric_fields=numeric)
        if changed:
            price = max(abs(_bp(_number(m, field), _number(d, field))) for field in PRICE_FIELDS)
            volume = max(_relative(_number(m, field), _number(d, field)) for field in VOLUME_FIELDS)
            trades = _relative(_number(m, "number_of_trades"), _number(d, "number_of_trades"))
            result.update(price_bp_max=str(price), vol_rel=str(volume), trades_rel=str(trades))
            mtrade, dtrade = _number(m, "number_of_trades"), _number(d, "number_of_trades")
            mvol, dvol = _number(m, "volume"), _number(d, "volume")
            if (m.placeholder and dtrade > 0) or (d.placeholder and mtrade > 0):
                severity = "structural"
            elif numeric:
                severity = "minor" if price <= 1 and volume <= Decimal(".01") and trades <= Decimal(".01") else (
                    "moderate" if price <= 10 and volume <= Decimal(".10") and trades <= Decimal(".10") else "major")
            elif "close_time" in changed:
                severity = "timestamp_only"
            else:
                severity = "format_only"
            result["severity"] = severity
            if (m.placeholder and dtrade > 0) or (dtrade >= mtrade and dvol >= mvol and (dtrade > mtrade or dvol > mvol)):
                result["confidence"] = "monthly_incomplete_pattern"
            elif (d.placeholder and mtrade > 0) or (mtrade >= dtrade and mvol >= dvol and (mtrade > dtrade or mvol > dvol)):
                result["confidence"] = "daily_incomplete_pattern"
    return result


def _field_difference(day: str, ts: int, field: str, m: RawBar | None, d: RawBar | None) -> dict:
    index = COLUMNS.index(field)
    out = {"day": day, "open_time_us": ts, "open_time_utc": utc_us(ts), "field": field,
           "monthly": m.raw[index] if m else None, "daily": d.raw[index] if d else None,
           "difference": None, "difference_bp": None, "daily_monthly_ratio": None}
    if m and d:
        a, b = m.numbers[index], d.numbers[index]
        out["difference"] = str(b - a)
        if field == "close_time":
            out["difference"] = str(d.close_end_us - m.close_end_us)
        elif field in PRICE_FIELDS:
            out["difference_bp"] = str(_bp(a, b)) if a else None
        elif field in VOLUME_FIELDS:
            out["daily_monthly_ratio"] = str(b / a) if a else None
    return out


def _neighbour_metrics(bar: RawBar | None, previous: RawBar | None, following: RawBar | None) -> tuple[str | None, str | None]:
    if bar is None:
        return None, None
    left = abs(_bp(_number(previous, "close"), _number(bar, "open"))) if previous and previous.open_us + MINUTE_US == bar.open_us else None
    right = abs(_bp(_number(bar, "close"), _number(following, "open"))) if following and bar.open_us + MINUTE_US == following.open_us else None
    return str(left) if left is not None else None, str(right) if right is not None else None


def compare_day(day: str, monthly: ParsedDay, daily: ParsedDay, *, previous=None, following=None) -> dict:
    """Compare one UTC day; caller supplies adjacent verified days for continuity."""
    if monthly.abnormal or daily.abnormal:
        raise StopTask("compare_day requires two syntactically valid days")
    start = day_start_us(day)
    counts = dict.fromkeys(CLASSIFICATIONS, 0)
    field_counts = dict.fromkeys(COLUMNS[1:], 0)
    severity_counts = dict.fromkeys(SEVERITIES, 0)
    crosstab = {"monthly_placeholder": {"daily_missing": 0, "daily_placeholder": 0, "daily_traded": 0},
                "monthly_missing": {"daily_missing": 0, "daily_placeholder": 0, "daily_traded": 0},
                "daily_placeholder": {"monthly_missing": 0, "monthly_placeholder": 0, "monthly_traded": 0}}
    minutes, conflicts, differences = [], [], []
    anomalies = list(monthly.anomalies) + list(daily.anomalies)
    incomparable = 0
    for i in range(1440):
        ts = start + i * MINUTE_US
        m, d = monthly.rows.get(ts), daily.rows.get(ts)
        row = {"day": day, "open_time_us": ts, "open_time_utc": utc_us(ts), **classify_rows(m, d, day)}
        row.update(monthly_present=m is not None, daily_present=d is not None,
                   monthly_placeholder=m.placeholder if m else None, daily_placeholder=d.placeholder if d else None,
                   monthly_time_anomalies="|".join(m.anomalies) if m else "", daily_time_anomalies="|".join(d.anomalies) if d else "",
                   monthly_unit=m.unit if m else None, daily_unit=d.unit if d else None)
        cls, severity = row["classification"], row["severity"]
        if cls:
            counts[cls] += 1
        else:
            incomparable += 1
        if severity:
            severity_counts[severity] += 1
        if m and d and m.unit != d.unit:
            anomalies.append(_anomaly(day, "cross_source", None, "", "cross_source_unit_mismatch", ts))
        if severity == "timestamp_only":
            anomalies.append(_anomaly(day, "cross_source", None, "", "timestamp_only", ts))
        for field in row["conflict_fields"]:
            field_counts[field] += 1
        daily_state = "daily_missing" if d is None else "daily_placeholder" if d.placeholder else "daily_traded"
        monthly_state = "monthly_missing" if m is None else "monthly_placeholder" if m.placeholder else "monthly_traded"
        if m and m.placeholder:
            crosstab["monthly_placeholder"][daily_state] += 1
        if m is None:
            crosstab["monthly_missing"][daily_state] += 1
        if d and d.placeholder:
            crosstab["daily_placeholder"][monthly_state] += 1
        minutes.append(row)
        if cls in ("value_conflict", "missing_in_monthly", "extra_in_monthly"):
            for side, parsed, bar, slot in (("monthly", monthly, m, 0), ("daily", daily, d, 1)):
                before = parsed.rows.get(ts - MINUTE_US)
                after = parsed.rows.get(ts + MINUTE_US)
                if i == 0 and previous is not None:
                    before = previous[slot].rows.get(ts - MINUTE_US)
                if i == 1439 and following is not None:
                    after = following[slot].rows.get(ts + MINUTE_US)
                left, right = _neighbour_metrics(bar, before, after)
                row[side + "_previous_gap_bp"] = left
                row[side + "_next_gap_bp"] = right
                row.update({side + "_" + k: v for k, v in _consistency(bar).items()})
                row[side + "_raw_row"] = ",".join(bar.raw) if bar else None
            conflicts.append(row)
            fields = row["conflict_fields"] if cls == "value_conflict" else COLUMNS[1:]
            differences.extend(_field_difference(day, ts, field, m, d) for field in fields)
    unique_rows = {(item["day"], item["side"], item["row_number"], item["open_time_us"]) for item in anomalies}
    owner = {"numeric_value_conflict": sum(1 for r in conflicts if r["classification"] == "value_conflict" and r["severity"] not in ("timestamp_only", "format_only")),
             "extra_in_monthly": counts["extra_in_monthly"], "missing_in_monthly": counts["missing_in_monthly"],
             "timestamp_only": severity_counts["timestamp_only"], "format_only": severity_counts["format_only"],
             "time_semantics_events": len(anomalies), "time_semantics_rows": len(unique_rows),
             "time_semantics_minutes": len({a["open_time_us"] for a in anomalies if a["open_time_us"] is not None})}
    return {"day": day, "counts": counts, "field_conflicts": field_counts, "severity_counts": severity_counts,
            "owner_categories": owner, "placeholder_crosstab": crosstab, "minutes": minutes,
            "conflict_minutes": conflicts, "field_differences": differences, "timestamp_anomalies": anomalies,
            "unit_incomparable_minutes": incomparable}


def _extend_interval(result: list[dict], row: dict) -> None:
    ts = row["open_time_us"]
    signature = (row["classification"], row["severity"])
    if result and (result[-1]["classification"], result[-1]["severity"]) == signature and result[-1]["end_us"] + MINUTE_US == ts:
        result[-1].update(end_us=ts, end_utc=utc_us(ts), minutes=result[-1]["minutes"] + 1)
    else:
        result.append({"start_us": ts, "end_us": ts, "start_utc": utc_us(ts), "end_utc": utc_us(ts),
                       "minutes": 1, "classification": signature[0], "severity": signature[1]})


def merge_intervals(minutes: list[dict]) -> list[dict]:
    result = []
    for row in sorted(minutes, key=lambda item: item["open_time_us"]):
        _extend_interval(result, row)
    return result


def merge_episodes(conflicts: list[dict], verified_days: set[str]) -> list[dict]:
    result = []
    for row in sorted(conflicts, key=lambda item: item["open_time_us"]):
        if row["classification"] not in ("value_conflict", "extra_in_monthly", "missing_in_monthly") or row["severity"] in ("timestamp_only", "format_only"):
            continue
        ts = row["open_time_us"]
        connect = bool(result and ts - result[-1]["end_us"] <= 60 * MINUTE_US)
        if connect:
            a, b = date.fromisoformat(result[-1]["end_utc"][:10]), date.fromisoformat(row["day"])
            connect = all((a + timedelta(days=i)).isoformat() in verified_days for i in range((b - a).days + 1))
        if connect:
            result[-1].update(end_us=ts, end_utc=utc_us(ts), conflict_minutes=result[-1]["conflict_minutes"] + 1)
        else:
            result.append({"episode": len(result) + 1, "start_us": ts, "end_us": ts, "start_utc": utc_us(ts),
                           "end_utc": utc_us(ts), "conflict_minutes": 1})
    return result


def _csv_bytes(rows: list[dict], columns: list[str] | tuple[str, ...]) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: "|".join(row[key]) if isinstance(row.get(key), (list, tuple)) else row.get(key) for key in columns})
    return out.getvalue().encode("utf-8")


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


def _distribution_rows(conflicts: list[dict]) -> dict:
    out = {}
    for cohort in ("D-041", "D-039", "combined"):
        subset = [r for r in conflicts if cohort == "combined" or r["source"] == cohort]
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
    return out

def _monthly_days(entry: dict) -> dict[str, ParsedDay]:
    month = research_policy.ensure_historical_month(entry["month"])
    payload = (RAW_MONTHLY / f"{month}.zip").read_bytes()
    if sha256(payload) != entry["zip_sha256"]:
        raise StopTask(f"monthly ZIP SHA differs: {month}")
    csv_payload = data.extract_csv(payload, f"BTCUSDT-1m-{month}.zip")
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


def _load_daily(day: str, status: dict, source: str) -> tuple[ParsedDay | None, dict]:
    summary = {"daily_zip_sha256": status.get("zip_sha256", status.get("sha256")),
               "daily_checksum_sha256": status.get("checksum_sha256"), "daily_csv_sha256": status.get("csv_sha256")}
    if status["status"] not in ("verified", "passed"):
        return None, summary
    root = D039_DIR if source == "D-039" else VERIFY_DIR_FULL
    name = f"BTCUSDT-1m-{day}.zip"
    z = (root / name).read_bytes()
    c = (root / (name + ".CHECKSUM")).read_bytes()
    digest = sha256(z)
    if digest != checksum_digest(c, name) or digest != summary["daily_zip_sha256"]:
        raise StopTask(f"saved daily checksum chain changed: {day}")
    if summary["daily_checksum_sha256"] and sha256(c) != summary["daily_checksum_sha256"]:
        raise StopTask(f"saved CHECKSUM bytes changed: {day}")
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


def _d039_statuses() -> dict:
    return json.loads((D039_EVIDENCE / "download-summary.json").read_bytes())["dates"]


def _compare_d039_from_results(results: dict[str, dict]) -> dict:
    expected = {r["day"]: r for r in json.loads((D039_EVIDENCE / "comparison-summary.json").read_bytes())}
    crosstabs = {r["day"]: {k: v for k, v in r.items() if k != "day"} for r in json.loads((D039_EVIDENCE / "placeholder-crosstab.json").read_bytes())}
    field_rows = {}
    with (D039_EVIDENCE / "minute-diff.csv").open(encoding="utf8", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["classification"] in ("value_conflict", "extra_in_monthly", "missing_in_monthly"):
                key = (row["day"], int(row["open_time_ms"]) * 1000, row["classification"])
                field_rows.setdefault(key, set()).add(row["field"])
    actual_fields = {}
    rows = []
    for day in D039_DAYS:
        compared = results[day]
        if compared["counts"] != expected[day]["counts"]:
            raise StopTask(f"D-039 category counts differ: {day}")
        if compared["field_conflicts"] != expected[day]["field_conflicts"]:
            raise StopTask(f"D-039 field counts differ: {day}")
        if compared["placeholder_crosstab"] != crosstabs[day]:
            raise StopTask(f"D-039 placeholder cross-tab differs: {day}")
        for minute in compared["conflict_minutes"]:
            fields = minute["conflict_fields"] if minute["classification"] == "value_conflict" else COLUMNS[1:]
            actual_fields[(day, minute["open_time_us"], minute["classification"])] = set(fields)
        rows.append({"day": day, "counts": compared["counts"], "field_conflicts": compared["field_conflicts"],
                     "placeholder_crosstab": compared["placeholder_crosstab"], "passed": True})
    if actual_fields != field_rows:
        raise StopTask("D-039 minute classification/field sets differ")
    totals = {key: sum(r["counts"][key] for r in rows) for key in CLASSIFICATIONS}
    if (totals["value_conflict"], totals["extra_in_monthly"], totals["missing_in_monthly"], totals["both_missing"]) != (112, 22, 0, 569):
        raise StopTask("D-039 reconciliation anchors differ")
    return {"passed": True, "days": rows, "totals": totals, "minute_field_sets_equal": True,
            "comparison_method": "new integer-microsecond parser; original D-039 files read only"}


def reconcile_d039(manifest: dict | None = None) -> dict:
    manifest = json.loads(MANIFEST.read_bytes()) if manifest is None else manifest
    statuses = _d039_statuses()
    results = {}
    for entry in manifest["months"]:
        days = [day for day in D039_DAYS if day.startswith(entry["month"])]
        if not days:
            continue
        monthly = _monthly_days(entry)
        for day in days:
            daily, _ = _load_daily(day, statuses[day], "D-039")
            if daily is None:
                raise StopTask("D-039 day is not verified")
            results[day] = compare_day(day, monthly[day], daily)
        check_memory()
    return _compare_d039_from_results(results)


def _summary_groups(daily_rows: list[dict], period: str) -> list[dict]:
    periods = sorted({r["day"][:{"day": 10, "month": 7, "year": 4}[period]] for r in daily_rows})
    keys = (*CLASSIFICATIONS, *SEVERITIES, "numeric_value_conflict", "time_semantics_events", "time_semantics_rows", "time_semantics_minutes", "unit_incomparable_minutes",
            "unverifiable_missing_file", "unverifiable_network", "unverifiable_abnormal_file", "checksum_failure")
    result = []
    for key in periods:
        for cohort in ("D-041", "D-039", "combined"):
            subset = [r for r in daily_rows if r["day"].startswith(key) and (cohort == "combined" or r["source"] == cohort)]
            row = {period: key, "source": cohort, "dates": len(subset), "verified_dates": sum(r["status"] == "verified" for r in subset), "note": CENSUS_NOTE}
            row.update({metric: sum(r.get(metric, 0) for r in subset) for metric in keys})
            result.append(row)
    return result


def _ensure_cohort_rows(rows: list[dict], period: str) -> list[dict]:
    """Keep explicit D-041, D-039 and combined rows for every period."""
    periods = sorted({row[period] for row in rows})
    by_key = {(row[period], row["source"]): row for row in rows}
    numeric = [key for key in rows[0] if key not in (period, "source", "note") and isinstance(rows[0][key], (int, float))]
    for value in periods:
        for source in ("D-041", "D-039", "combined"):
            if (value, source) in by_key:
                continue
            blank = {period: value, "source": source, "dates": 0, "verified_dates": 0, "note": CENSUS_NOTE}
            blank.update({key: 0 for key in numeric})
            rows.append(blank)
    return sorted(rows, key=lambda row: (row[period], ("D-041", "D-039", "combined").index(row["source"])))


def run_comparison(manifest: dict, download_summary: dict, *, contamination_fn=None) -> dict[str, bytes]:
    """Read each monthly ZIP once and compare all authorized days offline."""
    statuses = {**download_summary["dates"], **_d039_statuses()}
    if set(statuses) != set(ALL_DAYS):
        raise StopTask("comparison requires one terminal status for every authorized day")
    presence = bytearray(len(ALL_DAYS) * 1440)
    conflict_sets = {key: set() for key in ("struct", "close", "cvt", "bar", "any")}
    daily_rows, conflicts, field_rows, anomalies, intervals, gaps = [], [], [], [], [], []
    verified_days, d039_results = set(), {}
    # All day statuses are known before the offline comparison starts.  Keep
    # the corresponding global minute indices so each flag row can identify
    # whether its dependency window touches an unverified date.
    verified_scope_days = {day for day, state in statuses.items()
                           if state.get("status") in ("verified", "passed")}
    unverified_scope_days = set(ALL_DAYS) - verified_scope_days
    flag_buffer = io.BytesIO()
    flag_gzip = gzip.GzipFile(filename="", mode="wb", fileobj=flag_buffer, mtime=0, compresslevel=9)
    flag_text = io.TextIOWrapper(flag_gzip, encoding="utf8", newline="")
    flag_fields = ("day", "open_time_us", "open_time_utc", "verified", "classification", "severity", "status",
                   "monthly_present", "daily_present", "monthly_placeholder", "daily_placeholder", "monthly_time_anomalies", "daily_time_anomalies", "monthly_unit", "daily_unit",
                   "feature_window_touches_unverified", "label_5_window_touches_unverified", "label_15_window_touches_unverified")
    flag_writer = csv.DictWriter(flag_text, fieldnames=flag_fields, lineterminator="\n", extrasaction="ignore")
    flag_writer.writeheader()

    def touches_unverified(index: int, horizon: int) -> bool:
        lo, hi = max(0, index), min(len(ALL_DAYS) * 1440 - 1, index + horizon)
        first_day, last_day = lo // 1440, hi // 1440
        return any(ALL_DAYS[offset] in unverified_scope_days for offset in range(first_day, last_day + 1))

    def flag_row(row: dict, index: int, *, verified: bool, status: str) -> dict:
        row = dict(row)
        row.update(verified=verified, status=row.get("status", status),
                   feature_window_touches_unverified=touches_unverified(index - 60, 60),
                   label_5_window_touches_unverified=touches_unverified(index, 5),
                   label_15_window_touches_unverified=touches_unverified(index, 15))
        return row

    def iter_days():
        for entry in manifest["months"]:
            parsed_month = _monthly_days(entry)
            for day in sorted(parsed_month):
                source = "D-039" if day in D039_DAYS else "D-041"
                daily, hashes = _load_daily(day, statuses[day], source)
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
            previous_pair = (previous[1], previous[2]) if previous and previous[2] is not None else None
            next_pair = (following[1], following[2]) if following and following[2] is not None else None
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
    if len(daily_rows) != len(ALL_DAYS):
        raise StopTask("daily comparison coverage is incomplete")
    reconcile = _compare_d039_from_results(d039_results)
    month_rows = _ensure_cohort_rows(_summary_groups(daily_rows, "month"), "month")
    year_rows = _ensure_cohort_rows(_summary_groups(daily_rows, "year"), "year")
    category_keys = ("numeric_value_conflict", "extra_in_monthly", "missing_in_monthly", "time_semantics_events", "time_semantics_rows", "time_semantics_minutes", "unverifiable_missing_file", "unverifiable_network", "unverifiable_abnormal_file", "checksum_failure", "timestamp_only", "format_only")
    owner_rows = []
    for period, rows in (("day", _summary_groups(daily_rows, "day")), ("month", month_rows), ("year", year_rows)):
        for row in rows:
            owner_rows.append({"period": period, "value": row[period], "source": row["source"], **{k: row[k] for k in category_keys}})
    combined = {}
    for cohort in ("D-041", "D-039", "combined"):
        subset = [row for row in daily_rows if cohort == "combined" or row["source"] == cohort]
        combined[cohort] = {"dates": len(subset), "verified_dates": sum(r["status"] == "verified" for r in subset),
                            **{key: sum(r.get(key, 0) for r in subset) for key in (*CLASSIFICATIONS, *SEVERITIES, *category_keys)}, "note": CENSUS_NOTE}
    combined["selection_note"] = "The 11 D-039 dates were purposively selected and cannot be used to infer the overall defect rate."
    coverage = {"dates": len(daily_rows), "verified_dates": len(verified_days), "unverified_dates": [{"day": r["day"], "status": r["status"]} for r in daily_rows if r["status"] != "verified"],
                "grid_minutes": len(ALL_DAYS) * 1440, "monthly_present_bars": sum(presence), "unit_incomparable_minutes": sum(r["unit_incomparable_minutes"] for r in daily_rows), "note": CENSUS_NOTE}
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
              "distributions.json": json_bytes(_distribution_rows(conflicts)), "coverage.json": json_bytes(coverage),
              "combined-summary.json": json_bytes(combined), "d039-reconciliation.json": json_bytes(reconcile),
              "derived/minute-flags.csv.gz": flag_buffer.getvalue()}
    if contamination_fn is not None:
        result.update(contamination_fn(presence, conflict_sets, verified_days))
    return result

# D041 CONTAMINATION SECTION

def contamination_artifacts(presence: bytearray, conflict_sets: dict[str, set[int]],
                            verified_days: set[str]) -> dict[str, bytes]:
    """Count timestamp-only window exposure; never calculate features or targets.

    Structural feature and label counts have their own eligibility masks. The
    structural union uses the intersection of those masks, stated explicitly
    in joint_eligible_bars. All-sample label counts exclude insufficient future.
    """
    import numpy as np

    n = len(presence)
    if n > len(ALL_DAYS) * 1440:
        raise StopTask("presence extends beyond the historical protocol")
    exists = np.frombuffer(presence, dtype=np.uint8).astype(bool)
    indices = np.arange(n, dtype=np.int64)
    missing_prefix = np.r_[0, np.cumsum(~exists, dtype=np.int64)]
    unknown = np.zeros(n, dtype=bool)
    for offset in range((n + 1439) // 1440):
        if ALL_DAYS[offset] not in verified_days:
            unknown[offset * 1440:min(n, (offset + 1) * 1440)] = True
    unknown_prefix = np.r_[0, np.cumsum(unknown, dtype=np.int64)]
    sets = {key: set(conflict_sets.get(key, set())) for key in ("struct", "close", "cvt", "bar", "any")}
    if any(not isinstance(i, int) or i < 0 or i >= n for values in sets.values() for i in values):
        raise StopTask("conflict index is outside the protocol")

    def spread(points: set[int], left: int, right: int):
        change = np.zeros(n + 1, dtype=np.int32)
        if points:
            p = np.fromiter(points, dtype=np.int64)
            starts, ends = np.maximum(0, p + left), np.minimum(n, p + right + 1)
            valid = starts < ends
            np.add.at(change, starts[valid], 1)
            np.add.at(change, ends[valid], -1)
        return np.cumsum(change[:-1]) > 0

    feature_start = np.maximum(0, indices - 60)
    valid_feature = (indices >= 60) & (missing_prefix[indices + 1] == missing_prefix[feature_start])
    feature_unknown = unknown_prefix[indices + 1] > unknown_prefix[feature_start]
    feature_masks = {"U": spread(sets["any"], 0, 60),
                     "F": spread(sets["struct"] | sets["cvt"], 0, 60) | spread(sets["bar"], 0, 0)}
    rows = []
    # A bar's decision is 61 seconds later; every month-end final minute maps
    # to the following month, including the last historical bar.
    boundaries = []
    start_date = date(2020, 1, 1)
    for year in range(2020, 2026):
        for month in range(1, 13):
            current = date(year, month, 1)
            if current > date(2025, 9, 1):
                continue
            following = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
            first = max(0, (current - start_date).days * 1440 - 1)
            last = min(n, (following - start_date).days * 1440 - 1)
            if first < last:
                boundaries.append((current.strftime("%Y-%m"), first, last))
    for h in (5, 15):
        future_ok = indices + h < n
        label_end = np.minimum(n, indices + h + 1)
        valid_label = future_ok & (missing_prefix[label_end] == missing_prefix[indices])
        label_unknown = unknown_prefix[label_end] > unknown_prefix[indices]
        label_masks = {"U": spread(sets["any"], -h, 0),
                       "F": spread(sets["struct"], -h, 0) | spread(sets["close"], 0, 0) | spread(sets["close"], -h, -h)}
        for scope in ("U", "F"):
            fmask, lmask = feature_masks[scope], label_masks[scope] & future_ok
            for population in ("all", "structurally_valid"):
                fe = exists & (valid_feature if population == "structurally_valid" else True)
                le = exists & (valid_label if population == "structurally_valid" else future_ok)
                joint = fe & le
                union = (fmask | lmask) & (joint if population == "structurally_valid" else exists)
                for month, first, last in boundaries:
                    sl = slice(first, last)
                    count = lambda value: int(np.count_nonzero(value[sl]))
                    rows.append({"decision_month": month,
                                 "role": "warmup" if month < "2021-01" else ("evaluate" if month < "2025-09" else "outside_protocol"),
                                 "horizon": h, "scope": scope, "population": population,
                                 "sample_bars": count(exists), "feature_eligible_bars": count(fe),
                                 "label_eligible_bars": count(le), "joint_eligible_bars": count(joint),
                                 "feature_contaminated": count(fe & fmask), "label_contaminated": count(le & lmask),
                                 "feature_or_label_contaminated": count(union),
                                 "insufficient_history": count(exists & ~valid_feature),
                                 "insufficient_future": count(exists & ~future_ok),
                                 "label_window_has_gap": count(exists & future_ok & ~valid_label),
                                 "feature_window_touches_unverified": count(fe & feature_unknown),
                                 "label_window_touches_unverified": count(le & label_unknown)})
    gw = []
    for month in research_policy.HISTORICAL_EVALUATION_MONTHS:
        current = date.fromisoformat(month + "-01")
        prev_end = (current - start_date).days * 1440
        prev_start_date = (current - timedelta(days=1)).replace(day=1)
        prev_start = (prev_start_date - start_date).days * 1440
        relevant = sets["struct"] | sets["close"]
        gw.append({"evaluation_month": month, "previous_month": prev_start_date.strftime("%Y-%m"),
                   "conflict_minutes": sum(prev_start <= i < prev_end for i in relevant),
                   "unverified_minutes": int(np.count_nonzero(unknown[max(0, prev_start):min(n, prev_end)])),
                   "description": "timestamp exposure only; no instrument values computed"})

    def as_csv(values):
        stream = io.StringIO(newline="")
        if values:
            writer = csv.DictWriter(stream, fieldnames=list(values[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(values)
        return stream.getvalue().encode("utf-8")

    check_memory()
    return {"contamination-by-month.csv": as_csv(rows), "gw-instrument-exposure.csv": as_csv(gw)}
