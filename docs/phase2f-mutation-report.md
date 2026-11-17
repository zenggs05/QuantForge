# Phase 2F mutation regression

All ten fixed survivors were killed by their intended semantic assertion. No environment-only failure counted. The final vector took **103.82 seconds**; an earlier development vector took 101.45 seconds. Combined 205.26 seconds is below the 1800-second mutation budget. The final source byte hashes match the implementation worktree.

| Mutant | Semantic invariant | Killer test / assertion keyword | Three controls |
| --- | --- | --- | --- |
| f1 | Released identity cannot be re-registered under a new reference | `test_f1_released_identity_cannot_be_reused_with_new_reference` / `F1 released identity` | pass / semantic fail / pass |
| f4 | Consumed IDs equal update audit IDs | `test_f4_consumed_ids_must_match_audit` / `F4 consumed feedback IDs` | pass / semantic fail / pass |
| r3 | Released label intervals equal queued metadata | `test_r3_released_target_metadata_must_match_pending_record` / `R3 label metadata gate` | pass / semantic fail / pass |
| c4 | Sub-ULP increments survive accumulation | `test_c4_kahan_must_recover_sub_ulp_increments` / `C4 compensated sum` | pass / semantic fail / pass |
| c5 | Compensation sign agrees with fsum | `test_c5_compensation_sign_preserves_small_increments` / `C5 compensation sign` | pass / semantic fail / pass |
| c6 | Centered condition above 1e12 is rejected | `test_c6_condition_gate_rejects_above_frozen_limit` / `C6 condition gate` | pass / semantic fail / pass |
| i9 | Legacy coefficient tolerance stays frozen | `test_i9_legacy_coefficient_tolerance_stays_frozen` / `I9 legacy equivalence` | pass / semantic fail / pass |
| i15 | Coefficient audit uses independent SVD fit | `test_i15_batch_coefficient_error_uses_independent_fit` / `I15 independent batch coefficient` | pass / semantic fail / pass |
| a8 | Distinct events strictly advance the clock | `test_a8_distinct_event_cannot_share_current_clock` / `A8 distinct event` | pass / semantic fail / pass |
| h3 | Normal interval uses 95 percent coverage | `test_h3_normal_interval_has_95_percent_coverage_constant` / `H3 normal interval coverage` | pass / semantic fail / pass |

Each baseline used unmodified Phase 2F source. Each mutant ran in a unique copy beneath the authorized local `qf-2f-mutants` temporary subtree, with the imported package path checked against that copy. Restored-source control reused the same copy. Every mutant returned pytest exit 1 with its listed AssertionError; both controls returned exit 0. Exact failure lines, source hashes, stdout hashes and timing are in [mutation-matrix.json](phase2f-evidence/mutation-matrix.json); full logs remain in `.tmp/phase2f-mutation-final`.

The f1 fixture changes both reference and later time while retaining the consumed identity, so alias and maturity guards cannot hide the missing ID check. f4 calls restored-state validation after injecting an unlogged consumed identity. r3 keeps a label internally mature but mismatched to queued metadata. i15 examines the measured coefficient difference itself: a prediction-error failure alone would not prove that coefficient self-comparison was removed. a8 uses a distinct identity at the existing clock and requires the clock-specific refusal. These are isolated semantic contracts, not source-text equality assertions.

No mutation touched the working source, frozen evidence or any other worktree. Temporary copies include the h3 diagnostic mutation solely for the authorized regression check.
