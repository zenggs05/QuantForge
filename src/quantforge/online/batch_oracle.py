"""Independent maturity identities from caller-owned observation metadata.

NO ACCESS TO SUFFICIENT STATISTICS.
The input universe comprises initial training samples and the subsequently
observed carry-in/replay stream. The caller supplies original observation
validity; it must not substitute revised or engine-derived membership.
"""
from __future__ import annotations

import hashlib
import json

import pandas as pd

from quantforge.temporal import utc_timestamp


class OracleIndependenceError(ValueError):
    """An independently derived maturity identity set disagrees with the audit."""


def sample_ids_sha256(sample_ids) -> str:
    """Hash sorted string IDs with the exact D-026 JSON representation."""
    values = list(sample_ids)
    if any(not isinstance(value, str) or not value or value != value.strip() for value in values):
        raise ValueError("Oracle sample IDs must be nonempty trimmed strings")
    return hashlib.sha256(json.dumps(sorted(values)).encode("utf-8")).hexdigest()


def _metadata(frame, required, name):
    if not isinstance(frame, pd.DataFrame) or not required.issubset(frame.columns):
        raise ValueError(f"Oracle {name} metadata is missing required columns")
    if not frame.columns.is_unique or not frame.index.is_unique:
        raise ValueError(f"Oracle {name} metadata requires unique columns and sample IDs")
    sample_ids_sha256(frame.index)


def derive_mature_sets(features_frame, labels_frame, as_of,
                       initial_sample_ids: frozenset[str]) -> tuple[frozenset[str], str, frozenset[str], str]:
    """Return all released IDs/hash and valid update IDs/hash independently.

    Both frames use aligned canonical string sample-ID indices. Features must
    include original ``valid_features`` and ``feature_available_time`` metadata;
    labels must include ``label_available_time`` and ``valid_label``. Extra
    feature or target columns are not inspected. Label validity is read only
    after the maturity and feature-observation gates have selected eligible IDs.
    """
    if not isinstance(initial_sample_ids, frozenset):
        raise TypeError("initial_sample_ids must be a frozenset")
    if not initial_sample_ids:
        raise ValueError("initial_sample_ids must be nonempty")
    sample_ids_sha256(initial_sample_ids)
    _metadata(features_frame, {"valid_features", "feature_available_time"}, "feature")
    _metadata(labels_frame, {"valid_label", "label_available_time"}, "label")
    feature_ids, label_ids = set(features_frame.index), set(labels_frame.index)
    if feature_ids != label_ids or not initial_sample_ids.issubset(feature_ids):
        raise ValueError("Oracle metadata IDs and initial sample IDs must align")
    now = utc_timestamp(as_of, "oracle as_of")
    maturity = labels_frame["label_available_time"].map(lambda value: utc_timestamp(value, "label_available_time"))
    matured = labels_frame.index[maturity.le(now)]
    observed = features_frame.loc[matured, ["valid_features", "feature_available_time"]]
    if observed.empty:
        empty = frozenset()
        return empty, sample_ids_sha256(empty), empty, sample_ids_sha256(empty)
    validity = observed["valid_features"]
    if not pd.api.types.is_bool_dtype(validity) or validity.isna().any():
        raise ValueError("Oracle valid_features must contain nonmissing booleans")
    availability = observed["feature_available_time"].map(lambda value: utc_timestamp(value, "feature_available_time"))
    if (availability > maturity.loc[matured]).any():
        raise ValueError("Oracle feature availability must not follow label maturity")
    eligible = observed.index[validity & availability.le(now)]
    released = frozenset(eligible).difference(initial_sample_ids)
    if not released:
        return released, sample_ids_sha256(released), released, sample_ids_sha256(released)
    valid_labels = labels_frame.loc[sorted(released), "valid_label"]
    if not pd.api.types.is_bool_dtype(valid_labels) or valid_labels.isna().any():
        raise ValueError("Oracle matured valid_label must contain nonmissing booleans")
    updated = frozenset(valid_labels.index[valid_labels])
    return released, sample_ids_sha256(released), updated, sample_ids_sha256(updated)
