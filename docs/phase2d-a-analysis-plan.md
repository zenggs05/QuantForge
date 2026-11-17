# Phase 2D-A: Expanding versus calendar Rolling Ridge

Freeze date: 2026-11-03 (Asia/Shanghai). Base Git commit: `83577059d6081ec39233713b07fa46d96cb6c681`.

**This is an exploratory follow-up on already observed development data.** Phase 2B/2C already inspected March--June 2026. Freezing this protocol before the new implementation and run does not turn these months into fresh confirmatory out-of-sample evidence. The plan and configuration are hashed in `phase2d-a-plan-manifest.json` and committed before algorithm development. Any necessary protocol correction must be explicit, justified and separately recorded; there will be no silent result-driven change.

## Hypotheses and fixed primary contrast

H0: shortening Ridge training history does not systematically improve prediction squared error on the current development months. The research alternative is that a prespecified Rolling Ridge procedure has lower paired squared error.

The single primary diagnostic contrast is **15m, Rolling 3M minus Expanding, March--April Validation, observation-count-weighted MSE difference**. Rolling 3M is selected before this experiment as an intermediate-window hypothesis, not selected from its scores. The directional research alternative is lower loss, but the reported HAC p-values and 95% intervals are two-sided to retain comparability with the existing diagnostic tools. A negative loss difference favors Rolling; a positive improvement percentage favors Rolling.

All 1M/3M/6M candidates, all four months, both horizons, both historical roles and both declared role aggregations must be reported. May--June remain `development_evaluation`, already seen and exploratory. Statistical significance does not establish generalization, adaptation to concept drift, tradable alpha or cost-adjusted profit.

## Data, model and access boundary

Only the already verified local Binance Spot BTCUSDT 1-minute OHLCV development files for `[2025-09-01, 2026-07-01)` UTC may be used. Read the existing development manifest and period-policy contracts. Never request new market data, including HEAD, CHECKSUM, ZIP or APIs; never open, inspect or evaluate July/August 2026 final-holdout market files. The September 1, 2026 smoke sample is excluded. Synthetic fixtures are labeled synthetic and are not market evidence.

Use the existing 14 causal features in their existing order, existing close-to-close delayed labels, availability delay 1 second, 15m primary and 5m auxiliary. Features are constructed once using the full authorized historical data so that legitimate prior history is available for warm-up; the estimation window restricts training observations, not causal feature history. Keep alpha=1.0, StandardScaler + Ridge(solver="svd"), seed=42, and one CPU numerical thread. No new feature, target, hyperparameter search, neural model, calibration adjustment, drift trigger, online update or trading execution is authorized.

The original frozen reference is `results/research-baseline/20261103T010111153259Z-a2cb0858/summary.json`, SHA-256 `7aa9f334a9e5b5f1085edd2362676d86d0898fcdd376f92bb1886efbc3dc6f12`. Its prediction Parquet SHA-256 is `66f74e1367ed816455d8df2af5c8fdd89009c893aa5fe064df29fe646c86407a`. These files are read-only references and must never be overwritten. Original Phase 2B/2C source contracts, predictions, plans and tests remain intact. New comparison code is separate from the frozen Phase 2C analysis entry point.

## Calendar windows and causal eligibility

Each model fit time is the prediction month's first instant, 00:00:00 UTC. Expanding starts at 2025-09-01. A W-month Rolling window starts at `fit_time - W calendar months`, using calendar arithmetic, not W times 30 days.

| Prediction month | Rolling 1M start | Rolling 3M start | Rolling 6M start | Exclusive end / fit time |
|---|---|---|---|---|
| 2026-03 | 2026-02-01 | 2025-12-01 | 2025-09-01 | 2026-03-01 |
| 2026-04 | 2026-03-01 | 2026-01-01 | 2025-10-01 | 2026-04-01 |
| 2026-05 | 2026-04-01 | 2026-02-01 | 2025-11-01 | 2026-05-01 |
| 2026-06 | 2026-05-01 | 2026-03-01 | 2025-12-01 | 2026-06-01 |

Training eligibility requires all of:

- `decision_time >= train_start` and `decision_time < fit_time`;
- `feature_available_time <= fit_time` and `label_available_time <= fit_time`;
- existing valid-feature, valid-label, data-continuity and nonoverlapping-target conditions;
- each Rolling training index is a subset of the corresponding legal Expanding index.

Window membership uses decision time, never source file month or bar-open time. A prior-month last-minute bar whose feature becomes available in the new month cannot enter the earlier fit. Training order and feature order remain unchanged. Scalers fit only each model's own legal training rows.

All programs use exactly the original month/horizon evaluation indices, truth and metadata, with evaluation labels matured by next month start. No sample may be dropped because one estimator's training set is smaller. June end labels are not completed using July prices. The four folds are March/April `validation` and May/June `development_evaluation`. A zero/insufficient/invalid training window fails explicitly, rather than narrowing evaluation rows.

First verify March Rolling 6M and Expanding equality: training indices, features/order, matured labels, scaler statistics, Ridge coefficients/intercept and predictions. New Expanding must reproduce the frozen reference predictions for every month/horizon. Diagnose any mismatch before attributing it to adaptation. Logical month-start readiness retains the existing research assumption; measured fit latency is not an execution simulation.

## Models, budget and audits

Exactly four Ridge programs (`expanding`, `rolling_1m`, `rolling_3m`, `rolling_6m`) times four months times two horizons: at most **32 Ridge fits per formal real-data run**. Preserve original Expanding Zero and Historical Mean as auxiliary references. Synthetic unit tests and the single declared identical reproduction run have separate fit counts. No other candidate or result-driven rerun is allowed.

Record each model's requested/actual training bounds, row count, immature-label and invalid-row exclusions, scaler state, coefficients/intercept/version, feature order, fit/predict wall time, seed, numerical thread settings, data/manifest hash and code hash. Record total Ridge training-row visits and total fit time, plus reference model cost separately. Reusing the same number of fits does not imply equal computation: window sizes differ. Fixed sklearn alpha does not give identical per-observation regularization across sample sizes. Scaler differences, estimation variance, sample weighting and penalty strength prevent attributing every difference to concept drift.

## Pairing and descriptive metrics

Validate unique and complete keys `(decision_time, horizon_minutes, fold, month, role)` for all models, with exactly equal true targets and relevant temporal metadata. Require complete key-set equality before alignment; do not silently discard records with inner joins. Define squared-loss difference `d_t=(prediction_rolling-target)^2-(prediction_expanding-target)^2`; absolute-loss differences are descriptive only.

For each month, horizon and model report n, MSE, MAE, Pearson IC, Spearman IC, bias=mean(pred-target), prediction population standard deviation (ddof=0), target standard deviation, their ratio, and diagnostic calibration slope cov(pred,target)/var(pred). Constant/undefined correlations, slopes or ratios are null with reasons, not zero. Slopes are diagnostic only: no refit or recalibrated score. Report MSE change relative to Expanding and Zero; relative delta is `(MSE_model-MSE_reference)/MSE_reference`, improvement percentage is its negative times 100. Zero denominator is undefined with a reason.

Report all months plus role aggregation with observation weights n_m/sum(n_m) and equal-month weights 1/M. Aggregate absolute differences and reference MSE using the same weights before dividing; do not average monthly percentages. Do not combine Validation and Development into a confirmatory result. These are prediction metrics, not fees, profit, Sharpe or drawdown.

## Block bootstrap and HAC reuse

Reuse tested Phase 2C low-level numerical functions via a separate window-comparison adapter. Do not alter the frozen Ridge-versus-Zero/Mean families. Numerical equivalence tests must compare the adapter against the original lower-level functions.

Moving Block Bootstrap uses seed=42, 2,000 replicates, 95% two-sided percentile intervals, primary L=1440 and all sensitivity lengths `[60,360,1440,2880]`. Use the existing non-circular fixed-length algorithm: legal starts within each continuous minute segment; sample ceil(n/L) blocks with replacement and truncate the last block to original segment n. The same sampled indices apply to paired losses and reference loss, including the denominator of relative intervals. Never cross month, role or gap boundaries; short segments make that combination explicitly non-estimable, without deleting segments or reducing L. Use stable SeedSequence substreams `[42, horizon_minutes, window_id, block_length, year, month]`, with window_id 1/2/3 for Rolling 1M/3M/6M respectively; never Python randomized hash. Role inference resamples months independently under the fixed weights, conditional on those observed months.

