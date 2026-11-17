"""Synthetic-only causal replay tests; no market payloads or network."""
from dataclasses import replace
import copy

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import Ridge

from quantforge.features import build_features
from quantforge.labels import build_labels, mature_labels, LabelAvailabilityError
from quantforge.online.incremental_ridge import IncrementalRidge
from quantforge.online.replay import (
    DecisionEvent, ReplayEngine, GuardedFeatureAccessor, MatureLabelAccessor, event_from_metadata,
)


def synthetic_replay(minute_bars, horizon=15, *, periods=430):
    bars = minute_bars(periods, "2026-02-28T20:00:00Z")
    features, labels = build_features(bars), build_labels(bars, horizon)
    fit = pd.Timestamp("2026-03-01T00:00:00Z")
    metadata = features.metadata
    mask = metadata.valid_features & labels.frame.valid_label & labels.frame.label_available_time.le(fit)
    indices = metadata.index[mask]
    model = IncrementalRidge().initialize_from_batch(features.frame.loc[indices], mature_labels(labels.frame, fit, indices))
    engine = ReplayEngine(model, horizon, fit, "a" * 64, "b" * 64)
    feature_accessor = GuardedFeatureAccessor(features.frame, metadata)
    label_accessor = MatureLabelAccessor(labels.frame)
    carry = metadata.loc[metadata.valid_features & metadata.decision_time.lt(fit)
                         & labels.frame.label_available_time.gt(fit)]
    for _, row in carry.iterrows():
        engine.register_carry_in(event_from_metadata(row, horizon), feature_accessor)
    events = [event_from_metadata(row, horizon) for _, row in metadata.loc[metadata.decision_time.ge(fit)].iterrows()]
    return engine, feature_accessor, label_accessor, events, features, labels, indices


@pytest.mark.parametrize("horizon", [5, 15])
def test_cross_month_carry_and_update_before_prediction_match_batch(minute_bars, horizon):
    engine, fx, ly, events, features, labels, initial = synthetic_replay(minute_bars, horizon)
    assert engine.queue.pending_count() == len(engine.carry_ids) == horizon
    assert engine.queue.released_count() == 0
    first = events[0]
    assert first.decision_time == pd.Timestamp("2026-03-01T00:00:01Z")
    expected_ref = pd.Timestamp(engine.queue.state_dict()["payload"]["pending"][0]["feature_reference"])
    prediction = engine.step(first, fx, ly)
    assert engine.updates[0]["feature_reference"] == expected_ref.isoformat()
    assert engine.updates[0]["carry_in"] and engine.updates[0]["original_model_version"] == "carry_in:no_prediction"
    assert engine.updates[0]["label_available_time"] == first.decision_time.isoformat()
    assert prediction["training_samples"] == len(initial) + 1
    assert prediction["updated_this_event"] == 1
    assert prediction["model_version"] == engine.model.model_version != engine.initial_model_version
    all_indices = initial.append(pd.DatetimeIndex([expected_ref]))
    batch = Ridge(alpha=1.0, solver="svd").fit(engine.model.transform(features.frame.loc[all_indices]),
        mature_labels(labels.frame, first.decision_time, all_indices))
    expected = batch.predict(engine.model.transform(features.frame.loc[[pd.Timestamp(first.feature_reference)]]))[0]
    assert prediction["prediction"] == pytest.approx(expected, abs=1e-9, rel=1e-8)
    assert engine.queue.pending_count() == horizon and ly.row_reads == 1


@pytest.mark.parametrize("horizon", [5, 15])
def test_exact_maturity_read_gate_precedes_row_lookup(minute_bars, horizon):
    engine, fx, ly, events, features, labels, _ = synthetic_replay(minute_bars, horizon)
    sample = events[0].feedback(engine.model.model_version)
    before = ly.row_reads
    with pytest.raises(LabelAvailabilityError, match="before target row lookup"):
        ly(sample, sample.label_available_time - pd.Timedelta(seconds=1))
    assert ly.row_reads == before
    value = ly(sample, sample.label_available_time)
    expected = mature_labels(labels.frame.loc[[pd.Timestamp(sample.feature_reference)]], sample.label_available_time).iloc[0]
    assert value == expected and ly.row_reads == before + 1


