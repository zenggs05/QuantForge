"""Execute the D-039 acquisition once, then reproduce its comparison offline."""
from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
import urllib.request

from quantforge import archive_verification as av, data

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/phase3a-daily-verification-evidence"
MANIFEST = ROOT / "data/processed/binance/spot/BTCUSDT/1m/historical/historical-manifest.json"
RAW = ROOT / "data/raw/binance/spot/BTCUSDT/1m/historical"
MANIFEST_SHA = "71920d90d95a3d6600db3b57540865949b8784ce8753b0dccfbea93d7bb1fa02"
ISSUE_SHA = "59543b0862faf5953ce8c4e4efe3892d681711de345ced3ec5cf4dd9185a7154"
ISSUE_DAILY = "1608556740000,22647.85000000,22666.66000000,22628.62000000,22637.92000000,109.87666500,1608556799999,2488573.22763245,2138,49.06835600,1111469.47191801,0"
ISSUE_MONTHLY = "1608556740000,22647.85000000,22661.57000000,22633.30000000,22646.53000000,47.74209100,1608556799999,1081400.91111170,760,16.52699800,374330.37124588,0"


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def write_new(name: str, payload: bytes) -> None:
    target = av.safe_verify_path(EVIDENCE / name, root=EVIDENCE)
    if target.parent != EVIDENCE:
        raise av.ArchiveVerificationError("Invalid evidence target")
    av.safe_verify_path(EVIDENCE, root=EVIDENCE).mkdir(parents=True, exist_ok=True)
    target = av.safe_verify_path(target, root=EVIDENCE)
    with target.open("xb") as stream:
        stream.write(payload)


