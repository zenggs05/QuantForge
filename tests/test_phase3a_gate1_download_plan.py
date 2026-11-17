"""Gate 1 downloader guards and bounded synthetic transport tests."""

from __future__ import annotations

import http.client
import importlib.util
import io
from pathlib import Path
import socket
import urllib.request
import zipfile

import pytest

from quantforge import data, data_monthly as monthly, research_policy
from gate1_fixtures import FakeResponse, copy_frozen_contract, synthetic_archive, synthetic_month_csv


@pytest.fixture
def contract(tmp_path, monkeypatch):
    source_root = data.REPOSITORY_ROOT
    copy_frozen_contract(source_root, tmp_path)
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    return monthly.load_historical_download_contract()


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Gate 1 synthetic tests must never open a network socket")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.mark.parametrize("month", ["2026-07", "2026-08", "2026-09", "2026-10"])
def test_forbidden_month_is_rejected_before_any_path_or_network_io(month, monkeypatch):
    monkeypatch.setattr(data, "REPOSITORY_ROOT", Path.cwd())
    monkeypatch.setattr(monthly, "_open_historical_response", lambda *_: pytest.fail("network opened"))
    with pytest.raises(research_policy.ResearchPolicyError, match="historical allowlist"):
        monthly.download_historical_month(month, {"output_root": Path.cwd()})


def test_contract_has_correct_68_month_distribution_and_request_cap(contract):
    assert len(contract["months"]) == 68
    assert sum(month < "2025-01" for month in contract["months"]) == 60
    assert sum(month >= "2025-01" for month in contract["months"]) == 8
    assert contract["http_get_cap"] == 408
    assert contract["per_checksum_limit_bytes"] == 4096


def test_plan_is_offline_and_does_not_call_any_opener(monkeypatch):
    monkeypatch.setattr(urllib.request, "build_opener", lambda *_: pytest.fail("plan opened network"))
    plan = monthly.plan_historical_download()
    assert plan["network_requests"] == 0
    assert len(plan["months"]) == 68
    assert plan["months"][0]["month"] == "2020-01"
    assert plan["months"][-1]["month"] == "2025-08"


def test_exact_archive_checksum_and_timestamp_audit_are_synthetic(contract):
    archive = synthetic_archive("2024-12")
    checksum = f"{data.sha256(archive)}  BTCUSDT-1m-2024-12.zip\n".encode()
    frame, audit = monthly.inspect_historical_archive(archive, checksum, "2024-12", contract)
    assert len(frame) == 31 * 1440
    assert audit["timestamp_unit"] == "ms"
    assert audit["zip_sha256"] == data.sha256(archive)


def test_wrong_checksum_and_zip_member_fail_closed(contract):
    archive = synthetic_archive("2024-12")
    bad = ("0" * 64 + "  BTCUSDT-1m-2024-12.zip\n").encode()
    with pytest.raises(data.DataValidationError, match="SHA256"):
        monthly.inspect_historical_archive(archive, bad, "2024-12", contract)
    import io
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zipped:
        zipped.writestr("../escape.csv", b"bad")
    checksum = f"{data.sha256(out.getvalue())}  BTCUSDT-1m-2024-12.zip\n".encode()
    with pytest.raises(data.DataValidationError, match="expected CSV"):
        monthly.inspect_historical_archive(out.getvalue(), checksum, "2024-12", contract)


@pytest.mark.parametrize("bad_header", ["redirect", "missing_length", "oversized"])
def test_historical_response_rejected_before_body_read(contract, monkeypatch, bad_header):
    month = "2024-12"
    archive = synthetic_archive(month)
    url = monthly._historical_url(month)
    class BadResponse(FakeResponse):
        def __init__(self):
            super().__init__(url, archive)
            if bad_header == "redirect":
                self.url = url + "/redirect"
            elif bad_header == "missing_length":
                self.headers.pop("Content-Length")
            else:
                self.headers["Content-Length"] = str(contract["per_zip_limit_bytes"] + 1)

        def read1(self, count):
            pytest.fail("Invalid HTTP headers reached body reads")

    monkeypatch.setattr(monthly, "_open_historical_response", lambda request: BadResponse())
    with monthly.HistoricalNetworkLedger(contract) as ledger:
        with pytest.raises(data.DataValidationError):
            monthly._historical_fetch_once(url, "zip", month, contract, ledger, 1)


