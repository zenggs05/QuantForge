"""Causal delayed-feedback replay with immutable forecasts and safe checkpoints.

Offline frames belong to accessors. The engine receives a current feature row
or a released label only through a checked call, never a target matrix.
"""
from __future__ import annotations

from dataclasses import dataclass
import copy
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import numpy as np
import pandas as pd

from quantforge import data
from quantforge.labels import mature_labels, LabelAvailabilityError
from quantforge.research_policy import DEVELOPMENT_START, DEVELOPMENT_END
from quantforge.temporal import utc_timestamp
from quantforge.online.feedback_queue import FeedbackSample, DelayedFeedbackQueue
from quantforge.online.incremental_ridge import IncrementalRidge

CARRY_VERSION = "carry_in:no_prediction"
SCHEMA_VERSION = 1
PENDING_RECORD_SCHEMA = frozenset(FeedbackSample.__dataclass_fields__)
PREDICTION_RECORD_SCHEMA = (PENDING_RECORD_SCHEMA - {"original_model_version"}) | {
    "prediction", "model_version", "training_samples", "released_this_event", "updated_this_event"}
UPDATE_RECORD_SCHEMA = frozenset({"sample_id", "feature_reference", "decision_time", "label_available_time",
    "released_at", "horizon_minutes", "original_model_version", "carry_in", "n_before", "n_after", "status"})
