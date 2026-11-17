# Holdout-B confirmatory test: report

Protocol: `docs/confirmatory-protocol-holdout-b.md`, SHA-256 `6843156fdcd455956a3dc9cbc27cb5c213cf22b2fa7932ca49e6c51e13e2f315`, frozen in commit `f1f55d2` before any Holdout-B byte was accessed. Authorization: decision D-063 (owner approval given in reply to a request that quoted the protocol hash). Execution: decision D-064 (second attempt after a result-blind crash, see below). Evidence: `docs/holdout-b-evidence/`.

## Result

The pre-registered primary test does **not** reject the null hypothesis. This is a complete negative result for the confirmatory replication and is reported as such; no secondary or descriptive result changes it.

| Primary: v1, 15 min, within-month information coefficient | IC | s.e. | t | one-sided p | n |
| --- | --- | --- | --- | --- | --- |
| September 2026 (from 2 September) | 0.0246 | 0.0259 | 0.95 | 0.171 | 41,684 |
| October 2026 | 0.0185 | 0.0198 | 0.93 | 0.175 | 44,625 |
| Pooled (n-weighted) | 0.0215 | 0.0162 | 1.33 | 0.092 | 86,309 |

Secondary family (Holm, descriptive): S1 Clark-West vs zero p = 0.093; S2 within-month demeaned Clark-West p = 0.093; S3 Clark-West vs historical mean p = 0.098; S4 Diebold-Mariano (two-sided) p = 0.914; S5 5-minute IC p = 0.032 (Holm p = 0.159). No secondary test rejects after adjustment.

Pre-specified descriptive outputs:
- Specification v2: IC 0.0214 (t = 1.25).
- Out-of-sample R squared against zero: 0.012% (95% interval -0.21% to 0.23%).
- Drift term `2 mean(f) mean(y)`: -1.3e-9 (September) and +1.1e-9 (October), small relative to the mean Clark-West differential (4.5e-9 and 5.2e-9).
- Median |forecast| 0.37 bp; the always-trade non-overlapping rule breaks even at 0.29 bp per side; the rules with thresholds of 2 and 5 bp trade in 1.1% and 0.1% of periods and have negative break-even fees.
- Holdout A and Holdout B pooled information coefficient (descriptive only, not a test): 0.028, t = 2.95.

The point estimate (0.0215) is close to the historical estimates (0.0235 for 2021-2025; 0.0198 for 2022-2025). The protocol's ex-ante power was about 0.60 at the 2022-2025 effect size, so a non-rejection was a likely outcome even if the effect is real at that size.

## Data

Binance Spot BTCUSDT 1m monthly archives for 2026-09 and 2026-10: 4 GETs, all HTTP 200, no retries; both ZIPs match the official CHECKSUM files. The 1,440 rows of 2026-09-01 in the September archive were discarded at parsing, before any computation. No gaps, duplicates, close-time anomalies or undefined features in either month. Training used the development months (Sept 2025 - June 2026, read-only, hash-verified) and Holdout A (hash-verified against its download record); training samples 525,524 (fit 2026-09-02) and 567,209 (fit 2026-10-01).

## Execution and the D-064 incident

1. HB1 `ecbff80` (runner and synthetic tests), download, result-blind audit, HB2 `183b8df`.
2. The first formal run stopped with an assertion in the pooling step before writing any output: the runner labelled each fold by the month of its first evaluation bar, and October's first evaluation bar opens at 2026-09-30 23:59 (decision time 2026-10-01 00:00:01). No statistic was written or displayed. The stop file and the attempt-1 launch ledger are kept in `docs/holdout-b-evidence/`.
3. Under D-064 (owner approval) the one-line fix labels folds by fit-time month, as the frozen Holdout-A runner does (HB1b `2de4e49`, with a regression test). A second attempt re-audited the data; the re-audit equals the HB2 audit in every field except the recorded hash of the execution code, which necessarily changed (HB2b `6357320`).
4. Formal and reproduction runs at HB2b: six Ridge fits each; all nine deterministic artifacts are byte-identical (`determinism-check.json`). Predictions file SHA-256 `19f3093caff36d0630fa9330f58c9b86ba7bda24b46c2c03a0e292bf9db20af7` (not committed).

Deviations: the authorization text did not itself repeat the protocol hash (D-063); the first formal run crashed and was repeated under an owner decision (D-064); the re-audit was compared field by field except for the code hash rather than byte for byte. In the main worktree, 40 setup errors and 2 failures of the Holdout-A synthetic tests arise because those tests treat the main worktree path as forbidden real data; the same tests pass in the Codex worktree and in the public snapshot.

## Exposures

The months had passed when the protocol was frozen (sealed, not future). The 2026-09-01 smoke file fixes the starting price level (the primary statistic is drift-robust for this reason). The historical, Holdout A and exploratory results were known when the protocol was written. No daily-archive cross-check was made for September and October 2026.