def test_ledger_counts_gets_and_charges_payload_bytes(contract, monkeypatch):
    month = "2024-12"
    archive = synthetic_archive(month)
    checksum = f"{data.sha256(archive)}  BTCUSDT-1m-{month}.zip\n".encode()
    responses = {
        monthly._historical_url(month, ".zip.CHECKSUM"): FakeResponse(
            monthly._historical_url(month, ".zip.CHECKSUM"), checksum),
        monthly._historical_url(month): FakeResponse(monthly._historical_url(month), archive),
    }
    monkeypatch.setattr(monthly, "_open_historical_response", lambda request: responses[request.full_url])
    with monthly.HistoricalNetworkLedger(contract) as ledger:
        got_checksum, _ = monthly._historical_fetch_once(monthly._historical_url(month, ".zip.CHECKSUM"), "checksum", month, contract, ledger, 1)
        got_archive, _ = monthly._historical_fetch_once(monthly._historical_url(month), "zip", month, contract, ledger, 1)
    assert got_checksum == checksum and got_archive == archive
    assert ledger.state["http_gets_count"] == 2
    assert ledger.state["checksum_body_bytes"] == len(checksum)
    assert ledger.state["zip_body_bytes"] == len(archive)


def test_whole_synthetic_month_download_publishes_only_verified_files(contract, monkeypatch):
    month = "2020-01"
    archive = synthetic_archive(month)
    checksum = f"{data.sha256(archive)}  BTCUSDT-1m-{month}.zip\n".encode()
    requested = []

    def opener(request):
        requested.append(request.full_url)
        body = checksum if request.full_url.endswith(".CHECKSUM") else archive
        return FakeResponse(request.full_url, body)

    monkeypatch.setattr(monthly, "_open_historical_response", opener)
    with monthly.HistoricalNetworkLedger(contract) as ledger:
        entry = monthly.download_historical_month(month, contract, ledger=ledger)
    assert entry["month"] == month
    assert entry["timestamp_unit"] == "ms"
    assert len(requested) == 2
    assert ledger.state["http_gets_count"] == 2
    paths = monthly._historical_paths(month, contract)
    assert all(path.is_file() for path in paths.values())
    checked = monthly.verify_historical_month(month, contract, entry)
    assert checked["reverified"] is True
    assert checked["parquet_sha256"] == entry["parquet_sha256"]


def test_transient_failure_retries_whole_month_and_counts_each_get(contract, monkeypatch):
    month = "2020-01"
    archive = synthetic_archive(month)
    checksum = f"{data.sha256(archive)}  BTCUSDT-1m-{month}.zip\n".encode()
    requested = []
    sleeps = []
    failed_once = False

    def opener(request):
        nonlocal failed_once
        requested.append(request.full_url)
        if request.full_url.endswith(".zip") and not failed_once:
            failed_once = True
            raise TimeoutError("Synthetic transport interruption")
        body = checksum if request.full_url.endswith(".CHECKSUM") else archive
        return FakeResponse(request.full_url, body)

    monkeypatch.setattr(monthly, "_open_historical_response", opener)
    monkeypatch.setattr(monthly.time, "sleep", sleeps.append)
    with monthly.HistoricalNetworkLedger(contract) as ledger:
        entry = monthly.download_historical_month(month, contract, ledger=ledger)
    assert entry["attempts"] == 2
    assert len(requested) == ledger.state["http_gets_count"] == 4
    assert sleeps == [contract["backoff_seconds"][0]]
    assert sum(item["status"] == "failed" for item in ledger.state["attempts"]) == 1
    assert sum(item["kind"] == "checksum" for item in ledger.state["attempts"]) == 2
    assert ledger.state["checksum_body_bytes"] == 2 * len(checksum)


