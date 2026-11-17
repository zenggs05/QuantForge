# Phase 2D-C1 real pilot and resource audit

Checked 2026-11-03. A one-day historical engineering replay completed for both horizons. It used existing verified Binance Spot BTCUSDT 1m development files; no market download, July/August holdout access, orders or PnL. Mathematical results are in the [equivalence report](phase2d-c1-equivalence-report.md).

## Provenance and bounded run

Formal run: `results/online-foundation/20261103T045308206581Z-5632721d/`.
Unchanged reproduction: `results/online-foundation/20261103T045523927874Z-0b7d1d55/`.
Both recorded implementation commit `9d35923625b0760b9b00d5fae861ef567ffb41b3`, `git_dirty=false`, the same source/config/plan hashes and dependency versions. The plan was committed before algorithm implementation.

The existing loader revalidates the authorized ten-month development source (436,320 bars); offline feature/label preparation may see those authorized historical rows. The online engine obtains current features through an availability gate and targets only through the mature-label accessor. Future target values **and future target-validity flags** are absent from pending queue entries and do not select current forecasts. This is an audited historical simulation, not physical segregation from an adversarial process.

Fit time is `2026-03-01T00:00:00Z`. Pilot decisions run exactly from `00:00:01` through `23:59:01` UTC on March 1. Initial labels are mature by fit time, with separate fixed initial scalers for 15m and 5m. No end-of-day flushing occurs; the final unresolved labels stay pending. The original frozen reference summary/prediction hashes were verified unchanged before each run and during final auditing.

## Events, carry-in and leakage checks

Within each event: receive available features; release metadata whose label availability is at or before the current time; retrieve and validate the mature labels; accumulate each valid label once; solve once if any valid updates occurred; forecast with that solved version; persist the forecast and register its metadata. The priority queue orders ties by `(label_available_time, decision_time, sample_id)`. Backward time, duplicate identities and incompatible versions are refused. Missing/invalid labels are rejected only when they mature, never fabricated.

February carry-in entries have already available features but labels immature at the March fit. They use the explicit original-version sentinel `carry_in:no_prediction`: C1 does not fabricate pre-March predictions. They may update the model only after actual maturity. Pending records contain no target value or future-validity flag.

| Count | 15m primary | 5m auxiliary |
| --- | ---: | ---: |
| Initial mature training observations | 260,564 | 260,574 |
| February carry-in registered | 15 | 5 |
| March 1 predictions | 1,440 | 1,440 |
| Mature valid updates | 1,440 | 1,440 |
| Within-pilot feedback updates | 1,425 | 1,435 |
| End-of-pilot training observations | 262,004 | 262,014 |
| End pending / peak pending queue size | 15 / 15 | 5 / 5 |
| Formal solves, including initialization | 1,441 | 1,441 |
| Formal invalid labels / invalid features | 0 / 0 | 0 / 0 |
| Formal rejected queue operations / duplicate updates | 0 / 0 | 0 / 0 |
| Feature accessor reads, including carry-in | 1,455 | 1,445 |
| Label accessor reads | 1,440 | 1,440 |

Independent read-only auditing checked all **2,880 predictions and 2,880 updates**: unique IDs, exact minute grids, release time no earlier than maturity, correct carry-in sentinel counts and an exact original-forecast version match for every within-pilot update. Tests reject a target read one second before maturity, accept the exact boundary, and verify future-target/feature perturbations cannot change past forecasts. No premature read or duplicate consumption was found in the tested paths. Tests/audits are evidence for this implementation and pilot, not a universal proof against all possible future callers; direct use of the numerical Ridge class must still respect the queue/accessor contract.

## Checkpoints, interruption and restart

Ten versioned checkpoints were saved after 0/360/720/1080/1440 events per horizon. They include fixed scaler, sufficient statistics and compensation, solved model version, source/config bindings, logical time, pending/released/consumed IDs and original audit history. JSON uses schema and SHA-256 checks plus semantic validation; no pickle. Atomic publication uses a flushed/fsynced unique partial file and exclusive hard-link creation. Existing final paths cannot be overwritten. A failed event poisons the engine; recovery starts from a complete validated checkpoint. Failure retains diagnostic information instead of producing a success summary.

For each horizon, the main trace ran uninterrupted. The separate recovery branch loaded event720, deliberately replayed the completed event once (unchanged deterministic state), and then continued the remaining720 events. Both branches exactly reproduced final numerical state, queue, predictions, updates and versions. Two duplicate attempts produced **zero** extra updates. Recovery's additional **1,440 updates and 1,440 solves** are separately charged validation work. Checkpoint loads performed four extra numerical validation eigendecompositions in total.

Corrupt JSON/checksums, unknown/inconsistent schema, model versions, source/config/horizon bindings, forged counters and path escape are covered by rejection tests. Checksums detect accidental corruption; they are not authentication against someone able to rewrite every field and hash. Full-file semantic validation and retained history grow with replay length; this is not a durable, distributed live-trading store.

## Measured computation

Formal-run wall times below are seconds. Model-internal timers separate initialization/statistics/solve/predict; engine wrapper times are available in [budget.json](phase2d-c1-evidence/budget.json) and must not be added to the same model costs twice.

