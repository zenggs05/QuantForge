"""Run the D-041 yearly acquisition or network-blocked comparison/replay."""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import socket
import sys
import time
import urllib.request

from quantforge import archive_verification_full as full


def block_network(*args, **kwargs):
    raise full.StopTask("offline operation attempted network access")


def disable_network() -> None:
    socket.create_connection = block_network
    socket.socket.connect = block_network
    urllib.request.urlopen = block_network
    urllib.request.OpenerDirector.open = block_network


def prepare_artifacts(artifacts: dict[str, bytes]) -> dict[str, tuple[Path, bytes]]:
    """Apply deterministic size rules without writing files."""
    prepared = {}
    for name, payload in sorted(artifacts.items()):
        if len(payload) > 20 * 1024**2 and not name.endswith(".gz"):
            name += ".gz"
            payload = gzip.compress(payload, compresslevel=9, mtime=0)
        target = full.VERIFY_DIR_FULL / name if name.startswith("derived/") else full.EVIDENCE_DIR / name
        prepared[name] = (target, payload)
    existing = sum(path.stat().st_size for path in full.EVIDENCE_DIR.rglob("*") if path.is_file())
    projected = existing + sum(len(p) for target, p in prepared.values() if target.parent == full.EVIDENCE_DIR)
    # Move largest result files first if the complete evidence directory would
    # exceed its fixed budget. Raw files and historical evidence remain intact.
    for name in sorted(prepared, key=lambda key: (-len(prepared[key][1]), key)):
        if projected <= 100 * 1024**2:
            break
        target, payload = prepared[name]
        if target.parent == full.EVIDENCE_DIR and name != "d039-reconciliation.json":
            prepared[name] = (full.VERIFY_DIR_FULL / "derived" / name, payload)
            projected -= len(payload)
    return prepared


def offline_preflight() -> tuple[dict, dict]:
    disable_network()
    manifest = full.preflight(initial=False)
    state = full.rebuild_ledger()
    if state["batch_complete"] != [f"B{year}" for year in range(2020, 2026)]:
        raise full.StopTask("all six acquisition batches must complete first")
    if full.git_preflight(initial=False) != state["run_commit"]:
        raise full.StopTask("comparison must use the unchanged C1 commit")
    if list(full.EVIDENCE_DIR.glob("stop-*.json")):
        raise full.StopTask("a stopped task requires a new numbered decision")
    full.verify_download_inventory(acquisition_only=False)
    before = json.loads((full.EVIDENCE_DIR / "isolation-before.json").read_bytes())
    full.assert_isolation(before, hashes=True)
    summary = full.download_summary()
    if summary != json.loads((full.EVIDENCE_DIR / "download-summary.json").read_bytes()):
        raise full.StopTask("download summary differs from the append-only ledger")
    return manifest, summary


def compare() -> None:
    started = time.monotonic()
    start_utc = full.utc_now()
    manifest, summary = offline_preflight()
    artifacts = full.run_comparison(manifest, summary, contamination_fn=full.contamination_artifacts)
    prepared = prepare_artifacts(artifacts)
    index = {}
    for name, (target, payload) in prepared.items():
        root = full.VERIFY_DIR_FULL if target.is_relative_to(full.VERIFY_DIR_FULL) else full.EVIDENCE_DIR
        if name == "d039-reconciliation.json":
            if target.read_bytes() != payload:
                raise full.StopTask("D-039 reconciliation differs from the pre-acquisition check")
        else:
            full.write_bytes(target, payload, root=root, exclusive=True)
        index[name] = {"root": "external" if root == full.VERIFY_DIR_FULL else "evidence",
                       "path": target.relative_to(root).as_posix(), "bytes": len(payload),
                       "sha256": full.sha256(payload)}
    full.write_json("artifact-index.json", index, exclusive=True)
    full.write_json("verify-dir-inventory.json", full.inventory(), exclusive=True)
    before = json.loads((full.EVIDENCE_DIR / "isolation-before.json").read_bytes())
    after = full.isolation_snapshot()
    full.write_json("isolation-after.json", after, exclusive=True)
    if before != after:
        raise full.StopTask("protected files changed during comparison")
    full.write_json("runtime-compare.json", {"started_at_utc": start_utc, "finished_at_utc": full.utc_now(),
                    "elapsed_seconds": time.monotonic() - started, "peak_process_bytes": full.check_memory(),
                    "network_requests": 0, "research_computations": 0}, exclusive=True)


def replay_check() -> None:
    started = time.monotonic()
    start_utc = full.utc_now()
    manifest, summary = offline_preflight()
    artifacts = full.run_comparison(manifest, summary, contamination_fn=full.contamination_artifacts)
    index = json.loads((full.EVIDENCE_DIR / "artifact-index.json").read_bytes())
    checks = {}
    for original_name, original_payload in sorted(artifacts.items()):
        name, payload = original_name, original_payload
        if len(payload) > 20 * 1024**2 and not name.endswith(".gz"):
            name += ".gz"
            payload = gzip.compress(payload, compresslevel=9, mtime=0)
        item = index[name]
        root = full.VERIFY_DIR_FULL if item["root"] == "external" else full.EVIDENCE_DIR
        stored = full.safe_path(root / item["path"], root).read_bytes()
        digest = full.sha256(payload)
        matches = stored == payload and digest == item["sha256"]
        checks[name] = {"sha256": digest, "stored_sha256": full.sha256(stored), "byte_identical": matches}
    if len(checks) != len(index) or not all(item["byte_identical"] for item in checks.values()):
        raise full.StopTask("deterministic replay differs")
    full.assert_isolation(json.loads((full.EVIDENCE_DIR / "isolation-before.json").read_bytes()), hashes=True)
    full.write_json("determinism-check.json", {"passed": True, "artifacts": checks, "network_requests": 0,
                                              "isolation_unchanged": True}, exclusive=True)
    full.write_json("runtime-replay.json", {"started_at_utc": start_utc, "finished_at_utc": full.utc_now(),
                    "elapsed_seconds": time.monotonic() - started, "peak_process_bytes": full.check_memory(),
                    "network_requests": 0, "research_computations": 0}, exclusive=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("acquire", "compare", "replay-check"))
    parser.add_argument("--batch", type=int, choices=range(2020, 2026))
    args = parser.parse_args()
    if (args.mode == "acquire") != (args.batch is not None):
        parser.error("--batch is required only for acquire")
    try:
        if args.mode == "acquire":
            summary = full.acquire_batch(args.batch)
            print(json.dumps({"batch": args.batch, **summary["counters"]}), flush=True)
        elif args.mode == "compare":
            compare()
        else:
            replay_check()
    except BaseException as exc:
        # Never restart a failed action. Only write distinct stop evidence;
        # if recording itself is refused the exception propagates unchanged.
        path = full.write_stop(exc, args.mode if args.batch is None else f"B{args.batch}")
        print(json.dumps({"status": "stopped", "stop_file": str(path), "reason": str(exc)}), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
