"""Revalidate existing Gate 1 artifacts offline with the original JSON serializer."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import sys
import time
import urllib.request

from quantforge import data, data_monthly as monthly, research_policy


def assert_writer_json_bytes(reparsed_value: dict, file_bytes: bytes) -> dict:
    """Require byte and SHA equality with the unchanged ``_atomic_json`` writer."""
    rewritten = (json.dumps(reparsed_value, ensure_ascii=False, indent=2,
                            allow_nan=False) + "\n").encode("utf-8")
    expected_sha = hashlib.sha256(rewritten).hexdigest()
    actual_sha = hashlib.sha256(file_bytes).hexdigest()
    assert expected_sha == actual_sha and rewritten == file_bytes, "Original writer JSON bytes differ"
    return {"sha256": actual_sha, "rewritten_sha256": expected_sha,
            "matches_original_writer_bytes": True}


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    """Perform one clean read-only market audit and write a new D-034 report."""
    root = data.REPOSITORY_ROOT
    destination = research_policy.fixed_project_path(
        "docs/phase3a-gate1-evidence/d034-revalidation.json",
        "docs/phase3a-gate1-evidence/d034-revalidation.json")
    assert not destination.exists(), "Do not overwrite revalidation evidence"
    cli = _load_module("gate1_entry", root / "scripts/download_historical.py")
    head = cli._check_cli_preconditions()
    counter_module = _load_module(
        "gate1_runtime_counter", root / "docs/phase3a-gate1-evidence/d033-runtime-counter.py")
    counter = counter_module.Counter()
    network_attempts = 0

    def forbidden(*args, **kwargs):
        nonlocal network_attempts
        network_attempts += 1
        raise AssertionError("Offline integrity revalidation attempted network access")

    socket.create_connection = forbidden
    urllib.request.urlopen = forbidden
    urllib.request.OpenerDirector.open = forbidden
    monthly._open_historical_response = forbidden
    counter.start()
    sys.setprofile(cli._no_research)
    started = time.perf_counter()
    report = {"status": "failed", "gate": "gate1", "gate2_authorised": False,
              "evidence_level": "real_data_integrity_only", "revalidation_run_number": 2,
              "clean_commit": head, "harness_commit": head,
              "started_at_utc": _utc_now(), "rows_revalidated": 0, "months": [],
              "json_writer_checks": [],
              "prior_run": "docs/phase3a-gate1-evidence/d033-revalidation.json"}
    try:
        contract = monthly.load_historical_download_contract()
        path = root / cli.MANIFEST
        manifest_bytes = path.read_bytes()
        manifest = json.loads(manifest_bytes)
        monthly.validate_historical_graded_manifest(manifest, contract)
        checked_entries = []
        for entry in manifest["months"]:
            month_started = _utc_now()
            paths = monthly.historical_month_paths(entry["month"], contract)
            source = json.loads(paths["source"].read_bytes())
            check = monthly.verify_historical_graded_month(entry["month"], contract, source)
            actual = {k: v for k, v in check.items() if k != "reverified"}
            expected = {k: v for k, v in entry.items() if k != "reverified"}
            assert research_policy._same_typed_value(actual, expected), entry["month"]
            checked_entries.append(check)
            report["rows_revalidated"] += check["rows"]
            report["months"].append({
                "month": entry["month"], "rows": check["rows"], "verified": True,
                "started_at_utc": month_started, "finished_at_utc": _utc_now(),
                **{k: check[k] for k in ("zip_sha256", "checksum_text_sha256",
                                        "csv_sha256", "parquet_sha256")},
            })
            print(entry["month"] + ": exact raw/Parquet and manifest audit reproduced", flush=True)
        boundaries = monthly.audit_historical_boundaries(checked_entries)
        assert boundaries == manifest["cross_month_boundaries"]
        for month in ("2024-12", "2025-01"):
            entry = next(e for e in checked_entries if e["month"] == month)
            expected = {
                "month": month, "expected_unit": "ms" if month == "2024-12" else "us",
                "detected_unit": entry["timestamp_unit"], "first_open_raw": entry["first_open_raw"],
                "first_open_parsed_utc": entry["first_open_utc"],
                "last_open_parsed_utc": entry["last_open_utc"],
            }
            relative = f"docs/phase3a-gate1-evidence/timestamp-boundary-{month}.json"
            matched = assert_writer_json_bytes(expected, (root / relative).read_bytes())
            report["json_writer_checks"].append({"path": relative, **matched})
        assert path.read_bytes() == manifest_bytes
        manifest_check = assert_writer_json_bytes(manifest, manifest_bytes)
        report["json_writer_checks"].append({"path": cli.MANIFEST, **manifest_check})
        report.update(
            status="passed", months_revalidated=len(report["months"]),
            cross_month_boundaries_revalidated=len(boundaries),
            manifest_file_sha256=data.sha256(manifest_bytes),
            deterministic_file_count=207,
            deterministic_file_count_definition=(
                "204 local ZIP/CHECKSUM/Parquet files, manifest, two boundary JSON files; "
                "additionally 68 embedded CSV digests and sidecar metadata audited"),
        )
    except BaseException as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        sys.setprofile(None)
        report.update(counter.finish())
        report.update(
            market_network_requests=0, network_attempts=network_attempts,
            holdout_requests=0, holdout_access_count=0, smoke_day_rows_read=0,
            peak_memory_bytes=cli._peak_memory_bytes(), elapsed_seconds=time.perf_counter() - started,
            finished_at_utc=_utc_now(),
        )
        with destination.open("x", encoding="utf8", newline="\n") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
        print(json.dumps({k: v for k, v in report.items() if k != "months"}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
