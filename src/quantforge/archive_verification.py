"""Offline-first verification helpers for the D-039 daily archive audit.

The module deliberately keeps the daily archive format as raw strings.  This
allows the minute comparator to distinguish textual differences in prices and
volumes without introducing floating point roundoff.  Network helpers are
explicitly opt-in and are never called by importing this module.
"""
from __future__ import annotations

import csv
import hashlib
import http.client
import io
import json
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import stat
import time as _time
import urllib.error
import urllib.request
import zipfile
import zlib

from quantforge import data

VERIFY_DIR = Path(r"D:\zgs_project\QuantForge-verification\binance-spot-daily-1m-20261110")
EVIDENCE_DIR = data.REPOSITORY_ROOT / "docs/phase3a-daily-verification-evidence"
DATES = (
    "2020-11-30", "2020-12-21", "2021-01-11", "2021-01-21", "2021-02-11",
    "2021-04-22", "2021-04-23", "2021-09-23", "2021-09-29", "2022-04-13",
    "2023-03-24",
)
BASE_URL = "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m"
COLUMNS = tuple(data.COLUMNS)
COMPARE_FIELDS = ("open", "high", "low", "close", "volume", "quote_asset_volume",
                  "number_of_trades", "taker_buy_base_asset_volume",
                  "taker_buy_quote_asset_volume", "close_time", "ignore")
PRICE_FIELDS = {"open", "high", "low", "close"}
VOLUME_FIELDS = {"volume", "quote_asset_volume", "taker_buy_base_asset_volume",
                 "taker_buy_quote_asset_volume"}
ZIP_LIMIT = 1_048_576
CHECKSUM_LIMIT = 4_096
TOTAL_ZIP_LIMIT = 3_145_728
TOTAL_CHECKSUM_LIMIT = 65_536
GET_LIMIT = 66
MAX_ATTEMPTS = 3
RETRY_BACKOFF = (1, 4, 16)

# Frozen pre-registered windows. End points are inclusive minutes.
WINDOWS = {
    "2020-11-30": ("06:00", "06:59"), "2020-12-21": ("13:00", "17:59"),
    "2021-01-11": ("08:02", "08:09"), "2021-01-21": ("08:00", "11:59"),
    "2021-02-11": ("02:20", "04:59"), "2021-04-22": ("02:00", "02:59"),
    "2021-04-23": ("00:00", "03:59"), "2021-09-23": ("01:00", "01:59"),
    "2021-09-29": ("00:00", "23:59"), "2022-04-13": ("04:00", "07:59"),
    "2023-03-24": ("11:28", "13:59"),
}


class ArchiveVerificationError(data.DataValidationError):
    """A daily archive violates the D-039 acquisition/comparison contract."""


class BudgetExceeded(ArchiveVerificationError):
    """A transfer budget would be exceeded by the next request."""


class DayFailure(ArchiveVerificationError):
    """One date failed integrity checks while other dates may continue."""


class _ResponseLimitError(DayFailure):
    def __init__(self, limit: int, payload: bytes):
        super().__init__(f"response exceeds {limit} bytes")
        self.payload = payload


def _day(value: str | date) -> date:
    if isinstance(value, datetime):
        raise ArchiveVerificationError("day must be a calendar date")
    if isinstance(value, date):
        result = value
    elif isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            result = date.fromisoformat(value)
        except ValueError as exc:
            raise ArchiveVerificationError("invalid day") from exc
    else:
        raise ArchiveVerificationError("day must use YYYY-MM-DD")
    if result.isoformat() not in DATES:
        raise ArchiveVerificationError(f"date is outside the D-039 whitelist: {result.isoformat()}")
    data.ensure_daily_allowed(result)
    return result


def day_key(value: str | date) -> str:
    return _day(value).isoformat()


ensure_day = _day


def archive_name(day: str | date) -> str:
    return f"BTCUSDT-1m-{day_key(day)}.zip"


