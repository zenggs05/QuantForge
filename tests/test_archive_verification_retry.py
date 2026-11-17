"""Synthetic contracts for the D-043 retry verifier."""
from __future__ import annotations

import csv
import builtins
import copy
import errno
import gzip
import hashlib
import http.client
import io
import importlib.util
import json
import os
from pathlib import Path
from datetime import date
import socket
import ssl
import sys
from types import SimpleNamespace
import urllib.request
import zipfile

import pytest

from quantforge import archive_verification_retry as retry


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def row(day: str, minute: int = 0, unit: str = "ms") -> tuple[str, ...]:
    epoch = (date.fromisoformat(day) - date(1970, 1, 1)).days * 86_400_000_000
    factor = 1 if unit == "us" else 1000
    start = (epoch + minute * 60_000_000) // factor
    span = 59_999_999 if unit == "us" else 59_999
    return (str(start), "100", "100", "100", "100", "1", str(start + span),
            "100", "1", "0.5", "50", "0")


def csv_bytes(rows: list[tuple[str, ...]]) -> bytes:
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(rows)
    return output.getvalue().encode("utf-8")


def zip_bytes(day: str, rows: list[tuple[str, ...]], member_day: str | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"BTCUSDT-1m-{member_day or day}.csv", csv_bytes(rows))
    return output.getvalue()


READ_ROOT_NAMES = ("ROOT", "D041_EVIDENCE_RO", "D041_LEDGER_RO", "D041_VERIFY_RO",
                   "D039_DIR_RO", "D039_EVIDENCE_RO", "RAW_MONTHLY_RO", "MANIFEST_RO")
REAL_ROOTS = tuple(getattr(retry, name) for name in READ_ROOT_NAMES if name != "ROOT") + (
    retry.ROOT / "data", retry.VERIFY_DIR_RETRY, retry.EVIDENCE_DIR_RETRY)


def metadata(path):
    if not path.exists():
        return None
    files = [path] if path.is_file() else sorted(path.rglob("*"))
    return tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in files if p.is_file())


@pytest.fixture(scope="session", autouse=True)
def unchanged_real_destinations():
    # Metadata only: no real archive, checksum, ledger, or manifest is opened.
    before = tuple(metadata(p) for p in REAL_ROOTS)
    yield
    assert tuple(metadata(p) for p in REAL_ROOTS) == before


@pytest.fixture(autouse=True)
def isolated_retry(tmp_path, monkeypatch):
    for name in READ_ROOT_NAMES:
        monkeypatch.setattr(retry, name, tmp_path / name.lower())
    monkeypatch.setattr(retry, "OTHER_WORKTREES_RO", (tmp_path / "main", tmp_path / "2f"))
    monkeypatch.setattr(retry, "VERIFY_DIR_RETRY", tmp_path / "external")
    monkeypatch.setattr(retry, "EVIDENCE_DIR_RETRY", tmp_path / "evidence")
    monkeypatch.setattr(retry, "D041_VERIFY_RO", tmp_path / "d041")
    monkeypatch.setattr(retry, "D041_EVIDENCE_RO", tmp_path / "d041-evidence")
    monkeypatch.setattr(retry, "D039_DIR_RO", tmp_path / "d039")
    monkeypatch.setattr(retry, "D039_EVIDENCE_RO", tmp_path / "d039-evidence")
    monkeypatch.setattr(retry, "RAW_MONTHLY_RO", tmp_path / "monthly")
    monkeypatch.setattr(retry, "MANIFEST_RO", tmp_path / "manifest.json")
    monkeypatch.setattr(retry, "D041_LEDGER_RO", retry.D041_EVIDENCE_RO / "network-ledger.jsonl")

    def guarded(original):
        def open_file(path, *args, **kwargs):
            if isinstance(path, (str, bytes, os.PathLike)):
                resolved = Path(os.fsdecode(path)).resolve()
                assert not any(resolved == p or resolved.is_relative_to(p) for p in REAL_ROOTS), (
                    "synthetic test attempted to open protected real data", str(resolved))
            return original(path, *args, **kwargs)
        return open_file

    monkeypatch.setattr(builtins, "open", guarded(builtins.open))
    monkeypatch.setattr(io, "open", guarded(io.open))
    monkeypatch.setattr(os, "open", guarded(os.open))

    def no_network(*args, **kwargs):
        raise AssertionError("synthetic test attempted a real network request")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", no_network)
    originals = [sys.modules["quantforge." + name] for name in
                 ("archive_verification", "archive_verification_full")]
    before = [{k: id(v) for k, v in vars(m).items()} for m in originals]
    mutable = [{k: copy.deepcopy(v) for k, v in vars(m).items()
                if isinstance(v, (dict, list, set)) and k != "__builtins__"} for m in originals]
    yield tmp_path
    assert [{k: id(v) for k, v in vars(m).items()} for m in originals] == before
    for module, snapshot in zip(originals, mutable):
        assert all(vars(module)[k] == value for k, value in snapshot.items())


def test_exact_whitelist_and_holdout_rejection():
    assert len(retry.RETRY_DAYS) == 14
    assert len(retry.URLS) == 28
    assert all(retry.allowed_url(url)[0] in retry.RETRY_DAYS for url in retry.URLS)
    for day in ("2020-02-12", "2020-11-30", "2025-09-01", "2026-09-01"):
        with pytest.raises(Exception):
            retry.ensure_day(day)
    with pytest.raises(Exception):
        retry.allowed_url(retry.URL_PREFIX + "2020-02-13.zip?x=1")


