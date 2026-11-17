# Phase 2F: C1 engine hardening

Frozen before implementation under owner Instruction v2 with D-025 and D-026 supplements, from `9894ca25fe30812944e5a933fa5878315d876dd0`. Engineering validation only; synthetic observations cannot establish prediction information, generalization or tradability. Phase 2D-C1 mathematical behavior, default serialization and all 794 existing tests remain binding.

## Scope and immutable evidence

Work only in `D:/zgs_project/QuantForge-2f`, branch `phase2f-engine-hardening`. Use the D-022 preinstalled independent environment. No dependency installation, uv sync, Git configuration change, market request, account access, orders, PnL, model research, real-data execution, push, remote or PR. All old plans, configs, reports, evidence, seven confirmatory prediction source fingerprints, research state files, CLAUDE.md, Phase 2E/3A exclusive code and data loaders remain immutable. No Holdout-A/B or September smoke data or metadata access. No scripts/run_*.py, scripts/download_*.py or scripts/validate_*.py execution. New validation tooling resides in tools/ and runs synthetic inputs only.

The only cross-worktree content read is optional WP7(a), owned exclusively by its default-skipped test. WP0/WP8 additionally permit filesystem metadata-only environment isolation snapshots; no external environment content is read or written. Shared Git metadata is accessed only by the authorized local Git operations. Test output stays within .tmp/pytest; mutation copies use the explicitly authorized system temporary subtree.

## WP0: environment and baseline

Require clean worktree, authorized HEAD, successful `git merge --ff-only main`, D-024/D-025/D-026 records, and 37 unchanged original .gitattributes lines. Verify the local Python executable/import path and pandas 3.0.6, numpy 2.5.4, scipy 1.18.1, pyarrow 23.0.*, sklearn 1.9.*, matplotlib 3.11.*. Preserve the completed baseline: `python -B -m pytest -q -p no:cacheprovider`, 794 passed in 295.40 s; changes from that tested tree to this freeze base affect only two research state documents. No unnecessary repetition of unchanged tests. Record uv version only; never synchronize packages. Snapshot main/3A venv file counts, sizes and nanosecond mtimes for comparison, without content reads.

## WP1: separated checkpoint v2

Add audit_log.write_record(log_path,payload), read_records(log_path,start,end), verify_chain(log_path). JSONL envelopes contain exactly prev_hash, this_hash, payload. Hash SHA-256 over the ASCII previous 64-hex digest concatenated with canonical UTF-8 payload JSON (sorted keys, compact separators, ensure_ascii=False, allow_nan=False). Genesis is 64 zeroes; read ranges are zero-based and end-exclusive. Reject malformed, truncated and inconsistent records.

Add checkpoint_v2.save_v2(engine,core_path,log_path) and load_v2(core_path,log_path,source_sha256,config_sha256,horizon_minutes). Core is bounded to 33554432 bytes, containing fixed scaler, sufficient statistics, Kahan terms, solved system, n/initial_n, pending-only feature cache and queue, carry IDs, peak queue size, clocks, bindings and audit head/count/full-file SHA. All prediction/update/event/completed-event history goes into the linked log; reconstruct released IDs and registered references from pending and audited history. Preserve original v1 state SHA as an exact reconstruction check.

Each checkpoint is a new immutable pair with sibling checkpoint-audit.json and summary.json. Publication refuses overwrites. The independent log writer supports append-only writes; once a pair is published, its full-file SHA anchors that snapshot and later append makes that pair invalid. New checkpoints use new directories/logs. This avoids silently changing full-file SHA into prefix SHA; it duplicates historical log storage. Loading reconstructs full audit history in memory. The guarantee removes the single-file 32 MiB history limit, not all memory or disk growth. No constant-memory claim.

Default ReplayEngine.save_checkpoint(path) remains v1 with identical bytes. Add save_checkpoint_v2(core_path,log_path). load_checkpoint identifies v1 versus v2 core/index and dispatches without changing the default v1 save path.

SHA-256 hash chain with per-event link provides integrity verification against
accidental corruption. It provides tamper evidence ONLY IF the chain head hash
has an external anchor, typically the project owner's Git commit of
checkpoint-audit.json and summary.json. Without this external anchor, a party
with write access can recompute the chain and produce an undetectable forgery.
This project does not claim cryptographic authenticity or tamper resistance.

This mandatory denial is not a positive security claim; documentation must contain no absolute integrity guarantee.

## WP2: restoration integrity

Append validation without removing existing checks. Strict predefined key schemas cover pending/update/prediction/event records. Require prediction IDs union carry IDs equals pending IDs union all consumed IDs, including invalid_label. Original feedback versions and metadata must match emitted forecasts. Preserve temporal order; do not sort corrupt histories into validity.

D-026 event chain: start at initial_model_version; each event with updated>0 must advance to a different version, each event with updated=0 must retain the running version. Number of version steps equals number of update events. Each prediction matches its own event version, including intervening invalid_features events which update without predicting. Final version agrees with the restored solved model. Old v1 has no raw mature values or historical solved coefficient arrays, so this is structural chain validation, not independent recomputation of every historical model digest.

