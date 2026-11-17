"""Specific re-signed structural corruptions; no claim against total rewrites."""
import hashlib
import json

import pytest

from quantforge import data
from quantforge.online.audit_log import canonical_json
from quantforge.online.checkpoint_v2 import load_v2
from quantforge.online.feedback_queue import _digest as queue_digest
from quantforge.online.replay import ReplayEngine
from tests.test_online_checkpoint import rehash
from tests.test_online_replay import synthetic_replay
from tests.test_phase2f_checkpoint_v2_roundtrip import pair_paths


@pytest.mark.parametrize("kind,message", [
    ("pending", "pending feature cache mismatch"),
    ("extra_update", "Consumed feedback IDs"),
    ("prediction_version", "original prediction identity/version"),
    ("carry_id", "carry-in ID is orphaned"),
    ("cache", "feature cache is invalid"),
    ("event_time", "event identity or order mismatch"),
    ("prediction_schema", "prediction record schema"),
    ("update_schema", "update record schema"),
    ("event_schema", "event record schema"),
    ("missing_feedback", "prediction feedback partition"),
    ("metadata", "feedback metadata differs"),
])
def test_resigned_v1_structural_changes_are_rejected(minute_bars, kind, message):
    engine, fx, ly, events, *_ = synthetic_replay(minute_bars)
    for event in events[:40]:
        engine.step(event, fx, ly)
    state = engine.state_dict()
    p, q = state["payload"], state["payload"]["queue"]["payload"]
    if kind in {"pending", "missing_feedback"}:
        removed = q["pending"].pop()
        q["registered_refs"].pop(removed["feature_reference"])
        q["registered_count"] -= 1
        if kind == "missing_feedback":
            p["feature_cache"].pop(removed["sample_id"])
    elif kind == "extra_update":
        p["updates"].append(dict(p["updates"][0]))
    elif kind == "prediction_version":
        p["predictions"][0]["model_version"] = "d"*64
    elif kind == "carry_id":
        p["carry_ids"].append("synthetic:orphan")
    elif kind == "cache":
        p["feature_cache"][next(iter(p["feature_cache"]))] = [0.0]
    elif kind == "event_time":
        p["events"][1]["decision_time"] = p["events"][0]["decision_time"]
    elif kind.endswith("_schema"):
        name = kind.split("_")[0]
        p[{"prediction": "predictions", "update": "updates", "event": "events"}[name]][0]["extra"] = True
    elif kind == "metadata":
        row = next(r for r in p["updates"] if not r["carry_in"])
        row["feature_reference"] = "2026-03-01T00:00:00+00:00"
        if row["feature_reference"] == p["predictions"][0]["feature_reference"]:
            row["feature_reference"] = "2026-03-01T00:01:00+00:00"
    p["queue"]["sha256"] = queue_digest(q)
    rehash(state)
    with pytest.raises(ValueError, match=message):
        ReplayEngine.from_state_dict(state, source_sha256="a"*64, config_sha256="b"*64, horizon_minutes=15)


def rewrite_json(path, value):
    path.write_bytes(canonical_json(value) + b"\n")


def file_record(path):
    return {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}


def resign_v2_bindings(core, log, *, change_count=False):
    """Update all outer file hashes, leaving the intended inner invariant broken."""
    audit_path, summary_path = core.parent / "checkpoint-audit.json", core.parent / "summary.json"
    body, audit = json.loads(core.read_bytes()), json.loads(audit_path.read_bytes())
    body["log"].update(file_record(log))
    if change_count:
        body["log"]["rows"] += 1
    body["sha256"] = hashlib.sha256(canonical_json({k: v for k, v in body.items() if k != "sha256"})).hexdigest()
    rewrite_json(core, body)
    rewrite_json(summary_path, {"schema_version": 2, "state_sha256": body["state_sha256"],
                               "history": body["history"], "log": body["log"]})
    audit.update(core=file_record(core), log=body["log"], summary=file_record(summary_path))
    rewrite_json(audit_path, audit)


@pytest.mark.parametrize("kind,message", [
    ("link", "hash link mismatch"), ("count", "audit log count/head mismatch"),
    ("sha", "core file SHA mismatch"), ("truncated", "Truncated audit-log record"),
])
def test_v2_outer_resigning_cannot_hide_inner_corruption(minute_bars, tmp_path, monkeypatch, kind, message):
    engine, fx, ly, events, *_ = synthetic_replay(minute_bars)
    for event in events[:3]:
        engine.step(event, fx, ly)
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    core, log = pair_paths(tmp_path)
    engine.save_checkpoint_v2(core, log)
    if kind == "link":
        rows = [json.loads(line) for line in log.read_bytes().splitlines()]
        rows[1]["prev_hash"] = "d"*64
        log.write_bytes(b"\n".join(canonical_json(row) for row in rows) + b"\n")
        resign_v2_bindings(core, log)
    elif kind == "count":
        resign_v2_bindings(core, log, change_count=True)
    elif kind == "truncated":
        log.write_bytes(log.read_bytes()[:-1])
        resign_v2_bindings(core, log)
    else:
        raw = core.read_bytes()
        # Same file size, changed bytes; audit SHA must fail before JSON parsing.
        core.write_bytes(b"!" + raw[1:])
    with pytest.raises(ValueError, match=message):
        load_v2(core, log, "a"*64, "b"*64, 15)


def test_file_sha_check_precedes_log_and_core_parsing(minute_bars, tmp_path, monkeypatch):
    engine, *_ = synthetic_replay(minute_bars)
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    core, log = pair_paths(tmp_path)
    engine.save_checkpoint_v2(core, log)
    core.write_bytes(b"!" + core.read_bytes()[1:])
    import quantforge.online.checkpoint_v2 as module
    original = module._parse
    count = 0
    def only_index(raw):
        nonlocal count
        count += 1
        assert count == 1, "State/log parsed before external file SHA verification"
        return original(raw)
    monkeypatch.setattr(module, "_parse", only_index)
    with pytest.raises(ValueError, match="core file SHA mismatch"):
        load_v2(core, log, "a"*64, "b"*64, 15)
