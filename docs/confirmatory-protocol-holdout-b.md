# Confirmatory Protocol B - Holdout-B (frozen; not executed)

Frozen on 2026-11-17 (Asia/Shanghai) under decision D-061, before any byte of the Holdout-B period was downloaded, read, parsed or summarized. The SHA-256 of this file's raw LF bytes is the identifier the owner must cite in the written execution authorization. Execution without that authorization is forbidden.

## 1. Question and evidence status

The single confirmatory question is whether the frozen Phase 2B forecasting program (specification v1, 15-minute horizon) carries time-series predictive information about BTCUSDT returns on Holdout-B. The result concerns predictive information only. It is never evidence of lower forecast error, profitability, tradability or execution quality.

Relation to earlier evidence. The program was designed on September 2025 to June 2026. It was evaluated in a pre-registered historical external validation (January 2021 to August 2025; D-058) and in a confirmatory test on Holdout-A (July to August 2026; D-060). The choice of the primary statistic below was made after both of those results and the D-061 exploratory analyses were known. Holdout-B does not overlap any data used before, and no Holdout-B data have been accessed.

## 2. Data and time contract

- Dataset: Binance Spot BTCUSDT one-minute klines, monthly archives for 2026-09 and 2026-10 (each `.zip` and `.zip.CHECKSUM`; four URLs under `https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/`). Every ZIP must match the official CHECKSUM. GET only, serial, at most 3 attempts per URL and 12 GETs in total, each ZIP at most 4 MiB, no redirects.
- Holdout-B evaluation window: decision times in `[2026-09-02T00:00:00Z, 2026-11-01T00:00:00Z)`, evaluated as two calendar segments: September (from 2 September) and October.
- Smoke day: every row with `open_time < 2026-09-02T00:00:00Z` in the September archive is discarded immediately after parsing and before any other computation; no value from 1 September 2026 enters features, labels, statistics or logs. The series therefore has a gap from 2026-09-01T00:00Z to 2026-09-01T23:59Z. The frozen gap rules apply: samples whose 60-minute feature window or label window touches the gap are invalid.
- Training: expanding from 2025-09-01 using the development months (September 2025 to June 2026), Holdout-A (July and August 2026) and Holdout-B data only when mature. Fits occur at exactly 2026-09-02T00:00:00Z and 2026-10-01T00:00:00Z. Each fit uses only labels with `label_available_time <=` its fit time.
- Specification v1: the 14 frozen features in their frozen order, StandardScaler fitted on the legal training rows, Ridge(alpha=1, solver="svd", fit_intercept=True), the frozen seed. No tuning, recalibration, feature deletion, model selection or specification substitution. Specification v2 (without `log_close_open`) is a secondary only.
- Labels mature by each month-end cutoff (`label_available_time <= ` end of the evaluation month), as in Phase 2B and Holdout-A.
- The prediction code fingerprints listed in `docs/confirmatory-protocol-v1.md` (seven files) and `configs/history-extension.toml` (SHA-256 `e9cbd19d884d046199f468f29e912cd6a9fbec1852e90ffc125b86d8df0bd9af`) must be unchanged at execution.

## 3. Primary hypothesis and test

Let `f_t` be the v1 15-minute forecast and `y_t` the realized 15-minute log return. Within each evaluation month m, define the standardized centred product

`z_t = (f_t - mean_m(f)) (y_t - mean_m(y)) / (sd_m(f) sd_m(y))` (ddof 0),

so that `mean_m(z)` is the within-month time-series correlation (information coefficient). Because both series are centred within the month, the statistic is unaffected by the agreement of the forecast's average with the month's average return.

- H0: the pooled information coefficient is at most zero. H1: it is positive.
- Variance: within each month, Bartlett HAC of `z_t` within contiguous segments with zero cross-segment covariance (the unmodified `quantforge.power_reanalysis.monthly_stat`), lag `L_m = max(60, ceil(1.3 sqrt(T_m)))` from the month's valid evaluation count.
- Pooling: September and October weighted by their valid sample counts, zero cross-month covariance.
- One-sided normal p-value (`scipy.stats.norm`). Reject H0 if and only if `p < 0.05`. The primary family contains this one test only.
- Short-segment rule (fixed now, as in D-057 item 3): if gaps or invalid samples leave an evaluation segment of length at most the month's LLSW, that segment is dropped from the 15-minute evaluation set and disclosed; the month's lag is recomputed on the remaining count.

## 4. Secondary family (Holm; descriptive, never confirmatory)

- S1: v1, 15m, Clark-West versus Zero (the Protocol v1 primary statistic).
- S2: v1, 15m, within-month demeaned Clark-West, `2 (f_t - mean_m f)(y_t - mean_m y)`.
- S3: v1, 15m, Clark-West versus the training-sample historical mean.
- S4: v1, 15m, Diebold-Mariano versus Zero, two-sided, plus MSE and MAE.
- S5: v1, 5m, information coefficient as in Section 3.

Undefined p-values occupy their slot as 1.0 for the Holm adjustment and remain null in raw output with a reason. Secondary results cannot upgrade a non-rejection of the primary test.

## 5. Pre-specified descriptive outputs (not tests)

- Specification v2, 15m: information coefficient and Clark-West versus Zero.
- Economic magnitude as defined in `analysis/supplementary_a.py` (A1): distribution of `|f|`, and the per-side break-even fee of the non-overlapping 15-minute rule `sign(f) 1{|f| > k}` for k = 0, 2 and 5 basis points (decision phase: bar opening minute congruent to 14 modulo 15).
- 95% interval and one-sided upper bound for the out-of-sample R squared against Zero.
- Drift decomposition `mean(c) = 2 mean(f) mean(y) + 2 cov(f, y)` for S1.
- Pooled Holdout-A plus Holdout-B information coefficient (descriptive only).

## 6. Power statement (ex ante)

With a two-month standard error equal to the Holdout-A value (0.0105), the one-sided 5% test has power of about 0.60 if the true information coefficient equals the 2022-2025 historical value (0.0198), about 0.72 at the full historical value (0.0235) and about 0.95 at the Holdout-A value (0.0347). Holdout-B has fewer valid minutes than Holdout-A because of the excluded first hours of 2 September, so power is slightly lower. A non-rejection is therefore a plausible outcome even if the effect is real; it will be reported in full as a failed confirmatory replication and will not be reinterpreted.

## 7. Execution, integrity and reporting

- Execution only after the owner's written authorization citing this file's SHA-256.
- Real-data processes: download, result-blind audit (counts, gaps, anomalies, undefined features; no forecasts, no target statistics), formal run, independent reproduction. The formal and reproduction deterministic outputs must be byte-identical. A crashed formal or reproduction run is final unless the owner decides otherwise.
- Stop conditions: checksum or size mismatch, redirect, budget excess, any row at or after 2026-11-01T00:00Z or any 1 September 2026 value reaching computation, more than 1% undefined feature rows in an evaluation month (stop for owner review), any training set below 50,000 samples, any refusal by a tool or policy.
- The report states the primary result first, with monthly and pooled estimates, standard errors, lags, sample counts, dropped segments and the exposures below, and keeps all null and negative results.

## 8. Known exposures

- The Holdout-B months have already elapsed; they are sealed rather than in the future, and general knowledge of market conditions in September and October 2026 cannot be excluded.
- The 2026-09-01 smoke file was opened in Phase 1 for engineering purposes. It fixes the price level at the start of Holdout-B, which is why a drift-robust statistic is primary.
- The historical, Holdout-A and D-061 exploratory results were known when this protocol was written.
- No daily-archive cross-check is planned for 2026-09 and 2026-10.