def test_ascii_console_output(capsys):
    retry.emit({"状态": "通过", "value": "ok"})
    output = capsys.readouterr().out
    output.encode("ascii")
    assert "\\u72b6" in output


def test_union_budget_counts_urls_once():
    url = next(iter(retry.URLS))
    prior = {"gets": 4217, "urls": {"old"}, "attempts": {}, "verified": set(),
             "zip_bytes": 148197038, "checksum_bytes": 186368}
    state = {"gets": 0, "urls": set(), "attempts": {}, "unsettled": set(), "complete": False,
             "terminal": set(), "zip_bytes": 0, "checksum_bytes": 0}
    attempt, reserve, remaining = retry.request_budget(url, prior, state)
    assert attempt == 1 and reserve > 0 and remaining > reserve
    state["urls"].add(url)
    state["attempts"][url] = 1
    assert retry.counters(prior, state)["cumulative_distinct_urls"] == 2


def test_exclusive_write_and_path_guard(isolated_retry):
    retry.VERIFY_DIR_RETRY.mkdir()
    target = retry.VERIFY_DIR_RETRY / ".partial" / "x.bin"
    retry.write_bytes(target, b"abc", root=retry.VERIFY_DIR_RETRY)
    with pytest.raises(FileExistsError):
        retry.write_bytes(target, b"def", root=retry.VERIFY_DIR_RETRY)
    with pytest.raises(retry.StopTask):
        retry.safe_path(isolated_retry / "escape.bin", retry.VERIFY_DIR_RETRY)


def test_checksum_and_zip_roundtrip(isolated_retry):
    day = "2020-02-13"
    archive = zip_bytes(day, [row(day)])
    checksum = f"{digest(archive)}  BTCUSDT-1m-{day}.zip\n".encode("ascii")

    class Response:
        status = code = 200

        def __init__(self, payload, url):
            self.payload, self.url, self.offset = payload, url, 0
            self.headers = {"Content-Length": str(len(payload))}

        def geturl(self):
            return self.url

        def read(self, count=-1):
            if self.offset >= len(self.payload):
                return b""
            end = min(len(self.payload), self.offset + (count if count >= 0 else len(self.payload)))
            result = self.payload[self.offset:end]
            self.offset = end
            return result

        read1 = read

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    checksum_url, zip_url = retry.urls_for_day(day)
    responses = {checksum_url: Response(checksum, checksum_url), zip_url: Response(archive, zip_url)}
    prior = {"gets": 4217, "urls": set(), "attempts": {}, "verified": set(),
             "zip_bytes": 148197038, "checksum_bytes": 186368}
    retry.VERIFY_DIR_RETRY.mkdir()
    retry.EVIDENCE_DIR_RETRY.mkdir()
    retry.append_event({"event": "run_start", "commit": "synthetic", "utc": retry.utc_now()})

    def opener(request, timeout=30):
        return responses[request.full_url]

    result = retry.acquire_day(day, prior, {}, opener=opener, sleep=lambda _: None)
    assert result["status"] == "verified"
    assert retry.retry_state()["dates"][day]["status"] == "verified"
    assert (retry.VERIFY_DIR_RETRY / f"BTCUSDT-1m-{day}.zip").exists()
    assert not list((retry.VERIFY_DIR_RETRY / ".partial").glob("*"))


def test_network_and_module_guards(monkeypatch):
    called = []
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: called.append(args))
    with pytest.raises(retry.StopTask):
        retry.block_network()
    assert called == []
    assert retry.classify_network_error(TimeoutError("synthetic")) == "retry"
    assert retry.classify_network_error(PermissionError("synthetic")) == "stop"


def test_monthly_reader_rejects_hash_mismatch(tmp_path):
    (tmp_path / "2020-02.zip").write_bytes(zip_bytes("2020-02-01", [row("2020-02-01")]))
    with pytest.raises(retry.StopTask):
        retry.monthly_days(tmp_path, {"month": "2020-02", "zip_sha256": "0" * 64, "csv_sha256": "0" * 64})


def test_synthetic_v2_build_has_all_five_cohorts(tmp_path, monkeypatch):
    day = "2020-02-13"
    monthly_zip = zip_bytes("2020-02", [row(day)], member_day="2020-02")
    daily_zip = zip_bytes(day, [row(day)])
    checksum = f"{digest(daily_zip)}  BTCUSDT-1m-{day}.zip\n".encode("ascii")
    monthly = tmp_path / "monthly"
    external = tmp_path / "external"
    monthly.mkdir()
    external.mkdir()
    (monthly / "2020-02.zip").write_bytes(monthly_zip)
    (external / f"BTCUSDT-1m-{day}.zip").write_bytes(daily_zip)
    (external / f"BTCUSDT-1m-{day}.zip.CHECKSUM").write_bytes(checksum)
    monkeypatch.setattr(retry, "RAW_MONTHLY_RO", monthly)
    monkeypatch.setattr(retry, "VERIFY_DIR_RETRY", external)
    import quantforge.data as qdata
    monthly_csv = qdata.extract_csv(monthly_zip, "BTCUSDT-1m-2020-02.zip")
    daily_csv = qdata.extract_csv(daily_zip, f"BTCUSDT-1m-{day}.zip")
    h = hashlib.sha256
    manifest = {"months": [{"month": "2020-02", "zip_sha256": h(monthly_zip).hexdigest(),
                              "csv_sha256": h(monthly_csv).hexdigest()}]}
    statuses = {day: {"status": "verified", "zip_sha256": h(daily_zip).hexdigest(),
                      "checksum_sha256": h(checksum).hexdigest(), "csv_sha256": h(daily_csv).hexdigest()}}
    artifacts = retry.build_final_v2(manifest, statuses, contamination_fn=lambda *args: {}, days=(day,))
    assert len(artifacts) == 14
    assert set(retry.COHORTS) <= set(json.loads(artifacts["distributions.json"]))


