"""Metadata-only, deterministic delayed-feedback queues.

This module never stores, calculates or retrieves a target value. A release is
permission to resolve a label through a separate maturity-checked provider.
Replay must commit the queue and learner together at completed event boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
import heapq
import json
from typing import Mapping

import pandas as pd

from quantforge.temporal import utc_timestamp

STATE_SCHEMA_VERSION = 1
CARRY_IN_MODEL_VERSION = "carry_in:no_prediction"
_TIME_FIELDS = (
    "decision_time", "feature_available_time", "target_start_time",
    "target_end_time", "label_available_time",
)


class FeedbackQueueError(ValueError):
    """Feedback metadata, event order or restored state violates the contract."""


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise FeedbackQueueError(f"{name} must be a nonempty whitespace-trimmed string")
    return value


def _horizon(value: object) -> int:
    if type(value) is not int or value not in (5, 15):
        raise FeedbackQueueError("Feedback horizon must be integer 5 or 15")
    return value


def _digest(payload: dict) -> str:
    try:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FeedbackQueueError("Queue state must contain finite JSON-compatible metadata") from exc
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class FeedbackSample:
    """Only identifiers and information timestamps, never label values.

    ``feature_reference`` is a logical immutable identifier, not permission to
    read a file. The replay engine owns and validates the feature lookup.
    Carry-in samples use ``carry_in:no_prediction`` because no pre-fit forecast
    is invented. All ordinary forecasts retain their actual model version.
    """

    sample_id: str
    decision_time: pd.Timestamp
    feature_available_time: pd.Timestamp
    target_start_time: pd.Timestamp
    target_end_time: pd.Timestamp
    label_available_time: pd.Timestamp
    feature_reference: str
    horizon_minutes: int
    original_model_version: str

    def __post_init__(self) -> None:
        for name in ("sample_id", "feature_reference", "original_model_version"):
            _text(getattr(self, name), name)
        _horizon(self.horizon_minutes)
        for name in _TIME_FIELDS:
            # Heap keys use signed UTC nanoseconds; reject unsupported ranges
            # during validation rather than after mutating queue state.
            object.__setattr__(self, name, utc_timestamp(getattr(self, name), name).as_unit("ns"))
        one_second = pd.Timedelta(seconds=1)
        if (self.decision_time != self.feature_available_time
                or self.decision_time != self.target_start_time + one_second
                or self.target_end_time != self.target_start_time + pd.Timedelta(minutes=self.horizon_minutes)
                or self.label_available_time != self.target_end_time + one_second):
            raise FeedbackQueueError("Feedback temporal equations must retain the frozen one-second delay and horizon")
        if self.target_start_time != self.target_start_time.floor("min"):
            raise FeedbackQueueError("Feedback target starts must be UTC minute boundaries")

    @property
    def feature_ref(self) -> str:
        """Readable alias for the logical feature reference."""
        return self.feature_reference

    def to_dict(self) -> dict:
        return {field.name: (getattr(self, field.name).isoformat() if field.name in _TIME_FIELDS
                            else getattr(self, field.name)) for field in fields(self)}

    @classmethod
    def from_dict(cls, record: Mapping) -> FeedbackSample:
        expected = {field.name for field in fields(cls)}
        if not isinstance(record, Mapping) or set(record) != expected:
            raise FeedbackQueueError("Feedback schema is metadata-only; missing or extra fields (including targets) are forbidden")
        return cls(**dict(record))


class DelayedFeedbackQueue:
    """One horizon's deterministic heap with durable consumed identities.

    Heap order is (label_available_time, decision_time, sample_id), independent
    of registration order. Each feature reference may name only one label in a
    given horizon, including after that label is released. Separate instances
    represent the separate 5m and 15m label streams.
    """

    def __init__(self, horizon_minutes: int):
        self.horizon_minutes = _horizon(horizon_minutes)
        self._clock: pd.Timestamp | None = None
        self._heap: list[tuple[int, int, str]] = []
        self._pending: dict[str, FeedbackSample] = {}
        self._released: set[str] = set()
        self._registered_refs: dict[str, str] = {}
        self._rejected_count = 0

    @property
    def clock(self) -> pd.Timestamp | None:
        return self._clock

    @property
    def released_ids(self) -> frozenset[str]:
        return frozenset(self._released)

    @property
    def registered_count(self) -> int:
        return len(self._registered_refs)

    @property
    def rejected_count(self) -> int:
        return self._rejected_count

    @staticmethod
    def _key(sample: FeedbackSample) -> tuple[int, int, str]:
        return sample.label_available_time.value, sample.decision_time.value, sample.sample_id

    def _as_of(self, as_of: object) -> pd.Timestamp:
        instant = utc_timestamp(as_of, "as_of").as_unit("ns")
        if self._clock is not None and instant < self._clock:
            raise FeedbackQueueError("Feedback event clock cannot move backward")
        return instant

    def register_sample(self, sample: FeedbackSample | Mapping, as_of: object | None = None) -> None:
        """Register known features and immature metadata without resolving y.

        Historical carry-in may be registered at the initialization clock. A
        normal replay explicitly calls ``release_matured(t)`` before registering
        its newly emitted prediction at t; already due events cannot be skipped.
        """
        try:
            if isinstance(sample, Mapping):
                sample = FeedbackSample.from_dict(sample)
            if not isinstance(sample, FeedbackSample):
                raise FeedbackQueueError("Register a FeedbackSample or its exact metadata mapping")
            # Revalidate frozen objects in case low-level mutation bypassed the dataclass.
            sample = FeedbackSample.from_dict(sample.to_dict())
            instant = self._as_of(sample.decision_time if as_of is None else as_of)
            if sample.horizon_minutes != self.horizon_minutes:
                raise FeedbackQueueError("A feedback queue cannot mix independent horizons")
            if sample.sample_id in self._pending or sample.sample_id in self._released:
                raise FeedbackQueueError("Duplicate feedback sample_id; released labels cannot be registered again")
            if sample.feature_reference in self._registered_refs:
                raise FeedbackQueueError("Duplicate label feature reference under an alias sample_id")
            if sample.decision_time > instant or sample.feature_available_time > instant:
                raise FeedbackQueueError("Future or unavailable features cannot be registered")
            if sample.label_available_time <= instant:
                raise FeedbackQueueError("Registration requires an immature label; release precedes prediction")
            if self._heap and self._heap[0][0] <= instant.value:
                raise FeedbackQueueError("Release already matured feedback before registering the next prediction")
        except (ValueError, TypeError, OverflowError):
            self._rejected_count += 1
            raise
        self._clock = instant
        self._pending[sample.sample_id] = sample
        self._registered_refs[sample.feature_reference] = sample.sample_id
        heapq.heappush(self._heap, self._key(sample))

    def release_matured(self, as_of: object) -> list[FeedbackSample]:
        """Release each due metadata record exactly once, including equality.

        No market/label lookup occurs here. If later label resolution discovers
        a gap or invalid target, replay records that rejection; the consumed
        queue identity still prevents a duplicate attempt and fabricated update.
        """
        try:
            instant = self._as_of(as_of)
        except (ValueError, TypeError, OverflowError):
            self._rejected_count += 1
            raise
        released = []
        while self._heap and self._heap[0][0] <= instant.value:
            _, _, sample_id = heapq.heappop(self._heap)
            sample = self._pending.pop(sample_id)
            self._released.add(sample_id)
            released.append(sample)
        self._clock = instant
        return released

    def pending_count(self) -> int:
        return len(self._pending)

    def released_count(self) -> int:
        return len(self._released)

    def diagnostics(self) -> dict:
        return {"horizon_minutes": self.horizon_minutes, "registered_count": self.registered_count,
                "pending_count": self.pending_count(), "released_count": self.released_count(),
                "rejected_count": self.rejected_count,
                "clock": None if self._clock is None else self._clock.isoformat(),
                "target_values_stored": False}

    def state_dict(self) -> dict:
        """Return a detached, versioned, checksummed JSON metadata state."""
        payload = {
            "horizon_minutes": self.horizon_minutes,
            "clock": None if self._clock is None else self._clock.isoformat(),
            "pending": [self._pending[key[2]].to_dict() for key in sorted(self._heap)],
            "released_ids": sorted(self._released),
            "registered_refs": dict(sorted(self._registered_refs.items())),
            "registered_count": self.registered_count, "rejected_count": self.rejected_count,
        }
        return {"schema_version": STATE_SCHEMA_VERSION, "payload": payload, "sha256": _digest(payload)}

    def load_state_dict(self, state: Mapping) -> DelayedFeedbackQueue:
        """Validate every invariant before atomically replacing this instance."""
        if not isinstance(state, Mapping) or set(state) != {"schema_version", "payload", "sha256"}:
            raise FeedbackQueueError("Queue state envelope schema mismatch")
        if type(state["schema_version"]) is not int or state["schema_version"] != STATE_SCHEMA_VERSION:
            raise FeedbackQueueError("Unsupported feedback queue state version")
        payload = state["payload"]
        expected = {"horizon_minutes", "clock", "pending", "released_ids", "registered_refs",
                    "registered_count", "rejected_count"}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise FeedbackQueueError("Queue payload schema mismatch; target values forbidden")
        if not isinstance(state["sha256"], str) or state["sha256"] != _digest(payload):
            raise FeedbackQueueError("Feedback queue state checksum mismatch")
        if _horizon(payload["horizon_minutes"]) != self.horizon_minutes:
            raise FeedbackQueueError("Cannot restore a different horizon into this queue")
        for name in ("registered_count", "rejected_count"):
            if type(payload[name]) is not int or payload[name] < 0:
                raise FeedbackQueueError("Queue counters must be nonnegative integers")
        clock = None if payload["clock"] is None else utc_timestamp(payload["clock"], "clock").as_unit("ns")
        if not isinstance(payload["pending"], list) or not isinstance(payload["released_ids"], list):
            raise FeedbackQueueError("Queue pending and released identities must be lists")
        pending = [FeedbackSample.from_dict(record) for record in payload["pending"]]
        released = [_text(value, "released_id") for value in payload["released_ids"]]
        refs = payload["registered_refs"]
        if not isinstance(refs, dict):
            raise FeedbackQueueError("Registered feature references must be a mapping")
        for ref, sample_id in refs.items():
            _text(ref, "feature_reference")
            _text(sample_id, "registered sample_id")
        ids = [sample.sample_id for sample in pending]
        all_ids = ids + released
        if (len(set(all_ids)) != len(all_ids) or len(set(refs.values())) != len(refs)
                or set(refs.values()) != set(all_ids) or payload["registered_count"] != len(all_ids)):
            raise FeedbackQueueError("Queue restored identity/counter partition is inconsistent")
        for sample in pending:
            if (sample.horizon_minutes != self.horizon_minutes or refs.get(sample.feature_reference) != sample.sample_id
                    or clock is None or sample.decision_time > clock or sample.label_available_time <= clock):
                raise FeedbackQueueError("Pending queue item violates horizon/reference/clock/maturity invariants")
        if released and clock is None:
            raise FeedbackQueueError("Released identities require an event clock")
        if pending != sorted(pending, key=self._key) or released != sorted(released):
            raise FeedbackQueueError("Queue state must preserve canonical deterministic identity order")
        self._clock = clock
        self._pending = {sample.sample_id: sample for sample in pending}
        self._heap = [self._key(sample) for sample in pending]
        heapq.heapify(self._heap)
        self._released = set(released)
        self._registered_refs = dict(refs)
        self._rejected_count = payload["rejected_count"]
        return self
