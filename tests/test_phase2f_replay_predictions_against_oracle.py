"""Every synthetic decision is checked against independent source-row refits."""
import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from quantforge.labels import mature_labels
from quantforge.online.batch_oracle import derive_mature_sets
from quantforge.online.foundation import audit_checkpoint_v2
from quantforge.online.hardening_config import graded_limits, load_hardening_config
from tests.phase2f_helpers import noisy_replay, oracle_metadata, sample_ids


@pytest.mark.parametrize("horizon,invalid_label", [(15, False), (5, False), (15, True)])
def test_each_prediction_matches_independent_mature_row_svd(noisy_minute_bars, horizon, invalid_label):
    config = load_hardening_config()
    with threadpool_limits(limits=config["budget"]["numeric_threads"]):
        engine, fx, ly, events, features, labels, initial, _ = noisy_replay(noisy_minute_bars, horizon)
        if invalid_label:
            first = features.metadata.index[features.metadata.valid_features & labels.frame.label_available_time.eq(events[0].decision_time)][0]
            assert first not in initial, "Invalid-label negative control must be immature at initialization"
            labels.frame.loc[first, "valid_label"] = False
            labels.frame.loc[first, "target_return"] = np.nan
        feature_metadata, label_metadata = oracle_metadata(features, labels, horizon)
        initial_ids = sample_ids(initial, horizon)
        source_index = {f"{horizon}:{value.isoformat()}": value for value in features.frame.index}
        fixed_scaler = StandardScaler().fit(features.frame.loc[initial])
        for event in events:
            _, _, valid_ids, _ = derive_mature_sets(feature_metadata, label_metadata, event.decision_time, initial_ids)
            train_ids = sorted(initial_ids | valid_ids)
            indices = pd.DatetimeIndex([source_index[value] for value in train_ids]).sort_values()
            train_x = fixed_scaler.transform(features.frame.loc[indices])
            train_y = mature_labels(labels.frame, event.decision_time, indices)
            reference = Ridge(alpha=1.0, fit_intercept=True, solver="svd").fit(train_x, train_y.to_numpy())
            probe = fixed_scaler.transform(features.frame.loc[[pd.Timestamp(event.feature_reference)]])
            predicted = engine.step(event, fx, ly)
            expected = float(reference.predict(probe)[0])
            centered = train_x - train_x.mean(axis=0)
            condition = float(np.linalg.cond(centered.T @ centered + np.eye(train_x.shape[1])))
            limits = graded_limits(condition, reference.coef_, config=config)
            relative_error = abs(predicted["prediction"] - expected) / max(abs(expected), limits["norm_denominator_floor"])
            assert relative_error <= limits["prediction_relative_l2"], "Per-event prediction differs from independent maturity SVD oracle"
            assert predicted["training_samples"] == len(indices), "Per-event model uses incorrect mature training membership"
            result = audit_checkpoint_v2(engine, feature_metadata, label_metadata, event.decision_time, initial_ids)
            assert result["valid_update_count"] == len(valid_ids)
            if invalid_label:
                assert result["released_count"] == result["valid_update_count"] + 1