def test_no_real_network_in_synthetic_fixture(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("real network request")

    monkeypatch.setattr(urllib.request, "urlopen", denied)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", denied)
    with pytest.raises(AssertionError):
        urllib.request.urlopen("https://example.invalid")


DAY = "2020-02-13"


class Response:
    def __init__(self, payload=b"x", *, status=200, length="auto", url=None):
        self.stream = io.BytesIO(payload)
        self.status = status
        self.url = url or retry.urls_for_day(DAY)[0]
        self.headers = {} if length is None else {
            "Content-Length": str(len(payload)) if length == "auto" else length}

    def geturl(self):
        return self.url

    def read(self, count=-1):
        return self.stream.read(count)

    read1 = read

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def prior():
    return {"gets": 4217, "urls": set(), "attempts": {}, "verified": set(),
            "zip_bytes": 148197038, "checksum_bytes": 186368}


def start():
    retry.append_event({"event": "run_start", "commit": "synthetic", "utc": retry.utc_now()})


def successful_transport(day=DAY, rows=None):
    archive = zip_bytes(day, rows or [row(day)])
    checksum = f"{digest(archive)}  BTCUSDT-1m-{day}.zip\n".encode("ascii")

    def opener(request, timeout):
        assert timeout == 30 and request.get_method() == "GET"
        state = retry.retry_state()
        assert len(state["unsettled"]) == 1
        assert list(state["intents"].values())[-1]["url"] == request.full_url
        return Response(checksum if request.full_url.endswith("CHECKSUM") else archive,
                        url=request.full_url)
    return opener, checksum, archive


@pytest.mark.parametrize("day", ["2020-01-01", "2020-11-30", "2025-09-01", "2026-07-01",
                                     "2026-08-01", "2026-09-01", "2026-09-02", "2026-10-31"])
def test_rejects_each_unauthorized_date(day):
    with pytest.raises(retry.StopTask):
        retry.urls_for_day(day)


def test_all_roots_redirected_and_network_blocked(tmp_path):
    assert all(getattr(retry, name).is_relative_to(tmp_path) for name in READ_ROOT_NAMES)
    assert all(p.is_relative_to(tmp_path) for p in retry.OTHER_WORKTREES_RO)
    with pytest.raises(AssertionError, match="real network"):
        retry._open_response(urllib.request.Request(retry.urls_for_day(DAY)[0]), timeout=30)


def test_prior_ledger_and_union_not_sum():
    urls = sorted(retry.URLS)
    existing = set(urls[:17]) | {f"synthetic-old-{i}" for i in range(4090)}
    events = []
    for index, url in enumerate(sorted(existing)):
        events.extend([{"event": "intent", "id": str(index), "url": url, "kind": "checksum"},
                       {"event": "settle", "id": str(index), "bytes": 1}])
    p = retry.prior_state(events)
    state = retry.retry_state([])
    state["urls"] = set(urls)
    assert len(p["urls"]) + len(state["urls"]) > 4118
    assert retry.counters(p, state)["cumulative_distinct_urls"] == 4118
    assert retry.request_budget(urls[0], p, state)[0] == 1
    with pytest.raises(retry.StopTask, match="whitelist"):
        retry.request_budget(retry.URL_PREFIX + "2020-02-25.zip", p, state)


@pytest.mark.parametrize("mutation", ["total_gets", "new_gets", "attempts", "union", "zip", "checksum",
                                        "unsettled", "complete", "terminal", "verified"])
def test_budget_blocks_before_intent(mutation):
    p, state = prior(), retry.retry_state([])
    url = retry.urls_for_day(DAY)[1 if mutation == "zip" else 0]
    if mutation == "total_gets":
        p["gets"] = 4600
    elif mutation == "new_gets":
        state["gets"] = 84
    elif mutation == "attempts":
        state["attempts"][url] = 3
    elif mutation == "union":
        p["urls"] = {str(i) for i in range(4118)}
    elif mutation in ("zip", "checksum"):
        p[mutation + "_bytes"] = retry.TOTAL_LIMITS[mutation] - (
            retry.ZIP_LIMIT if mutation == "zip" else retry.CHECKSUM_LIMIT)
    elif mutation == "unsettled":
        state["unsettled"].add("R0001")
    elif mutation == "complete":
        state["complete"] = True
    elif mutation == "terminal":
        state["terminal"].add(url)
    else:
        p["verified"].add(DAY)
    with pytest.raises(retry.StopTask):
        retry.request_budget(url, p, state)
    assert not retry.EVIDENCE_DIR_RETRY.exists()


def test_unsettled_intent_reserves_bytes_and_blocks_restart():
    start()
    url = retry.urls_for_day(DAY)[0]
    retry.append_event({"event": "intent", "id": "R0001", "url": url, "day": DAY,
                        "kind": "checksum", "attempt": 1, "reserved_bytes": retry.CHECKSUM_LIMIT + 1})
    state = retry.retry_state()
    assert state["checksum_bytes"] == retry.CHECKSUM_LIMIT + 1
    with pytest.raises(retry.StopTask):
        retry.download_url(DAY, "checksum", prior(), {}, opener=lambda *a, **k: pytest.fail("GET"))


@pytest.mark.parametrize("code", [301, 302, 307, 308, 201, 204, 403, 418])
def test_disallowed_http_status_never_retries(code):
    start()
    with pytest.raises(retry.StopTask):
        retry.download_url(DAY, "checksum", prior(), {}, opener=lambda *a, **k: Response(status=code),
                           sleep=lambda _: pytest.fail("retry"))
    state = retry.retry_state()
    assert state["gets"] == 1 and state["settles"]["R0001"]["result"] == "stop"
    assert retry.inventory() == sorted(state["files"].values(), key=lambda x: x["path"])


@pytest.mark.parametrize("length", [None, "", "NaN", "-1", "0", "1.0", "4097"])
def test_content_length_stop_retains_original_error(length):
    start()
    with pytest.raises(retry.StopTask, match="Content-Length"):
        retry.download_url(DAY, "checksum", prior(), {}, opener=lambda *a, **k: Response(length=length))
    settle = retry.retry_state()["settles"]["R0001"]
    assert settle["error"] and settle["content_length"] == (4097 if length == "4097" else 0 if length == "0" else length)


def test_content_length_above_remaining_and_overlong_body():
    with pytest.raises(retry.StopTask, match="remaining"):
        retry.read_response(Response(b"xx"), "checksum", 1, 999, clock=lambda: 0)
    start()
    with pytest.raises(retry.StopTask, match="exceeds Content-Length"):
        retry.download_url(DAY, "checksum", prior(), {},
                           opener=lambda *a, **k: Response(b"xx", length="1"))
    assert retry.retry_state()["checksum_bytes"] == 2


def test_redirect_url_is_rejected_without_second_request():
    start()
    with pytest.raises(retry.StopTask, match="redirect"):
        retry.download_url(DAY, "checksum", prior(), {},
                           opener=lambda *a, **k: Response(url="https://invalid.test/"))
    assert retry.retry_state()["gets"] == 1


@pytest.mark.parametrize("error", [TimeoutError("timeout"), ConnectionResetError("reset"),
    ConnectionAbortedError("abort"), http.client.RemoteDisconnected("disconnected"),
    http.client.IncompleteRead(b"partial", 5), ssl.SSLEOFError("eof"),
    ssl.SSLZeroReturnError("zero"), socket.gaierror("dns")])
def test_retryable_errors_exhaust_exactly_three_attempts(error):
    start()
    pauses = []

    def opener(*args, **kwargs):
        raise error

    result = retry.acquire_day(DAY, prior(), {}, opener=opener, sleep=pauses.append)
    assert result["status"] == "unverifiable_network"
    assert retry.retry_state()["gets"] == 3 and pauses == [1, 4]
    assert not list((retry.VERIFY_DIR_RETRY / ".partial").glob("*"))


@pytest.mark.parametrize("code", [429, 500, 503, 599])
def test_transient_http_statuses_retry_then_exhaust(code):
    start()
    result = retry.acquire_day(DAY, prior(), {}, opener=lambda *a, **k: Response(status=code), sleep=lambda _: None)
    assert result["status"] == "unverifiable_network"
    assert retry.retry_state()["gets"] == 3


def test_short_body_is_preserved_on_every_attempt():
    start()
    result = retry.acquire_day(DAY, prior(), {}, opener=lambda *a, **k: Response(b"abc", length="5"), sleep=lambda _: None)
    assert result["status"] == "unverifiable_network"
    assert retry.retry_state()["checksum_bytes"] == 9
    assert all(p.read_bytes() == b"abc" for p in (retry.VERIFY_DIR_RETRY / "failed").glob("*"))


def test_attempt_wall_clock_timeout():
    with pytest.raises(TimeoutError, match="120 seconds"):
        retry.read_response(Response(), "checksum", 4096, 120, clock=lambda: 121)


@pytest.mark.parametrize("error", [PermissionError("denied"), ssl.SSLCertVerificationError("cert"),
                                     OSError(errno.EACCES, "access")])
def test_refusal_propagates_without_settle_or_payload_write(error):
    start()

    def opener(*args, **kwargs):
        raise error

    with pytest.raises(type(error)):
        retry.acquire_day(DAY, prior(), {}, opener=opener)
    assert retry.retry_state()["unsettled"] == {"R0001"}
    assert not retry.VERIFY_DIR_RETRY.exists()


@pytest.mark.parametrize("code", [5, 10013])
def test_windows_refusal_propagates_without_fallback(code):
    error = OSError("windows denial")
    error.winerror = code
    assert retry.is_refusal(error)
    assert retry.is_refusal(urllib.error.URLError(error))


def test_unknown_transport_error_stops_without_retry():
    start()

    def opener(*args, **kwargs):
        raise ValueError("unknown synthetic error")

    with pytest.raises(retry.StopTask, match="unknown synthetic error"):
        retry.acquire_day(DAY, prior(), {}, opener=opener, sleep=lambda _: pytest.fail("retry"))
    assert retry.retry_state()["gets"] == 1


@pytest.mark.parametrize("old", [False, True])
def test_checksum_404_has_two_distinct_outcomes(old):
    start()
    opener = lambda *a, **k: Response(status=404)
    if old:
        with pytest.raises(retry.StopTask, match="previously accepted"):
            retry.acquire_day(DAY, prior(), {DAY: b"old"}, opener=opener)
    else:
        assert retry.acquire_day(DAY, prior(), {}, opener=opener)["status"] == "unverifiable_missing_file"
    assert retry.retry_state()["gets"] == 1


@pytest.mark.parametrize("bad", [b"not checksum", b"0" * 64 + b" wrong.zip", b"\xff"])
def test_checksum_format_stops_before_zip(bad):
    start()
    with pytest.raises(retry.StopTask, match="CHECKSUM"):
        retry.acquire_day(DAY, prior(), {}, opener=lambda *a, **k: Response(bad))
    assert retry.retry_state()["gets"] == 1


@pytest.mark.parametrize("same", [True, False])
def test_old_checksum_compared_before_settle_and_no_final_on_mismatch(same):
    start()
    opener, checksum, _ = successful_transport()
    old = {DAY: checksum if same else checksum.replace(b"  ", b" ")}
    if same:
        assert retry.acquire_day(DAY, prior(), old, opener=opener)["status"] == "verified"
    else:
        with pytest.raises(retry.StopTask, match="bytes differ"):
            retry.acquire_day(DAY, prior(), old, opener=opener)
        assert not (retry.VERIFY_DIR_RETRY / f"BTCUSDT-1m-{DAY}.zip.CHECKSUM").exists()
    events = retry.read_events(retry.EVIDENCE_DIR_RETRY / "network-ledger.jsonl")
    comparison = next(r for r in events if r["event"] == "checksum_compare")
    assert comparison["byte_equal"] is same
    assert events.index(comparison) < next(i for i, r in enumerate(events) if r["event"] == "settle")
    consistency = retry.checksum_consistency(retry.retry_state(), old, events)[0]
    assert consistency["byte_equal"] is same


def test_wrong_old_checksum_hash_fails_before_get(monkeypatch):
    path = retry.D041_VERIFY_RO / "failed/old.bin"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"synthetic")
    monkeypatch.setattr(retry, "OLD_CHECKSUMS", {DAY: ("old.bin", "0" * 64)})
    with pytest.raises(retry.StopTask, match="old CHECKSUM bytes differ"):
        retry.old_checksums()
    assert not retry.EVIDENCE_DIR_RETRY.exists()