def allowed_urls(day: str | date) -> tuple[str, str]:
    key = day_key(day)
    root = f"{BASE_URL}/BTCUSDT-1m-{key}.zip"
    return root, root + ".CHECKSUM"


def ensure_url(url: str) -> tuple[str, str]:
    """Return (day, kind) only for an exact frozen URL."""
    for key in DATES:
        zip_url, checksum_url = allowed_urls(key)
        if url == zip_url:
            return key, "zip"
        if url == checksum_url:
            return key, "checksum"
    raise ArchiveVerificationError("URL is outside the D-039 whitelist")


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def safe_verify_path(path: str | Path, *, root: Path | None = None) -> Path:
    """Resolve a writable verification path and reject links/escapes."""
    root = VERIFY_DIR if root is None else Path(root)
    if root.absolute() not in (VERIFY_DIR.absolute(), EVIDENCE_DIR.absolute()):
        raise ArchiveVerificationError("write root is not a D-039 output directory")
    root_lexical = root.absolute()
    p = Path(path)
    if not p.is_absolute():
        p = root_lexical / p
    # Examine existing components: a symlink or junction must never be used.
    current = p
    while True:
        try:
            st = current.lstat()
        except FileNotFoundError:
            pass
        else:
            reparse = getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if current.is_symlink() or reparse:
                raise ArchiveVerificationError("verification path may not contain a symlink or junction")
        if current == current.parent:
            break
        current = current.parent
    resolved = p.resolve(strict=False)
    if not _inside(resolved, root_lexical):
        raise ArchiveVerificationError("verification path escapes VERIFY_DIR")
    return resolved


def require_empty_verify_dir(root: Path | None = None) -> Path:
    root = VERIFY_DIR if root is None else Path(root)
    if root.absolute() != VERIFY_DIR.absolute():
        raise ArchiveVerificationError("raw archive root must equal VERIFY_DIR")
    root = safe_verify_path(root, root=root)
    if root.exists() and any(root.iterdir()):
        raise ArchiveVerificationError("VERIFY_DIR must be absent or empty before networking")
    root.mkdir(parents=True, exist_ok=True)
    return root


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _raw_rows(csv_bytes: bytes) -> list[tuple[str, ...]]:
    try:
        text = csv_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ArchiveVerificationError("CSV is not UTF-8") from exc
    rows: list[tuple[str, ...]] = []
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        for row in reader:
            if not row or all(x == "" for x in row):
                continue
            if len(row) != 12:
                raise ArchiveVerificationError("CSV row must contain 12 fields")
            rows.append(tuple(row))
    except csv.Error as exc:
        raise ArchiveVerificationError("Malformed CSV") from exc
    return rows


def parse_rows(csv_bytes: bytes, day: str | date, *, monthly: bool = False) -> dict[int, tuple[str, ...]]:
    """Parse raw rows and return integer-ms open times mapped to raw tuples.

    ``monthly=True`` selects only the requested day from a monthly CSV.  Daily
    parser checks timestamp unit, duplicate/open-grid and value syntax, but
    intentionally permits missing minutes so comparison can classify them.
    """
    key = day_key(day)
    expected_start = day_start_ms(key)
    expected_end = expected_start + 86_400_000
    rows = _raw_rows(csv_bytes)
    result: dict[int, tuple[str, ...]] = {}
    for row in rows:
        try:
            if re.fullmatch(r"[0-9]+", row[0]) is None:
                raise ValueError("open_time must contain nonnegative decimal digits")
            open_ms = int(row[0])
            if monthly and not (expected_start <= open_ms < expected_end):
                continue
            for i in (0, 6, 8):
                if re.fullmatch(r"[0-9]+", row[i]) is None:
                    raise ValueError("integer field must contain nonnegative decimal digits")
            open_ms = int(row[0]); close_ms = int(row[6]); int(row[8])
            for i in (1, 2, 3, 4, 5, 7, 9, 10, 11):
                if not Decimal(row[i]).is_finite():
                    raise ValueError("nonfinite numeric field")
        except (ValueError, InvalidOperation) as exc:
            raise ArchiveVerificationError("non-numeric CSV field") from exc
        if open_ms < 10**12 or open_ms >= 10**14 or close_ms < 10**12 or close_ms >= 10**14:
            raise ArchiveVerificationError("daily timestamp unit is not milliseconds")
        if not monthly and not (expected_start <= open_ms < expected_end):
            raise ArchiveVerificationError("daily row lies outside requested day")
        if open_ms in result:
            raise ArchiveVerificationError("duplicate open_time")
        if open_ms % 60_000 != expected_start % 60_000:
            raise ArchiveVerificationError("open_time is not on minute grid")
        result[open_ms] = row
    return result


