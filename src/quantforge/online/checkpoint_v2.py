"""Immutable state-core/log pairs; historical records stay outside the core."""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
from uuid import uuid4

from quantforge.online.audit_log import (
    GENESIS, canonical_json, _hex_hash, _iter_records, _log_path, _parse, _write_records,
)
from quantforge.online.hardening_config import load_hardening_config

CORE_SCHEMA = frozenset({"schema_version", "state_sha256", "payload", "history", "log", "sha256"})
PAYLOAD_SCHEMA = frozenset({"horizon_minutes", "initial_fit_time", "clock", "initial_n", "initial_model_version",
    "source_sha256", "config_sha256", "model", "queue", "feature_cache", "carry_ids", "peak_queue_size"})
HISTORY_SCHEMA = frozenset({"events", "predictions", "updates"})
FILE_SCHEMA = frozenset({"name", "sha256", "bytes"})
LOG_SCHEMA = FILE_SCHEMA | {"rows", "head"}
EVENT_LOG_SCHEMA = frozenset({"kind", "event", "completed", "prediction", "updates"})


def _sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _file_record(path: Path) -> dict:
    return {"name": path.name, "sha256": _sha(path), "bytes": path.stat().st_size}


def _publish(path: Path, value) -> None:
    encoded = canonical_json(value) + b"\n"
    partial = path.with_name(f".{path.name}.{uuid4().hex}.partial")
    with partial.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.link(partial, path)
    partial.unlink()


def _paths(core_path, log_path):
    from quantforge.online.replay import _checkpoint_path
    core = _checkpoint_path(core_path)
    log = _log_path(log_path)
    audit, summary = core.parent / "checkpoint-audit.json", core.parent / "summary.json"
    if core.parent != log.parent or core in (audit, summary):
        raise ValueError("v2 core/log must be siblings with distinct reserved audit names")
    for path in (audit, summary):
        _checkpoint_path(path)
    return core, log, audit, summary


def _history_rows(payload):
    update_index = 0
    for event in payload["events"]:
        completed = payload["completed_events"][event["sample_id"]]
        index = completed["prediction_index"]
        updates = []
        while (update_index < len(payload["updates"])
               and payload["updates"][update_index]["released_at"] == event["decision_time"]):
            updates.append(payload["updates"][update_index])
            update_index += 1
        yield {"kind": "event", "event": event, "completed": completed,
               "prediction": None if index is None else payload["predictions"][index], "updates": updates}
    if update_index != len(payload["updates"]):
        raise ValueError("v2 history has feedback outside completed events")


def save_v2(engine, core_path, log_path) -> dict:
    """Write a fresh pair; audit publication commits the completed snapshot."""
    config = load_hardening_config()["checkpoint"]
    core, log, audit, summary = _paths(core_path, log_path)
    for path in (core, log, audit, summary):
        if path.exists():
            raise FileExistsError(path)
    engine._ready()
    engine._validate_restored_state()
    state = engine.state_dict()
    original = state["payload"]
    payload = {key: value for key, value in original.items()
               if key not in {"predictions", "updates", "events", "completed_events", "queue"}}
    queue = original["queue"]["payload"]
    payload["queue"] = {key: value for key, value in queue.items() if key not in {"released_ids", "registered_refs"}}
    history = {key: len(original[key]) for key in HISTORY_SCHEMA}
    # Check the bound before publication with fixed-length hashes and final counters.
    candidate = {"schema_version": 2, "state_sha256": state["sha256"], "payload": payload,
                 "history": history, "log": {"name": log.name, "sha256": GENESIS, "bytes": 0,
                                              "rows": history["events"], "head": GENESIS}, "sha256": GENESIS}
    if len(canonical_json(candidate)) + 1 > config["state_core_max_bytes"]:
        raise ValueError("v2 state-core exceeds 32 MiB safety bound")
    core.parent.mkdir(parents=True, exist_ok=True)
    chain = _write_records(log, _history_rows(original), exclusive=True,
                           chunk_rows=config["audit_log_chunk_rows"])
    candidate["log"] = {**_file_record(log), **chain}
    candidate["sha256"] = hashlib.sha256(canonical_json({k: v for k, v in candidate.items() if k != "sha256"})).hexdigest()
    if len(canonical_json(candidate)) + 1 > config["state_core_max_bytes"]:
        raise ValueError("v2 state-core exceeds 32 MiB safety bound")
    _publish(core, candidate)
    _publish(summary, {"schema_version": 2, "state_sha256": state["sha256"],
                       "history": history, "log": candidate["log"]})
    audit_data = {"schema_version": 2, "core": _file_record(core), "log": candidate["log"],
                  "summary": _file_record(summary)}
    _publish(audit, audit_data)
    return audit_data