def csv_bytes(rows: list[dict], columns: list[str]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def preflight() -> dict:
    if Path.cwd().resolve() != ROOT or ROOT != Path(r"D:\zgs_project\QuantForge-3a"):
        raise av.ArchiveVerificationError("Wrong worktree")
    branch = subprocess.check_output(["git", "branch", "--show-current"], text=True).strip()
    if branch != "phase3a-protocol-and-impl":
        raise av.ArchiveVerificationError("Wrong branch")
    if Path(sys.executable).resolve() != ROOT / ".venv/Scripts/python.exe":
        raise av.ArchiveVerificationError("Use the project-local virtual environment")
    decisions = (ROOT / "research/DECISION_LOG.md").read_text(encoding="utf-8")
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    if ("## D-039" not in decisions or str(av.VERIFY_DIR) not in decisions
            or "D-039" not in (ROOT / "AGENTS.md").read_text(encoding="utf-8")
            or "docs/phase3a-daily-verification-record.md text eol=lf" not in attributes
            or "docs/phase3a-daily-verification-evidence/* -text" not in attributes):
        raise av.ArchiveVerificationError("D-039 scope prerequisites are missing")
    raw = MANIFEST.read_bytes()
    if av.sha256(raw) != MANIFEST_SHA:
        raise av.ArchiveVerificationError("Historical manifest fingerprint mismatch")
    pairs = re.findall(r"\| `(src/[^`]+)` \| `([0-9a-f]{64})` \|", (ROOT / "docs/confirmatory-protocol-v1.md").read_text(encoding="utf-8"))
    if len(pairs) != 7:
        raise av.ArchiveVerificationError("Expected seven code fingerprints")
    for path, expected in pairs:
        if av.sha256((ROOT / path).read_bytes()) != expected:
            raise av.ArchiveVerificationError(f"Code fingerprint mismatch: {path}")
    return json.loads(raw)


def isolation_snapshot(manifest: dict) -> dict:
    paths = [MANIFEST]
    for entry in manifest["months"]:
        month = entry["month"]
        paths.extend([RAW / f"{month}.zip", RAW / f"{month}.zip.CHECKSUM", RAW / f"{month}.zip.source.json", MANIFEST.parent / f"{month}.parquet"])
    if len(paths) != 273 or not all(p.is_file() for p in paths):
        raise av.ArchiveVerificationError("Expected all 273 historical artifacts")
    result = {"historical_artifacts": av.snapshot_paths(paths), "other_worktree_metadata": {}}
    for label, worktree in (("main", Path(r"D:\zgs_project\QuantForge")), ("phase2f", Path(r"D:\zgs_project\QuantForge-2f"))):
        groups = {}
        for name in (".venv", "results", "data"):
            directory = worktree / name
            entries = []
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    stat = path.stat()
                    entries.append({"path": path.relative_to(directory).as_posix(), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns})
            groups[name] = {"exists": directory.exists(), "files": entries}
        result["other_worktree_metadata"][label] = groups
    return result


def inventory() -> list[dict]:
    rows = []
    for path in sorted(av.VERIFY_DIR.rglob("*")):
        if path.is_file():
            checked = av.safe_verify_path(path)
            payload = checked.read_bytes()
            rows.append({"name": path.relative_to(av.VERIFY_DIR).as_posix(), "bytes": len(payload), "sha256": av.sha256(payload)})
    return rows


def comparison_artifacts(manifest: dict, downloads: dict) -> dict[str, bytes]:
    months = {entry["month"]: entry for entry in manifest["months"]}
    sources, summaries, crosstabs, diffs, intervals, flags = [], [], [], [], [], []
    issue = {"day": "2020-12-21", "status": "not_compared", "expected_zip_sha256": ISSUE_SHA}
    cache = {}
    for day in av.DATES:
        acquisition = downloads["dates"][day]
        if acquisition["status"] != "passed":
            summaries.append({"day": day, "status": "not_compared", "download_status": acquisition["status"], "reason": acquisition.get("error")})
            continue
        month = day[:7]
        if month not in cache:
            zip_bytes = (RAW / f"{month}.zip").read_bytes()
            if av.sha256(zip_bytes) != months[month]["zip_sha256"]:
                raise av.ArchiveVerificationError(f"Monthly ZIP fingerprint mismatch: {month}")
            monthly_csv = data.extract_csv(zip_bytes, f"BTCUSDT-1m-{month}.zip")
            cache[month] = (zip_bytes, monthly_csv)
        monthly_zip, monthly_csv = cache[month]
        name = av.archive_name(day)
        daily_zip = av.safe_verify_path(name).read_bytes()
        checksum = av.safe_verify_path(name + ".CHECKSUM").read_bytes()
        digest = av.verify_checksum(daily_zip, checksum, name)
        if digest != acquisition["sha256"]:
            raise av.ArchiveVerificationError(f"Daily ZIP changed: {day}")
        daily_csv = av.extract_expected_csv(daily_zip, day)
        monthly_rows = av.parse_rows(monthly_csv, day, monthly=True)
        daily_rows = av.parse_rows(daily_csv, day)
        compared = av.compare_day(day, monthly_rows, daily_rows)
        sources.append({"day": day, "daily_zip_sha256": digest, "daily_checksum_sha256": av.sha256(checksum), "daily_csv_sha256": av.sha256(daily_csv), "monthly_zip_sha256": av.sha256(monthly_zip), "monthly_csv_sha256": av.sha256(monthly_csv)})
        summaries.append({"day": day, "status": "compared", "monthly_rows": len(monthly_rows), "daily_rows": len(daily_rows), **{key: compared[key] for key in ("counts", "field_conflicts", "window_counts")}})
        crosstabs.append({"day": day, **compared["placeholder_crosstab"]})
        for minute in compared["minute_diffs"]:
            fields = minute["field_differences"] or [{"field": "row_presence", "monthly": "present" if minute["classification"] == "extra_in_monthly" else None, "daily": "present" if minute["classification"] == "missing_in_monthly" else None}]
            for field in fields:
                diffs.append({"day": day, "open_time_ms": minute["open_time_ms"], "open_time_utc": minute["open_time_utc"], "classification": minute["classification"], "window": "inside_window" if minute["inside_window"] else "outside_window", "monthly_placeholder": minute["monthly_placeholder"], "daily_placeholder": minute["daily_placeholder"], **{key: field.get(key) for key in ("field", "monthly", "daily", "difference", "difference_bp", "ratio")}})
        for interval in compared["intervals"]:
            intervals.append({"day": day, **interval, "conflict_fields": "|".join(interval["conflict_fields"])})
        flags.extend({"day": day, **row} for row in compared.get("minute_flags", []))
        if day == "2020-12-21":
            ts = 1608556740000
            def exact_line(payload: bytes) -> str | None:
                for line in payload.decode("utf-8").splitlines():
                    row = next(csv.reader([line]))
                    if row and int(row[0]) == ts:
                        return line
                return None
            drow = exact_line(daily_csv)
            mrow = exact_line(monthly_csv)
            issue.update(status="checked", actual_zip_sha256=digest, zip_matches=digest == ISSUE_SHA, daily_row_matches=drow == ISSUE_DAILY, monthly_row_matches=mrow == ISSUE_MONTHLY, actual_daily_row=drow, actual_monthly_row=mrow, expected_daily_row=ISSUE_DAILY, expected_monthly_row=ISSUE_MONTHLY)
    result = {"source-sha256.json": json_bytes(sources), "comparison-summary.json": json_bytes(summaries), "placeholder-crosstab.json": json_bytes(crosstabs), "issue-475-check.json": json_bytes(issue), "verify-dir-inventory.json": json_bytes(inventory())}
    result["minute-diff.csv"] = csv_bytes(diffs, ["day", "open_time_ms", "open_time_utc", "classification", "window", "monthly_placeholder", "daily_placeholder", "field", "monthly", "daily", "difference", "difference_bp", "ratio"])
    result["intervals.csv"] = csv_bytes(intervals, ["day", "classification", "conflict_fields", "monthly_placeholder", "daily_placeholder", "start_utc", "end_utc", "length"])
    result["minute-flags.csv"] = csv_bytes(flags, ["day", "open_time_ms", "open_time_utc", "monthly_present", "daily_present", "monthly_placeholder", "daily_placeholder", "monthly_close_time_anomaly", "daily_close_time_anomaly"])
    return result


def block_network(*args, **kwargs):
    raise AssertionError("Offline reproduction may not access the network")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("acquire", "replay"))
    args = parser.parse_args()
    started = time.perf_counter()
    runtime = {"mode": args.mode, "started_at_utc": datetime.now(timezone.utc).isoformat(), "python": sys.version, "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(), "research_computations": 0}
    manifest = preflight()
    if args.mode == "acquire":
        if subprocess.check_output(["git", "status", "--porcelain"], text=True).strip():
            raise av.ArchiveVerificationError("Acquisition requires a clean C1 commit")
        if EVIDENCE.exists():
            raise av.ArchiveVerificationError("Evidence directory already exists")
        av.require_empty_verify_dir()
        before = isolation_snapshot(manifest)
        write_new("isolation-before.json", json_bytes(before))
        try:
            downloads = av.run_downloads()
            if not (EVIDENCE / "download-summary.json").exists():
                write_new("download-summary.json", json_bytes(downloads))
            artifacts = comparison_artifacts(manifest, downloads)
            for name, payload in artifacts.items():
                write_new(name, payload)
        finally:
            after = isolation_snapshot(manifest)
            write_new("isolation-after.json", json_bytes(after))
            runtime.update(elapsed_seconds=time.perf_counter() - started, finished_at_utc=datetime.now(timezone.utc).isoformat(), isolation_unchanged=before == after)
            write_new("runtime.json", json_bytes({"acquire": runtime}))
        if before != after:
            raise av.ArchiveVerificationError("Isolation snapshot changed")
    else:
        socket.create_connection = block_network
        socket.socket.connect = block_network
        urllib.request.urlopen = block_network
        urllib.request.OpenerDirector.open = block_network
        downloads = json.loads((EVIDENCE / "download-summary.json").read_bytes())
        original = {p.name: av.sha256(p.read_bytes()) for p in EVIDENCE.iterdir() if p.is_file() and p.name != "runtime.json"}
        artifacts = comparison_artifacts(manifest, downloads)
        compared = {}
        for name, payload in artifacts.items():
            existing = (EVIDENCE / name).read_bytes()
            compared[name] = {"original_sha256": av.sha256(existing), "reproduced_sha256": av.sha256(payload), "byte_identical": payload == existing}
        if not all(row["byte_identical"] for row in compared.values()):
            raise av.ArchiveVerificationError("Offline comparison differs")
        if any(av.sha256((EVIDENCE / name).read_bytes()) != digest for name, digest in original.items()):
            raise av.ArchiveVerificationError("Evidence changed during replay")
        if isolation_snapshot(manifest) != json.loads((EVIDENCE / "isolation-before.json").read_bytes()):
            raise av.ArchiveVerificationError("Isolation changed during replay")
        write_new("determinism-check.json", json_bytes({"passed": True, "network_requests": 0, "comparison_artifacts": compared, "preserved_artifact_sha256": original, "isolation_unchanged": True}))
        runtime.update(elapsed_seconds=time.perf_counter() - started, finished_at_utc=datetime.now(timezone.utc).isoformat(), network_requests=0)
        runtimes = json.loads((EVIDENCE / "runtime.json").read_bytes())
        runtimes["replay"] = runtime
        av.safe_verify_path(EVIDENCE / "runtime.json", root=EVIDENCE).write_bytes(json_bytes(runtimes))
    print(json.dumps(runtime, indent=2), flush=True)


if __name__ == "__main__":
    main()