def extract_expected_csv(archive_bytes: bytes, day: str | date) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            for info in archive.infolist():
                if info.is_dir() or info.flag_bits & 1 or stat.S_ISLNK(info.external_attr >> 16):
                    raise DayFailure("ZIP member is a directory, link, or encrypted")
        return data.extract_csv(archive_bytes, archive_name(day))
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, zlib.error) as exc:
        raise DayFailure(f"ZIP member cannot be read: {exc}") from exc


def _flag(row: tuple[str, ...] | None) -> dict[str, bool] | None:
    if row is None:
        return None
    placeholder = int(row[8]) == 0 and Decimal(row[5]) == 0 and len({Decimal(x) for x in row[1:5]}) == 1
    try:
        anomaly = int(row[6]) - int(row[0]) != 59_999
    except ValueError:
        anomaly = True
    return {"zero_trade_placeholder": placeholder, "close_time_anomaly": anomaly}


def row_flags(row: tuple[str, ...] | None) -> dict[str, bool] | None:
    return _flag(row)


def _minute_label(ts: int) -> str:
    return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=ts)).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_start_ms(day: str | date) -> int:
    value = ensure_day(day)
    return (value - date(1970, 1, 1)).days * 86_400_000


def _inside_window(key: str, ts: int) -> bool:
    start_s, end_s = WINDOWS[key]
    minute = (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=ts)).time().replace(second=0, microsecond=0)
    start = time.fromisoformat(start_s); end = time.fromisoformat(end_s)
    return start <= minute <= end


def _safe_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _field_diff(month: tuple[str, ...] | None, day: tuple[str, ...] | None, field: str, idx: int) -> dict:
    mv = month[idx] if month else None; dv = day[idx] if day else None
    result = {"field": field, "monthly": mv, "daily": dv}
    if mv is not None and dv is not None:
        md, dd = _safe_decimal(mv), _safe_decimal(dv)
        if field in PRICE_FIELDS and md is not None and dd is not None:
            result["difference"] = str(dd - md)
            result["difference_bp"] = None if md == 0 else str((dd - md) / md * Decimal(10000))
        elif field in VOLUME_FIELDS and md is not None:
            result["difference"] = str(dd - md) if dd is not None else None
            result["ratio"] = None if md == 0 or dd is None else str(dd / md)
        elif field == "number_of_trades":
            result["difference"] = int(dv) - int(mv)
        elif field == "close_time":
            result["difference"] = int(dv) - int(mv)
        else:
            result["difference"] = None if md is None or dd is None else str(dd - md)
    return result


def _intervals(items: list[dict]) -> list[dict]:
    if not items:
        return []
    out = []; current = None
    for item in items:
        sig = (item["classification"], tuple(item["conflict_fields"]),
               item["monthly_placeholder"], item["daily_placeholder"])
        if current is not None and sig == current["_sig"] and item["open_time_ms"] == current["last_ms"] + 60_000:
            current["end_utc"] = item["open_time_utc"]; current["length"] += 1; current["last_ms"] = item["open_time_ms"]
        else:
            current = {"classification": item["classification"], "conflict_fields": list(item["conflict_fields"]),
                       "monthly_placeholder": item["monthly_placeholder"], "daily_placeholder": item["daily_placeholder"],
                       "start_utc": item["open_time_utc"], "end_utc": item["open_time_utc"], "length": 1,
                       "last_ms": item["open_time_ms"], "_sig": sig}
            out.append(current)
    for item in out:
        item.pop("last_ms", None); item.pop("_sig", None)
    return out


