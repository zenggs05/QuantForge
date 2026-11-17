"""Read-only D-043 precondition check (Research Lead, D-049).

Run from the Codex worktree root:
    .venv\\Scripts\\python.exe -X utf8 -B tools\\d043_precheck.py
Prints ASCII only. Exit 0 with PRECHECK_OK, or exit 1 with PRECHECK_FAIL.
It never writes, deletes, moves or downloads anything.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("QF_PRECHECK_ROOT") or Path(__file__).resolve().parents[1])
VERIFICATION = Path(r"D:\zgs_project\QuantForge-verification")
D041_VERIFY = VERIFICATION / "binance-spot-daily-1m-full-d041"
D039_DIR = VERIFICATION / "binance-spot-daily-1m-20261110"
RETRY_DIR = VERIFICATION / "binance-spot-daily-1m-retry-d043"
RAW = ROOT / "data/raw/binance/spot/BTCUSDT/1m/historical"
PROCESSED = ROOT / "data/processed/binance/spot/BTCUSDT/1m/historical"
D041_EVIDENCE = ROOT / "docs/phase3a-full-verification-evidence"
D039_EVIDENCE = ROOT / "docs/phase3a-daily-verification-evidence"

FROZEN = {
    "docs/phase3a-analysis-plan.md": "fea667f7689d2c45ab0697b0dd2e8877cf0894ae27e4a5f0277efae48e50cb3c",
    "configs/history-extension.toml": "e9cbd19d884d046199f468f29e912cd6a9fbec1852e90ffc125b86d8df0bd9af",
    "docs/confirmatory-protocol-v1-history-addendum.md": "a4a99cfbfb9e33105d98ad44b439a57020a050b64b46b5a8e4a0bbc4b204fc07",
    "docs/confirmatory-protocol-v1.md": "38ef1aae687b6cd1a9b8b59709b84047250e2a9f90396cb9089244133b38f1b9",
    "docs/phase3a-plan-manifest.json": "ceb56c5683ff682240563c87991c96298b3f6c45baa93eee03134efc7d34c54a",
    "docs/phase3a-download-manifest-draft.json": "1a865966ff55094a5fa54fc245ed7f0e7543360fbd8a0a4da8459dda306ab0f6",
    "data/processed/binance/spot/BTCUSDT/1m/historical/historical-manifest.json": "71920d90d95a3d6600db3b57540865949b8784ce8753b0dccfbea93d7bb1fa02",
    "src/quantforge/features/__init__.py": "857819a0c3c718cf2b1100ea72995db989ed149f65d1ad3d5e4cd3eda1fcbec1",
    "src/quantforge/labels/__init__.py": "b968218639387cb82ab1beccef5c5bf3ed3674e5975c3d2b25cb4d318037746e",
    "src/quantforge/datasets/research_splits.py": "22c862f6e8e232dedfe17c3a9832f5b7d78737de1593d6b904e2c29c5949919c",
    "src/quantforge/datasets/splits.py": "3459e5be5185eee2638709c3db4250e150d8e1deff140d1459882cb5257d93cd",
    "src/quantforge/datasets/window_splits.py": "24de7804527d3dc805f46ee969d17267927e345b94af434303cd19793675fc22",
    "src/quantforge/models/baselines.py": "a9269ca7f6147f653f608051746304596e898f49ec30f9e1714567e25a1ded83",
    "src/quantforge/temporal.py": "6c262bbd1c77e6696c7d310eb31b321f3f27ffeea02fbd64a70f3dcee9bd648c",
}
D041_EVIDENCE_SHA = {
    "network-ledger.jsonl": "93c0f0bb6faf942011710aab029bf8bec5eb27822987fbe0c41efa7e0af47177",
    "download-summary.json": "525f1420aa789baa9c24fa20bce317d36147d45663013262128b9f76ff38b7fb",
    "verify-dir-inventory.json": "2cd0e6d55743f53d50a52ae7bd5b21c282398ad483e6901d6fdd0ac325c9c76d",
}
D041_DERIVED = ("minute-flags.csv.gz", 15_109_550, "71283ab3b619bb370f22dae62db5111ff5f5accb3d5e226b6b7e068372ebf984")
D039_SUMMARY_SHA = "f82ec90ec19b91d6ec8fa31450a618a5d27f26863c29e39a0f1963f45ce95d09"
OLD_CHECKSUMS = {
    "2020-02-13": ("000088.bin", "0ceba65b3adbcb5d1811f20609f2656b3467447ef11b2c055bc2801f995929a1"),
    "2020-12-09": ("000713.bin", "a902b0410f04b913b7ca9d5f06f9da9f9d53bb7dbd160736eca33918db8e22ec"),
    "2024-03-17": ("003133.bin", "051bffaad17856717344874804fc48cf6414ccd5aeba71bf7f6c75ab2135504f"),
}
REQUIRED_ANCESTORS = ("fa064908263915e5b70053353d22fc16e97f9060", "7c3c846bbbd1607edadd5ce0c3666050a232a641",
                      "7f09150f3c326b5bf26b16ecb462f31478f96e61")
DECISIONS = ("## D-043", "## D-044", "## D-046", "## D-048", "## D-049")
NEIGHBOUR_DAYS = ("2020-02-12", "2020-02-25", "2020-12-08", "2020-12-10", "2024-03-16", "2024-03-18")
NEW_PATHS = ("src/quantforge/archive_verification_retry.py", "scripts/verify_daily_archives_retry.py",
             "tests/test_archive_verification_retry.py", "docs/phase3a-retry-verification-record.md",
             "docs/phase3a-retry-verification-evidence")


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


def files_under(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def main() -> None:
    summary = {}
    # A: git state
    code, out = git("status", "--porcelain")
    if code or out:
        fail("A1", "working tree is not clean")
    code, branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch != "phase3a-protocol-and-impl":
        fail("A2", f"unexpected branch {branch}")
    head, main_head = git("rev-parse", "HEAD")[1], git("rev-parse", "main")[1]
    if head != main_head:
        fail("A3", f"HEAD {head} differs from main {main_head}; run git merge --ff-only main first")
    for commit in REQUIRED_ANCESTORS:
        if git("merge-base", "--is-ancestor", commit, "HEAD")[0] != 0:
            fail("A4", f"required commit missing from history: {commit}")
    summary["head"] = head
    # B: decision records and standing rules
    log = (ROOT / "research/DECISION_LOG.md").read_text(encoding="utf-8")
    for heading in DECISIONS:
        if heading not in log:
            fail("B1", f"DECISION_LOG lacks {heading}")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for phrase in ("D-043 14-day retry is not Gate 2", "D-043 clarification of the permanent rule"):
        if phrase not in agents:
            fail("B2", f"AGENTS.md lacks: {phrase}")
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    for line in ("docs/phase3a-retry-verification-record.md text eol=lf", "docs/phase3a-retry-verification-evidence/** -text"):
        if line not in attributes:
            fail("B3", f".gitattributes lacks: {line}")
    # C: frozen files and Gate 1 data
    for rel, expected in FROZEN.items():
        if sha(ROOT / rel) != expected:
            fail("C1", f"frozen file hash differs: {rel}")
    manifest = json.loads((PROCESSED / "historical-manifest.json").read_bytes())
    months = manifest["months"]
    if len(months) != 68:
        fail("C2", f"manifest has {len(months)} months")
    for entry in months:
        month = entry["month"]
        for suffix in (".zip", ".zip.CHECKSUM", ".zip.source.json"):
            if not (RAW / f"{month}{suffix}").is_file():
                fail("C2", f"missing Gate 1 raw file {month}{suffix}")
        if sha(RAW / f"{month}.zip") != entry["zip_sha256"]:
            fail("C2", f"Gate 1 ZIP hash differs: {month}")
        if sha(PROCESSED / f"{month}.parquet") != entry["parquet_sha256"]:
            fail("C2", f"Gate 1 parquet hash differs: {month}")
    data_files = len(files_under(ROOT / "data"))
    if data_files != 275:
        fail("C3", f"data/ holds {data_files} files, expected 275")
    summary["gate1_months"] = len(months)
    # D: D-041 code, evidence and external directory
    code, _ = git("diff", "--quiet", "main", "--", "src/quantforge/archive_verification_full.py",
                  "scripts/verify_daily_archives_full.py", "tests/test_archive_verification_full.py",
                  "docs/phase3a-full-verification-evidence", "docs/phase3a-full-verification-record.md")
    if code != 0:
        fail("D1", "D-041 code or evidence differs from main")
    for name, expected in D041_EVIDENCE_SHA.items():
        if sha(D041_EVIDENCE / name) != expected:
            fail("D2", f"D-041 evidence hash differs: {name}")
    inventory = {item["path"].replace("\\", "/"): (item["bytes"], item["sha256"])
                 for item in json.loads((D041_EVIDENCE / "verify-dir-inventory.json").read_bytes())}
    actual, derived, partial = {}, [], []
    for path in files_under(D041_VERIFY):
        rel = path.relative_to(D041_VERIFY).as_posix()
        if rel.startswith("derived/"):
            derived.append(path)
        elif rel.startswith(".partial/"):
            partial.append(rel)
        else:
            actual[rel] = (path.stat().st_size, sha(path))
    if partial:
        fail("D3", f"D-041 .partial is not empty: {partial[:3]}")
    if actual != inventory:
        fail("D3", f"D-041 external files differ from inventory ({len(actual)} vs {len(inventory)})")
    name, size, digest = D041_DERIVED
    if [p.name for p in derived] != [name] or derived[0].stat().st_size != size or sha(derived[0]) != digest:
        fail("D3", "D-041 derived directory differs")
    summary["d041_external_files"] = len(actual) + len(derived)
    # E: D-039 directory and evidence
    d039_inventory = {item["name"].replace("\\", "/"): (item["bytes"], item["sha256"])
                      for item in json.loads((D039_EVIDENCE / "verify-dir-inventory.json").read_bytes())}
    d039_actual = {p.relative_to(D039_DIR).as_posix(): (p.stat().st_size, sha(p)) for p in files_under(D039_DIR)}
    if set(d039_actual) != set(d039_inventory) and {k.split("/")[-1] for k in d039_actual} != {k.split("/")[-1] for k in d039_inventory}:
        fail("E1", "D-039 directory file names differ from inventory")
    by_name = {k.split("/")[-1]: v for k, v in d039_inventory.items()}
    for rel, value in d039_actual.items():
        if by_name.get(rel.split("/")[-1]) != value:
            fail("E1", f"D-039 file differs from inventory: {rel}")
    if len(d039_actual) != 23:
        fail("E1", f"D-039 directory holds {len(d039_actual)} files, expected 23")
    if sha(D039_EVIDENCE / "download-summary.json") != D039_SUMMARY_SHA:
        fail("E2", "D-039 download-summary hash differs")
    # F: the three previously accepted CHECKSUM files
    for day, (file_name, expected) in OLD_CHECKSUMS.items():
        path = D041_VERIFY / "failed" / file_name
        if sha(path) != expected:
            fail("F1", f"old CHECKSUM hash differs: {day}")
        text = path.read_bytes().decode("ascii").split()
        if len(text) != 2 or text[1].lstrip("*") != f"BTCUSDT-1m-{day}.zip":
            fail("F1", f"old CHECKSUM names the wrong archive: {day}")
    # G: D-043 outputs must not exist yet
    for rel in NEW_PATHS:
        if (ROOT / rel).exists():
            fail("G1", f"D-043 path already exists: {rel}")
    if RETRY_DIR.exists():
        fail("G1", "retry directory already exists")
    logs = sorted((ROOT / ".tmp").glob("d043-pytest-run-*.log")) if (ROOT / ".tmp").exists() else []
    if logs:
        fail("G1", f"D-043 pytest logs already exist: {[p.name for p in logs]}")
    # H: neighbour days carry no D-041 conflict or anomaly rows
    for name in ("conflict-minutes.csv", "timestamp-anomalies.csv"):
        rows = (D041_EVIDENCE / name).read_text(encoding="utf-8").splitlines()[1:]
        hits = [r for r in rows if r.split(",", 1)[0] in NEIGHBOUR_DAYS]
        if hits:
            fail("H1", f"{name} has rows on neighbour days")
    # I: disk
    free = shutil.disk_usage(VERIFICATION).free
    if free < 8 * 1024**3:
        fail("I1", f"free disk below 8 GiB: {free}")
    summary["disk_free_bytes"] = free
    print("PRECHECK_OK " + json.dumps(summary, ensure_ascii=True, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
