"""Behavioral controls for the ten historical C1 mutation survivors."""
from dataclasses import replace
import math

import numpy as np
import pandas as pd

from quantforge.diagnostics.hac_tests import _stats
from quantforge.online import foundation
from quantforge.online.feedback_queue import DelayedFeedbackQueue
from quantforge.online.hardening_config import load_hardening_config
from quantforge.online.incremental_ridge import IncrementalRidge, NumericalStabilityError
from quantforge.online.replay import MatureLabelAccessor
from tests.test_feedback_queue import sample
from tests.test_online_replay import synthetic_replay


def test_f1_released_identity_cannot_be_reused_with_new_reference():
    queue = DelayedFeedbackQueue(5)
    first = sample('identity', horizon=5, feature_reference='first-reference')
    queue.register_sample(first)
    queue.release_matured(first.label_available_time)
    later = sample('identity', '2026-03-01T00:06:01Z', horizon=5,
                   feature_reference='new-reference')
    rejected = False
    try:
        queue.register_sample(later)
    except ValueError as exc:
        rejected = 'Duplicate feedback sample_id' in str(exc)
    assert rejected, 'F1 released identity was registered again under a new reference'


def test_f4_consumed_ids_must_match_audit(minute_bars):
    engine, fx, ly, events, *_ = synthetic_replay(minute_bars)
    engine.step(events[0], fx, ly)
    # Exercise this specific restored-state contract, independent of envelope SHA.
    engine.queue._released.add('synthetic:unrecorded-consumption')
    rejected = False
    try:
        engine._validate_restored_state()
    except ValueError as exc:
        rejected = 'Consumed feedback IDs' in str(exc)
    assert rejected, 'F4 consumed feedback IDs must match update audit'


def test_r3_released_target_metadata_must_match_pending_record(minute_bars):
    engine, _, _, events, _, labels, _ = synthetic_replay(minute_bars)
    event = events[0]
    feedback = event.feedback(engine.model.model_version)
    altered = labels.frame.copy()
    ref = pd.Timestamp(feedback.feature_reference)
    # Still internally mature and start<end; only the pending metadata disagrees.
    altered.loc[ref, 'target_start_time'] += pd.Timedelta(seconds=1)
    rejected = False
    try:
        MatureLabelAccessor(altered)(feedback, feedback.label_available_time)
    except ValueError as exc:
        rejected = 'metadata mismatch' in str(exc)
    assert rejected, 'R3 label metadata gate accepted a different target interval'


def _compensated_sum():
    cfg = load_hardening_config()['synthetic']
    total, compensation = cfg['kahan_initial_sum'], 0.0
    increment = cfg['kahan_increment']
    for _ in range(cfg['kahan_updates']):
        total, compensation = IncrementalRidge._kahan(total, compensation, increment)
    expected = math.fsum([cfg['kahan_initial_sum'],
                          math.fsum(increment for _ in range(cfg['kahan_updates']))])
    return abs(total - expected) / math.ulp(expected)


def test_c4_kahan_must_recover_sub_ulp_increments():
    ulps = _compensated_sum()
    assert ulps <= load_hardening_config()['acceptance_graded']['kahan_ulp_multiplier'], \
        f'C4 compensated sum lost sub-ULP increments: {ulps} ULP'


def test_c5_compensation_sign_preserves_small_increments():
    ulps = _compensated_sum()
    assert ulps <= load_hardening_config()['acceptance_graded']['kahan_ulp_multiplier'], \
        f'C5 compensation sign violates fsum reference: {ulps} ULP'


def test_c6_condition_gate_rejects_above_frozen_limit():
    cfg = load_hardening_config()
    large = cfg['stability']['condition_limit'] * cfg['stability']['drift_warning_factor']
    A = np.diag([1.0, large])
    rejected = False
    try:
        IncrementalRidge._spectrum(A, A - np.eye(2))
    except NumericalStabilityError:
        rejected = True
    assert rejected, 'C6 condition gate accepted a system above frozen 1e12'


def test_i9_legacy_coefficient_tolerance_stays_frozen():
    tol = load_hardening_config()['tolerance']
    result = foundation.equivalence_metrics(
        [2 * tol['coef_atol']], 0., [0.], [0.], 0., [0.])
    assert not result['passed'], 'I9 legacy equivalence accepted coefficient error beyond frozen tolerance'


def test_i15_batch_coefficient_error_uses_independent_fit(minute_bars):
    engine, _, _, _, features, labels, initial = synthetic_replay(minute_bars)
    engine.model._coef = engine.model.coef_ + 0.1
    row, _ = foundation._batch_check(engine.model, features.frame, labels.frame,
        initial, features.frame.loc[initial], engine.initial_fit_time, 15, 0)
    assert row['coefficient_max_abs_error'] > 0.09, \
        'I15 independent batch coefficient audit self-compared the altered model'


def test_a8_distinct_event_cannot_share_current_clock(minute_bars):
    engine, fx, ly, events, *_ = synthetic_replay(minute_bars)
    engine.step(events[0], fx, ly)
    duplicate_clock = replace(events[0], sample_id='synthetic:another-identity')
    rejected = False
    try:
        engine.step(duplicate_clock, fx, ly)
    except ValueError as exc:
        rejected = 'advance the logical clock' in str(exc)
    assert rejected, 'A8 distinct event at the current clock passed the clock gate'


def test_h3_normal_interval_has_95_percent_coverage_constant():
    from scipy.special import ndtr
    _, _, _, low, high, _ = _stats(0., 1., 'estimable', None)
    coverage = float(ndtr(high) - ndtr(low))
    assert abs(coverage - 0.95) < 1e-10, 'H3 normal interval coverage differs from 95 percent'
