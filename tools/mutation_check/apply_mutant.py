"""Apply one exact historical mutation to an explicitly supplied source copy."""
from __future__ import annotations

import argparse
from pathlib import Path


MUTANTS = {
    'f1': ('online/feedback_queue.py',
        'sample.sample_id in self._pending or sample.sample_id in self._released',
        'sample.sample_id in self._pending', 'test_f1_released_identity_cannot_be_reused_with_new_reference', 'F1 released identity'),
    'f4': ('online/replay.py',
        'if len(set(update_ids)) != len(update_ids) or set(update_ids) != self.queue.released_ids:',
        'if False:', 'test_f4_consumed_ids_must_match_audit', 'F4 consumed feedback IDs'),
    'r3': ('online/replay.py',
        'if (utc_timestamp(actual.label_available_time) != maturity\n'
        '                or utc_timestamp(actual.target_start_time) != sample.target_start_time\n'
        '                or utc_timestamp(actual.target_end_time) != sample.target_end_time):',
        'if False:', 'test_r3_released_target_metadata_must_match_pending_record', 'R3 label metadata gate'),
    'c4': ('online/incremental_ridge.py',
        'corrected = increment - compensation\n        updated = total + corrected\n'
        '        residual = (updated - total) - corrected\n        return updated, residual',
        'return total + increment, np.zeros_like(compensation)',
        'test_c4_kahan_must_recover_sub_ulp_increments', 'C4 compensated sum'),
    'c5': ('online/incremental_ridge.py',
        'residual = (updated - total) - corrected', 'residual = corrected - (updated - total)',
        'test_c5_compensation_sign_preserves_small_increments', 'C5 compensation sign'),
    'c6': ('online/incremental_ridge.py', 'MAX_CONDITION = 1e12', 'MAX_CONDITION = 1e16',
        'test_c6_condition_gate_rejects_above_frozen_limit', 'C6 condition gate'),
    'i9': ('online/foundation.py',
        'dc <= 1e-9 + 1e-8*np.abs(batch_coef)).all() and di <= 1e-10 + 1e-8*abs(batch_intercept)',
        'dc <= 1e-5 + 1e-4*np.abs(batch_coef)).all() and di <= 1e-6 + 1e-4*abs(batch_intercept)',
        'test_i9_legacy_coefficient_tolerance_stays_frozen', 'I9 legacy equivalence'),
    'i15': ('online/foundation.py',
        'predicted, batch.coef_, batch.intercept_, reference)',
        'predicted, model.coef_, batch.intercept_, reference)',
        'test_i15_batch_coefficient_error_uses_independent_fit', 'I15 independent batch coefficient'),
    'a8': ('online/replay.py', 'if now <= self.clock:', 'if now < self.clock:',
        'test_a8_distinct_event_cannot_share_current_clock', 'A8 distinct event'),
    'h3': ('diagnostics/hac_tests.py', 'Z975 = 1.95996398454', 'Z975 = 1.645',
        'test_h3_normal_interval_has_95_percent_coverage_constant', 'H3 normal interval coverage'),
}


def apply_mutant(name: str, source: bytes) -> bytes:
    """Preserve original encoding/newlines and require exactly one known match."""
    if name not in MUTANTS:
        raise ValueError('Unknown mutation name')
    _, before, after, _, _ = MUTANTS[name]
    newline = b'\r\n' if b'\r\n' in source else b'\n'
    old = before.encode().replace(b'\n', newline)
    new = after.encode().replace(b'\n', newline)
    if source.count(old) != 1:
        raise ValueError(f'{name}: source match is not unique: {source.count(old)}')
    result = source.replace(old, new, 1)
    compile(result, f'<synthetic-mutant-{name}>', 'exec')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name', choices=sorted(MUTANTS))
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve():
        raise ValueError('Mutation output must be a separate copy')
    with args.output.open('xb') as handle:
        handle.write(apply_mutant(args.name, args.source.read_bytes()))


if __name__ == '__main__':
    main()
