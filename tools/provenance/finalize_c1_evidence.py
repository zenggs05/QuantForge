"""Post-run audit only: no market reads, model fits or event replays."""
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
assert str(ROOT) == r"D:\zgs_project\QuantForge"
FORMAL = ROOT / "results/online-foundation/20261103T045308206581Z-5632721d"
REPRO = ROOT / "results/online-foundation/20261103T045523927874Z-0b7d1d55"
OUT = ROOT / "docs/phase2d-c1-evidence"
OUT.mkdir(exist_ok=True)

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def read(path):
    return json.loads(path.read_text(encoding="utf-8"))

def write(path, obj):
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False)+"\n", encoding="utf-8", newline="\n")

a, b = read(FORMAL / "summary.json"), read(REPRO / "summary.json")
assert a["status"] == b["status"] == "completed_online_foundation"
assert a["run_kind"] == "formal" and b["run_kind"] == "reproduction"
assert a["provenance"] == b["provenance"]
assert a["provenance"]["git_commit"] == "9d35923625b0760b9b00d5fae861ef567ffb41b3"
assert not a["provenance"]["git_dirty"]
assert a["deterministic_artifacts"] == b["deterministic_artifacts"]
assert len(a["deterministic_artifacts"]) == 16
artifact_checks = []
for name in a["artifacts_sha256"]:
    ah, bh = digest(FORMAL/name), digest(REPRO/name)
    assert ah == a["artifacts_sha256"][name] and bh == b["artifacts_sha256"][name]
    deterministic = name in a["deterministic_artifacts"]
    if deterministic:
        assert (FORMAL/name).read_bytes() == (REPRO/name).read_bytes()
    artifact_checks.append(dict(path=name, formal_sha256=ah, reproduction_sha256=bh,
                                deterministic=deterministic, byte_equal=ah == bh))

differences = []
def compare(x, y, path=""):
    assert type(x) is type(y), path
    if isinstance(x, dict):
        assert x.keys() == y.keys(), path
        for k in x:
            compare(x[k], y[k], path+"/"+k)
    elif isinstance(x, list):
        assert len(x) == len(y), path
        for i, (v, w) in enumerate(zip(x, y)):
            compare(v, w, path+f"/{i}")
    elif x != y:
        leaf = path.split("/")[-1]
        allowed = path in ["/run_id", "/run_kind", "/output_directory", "/elapsed_seconds", "/source/elapsed_seconds"]
        allowed |= path.startswith("/budget/") and (leaf.endswith("_seconds") or "/timings_seconds/" in path or "/stage_timings/" in path or path in ["/budget/memory/peak_process_bytes", "/budget/memory/current_process_bytes"])
        allowed |= path in ["/artifacts_sha256/"+n for n in ["batch-timings.csv", "checkpoint-audit.json", "budget.json", "report.md"]]
        assert allowed, (path, x, y)
        differences.append(dict(path=path, formal=x, reproduction=y))
compare(a, b)
assert a["prediction_count"] == a["update_count"] == 2880
assert a["batch_checkpoint_count"] == 10 and a["restored_continuation_exact"]
for k in ["holdout_accessed", "new_market_downloads", "alpha_evaluated", "trading_implemented"]:
    assert a[k] is False and b[k] is False
for path, sha in a["provenance"]["source_sha256"].items():
    assert digest(ROOT/path) == sha, path
manifest = read(ROOT/"docs/phase2d-c1-plan-manifest.json")
for kind in ["plan", "config"]:
    assert digest(ROOT/manifest[kind+"_path"]) == manifest[kind+"_sha256"] == a[kind+"_sha256"]
reference = ROOT/"results/research-baseline/20261103T010111153259Z-a2cb0858"
reference_checks = {
    "summary.json": "7aa9f334a9e5b5f1085edd2362676d86d0898fcdd376f92bb1886efbc3dc6f12",
    "predictions.parquet": "66f74e1367ed816455d8df2af5c8fdd89009c893aa5fe064df29fe646c86407a",
}
for name, sha in reference_checks.items():
    assert digest(reference/name) == sha

