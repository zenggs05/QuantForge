"""Read-only Holdout-A precondition check (Research Lead, D-059).

Run from the Codex worktree root after `git merge --ff-only main`:
    .venv\\Scripts\\python.exe -X utf8 -B tools\\holdout_a_precheck.py
Prints ASCII only. Exit 0 with PRECHECK_OK, or exit 1 with PRECHECK_FAIL.
It never writes, deletes, moves, downloads or parses market rows; it only hashes
the frozen inputs and the 10 development parquet files (read-only).
QF_PRECHECK_SKIP_GIT=1 (Research Lead self-test only) skips section A and can
never print PRECHECK_OK.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(os.environ.get("QF_PRECHECK_ROOT") or Path(__file__).resolve().parents[1])
CONTRACT = "configs/holdout-a-execution.toml"
CONTRACT_SHA = "02cea40915339da68ba40befcc893872f885444e46d100787ef91533c9e6c408"
PROTOCOL_SHA = "38ef1aae687b6cd1a9b8b59709b84047250e2a9f90396cb9089244133b38f1b9"
DEV_ROOT = Path(r"D:\zgs_project\QuantForge")
DEV_MANIFEST = DEV_ROOT / "data/processed/binance/spot/BTCUSDT/1m/development-manifest.json"
DEV_MANIFEST_SHA = "6fc42cd53ab30e1282bc9202046edd1fb43e5bc3fa8c44595f1639958c66acb8"
PROBES = "research/evidence/data-expansion-probes.json"
REQUIRED_ANCESTORS = ("4e240b8c020938806c309278fa7253b869d8fb78",)
DECISIONS = ("## D-058", "## D-059")
AGENTS_PHRASE = "Holdout-A confirmatory run (D-059)"
ATTRIBUTE_LINES = (
    "configs/holdout-a-execution.toml text eol=lf",
    "docs/holdout-a-record.md text eol=lf",
    "docs/holdout-a-report.md text eol=lf",
    "docs/holdout-a-evidence/** -text",
    "tools/holdout_a_precheck.py text eol=lf",
)
NEW_PATHS = (
    "src/quantforge/holdout_a.py", "scripts/run_holdout_a.py", "docs/holdout-a-evidence",
    "docs/holdout-a-record.md", "docs/holdout-a-report.md", "results/holdout-a",
    "data/raw/binance/spot/BTCUSDT/1m/holdout-a",
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


def holdout_named_files(base: Path) -> list[str]:
    if not base.exists():
        return []
    return [p.as_posix() for p in base.rglob("*") if p.is_file() and ("2026-07" in p.name or "2026-08" in p.name)]


def main() -> None:
    summary: dict = {}
    skip_git = os.environ.get("QF_PRECHECK_SKIP_GIT") == "1"
    # A: git state
    if not skip_git:
        check_git(summary)
    # B: interpreter, environment, decisions, standing rules
    if Path(sys.executable).resolve() != (ROOT / ".venv/Scripts/python.exe").resolve():
        fail("B0", "interpreter is not the worktree .venv python")
    if os.environ.get("QF_HOLDOUT_A_AUTH") or os.environ.get("QF_GATE2_REAL_DATA"):
        fail("B0", "real-data authorization variables must not be set during the precheck")
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
    # C: contract, protocol, frozen code
    if sha(ROOT / CONTRACT) != CONTRACT_SHA:
        fail("C1", "contract hash differs")
    contract = tomllib.loads((ROOT / CONTRACT).read_bytes().decode("utf-8"))
    if contract["contract"]["authorization"] != "D-059" or contract["contract"]["protocol_sha256"] != PROTOCOL_SHA:
        fail("C1", "contract header differs")
    for rel, expected in contract["frozen_sha256"].items():
        if sha(ROOT / rel) != expected:
            fail("C2", f"frozen file hash differs: {rel}")
    if contract["frozen_sha256"]["docs/confirmatory-protocol-v1.md"] != PROTOCOL_SHA:
        fail("C2", "protocol hash entry differs")
    # D: development data in the main worktree (read-only hashing)
    if sha(DEV_MANIFEST) != DEV_MANIFEST_SHA:
        fail("D1", "development manifest hash differs")
    manifest = json.loads(DEV_MANIFEST.read_bytes())
    sources = manifest["validation"]["month_sources"]
    months = [s["month"] for s in sources]
    if months != contract["development"]["months"] or manifest["expected_rows"] != 436320:
        fail("D1", "development months or row count differ")
    if sum(s["rows"] for s in sources) != 436320:
        fail("D1", "development row total differs")
    for source in sources:
        path = DEV_ROOT / contract["development"]["parquet_dir"] / f"BTCUSDT-1m-{source['month']}.parquet"
        if sha(path) != source["parquet_sha256"]:
            fail("D2", f"development parquet hash differs: {source['month']}")
    summary["development_months"] = len(sources)
    # E: recorded Phase 2A metadata equals the contract
    probes = json.loads((ROOT / PROBES).read_bytes())
    found = {}
    for entry in probes.get("months", probes.get("archives", [])) if isinstance(probes, dict) else []:
        if isinstance(entry, dict) and entry.get("month") in ("2026-07", "2026-08"):
            found[entry["month"]] = (entry["checksum"]["text"], int(entry["head"]["content_length"]))
    for item in contract["download"]["recorded"]:
        if found.get(item["month"]) != (item["checksum_text"], item["zip_content_length"]):
            fail("E1", f"recorded probe metadata differs from contract: {item['month']}")
    # F: no Holdout-A bytes or Gate outputs yet
    for rel in NEW_PATHS:
        if (ROOT / rel).exists():
            fail("F1", f"Holdout-A path already exists: {rel}")
    if list((ROOT / "tests").glob("test_holdout_a*")):
        fail("F1", "Holdout-A test files already exist")
    tmp = ROOT / ".tmp"
    if tmp.exists() and list(tmp.glob("ha-*")):
        fail("F1", "Holdout-A logs already exist in .tmp")
    for base in (ROOT / "data", DEV_ROOT / "data"):
        hits = holdout_named_files(base)
        if hits:
            fail("F2", f"Holdout-A month files already exist: {hits[:3]}")
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
