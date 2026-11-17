"""Synthetic v1/v2 initial, resumed and invalid-feedback equivalence."""
import json

import pandas as pd
import pytest

from quantforge import data
from quantforge.online.checkpoint_v2 import load_v2
from quantforge.online.replay import ReplayEngine, _canonical
from tests.test_online_replay import synthetic_replay
from tests.test_phase2f_checkpoint_v2_roundtrip import pair_paths


@pytest.mark.parametrize("horizon", [5, 15])
@pytest.mark.parametrize("count", [0, 40])
def test_initial_and_running_v2_roundtrip_exact(minute_bars, tmp_path, monkeypatch, horizon, count):
    engine, fx, ly, events, *_ = synthetic_replay(minute_bars, horizon)
    for event in events[:count]:
        engine.step(event, fx, ly)
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    core, log = pair_paths(tmp_path)
    engine.save_checkpoint_v2(core, log)
    body = json.loads(core.read_bytes())
    assert not {"events", "predictions", "updates", "completed_events"} & set(body["payload"])
    assert not {"released_ids", "registered_refs"} & set(body["payload"]["queue"])
    assert set(body["payload"]["feature_cache"]) == {r["sample_id"] for r in body["payload"]["queue"]["pending"]}
    assert core.stat().st_size < 33_554_432
    recovered = ReplayEngine.load_checkpoint(core, source_sha256="a"*64, config_sha256="b"*64,
                                             horizon_minutes=horizon)
    assert recovered.state_dict() == engine.state_dict()
    via_index = ReplayEngine.load_checkpoint(core.parent / "checkpoint-audit.json", source_sha256="a"*64,
                                             config_sha256="b"*64, horizon_minutes=horizon)
    assert via_index.state_dict() == engine.state_dict()
    for event in events[count:90]:
        assert engine.step(event, fx, ly) == recovered.step(event, fx, ly)
    assert _canonical(engine.state_dict()) == _canonical(recovered.state_dict())


@pytest.mark.parametrize("horizon", [5, 15])
def test_v1_v2_continuation_with_invalid_feedback_and_features(minute_bars, tmp_path, monkeypatch, horizon):
    engine, fx, ly, events, features, labels, _ = synthetic_replay(minute_bars, horizon)
    ref = pd.Timestamp(engine.queue.state_dict()["payload"]["pending"][0]["feature_reference"])
    labels.frame.loc[ref, "valid_label"] = False
    features.metadata.loc[pd.Timestamp(events[1].feature_reference), "valid_features"] = False
    for event in events[:40]:
        engine.step(event, fx, ly)
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    core, log = pair_paths(tmp_path)
    v1 = tmp_path / "results" / "online-foundation" / "v1.json"
    engine.save_checkpoint(v1)
    assert v1.read_bytes() == _canonical(engine.state_dict()) + b"\n"
    engine.save_checkpoint_v2(core, log)
    one = ReplayEngine.load_checkpoint(v1, source_sha256="a"*64, config_sha256="b"*64, horizon_minutes=horizon)
    two = load_v2(core, log, "a"*64, "b"*64, horizon)
    assert one.state_dict() == two.state_dict()
    assert any(row["status"] == "invalid_label" for row in two.updates)
    assert any(row["status"] == "invalid_features" for row in two.events)
    for event in events[40:90]:
        assert one.step(event, fx, ly) == two.step(event, fx, ly)
    first, second = core.parent / "first-v1.json", core.parent / "second-v1.json"
    one.save_checkpoint(first)
    two.save_checkpoint(second)
    assert first.read_bytes() == second.read_bytes()