v2 verifies checkpoint-audit full-file SHA/size before parsing state/log, then the entire hash chain and declared count/head. Tests cover at least ten separately re-signed structural failures: missing pending, extra update, wrong prediction version, orphan carry, invalid cache shape/value, reversed event clock, broken event version chain, broken log link, count truncation and external file SHA disagreement. A finite same-shape vector with all hashes consistently rewritten is not universally detectable without an independent anchor. Test exact ValueError messages for the specific broken invariant. Add dedicated invalid-feature update/version-chain controls.

## WP3: independent dual-set oracle

derive_mature_sets(features_frame,labels_frame,as_of,initial_sample_ids:frozenset[str]) returns (all_released_ids,all_sha,valid_update_ids,valid_sha). Use caller-supplied aligned canonical string IDs and independent temporal metadata. Reject empty/non-frozenset initial IDs and inconsistent metadata. First gate label maturity and valid observed features at their availability; subtract initial IDs. Only then inspect validity for the valid-update subset. Do not access engine state or sufficient statistics. Hash each sorted ID list with UTF-8 json.dumps, as specified by D-026.

Append foundation.audit_checkpoint_v2: compare all-release hash with queue.released_ids, then valid-update hash with update records of status updated. Distinct error messages: released_ids sha mismatch / valid update sha mismatch. Existing _batch_check and equivalence_metrics stay byte-for-byte unchanged. New-path startup reads hashed configuration and checks frozen legacy constants against it.

BP6 controls: late valid release; future release; invalid_label incorrectly counted as updated; omitted invalid release; and correct mixed valid/invalid history. For an omitted invalid release only the all-release hash differs; valid-update hash correctly remains unchanged. This follows the supplied two-set algorithm despite the supplement's incidental 'both hashes' wording.

## WP4: numerical reference and graded acceptance

OnlineQRReference takes immutable initial mean/scale and raw rows only. Maintain (p+1)-square upper-triangular R and Qt_y with Givens updates; expose update_from_raw, update_batch_from_raw, solve_ridge(alpha=1), state_bytes. Ridge penalty excludes intercept. Condition diagnostic must be for centered predictor A=Zc.T@Zc+alpha I, not the uncentered augmented intercept system. Derive centered QR via independent small QR transformations, never incremental sufficient statistics.

IncrementalRidge.solve(cross_check=False) preserves all old default behavior and state bytes. Explicit attach_cross_check_data(X,y) supplies a complete independent raw audit dataset; no implicit raw history retention occurs on the default path. solve(True) requires compatible complete raw rows and fixed scaler, then compares Cholesky, independent sklearn Ridge(solver=svd), and raw-row QR. Diagnostics are external to v1 state. New errors raise NumericalCrossCheckError; ill-conditioned objectives raise NumericalStabilityError. Independent work and raw-data audit memory are charged separately.

All new parameters come from configs/c1-engine-hardening.toml. expected_floor=eps*cond(A)*norm_inf(coef_ref); coefficient relative limit=max(1e-9,10*expected_floor); prediction relative L2 limit=max(1e-8,10*eps*cond(A)). Relative coefficient means norm_inf(delta)/max(norm_inf(reference),1e-12); prediction denominator floor also 1e-12. Report condition, relative residual norm(Aw-b)/max(norm(b),floor), prediction differences, and all three pairwise coefficient differences. Do not replace frozen C1 tolerances or change its default solve.

Fixed seed 20261105, p=14, n0=100. Stress all k={10,100,1000,10000} and n1/n0={.01,.1,1,10}; two predeclared extreme near-collinear shifts 1e7/1e8 cover rejection. For measured cond(A)<=1e12 require graded equality; above require incremental and QR rejection. At least one rejection case must exist. Kahan: initial sum_y=1e10 from 100 initial rows, one million increments 1e-6, compare math.fsum. Compensated ULP<=max(10,p*eps*n_updates), plain sum at least 100 times worse (zero compensated ULP treated with a one-ULP comparison floor). No change to compensation algorithm or condition threshold.

## WP5: stronger update ordering

Append noisy_minute_bars without changing minute_bars. D-026 log random walk uses seed 20261105, noise SD .001 and signal .0003*sin(t/97). Fixed 430 bars from 2026-02-28T20:00Z; initial fit 2026-03-01T00:00Z; first 90 replay events separately for h15/h5. OHLC must bracket both open=previous close and current close; preserve RNG draw order and the mandated close process. Other bar constants are in the hashed config. Check initial legal training OLS R2<.5 and full aligned synthetic fixture R2<.5. Preliminary feasibility only: initial R2=.23974168, full R2=.40803949; no market inference. Per-horizon max correct-minus-stale prediction must exceed 1e-8. Independently derive mature sets and refit SVD at every decision; compare predictions with graded limits. Preserve no future validity in pending entries and update before prediction.

## WP6: mutation regression