copied = []
for name in ["batch-equivalence.csv", "batch-timings.csv", "initial-equivalence.json", "budget.json", "checkpoint-audit.json"]:
    destination = OUT/name
    destination.write_bytes((FORMAL/name).read_bytes())
    copied.append(dict(path=destination.relative_to(ROOT).as_posix(), source=(FORMAL/name).relative_to(ROOT).as_posix(), sha256=digest(destination)))
synthetic = ROOT/"results/phase2d-c1-synthetic-equivalence.json"
(OUT/"synthetic-equivalence.json").write_bytes(synthetic.read_bytes())
copied.append(dict(path=(OUT/"synthetic-equivalence.json").relative_to(ROOT).as_posix(),source=synthetic.relative_to(ROOT).as_posix(),sha256=digest(synthetic)))
compact = {k:a[k] for k in ["status", "run_id", "run_kind", "output_directory", "provenance", "source", "config_sha256", "plan_sha256", "prediction_count", "update_count", "batch_checkpoint_count", "restored_continuation_exact", "holdout_accessed", "new_market_downloads", "alpha_evaluated", "trading_implemented", "limitations", "artifacts_sha256", "deterministic_artifacts", "elapsed_seconds"]}
write(OUT/"formal-summary.json", compact)
with (FORMAL/"batch-equivalence.csv").open(newline="",encoding="utf-8") as f:
    rows=list(csv.DictReader(f))
maxima={k:max(float(r[k]) for r in rows) for k in ["coefficient_max_abs_error", "coefficient_max_relative_error", "intercept_abs_error", "intercept_relative_error", "prediction_max_abs_error", "prediction_l2_error", "prediction_relative_l2_error", "condition_number"]}
audit = dict(status="verified_same_environment_reproduction", checked_on="2026-11-03", formal_run=FORMAL.relative_to(ROOT).as_posix(), reproduction_run=REPRO.relative_to(ROOT).as_posix(), implementation_commit=a["provenance"]["git_commit"], both_git_dirty_false=True, plan_sha256=a["plan_sha256"], config_sha256=a["config_sha256"], source_manifest_sha256=a["source"]["overall_manifest_sha256"], original_summary_sha256={"formal":digest(FORMAL/"summary.json"), "reproduction":digest(REPRO/"summary.json")}, deterministic_artifact_count=16, all_16_byte_equal=True, all_20_artifact_hashes_validated_per_run=True, identical_prediction_rows=2880, identical_update_rows=2880, identical_batch_checkpoints=10, restored_continuation_exact_both=True, identical_code_dependencies_source_and_operation_counts=True, unchanged_frozen_reference_sha256=reference_checks, artifact_checks=artifact_checks, allowed_summary_differences= differences, unexpected_summary_differences=[], real_batch_error_maxima=maxima, formal_operation_totals=a["budget"]["operation_totals"], reproduction_operation_totals=b["budget"]["operation_totals"], elapsed_seconds={"formal":a["elapsed_seconds"],"reproduction":b["elapsed_seconds"]}, peak_process_bytes={"formal":a["budget"]["memory"]["peak_process_bytes"],"reproduction":b["budget"]["memory"]["peak_process_bytes"]}, copied_aggregate_evidence=copied, original_scope_flags={k:a[k] for k in ["holdout_accessed","new_market_downloads","alpha_evaluated","trading_implemented"]}, tests={"original":345,"new":139,"total":484,"passed":484,"full_suite_seconds":77.22}, limitations=["Same host and dependency environment; byte equality is an observation, not a cross-platform guarantee.","Aggregate documents exclude per-minute forecasts, updates and checkpoint histories; these remain Git-ignored.","Timing, process memory and run identity differ; no unexpected numerical/event/count/source differences.","No fresh out-of-sample alpha, full March-June comparison, market latency or execution claim."])
write(ROOT/"docs/phase2d-c1-reproducibility.json", audit)
print(json.dumps({"status":audit["status"],"deterministic_artifacts":16,"allowed_difference_count":len(differences),"maxima":maxima},indent=2))