def test_request_cap_fails_before_opening_next_get(contract, monkeypatch):
    month = "2024-12"
    monkeypatch.setattr(monthly, "_open_historical_response", lambda *_: pytest.fail("GET cap was bypassed"))
    with monthly.HistoricalNetworkLedger(contract) as ledger:
        ledger.state["http_gets_count"] = contract["http_get_cap"]
        with pytest.raises(monthly.DownloadBudgetError, match="GET cap"):
            monthly._historical_fetch_once(monthly._historical_url(month), "zip", month, contract, ledger, 1)
        ledger.state["http_gets_count"] = 0


def test_existing_month_artifact_is_never_overwritten(contract, monkeypatch):
    month = "2020-01"
    paths = monthly._historical_paths(month, contract)
    paths["raw"].parent.mkdir(parents=True)
    paths["raw"].write_bytes(b"existing synthetic marker")
    monkeypatch.setattr(monthly, "_open_historical_response", lambda *_: pytest.fail("existing output triggered a request"))
    with monthly.HistoricalNetworkLedger(contract) as ledger:
        with pytest.raises(data.DataValidationError, match="overwrite"):
            monthly.download_historical_month(month, contract, ledger=ledger)
    assert paths["raw"].read_bytes() == b"existing synthetic marker"


def test_three_failed_month_attempts_stop_without_fourth_get(contract, monkeypatch):
    calls, sleeps = [], []

    def interrupted(request):
        calls.append(request.full_url)
        raise TimeoutError("Synthetic outage")

    monkeypatch.setattr(monthly, "_open_historical_response", interrupted)
    monkeypatch.setattr(monthly.time, "sleep", sleeps.append)
    with monthly.HistoricalNetworkLedger(contract) as ledger:
        with pytest.raises(TimeoutError, match="Synthetic outage"):
            monthly.download_historical_month("2020-01", contract, ledger=ledger)
    assert len(calls) == ledger.state["http_gets_count"] == 3
    assert sleeps == [1, 4]
    assert ledger.state["status"] == "failed"
    assert ledger.state["months"] == []


def test_unknown_read_error_keeps_conservative_reservation(contract, monkeypatch):
    month = "2020-01"
    url = monthly._historical_url(month, ".zip.CHECKSUM")

    class UnknownPartial(FakeResponse):
        def read1(self, count):
            raise TimeoutError("Unknown partial body")

    monkeypatch.setattr(monthly, "_open_historical_response", lambda request: UnknownPartial(url, b"x" * 32))
    with monthly.HistoricalNetworkLedger(contract) as ledger:
        with pytest.raises(TimeoutError, match="Unknown partial"):
            monthly._historical_fetch_once(url, "checksum", month, contract, ledger, 1)
    assert ledger.state["checksum_body_bytes"] == 0
    assert ledger.state["checksum_reserved_bytes"] == 32
    with pytest.raises(monthly.DownloadBudgetError, match="resume|reset"):
        with monthly.HistoricalNetworkLedger(contract):
            pytest.fail("A stopped ledger was resumed")


def test_zip_reservation_cannot_cross_frozen_cumulative_budget(contract):
    month = "2020-01"
    with monthly.HistoricalNetworkLedger(contract) as ledger:
        attempt = ledger.start_get(monthly._historical_url(month), "zip", month)
        ledger.reserve("zip", contract["zip_budget_bytes"], attempt)
        with pytest.raises(monthly.DownloadBudgetError, match="byte budget"):
            ledger.reserve("zip", 1, attempt)
        assert ledger.zip_remaining == 0
        assert ledger.state["zip_reserved_bytes"] == contract["zip_budget_bytes"]


