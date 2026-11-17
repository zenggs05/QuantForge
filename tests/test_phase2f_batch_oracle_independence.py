"""Independent dual-channel maturity audits, including rejected feedback."""
import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from quantforge.online.batch_oracle import derive_mature_sets, OracleIndependenceError
from quantforge.online.foundation import audit_checkpoint_v2


def metadata_case():
    ids = ["initial", "valid", "invalid", "future", "bad_features"]
    available = pd.Timestamp("2026-03-01T00:00:01Z")
    as_of = available + pd.Timedelta(minutes=15)
    features = pd.DataFrame({"feature_available_time": [available] * len(ids),
                             "valid_features": [True, True, True, True, False]}, index=ids)
    labels = pd.DataFrame({"label_available_time": [as_of, as_of, as_of, as_of + pd.Timedelta(minutes=1), as_of],
                           "valid_label": [True, True, False, True, True],
                           "target_return": [0., 1., np.nan, 99., 5.]}, index=ids)
    return features, labels, as_of, frozenset({"initial"})


def fake_engine(released, updated):
    return SimpleNamespace(queue=SimpleNamespace(released_ids=frozenset(released)),
                           updates=[{"sample_id": value, "status": "updated"} for value in updated])


def test_oracle_separates_valid_invalid_future_and_invalid_feature_ids():
    features, labels, as_of, initial = metadata_case()
    released, released_sha, updated, updated_sha = derive_mature_sets(features, labels, as_of, initial)
    assert released == frozenset({"valid", "invalid"})
    assert updated == frozenset({"valid"})
    assert released_sha == hashlib.sha256(json.dumps(["invalid", "valid"]).encode()).hexdigest()
    assert updated_sha == hashlib.sha256(json.dumps(["valid"]).encode()).hexdigest()


@pytest.mark.parametrize("released,updated,expected", [
    ({"invalid"}, set(), "released_ids sha mismatch"),
    ({"invalid", "valid", "future"}, {"valid", "future"}, "released_ids sha mismatch"),
    ({"invalid", "valid"}, {"invalid", "valid"}, "valid update sha mismatch"),
    ({"valid"}, {"valid"}, "released_ids sha mismatch"),
])
def test_oracle_rejects_late_future_false_valid_and_missing_invalid(released, updated, expected):
    args = metadata_case()
    with pytest.raises(OracleIndependenceError, match=f"^{expected}$"):
        audit_checkpoint_v2(fake_engine(released, updated), *args)


def test_correct_mixed_release_audit_passes_without_counting_invalid_as_update():
    result = audit_checkpoint_v2(fake_engine({"invalid", "valid"}, {"valid"}), *metadata_case())
    assert result["passed"] and result["released_count"] == 2 and result["valid_update_count"] == 1


@pytest.mark.parametrize("initial,error", [(set(["initial"]), TypeError), (frozenset(), ValueError)])
def test_initial_ids_are_explicit_nonempty_frozenset(initial, error):
    features, labels, as_of, _ = metadata_case()
    with pytest.raises(error, match="initial_sample_ids"):
        derive_mature_sets(features, labels, as_of, initial)


def test_target_payload_and_future_validity_do_not_change_mature_identity_sets():
    features, labels, as_of, initial = metadata_case()
    expected = derive_mature_sets(features, labels, as_of, initial)
    labels["target_return"] = [object() for _ in range(len(labels))]
    labels.loc["future", "valid_label"] = False
    assert derive_mature_sets(features, labels, as_of, initial) == expected


@pytest.mark.parametrize("case", ["before_first_maturity", "only_initial", "invalid_features_only"])
def test_empty_mature_sets_are_valid_and_do_not_read_unavailable_validity(case):
    features, labels, as_of, initial = metadata_case()
    if case == "before_first_maturity":
        as_of -= pd.Timedelta(seconds=1)
    elif case == "only_initial":
        features, labels = features.loc[["initial"]], labels.loc[["initial"]]
    else:
        features.loc[["valid", "invalid"], "valid_features"] = False
    assert derive_mature_sets(features, labels, as_of, initial) == (
        frozenset(), hashlib.sha256(b"[]").hexdigest(),
        frozenset(), hashlib.sha256(b"[]").hexdigest())


def test_exact_maturity_boundary_releases_and_one_second_earlier_does_not():
    features, labels, as_of, initial = metadata_case()
    assert derive_mature_sets(features, labels, as_of - pd.Timedelta(seconds=1), initial)[0] == frozenset()
    assert derive_mature_sets(features, labels, as_of, initial)[0] == frozenset({"valid", "invalid"})


def test_sample_ids_reject_nonstring_before_sorting():
    features, labels, as_of, initial = metadata_case()
    features.index = pd.Index([1, *list(features.index[1:])], dtype=object)
    with pytest.raises(ValueError, match="nonempty trimmed strings"):
        derive_mature_sets(features, labels, as_of, initial)


@pytest.mark.parametrize("damage", ["duplicate", "missing_id", "unknown_initial", "feature_time", "naive_time"])
def test_inconsistent_independent_metadata_rejected(damage):
    features, labels, as_of, initial = metadata_case()
    if damage == "duplicate":
        features = pd.concat([features, features.iloc[[0]]])
    elif damage == "missing_id":
        labels = labels.drop(index="valid")
    elif damage == "unknown_initial":
        initial = frozenset({"unknown"})
    elif damage == "feature_time":
        features.loc["valid", "feature_available_time"] = as_of + pd.Timedelta(minutes=1)
    else:
        as_of = "2026-03-01 00:15:01"
    with pytest.raises(ValueError, match="unique|align|availability|UTC|timezone|aware"):
        derive_mature_sets(features, labels, as_of, initial)


@pytest.mark.parametrize("module_name", ["batch_oracle.py", "qr_reference.py"])
def test_independent_reference_ast_has_no_sufficient_statistics_access(module_name):
    path = Path(__file__).resolve().parents[1] / "src/quantforge/online" / module_name
    tree = ast.parse(path.read_text(encoding="utf-8"))
    banned = {"_sums", "_sum_z", "_sum_y", "_sum_zz", "_sum_zy", "_compensation"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in banned, f"Independent reference reads forbidden attribute {node.attr}"
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            assert node.slice.value not in banned, f"Independent reference reads forbidden key {node.slice.value}"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                assert node.args[1].value not in banned, "Independent reference reads forbidden dynamic attribute"
    if module_name == "batch_oracle.py":
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                assert node.id not in {"engine", "IncrementalRidge"}, "Metadata oracle must not read an engine"