def _validate_file(path: Path, record: dict, name: str, *, log=False):
    schema = LOG_SCHEMA if log else FILE_SCHEMA
    if (not isinstance(record, dict) or set(record) != schema or record["name"] != path.name
            or not _hex_hash(record["sha256"]) or type(record["bytes"]) is not int or record["bytes"] < 0):
        raise ValueError(f"Invalid checkpoint audit {name} schema")
    if path.stat().st_size != record["bytes"]:
        raise ValueError(f"Checkpoint audit {name} file size mismatch")
    if _sha(path) != record["sha256"]:
        raise ValueError(f"Checkpoint audit {name} file SHA mismatch")
    if log and (type(record["rows"]) is not int or record["rows"] < 0 or not _hex_hash(record["head"])):
        raise ValueError("Invalid checkpoint audit log count/head schema")


def indexed_core(index_path):
    """Resolve only the basename of a bounded local v2 checkpoint index.

    Legacy per-run checkpoint-audit files are not v2 dispatch indices.
    """
    from quantforge.online.replay import _checkpoint_path
    path = _checkpoint_path(index_path)
    if path.stat().st_size > load_hardening_config()["checkpoint"]["state_core_max_bytes"]:
        raise ValueError("v2 checkpoint audit exceeds safety bound")
    index = _parse(path.read_bytes())
    if not isinstance(index, dict) or index.get("schema_version") != 2:
        return None
    record = index.get("core")
    name = record.get("name") if isinstance(record, dict) else None
    if not isinstance(name, str) or Path(name).name != name:
        raise ValueError("Invalid v2 core filename in checkpoint index")
    return _checkpoint_path(path.parent / name)