def test_future_feature_read_and_forged_reference_are_rejected_before_lookup(minute_bars):
    engine, fx, _, events, _, _, _ = synthetic_replay(minute_bars)
    first = events[0]
    reads = fx.row_reads
    with pytest.raises(ValueError, match="Future features"):
        fx(first, first.decision_time - pd.Timedelta(seconds=1))
    assert fx.row_reads == reads
    with pytest.raises(ValueError, match="Feature reference"):
        replace(first, feature_reference=(pd.Timestamp(first.feature_reference) + pd.Timedelta(minutes=1)).isoformat())


def test_future_target_and_feature_perturbations_preserve_past_predictions(minute_bars):
    one, fx, ly, events, features, labels, _ = synthetic_replay(minute_bars)
    two = ReplayEngine.from_state_dict(one.state_dict(), source_sha256="a"*64, config_sha256="b"*64, horizon_minutes=15)
    cutoff = events[25].decision_time
    altered_features = features.frame.copy(deep=True)
    altered_labels = labels.frame.copy(deep=True)
    altered_features.loc[features.metadata.feature_available_time.gt(cutoff)] *= 100
    altered_labels.loc[altered_labels.label_available_time.gt(cutoff), "target_return"] += 12345
    altered_labels.loc[altered_labels.label_available_time.gt(cutoff), "valid_label"] = False
    other_fx = GuardedFeatureAccessor(altered_features, features.metadata)
    other_ly = MatureLabelAccessor(altered_labels)
    for event in events[:26]:
        assert one.step(event, fx, ly) == two.step(event, other_fx, other_ly)
    assert one.predictions == two.predictions and one.model.state_dict() == two.model.state_dict()
    # Future validity was never used to select current predictions or queue entries.
    assert one.queue.state_dict() == two.queue.state_dict()


def test_invalid_mature_labels_audited_without_fabrication_or_retry(minute_bars):
    engine, fx, ly, events, _, labels, _ = synthetic_replay(minute_bars)
    first_ref = pd.Timestamp(engine.queue.state_dict()["payload"]["pending"][0]["feature_reference"])
    labels.frame.loc[first_ref, "valid_label"] = False
    labels.frame.loc[first_ref, "target_return"] = np.nan
    n = engine.model.n
    prediction = engine.step(events[0], fx, ly)
    assert engine.model.n == n and prediction["updated_this_event"] == 0
    assert engine.updates[0]["status"] == "invalid_label"
    assert engine.queue.released_count() == 1 and len(engine.predictions) == 1
    engine.step(events[1], fx, ly)
    assert engine.model.n == n+1 and len(engine.updates) == 2


def test_invalid_current_features_still_release_older_labels(minute_bars):
    engine, fx, ly, events, features, _, _ = synthetic_replay(minute_bars)
    features.metadata.loc[pd.Timestamp(events[0].feature_reference), "valid_features"] = False
    assert engine.step(events[0], fx, ly) is None
    assert engine.diagnostics()["invalid_feature_count"] == 1
    assert engine.model.n == engine.initial_n+1 and engine.queue.released_count() == 1
    assert engine.queue.pending_count() == 14


def test_repeat_event_is_idempotent_and_conflicting_duplicate_refused(minute_bars):
    engine, fx, ly, events, _, _, _ = synthetic_replay(minute_bars)
    first = engine.step(events[0], fx, ly)
    state, reads = engine.state_dict(), (fx.row_reads, ly.row_reads)
    again = engine.step(events[0], fx, ly)
    assert first == again and engine.state_dict() == state
    assert (fx.row_reads, ly.row_reads) == reads
    assert engine.runtime["duplicate_replay_attempts"] == 1
    # Same ID with another valid time is a conflict rather than a new update.
    wrong = replace(events[1], sample_id=events[0].sample_id)
    with pytest.raises(ValueError, match="Conflicting"):
        engine.step(wrong, fx, ly)
    assert engine.state_dict() == state


