"""Execute the fixed Gate 1 acquisition; no research or configurable dates."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import os
import subprocess
import sys
import time

from quantforge import data, data_monthly, research_policy

RAW_ROOT = "data/raw/binance/spot/BTCUSDT/1m/historical"
MANIFEST = "data/processed/binance/spot/BTCUSDT/1m/historical/historical-manifest.json"
PROTOCOL_COMMIT = "927145671910df600a1081d4ce6478439ca4b0a4"


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=data.REPOSITORY_ROOT, text=True).strip()


def _no_research(frame, event, arg):
    """Fail before entering research functions in this integrity-only process."""
    if event != "call":
        return
    filename = frame.f_code.co_filename.replace("\\", "/").lower()
    name = frame.f_code.co_name
    prohibited = (
        "/quantforge/diagnostics/" in filename
        or "/quantforge/online/" in filename
        or any(f"/quantforge/{module}.py" in filename for module in (
            "phase3a_runner", "phase3a_power", "research_experiment", "power_reanalysis"))
        or ("/quantforge/models/baselines.py" in filename and name in {"fit", "regression_metrics"})
        or ("/sklearn/linear_model/" in filename and name == "fit")
    )
    if prohibited:
        raise RuntimeError(f"Gate 1 prohibits research computation: {filename}:{name}")


def _peak_memory_bytes() -> int:
    """Read the Windows process peak working set without a new dependency."""
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                "PagefileUsage", "PeakPagefileUsage")]

    if os.name != "nt":
        import resource
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024))
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(counters.PeakWorkingSetSize)


def _write_new(relative: str, payload: dict):
    path = research_policy.fixed_project_path(relative, relative)
    if path.exists():
        raise data.DataValidationError(f"Gate 1 refuses to overwrite {relative}")
    data_monthly._atomic_json(path, payload)


def _check_cli_preconditions() -> str:
    if _git("branch", "--show-current") != "phase3a-protocol-and-impl":
        raise RuntimeError("Gate 1 requires its designated branch")
    if _git("status", "--porcelain", "--untracked-files=normal"):
        raise RuntimeError("Gate 1 requires a clean committed worktree before acquisition")
    return _git("rev-parse", "HEAD")


def _assert_outputs_absent():
    planned_artifacts = [MANIFEST] + [
        f"docs/phase3a-gate1-evidence/timestamp-boundary-{month}.json"
        for month in ("2024-12", "2025-01")]
    for relative in planned_artifacts:
        if research_policy.fixed_project_path(relative, relative).exists():
            raise data.DataValidationError(f"Gate 1 output already exists: {relative}")


def _write_timestamp_evidence(entries: list[dict]) -> list[str]:
    owned = []
    try:
        for entry in entries:
            if entry["month"] in {"2024-12", "2025-01"}:
                audit = entry["audit"]
                relative = f"docs/phase3a-gate1-evidence/timestamp-boundary-{entry['month']}.json"
                _write_new(relative, {
                    "month": entry["month"],
                    "expected_unit": "ms" if entry["month"] == "2024-12" else "us",
                    "detected_unit": entry["timestamp_unit"],
                    "first_open_raw": audit["first_open_raw"],
                    "first_open_parsed_utc": entry["first_open_utc"],
                    "last_open_parsed_utc": entry["last_open_utc"],
                })
                owned.append(relative)
    except BaseException:
        for relative in reversed(owned):
            research_policy.fixed_project_path(relative, relative).unlink(missing_ok=True)
        raise
    return owned


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, choices=["configs/history-extension.toml"])
    parser.add_argument("--output-root", required=True, choices=[RAW_ROOT])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--only-2020-02-refetch", action="store_true")
    mode.add_argument("--resume-from", choices=["2020-03", "2022-06"])
    args = parser.parse_args(argv)
    started = time.perf_counter()
    sys.setprofile(_no_research)
    try:
        contract = data_monthly.load_historical_download_contract(args.config, args.output_root)
        implementation = _check_cli_preconditions()
        _assert_outputs_absent()
        if args.only_2020_02_refetch:
            entry = data_monthly.refetch_failed_historical_month_2020_02(
                contract, implementation_commit_sha=implementation)
            print(f"2020-02: refetch validated; total GETs={6}; status={entry['status']}", flush=True)
            return 0
        if args.resume_from is not None:
            if args.resume_from == "2022-06":
                from quantforge.historical_recovery import HistoricalD033Ledger
                context = HistoricalD033Ledger(contract, implementation)
            else:
                context = data_monthly.HistoricalContinuationLedger(contract)
            with context as ledger:
                entries = list(ledger.state["months"])
                owned_artifacts = []
                try:
                    for month in contract["months"][contract["months"].index(args.resume_from):]:
                        print(f"{month}: acquisition starting", flush=True)
                        entry = data_monthly.download_historical_graded_month(month, contract, ledger=ledger)
                        entries.append(entry)
                        peak = _peak_memory_bytes()
                        ledger.state["peak_process_memory_bytes"] = peak
                        ledger.state["elapsed_seconds"] = time.perf_counter() - started
                        ledger.save()
                        if peak > 2_000_000_000:
                            raise data_monthly.DownloadBudgetError("Gate 1 peak process memory exceeds 2 GB")
                        print(f"{month}: validated {entry['rows']} rows; total GETs={ledger.state['http_gets_count']}", flush=True)
                    completion = {"status": "passed", "completed_at_utc": data_monthly._now(),
                                  "elapsed_seconds": time.perf_counter() - started,
                                  "peak_process_memory_bytes": ledger.state.get("peak_process_memory_bytes", 0)}
                    summary = {**ledger.state, **completion, "final_commit_sha": implementation}
                    manifest = data_monthly.build_historical_graded_manifest(
                        entries, contract, ledger_summary=summary, protocol_commit=PROTOCOL_COMMIT)
                    data_monthly.validate_historical_graded_manifest(manifest, contract)
                    owned_artifacts.extend(_write_timestamp_evidence(entries))
                    _write_new(MANIFEST, manifest)
                    owned_artifacts.append(MANIFEST)
                    ledger.finish(implementation)
                    if data_monthly.get_ledger_persistence_diagnostics()["permission_error_hard_fail_count"]:
                        raise RuntimeError("Gate 1 ledger persistence hard failure was observed")
                except BaseException as exc:
                    for relative in reversed(owned_artifacts):
                        artifact_path = research_policy.fixed_project_path(relative, relative)
                        if artifact_path.exists():
                            artifact_path.unlink()
                    ledger.state.update(status="failed", stopped_at_utc=data_monthly._now(),
                                        elapsed_seconds=time.perf_counter() - started,
                                        stop_error={"type": type(exc).__name__, "message": str(exc)},
                                        peak_process_memory_bytes=_peak_memory_bytes())
                    ledger.save()
                    raise
            return 0
    finally:
        sys.setprofile(None)


if __name__ == "__main__":
    raise SystemExit(main())
