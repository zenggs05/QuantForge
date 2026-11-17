"""Synthetic checkpoint integrity, event continuation and path safety."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest

from quantforge.online.replay import ReplayEngine, _digest
from tests.test_online_replay import synthetic_replay


def rehash(state):
    state["sha256"] = _digest({k:state[k] for k in ("schema_version", "payload")})
    return state


@pytest.mark.parametrize("horizon", [5, 15])
def test_checkpoint_restart_duplicate_and_final_state_exact(minute_bars, horizon):
    original, fx, ly, events, *_ = synthetic_replay(minute_bars, horizon)
    for e in events[:40]: original.step(e, fx, ly)
    checkpoint = original.state_dict()
    restored = ReplayEngine.from_state_dict(json.loads(json.dumps(checkpoint)), source_sha256="a"*64,
                                           config_sha256="b"*64, horizon_minutes=horizon)
    before = restored.state_dict()
    assert restored.step(events[39], fx, ly) == original.predictions[-1]
    assert restored.state_dict() == before
    for e in events[40:90]:
        assert original.step(e, fx, ly) == restored.step(e, fx, ly)
    assert original.state_dict() == restored.state_dict()
    assert original.queue.released_ids == restored.queue.released_ids
    assert restored.runtime["duplicate_replay_attempts"] == 1


def test_initial_checkpoint_restores_carry_pending(minute_bars):
    engine, fx, ly, events, *_ = synthetic_replay(minute_bars)
    recovered = ReplayEngine.from_state_dict(engine.state_dict(), source_sha256="a"*64,
                                           config_sha256="b"*64, horizon_minutes=15)
    assert engine.state_dict() == recovered.state_dict()
    assert engine.step(events[0], fx, ly) == recovered.step(events[0], fx, ly)


@pytest.mark.parametrize("corruption", ["checksum", "schema", "config", "source", "horizon", "version", "cache",
                                       "audit", "n", "prediction", "event", "clock"])
def test_corrupt_incompatible_checkpoint_rejected(minute_bars, corruption):
    engine, fx, ly, events, *_ = synthetic_replay(minute_bars)
    for e in events[:10]: engine.step(e, fx, ly)
    state = copy.deepcopy(engine.state_dict())
    p = state["payload"]
    if corruption == "checksum": state["sha256"] = "0"*64
    elif corruption == "schema": state["schema_version"] = 999
    elif corruption == "config": p["config_sha256"] = "f"*64
    elif corruption == "source": p["source_sha256"] = "f"*64
    elif corruption == "horizon": p["horizon_minutes"] = 5
    elif corruption == "version": p["events"][-1]["model_version"] = "e"*64
    elif corruption == "cache": p["feature_cache"].pop(next(iter(p["feature_cache"])))
    elif corruption == "audit": p["updates"].pop()
    elif corruption == "n": p["initial_n"] += 1
    elif corruption == "prediction": p["predictions"][0]["model_version"] = "e"*64
    elif corruption == "event": p["events"][0]["sample_id"] = "missing"
    elif corruption == "clock": p["clock"] = "2026-07-01T00:00:00+00:00"
    if corruption not in ("checksum", "schema"): rehash(state)
    with pytest.raises(ValueError):
        ReplayEngine.from_state_dict(state, source_sha256="a"*64, config_sha256="b"*64, horizon_minutes=15)


def test_checkpoint_files_sha_exclusive_create_and_load(minute_bars, tmp_path, monkeypatch):
    from quantforge import data
    engine, fx, ly, events, *_ = synthetic_replay(minute_bars)
    for e in events[:10]: engine.step(e, fx, ly)
    # Tests use an actual allowed output subtree while keeping unique test paths.
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    relative = Path("results/online-foundation/synthetic-checkpoint-tests/h15.json")
    audit = engine.save_checkpoint(relative)
    path = data.repository_path(relative)
    assert path.stat().st_size == audit["bytes"]
    assert data.sha256(path.read_bytes()) == audit["sha256"]
    restored = ReplayEngine.load_checkpoint(relative, source_sha256="a"*64, config_sha256="b"*64, horizon_minutes=15)
    assert restored.state_dict() == engine.state_dict()
    assert restored.runtime["checkpoint_load_seconds"] > 0
    with pytest.raises(FileExistsError): engine.save_checkpoint(relative)


@pytest.mark.parametrize("path", ["../outside.json", "results/research-baseline/a.json",
                                 "data/processed/binance/spot/BTCUSDT/1m/2026-07.json"])
def test_path_rejection_precedes_file_open(minute_bars, monkeypatch, path):
    called = []
    def forbidden(*args, **kwargs): called.append(1); raise AssertionError("unexpected open")
    monkeypatch.setattr(Path, "open", forbidden)
    with pytest.raises(ValueError, match="Checkpoint path"):
        ReplayEngine.load_checkpoint(path, source_sha256="a"*64, config_sha256="b"*64, horizon_minutes=15)
    assert not called


def test_corrupt_json_file_is_rejected_without_fallback(minute_bars, tmp_path, monkeypatch):
    from quantforge import data
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    relative = Path("results/online-foundation/synthetic-checkpoint-tests/corrupt.json")
    path = data.repository_path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"not-valid-json":', encoding="utf-8")
    with pytest.raises(ValueError, match="JSON"):
        ReplayEngine.load_checkpoint(relative, source_sha256="a"*64, config_sha256="b"*64, horizon_minutes=15)


def test_checkpoint_publish_failure_leaves_no_final_file(minute_bars, tmp_path, monkeypatch):
    from quantforge import data
    import quantforge.online.replay as replay
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    engine, *_ = synthetic_replay(minute_bars)
    def failure(*args): raise OSError("SYNTHETIC publish interruption")
    monkeypatch.setattr(replay.os, "link", failure)
    path = tmp_path / "results/online-foundation/run/h15.json"
    with pytest.raises(OSError, match="interruption"):
        engine.save_checkpoint(path)
    assert not path.exists()
    assert len(list(path.parent.glob("*.partial"))) == 1


def test_known_but_wrong_original_model_version_rejected(minute_bars):
    engine, fx, ly, events, *_ = synthetic_replay(minute_bars)
    for e in events[:30]: engine.step(e, fx, ly)
    state = engine.state_dict()
    # Use a valid actual version belonging to another forecast, not random text.
    updates = state["payload"]["updates"]
    row = next(r for r in updates if not r["carry_in"])
    row["original_model_version"] = engine.initial_model_version
    rehash(state)
    with pytest.raises(ValueError, match="exact original model version"):
        ReplayEngine.from_state_dict(state, source_sha256="a"*64, config_sha256="b"*64, horizon_minutes=15)


def test_queue_clock_cannot_disagree_with_replay_clock(minute_bars):
    engine, fx, ly, events, *_ = synthetic_replay(minute_bars)
    for e in events[:10]: engine.step(e, fx, ly)
    state = engine.state_dict()
    state["payload"]["clock"] = "2026-03-01T01:00:01+00:00"
    rehash(state)
    with pytest.raises(ValueError, match="clock"):
        ReplayEngine.from_state_dict(state, source_sha256="a"*64, config_sha256="b"*64, horizon_minutes=15)


@pytest.mark.parametrize("kind", ["event_released", "event_updated", "prediction_n", "prediction_updated", "orphan_carry", "initial_version"])
def test_resigned_checkpoint_cannot_forge_event_budget(minute_bars, kind):
    engine, fx, ly, events, *_ = synthetic_replay(minute_bars)
    if kind != "initial_version":
        for e in events[:10]: engine.step(e, fx, ly)
    state = engine.state_dict()
    payload = state["payload"]
    if kind == "event_released": payload["events"][0]["released"] += 1
    elif kind == "event_updated": payload["events"][0]["updated"] += 1
    elif kind == "prediction_n": payload["predictions"][0]["training_samples"] += 1
    elif kind == "prediction_updated": payload["predictions"][0]["updated_this_event"] += 1
    elif kind == "orphan_carry": payload["carry_ids"].append("orphan")
    else: payload["initial_model_version"] = "a"*64
    rehash(state)
    with pytest.raises(ValueError):
        ReplayEngine.from_state_dict(state, source_sha256="a"*64, config_sha256="b"*64, horizon_minutes=15)