def compare_day(day: str | date, monthly_rows: dict[int, tuple[str, ...]], daily_rows: dict[int, tuple[str, ...]]) -> dict:
    key = day_key(day)
    start = day_start_ms(key)
    counts = {name: 0 for name in ("both_equal", "value_conflict", "missing_in_monthly", "extra_in_monthly", "both_missing")}
    field_conflicts = {name: 0 for name in COMPARE_FIELDS}
    window_counts = {"inside_window": 0, "outside_window": 0}
    crosstab = {"monthly_placeholder": {"daily_missing": 0, "daily_placeholder": 0, "daily_traded": 0},
                "monthly_missing": {"daily_missing": 0, "daily_placeholder": 0, "daily_traded": 0},
                "daily_placeholder": {"monthly_missing": 0, "monthly_placeholder": 0, "monthly_traded": 0}}
    diffs: list[dict] = []
    minute_flags: list[dict] = []
    all_minutes: list[dict] = []
    for n in range(1440):
        ts = start + n * 60_000; m = monthly_rows.get(ts); d = daily_rows.get(ts)
        if m is None and d is None: cls = "both_missing"
        elif m is None: cls = "missing_in_monthly"
        elif d is None: cls = "extra_in_monthly"
        elif m[1:] == d[1:]: cls = "both_equal"
        else: cls = "value_conflict"
        counts[cls] += 1
        mf, df = _flag(m), _flag(d)
        if cls == "value_conflict":
            fields = [f for i, f in enumerate(COLUMNS) if i and m[i] != d[i]]
            for f in fields: field_conflicts[f] += 1
        else: fields = []
        if cls != "both_equal": window_counts["inside_window" if _inside_window(key, ts) else "outside_window"] += 1
        def state(f): return "daily_missing" if d is None else ("daily_placeholder" if df and df["zero_trade_placeholder"] else "daily_traded")
        def state_m(): return "monthly_missing" if m is None else ("monthly_placeholder" if mf and mf["zero_trade_placeholder"] else "monthly_traded")
        if mf and mf["zero_trade_placeholder"]: crosstab["monthly_placeholder"][state(d)] += 1
        if m is None: crosstab["monthly_missing"][state(d)] += 1
        if df and df["zero_trade_placeholder"]: crosstab["daily_placeholder"][state_m()] += 1
        item = {"day": key, "open_time_ms": ts, "open_time_utc": _minute_label(ts),
                          "classification": cls, "conflict_fields": fields,
                          "monthly_placeholder": None if mf is None else mf["zero_trade_placeholder"],
                          "daily_placeholder": None if df is None else df["zero_trade_placeholder"],
                          "monthly_close_time_anomaly": None if mf is None else mf["close_time_anomaly"],
                          "daily_close_time_anomaly": None if df is None else df["close_time_anomaly"],
                          "inside_window": _inside_window(key, ts),
                          "field_differences": [_field_diff(m, d, f, COLUMNS.index(f))
                                                for f in (fields if cls in ("both_equal", "value_conflict")
                                                          else COMPARE_FIELDS)]}
        all_minutes.append(item)
        if cls != "both_equal":
            diffs.append(item)
        minute_flags.append({"day": key, "open_time_ms": ts, "open_time_utc": _minute_label(ts),
                             "monthly_present": m is not None, "daily_present": d is not None,
                             **{k: item[k] for k in ("monthly_placeholder", "daily_placeholder",
                                                     "monthly_close_time_anomaly", "daily_close_time_anomaly")}})
    return {"day": key, "counts": counts, "field_conflicts": field_conflicts,
            "window_counts": window_counts, "placeholder_crosstab": crosstab,
            "minute_diffs": diffs, "minute_flags": minute_flags, "intervals": _intervals(all_minutes)}


