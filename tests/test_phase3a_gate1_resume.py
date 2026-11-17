"""Synthetic D-032 continuation tests; all transfer bytes stay in memory."""

from __future__ import annotations

import io
import json
import socket
import urllib.request
import zipfile

import pytest

from quantforge import data, data_monthly as monthly
from gate1_fixtures import FakeResponse, copy_frozen_contract, synthetic_month_csv


SYNTHETIC_IMPLEMENTATION_COMMIT = "a" * 40


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("D-032 synthetic tests must never open a network socket")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture
def contract(tmp_path, monkeypatch):
    source_root = data.REPOSITORY_ROOT
    copy_frozen_contract(source_root, tmp_path)
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    return monthly.load_historical_download_contract()


@pytest.fixture
def continuation_fixture(contract, tmp_path, monkeypatch):
    """Create a verified synthetic January prefix and the exact failed-ledger shape."""
    february_archive = _zip_february(_sparse_february())
    february_checksum = _checksum(february_archive, "2020-02")
    expected = dict(monthly._D032_FAILED_MONTH_EXPECTED)
    expected.update(zip_sha256=data.sha256(february_archive),
                    checksum_sha256=data.sha256(february_checksum))
    monkeypatch.setattr(monthly, "_D032_FAILED_MONTH_EXPECTED", expected)

    january_archive = _padded_archive(synthetic_month_csv("2020-01"), "2020-01", 2_224_194)
    january_checksum = _checksum(january_archive, "2020-01")
    jan_responses = {
        monthly._historical_url("2020-01", ".zip.CHECKSUM"): FakeResponse(
            monthly._historical_url("2020-01", ".zip.CHECKSUM"), january_checksum),
        monthly._historical_url("2020-01"): FakeResponse(
            monthly._historical_url("2020-01"), january_archive),
    }
    _install_january_opener(monkeypatch, jan_responses)
    with monthly.HistoricalNetworkLedger(contract) as jan_ledger:
        jan_entry = monthly.download_historical_month("2020-01", contract, ledger=jan_ledger)

    prior = json.loads(json.dumps(jan_ledger.state))
    prior["status"] = "failed"
    prior["failed_month"] = "2020-02"
    prior["zip_body_bytes"] = 4_301_660
    prior["zip_reserved_bytes"] = 0
    prior["checksum_body_bytes"] = 176
    prior["checksum_reserved_bytes"] = 0
    prior["total_bytes_downloaded"] = 4_301_660
    prior["checksum_bytes_downloaded"] = 176
    prior["http_gets_count"] = 4
    prior["attempts"] = [
        _attempt(0, "2020-01", "checksum", 88, data.sha256(january_checksum)),
        _attempt(1, "2020-01", "zip", 2_224_194, data.sha256(january_archive)),
        _attempt(2, "2020-02", "checksum", 88, expected["checksum_sha256"]),
        _attempt(3, "2020-02", "zip", 2_077_466, expected["zip_sha256"]),
    ]
    _pin_synthetic_prior_transfers(monkeypatch, prior["attempts"])
    prior["market_rows_read"] = 44_640 + 41_346
    prior["failure"] = {
        "error_type": "DataValidationError",
        "error": "Synthetic checkpoint: strict parser rejected sparse February archive",
        "failed_at_utc": "2026-11-06T00:00:00+00:00",
        "diagnostics": {
            "raw_rows_read": 41_346,
            "zip_sha256": expected["zip_sha256"],
            "checksum_text_sha256": expected["checksum_sha256"],
        },
    }
    prior_path = tmp_path / "results/phase3a-gate1/network-ledger.json"
    prior_path.parent.mkdir(parents=True, exist_ok=True)
    prior_path.write_bytes(monthly._historical_json_bytes(prior))
    return contract, prior_path, jan_entry


def _pin_synthetic_prior_transfers(monkeypatch, attempts):
    """Replace only checkpoint digests with hashes of the synthetic payloads."""
    monkeypatch.setattr(monthly, "_D032_PRIOR_TRANSFERS", tuple(
        (attempt["month"], attempt["kind"], attempt["body_bytes"], attempt["sha256"])
        for attempt in attempts
    ))