def test_prediction_records_are_immutable_from_caller_and_future_updates(minute_bars):
    engine, fx, ly, events, _, _, _ = synthetic_replay(minute_bars)
    returned = engine.step(events[0], fx, ly)
    first = copy.deepcopy(engine.predictions[0])
    returned["prediction"] = 999
    for event in events[1:30]: engine.step(event, fx, ly)
    assert engine.predictions[0] == first


def test_horizon_engines_are_independent_and_wrong_horizon_fails(minute_bars):
    five, f5, y5, e5, *_ = synthetic_replay(minute_bars, 5)
    fifteen, f15, y15, e15, *_ = synthetic_replay(minute_bars, 15)
    state = fifteen.state_dict()
    five.step(e5[0], f5, y5)
    assert fifteen.state_dict() == state
    with pytest.raises(ValueError, match="horizon"):
        fifteen.step(e5[0], f15, y15)


def test_failed_label_provider_poisoned_engine_requires_checkpoint_restore(minute_bars):
    engine, fx, _, events, *_ = synthetic_replay(minute_bars)
    def broken(*args): raise RuntimeError("SYNTHETIC target failure")
    with pytest.raises(RuntimeError, match="target failure"):
        engine.step(events[0], fx, broken)
    assert engine.diagnostics()["poisoned"]
    with pytest.raises(ValueError, match="poisoned"):
        engine.step(events[0], fx, broken)
    with pytest.raises(ValueError, match="poisoned"):
        engine.state_dict()


def test_new_event_clock_cannot_move_backwards(minute_bars):
    engine, fx, ly, events, *_ = synthetic_replay(minute_bars)
    engine.step(events[2], fx, ly)
    with pytest.raises(ValueError, match="advance"):
        engine.step(events[1], fx, ly)


def test_missing_minutes_do_not_forge_a_label_or_trigger_future_selection(minute_bars):
    bars = minute_bars(400, "2026-02-28T20:00:00Z").drop(index=250)
    features, labels = build_features(bars), build_labels(bars, 15)
    event = event_from_metadata(features.metadata.iloc[240], 15)
    assert features.metadata.iloc[240].valid_features
    sample = event.feedback("c"*64)
    accessor = MatureLabelAccessor(labels.frame)
    assert accessor(sample, sample.label_available_time) is None
    assert labels.diagnostics["exclusion_counts"]["gap_in_label_horizon"] > 0


def test_holdout_time_rejected_before_any_accessor_call(minute_bars):
    engine, fx, ly, events, *_ = synthetic_replay(minute_bars)
    reads = (fx.row_reads, ly.row_reads)
    with pytest.raises(ValueError, match="holdout"):
        event_from_metadata({"decision_time": pd.Timestamp("2026-07-01T00:00:01Z"),
                             "feature_available_time": pd.Timestamp("2026-07-01T00:00:01Z")}, 15)
    sample = events[0].feedback("d"*64)
    with pytest.raises(ValueError, match="holdout"):
        ly(sample, pd.Timestamp("2026-07-01T00:00Z"))
    assert (fx.row_reads, ly.row_reads) == reads


def test_replay_counts_solves_and_pending_without_end_flush(minute_bars):
    engine, fx, ly, events, *_ = synthetic_replay(minute_bars)
    for event in events[:80]: engine.step(event, fx, ly)
    d = engine.diagnostics()
    assert d["event_count"] == d["prediction_count"] == d["valid_update_count"] == d["released_count"] == 80
    assert d["pending_count"] == d["carry_in_count"] == d["peak_queue_size"] == 15
    assert d["model"]["solve_calls"] == 81
    assert d["model"]["training_sample_visits"] == engine.initial_n+80
    assert d["runtime"]["label_row_reads"] == 80
    assert all(r["label_available_time"] <= r["released_at"] for r in engine.updates)