def verify_checksum(archive: bytes, checksum_text: bytes | str, filename: str) -> str:
    text = checksum_text.decode("ascii") if isinstance(checksum_text, bytes) else checksum_text
    lines = text.strip().splitlines()
    if len(lines) != 1:
        raise DayFailure("CHECKSUM must contain one line")
    match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?([^\s]+)", lines[0])
    if not match or match.group(2) != filename:
        raise DayFailure("malformed CHECKSUM")
    digest = sha256(archive)
    if digest != match.group(1).lower():
        raise DayFailure("ZIP SHA-256 does not match CHECKSUM")
    return digest


def verify_archive_bytes(archive: bytes, checksum_text: bytes | str, filename: str) -> str:
    """Compatibility alias used by the synthetic verifier tests."""
    return verify_checksum(archive, checksum_text, filename)


def _response_read(response, limit: int, remaining: int) -> bytes:
    """Read at most the file sentinel and never beyond remaining budget."""
    cap = min(limit + 1, remaining)
    if cap <= 0:
        raise BudgetExceeded("no transfer budget remains")
    body = b""
    # HTTPResponse.read1 yields bounded chunks, allowing completed chunks to
    # remain accounted if a later socket read times out. Test transports may
    # supply a conventional read(n) only, in which case one bounded read is used.
    read1 = getattr(response, "read1", None)
    try:
        if read1 is None:
            body = response.read(cap)
        else:
            while len(body) < cap:
                chunk = read1(min(65_536, cap - len(body)))
                if not chunk:
                    break
                body += chunk
    except Exception as exc:
        exc.payload = body + (exc.partial if isinstance(exc, http.client.IncompleteRead) else b"")
        raise
    if len(body) > cap:
        # Mock transports must honor read(n), as a real HTTPResponse does.
        exc = BudgetExceeded("transport exceeded bounded read") if cap == remaining else _ResponseLimitError(limit, body)
        exc.payload = body
        raise exc
    if len(body) > limit:
        raise _ResponseLimitError(limit, body)
    if len(body) == remaining and remaining <= limit:
        exc = BudgetExceeded("transfer exhausted budget before safe EOF confirmation")
        exc.payload = body
        raise exc
    return body


def _transient(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or 500 <= exc.code <= 599
    if isinstance(exc, urllib.error.URLError):
        return isinstance(exc.reason, (TimeoutError, ConnectionError, OSError))
    if isinstance(exc, (TimeoutError, ConnectionError, http.client.IncompleteRead)):
        return True
    return False


def _write_bytes(path: Path, payload: bytes, root: Path) -> None:
    target = safe_verify_path(path, root=root)
    safe_verify_path(target.parent, root=root).mkdir(parents=True, exist_ok=True)
    safe_verify_path(target, root=root).write_bytes(payload)


def _append_ledger(path: Path, entry: dict, root: Path) -> None:
    target = safe_verify_path(path, root=root)
    safe_verify_path(target.parent, root=root).mkdir(parents=True, exist_ok=True)
    rows = []
    if target.exists():
        rows = json.loads(target.read_text(encoding="utf-8"))
    rows.append(entry)
    safe_verify_path(target, root=root).write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_response(request, timeout=30):
    """A redirect is rejected before any request to its Location target."""
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)


def _checksum_syntax(payload: bytes, filename: str) -> None:
    try:
        text = payload.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise DayFailure("CHECKSUM is not ASCII") from exc
    match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?([^\s]+)", text)
    if not match or match.group(2) != filename:
        raise DayFailure("malformed CHECKSUM")