def test_old_checksum_exhaustion_never_falls_back():
    start()
    _, checksum, _ = successful_transport()

    def opener(*args, **kwargs):
        raise TimeoutError("synthetic")

    result = retry.acquire_day(DAY, prior(), {DAY: checksum}, opener=opener, sleep=lambda _: None)
    assert result["status"] == "unverifiable_network"
    events = retry.read_events(retry.EVIDENCE_DIR_RETRY / "network-ledger.jsonl")
    evidence = retry.checksum_consistency(retry.retry_state(), {DAY: checksum}, events)[0]
    assert evidence["byte_equal"] is None and evidence["status"] == "unverifiable_network"
    assert all(r["kind"] == "checksum" for r in retry.retry_state()["intents"].values())


def test_zip_checksum_mismatch_retains_full_bytes():
    start()
    success, _, _ = successful_transport()

    def opener(request, **kwargs):
        return success(request, **kwargs) if request.full_url.endswith("CHECKSUM") else Response(b"bad ZIP", url=request.full_url)

    with pytest.raises(retry.StopTask, match="SHA-256"):
        retry.acquire_day(DAY, prior(), {}, opener=opener)
    assert retry.retry_state()["settles"]["R0002"]["sha256"] == digest(b"bad ZIP")


def test_zip_failure_moves_accepted_checksum_with_ledger_event():
    start()
    success, checksum, _ = successful_transport()

    def opener(request, **kwargs):
        return success(request, **kwargs) if request.full_url.endswith("CHECKSUM") else Response(status=404, url=request.full_url)

    assert retry.acquire_day(DAY, prior(), {}, opener=opener)["status"] == "unverifiable_missing_file"
    assert (retry.VERIFY_DIR_RETRY / "failed/R0001.bin").read_bytes() == checksum
    assert any(r["event"] == "file_move" for r in retry.read_events(retry.EVIDENCE_DIR_RETRY / "network-ledger.jsonl"))
    retry.verify_inventory(retry.retry_state())


