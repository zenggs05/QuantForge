"""A noisy synthetic target makes the update ordering negative control strong."""
import numpy as np
import pytest
from threadpoolctl import threadpool_limits

from quantforge.online.hardening_config import load_hardening_config
from tests.phase2f_helpers import noisy_replay, stale_differences


@pytest.mark.parametrize("horizon", [15, 5])
def test_each_horizon_has_large_correct_minus_stale_margin(noisy_minute_bars, horizon):
    config = load_hardening_config()
    with threadpool_limits(limits=config["budget"]["numeric_threads"]):
        engine, features, labels, events, *_ = noisy_replay(noisy_minute_bars, horizon)
        differences = stale_differences(engine, features, labels, events)
    assert np.max(differences) >= config["synthetic"]["stale_margin"], "Update-before-predict stale margin below frozen limit"
    assert len(differences) == config["synthetic"]["replay_events"]
    assert all(row["updated_this_event"] > 0 for row in engine.predictions)


def test_noisy_fixture_initial_and_full_ols_gates_and_ohlc_ranges(noisy_minute_bars):
    frame = noisy_minute_bars()
    config = load_hardening_config()
    assert frame.attrs["phase2f_sanity"]["initial_train"]["r2"] < config["synthetic"]["r2_max"]
    assert frame.attrs["phase2f_sanity"]["all_aligned"]["r2"] < config["synthetic"]["r2_max"]
    assert (frame.high >= frame[["open", "close"]].max(axis=1)).all()
    assert (frame.low <= frame[["open", "close"]].min(axis=1)).all()


def test_fixture_rejects_unfrozen_noise_parameters(noisy_minute_bars):
    with pytest.raises(ValueError, match="frozen"):
        noisy_minute_bars(sigma_noise=0.002)