| Stage | 15m | 5m | Combined |
| --- | ---: | ---: | ---: |
| Initial scaler + sufficient statistics, excluding solve | 0.081066 | 0.058854 | 0.139921 |
| Initial solve only (included in cumulative solves below) | 0.000433 | 0.000294 | 0.000727 |
| Formal incremental statistics updates | 0.066989 | 0.066801 | 0.133790 |
| Formal solves, including initial solve | 0.228250 | 0.219823 | 0.448073 |
| Formal model predictions | 0.023063 | 0.022511 | 0.045574 |
| Feature accessor time | 0.260436 | 0.255545 | 0.515981 |
| Label accessor time | 1.897486 | 1.853421 | 3.750908 |
| Formal event loop including audit/checkpoints | 4.082132 | 4.006440 | 8.088572 |
| Recovery checkpoint load (outer timer) | 0.195756 | 0.204681 | 0.400437 |
| Recovery continuation, 720 events each | 1.517825 | 1.528509 | 3.046333 |

Other formal measurements: frozen-reference validation **0.031903 s**, verified development loading **2.797194 s**, feature building **0.293659 s**, all10 batch fits **1.269488 s**, all10 checkpoint saves **0.323933 s**. Individual batch fit times ranged **0.121923–0.134891 s**, with counts and times retained in [batch-timings.csv](phase2d-c1-evidence/batch-timings.csv). Total recorded runtime was **17.085246 s**, versus **17.886687 s** for reproduction. The total timer ends immediately before the final success-summary write; no precise whole-process exit-time claim is made. Nested stages overlap; their values are not an additive partition of the total.

| Work category, per run | Count |
| --- | ---: |
| Initial incremental fits | 2 |
| Formal valid incremental updates | 2,880 |
| Formal coefficient solves | 2,882 |
| Formal condition eigendecompositions | 2,882 |
| Formal Cholesky factorizations | 2,882 |
| Formal training row visits (initial + feedback) | 524,018 |
| Same-scaler batch audit fits (SVD) | 10 |
| Batch audit training row visits | 2,612,890 |
| Recovery extra updates / solves / training visits | 1,440 / 1,440 / 1,440 |
| Recovery extra eigendecompositions / Cholesky factors | 1,440 / 1,440 |
| Additional load-validation eigendecompositions | 4 |
| Deliberate duplicate replay attempts / resulting updates | 2 / 0 |

Training visits count rows processed by fitting/statistics, not literal CPU array reads, validation passes, feature scans or byte traffic. The 10 sparse batch oracle fits and 2,880 incremental updates are different schedules. Their measured cost comparison is descriptive, not evidence for an equal-schedule speedup. The second entire run repeats every budget item above and is reported separately; it is not free compute. Across formal plus reproduction there were 20 batch audit fits, 5,760 formal updates and 2,880 additional recovery updates.

## Memory and hardware

Windows `GetProcessMemoryInfo.PeakWorkingSetSize` recorded formal lifetime peak **670,986,240 bytes (639.90 MiB)**; reproduction **711,929,856 bytes (678.95 MiB)**. This includes offline market loading, feature/label tables, sklearn audit fits, replay histories and checkpoint processing. It is not the learner's memory alone and is not a Python-allocation proxy.

Each final numeric model reports 4,048 bytes for its primary NumPy arrays; this excludes Python object overhead and stored solved-system lists. Complete serialized model states are **16,845 bytes (15m)** and **16,763 bytes (5m)**. Queue released/consumed IDs, feature cache and original audit history grow with the number of events; full checkpoints and the entire engine are **not** O(p²) memory merely because the sufficient statistics are. Long-duration study needs a separately reviewed retention/persistence strategy or an explicitly bounded resource budget.

Host: Windows11 build26200, Intel Core Ultra9 275HX,24 cores/24 logical processors, Python3.13.5. Numeric work used CPU float64, with all three detected numerical thread pools fixed to one thread. GPU detection found NVIDIA GeForce RTX5070 Laptop GPU,8151MiB,driver592.01; `nvidia-smi` reports CUDA13.1 driver compatibility, while `nvcc` was not on PATH. No toolkit installation claim is made. GPU was unused; no dependency, driver, CUDA or global configuration change was needed.

## Reproduction and conclusion

All **16 deterministic artifacts** match byte for byte: predictions, updates, batch comparisons, initial comparisons, final state, resolved configuration and10 checkpoints. All20 artifact hashes per run were independently revalidated. Operation counts, dependencies, code hashes, authorized source hashes and mature-index hashes also match. [The reproduction record](phase2d-c1-reproducibility.json) lists all66 observed summary-field differences; all are run identity, timing, measured memory or hashes of explicitly nondeterministic reports. No unexpected difference remains. Exact equality is an observation on this environment, not a portability requirement.

The foundation passes its numerical, temporal and bounded resource-audit acceptance criteria. The one-day interval was already observed development data. No C1 predictive metric, significance test, trading-cost result or alpha claim is made. A C2 protocol must distinguish fixed-scaler update-frequency effects from monthly scaler refitting, freeze comparison schedules and compute accounting, retain delayed-label guards, and keep the final holdout locked. It has not been launched here.
