"""Synthetic-only shared inputs for the Phase 2F engineering tests."""
import numpy as np
import pandas as pd

from quantforge.features import build_features
from quantforge.labels import build_labels, mature_labels
from quantforge.online.hardening_config import load_hardening_config
from quantforge.online.incremental_ridge import IncrementalRidge
from quantforge.online.replay import ReplayEngine, GuardedFeatureAccessor, MatureLabelAccessor, event_from_metadata


def sample_ids(index, horizon):
    return frozenset(f"{horizon}:{pd.Timestamp(value).isoformat()}" for value in index)


def oracle_metadata(features, labels, horizon):
    """Convert immutable source metadata to IDs without consulting an engine."""
    feature_frame = features.frame.join(features.metadata[["valid_features", "feature_available_time"]]).copy()
    label_frame = labels.frame.copy()
    feature_frame.index = pd.Index([f"{horizon}:{value.isoformat()}" for value in feature_frame.index])
    label_frame.index = pd.Index([f"{horizon}:{value.isoformat()}" for value in label_frame.index])
    return feature_frame, label_frame


def noisy_replay(factory, horizon):
    config = load_hardening_config()
    settings = config["synthetic"]
    bars = factory(settings["periods"], settings["start"])
    features, labels = build_features(bars), build_labels(bars, horizon)
    fit_time = pd.Timestamp(settings["fit_time"])
    metadata = features.metadata
    eligible = metadata.valid_features & labels.frame.valid_label & labels.frame.label_available_time.le(fit_time)
    initial = metadata.index[eligible]
    model = IncrementalRidge().initialize_from_batch(features.frame.loc[initial], mature_labels(labels.frame, fit_time, initial))
    engine = ReplayEngine(model, horizon, fit_time, "a" * 64, "b" * 64)
    feature_accessor = GuardedFeatureAccessor(features.frame, metadata)
    label_accessor = MatureLabelAccessor(labels.frame)
    carry = metadata.loc[metadata.valid_features & metadata.decision_time.lt(fit_time)
                         & labels.frame.label_available_time.gt(fit_time)]
    for _, row in carry.iterrows():
        engine.register_carry_in(event_from_metadata(row, horizon), feature_accessor)
    events = [event_from_metadata(row, horizon) for _, row in metadata.loc[metadata.decision_time.ge(fit_time)].iterrows()]
    return engine, feature_accessor, label_accessor, events[:settings["replay_events"]], features, labels, initial, bars.attrs


def stale_differences(engine, feature_accessor, label_accessor, events):
    """Keep pre-update coefficients as the deliberately stale negative control."""
    differences = []
    for event in events:
        x = feature_accessor(event, event.decision_time)
        stale = float(engine.model.predict(x.reshape(1, -1))[0])
        actual = engine.step(event, feature_accessor, label_accessor)
        differences.append(abs(actual["prediction"] - stale))
    return np.asarray(differences)