EVENT_RECORD_SCHEMA = frozenset({"sample_id", "decision_time", "status", "released", "updated", "model_version"})
COMPLETED_RECORD_SCHEMA = frozenset({"event_sha256", "event", "prediction_index"})


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _hash_value(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{name} must be a lowercase SHA256")
    return value


def _allowed_time(value: object, name: str) -> pd.Timestamp:
    time = utc_timestamp(value, name)
    if time < DEVELOPMENT_START or time >= DEVELOPMENT_END:
        raise ValueError(f"{name} exceeds development bounds; final holdout prohibited")
    return time


@dataclass(frozen=True)
class DecisionEvent:
    sample_id: str
    decision_time: object
    feature_available_time: object
    target_start_time: object
    target_end_time: object
    label_available_time: object
    feature_reference: str
    horizon_minutes: int

    def __post_init__(self):
        # Reuse the queue's full temporal contract without storing any target.
        sample = self.feedback(CARRY_VERSION)
        for name in ("decision_time", "feature_available_time", "target_start_time", "target_end_time", "label_available_time"):
            object.__setattr__(self, name, getattr(sample, name))
        ref = utc_timestamp(self.feature_reference, "feature_reference")
        if ref != ref.floor("min") or self.decision_time != ref + pd.Timedelta(minutes=1, seconds=1):
            raise ValueError("Feature reference must identify the decision's minute-open row")
        _allowed_time(self.decision_time, "decision_time")
        object.__setattr__(self, "feature_reference", ref.isoformat())

    def feedback(self, model_version: str) -> FeedbackSample:
        return FeedbackSample(**{k: getattr(self, k) for k in self.__dataclass_fields__}, original_model_version=model_version)

    def to_dict(self) -> dict:
        result = self.feedback(CARRY_VERSION).to_dict()
        result.pop("original_model_version")
        return result

    @classmethod
    def from_dict(cls, value: dict) -> "DecisionEvent":
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("Invalid decision event schema")
        return cls(**value)


def event_from_metadata(row: pd.Series | dict, horizon_minutes: int) -> DecisionEvent:
    """Construct maturity metadata using no future target or validity field."""
    decision = utc_timestamp(row["decision_time"], "decision_time")
    ref = (decision - pd.Timedelta(minutes=1, seconds=1)).isoformat()
    start = decision - pd.Timedelta(seconds=1)
    return DecisionEvent(sample_id=f"{horizon_minutes}:{ref}", decision_time=decision,
        feature_available_time=row["feature_available_time"], target_start_time=start,
        target_end_time=start + pd.Timedelta(minutes=horizon_minutes),
        label_available_time=decision + pd.Timedelta(minutes=horizon_minutes),
        feature_reference=ref, horizon_minutes=horizon_minutes)


class GuardedFeatureAccessor:
    """One-row feature access, gated before lookup by the real availability."""
    def __init__(self, frame: pd.DataFrame, metadata: pd.DataFrame):
        if not frame.index.is_unique or not metadata.index.is_unique:
            raise ValueError("Feature accessor indices must be unique")
        self._frame, self._metadata = frame, metadata
        self.row_reads = 0

    def __call__(self, event: DecisionEvent | FeedbackSample, as_of: object) -> np.ndarray | None:
        now = _allowed_time(as_of, "feature as_of")
        ref = utc_timestamp(event.feature_reference, "feature_reference")
        # Canonical availability is derived before either frame is indexed.
        available = ref + pd.Timedelta(minutes=1, seconds=1)
        if available > now or utc_timestamp(event.feature_available_time) != available:
            raise ValueError("Future features cannot be accessed before availability")
        if not DEVELOPMENT_START <= ref < DEVELOPMENT_END:
            raise ValueError("Feature source exceeds development bounds")
        meta = self._metadata.loc[ref]
        if utc_timestamp(meta.feature_available_time) != available:
            raise ValueError("Feature metadata availability mismatch")
        self.row_reads += 1
        if not bool(meta.valid_features):
            return None
        values = self._frame.loc[ref].to_numpy(dtype=np.float64, copy=True)
        if not np.isfinite(values).all():
            raise ValueError("Valid feature row contains nonfinite values")
        return values


class MatureLabelAccessor:
    """Gate release before reading even the requested row's target/validity."""
    def __init__(self, frame: pd.DataFrame):
        if not frame.index.is_unique:
            raise ValueError("Label accessor index must be unique")
        self._frame = frame
        self.row_reads = 0

    def __call__(self, sample: FeedbackSample, as_of: object) -> float | None:
        now = _allowed_time(as_of, "label as_of")
        maturity = utc_timestamp(sample.label_available_time)
        ref = utc_timestamp(sample.feature_reference, "feature_reference")
        expected = ref + pd.Timedelta(minutes=1 + sample.horizon_minutes, seconds=1)
        if maturity != expected or maturity > now:
            raise LabelAvailabilityError("Future label access rejected before target row lookup")
        if not DEVELOPMENT_START <= ref < DEVELOPMENT_END or expected >= DEVELOPMENT_END:
            raise LabelAvailabilityError("Label source/maturity reaches final holdout")
        selected = self._frame.loc[[ref]]
        self.row_reads += 1
        actual = selected.iloc[0]
        if (utc_timestamp(actual.label_available_time) != maturity
                or utc_timestamp(actual.target_start_time) != sample.target_start_time
                or utc_timestamp(actual.target_end_time) != sample.target_end_time):
            raise LabelAvailabilityError("Released target metadata mismatch")
        if not bool(actual.valid_label):
            return None
        return float(mature_labels(selected, as_of=now).iloc[0])


class ReplayEngine:
    """One independent horizon, fixed initial model and monotone logical clock.

    An exception after an event starts poisons this in-memory engine. Resume
    only from the last completed checkpoint; partial releases cannot be retried.
    This avoids copying the full event history on every O(p²) update.
    """
    def __init__(self, model: IncrementalRidge, horizon_minutes: int, initial_fit_time: object,
                 source_sha256: str, config_sha256: str):
        if model.n != model.solved_n:
            raise ValueError("Replay needs an initialized solved model")
        self.model = model
        self.horizon_minutes = horizon_minutes
        self.queue = DelayedFeedbackQueue(horizon_minutes)
        self.initial_fit_time = _allowed_time(initial_fit_time, "initial_fit_time")
        self.source_sha256 = _hash_value(source_sha256, "source_sha256")
        self.config_sha256 = _hash_value(config_sha256, "config_sha256")
        self.clock = self.initial_fit_time
        self.initial_n = model.n
        self.initial_model_version = model.model_version
        self.feature_cache: dict[str, list[float]] = {}
        self.carry_ids: list[str] = []
        self.completed_events: dict[str, dict] = {}
        self.predictions: list[dict] = []
        self.updates: list[dict] = []
        self.events: list[dict] = []
        self._poisoned = False
        self.peak_queue_size = 0
        self.runtime = {"feature_access_seconds": 0.0, "label_access_seconds": 0.0,
            "update_seconds": 0.0, "solve_seconds": 0.0, "prediction_seconds": 0.0,
            "checkpoint_save_seconds": 0.0, "checkpoint_load_seconds": 0.0,
            "duplicate_replay_attempts": 0, "failed_events": 0,
            "feature_row_reads": 0, "label_row_reads": 0}

    def _ready(self):
        if self._poisoned:
            raise ValueError("Failed replay engine is poisoned; restore a completed checkpoint")

    def _event(self, event: DecisionEvent):
        if not isinstance(event, DecisionEvent) or event.horizon_minutes != self.horizon_minutes:
            raise ValueError("Replay event horizon mismatch")

    def _features(self, event, as_of, provider):
        if event.feature_available_time > as_of:
            raise ValueError("Future features are unavailable")
        tick = perf_counter()
        result = provider(event, as_of)
        self.runtime["feature_access_seconds"] += perf_counter() - tick
        self.runtime["feature_row_reads"] += 1
        if result is None:
            return None
        vector = np.asarray(result, dtype=np.float64)
        if vector.shape != (len(self.model.feature_names),) or not np.isfinite(vector).all():
            raise ValueError("Observed feature vector is invalid")
        return vector.copy()

    def register_carry_in(self, event: DecisionEvent, feature_accessor) -> bool:
        self._ready()
        self._event(event)
        if self.completed_events or self.clock != self.initial_fit_time:
            raise ValueError("Carry-in may only be registered before the replay starts")
        if not event.decision_time < self.initial_fit_time < event.label_available_time:
            raise ValueError("Carry-in must be pre-fit but still immature")
        if event.sample_id in self.feature_cache:
            raise ValueError("Duplicate carry-in sample")
        feature = self._features(event, self.initial_fit_time, feature_accessor)
        if feature is None:
            return False
        self.queue.register_sample(event.feedback(CARRY_VERSION), as_of=self.initial_fit_time)
        self.feature_cache[event.sample_id] = feature.tolist()
        self.carry_ids.append(event.sample_id)
        self.peak_queue_size = max(self.peak_queue_size, self.queue.pending_count())
        return True

    def step(self, event: DecisionEvent, feature_accessor, label_accessor) -> dict | None:
        self._ready()
        self._event(event)
        fingerprint = _digest(event.to_dict())
        if event.sample_id in self.completed_events:
            previous = self.completed_events[event.sample_id]
            if previous["event_sha256"] != fingerprint:
                raise ValueError("Conflicting duplicate replay event")
            self.runtime["duplicate_replay_attempts"] += 1
            number = previous["prediction_index"]
            return None if number is None else copy.deepcopy(self.predictions[number])
        now = _allowed_time(event.decision_time, "decision_time")
        if now <= self.clock:
            raise ValueError("New replay events must advance the logical clock")
        try:
            feature = self._features(event, now, feature_accessor)
            released = self.queue.release_matured(now)
            valid_updates = 0
            for sample in released:
                if sample.sample_id not in self.feature_cache or sample.label_available_time > now:
                    raise ValueError("Released feedback lacks observed features or is premature")
                tick = perf_counter()
                target = label_accessor(sample, now)
                self.runtime["label_access_seconds"] += perf_counter() - tick
                self.runtime["label_row_reads"] += 1
                record = {"sample_id": sample.sample_id, "feature_reference": sample.feature_reference,
                    "decision_time": sample.decision_time.isoformat(), "label_available_time": sample.label_available_time.isoformat(),
                    "released_at": now.isoformat(), "horizon_minutes": self.horizon_minutes,
                    "original_model_version": sample.original_model_version,
                    "carry_in": sample.sample_id in self.carry_ids, "n_before": self.model.n}
                if target is None:
                    record.update(status="invalid_label", n_after=self.model.n)
                else:
                    if not np.isfinite(target):
                        raise ValueError("Released valid target must be finite")
                    tick = perf_counter()
                    self.model.update_one(np.asarray(self.feature_cache[sample.sample_id]), float(target))
                    self.runtime["update_seconds"] += perf_counter() - tick
                    valid_updates += 1
                    record.update(status="updated", n_after=self.model.n)
                self.updates.append(record)
                del self.feature_cache[sample.sample_id]
            if valid_updates:
                tick = perf_counter()
                self.model.solve()
                self.runtime["solve_seconds"] += perf_counter() - tick
            prediction = None
            prediction_index = None
            if feature is not None:
                tick = perf_counter()
                value = float(self.model.predict(feature.reshape(1, -1))[0])
                self.runtime["prediction_seconds"] += perf_counter() - tick
                prediction = {**event.to_dict(), "prediction": value, "model_version": self.model.model_version,
                    "training_samples": self.model.n, "released_this_event": len(released),
                    "updated_this_event": valid_updates}
                self.queue.register_sample(event.feedback(self.model.model_version), as_of=now)
                self.feature_cache[event.sample_id] = feature.tolist()
                prediction_index = len(self.predictions)
                self.predictions.append(prediction)
            self.clock = now
            self.completed_events[event.sample_id] = {"event_sha256": fingerprint, "event": event.to_dict(),
                                                    "prediction_index": prediction_index}
            self.events.append({"sample_id": event.sample_id, "decision_time": now.isoformat(),
                "status": "predicted" if feature is not None else "invalid_features", "released": len(released),
                "updated": valid_updates, "model_version": self.model.model_version})
            self.peak_queue_size = max(self.peak_queue_size, self.queue.pending_count())
            return copy.deepcopy(prediction)
        except Exception:
            self._poisoned = True
            self.runtime["failed_events"] += 1
            raise

    def diagnostics(self) -> dict:
        return {"horizon_minutes": self.horizon_minutes, "initial_samples": self.initial_n,
            "event_count": len(self.events), "prediction_count": len(self.predictions),
            "released_count": self.queue.released_count(), "valid_update_count": sum(r["status"] == "updated" for r in self.updates),
            "invalid_label_count": sum(r["status"] == "invalid_label" for r in self.updates),
            "invalid_feature_count": sum(r["status"] == "invalid_features" for r in self.events),
            "carry_in_count": len(self.carry_ids), "pending_count": self.queue.pending_count(),
            "peak_queue_size": self.peak_queue_size, "poisoned": self._poisoned,
            "model": self.model.diagnostics(), "runtime": copy.deepcopy(self.runtime)}

    def state_dict(self) -> dict:
        self._ready()
        payload = {"horizon_minutes": self.horizon_minutes, "initial_fit_time": self.initial_fit_time.isoformat(),
            "clock": self.clock.isoformat(), "initial_n": self.initial_n, "initial_model_version": self.initial_model_version,
            "source_sha256": self.source_sha256, "config_sha256": self.config_sha256,
            "model": self.model.state_dict(), "queue": self.queue.state_dict(),
            "feature_cache": self.feature_cache, "carry_ids": self.carry_ids,
            "completed_events": self.completed_events, "predictions": self.predictions,
            "updates": self.updates, "events": self.events, "peak_queue_size": self.peak_queue_size}
        result = {"schema_version": SCHEMA_VERSION, "payload": copy.deepcopy(payload)}
        result["sha256"] = _digest(result)
        return result

    @classmethod
    def from_state_dict(cls, state: dict, *, source_sha256: str, config_sha256: str,
                        horizon_minutes: int) -> "ReplayEngine":
        if (not isinstance(state, dict) or set(state) != {"schema_version", "payload", "sha256"}
                or type(state["schema_version"]) is not int or state["schema_version"] != SCHEMA_VERSION
                or state["sha256"] != _digest({k: state[k] for k in ("schema_version", "payload")})):
            raise ValueError("Invalid replay checkpoint schema or checksum")
        p = copy.deepcopy(state["payload"])
        required = {"horizon_minutes", "initial_fit_time", "clock", "initial_n", "initial_model_version", "source_sha256", "config_sha256",
            "model", "queue", "feature_cache", "carry_ids", "completed_events", "predictions", "updates", "events", "peak_queue_size"}
        if not isinstance(p, dict) or set(p) != required:
            raise ValueError("Invalid replay payload schema")
        if (p["source_sha256"] != source_sha256 or p["config_sha256"] != config_sha256
                or p["horizon_minutes"] != horizon_minutes):
            raise ValueError("Replay checkpoint source/config/horizon is incompatible")
        model = IncrementalRidge().load_state_dict(p["model"])
        engine = cls(model, horizon_minutes, p["initial_fit_time"], source_sha256, config_sha256)
        engine.queue.load_state_dict(p["queue"])
        engine.clock = _allowed_time(p["clock"], "checkpoint clock")
        if engine.clock < engine.initial_fit_time:
            raise ValueError("Checkpoint clock precedes initial fit")
        if engine.queue.clock is not None and engine.queue.clock != engine.clock:
            raise ValueError("Checkpoint queue clock and replay clock disagree")
        if type(p["initial_n"]) is not int or p["initial_n"] != model.initial_n:
            raise ValueError("Checkpoint initial sample count mismatch")
        engine.initial_n = p["initial_n"]
        engine.initial_model_version = _hash_value(p["initial_model_version"], "initial model version")
        for field in ("feature_cache", "carry_ids", "completed_events", "predictions", "updates", "events", "peak_queue_size"):
            setattr(engine, field, p[field])
        engine._validate_restored_state()
        return engine

    def _validate_restored_state(self):
        self._validate_record_schemas()
        if not all(isinstance(v, list) for v in (self.carry_ids, self.predictions, self.updates, self.events)):
            raise ValueError("Checkpoint audit collections must be lists")
        if not isinstance(self.completed_events, dict) or not isinstance(self.feature_cache, dict):
            raise ValueError("Checkpoint event/cache maps invalid")
        if len(set(self.carry_ids)) != len(self.carry_ids) or set(self.carry_ids) & set(self.completed_events):
            raise ValueError("Carry-in IDs cannot overlap replay events")
        pending = self.queue.state_dict()["payload"]["pending"]
        pending_map = {r["sample_id"]: r for r in pending}
        if set(self.feature_cache) != set(pending_map):
            raise ValueError("Checkpoint pending feature cache mismatch")
        for vector in self.feature_cache.values():
            a = np.asarray(vector, dtype=float)
            if a.shape != (len(self.model.feature_names),) or not np.isfinite(a).all():
                raise ValueError("Checkpoint feature cache is invalid")
        update_ids = [r["sample_id"] for r in self.updates]
        if len(set(update_ids)) != len(update_ids) or set(update_ids) != self.queue.released_ids:
            raise ValueError("Consumed feedback IDs disagree with update audit")
        if not set(self.carry_ids).issubset(set(pending_map) | set(update_ids)):
            raise ValueError("Checkpoint carry-in ID is orphaned")
        valid = 0
        previous_release = self.initial_fit_time
        release_counts: dict[str, list[int]] = {}
        for row in self.updates:
            release, maturity = _allowed_time(row["released_at"], "released_at"), utc_timestamp(row["label_available_time"])
            if not previous_release <= release <= self.clock or maturity > release:
                raise ValueError("Checkpoint feedback maturity/order mismatch")
            previous_release = release
            if row["status"] not in ("updated", "invalid_label") or row["n_before"] != self.initial_n + valid:
                raise ValueError("Checkpoint feedback sample count mismatch")
            valid += row["status"] == "updated"
            if row["n_after"] != self.initial_n + valid:
                raise ValueError("Checkpoint updated sample count mismatch")
            key = release.isoformat()
            counters = release_counts.setdefault(key, [0, 0, self.initial_n])
            counters[0] += 1
            counters[1] += row["status"] == "updated"
            counters[2] = self.initial_n + valid
        if self.model.n != self.initial_n + valid:
            raise ValueError("Checkpoint model sample count does not match consumed feedback")
        if len(self.events) != len(self.completed_events):
            raise ValueError("Checkpoint completed event count mismatch")
        seen, previous, prediction_count, expected_n = set(), self.initial_fit_time, 0, self.initial_n
        versions = {self.initial_model_version}
        for row in self.events:
            sample_id = row["sample_id"]
            if sample_id in seen or sample_id not in self.completed_events:
                raise ValueError("Checkpoint duplicate or missing event")
            seen.add(sample_id)
            completed = self.completed_events[sample_id]
            event = DecisionEvent.from_dict(completed["event"])
            if (event.sample_id != sample_id or event.horizon_minutes != self.horizon_minutes
                    or _digest(event.to_dict()) != completed["event_sha256"]
                    or not previous < event.decision_time <= self.clock
                    or row["decision_time"] != event.decision_time.isoformat()):
                raise ValueError("Checkpoint event identity or order mismatch")
            previous = event.decision_time
            counts = release_counts.pop(previous.isoformat(), [0, 0, expected_n])
            expected_n = counts[2]
            if row["released"] != counts[0] or row["updated"] != counts[1]:
                raise ValueError("Checkpoint event release/update counts disagree with feedback audit")
            _hash_value(row["model_version"], "event model version")
            versions.add(row["model_version"])
            i = completed["prediction_index"]
            if i is not None:
                if type(i) is not int or i != prediction_count or i >= len(self.predictions):
                    raise ValueError("Checkpoint prediction order mismatch")
                pred = self.predictions[i]
                if any(pred[k] != v for k, v in event.to_dict().items()) or pred["model_version"] != row["model_version"]:
                    raise ValueError("Checkpoint original prediction identity/version mismatch")
                if not np.isfinite(pred["prediction"]) or row["status"] != "predicted":
                    raise ValueError("Checkpoint prediction is invalid")
                if (pred["released_this_event"] != counts[0] or pred["updated_this_event"] != counts[1]
                        or pred["training_samples"] != expected_n):
                    raise ValueError("Checkpoint forecast budget disagrees with feedback audit")
                prediction_count += 1
            elif row["status"] != "invalid_features":
                raise ValueError("Checkpoint missing prediction lacks exclusion reason")
        if prediction_count != len(self.predictions) or (self.events and previous != self.clock):
            raise ValueError("Checkpoint prediction count or final clock mismatch")
        if release_counts:
            raise ValueError("Checkpoint feedback release lacks a completed decision event")
        if self.events and self.events[-1]["model_version"] != self.model.model_version:
            raise ValueError("Checkpoint final model version mismatch")
        if not self.events and self.initial_model_version != self.model.model_version:
            raise ValueError("Initial checkpoint model version mismatch")
        prediction_versions = {row["sample_id"]: row["model_version"] for row in self.predictions}
        for row in [*self.updates, *pending]:
            version = row["original_model_version"]
            if row["sample_id"] in self.carry_ids:
                if version != CARRY_VERSION:
                    raise ValueError("Carry-in cannot invent a prior forecast version")
            elif version != prediction_versions.get(row["sample_id"]):
                raise ValueError("Feedback must retain that sample's exact original model version")
        if type(self.peak_queue_size) is not int or self.peak_queue_size < self.queue.pending_count():
            raise ValueError("Checkpoint peak queue count is invalid")
        self._validate_history_integrity()

    def _validate_record_schemas(self):
        """Reject missing/extra audit fields before using their values."""
        groups = ((self.predictions, PREDICTION_RECORD_SCHEMA, "prediction"),
                  (self.updates, UPDATE_RECORD_SCHEMA, "update"),
                  (self.events, EVENT_RECORD_SCHEMA, "event"),
                  (self.queue.state_dict()["payload"]["pending"], PENDING_RECORD_SCHEMA, "pending"))
        for rows, schema, name in groups:
            if not isinstance(rows, list) or any(not isinstance(r, dict) or set(r) != schema for r in rows):
                raise ValueError(f"Invalid checkpoint {name} record schema")
        if (not isinstance(self.completed_events, dict)
                or any(not isinstance(r, dict) or set(r) != COMPLETED_RECORD_SCHEMA for r in self.completed_events.values())):
            raise ValueError("Invalid checkpoint completed-event record schema")
        for rows, names in ((self.updates, ("n_before", "n_after", "horizon_minutes")),
                            (self.events, ("released", "updated")),
                            (self.predictions, ("training_samples", "released_this_event", "updated_this_event"))):
            if any(type(row[name]) is not int or row[name] < 0 for row in rows for name in names):
                raise ValueError("Checkpoint audit counters must be nonnegative integers")

    def _validate_history_integrity(self):
        """Validate structural version steps, not unavailable historical solves."""
        pending = self.queue.state_dict()["payload"]["pending"]
        prediction_map = {row["sample_id"]: row for row in self.predictions}
        if len(prediction_map) != len(self.predictions):
            raise ValueError("Checkpoint prediction identities are duplicated")
        if set(prediction_map) | set(self.carry_ids) != {r["sample_id"] for r in pending + self.updates}:
            raise ValueError("Checkpoint prediction feedback partition is incomplete")
        running, solved_versions = self.initial_model_version, {self.initial_model_version}
        for event in self.events:
            version = event["model_version"]
            if event["updated"] > 0:
                if version in solved_versions:
                    raise ValueError("Checkpoint event version failed to advance after valid updates")
                solved_versions.add(version)
            elif version != running:
                raise ValueError("Checkpoint event version changed without valid updates")
            running = version
            pred = prediction_map.get(event["sample_id"])
            if pred is not None and (pred["model_version"] != running or running not in solved_versions):
                raise ValueError("Checkpoint prediction version is outside its event solve history")
        for row in pending + self.updates:
            sid = row["sample_id"]
            if sid in prediction_map:
                pred = prediction_map[sid]
                keys = ("sample_id", "feature_reference", "decision_time", "label_available_time", "horizon_minutes")
                if any(row[key] != pred[key] for key in keys):
                    raise ValueError("Checkpoint feedback metadata differs from original prediction")
            if "carry_in" in row and (type(row["carry_in"]) is not bool or row["carry_in"] != (sid in self.carry_ids)):
                raise ValueError("Checkpoint feedback carry-in flag disagrees with identity")

    def save_checkpoint(self, path: str | Path) -> dict:
        self._ready()
        tick = perf_counter()
        target = _checkpoint_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        state = self.state_dict()
        payload = _canonical(state) + b"\n"
        # Publish only a complete fsynced file. A hard link is an atomic
        # no-overwrite directory operation on both NTFS and POSIX filesystems.
        # Interrupted writes leave the uniquely named partial for failure audit.
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.partial")
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, target)
        temporary.unlink()
        elapsed = perf_counter() - tick
        self.runtime["checkpoint_save_seconds"] += elapsed
        return {"path": target.relative_to(data.REPOSITORY_ROOT).as_posix(), "sha256": hashlib.sha256(payload).hexdigest(),
                "state_sha256": state["sha256"], "bytes": len(payload), "seconds": elapsed,
                "events": len(self.events), "horizon_minutes": self.horizon_minutes}

    def save_checkpoint_v2(self, core_path: str | Path, log_path: str | Path) -> dict:
        """Write a new immutable separated state-core and audit-log pair."""
        from quantforge.online.checkpoint_v2 import save_v2
        return save_v2(self, core_path, log_path)

    @classmethod
    def load_checkpoint(cls, path: str | Path, *, source_sha256: str, config_sha256: str,
                        horizon_minutes: int) -> "ReplayEngine":
        tick = perf_counter()
        target = _checkpoint_path(path)
        # Published v2 pairs carry a sibling index. Verify its file SHA before
        # parsing the core; the legacy path below remains unchanged.
        audit = _checkpoint_path(target.parent / "checkpoint-audit.json")
        if target.name != "summary.json" and audit.exists():
            from quantforge.online.checkpoint_v2 import load_v2, indexed_core
            try:
                selected = indexed_core(audit)
            except (OSError, ValueError, TypeError):
                if target == audit:
                    raise
                # An unrelated malformed sidecar cannot change v1 behavior.
                # A v2 core below is still rejected unless its index validates.
                selected = None
            if target == audit or target == selected:
                engine = load_v2(selected, None, source_sha256, config_sha256, horizon_minutes)
                engine.runtime["checkpoint_load_seconds"] = perf_counter() - tick
                return engine
        if target.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("Replay checkpoint exceeds 32 MiB safety bound")
        try:
            state = json.loads(target.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Invalid checkpoint JSON") from exc
        if isinstance(state, dict) and state.get("schema_version") == 2:
            from quantforge.online.checkpoint_v2 import load_v2
            engine = load_v2(target, None, source_sha256, config_sha256, horizon_minutes)
            engine.runtime["checkpoint_load_seconds"] = perf_counter() - tick
            return engine
        engine = cls.from_state_dict(state, source_sha256=source_sha256, config_sha256=config_sha256,
                                     horizon_minutes=horizon_minutes)
        engine.runtime["checkpoint_load_seconds"] = perf_counter() - tick
        return engine


def _checkpoint_path(path: str | Path) -> Path:
    """Reject other-phase/holdout/external paths before stat or file open."""
    root = data.REPOSITORY_ROOT.resolve()
    supplied = Path(path)
    lexical = supplied if supplied.is_absolute() else root / supplied
    allowed = root / "results" / "online-foundation"
    if ".." in lexical.parts or not lexical.is_relative_to(allowed) or lexical.suffix != ".json":
        raise ValueError("Checkpoint path must be JSON under results/online-foundation")
    if lexical.resolve() != lexical or not lexical.resolve().is_relative_to(root):
        raise ValueError("Checkpoint symlink/path escape is prohibited")
    return lexical