def _install_january_opener(monkeypatch, responses):
    monkeypatch.setattr(monthly, "_open_historical_response", lambda request: responses[request.full_url])


def _attempt(identifier: int, month: str, kind: str, body_bytes: int, digest: str) -> dict:
    suffix = ".zip.CHECKSUM" if kind == "checksum" else ".zip"
    return {
        "id": identifier,
        "url": monthly._historical_url(month, suffix),
        "kind": kind,
        "month": month,
        "month_attempt": 1,
        "started_at_utc": "2026-11-06T00:00:00+00:00",
        "finished_at_utc": "2026-11-06T00:00:01+00:00",
        "status": "received",
        "http_status": 200,
        "content_length": body_bytes,
        "body_bytes": body_bytes,
        "reserved_bytes": 0,
        "sha256": digest,
    }


def _sparse_february(missing: int = 414, bad_close: int = 1) -> bytes:
    lines = synthetic_month_csv("2020-02").splitlines()
    del lines[100:100 + missing]
    for index in range(bad_close):
        fields = lines[1_000 + index].split(b",")
        fields[6] = str(int(fields[6]) + 60_000).encode("ascii")
        lines[1_000 + index] = b",".join(fields)
    return b"\n".join(lines) + b"\n"


def _padded_archive(csv_bytes: bytes, month: str, total_bytes: int) -> bytes:
    """Use a valid ZIP prefix to match checkpoint byte counts without real data."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        member = zipfile.ZipInfo(f"BTCUSDT-1m-{month}.csv", date_time=(2020, 1, 1, 0, 0, 0))
        member.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(member, csv_bytes)
    payload = output.getvalue()
    assert len(payload) < total_bytes
    return b"\0" * (total_bytes - len(payload)) + payload


def _zip_february(csv_bytes: bytes) -> bytes:
    return _padded_archive(csv_bytes, "2020-02", 2_077_466)


def _checksum(archive: bytes, month: str) -> bytes:
    return f"{data.sha256(archive)} BTCUSDT-1m-{month}.zip\n".encode("ascii")


def _responses(archive: bytes, checksum: bytes):
    checksum_url = monthly._historical_url("2020-02", ".zip.CHECKSUM")
    zip_url = monthly._historical_url("2020-02")
    requested = []

    def opener(request):
        requested.append(request.full_url)
        assert request.full_url in {checksum_url, zip_url}
        payload = checksum if request.full_url == checksum_url else archive
        return FakeResponse(request.full_url, payload)

    return opener, requested


def test_d032_refetch_accepts_exact_sparse_archive_and_publishes_resume_ledger(
    continuation_fixture, monkeypatch,
):
    contract, prior_path, january = continuation_fixture
    csv_bytes = _sparse_february()
    archive = _zip_february(csv_bytes)
    checksum = _checksum(archive, "2020-02")
    monkeypatch.setattr(monthly, "_historical_disk_gate", lambda _contract: None)
    opener, requested = _responses(archive, checksum)
    before_prior = prior_path.read_bytes()

    entry = monthly.refetch_failed_historical_month_2020_02(
        contract, prior_ledger_path=prior_path, open_response=opener,
        implementation_commit_sha=SYNTHETIC_IMPLEMENTATION_COMMIT,
    )

    assert requested == [
        monthly._historical_url("2020-02", ".zip.CHECKSUM"),
        monthly._historical_url("2020-02"),
    ]
    assert entry["month"] == "2020-02"
    assert entry["attempts"] == 1
    assert entry["historical_attempts"] == 2
    assert entry["http_gets"] == 2
    assert entry["rows"] == entry["actual_minutes"] == 41_346
    assert entry["expected_minutes"] == 41_760
    assert entry["missing_minutes"] == 414
    assert entry["anomalous_close_time_rows"] == 1
    assert entry["timestamp_unit"] == "ms"
    assert january["month"] == "2020-01"

    output_paths = monthly.historical_month_paths("2020-02", contract)
    assert all(path.is_file() for path in output_paths.values())
    assert prior_path.read_bytes() == before_prior
    event = json.loads((prior_path.parent / "continuations/2020-02-refetch.json").read_text())
    resume = json.loads((prior_path.parent / "network-ledger-continuation.json").read_text())
    assert event["http_gets_count"] == 6
    assert event["status"] == "refetch_passed"
    assert event["months"][-1]["month"] == "2020-02"
    assert resume["status"] == "ready_to_resume"
    assert resume["resume_from_month"] == "2020-03"
    assert resume["months"][-1]["month"] == "2020-02"
    assert event["attempts"] == resume["attempts"]
    assert event["attempts"][:4] == json.loads(before_prior)["attempts"]
    assert event["zip_body_bytes"] == 4_301_660 + len(archive)
    assert event["checksum_body_bytes"] == 176 + len(checksum)
    with monthly.HistoricalContinuationLedger(contract) as ledger:
        assert ledger.state["status"] == "in_progress"
        assert [row["month"] for row in ledger.state["months"]] == ["2020-01", "2020-02"]
        assert ledger.state["http_gets_count"] == 6


@pytest.mark.parametrize("failure", ["checksum_digest", "official_zip_hash", "zip_digest", "gap_audit"])
def test_d032_mismatch_fails_without_publishing_february(failure, continuation_fixture, monkeypatch):
    contract, prior_path, _ = continuation_fixture
    missing = 415 if failure == "gap_audit" else 414
    csv_bytes = _sparse_february(missing=missing)
    archive = _zip_february(csv_bytes)
    checksum = _checksum(archive, "2020-02")
    expected = dict(monthly._D032_FAILED_MONTH_EXPECTED)
    if failure == "checksum_digest":
        checksum = checksum[:-1] + b" "
    elif failure == "official_zip_hash":
        checksum = f"{'0' * 64} BTCUSDT-1m-2020-02.zip\n".encode("ascii")
        expected["checksum_sha256"] = data.sha256(checksum)
    elif failure == "zip_digest":
        archive = b"\1" + archive[1:]
    elif failure == "gap_audit":
        expected.update(zip_sha256=data.sha256(archive), checksum_sha256=data.sha256(checksum))
    monkeypatch.setattr(monthly, "_D032_FAILED_MONTH_EXPECTED", expected)
    if failure in {"official_zip_hash", "gap_audit"}:
        prior = json.loads(prior_path.read_bytes())
        prior["attempts"][2]["sha256"] = expected["checksum_sha256"]
        prior["attempts"][3]["sha256"] = expected["zip_sha256"]
        prior["failure"]["diagnostics"].update(
            zip_sha256=expected["zip_sha256"], checksum_text_sha256=expected["checksum_sha256"])
        _pin_synthetic_prior_transfers(monkeypatch, prior["attempts"])
        prior_path.write_bytes(monthly._historical_json_bytes(prior))
    monkeypatch.setattr(monthly, "_historical_disk_gate", lambda _contract: None)
    opener, requested = _responses(archive, checksum)

    with pytest.raises(data.DataValidationError):
        monthly.refetch_failed_historical_month_2020_02(
            contract, prior_ledger_path=prior_path, open_response=opener,
            implementation_commit_sha=SYNTHETIC_IMPLEMENTATION_COMMIT,
        )

    expected_gets = 1 if failure in {"checksum_digest", "official_zip_hash"} else 2
    assert len(requested) == expected_gets
    assert not any(path.exists() for path in monthly.historical_month_paths("2020-02", contract).values())
    continuation = prior_path.parent / "network-ledger-continuation.json"
    assert not continuation.exists()
    event = json.loads((prior_path.parent / "continuations/2020-02-refetch.json").read_text())
    assert event["status"] == "refetch_failed"


def test_strict_historical_parser_still_rejects_sparse_february(continuation_fixture):
    with pytest.raises(data.DataValidationError, match="gap|complete|missing|minute"):
        monthly.parse_historical_monthly_klines(_sparse_february(), "2020-02")


@pytest.mark.parametrize("ledger_path", [
    "results/phase3a-gate1/network-ledger-continuation.json",
    "results/phase3a-gate1/continuations/2020-02-refetch.json",
])
def test_generic_ledger_cannot_initialize_reserved_continuation_path(contract, ledger_path):
    target = data.REPOSITORY_ROOT / ledger_path
    assert not target.exists()
    with pytest.raises(data.DataValidationError):
        with monthly.HistoricalNetworkLedger(contract, ledger_path=ledger_path):
            pytest.fail("Generic ledger entered a reserved continuation path")
    assert not target.exists()
    assert not (data.REPOSITORY_ROOT / "results/phase3a-gate1/network-ledger.json").exists()
    assert not (data.REPOSITORY_ROOT / "results/phase3a-gate1/download.lock").exists()


@pytest.mark.parametrize("attempt_index", range(4))
@pytest.mark.parametrize("tamper", ["order", "url", "status", "bytes", "sha256"])
def test_each_prior_transfer_tamper_is_rejected_before_get(
    continuation_fixture, attempt_index, tamper,
):
    contract, prior_path, _ = continuation_fixture
    prior = json.loads(prior_path.read_bytes())
    attempts = prior["attempts"]
    attempt = attempts[attempt_index]
    if tamper == "order":
        other = (attempt_index + 1) % 4
        attempts[attempt_index], attempts[other] = attempts[other], attempts[attempt_index]
        for index, item in enumerate(attempts):
            item["id"] = index
    elif tamper == "url":
        suffix = ".zip.CHECKSUM" if attempt["kind"] == "checksum" else ".zip"
        attempt["url"] = monthly._historical_url("2020-03", suffix)
    elif tamper == "status":
        attempt["status"] = "failed"
    elif tamper == "bytes":
        # Preserve aggregate accounting while corrupting both same-kind transfers.
        other = attempts[attempt_index ^ 2]
        for key in ("body_bytes", "content_length"):
            attempt[key] += 1
            other[key] -= 1
    else:
        attempt["sha256"] = "f" * 64
    prior_path.write_bytes(monthly._historical_json_bytes(prior))
    before = prior_path.read_bytes()
    requested = []

    def forbidden_opener(request):
        requested.append(request.full_url)
        pytest.fail("A mutated prior transfer reached a GET")

    with pytest.raises(data.DataValidationError):
        monthly.refetch_failed_historical_month_2020_02(
            contract, prior_ledger_path=prior_path, open_response=forbidden_opener,
            implementation_commit_sha=SYNTHETIC_IMPLEMENTATION_COMMIT,
        )
    assert requested == []
    assert prior_path.read_bytes() == before
    assert not (prior_path.parent / "continuations/2020-02-refetch.json").exists()
    assert not (prior_path.parent / "network-ledger-continuation.json").exists()
    assert not any(path.exists() for path in monthly.historical_month_paths("2020-02", contract).values())


@pytest.mark.parametrize("ledger_name", ["event", "continuation"])
@pytest.mark.parametrize("attempt_index", [0, 4, 5])
def test_resume_rejects_event_continuation_attempt_disagreement(
    continuation_fixture, monkeypatch, ledger_name, attempt_index,
):
    contract, prior_path, _ = continuation_fixture
    archive = _zip_february(_sparse_february())
    checksum = _checksum(archive, "2020-02")
    monkeypatch.setattr(monthly, "_historical_disk_gate", lambda _contract: None)
    opener, requested = _responses(archive, checksum)
    monthly.refetch_failed_historical_month_2020_02(
        contract, prior_ledger_path=prior_path, open_response=opener,
        implementation_commit_sha=SYNTHETIC_IMPLEMENTATION_COMMIT,
    )
    event_path = prior_path.parent / "continuations/2020-02-refetch.json"
    continuation_path = prior_path.parent / "network-ledger-continuation.json"
    event = json.loads(event_path.read_bytes())
    continuation = json.loads(continuation_path.read_bytes())
    assert event["attempts"] == continuation["attempts"]
    target_path = event_path if ledger_name == "event" else continuation_path
    mutated = event if ledger_name == "event" else continuation
    # Keep totals, URLs and status valid so the cross-ledger equality is decisive.
    mutated["attempts"][attempt_index]["sha256"] = "f" * 64
    target_path.write_bytes(monthly._historical_json_bytes(mutated))
    before_continuation = continuation_path.read_bytes()

    with pytest.raises(data.DataValidationError):
        with monthly.HistoricalContinuationLedger(contract):
            pytest.fail("A continuation with inconsistent request history was opened")
    assert len(requested) == 2
    assert continuation_path.read_bytes() == before_continuation
    assert not (prior_path.parent / "download.lock").exists()
