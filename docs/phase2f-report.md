# Phase 2F C1 engine hardening report

Completed synthetic implementation and two clean engineering runs on 2026-11-05. **929 passed, 1 optional historical integration skipped**: all 794 old tests plus 135 new passing tests. **10/10 valid mutation kills**. Implementation commit `c893d757d18fead4993f2883fa61039c3305b5e5` generated **16 byte-identical deterministic artifacts** in formal and reproduction runs. This is engineering evidence only; it does not establish alpha, prediction generalization, trading profit or execution quality.

## ENG-1: separated checkpoints

Default save remains v1. The explicit v2 API writes an immutable bounded state-core plus a linked JSONL event log, checkpoint-audit.json and summary.json. File SHA checks precede state/log parsing; all links, declared counts, reconstructed v1 SHA and restored-state invariants must agree. Existing v1 bytes and synthetic continuation are exact across both horizons.

A synthetic capacity test enlarged event identities without changing prices or features. The v1 file was **88,121,258 bytes**, and the original 32 MiB load gate refused it. The v2 log was **50,353,563 bytes**, while core remained **20,355 bytes**; restored state SHA and the next event matched exactly. This proves separation across the old size limit, not long-running market performance. Each checkpoint uses a fresh log; published snapshots are immutable. Loading reconstructs full history in memory, and audit storage still grows.

SHA-256 hash chain with per-event link provides integrity verification against
accidental corruption. It provides tamper evidence ONLY IF the chain head hash
has an external anchor, typically the project owner's Git commit of
checkpoint-audit.json and summary.json. Without this external anchor, a party
with write access can recompute the chain and produce an undetectable forgery.
This project does not claim cryptographic authenticity or tamper resistance.

Small formal checkpoint-audit and summary records are anchored in [checkpoint-anchors.json](phase2f-evidence/checkpoint-anchors.json), committed with this report. Raw synthetic histories remain ignored locally. Without an independently trusted copy of that commit, a writer can replace the complete chain and all anchors.

## ENG-2: restoration contracts

Strict schemas cover predictions, updates, events, completed events and pending records. Identity conservation includes rejected invalid-label feedback. Versions advance over the full event sequence, including invalid-feature events that update without predicting; predictions retain their owning event version. Specific re-signed corruption tests reject missing feedback, extra updates, orphan carry IDs, malformed cache, changed metadata, reversed clocks, broken versions, log link/count damage and file SHA mismatch. Original v1 lacks historical raw rows/solved coefficients, so structural validation does not independently reconstruct every historical model version or detect arbitrary consistent rewrites.

BP1(b) passed for initial/running/restored states and invalid features/labels. **BP1(a) was not enabled**: the sole historical-file test skipped because QF_C1_HISTORICAL_RESULTS was unset. No historical checkpoints or market rows were read. Research Lead can run that optional ten-checkpoint read/write-byte check before merge; this report does not claim its result.

## ENG-3: independent maturity oracle

The oracle independently derives all matured releases and valid updates from caller-owned metadata and explicit initial IDs. The audit compares two separate hashes. Tests cover late/future releases, missing invalid releases, invalid feedback falsely counted as updated, exact maturity, empty released sets and correct mixed histories. No engine state or sufficient statistics enters the oracle. Caller metadata must describe initial training plus the observed carry/replay stream; the API cannot invent unseen registration events.

Existing foundation._batch_check and equivalence_metrics remain unchanged. Opt-in startup verifies their numerical constants against hashed config. `numerical_cross_check_tolerance=1e-10` is a retained configuration field and is **not an additional acceptance gate**: D-016 graded limits below govern numerical cross-checks. `drift_warning_factor` does not enable a drift monitor. No prediction parameters were tuned.

## ENG-4: raw-row QR/SVD checks

Default solve retains no raw history and performs no extra reference fit. Explicit attach_cross_check_data supplies the complete caller-authorized raw audit rows; solve(cross_check=True) compares Cholesky, independent SVD and independent raw-row Givens QR. QR has fixed scaler and unpenalized intercept, and its condition diagnostic uses the centered predictor system. A failed audit preserves the prior solved parameters/version; actual diagnostic/factor work remains counted.

All sixteen frozen mean-shift cases passed. Both extreme near-collinear cases rejected: at about 1.075e15 the condition gate fires; at about 1.075e17 Cholesky's finite-positive-definite gate fires and QR's centered condition gate fires. No fallback fit, jitter, threshold widening or seed replacement occurred.