def test_forged_url_and_output_root_rejected_before_io(contract, monkeypatch):
    monkeypatch.setattr(monthly, "_open_historical_response", lambda *_: pytest.fail("Forged URL reached network"))
    with monthly.HistoricalNetworkLedger(contract) as ledger:
        with pytest.raises(data.DataValidationError, match="allowlist"):
            monthly._historical_fetch_once("https://example.invalid/2020-01.zip", "zip", "2020-01", contract, ledger, 1)
        assert ledger.state["http_gets_count"] == 0
    with pytest.raises(research_policy.ResearchPolicyError, match="fixed development path"):
        monthly.load_historical_download_contract(output_root="data/raw/../forbidden")


def test_modified_budget_contract_cannot_reach_network(contract, monkeypatch):
    contract["zip_budget_bytes"] += 1
    monkeypatch.setattr(monthly, "_open_historical_response", lambda *_: pytest.fail("Mutated contract opened network"))
    with pytest.raises(data.DataValidationError, match="contract"):
        monthly.download_historical_month("2020-01", contract)


def test_symlink_alias_rejected_before_archive_access(contract, monkeypatch):
    raw = monthly._historical_paths("2020-01", contract)["raw"]
    original = Path.resolve

    def redirected(path, *args, **kwargs):
        if path == raw:
            return raw.parent / "redirected.zip"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", redirected)
    monkeypatch.setattr(monthly, "_open_historical_response", lambda *_: pytest.fail("Alias reached network"))
    with pytest.raises(research_policy.ResearchPolicyError, match="symlinks"):
        monthly.download_historical_month("2020-01", contract)


def test_partial_read_is_recorded_and_no_partial_file_is_published(contract, monkeypatch):
    month = "2024-12"
    url = monthly._historical_url(month, ".zip.CHECKSUM")
    class Broken(FakeResponse):
        def read1(self, count):
            raise http.client.IncompleteRead(b"partial", count)
    monkeypatch.setattr(monthly, "_open_historical_response", lambda request: Broken(url, b"x" * 32))
    with monthly.HistoricalNetworkLedger(contract) as ledger:
        with pytest.raises(http.client.IncompleteRead):
            monthly._historical_fetch_once(url, "checksum", month, contract, ledger, 1)
    assert ledger.state["checksum_body_bytes"] == 7
    assert ledger.state["checksum_reserved_bytes"] == 0
    assert not list(contract["output_root"].glob("*.part"))


@pytest.mark.parametrize("defect", ["missing", "duplicate"])
def test_bad_calendar_stops_without_retry_or_publishing_month(contract, monkeypatch, defect):
    month = "2020-01"
    rows = synthetic_month_csv(month).splitlines()
    if defect == "missing":
        rows.pop(100)
    else:
        rows[100] = rows[99]
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr(f"BTCUSDT-1m-{month}.csv", b"\n".join(rows) + b"\n")
    archive = stream.getvalue()
    checksum = f"{data.sha256(archive)}  BTCUSDT-1m-{month}.zip\n".encode()
    requests = []

    def opener(request):
        requests.append(request.full_url)
        return FakeResponse(request.full_url, checksum if request.full_url.endswith(".CHECKSUM") else archive)

    monkeypatch.setattr(monthly, "_open_historical_response", opener)
    monkeypatch.setattr(monthly.time, "sleep", lambda *_: pytest.fail("An integrity failure retried"))
    with monthly.HistoricalNetworkLedger(contract) as ledger:
        with pytest.raises(data.DataValidationError, match="Monthly kline validation"):
            monthly.download_historical_month(month, contract, ledger=ledger)
        assert ledger.state["status"] == "failed"
        assert ledger.state["market_rows_read"] == len(rows)
    assert len(requests) == 2
    assert not any(path.exists() for path in monthly._historical_paths(month, contract).values())
    assert not list(contract["output_root"].glob("*.part"))