def _save_failed(key: str, kind: str, attempt: int, payload: bytes, root: Path) -> dict:
    name = f"{archive_name(key)}.{kind}.attempt-{attempt}.bin"
    path = root / "failed" / name
    _write_bytes(path, payload, root)
    return {"path": str(path), "bytes": len(payload), "sha256": sha256(payload)}


def download_one(day: str | date, *, opener=None, sleep=_time.sleep, root: Path | None = None,
                 ledger_path: Path | None = None, counters: dict | None = None) -> dict:
    """Download one whitelisted day with checksum-first ordering."""
    key = day_key(day)
    root = VERIFY_DIR if root is None else Path(root)
    if root.absolute() != VERIFY_DIR.absolute():
        raise ArchiveVerificationError("raw archive root must equal VERIFY_DIR")
    opener = _open_response if opener is None else opener
    ledger_path = EVIDENCE_DIR / "network-ledger.json" if ledger_path is None else Path(ledger_path)
    ledger_root = EVIDENCE_DIR
    counters = {"gets": 0, "zip_bytes": 0, "checksum_bytes": 0} if counters is None else counters
    result = {"day": key, "status": "failed", "attempts": [], "sha256": None, "failed_files": []}
    accepted = {}
    try:
        for url, kind in ((allowed_urls(key)[1], "checksum"), (allowed_urls(key)[0], "zip")):
            max_bytes = CHECKSUM_LIMIT if kind == "checksum" else ZIP_LIMIT
            counter_key = "checksum_bytes" if kind == "checksum" else "zip_bytes"
            total_limit = TOTAL_CHECKSUM_LIMIT if kind == "checksum" else TOTAL_ZIP_LIMIT
            for attempt in range(1, MAX_ATTEMPTS + 1):
                ensure_url(url)
                if counters["gets"] >= GET_LIMIT or counters[counter_key] >= total_limit:
                    raise BudgetExceeded("request would exceed the fixed transfer budget")
                counters["gets"] += 1
                entry = {"day": key, "url": url, "kind": kind, "attempt": attempt,
                         "started_at_utc": datetime.now(timezone.utc).isoformat(),
                         "ended_at_utc": None, "http_status": None, "bytes": 0,
                         "sha256": sha256(b""), "last_modified": None, "error": None}
                payload = b""
                error = None
                try:
                    data.ensure_daily_allowed(date.fromisoformat(key))
                    request = urllib.request.Request(url, method="GET")
                    try:
                        response = opener(request, timeout=30)
                    except urllib.error.HTTPError as exc:
                        response = exc
                    with response:
                        resolved = response.geturl() if hasattr(response, "geturl") else getattr(response, "url", url)
                        status = getattr(response, "status", getattr(response, "code", 200))
                        entry.update(http_status=status, resolved_url=resolved,
                                     last_modified=getattr(response, "headers", {}).get("Last-Modified"))
                        payload = _response_read(response, max_bytes, total_limit - counters[counter_key])
                        if resolved != url or 300 <= status < 400:
                            raise DayFailure("redirected URL differs from requested URL")
                        if status >= 400:
                            raise urllib.error.HTTPError(url, status, "HTTP error", getattr(response, "headers", {}), None)
                    if kind == "checksum":
                        _checksum_syntax(payload, archive_name(key))
                except Exception as exc:
                    payload = getattr(exc, "payload", payload)
                    error = exc
                finally:
                    counters[counter_key] += len(payload)
                    entry.update(ended_at_utc=datetime.now(timezone.utc).isoformat(),
                                 bytes=len(payload), sha256=sha256(payload),
                                 error=None if error is None else f"{type(error).__name__}: {error}")
                    if counters[counter_key] > total_limit:
                        error = BudgetExceeded("transfer byte budget exceeded")
                        entry["error"] = f"BudgetExceeded: {error}"
                    _append_ledger(ledger_path, entry, ledger_root)
                    result["attempts"].append(entry)
                if error is None:
                    accepted[kind] = payload
                    break
                result["failed_files"].append(_save_failed(key, kind, attempt, payload, root))
                if isinstance(error, BudgetExceeded):
                    raise error
                if not _transient(error) or attempt == MAX_ATTEMPTS:
                    raise DayFailure(f"{key} {kind} failed: {error}") from error
                sleep(RETRY_BACKOFF[attempt - 1])
        digest = verify_checksum(accepted["zip"], accepted["checksum"], archive_name(key))
        csv_bytes = extract_expected_csv(accepted["zip"], key)
        rows = parse_rows(csv_bytes, key)
        _write_bytes(root / archive_name(key), accepted["zip"], root)
        _write_bytes(root / (archive_name(key) + ".CHECKSUM"), accepted["checksum"], root)
        result.update(status="passed", sha256=digest,
                      checksum_sha256=sha256(accepted["checksum"]),
                      csv_sha256=sha256(csv_bytes), rows=len(rows))
        return result
    except Exception as exc:
        for kind, payload in accepted.items():
            result["failed_files"].append(_save_failed(key, kind, 0, payload, root))
        result["error"] = f"{type(exc).__name__}: {exc}"
        exc.result = result
        if isinstance(exc, BudgetExceeded):
            raise
        if not isinstance(exc, (ArchiveVerificationError, data.DataValidationError,
                                urllib.error.URLError, TimeoutError, ConnectionError,
                                http.client.IncompleteRead)):
            raise
        failed = DayFailure(result["error"])
        failed.result = result
        raise failed from exc