def test_verified_is_recorded_before_replace_and_atomic_failure_not_retried(monkeypatch):
    start()
    opener, _, _ = successful_transport()
    calls = []

    def replace(source, target):
        calls.append((source, target))
        assert retry.retry_state()["dates"][DAY]["status"] == "verified"
        raise PermissionError("synthetic replace denial")

    monkeypatch.setattr(retry, "os", SimpleNamespace(replace=replace, fsync=os.fsync))
    with pytest.raises(PermissionError, match="synthetic replace denial"):
        retry.acquire_day(DAY, prior(), {}, opener=opener)
    assert len(calls) == 1
    assert len(list((retry.VERIFY_DIR_RETRY / ".partial").glob("*"))) == 2


def test_existing_move_target_is_preserved():
    retry.VERIFY_DIR_RETRY.mkdir()
    source, target = (retry.VERIFY_DIR_RETRY / n for n in ("source", "target"))
    source.write_bytes(b"source")
    target.write_bytes(b"original")
    with pytest.raises(retry.StopTask, match="already exists"):
        retry.move_new(source, target)
    assert source.read_bytes() == b"source" and target.read_bytes() == b"original"


@pytest.mark.parametrize("day,unit", [("2024-12-31", "ms"), ("2025-01-01", "us"),
    ("2020-02-29", "ms"), ("2024-02-29", "ms"), ("2020-01-01", "ms"), ("2025-08-31", "us")])