@pytest.mark.parametrize("failure_point", ["stage", "publish"])
def test_file_publication_failure_cleans_all_staged_and_published_month_files(
        contract, monkeypatch, failure_point):
    month = "2020-01"
    archive = synthetic_archive(month)
    checksum = f"{data.sha256(archive)}  BTCUSDT-1m-{month}.zip\n".encode()

    def opener(request):
        body = checksum if request.full_url.endswith(".CHECKSUM") else archive
        return FakeResponse(request.full_url, body)

    monkeypatch.setattr(monthly, "_open_historical_response", opener)
    if failure_point == "stage":
        original = monthly._stage_historical_bytes

        def fail_parquet_stage(final_path, payload, suffix):
            if final_path.suffix == ".parquet":
                raise OSError("Synthetic parquet staging failure")
            return original(final_path, payload, suffix)

        monkeypatch.setattr(monthly, "_stage_historical_bytes", fail_parquet_stage)
    else:
        original = monthly._publish_historical_part

        def fail_checksum_publish(temporary, final_path):
            if final_path.suffix == ".CHECKSUM":
                raise OSError("Synthetic checksum publication failure")
            return original(temporary, final_path)

        monkeypatch.setattr(monthly, "_publish_historical_part", fail_checksum_publish)

    with monthly.HistoricalNetworkLedger(contract) as ledger:
        with pytest.raises(OSError, match="Synthetic .* failure"):
            monthly.download_historical_month(month, contract, ledger=ledger)
    paths = monthly._historical_paths(month, contract)
    assert not any(path.exists() for path in paths.values())
    assert not list(contract["output_root"].glob("*.part"))
    assert ledger.state["status"] == "failed"
    assert ledger.state["months"] == []


def test_oversized_checksum_stops_before_read_or_zip_request(contract, monkeypatch):
    requests = []

    class TooLarge(FakeResponse):
        def read1(self, count):
            pytest.fail("Oversized CHECKSUM body was read")

    def opener(request):
        requests.append(request.full_url)
        return TooLarge(request.full_url, b"", content_length=4097)

    monkeypatch.setattr(monthly, "_open_historical_response", opener)
    with monthly.HistoricalNetworkLedger(contract) as ledger:
        with pytest.raises(monthly.DownloadBudgetError, match="per-file"):
            monthly.download_historical_month("2020-01", contract, ledger=ledger)
    assert len(requests) == 1
    assert requests[0].endswith(".CHECKSUM")
    assert ledger.state["checksum_body_bytes"] == 0


@pytest.fixture
def cli_module():
    location = Path(__file__).resolve().parents[1] / "scripts/download_historical.py"
    spec = importlib.util.spec_from_file_location("gate1_cli_under_test", location)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("filename,name", [
    ("/env/site-packages/sklearn/linear_model/_ridge.py", "fit"),
    ("/project/src/quantforge/models/baselines.py", "fit"),
    ("/project/src/quantforge/diagnostics/forecast_tests.py", "clark_west_differential"),
    ("/project/src/quantforge/phase3a_power.py", "build_power_table"),
])
def test_cli_profile_guard_rejects_research_calls(cli_module, filename, name):
    from types import SimpleNamespace
    frame = SimpleNamespace(f_code=SimpleNamespace(co_filename=filename, co_name=name))
    with pytest.raises(RuntimeError, match="prohibits research"):
        cli_module._no_research(frame, "call", None)


def test_cli_profile_guard_allows_only_integrity_example(cli_module):
    from types import SimpleNamespace
    frame = SimpleNamespace(f_code=SimpleNamespace(
        co_filename="/project/src/quantforge/data_monthly.py", co_name="parse_historical_monthly_klines"))
    assert cli_module._no_research(frame, "call", None) is None


@pytest.mark.parametrize("args", [[], ["--config", "other.toml", "--output-root", "elsewhere"]])
def test_cli_requires_exact_explicit_contract_arguments(cli_module, args):
    with pytest.raises(SystemExit) as exc:
        cli_module.main(args)
    assert exc.value.code == 2
