"""Version advancement includes events without a current prediction."""
import pandas as pd
import pytest

from quantforge.online.replay import ReplayEngine
from tests.test_online_checkpoint import rehash
from tests.test_online_replay import synthetic_replay


def invalid_feature_history(minute_bars):
    engine, fx, ly, events, features, labels, _ = synthetic_replay(minute_bars, 5)
    features.metadata.loc[pd.Timestamp(events[1].feature_reference), "valid_features"] = False
    # The third event releases invalid feedback and therefore retains the
    # version advanced by the second, non-predicting event.
    carry_ref = pd.Timestamp(engine.queue.state_dict()["payload"]["pending"][2]["feature_reference"])
    labels.frame.loc[carry_ref, "valid_label"] = False
    for event in events[:4]:
        engine.step(event, fx, ly)
    return engine


def test_valid_chain_advances_over_invalid_feature_event(minute_bars):
    engine = invalid_feature_history(minute_bars)
    assert engine.events[1]["status"] == "invalid_features" and engine.events[1]["updated"] == 1
    assert engine.events[2]["updated"] == 0
    assert engine.events[1]["model_version"] == engine.events[2]["model_version"]
    assert engine.predictions[0]["model_version"] != engine.predictions[1]["model_version"]
    prior, steps = engine.initial_model_version, 0
    for row in engine.events:
        steps += prior != row["model_version"]
        prior = row["model_version"]
    assert steps == sum(row["updated"] > 0 for row in engine.events)
    restored = ReplayEngine.from_state_dict(engine.state_dict(), source_sha256="a"*64,
                                            config_sha256="b"*64, horizon_minutes=5)
    assert restored.state_dict() == engine.state_dict()


@pytest.mark.parametrize("kind,message", [("rollback", "failed to advance"), ("prediction", "original prediction identity/version"),
                                          ("no_update_change", "changed without valid updates")])
def test_resigned_invalid_feature_version_chain_rejected(minute_bars, kind, message):
    engine = invalid_feature_history(minute_bars)
    state = engine.state_dict()
    p = state["payload"]
    if kind == "rollback":
        p["events"][1]["model_version"] = p["events"][0]["model_version"]
    elif kind == "prediction":
        p["predictions"][1]["model_version"] = "d"*64
    else:
        p["events"][2]["model_version"] = p["predictions"][1]["model_version"] = "d"*64
        # Match the pending original version so the event-chain check is isolated.
        sid = p["predictions"][1]["sample_id"]
        for row in p["queue"]["payload"]["pending"]:
            if row["sample_id"] == sid:
                row["original_model_version"] = "d"*64
        from quantforge.online.feedback_queue import _digest
        p["queue"]["sha256"] = _digest(p["queue"]["payload"])
    rehash(state)
    with pytest.raises(ValueError, match=message):
        ReplayEngine.from_state_dict(state, source_sha256="a"*64, config_sha256="b"*64, horizon_minutes=5)