def load_v2(core_path, log_path, source_sha256, config_sha256, horizon_minutes):
    """Verify file bytes before parsing state or log, then reconstruct v1 state.

    Log history is loaded into memory for the existing replay API. This removes
    the single-file history limit without claiming bounded resident memory.
    """
    from quantforge.online.replay import ReplayEngine, _checkpoint_path, _digest
    from quantforge.online.feedback_queue import _digest as queue_digest
    config = load_hardening_config()["checkpoint"]
    core = _checkpoint_path(core_path)
    audit = _checkpoint_path(core.parent / "checkpoint-audit.json")
    try:
        if audit.stat().st_size > config["state_core_max_bytes"]:
            raise ValueError("v2 checkpoint audit exceeds safety bound")
        audit_data = _parse(audit.read_bytes())
        if (not isinstance(audit_data, dict) or set(audit_data) != {"schema_version", "core", "log", "summary"}
                or type(audit_data["schema_version"]) is not int or audit_data["schema_version"] != 2):
            raise ValueError("Invalid v2 checkpoint audit schema")
        if log_path is None:
            log_name = audit_data["log"].get("name") if isinstance(audit_data["log"], dict) else None
            if not isinstance(log_name, str) or Path(log_name).name != log_name:
                raise ValueError("Invalid v2 audit log filename")
            log_path = core.parent / log_name
        core, log, _, summary = _paths(core, log_path)
        if core.stat().st_size > config["state_core_max_bytes"]:
            raise ValueError("v2 state-core exceeds 32 MiB safety bound")
        _validate_file(core, audit_data["core"], "core")
        _validate_file(log, audit_data["log"], "log", log=True)
        _validate_file(summary, audit_data["summary"], "summary")
        body = _parse(core.read_bytes())
        if (not isinstance(body, dict) or set(body) != CORE_SCHEMA or type(body["schema_version"]) is not int
                or body["schema_version"] != 2 or not _hex_hash(body["state_sha256"])
                or body["sha256"] != hashlib.sha256(canonical_json({k: v for k, v in body.items() if k != "sha256"})).hexdigest()):
            raise ValueError("Invalid v2 core schema or checksum")
        if body["log"] != audit_data["log"]:
            raise ValueError("v2 core and audit log bindings disagree")
        if (not isinstance(body["history"], dict) or set(body["history"]) != HISTORY_SCHEMA
                or any(type(n) is not int or n < 0 for n in body["history"].values())):
            raise ValueError("Invalid v2 history counts")
        expected_summary = {"schema_version": 2, "state_sha256": body["state_sha256"],
                            "history": body["history"], "log": body["log"]}
        if _parse(summary.read_bytes()) != expected_summary:
            raise ValueError("v2 summary disagrees with core")
        payload = copy.deepcopy(body["payload"])
        if not isinstance(payload, dict) or set(payload) != PAYLOAD_SCHEMA:
            raise ValueError("Invalid v2 core payload")
        payload.update(events=[], updates=[], predictions=[], completed_events={})
        count, head = 0, GENESIS
        for envelope in _iter_records(log):
            row = envelope["payload"]
            if (set(row) != EVENT_LOG_SCHEMA or row["kind"] != "event" or not isinstance(row["event"], dict)
                    or not isinstance(row["updates"], list)):
                raise ValueError("Invalid v2 event-log schema")
            sid = row["event"].get("sample_id")
            if not isinstance(sid, str) or sid in payload["completed_events"]:
                raise ValueError("Duplicate or invalid v2 logged event")
            payload["events"].append(row["event"])
            payload["completed_events"][sid] = row["completed"]
            payload["updates"].extend(row["updates"])
            if row["prediction"] is not None:
                payload["predictions"].append(row["prediction"])
            count += 1
            head = envelope["this_hash"]
        if count != body["log"]["rows"] or head != body["log"]["head"]:
            raise ValueError("v2 audit log count/head mismatch")
        if any(len(payload[name]) != size for name, size in body["history"].items()):
            raise ValueError("v2 audit history count mismatch")
        queue = payload["queue"]
        queue_keys = {"horizon_minutes", "clock", "pending", "registered_count", "rejected_count"}
        if not isinstance(queue, dict) or set(queue) != queue_keys or not isinstance(queue["pending"], list):
            raise ValueError("Invalid v2 pending queue schema")
        released, refs = [], {}
        for row in [*payload["updates"], *queue["pending"]]:
            if not isinstance(row, dict) or not isinstance(row.get("feature_reference"), str) or not isinstance(row.get("sample_id"), str):
                raise ValueError("Invalid v2 registered feedback identity")
            if row["feature_reference"] in refs:
                raise ValueError("Duplicate v2 feedback reference")
            refs[row["feature_reference"]] = row["sample_id"]
        for row in payload["updates"]:
            released.append(row["sample_id"])
        queue.update(released_ids=sorted(released), registered_refs=dict(sorted(refs.items())))
        payload["queue"] = {"schema_version": 1, "payload": queue, "sha256": queue_digest(queue)}
        state = {"schema_version": 1, "payload": payload}
        state["sha256"] = _digest(state)
        if state["sha256"] != body["state_sha256"]:
            raise ValueError("Reconstructed v1 state checksum mismatch")
        return ReplayEngine.from_state_dict(state, source_sha256=source_sha256,
                                           config_sha256=config_sha256, horizon_minutes=horizon_minutes)
    except FileNotFoundError as exc:
        raise ValueError("Incomplete v2 checkpoint pair") from exc