def test_calendar_and_unit_boundaries(day, unit):
    payload = csv_bytes([row(day, 0, unit), row(day, 1439, unit)])
    parsed = retry.parse_daily_csv(payload, day)
    assert not parsed.abnormal and len(parsed.rows) == 2 and parsed.unit == unit
    assert min(parsed.rows) == retry.day_start_us(day)
    assert max(parsed.rows) == retry.day_start_us(day) + 1439 * retry.MINUTE_US


def test_abnormal_download_is_retained_with_anomaly_rows():
    start()
    opener, _, _ = successful_transport(rows=[row(DAY), row(DAY)])
    result = retry.acquire_day(DAY, prior(), {}, opener=opener)
    assert result["status"] == "unverifiable_abnormal_file" and result["anomalies"]
    assert (retry.VERIFY_DIR_RETRY / f"BTCUSDT-1m-{DAY}.zip").exists()
    assert retry.load_daily(DAY, result, "D-043")[0] is None


@pytest.mark.parametrize("name", ["conflict-minutes.csv", "conflict-fields.csv", "timestamp-anomalies.csv"])
def test_row_evidence_allows_additions_only(name):
    value = {"day": DAY, "open_time_us": "1", "field": "close", "side": "daily",
             "row_number": "1", "reason": "synthetic"}
    assert retry.delta_rows(name, [], [value])["all_within_allowed"]
    assert not retry.delta_rows(name, [value], [])["all_within_allowed"]
    assert not retry.delta_rows(name, [], [{**value, "day": "2020-02-12"}])["all_within_allowed"]


@pytest.mark.parametrize("name,inside,outside", [
    ("daily-integrity.csv", {"day": DAY}, {"day": "2020-02-12"}),
    ("summary-by-month.csv", {"month": "2020-02", "source": "D-041"}, {"month": "2020-01", "source": "D-041"}),
    ("summary-by-year.csv", {"year": "2024", "source": "combined"}, {"year": "2023", "source": "combined"}),
    ("owner-categories.csv", {"period": "day", "value": DAY, "source": "combined"},
        {"period": "day", "value": "2020-02-12", "source": "combined"}),
    ("contamination-by-month.csv", {"decision_month": "2020-12", "horizon": "5", "scope": "s", "population": "p"},
        {"decision_month": "2021-01", "horizon": "5", "scope": "s", "population": "p"}),
    ("gw-instrument-exposure.csv", {"evaluation_month": "2021-01"}, {"evaluation_month": "2020-12"}),
])
def test_delta_rows_check_each_predeclared_range(name, inside, outside):
    assert retry.delta_rows(name, [dict(inside, value_test="a")], [dict(inside, value_test="b")])["all_within_allowed"]
    assert not retry.delta_rows(name, [], [outside])["all_within_allowed"]


@pytest.mark.parametrize("name", ["summary-by-month.csv", "summary-by-year.csv", "owner-categories.csv"])
def test_d039_summary_rows_cannot_change(name):
    value = {"source": "D-039", "month": "2020-02", "year": "2020", "period": "day", "value": DAY}
    assert not retry.allowed_delta(name, value, added=True)
    assert not retry.allowed_delta(name, value, added=False)
    assert retry.allowed_delta(name, dict(value, source="census_non_selected"), added=True)


@pytest.mark.parametrize("name", ["intervals.csv", "consistent-gaps.csv", "episodes.csv"])
def test_interval_overlap_and_episode_numbering(name):
    stamp = retry.day_start_us("2020-02-12")
    value = {"start_us": str(stamp), "end_us": str(stamp + retry.MINUTE_US),
             "conflict_minutes": "2", "episode": "1"}
    assert retry.delta_rows(name, [value], [dict(value, end_us=str(stamp + 2 * retry.MINUTE_US))])["all_within_allowed"]
    outside = dict(value, start_us="0", end_us="60")
    assert not retry.delta_rows(name, [], [outside])["all_within_allowed"]
    if name == "episodes.csv":
        assert retry.delta_rows(name, [outside], [dict(outside, episode="100")])["changes"] == []


@pytest.mark.parametrize("offset,allowed", [(-16, False), (-15, True), (0, True), (1499, True), (1500, False)])
def test_minute_flag_delta_window(offset, allowed):
    stamp = retry.day_start_us("2020-12-09") + offset * retry.MINUTE_US
    before = f"open_time_us,status\n{stamp},old\n".encode()
    after = f"open_time_us,status\n{stamp},new\n".encode()
    assert retry.flag_delta(gzip.compress(before), gzip.compress(after))["all_within_allowed"] is allowed


