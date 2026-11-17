"""Read-only Phase 3A Gate 2 precondition check (Research Lead, D-056).

Run from the Codex worktree root after `git merge --ff-only main`:
    .venv\\Scripts\\python.exe -X utf8 -B tools\\gate2_precheck.py
Prints ASCII only. Exit 0 with PRECHECK_OK, or exit 1 with PRECHECK_FAIL.
It never writes, deletes, moves, downloads or parses market rows.
QF_PRECHECK_SKIP_GIT=1 (Research Lead self-test only) skips section A and can
never print PRECHECK_OK.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(os.environ.get("QF_PRECHECK_ROOT") or Path(__file__).resolve().parents[1])
# QF_PRECHECK_DATA_ROOT is honoured only together with QF_PRECHECK_SKIP_GIT=1 (self-test).
DATA_ROOT = Path(os.environ.get("QF_PRECHECK_DATA_ROOT") or ROOT)
RAW = DATA_ROOT / "data/raw/binance/spot/BTCUSDT/1m/historical"
PROCESSED = DATA_ROOT / "data/processed/binance/spot/BTCUSDT/1m/historical"
CONTRACT = "configs/phase3a-gate2-execution.toml"
CONTRACT_SHA = "0db8183ee1cbc6af80f429920794209d3ffd4cbe5b37d5ed8cdae94275c99419"
STRUCTURAL = "docs/phase3a-gate2-structural-expectations.json"
STRUCTURAL_SHA = "90983610b7a7cd311b49c903d9c3ec8cc33b20e7e7286c5b67762024fef87bea"
CONFLICTS = "docs/phase3a-gate2-sensitivity-conflicts.csv"
CONFLICTS_SHA = "c2b9cfb5183abf57ea07d9bda67d2314b5612c8f63cdcc5e97b37f153e9fe660"
V2_CONFLICTS = "docs/phase3a-retry-verification-evidence/final-v2/conflict-minutes.csv"
REQUIRED_ANCESTORS = ("6768335d736529428aef539e8042c3076e3c5574",)
DECISIONS = ("## D-055", "## D-056")
AGENTS_PHRASE = "Phase 3A Gate 2 (D-056)"
ATTRIBUTE_LINES = (
    "configs/phase3a-gate2-execution.toml text eol=lf",
    "docs/phase3a-gate2-structural-expectations.json -text",
    "docs/phase3a-gate2-sensitivity-conflicts.csv text eol=lf",
    "docs/phase3a-gate2-record.md text eol=lf",
    "docs/phase3a-gate2-report.md text eol=lf",
    "docs/phase3a-gate2-reproducibility.json text eol=lf",
    "docs/phase3a-gate2-evidence/** -text",
    "tools/gate2_precheck.py text eol=lf",
)
NEW_PATHS = (
    "src/quantforge/phase3a_gate2.py", "src/quantforge/phase3a_gate2_oracle.py",
    "src/quantforge/phase3a_gate2_rehearsal.py", "scripts/run_phase3a_gate2.py",
    "scripts/phase3a_gate2_prefit_audit.py", "scripts/phase3a_gate2_rehearsal.py",
    "docs/phase3a-gate2-evidence", "docs/phase3a-gate2-record.md", "docs/phase3a-gate2-report.md",
    "docs/phase3a-gate2-reproducibility.json", "results/phase3a/gate2", "results/phase3a/gate2-rehearsal",
)


def fail(code: str, message: str) -> None:
    text = f"PRECHECK_FAIL {code}: {message}"
    print(text.encode("ascii", "backslashreplace").decode("ascii"), flush=True)
    sys.exit(1)


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str) -> tuple[int, str]:
    result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True)
    return result.returncode, result.stdout.decode("utf-8", "replace").strip()


def check_git(summary: dict) -> None:
    code, out = git("status", "--porcelain")
    if code or out:
        fail("A1", "working tree is not clean")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")[1]
    if branch != "phase3a-protocol-and-impl":
        fail("A2", f"unexpected branch {branch}")
    head, main_head = git("rev-parse", "HEAD")[1], git("rev-parse", "main")[1]
    if head != main_head:
        fail("A3", f"HEAD {head} differs from main {main_head}; run git merge --ff-only main first")
    for commit in REQUIRED_ANCESTORS:
        if git("merge-base", "--is-ancestor", commit, "HEAD")[0] != 0:
            fail("A4", f"required commit missing from history: {commit}")
    summary["head"] = head


def main() -> None:
    summary: dict = {}
    skip_git = os.environ.get("QF_PRECHECK_SKIP_GIT") == "1"
    # A: git state
    if os.environ.get("QF_PRECHECK_DATA_ROOT") and not skip_git:
        fail("A0", "QF_PRECHECK_DATA_ROOT is a self-test option only")
    if not skip_git:
        check_git(summary)
    # B: interpreter, decisions, standing rules
    expected_python = (ROOT / ".venv/Scripts/python.exe").resolve()
    if Path(sys.executable).resolve() != expected_python:
        fail("B0", f"interpreter is not {expected_python}")
    if os.environ.get("QF_GATE2_REAL_DATA"):
        fail("B0", "QF_GATE2_REAL_DATA must not be set during the precheck")
    log = (ROOT / "research/DECISION_LOG.md").read_text(encoding="utf-8")
    for heading in DECISIONS:
        if heading not in log:
            fail("B1", f"DECISION_LOG lacks {heading}")
    if AGENTS_PHRASE not in (ROOT / "AGENTS.md").read_text(encoding="utf-8"):
        fail("B2", f"AGENTS.md lacks: {AGENTS_PHRASE}")
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    for line in ATTRIBUTE_LINES:
        if line not in attributes:
            fail("B3", f".gitattributes lacks: {line}")
    # C: contract, structural table, conflict list, frozen files
    for rel, expected in ((CONTRACT, CONTRACT_SHA), (STRUCTURAL, STRUCTURAL_SHA), (CONFLICTS, CONFLICTS_SHA)):
        if sha(ROOT / rel) != expected:
            fail("C1", f"hash differs: {rel}")
    contract = tomllib.loads((ROOT / CONTRACT).read_bytes().decode("utf-8"))
    if contract["contract"]["authorization"] != "D-056" or contract["contract"]["version"] != "1.1":
        fail("C1", "contract header differs")
    if contract["contract"]["structural_expectations_sha256"] != STRUCTURAL_SHA:
        fail("C1", "contract structural hash differs")
    if contract["contract"]["sensitivity_conflicts_sha256"] != CONFLICTS_SHA:
        fail("C1", "contract conflict-list hash differs")
    for rel, expected in contract["frozen_sha256"].items():
        if sha(ROOT / rel) != expected:
            fail("C2", f"frozen file hash differs: {rel}")
    structural = json.loads((ROOT / STRUCTURAL).read_bytes())
    if len(structural) != 112 or len({(e["month"], e["horizon"]) for e in structural}) != 112:
        fail("C3", "structural table does not hold 112 unique (month, horizon) rows")
    listed = [row for row in csv.DictReader((ROOT / CONFLICTS).read_text(encoding="utf-8").splitlines())]
    source = [row for row in csv.DictReader((ROOT / V2_CONFLICTS).read_text(encoding="utf-8").splitlines())]
    if len(listed) != 157 or {r["open_time_us"] for r in listed} != {r["open_time_us"] for r in source}:
        fail("C4", "conflict list differs from the D-055 final-v2 conflict minutes")
    if {r["classification"] for r in listed} != {"value_conflict", "extra_in_monthly"}:
        fail("C4", "conflict list classes differ")
    # D: Gate 1 data
    manifest = json.loads((PROCESSED / "historical-manifest.json").read_bytes())
    months = manifest["months"]
    if len(months) != 68 or manifest["total_rows"] != 2978475 or sum(e["rows"] for e in months) != 2978475:
        fail("D1", f"manifest month count or row total differs ({len(months)})")
    for entry in months:
        month = entry["month"]
        if sha(RAW / f"{month}.zip") != entry["zip_sha256"]:
            fail("D1", f"Gate 1 ZIP hash differs: {month}")
        if sha(PROCESSED / f"{month}.parquet") != entry["parquet_sha256"]:
            fail("D1", f"Gate 1 parquet hash differs: {month}")
    if any(entry["month"] >= "2025-09" for entry in months):
        fail("D1", "manifest reaches 2025-09 or later")
    data_files = sum(1 for p in (DATA_ROOT / "data").rglob("*") if p.is_file())
    if data_files != 275:
        fail("D2", f"data/ holds {data_files} files, expected 275")
    summary["gate1_months"] = len(months)
    # E: synthetic baseline reference
    reference = json.loads((ROOT / "docs/phase3a-evidence/reproducibility.json").read_bytes())
    if len(reference.get("deterministic_sha256", {})) != 19:
        fail("E1", "synthetic baseline reference does not hold 19 hashes")
    # F: Gate 2 outputs must not exist yet
    for rel in NEW_PATHS:
        if (ROOT / rel).exists():
            fail("F1", f"Gate 2 path already exists: {rel}")
    if list((ROOT / "tests").glob("test_phase3a_gate2*")):
        fail("F1", "Gate 2 test files already exist")
    tmp = ROOT / ".tmp"
    if tmp.exists() and list(tmp.glob("g2-*.log")):
        fail("F1", "Gate 2 logs already exist in .tmp")
    if (ROOT / "results/phase3a").exists() and list((ROOT / "results/phase3a").glob("gate2*")):
        fail("F1", "Gate 2 result directories already exist")
    # G: disk
    free = shutil.disk_usage(ROOT).free
    if free < 8 * 1024**3:
        fail("G1", f"free disk below 8 GiB: {free}")
    summary["disk_free_bytes"] = free
    status = "PRECHECK_TEST_ONLY_GIT_SKIPPED " if skip_git else "PRECHECK_OK "
    print(status + json.dumps(summary, ensure_ascii=True, sort_keys=True), flush=True)
    sys.exit(1 if skip_git else 0)


if __name__ == "__main__":
    main()