| Kind / shift SD / ratio | cond(A) | Relative residual | Max prediction relative L2 (3 pairs) | Max coefficient abs difference (3 pairs) | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| mean_shift / 10 / 0.01 | 4.71138326 | 1.04238e-16 | 2.94752e-15 | 4.21885e-15 | pass |
| mean_shift / 10 / 0.1 | 23.2363087 | 8.14948e-17 | 3.03605e-15 | 4.44089e-15 | pass |
| mean_shift / 10 / 1 | 51.2913682 | 1.64856e-16 | 3.71555e-15 | 7.54952e-15 | pass |
| mean_shift / 10 / 10 | 14.5187804 | 2.50228e-17 | 1.80085e-15 | 4.41314e-15 | pass |
| mean_shift / 100 / 0.01 | 216.714075 | 6.27031e-18 | 1.74623e-15 | 5.05151e-15 | pass |
| mean_shift / 100 / 0.1 | 1903.4582 | 1.24354e-16 | 9.20286e-16 | 5.32907e-15 | pass |
| mean_shift / 100 / 1 | 4615.74285 | 4.16572e-16 | 1.38222e-15 | 7.10543e-15 | pass |
| mean_shift / 100 / 10 | 1208.65175 | 4.41309e-19 | 3.86828e-15 | 2.81442e-14 | pass |
| mean_shift / 1000 / 0.01 | 21709.2881 | 1.44732e-16 | 7.07513e-16 | 1.45439e-14 | pass |
| mean_shift / 1000 / 0.1 | 188543.065 | 3.19386e-16 | 5.28112e-16 | 4.92384e-14 | pass |
| mean_shift / 1000 / 1 | 458342.812 | 1.27951e-16 | 2.64663e-15 | 7.77156e-15 | pass |
| mean_shift / 1000 / 10 | 120039.93 | 1.72359e-16 | 4.97528e-16 | 1.4122e-13 | pass |
| mean_shift / 10000 / 0.01 | 2173542.93 | 3.46014e-20 | 3.54879e-16 | 1.82132e-13 | pass |
| mean_shift / 10000 / 0.1 | 18838596.5 | 2.04469e-16 | 2.93332e-16 | 3.60822e-13 | pass |
| mean_shift / 10000 / 1 | 45803864.3 | 4.89377e-16 | 8.04288e-16 | 7.77156e-14 | pass |
| mean_shift / 10000 / 10 | 11997342.6 | 2.20701e-16 | 9.06167e-16 | 1.73395e-12 | pass |
| near_collinear / 1e+07 / 1 | 1.07522445e+15 | ? | ? | ? | both reject |
| near_collinear / 1e+08 / 1 | 1.07522445e+17 | ? | ? | ? | both reject |

The four required metrics above retain nulls for rejected solves. [numerical-stress.json](phase2f-evidence/numerical-stress.json) records all three pairwise coefficient absolute/relative differences, prediction differences, graded limits and actual operation counts without rounding. Coefficient relative error uses the infinity-norm ratio; expected_floor=eps*cond(A)*norm_inf(reference). The limit is max(1e-9,10*expected_floor); prediction relative L2 limit is max(1e-8,10*eps*cond(A)).

Kahan's one million scalar increments of 1e-6 onto 1e10 equal math.fsum exactly: **0 ULP**, versus **475,712 ULP** for plain addition. The ratio uses a one-ULP denominator floor when the compensated error is zero. A separate update_one/restart test confirms that the real model stores and restores compensation. The million-loop test counts scalar Kahan calls separately; it is not misreported as one million full 14-feature model updates.

## ENG-5: stronger ordering control

D-026 fixed log-random-walk parameters passed without adjustment. Initial training OLS R2=0.239741680183, full aligned synthetic R2=0.408039491492; both below .5. For each horizon, 90 decisions were compared with stale coefficients and an independently reconstructed mature-row SVD fit.

| Horizon | Initial rows | Correct minus stale max | Independent oracle max abs | Oracle max relative |
| --- | ---: | ---: | ---: | ---: |
| 15m | 164 | 0.000364651292013 | 8.67361737988e-18 | 1.88333349499e-15 |
| 5m | 174 | 0.00034296773117 | 8.67361737988e-18 | 8.82627045088e-15 |

The required stale margin is 1e-8 for each horizon. These are synthetic engineering errors, not forecast-performance statistics on market data.

## Mutation regression

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

See [the mutation report](phase2f-mutation-report.md) for control design and exact assertion evidence.

## Reproduction and budget

Formal: **5.376 s**, process peak **172.95 MiB**. Reproduction: **5.372 s**, peak **173.68 MiB**. Both use one numerical thread and the same clean implementation commit. Elapsed validation excludes Python imports and initial clean-source/config checks; process memory is the OS lifetime high-water measure. Both are below 1200 s / 4 GiB limits.

Each run includes 4,644 stress statistic updates, 18 initial stress solves, 18 attempted later solves (16 accepted), 16 stress SVD fits, 6,444 QR row updates and 18 QR solve attempts (16 accepted). Two synthetic replay horizons add 180 updates and 180 later solves plus two initial solves, 180 per-event SVD fits, 180 stale-control predictions and 180 ordinary predictions. Four recovery branches add 180 updates/solves/predictions and eight checkpoint-validation eigendecompositions. Kahan adds one initialization solve and 1,000,000 scalar primitive calls. [computation-budget.json](phase2f-evidence/computation-budget.json) separates those operations; no work is counted as a new predictor candidate.

Allowed nondeterminism is confined to elapsed/stage timing, process memory, run kind/path. All 16 deterministic file hashes agree. Main and Phase 3A environments each remain 10,052 files with **zero size/mtime changes**. All seven protected source fingerprints and old frozen evidence remain unchanged. No downloads, network requests, real-market rows, Holdout/smoke data, trading, push, remote, PR, global configuration changes or package synchronization occurred.

## Acceptance and remaining scope

BP1(b), BP2-BP10 pass for the implemented synthetic scope. BP1(a) is optional and **unexecuted**, not passed by inference. Research Lead's independent acceptance and optional historical compatibility check remain external review steps. No new research or longer replay is authorized by this delivery. Development deviations and non-final failures are retained in [the execution record](phase2f-record.md).