Report every monthly and role/weight combination: 8 scopes (4 months + 2 roles times 2 weights) times 2 horizons times 3 candidates times 4 block lengths = **192 bootstrap rows**. Include absolute and relative intervals, n/L and small-block-count limitations. No interval-selected window or block length replaces the primary comparison. Percentile intervals are marginal, not simultaneous; finite-sample boundary effects and Monte Carlo error remain.

HAC uses existing Bartlett/Newey--West computations, primary lag=60, sensitivities `[15,60,240]`, fixed-n autocovariance denominator, normal approximation and no new small-sample adjustment. Apply within continuous segments and combine segment/month mean variances using squared weights, with zero cross-segment/month covariance approximation. Segments shorter than lag requirements are explicitly non-estimable. Degenerate standard errors retain null p-values with reasons.

Report 8 scopes times 2 horizons times 3 candidates times 3 lags = **144 HAC rows**, retaining raw two-sided p-values, estimates, standard errors and 95% intervals. The primary Holm family is **six tests**: 15m, each of three Rolling candidates, each of two roles, observation weighting, lag60. Undefined tests still count in family size; retain null for undefined p. Also report a separate sensitivity Holm correction across all 144 predeclared HAC rows; it does not replace the primary family. Other horizons, weights, months and lags are exploratory sensitivities, never a source of a substitute best-looking primary result. Bootstrap does not invent a p-value; inferential p-values are HAC values. Holm-adjusted p-values do not make the marginal CIs simultaneous.

Each role contains only two adjacent months. Shared training history and persistent market conditions can violate the assumed negligible cross-month covariance. Overlapping labels, heavy tails and nonstationarity limit both methods. A time-block bootstrap cannot establish generalization across market regimes or remedy prior observation of these data.

## Validation, execution and artifacts

Preserve all 268 original tests. New tests cover calendar/leap-year arithmetic; March 6M equivalence first; rolling-subset property; maturity and future exclusion; gaps/warm-up; one-second month boundary; fixed evaluation rows; train-only scaling; invalid-window rejection; holdout rejection before file access; model determinism; constant/linear synthetic signals; stationary and known-break synthetic comparisons; paired-loss sign; bootstrap/HAC equivalence; Holm; missing-artifact failure; frozen-artifact protection. Label synthetic experiments. Do not delete tests or alter this plan in response to insignificant real results.

After passing all tests, create `feat: implement audited rolling ridge window comparison`, then run from its clean worktree with `git_dirty=false`. Command: `.\.venv\Scripts\python.exe scripts\run_window_comparison.py --config configs\rolling-windows.toml`. Write a fresh unique run under `results/rolling-window-comparison/<run-id>/`; never reuse existing output directories.

Required artifacts: predictions.parquet, metrics.csv, window-audit.json, paired-losses.csv (or complete aggregate equivalent), bootstrap-results.csv, hac-results.csv, summary.json, report.md, figures/, resolved config and provenance. Success summary is written last after verifying all artifacts; failure creates failure.json and never a successful summary. Capture input manifest and frozen-reference hashes, Git commit/dirty, source hashes, model states, dependencies, CPU threads, seed and timestamps. Separate nondeterministic timings and run identifiers from deterministic content.

Run exactly one clearly labeled reproduction with unchanged code/config/data and compare all prediction fields, model states, statistics and deterministic artifacts. Run ID, timestamps and timings may differ. Keep real data and large artifacts out of Git. Publish small audit/statistical tables and real generated figures in docs where useful, with hashes and exact reproduction paths.

Deliver `docs/phase2d-a-record.md`, `docs/phase2d-a-analysis-report.md`, `research/rolling-window-findings.md`, and `docs/phase2d-a-reproducibility.json`; update README. Report all windows, negative findings, resource usage, equivalence/frozen-artifact checks, synthetic test behavior and limitations. Evaluate whether B (drift-triggered retraining) or C (delayed online updates) deserves a separately frozen future study; implement neither here. Final documentation may be committed as `docs: record phase2d-a exploratory findings`. No remote repository, push, PR or orders.
