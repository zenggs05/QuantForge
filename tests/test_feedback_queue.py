"""Synthetic delayed-feedback state and maturity tests; no market files."""
from dataclasses import replace
import copy
import hashlib
import json

import pandas as pd
import pytest

from quantforge.labels import LabelAvailabilityError, build_labels, mature_labels
from quantforge.online.feedback_queue import (
    CARRY_IN_MODEL_VERSION, DelayedFeedbackQueue, FeedbackQueueError, FeedbackSample,
)


def sample(sample_id="synthetic:one", decision="2026-03-01T00:00:01Z", horizon=15,
           feature_reference=None, version="synthetic:model:0"):
    t = pd.Timestamp(decision)
    start = t - pd.Timedelta(seconds=1)
    end = start + pd.Timedelta(minutes=horizon)
    return FeedbackSample(sample_id, t, t, start, end, end + pd.Timedelta(seconds=1),
                          feature_reference or sample_id, horizon, version)


def resign(state):
    state["sha256"] = hashlib.sha256(json.dumps(state["payload"], sort_keys=True,
        separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return state


@pytest.mark.parametrize("horizon", [5, 15])
def test_exact_maturity_boundary_and_at_most_once_release(horizon):
    q = DelayedFeedbackQueue(horizon)
    item = sample(horizon=horizon)
    q.register_sample(item)
    assert q.pending_count() == 1 and q.released_count() == 0
    assert q.release_matured(item.label_available_time-pd.Timedelta(seconds=1)) == []
    assert q.release_matured(item.label_available_time) == [item]
    assert q.release_matured(item.label_available_time) == []
    assert q.pending_count() == 0 and q.released_count() == 1
    with pytest.raises(FeedbackQueueError, match="Duplicate"):
        q.register_sample(item, as_of=item.label_available_time)
    assert q.released_ids == frozenset([item.sample_id]) and q.rejected_count == 1


def test_independent_horizons_release_different_labels_for_same_feature():
    queues = {h: DelayedFeedbackQueue(h) for h in (5, 15)}
    for h, q in queues.items(): q.register_sample(sample(horizon=h,feature_reference="same:bar"))
    t = pd.Timestamp("2026-03-01T00:05:01Z")
    assert len(queues[5].release_matured(t)) == 1
    assert queues[15].release_matured(t) == []
    with pytest.raises(FeedbackQueueError, match="horizons"):
        queues[15].register_sample(sample("other",decision="2026-03-01T00:05:01Z",horizon=5))


def test_simultaneous_events_order_is_independent_of_registration_order():
    items = [sample(name, feature_reference=f"synthetic-ref:{name}") for name in ("c", "a", "b")]
    one, two = DelayedFeedbackQueue(15), DelayedFeedbackQueue(15)
    for item in items: one.register_sample(item)
    for item in reversed(items): two.register_sample(item)
    assert one.state_dict() == two.state_dict()
    released = one.release_matured(items[0].label_available_time)
    assert [item.sample_id for item in released] == ["a", "b", "c"]
    assert two.release_matured(items[0].label_available_time) == released
    assert one.state_dict() == two.state_dict()


def test_out_of_registration_order_releases_by_maturity_and_carries_across_month():
    fit = pd.Timestamp("2026-03-01T00:00:00Z")
    early = sample("early", "2026-02-28T23:46:01Z", version=CARRY_IN_MODEL_VERSION)
    late = sample("late", "2026-02-28T23:59:01Z", version=CARRY_IN_MODEL_VERSION)
    q = DelayedFeedbackQueue(15)
    q.register_sample(late, as_of=fit)
    q.register_sample(early, as_of=fit)
    assert q.release_matured(fit) == []
    assert q.release_matured("2026-03-01T00:01:00Z") == []
    assert q.release_matured("2026-03-01T00:01:01Z") == [early]
    assert q.release_matured("2026-03-01T00:14:01Z") == [late]


def test_future_feature_and_registration_after_maturity_rejected_without_state_change():
    q = DelayedFeedbackQueue(15)
    item = sample()
    with pytest.raises(FeedbackQueueError, match="unavailable"):
        q.register_sample(item,as_of=item.decision_time-pd.Timedelta(seconds=1))
    with pytest.raises(FeedbackQueueError, match="immature"):
        q.register_sample(item,as_of=item.label_available_time)
    assert q.registered_count == 0 and q.clock is None and q.rejected_count == 2


def test_duplicate_label_alias_rejected_pending_and_after_release():
    q = DelayedFeedbackQueue(15)
    item = sample(feature_reference="same bar")
    q.register_sample(item)
    with pytest.raises(FeedbackQueueError,match="alias"):
        q.register_sample(replace(item,sample_id="alias"))
    q.release_matured(item.label_available_time)
    with pytest.raises(FeedbackQueueError,match="alias"):
        q.register_sample(replace(item,sample_id="another"),as_of=item.label_available_time)
    assert q.registered_count == 1 and q.released_count() == 1


def test_release_before_prediction_registration_and_monotonic_clock():
    q = DelayedFeedbackQueue(5)
    first = sample(horizon=5)
    q.register_sample(first)
    current = sample("current","2026-03-01T00:05:01Z",horizon=5)
    with pytest.raises(FeedbackQueueError,match="Release"):
        q.register_sample(current)
    assert q.release_matured(current.decision_time) == [first]
    q.register_sample(current)
    with pytest.raises(FeedbackQueueError,match="backward"):
        q.release_matured(current.decision_time-pd.Timedelta(seconds=1))
    assert q.pending_count() == 1


@pytest.mark.parametrize("extra", ["target_return", "y", "target", "valid_label", "future_close"])
def test_queue_schema_rejects_future_target_and_future_validity_fields(extra):
    record = sample().to_dict()
    record[extra] = 123.0
    q = DelayedFeedbackQueue(15)
    with pytest.raises(FeedbackQueueError,match="metadata-only"):
        q.register_sample(record)
    assert q.pending_count() == 0


def test_queue_does_not_obtain_or_serialize_targets_and_provider_still_checks_maturity(minute_bars):
    labels = build_labels(minute_bars(100,"2026-03-01T00:00:00Z"),15).frame
    index = labels.index[10]
    row = labels.loc[index]
    item = sample(decision=(row.target_start_time+pd.Timedelta(seconds=1)).isoformat(),feature_reference=index.isoformat())
    q = DelayedFeedbackQueue(15)
    q.register_sample(item)
    assert "target_return" not in json.dumps(q.state_dict())
    with pytest.raises(LabelAvailabilityError,match="immature"):
        mature_labels(labels,item.decision_time,indices=[index])
    released = q.release_matured(item.label_available_time)
    assert len(released) == 1
    target = mature_labels(labels,item.label_available_time,indices=[pd.Timestamp(released[0].feature_reference)])
    assert target.iloc[0] == row.target_return


def test_gap_invalidity_is_discovered_at_release_never_fabricated(minute_bars):
    bars = minute_bars(100,"2026-03-01T00:00:00Z").drop(index=15)
    labels = build_labels(bars,5).frame
    index = pd.Timestamp("2026-03-01T00:13:00Z")
    row = labels.loc[index]
    item = sample(decision=(row.target_start_time+pd.Timedelta(seconds=1)).isoformat(),horizon=5,feature_reference=index.isoformat())
    q=DelayedFeedbackQueue(5);q.register_sample(item)
    assert q.release_matured(item.label_available_time) == [item]
    with pytest.raises(LabelAvailabilityError,match="invalid"):
        mature_labels(labels,item.label_available_time,indices=[index])
    assert q.release_matured(item.label_available_time) == []
    assert q.released_count() == 1


def test_state_json_roundtrip_and_resume_never_releases_consumed_label_twice():
    q=DelayedFeedbackQueue(5)
    first=sample("first",horizon=5)
    last=sample("last","2026-03-01T00:01:01Z",horizon=5)
    q.register_sample(first);q.register_sample(last)
    q.release_matured(first.label_available_time)
    state=json.loads(json.dumps(q.state_dict()))
    restored=DelayedFeedbackQueue(5).load_state_dict(state)
    assert restored.state_dict()==q.state_dict()
    assert restored.release_matured(first.label_available_time)==[]
    assert restored.release_matured(last.label_available_time)==[last]
    with pytest.raises(FeedbackQueueError,match="Duplicate"):
        restored.register_sample(first,as_of=last.label_available_time)
    assert restored.released_count()==2


def test_state_snapshots_are_detached_and_failed_load_is_atomic():
    q=DelayedFeedbackQueue(15);q.register_sample(sample())
    original=q.state_dict(); modified=q.state_dict()
    modified["payload"]["pending"][0]["sample_id"]="corrupt"
    with pytest.raises(FeedbackQueueError,match="checksum"):
        q.load_state_dict(modified)
    assert q.state_dict()==original
    with pytest.raises(AttributeError): q.released_ids.add("fake")


@pytest.mark.parametrize("corruption", ["schema", "horizon", "counter", "negative", "alias", "lost_ref", "wrong_ref",
                                      "released_pending", "past_due", "future_clock", "no_clock", "extra_target", "pending_target"])
def test_recomputed_checksum_cannot_bypass_restored_state_invariants(corruption):
    q=DelayedFeedbackQueue(15);q.register_sample(sample())
    original=q.state_dict(); state=copy.deepcopy(original); p=state["payload"]
    if corruption=="schema": state["schema_version"]=2
    elif corruption=="horizon": p["horizon_minutes"]=5
    elif corruption=="counter": p["registered_count"]=2
    elif corruption=="negative": p["rejected_count"]=-1
    elif corruption=="alias": p["registered_refs"]["alias"]="synthetic:one"
    elif corruption=="lost_ref": p["registered_refs"]={}
    elif corruption=="wrong_ref": p["registered_refs"]={"wrong":"synthetic:one"}
    elif corruption=="released_pending": p["released_ids"]=["synthetic:one"]
    elif corruption=="past_due": p["clock"]="2026-03-01T00:15:01+00:00"
    elif corruption=="future_clock": p["clock"]="2026-02-28T23:59:00+00:00"
    elif corruption=="no_clock": p["clock"]=None
    elif corruption=="extra_target": p["target_return"]=0.2
    else: p["pending"][0]["target_return"]=0.2
    resign(state)
    with pytest.raises(ValueError): q.load_state_dict(state)
    assert q.state_dict()==original


@pytest.mark.parametrize("change", ["naive", "delay", "horizon", "grid", "missing_model", "empty_ref"])
def test_invalid_sample_metadata_rejected(change):
    record=sample().to_dict()
    if change=="naive": record["decision_time"]="2026-03-01T00:00:01"
    elif change=="delay": record["label_available_time"]="2026-03-01T00:15:00Z"
    elif change=="horizon": record["horizon_minutes"]=True
    elif change=="grid":
        for key in ["decision_time","feature_available_time","target_start_time","target_end_time","label_available_time"]:
            record[key]=(pd.Timestamp(record[key])+pd.Timedelta(seconds=1)).isoformat()
    elif change=="missing_model": record["original_model_version"]=""
    else: record["feature_reference"]=" "
    with pytest.raises(ValueError): FeedbackSample.from_dict(record)


def test_timezone_normalization_retains_same_instant_and_empty_state_roundtrip():
    utc=sample()
    record=utc.to_dict()
    for key in ["decision_time","feature_available_time","target_start_time","target_end_time","label_available_time"]:
        record[key]=pd.Timestamp(record[key]).tz_convert("Asia/Shanghai")
    assert FeedbackSample.from_dict(record)==utc
    empty=DelayedFeedbackQueue(15)
    assert empty.load_state_dict(empty.state_dict()).diagnostics()["registered_count"]==0


def test_unsupported_nanosecond_range_rejected_before_queue_mutation():
    q = DelayedFeedbackQueue(15)
    with pytest.raises(ValueError):
        sample(decision="3000-03-01T00:00:01Z")
    with pytest.raises(ValueError):
        q.release_matured("3000-03-01T00:00:01Z")
    assert q.clock is None and q.registered_count == q.pending_count() == q.released_count() == 0