def install_synthetic_archives(days, unverified=()):
    """Create fixtures exclusively under the redirected pytest roots."""
    entries, statuses = [], {}
    retry.RAW_MONTHLY_RO.mkdir(parents=True)
    for month in sorted({d[:7] for d in days}):
        rows = [row(d) for d in days if d.startswith(month)]
        payload = zip_bytes(month, rows)
        (retry.RAW_MONTHLY_RO / (month + ".zip")).write_bytes(payload)
        entries.append({"month": month, "zip_sha256": digest(payload), "csv_sha256": digest(csv_bytes(rows))})
    for day in days:
        if day in unverified:
            statuses[day] = {"status": "unverifiable_network"}
            continue
        root = {"D-039": retry.D039_DIR_RO, "D-041": retry.D041_VERIFY_RO,
                "D-043": retry.VERIFY_DIR_RETRY}[retry.source_for_day(day)]
        root.mkdir(parents=True, exist_ok=True)
        payload = zip_bytes(day, [row(day)])
        checksum = f"{digest(payload)}  BTCUSDT-1m-{day}.zip\n".encode()
        (root / f"BTCUSDT-1m-{day}.zip").write_bytes(payload)
        (root / f"BTCUSDT-1m-{day}.zip.CHECKSUM").write_bytes(checksum)
        statuses[day] = {"status": "verified", "zip_sha256": digest(payload),
                         "checksum_sha256": digest(checksum), "csv_sha256": digest(csv_bytes([row(day)]))}
    return {"months": entries}, statuses


@pytest.mark.parametrize("source,day", [("D-039", "2020-11-30"), ("D-041", "2020-02-12"), ("D-043", DAY)])
def test_three_readers_verify_checksum_chain(source, day):
    _, statuses = install_synthetic_archives((day,))
    parsed, _ = retry.load_daily(day, statuses[day], source)
    assert len(parsed.rows) == 1
    changed = dict(statuses[day], checksum_sha256="0" * 64)
    with pytest.raises(retry.StopTask, match="CHECKSUM bytes changed"):
        retry.load_daily(day, changed, source)
    changed = dict(statuses[day], csv_sha256="0" * 64)
    with pytest.raises(retry.StopTask, match="CSV hash changed"):
        retry.load_daily(day, changed, source)


def test_v2_preserves_other_days_and_d039_and_unverified_is_not_conflict_free(monkeypatch):
    days = ("2020-02-12", DAY, "2020-02-14", "2020-11-30")
    manifest, statuses = install_synthetic_archives(days, ("2020-02-14",))
    original_read = retry.monthly_days
    reads = []

    def monthly(root, entry):
        reads.append(entry["month"])
        return original_read(root, entry)

    monkeypatch.setattr(retry, "monthly_days", monthly)
    new = retry.build_final_v2(manifest, statuses, contamination_fn=None, days=days)
    assert reads == ["2020-02", "2020-11"]
    with monkeypatch.context() as patch:
        patch.setattr(retry, "RETRY_DAYS", ())
        old_statuses = dict(statuses, **{DAY: {"status": "unverifiable_network"}})
        old = retry.build_final_v2(manifest, old_statuses, contamination_fn=None, days=days)
    for name in new:
        if name.endswith(".csv"):
            old_rows = [r for r in retry.csv_rows(old[name]) if r.get("source") not in ("D-043", "census_non_selected")]
            assert retry.delta_rows(name, old_rows, retry.csv_rows(new[name]))["all_within_allowed"], name
    new_daily, old_daily = (retry.csv_rows(a["daily-integrity.csv"]) for a in (new, old))
    assert [r for r in new_daily if r["day"] not in retry.RETRY_DAYS] == [r for r in old_daily if r["day"] not in retry.RETRY_DAYS]
    coverage = json.loads(new["coverage.json"])
    assert coverage["unverified_dates"] == [{"day": "2020-02-14", "status": "unverifiable_network"}]
    assert coverage["verified_dates"] == 3
    for name in ("combined-summary.json", "distributions.json"):
        obj = json.loads(new[name])
        assert set(retry.COHORTS) <= set(obj)
        assert obj["census_non_selected"]["verified_dates"] == 2
    flags = retry.flag_delta(old["derived/minute-flags-v2.csv.gz"], new["derived/minute-flags-v2.csv.gz"])
    assert flags["all_within_allowed"] and flags["changes"]


def test_nonadjacent_days_are_not_used_as_neighbours(monkeypatch):
    days = (DAY, "2020-11-30")
    manifest, statuses = install_synthetic_archives(days)
    original = retry.compare_day

    def compare(day, monthly, daily, *, previous, following):
        assert previous is None and following is None
        return original(day, monthly, daily, previous=previous, following=following)

    monkeypatch.setattr(retry, "compare_day", compare)
    retry.build_final_v2(manifest, statuses, contamination_fn=None, days=days)


def test_retry_orchestration_skips_missing_checksums_without_false_union_stop(monkeypatch):
    p = prior()
    p["urls"] = set(sorted(retry.URLS)[:17]) | {f"old-{i}" for i in range(4090)}
    monkeypatch.setattr(retry, "require_r1", lambda **k: "synthetic")
    monkeypatch.setattr(retry, "verify_runtime_inputs", lambda: ({}, {}))
    monkeypatch.setattr(retry, "prior_state", lambda *a: p)
    monkeypatch.setattr(retry, "isolation_snapshot", lambda: {"synthetic": True})

    def opener(request, **kwargs):
        return Response(status=404, url=request.full_url)

    monkeypatch.setattr(retry, "_open_response", opener)
    result = retry.run_retry()
    assert result["counters"]["new_gets"] == 14
    assert retry.retry_state()["complete"]
    assert len(result["dates"]) == 14
    retry.verify_inventory(retry.retry_state(), recorded=True)
    with pytest.raises(retry.StopTask, match="restart forbidden"):
        retry.run_retry()