Add tools/mutation_check/apply_mutant.py and run_kill_vector.py. Exact uniquely matched source mutations only, preserve all other source bytes, and verify imported source belongs to the temporary copy. Ten targets: f1 released-ID registration guard; f4 consumed-ID restore check; r3 label metadata gate; c4 plain instead of Kahan sum; c5 Kahan sign reversal; c6 condition 1e12 to 1e16; i9 legacy equivalence tolerance times 1e4; i15 batch coefficient self-comparison; a8 clock <= to <; h3 normal 95% critical value to 1.645. Mutations affect temporary copies only, including otherwise immutable historical diagnostic code.

For each killer require (a) unchanged local 2F source passes, (b) mutated copy fails for its declared semantic assertion, (c) restored same copy passes. No import/encoding/path/permission failure counts as a kill. All ten must be valid kills; any survivor or environment-only failure stops work. Record real output and elapsed budget. New semantic tests may explicitly isolate a legacy guard from redundant newly added validation; this is disclosed and must still exercise behavior, not merely grep source. Never claim a redundant full-engine survivor was killed by another unrelated check.

## WP7: backward compatibility

(a) One test skipped unless QF_C1_HISTORICAL_RESULTS equals the exact authorized main results/online-foundation root. Enumerate h15/h5 and event0/360/720/1080/1440 in run 20261103T045308206581Z-5632721d. Read checkpoints only, load, compare state_dict internal sha256 to the original internal sha256, serialize in v1 format to unique .tmp/pytest files, compare complete bytes, and confirm accessed original files' mtime unchanged. Internal canonical payload SHA and complete-file SHA are distinct hashes; do not confuse them. Research Lead may enable this optional integration test; default execution performs zero historical reads. No market bar file or prediction CSV access.

(b) Default synthetic tests compare identical inputs through v1/v2 save/load/continuation. Predictions, updates, events, versions, sample counts and final serialized payload match exactly. Test both horizons, carry-in and invalid feedback/features. Preserve default-v1 serialization and compare old source behavior where needed without any historical market input.

## WP8: evidence and execution

Commit A freezes this plan, config and manifest, appends only new .gitattributes lines and AGENTS authorization, and adds one README sentence. Manifest hashes plan/config and seven protected sources but never itself; record.md externally anchors manifest SHA. Add report/mutation-report/evidence skeletons with unexecuted status, never fabricated results.

After tested code and all required mutation checks, commit B. Run exactly one clean synthetic validation and one unchanged reproduction via tools/phase2f_validate.py; do not run any existing research CLI. Write distinct ignored output directories. Retain deterministic aggregate numeric/error/compatibility/chain/budget artifacts; separate elapsed times, memory, run path/time and commit identity. Compare deterministic bytes. Snapshot other environments again, with zero metadata change. Commit C records tested facts, failures, optional integration skip status, limitations, provenance, seeds and precise commands. Source changes after formal runs invalidate their final-code evidence and must be reported rather than silently repeated beyond budget.

## Acceptance anchors and stop rules

BP1(a) optional historical v1 read/write compatibility; BP1(b) required synthetic v1/v2 equivalence. BP2 ten valid mutation kills with three controls. BP3 graded/condition rejection branches. BP4 compensated ULP and plain-sum control. BP5 all 794 old tests retained and roughly 50 or more meaningful new synthetic tests. BP6 separate oracle channels and positive control. BP7 AST checks Attribute and literal-key Subscript access to prohibited sufficient statistics in both independent modules; comments/docstrings allowed. BP8 exact honest hash-chain paragraph without positive absolute claims. BP9 main/3A environment metadata unchanged. BP10 manifest plan/config hashes match, record contains manifest SHA, all seven prediction hashes match original protocol. Optional BP1(a) default skip is not claimed as historical proof.

Stop immediately and report failed acceptance, altered existing C1 behavior, mutated frozen evidence, invalid mutation controls, market/holdout access, uv sync, failed ff-only, missing default skip, or external-worktree writes. Do not loosen thresholds or swap seeds. Authorized D-026 sigma adjustments are unnecessary because the suggested fixed values passed feasibility; they are now frozen here. Implementation defects may be corrected within the fixed contract; scientifically impossible or scope-changing acceptance requires owner revision before resuming.

## Budget, tests and commits

CPU float64 and one numerical thread; no GPU. Each formal run <=1200 seconds and process peak <=4 GiB, one formal plus one reproduction; mutation suite <=1800 seconds. Synthetic development tests are separately measured, not formal runs. Count updates, solves, SVD oracle fits, QR row updates/solves, recovery and memory separately. All previous tests stay unchanged except append-only conftest additions. No real Git/network in new tests; tools orchestrating authorized commits/mutation processes are distinct from test bodies.

Three local commits: `docs: freeze phase2f engine hardening plan`; `feat: harden c1 engine (eng-1..eng-5): v2 checkpoint, restore validation, independent oracle, qr cross-check, mutation retest`; `docs: record phase2f engine hardening and mutation retest`. No remote publication.