def snapshot_paths(paths: list[Path]) -> list[dict]:
    """Stat/hash a set of paths without modifying them."""
    out = []
    for p in sorted(paths, key=lambda x: str(x)):
        if not p.exists():
            out.append({"path": str(p), "exists": False}); continue
        b = p.read_bytes() if p.is_file() else None
        st = p.stat(); out.append({"path": str(p), "exists": True,
                                   "bytes": st.st_size, "sha256": sha256(b) if b is not None else None,
                                   "mtime_ns": st.st_mtime_ns})
    return out


def compare_day_from_archives(day: str | date, monthly_csv: bytes, daily_zip: bytes) -> dict:
    key = day_key(day)
    return compare_day(key, parse_rows(monthly_csv, key, monthly=True),
                       parse_rows(extract_expected_csv(daily_zip, key), key))


def run_downloads(*, dates: tuple[str, ...] | list[str] = DATES, opener=None,
                  sleep=_time.sleep, root: Path | None = None) -> dict:
    """Acquire serially; a date failure continues, a budget failure stops."""
    root = VERIFY_DIR if root is None else Path(root)
    keys = tuple(day_key(value) for value in dates)
    if len(set(keys)) != len(keys):
        raise ArchiveVerificationError("duplicate requested dates")
    require_empty_verify_dir(root)
    ledger = safe_verify_path(EVIDENCE_DIR / "network-ledger.json", root=EVIDENCE_DIR)
    if ledger.exists():
        raise ArchiveVerificationError("network ledger already exists; acquisition cannot repeat")
    counters = {"gets": 0, "zip_bytes": 0, "checksum_bytes": 0}
    result = {"dates": {}, "counters": counters, "status": "completed"}
    summary_path = EVIDENCE_DIR / "download-summary.json"
    try:
        for key in keys:
            try:
                result["dates"][key] = download_one(key, opener=opener, sleep=sleep, root=root,
                                                     ledger_path=ledger, counters=counters)
            except DayFailure as exc:
                result["dates"][key] = exc.result
            except BudgetExceeded as exc:
                result["dates"][key] = getattr(exc, "result", {"day": key, "status": "failed", "error": str(exc)})
                result["status"] = "budget_stopped"
                raise
            finally:
                _write_bytes(summary_path, (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), EVIDENCE_DIR)
    finally:
        pass
    return result