def test_finalize_and_replay_write_once_and_recompute_in_memory(monkeypatch):
    manifest, statuses = install_synthetic_archives((DAY,))
    original = retry.build_final_v2
    monkeypatch.setattr(retry, "offline_inputs", lambda: (manifest, statuses))
    monkeypatch.setattr(retry, "build_final_v2", lambda m, s: original(m, s, contamination_fn=None, days=(DAY,)))
    monkeypatch.setattr(retry, "delta_artifacts", lambda a: {"all_within_allowed": True, "files": {}})
    monkeypatch.setattr(retry, "assert_isolation", lambda: {"synthetic": True})
    retry.finalize()
    before = {p: p.read_bytes() for p in retry.EVIDENCE_DIR_RETRY.rglob("*") if p.is_file()}
    retry.replay_check()
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert json.loads((retry.EVIDENCE_DIR_RETRY / "determinism-check.json").read_bytes())["passed"]
    notes = json.loads((retry.EVIDENCE_DIR_RETRY / "artifact-index.json").read_bytes())["owner-categories.csv"]["cohort_notes"]
    assert set(notes) == set(retry.COHORTS)
    assert all(retry.CENSUS_NOTE in value for value in notes.values())


def test_delta_failure_is_read_only_even_when_replaying(monkeypatch):
    monkeypatch.setattr(retry, "build_final_v2", lambda *a: {})
    monkeypatch.setattr(retry, "delta_artifacts", lambda a: {"all_within_allowed": False})
    with pytest.raises(retry.StopTask) as caught:
        retry.final_artifacts({}, {})
    assert caught.value.delta == {"all_within_allowed": False}
    assert not retry.EVIDENCE_DIR_RETRY.exists()


def test_stop_artifacts_do_not_change_finished_ledger(monkeypatch):
    start()
    retry.append_event({"event": "run_complete"})
    ledger = (retry.EVIDENCE_DIR_RETRY / "network-ledger.jsonl").read_bytes()
    monkeypatch.setattr(retry, "isolation_snapshot", lambda: {"synthetic": True})
    name = retry.write_stop(retry.StopTask("synthetic invariant"), "replay-check")
    assert (retry.EVIDENCE_DIR_RETRY / name).exists()
    assert (retry.EVIDENCE_DIR_RETRY / "network-ledger.jsonl").read_bytes() == ledger
    assert retry.retry_state()["complete"]


@pytest.mark.parametrize("kind", ["disk", "evidence", "symlink", "junction"])
def test_resource_and_path_guards(monkeypatch, kind):
    if kind == "disk":
        monkeypatch.setattr(retry, "shutil", SimpleNamespace(disk_usage=lambda p: SimpleNamespace(free=retry.DISK_RESERVE - 1)))
        with pytest.raises(retry.StopTask, match="8 GiB"):
            retry.check_resources()
    elif kind == "evidence":
        monkeypatch.setattr(retry, "EVIDENCE_LIMIT", 2)
        with pytest.raises(retry.StopTask, match="100 MiB"):
            retry.write_json("too-large.json", {"x": 1})
        assert not retry.EVIDENCE_DIR_RETRY.exists()
    else:
        retry.VERIFY_DIR_RETRY.mkdir()
        original = Path.lstat

        def lstat(path):
            info = original(path)
            return SimpleNamespace(st_file_attributes=0x400, st_mode=info.st_mode) if path == retry.VERIFY_DIR_RETRY else info

        if kind == "junction":
            monkeypatch.setattr(Path, "lstat", lstat)
        else:
            original_link = Path.is_symlink
            monkeypatch.setattr(Path, "is_symlink", lambda p: p == retry.VERIFY_DIR_RETRY or original_link(p))
        with pytest.raises(retry.StopTask, match="symlink or junction"):
            retry.safe_path(retry.VERIFY_DIR_RETRY / "x", retry.VERIFY_DIR_RETRY)


@pytest.mark.parametrize("mode", ["retry", "finalize", "replay-check"])
@pytest.mark.parametrize("failure", [False, True])
def test_cli_output_always_ascii(mode, failure, monkeypatch, capsys):
    path = Path(__file__).resolve().parents[1] / "scripts/verify_daily_archives_retry.py"
    spec = importlib.util.spec_from_file_location("synthetic_retry_cli", path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    def run():
        if failure:
            raise PermissionError("合成拒绝")
        retry.emit({"value": "合成成功"})
        return {"counters": {}}

    monkeypatch.setattr(retry, {"retry": "run_retry", "finalize": "finalize", "replay-check": "replay_check"}[mode], run)
    monkeypatch.setattr(retry, "write_stop", lambda *a: pytest.fail("no fallback after refusal"))
    assert cli.main([mode]) == int(failure)
    capsys.readouterr().out.encode("ascii")


def test_imports_obey_closed_allowlist():
    import ast
    source = Path(__file__).resolve().parents[1] / "src/quantforge/archive_verification_retry.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    allowed = {
        "quantforge.archive_verification": {"sha256", "_raw_rows", "_open_response"},
        "quantforge.archive_verification_full": set("classify_network_error read_response checksum_digest parse_daily_csv ParsedDay RawBar compare_day _extend_interval merge_episodes _csv_bytes json_bytes contamination_artifacts check_memory unit_of day_start_us utc_us _anomaly StopTask RetryableTransfer ALL_DAYS D039_DAYS D041_DAYS START_US MINUTE_US DAY_US ZIP_LIMIT CHECKSUM_LIMIT MAX_ATTEMPTS BACKOFF_SECONDS CLASSIFICATIONS SEVERITIES CENSUS_NOTE COLUMNS".split()),
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in allowed:
            assert {alias.name for alias in node.names} <= allowed[node.module]
