# Phase 3A analysis plan — frozen protocol (no download)

Phase 3A 是"Historical External Validation under Design Frozen in 2026"：
- spec v1 冻结于 2026-11-02 commit e257efa，早于任何开发数据下载；但设计者的一般市场知识不可被审计为"真正时序前瞻"。
- spec v2 的删除规则由 Phase 2E 从 Initial Train 2025-09 至 2026-02 识别；在 2020-2025 上评估属于对早于其定义时间的数据做后视规格检验，带选择偏差，必须单独披露并标注 evidence_level="post_hoc_specification_evaluated_on_earlier_data"。
- 所有者和 Research Lead 自 2026-11-03 起拥有已见月份的探索性 CW 结果；此协议承认该先验但不承诺可以用它"校准"任何未见数据的检验。
主结论形式："在冻结设计下，spec v1 在 2020-2025 历史区间 CW 检验 {拒绝/未拒绝} H0"。**禁止**将结果表述为 "spec v1 在加密市场中可泛化"、"spec v1 可盈利"、"模型适用于未来"。

## Mathematical definitions, incorporating D-018 through D-022

记号：M = {2021-01, 2021-02, ..., 2025-08}，|M| = 56。
- fit_time_m = 第 m 月第 1 日 00:00:00 UTC。
- train_indices_m 由新增 `make_historical_splits` 的第 m 折提供；训练候选为 `decision_time ∈ [2020-01-01T00:00:00Z, fit_time_m)`，且 valid features、valid labels、feature_available_time ≤ fit_time_m、label_available_time ≤ fit_time_m、target_end_time ≤ fit_time_m，并满足与首条评价目标区间不重叠。
- 训练目标必须且仅能经 `train_y = mature_labels(labels.frame, as_of=fold.fit_time, indices=fold.train_indices)` 读取并二次验证。`mature_labels` 返回目标 Series，不筛选索引；任何不成熟、无效或不一致标签必须 raise。runner 不重复实现候选过滤，也不直接读取训练 target_return。
- eval_indices_m = {t : fit_time_m ≤ decision_time(t) < fit_time_{m+1} AND label_available_time(t) ≤ fit_time_{m+1}}，再同时要求 valid features 与 valid labels。
- 预测 f_t（spec v1 或 v2）、基准 b_t ∈ {Zero=0, HistoricalMean=mean(train_y)}。
- c_t = 2(f_t - b_t)(y_t - b_t)、d_t = (y_t - f_t)² - (y_t - b_t)²、z_t 为每月使用 ddof=0 居中并标准化的 IC 乘积。
- 段内 Newey-West: L_m = max(60, ceil(1.3·sqrt(T_m)))。
- 角色合并（primary）: n 加权 mean/var，跨月协方差 = 0；正态参考 p。具体地，w_m=T_m/ΣT_m，mean=Σw_m mean_m，Var(mean)=Σw_m² Var(mean_m)。不拼接月末观测，不把缺口压缩为连续分钟。

主检验 F_main（|F_main|=1）: mean(c_t)（spec v1、15m、Zero），单侧 H1>0，HAC-LLSW SE，拒 H0 当且仅当 p<0.05。主检验结论仅涉及预测信息，不涉及 MSE 优势或可交易性。

次要族 F_sec（Holm, 5 项）: S1 spec v2 vs Zero 15m CW、S2 spec v1 vs Zero 5m CW、S3 spec v1 vs HistoricalMean 15m CW、S4 spec v1 vs Zero 15m DM（双侧 + 描述性 MSE/MAE）、S5 spec v1 IC-HAC 15m（单侧 H1>0）。S1 带标签 "post_hoc_specification_evaluated_on_earlier_data"。

事前指定稳健性（不入 Holm，只作报告证据）:
- S_dep1：{mean(c_t)_m : m∈M} 的 Newey-West 1994 HAC，L*=4。
- S_dep2：{mean(c_t)_m : m∈M} 的循环块 bootstrap，块长 ∈ {3, 6, 12}，B=2000，percentile 95% CI。
- S_dep3：月份等权 vs n 加权的 CW 均值与 t 对比，差异展示。

探索性诊断（不入 Holm）：Giacomini-Rossi fluctuation 的 signed 与 absolute 曲线不输出 p 值；条件 GW on realized_vol_prev_month 输出 OLS β、HAC SE/t 和 Wald p，标 `exploratory_not_in_holm`，不作确认性或显著性结论；月度/年度/半年度子周期、集中度 top-k、encompassing 校准仅作描述。

## Freeze status, provenance and authorization

Frozen before implementation and any historical download. This plan defines protocol and synthetic-only infrastructure; it is not permission to access market history.

Task provenance: owner original Phase 3A instruction plus D-018, D-019, D-020, D-021 and D-022 superseding amendments. Original requested starting commit: `69adaa3359a5f4caa3e394c67ff05ceb0dfc7ec0`. After the explicitly authorized fast-forward synchronization and environment repair, the pre-implementation base is `86841fd` (record its full SHA in the final manifest). Do not rebase or copy files from another worktree. Work only in `D:\zgs_project\QuantForge-3a`, branch `phase3a-protocol-and-impl`.

Phase 3A authorizes protocol freeze, offline download-plan generation, new history-only infrastructure, synthetic mathematical/engineering tests, and three local commits. It does not authorize a historical-data download, a real historical evaluation, a final-holdout evaluation, a model-selection study, or publication. Existing March–June 2026 forecast evidence supplies only the already-published planning inputs explicitly listed here; no market rows, refits or new scores on those months are used.

The 68-month historical whitelist is exactly 2020-01 through 2025-08: 12 warm-up/training months followed by 56 evaluation months. It is a future permitted-domain definition, not current permission to access that data. All data access remains synthetic-only in this phase. Historical archive availability and size remain unverified.

## Environment gate — D-022 supersedes uv synchronization

- The Research Lead has supplied an independent project-local `.venv` and corrected its editable installation path. Codex must not repeat `uv sync --frozen` or `uv sync --frozen --offline`; the earlier online/offline failures remain honestly recorded.
- Verify imports of pandas, numpy, scipy, pyarrow, sklearn, matplotlib, quantforge and quantforge.research_policy using this worktree's `.venv/Scripts/python.exe`. Record exact Python/package versions and assert `quantforge.__file__` resolves within `D:\zgs_project\QuantForge-3a\src`.
- Run the existing complete suite using the project's default repository-local basetemp; baseline acceptance is exactly the existing 594 passing tests. A previously reported Research Lead run is not substituted for the current execution record.
- Use project-local `.tmp/`, `.cache/`, `.venv` and output directories. Do not modify global packages, system settings, Git configuration, existing commit identity, another worktree, or preinstalled editable paths unless a new explicit instruction authorizes it.
- No new dependency is planned. A missing dependency/import or a failed baseline gate is recorded and stops the phase; do not use repeated synchronization to repair the environment.
- Record the authorized `git merge --ff-only main` outcome before work. An ff-only failure or missing D-015..D-022 / Q10/Q11/Q12 state references is a stop condition. Read state files but do not edit them.

## Frozen files and commit sequence

Commit A freezes exactly three primary artifacts, each UTF-8/LF:
1. `docs/phase3a-analysis-plan.md`.
2. `configs/history-extension.toml`.
3. `docs/confirmatory-protocol-v1-history-addendum.md` — a new historical supplement, not an edit to the old Holdout-A protocol.

`docs/phase3a-plan-manifest.json` records their raw-byte SHA-256 values, timezone-aware UTC frozen_at, original request base, actual synchronized pre-implementation base, approval lineage D-018..D-022, frozen config contract, the existing Phase 2E evidence source hash, the seven code fingerprints, and zero market-network/data-access authorization. Add a hash for the download-plan draft as an auxiliary artifact. Do not hash a manifest into itself.

Commit A also adds `docs/phase3a-download-manifest-draft.json`, appends the Phase 3A safety section to `docs/holdout-policy.md`, appends the English Phase 3A scope clause to `AGENTS.md`, and appends LF rules for only the new frozen files plus `docs/phase3a-evidence/* -text` to `.gitattributes`. No existing `.gitattributes` line may change.

Commit A: `docs: freeze phase3a historical external validation protocol (no download)`.

Only after A: implement WP1–WP5 and synthetic tests; preserve all 594 existing tests. Complete relevant tests and the full suite before commit B:
`feat: add phase3a rolling pipeline, timestamp boundary, cross-month dependence, conditional GW (synthetic only)`.

After B, run the fixed synthetic command and one unchanged reproduction from clean tested code, compare deterministic artifact bytes, and record measured wall time separately from deterministic statistics. No real-data path is wired up. Commit C adds the protocol-only report, record and README section:
`docs: record phase3a protocol freeze and infrastructure (no download)`.

The README status is `protocol frozen, download not authorized`. The old frozen Holdout-A protocol remains a distinct preregistration with unchanged release conditions. No push, remote creation or PR.

## Confirmatory protocol v1 fingerprint pinning

These values are copied from `docs/confirmatory-protocol-v1.md`; they are raw source-byte SHA-256 fingerprints, not newline-normalized comparison hashes. D-021 made the worktree files LF. All seven must be verified unchanged before Phase 3A freeze and at every runner startup; a mismatch stops before model fitting or output generation.

| File | SHA-256 |
| --- | --- |
| `src/quantforge/features/__init__.py` | `857819a0c3c718cf2b1100ea72995db989ed149f65d1ad3d5e4cd3eda1fcbec1` |
| `src/quantforge/labels/__init__.py` | `b968218639387cb82ab1beccef5c5bf3ed3674e5975c3d2b25cb4d318037746e` |
| `src/quantforge/datasets/research_splits.py` | `22c862f6e8e232dedfe17c3a9832f5b7d78737de1593d6b904e2c29c5949919c` |
| `src/quantforge/datasets/splits.py` | `3459e5be5185eee2638709c3db4250e150d8e1deff140d1459882cb5257d93cd` |
| `src/quantforge/datasets/window_splits.py` | `24de7804527d3dc805f46ee969d17267927e345b94af434303cd19793675fc22` |
| `src/quantforge/models/baselines.py` | `a9269ca7f6147f653f608051746304596e898f49ec30f9e1714567e25a1ded83` |
| `src/quantforge/temporal.py` | `6c262bbd1c77e6696c7d310eb31b321f3f27ffeea02fbd64a70f3dcee9bd648c` |

The original confirmatory protocol SHA remains `38ef1aae687b6cd1a9b8b59709b84047250e2a9f90396cb9089244133b38f1b9`. The new supplement explicitly states that historical external validation does not satisfy, amend or execute Holdout-A's release conditions.

## Predictor, folds and deterministic computation contract

- Numeric precision float64; CPU threads 1; NumPy SeedSequence master seed 42; no tuning, recalibration, model selection or early stopping based on outcomes.
- Model horizons `[15, 5]`, specifications `[v1, v2]`, Ridge alpha 1.0, solver `svd`, fitted intercept true, StandardScaler fitted on exactly the legal training X for that month/specification/horizon. Features and targets use the unchanged one-second availability delay.
- v1 canonical feature order: `log_return_1m`, `log_return_5m`, `log_return_15m`, `log_return_60m`, `return_std_5m`, `return_std_15m`, `return_std_60m`, `volume_change_1m`, `volume_zscore_60m`, `taker_buy_volume_ratio`, `trades_change_60m`, `log_high_low`, `log_close_open`, `close_range_position`.
- v2 deletes only `log_close_open`, retaining the other 13 features in order. It is derived from `phase2e_spec_audit_2026-11-04` and carries `post_hoc_specification_evaluated_on_earlier_data`. Do not recompute the feature deletion rule on synthetic or historical evaluation outcomes.
- Each formal synthetic run performs exactly `56 × 2 horizons × 2 specifications = 224` Ridge fits. One unchanged reproduction separately adds 224. Zero and HistoricalMean predictions are algebraic baselines using the same validated train_y; no additional model search or specification is introduced. Test-fixture fits, if any, are counted separately in the record.
- The auxiliary 1m labels used for the conditional-GW instrument are not an additional prediction horizon, fit, test family or model evaluation.
- Public historical splitter range is `[2020-01-01T00:00:00Z,2025-09-01T00:00:00Z)` only. No 2025-09 or later row is accepted by that public entry point. The runner requires all 56 ordered evaluation months; no subset selection or favorable-year stopping.
- `historical_splits.py` imports the unchanged `ResearchSplit` data class. A new private `_make_splits_internal(..., start_bound, end_bound)` applies all frozen validation/time rules with parameterized bounds. Its validator implements the same checks with the supplied bounds; it must not call the old fixed-development-range validator on historical samples.
- The internal kernel validates sorted unique minute-aligned UTC indices, explicit booleans, exact bar/end/feature/label availability equations, legal horizons and month-end cutoff. It includes the original nonoverlap condition and all original audit counts; do not drop apparently redundant safeguards.
- AP9 compares the private kernel on synthetic development-range samples to the unchanged `make_research_splits`. The public wrapper rejects those samples. No monkeypatch of frozen constants, fake calendar shifts, or policy override flag is used.
- Historical folds use `role="historical_rolling"`; `role_bucket` covers every half-year from `2021H1` through `2025H2`, including July/August 2025. Calendar/year/half-year summaries are descriptive, never separately selected inferential endpoints.
- Runner startup validates manifest hashes, code fingerprints, exact configuration keys/types/values and the code/config contract before data construction or fitting. New statistical parameters are read from the frozen configuration; no unrecorded hard-coded alternate values.

## Synthetic dataset — engineering only

The fixed fixture has 68 calendar-month episodes, 2020-01 through 2025-08, each starting at that month's first 00:00:00 UTC and containing exactly 4,096 consecutive one-minute bar opens. Total generated bars are `68 × 4096 = 278528`. Twelve 2020 episodes form initial warm-up/training history, followed by all 56 evaluation episodes. This deliberately sparse synthetic fixture is not a complete 68-month market history and does not estimate the future experiment's power or real-data memory/time requirements.

Between episodes, missing calendar minutes remain missing. The unchanged causal feature/label builders reset at gaps; no price, feature or target is filled across a gap. Each episode includes the unchanged feature warm-up requirement (60 preceding consecutive bars) and horizon tail exclusions. Separate synthetic calendar-edge fixtures verify true month-end maturity, leap days, first/last-minute decision times and the timestamp-unit boundary; the sparse fixture alone cannot verify those boundaries.

For each episode, simulate AR(1) returns with GARCH(1,1) innovations:

```text
z_t = standardized Student-t(df=5), standardized by sqrt((5-2)/5)
h_t = omega + alpha_garch * epsilon_(t-1)^2 + beta_garch * h_(t-1)
epsilon_t = sqrt(h_t) * z_t
r_t = phi * r_(t-1) + epsilon_t
phi = 0.2; omega = 1e-8; alpha_garch = 0.05; beta_garch = 0.9
h_initial = omega / (1-alpha_garch-beta_garch)
r_initial = 0; epsilon_initial = 0
burn-in = 1024 steps per episode; discard burn-in before constructing bars
master_seed = 42
```

The streams use `SeedSequence([42, month_ordinal, stream_id])`, where month_ordinal is 0 for 2020-01 and 67 for 2025-08. Stream 0 generates return innovations; streams 1–5 generate respectively opening gaps, intrabar ranges, volume, trades and taker participation. There is no shared global random state and no seed retries.

The fixed bar constructor starts each episode at price 100. After burn-in, close[t]=previous_close*exp(r[t]); open[t]=previous_close*exp(N(0,0.00005)); width=0.0001+0.0001*abs(N(0,1)); high=max(open,close)*exp(width); low=min(open,close)*exp(-width). Volume is lognormal(3,0.2); trades are uniform integers [10,100); taker fraction is uniform [0.2,0.8]; quote volume=volume*close, with matching taker fraction. Stream IDs 0..5 respectively generate innovations, opening gaps, wick widths, volumes, trades and taker fractions. These settings come from [synthetic]. Close time is one microsecond before exclusive minute end. Synthetic bars are never attributed to Binance as observations.

Do not write raw synthetic bars into Git. Record generation settings, row counts, valid/excluded counts, consumed train indices, fit time, model version, prediction time, target availability, output schema and artifact hashes. The runner accepts only `--synthetic-only`; a real-data argument is rejected before file I/O. Two unchanged runs must produce byte-identical deterministic artifact sets; runtime/process-memory fields belong in a separate run record excluded from that equality assertion.

No Monte Carlo power claim follows from one synthetic run and one reproduction. The final summary reports engineering completion, deterministic reproduction and separately measured elapsed time. A Monte Carlo coverage check for S_dep1 and the stability-unit-test mean estimates are explicitly limited diagnostics, not power estimates for the historical market experiment.

## Inference and robustness parameters to freeze

### Monthly tests and pooling

Use the existing `diagnostics/forecast_tests.py` functions without modifying their behavior. Monthly CW and DM differentials retain their stated directions; IC uses per-month ddof=0 centered products. R²_OOS uses `1 - total_model_SSE / total_baseline_SSE`; never average monthly ratios for the primary summary. Report model/baseline MSE and MAE alongside the tests, including negative/undefined results.

Report all fixed lag sensitivities `[60,275,"LLSW"]` and both poolings `n_weighted`, `month_equal`. `LLSW` means `max(60,ceil(1.3*sqrt(T_m)))`, and every lag is marked admissible only when `lag >= 4*horizon`. The primary and five-member Holm family use LLSW and n weighting only. No most-significant lag or pooling is selected. Null/degenerate monthly quantities remain null with reasons; the formal runner stops if the required primary endpoint cannot be formed from all 56 months.

The secondary family contains exactly five inferential p-values. An undefined raw p remains null with an explicit reason and occupies its predeclared Holm slot as 1.0. Other model/horizon/baseline/lag/pooling rows remain named descriptive sensitivity output; they do not enlarge, replace or silently shrink F_sec. Each S1 row carries the v2 evidence tag. The primary uses unadjusted one-sided p<0.05 because its predeclared family has one member.

### S_dep1, S_dep2 and S_dep3

S_dep1 acts on the ordered 56-month primary CW means, not minute-level concatenation. Use fixed Bartlett HAC lag L=4, fixed-n autocovariance denominator n, and variance-of-mean denominator n; the report names this fixed-bandwidth monthly-mean sensitivity. Report mean, SE, t and interval, with the explicitly stipulated normal reference. This sensitivity does not override the primary rejection rule.

S_dep2 uses a circular moving-block bootstrap on the 56 monthly means. Fixed block lengths `[3,6,12]`, draws 2000 each, master seed 42, block-length stream ID equal to block length. Draw starting positions uniformly with replacement, wrap modulo 56, concatenate complete blocks and truncate to 56 observations. Report every block length's percentile 0.025/0.975 interval; quantile interpolation `linear`. The estimand is the equal-month mean, not a newly selected n-weighted estimand. No block-length tuning, silent block dropping or redraw after a disappointing interval.

S_dep3 receives the monthly primary CW means, counts and primary within-month HAC mean variances. The comparison function may add keyword `month_variances`; the runner always supplies those variances. For weights w, estimate=Σw_m mean_m and variance=Σw_m² variance_m, consistent with the zero-cross-month-covariance convention. Compare equal weights 1/56 against counts/Σcounts; report both estimates/SE/t and their differences, without a new inferential p. If a standalone call omits variances, return the means and mark SE/t undefined rather than inventing a variance from counts alone.

The S_dep1 synthetic coverage check uses the exact [synthetic_validation] settings: stationary AR(1) phi=.3, innovation SD=1, 256 burn-in, 56 months, 1000 replications, seed42 and acceptance [.85,.99]. The additional implementation conventions below fix stream identities and reporting. An independent closed-form Bartlett check also guards the estimator; no tuning or retries.

### Placebos and concentration

Fixed sign-flip block width 60 minutes, draws 500, seed 42. Within each monthly segment, apply independently sampled ±1 signs to contiguous 60-minute blocks of the model-minus-baseline forecast. Recompute CW products against the unchanged outcomes/baseline and pool under the same primary LLSW/n-weighted rule; report the full fixed placebo summary including `(1 + count(t_draw >= t_observed))/(500+1)` and Monte Carlo uncertainty as exploratory evidence. Time-shift placebo uses +1440 actual minutes inside the same monthly segment, exact-time matching, no wrap, no cross-month pairing, and records retained/excluded pairs. A gap is not compressed into a different time shift.

Concentration top-k is `[1,10,100]`, applied separately by month to absolute primary CW contributions, with stable timestamp/order tie-breaking. Report signed share, remaining mean and the existing gap-preserving sensitivity output; do not promote any deletion sensitivity to the primary test. The `encompassing_approx` result uses the existing formula, observed primary forecast differences/residuals and the corresponding reported n_eff; label it approximate and descriptive. Do not insert a new regression fit or claim parameter stability from this approximation.

## Conditional GW information set and diagnostic definition

`build_instrument(labels_1m, fit_times)` uses auxiliary 1m delayed labels built by the unchanged label code solely for the instrument. For fit time m, select the previous calendar month, require the target interval to lie wholly within that month, and require `label_available_time <= fit_time_m`. Select maturity metadata before reading target_return or its validity flags; spies guard target/validity accesses on immature rows. Pending/future targets never enter the instrument.

`realized_vol_prev_month = sqrt(sum(r_1m^2))` over those mature, valid 1m targets. Report contributing count, previous-month bounds and maximum contributing availability time. No forward-fill or inferred missing minutes. For the sparse synthetic fixture, explicitly label this as partial-episode realized volatility, not full-calendar realized volatility. A missing/empty/nonfinite instrument is an explicit diagnostic error or undefined result, never filled from a later month.

Use primary spec-v1/15m/Zero monthly DM loss means as the GW response and the known-at-fit previous-month realized volatility as the scalar instrument; include an intercept. No additional forecast fitting occurs. OLS uses float64, full-rank design and the standard unpenalized least-squares coefficient solution. Report a Bartlett HAC coefficient covariance at fixed monthly lag 4 and normal-reference coefficient t; the joint null is intercept=0 and volatility slope=0, Wald chi-square df=2. Singular design/covariance yields null diagnostics with a reason; do not use arbitrary regularization or pseudoinverse to manufacture a p-value.

All β, SE, t, Wald statistic and Wald p outputs carry `evidence_level="exploratory_not_in_holm"`. The expanding nested-estimation setting does not acquire confirmatory validity from a chi-square reference. This diagnostic cannot select a model, threshold, window, regime or future action.

Fixed bins are empirical quantile positions `[0.0,0.25,0.5,0.75,1.0]` of the already computed 56 monthly instrument values, using quantile interpolation `linear`, left-closed/right-open intervals except the final inclusive upper edge. Tied cut points are retained and empty bins are reported. These bins summarize diagnostic output only, use no target values to set cut points, and are never fed into a predictor or called an available-at-fit trading threshold.

## Giacomini–Rossi-style exploratory fluctuation

The diagnostic input is the primary CW monthly-mean sequence. Use exactly W=12 consecutive calendar months, trailing windows only; no partial windows. The month on each output is the final month of its window. Output signed rolling mean and its absolute value. Do not output p-values or infer break significance. This is a descriptive rolling-CW fluctuation curve. No window search or outcome-selected break date. CSV is required; PNG is optional and has deterministic metadata if emitted.

Stability tests freeze two regimes, m*=25, W=12, sequence length 56, cases β=(0.5,1.0) and β=(0.5,-0.5), f=1, b=0, `y=β+ε`, ε~N(0,0.01²). Hence `c=2β+2ε` and the exact regime means are μ_j=2β_j. Use seed 42 and independent stream identifiers for each case, regime calibration and combined trajectory. Each regime additionally receives exactly 50000 Monte Carlo draws; record mean/SE and assert `SE(mu_j) <= 0.01*abs(mu_j)` without an added epsilon or adaptive sample increase. The analytic μ_j is the primary expectation oracle; Monte Carlo is a separate check.

For one-based monthly t and regime 2 beginning at m*=25:

```text
r_t = mean(c_(t-W+1), ..., c_t)
W_before(t) = max(0, min(W, m* - t + W - 1))
W_after(t) = W - W_before(t)
E[r_t] = (W_before(t)*mu_1 + W_after(t)*mu_2) / W
midpoint_value = (mu_1 + mu_2) / 2
```

For each full trailing window, compare the simulated r_t against this oracle with the specified `3*SE(r_t)` bound. For the independent known-SD noise fixture, `SE(r_t)=2*0.01/sqrt(W)`; also record the sample-based estimate without confusing the regime shift with within-regime noise variance. Check the closest-to-midpoint time lies within W/2 of m*; use earliest timestamp for an exact tie. Check `abs(r_t)^2 == r_t^2` within 1e-18. No universal U/peak assertion remains. The pointwise 3-SE guard is a fixed synthetic test tolerance, not a simultaneous 99% confidence statement or a claim about detecting historical drift.

## CW-based power planning and machine anchors — D-021

Source: the existing frozen `docs/phase2e-evidence/evidence-layers.csv`, selecting exactly four rows with `baseline=zero`, `bandwidth=LLSW`, `pooling=n_weighted`, horizons 15/5 and roles validation/development_evaluation. Require unique keys and read the 24 original cw/dm/ic mean/SE inputs; record the source file raw hash. Do not recompute or edit Phase 2E forecast evidence.

All extrapolation assumes independent identically distributed months [H], stable effect and variance, with `SE_56=SE_2E*sqrt(2/56)`. These are post-observation inputs used for ex-ante planning of the future historical exercise, not preregistration of the already-seen 2E result. Shared expanding training sets, heavy tails and nonstationarity can violate the scaling; S_dep1–3 are prespecified sensitivities. Raw row counts and CW effective n are not interchangeable.

CW/IC use one-sided alpha=.05; DM uses two-sided alpha=.05; power=.8. Frozen critical values are z_one=1.6448536269514722, z_two=1.959963984540054, z_power=0.8416212335729143. `MDE=(z_alpha+z_power)*SE_56`; `%observed=100*MDE/abs(observed_mean)`. IC is dimensionless, CW/DM have return-squared units. Never report cross-unit raw MDE ratios.

| h | Role | Test | SE 56m | MDE 56m | % of own observed effect |
| --- | --- | --- | ---: | ---: | ---: |
| 15 | validation | CW | 8.1838e-10 | 2.0349e-09 | 19.014% |
| 15 | validation | DM | 8.1769e-10 | 2.2908e-09 | 65.769% |
| 15 | validation | IC | 2.0809e-03 | 5.1740e-03 | 15.501% |
| 15 | development_evaluation | CW | 9.0394e-10 | 2.2476e-09 | 23.381% |
| 15 | development_evaluation | DM | 9.1138e-10 | 2.5533e-09 | 141.138% |
| 15 | development_evaluation | IC | 1.9698e-03 | 4.8978e-03 | 18.740% |
| 5 | validation | CW | 1.6329e-10 | 4.0603e-10 | 35.720% |
| 5 | validation | DM | 1.6792e-10 | 4.7043e-10 | 88.423% |
| 5 | validation | IC | 1.3414e-03 | 3.3354e-03 | 29.663% |
| 5 | development_evaluation | CW | 1.4585e-10 | 3.6265e-10 | 25.281% |
| 5 | development_evaluation | DM | 1.4813e-10 | 4.1499e-10 | 2073.524%* |
| 5 | development_evaluation | IC | 1.2466e-03 | 3.0997e-03 | 21.948% |

*: 5m Development 的观测 DM 效应 −2.0014e-11 近零，使 DM 规划工具失效；CW 和 IC 在该角色仍可用。此行必须保留；不得通过改分母或剔除来掩盖。Here "失效" describes the observed-effect-relative extrapolation becoming uninformative, not arithmetic failure of the MDE formula.

The human-readable display table is checked against full precision at rtol=1e-4. It is never used as the rtol=1e-12 machine oracle. The report includes both a 15-digit machine table and the readable table. The following D-021 values are the exact machine-anchor text to preserve in the frozen plan/config artifact:

```json
{
  "formula": {
    "scale_56m": "sqrt(2/56)",
    "z_one_sided_alpha_0p05": "1.6448536269514722",
    "z_two_sided_alpha_0p05": "1.959963984540054",
    "z_power_0p8": "0.8416212335729143",
    "mde_formula": "(z_alpha + z_power) * se_2E * scale_56m",
    "pct_formula": "100 * mde / abs(obs_mean)"
  },
  "anchors_from_phase2e_evidence_layers_csv": {
    "15_validation_CW": {"se_2E":"4.330445321942128e-09","se_56":"8.183772420015648e-10","mde_56":"2.034874438662173e-09","pct_obs":"19.01395494184081","obs_mean":"1.0702005158244944e-08"},
    "15_validation_DM": {"se_2E":"4.326795335294619e-09","se_56":"8.176874593617067e-10","mde_56":"2.290821099184106e-09","pct_obs":"65.76901835183048","obs_mean":"-3.4831310495306305e-09"},
    "15_validation_IC": {"se_2E":"0.01101095402994968","se_56":"0.0020808747186293786","mde_56":"0.005174042675772705","pct_obs":"15.501303730194117","obs_mean":"0.033378113001517926"},
    "15_development_evaluation_CW": {"se_2E":"4.783212507512544e-09","se_56":"9.039421973465615e-10","mde_56":"2.247629549069399e-09","pct_obs":"23.38055282897031","obs_mean":"9.613243816392623e-09"},
    "15_development_evaluation_DM": {"se_2E":"4.8225709984444895e-09","se_56":"9.113802529883271e-10","mde_56":"2.5533094448521543e-09","pct_obs":"141.13818846342443","obs_mean":"-1.8090847506618222e-09"},
    "15_development_evaluation_IC": {"se_2E":"0.010423137780364284","se_56":"0.0019697878891289764","mde_56":"0.004897828066884597","pct_obs":"18.7398434923726","obs_mean":"0.026135906998786235"},
    "5_validation_CW": {"se_2E":"8.640729996716182e-10","se_56":"1.6329444798119267e-10","mde_56":"4.060275397684427e-10","pct_obs":"35.7203885037303","obs_mean":"1.1366828771362356e-09"},
    "5_validation_DM": {"se_2E":"8.885343560209244e-10","se_56":"1.6791720981202088e-10","mde_56":"4.704343728761316e-10","pct_obs":"88.42313825448302","obs_mean":"5.320263249673575e-10"},
    "5_validation_IC": {"se_2E":"0.00709812709482857","se_56":"0.0013414199333746987","mde_56":"0.003335406941742485","pct_obs":"29.663262229627513","obs_mean":"0.011244235094315075"},
    "5_development_evaluation_CW": {"se_2E":"7.717520543806254e-10","se_56":"1.4584742926388075e-10","mde_56":"3.6264596633674817e-10","pct_obs":"25.28135242840264","obs_mean":"1.4344405322609606e-09"},
    "5_development_evaluation_DM": {"se_2E":"7.838104675873235e-10","se_56":"1.4812625516037933e-10","mde_56":"4.1498832687174854e-10","pct_obs":"2073.5237704055958","obs_mean":"-2.001367588810298e-11"},
    "5_development_evaluation_IC": {"se_2E":"0.0065965028843915086","se_56":"0.0012466218682014418","mde_56":"0.0030996939358628297","pct_obs":"21.94780824033855","obs_mean":"0.014123022681443914"}
  }
}
```

All power calculations are conditional planning, never stop/rejection criteria. A later separately authorized historical report must highlight actual CW SE differing by more than 2× from the specified calibration expectation; do not use that deviation to tune, stop early, or replace the primary test. Synthetic engineering success does not establish historical power, predictive information or profit.

## Offline download plan, limits and timestamp policy

`research_policy.py` may only receive additive history constants/functions: HISTORICAL_START/END, the 12/56/68 ordered month tuples, `ensure_historical_month`, `ensure_month_in_historical_allowlist`, and `historical_month_bounds`. Existing `ALLOWED_MONTHS`, `EVALUATION_MONTHS`, `DEVELOPMENT_START/END`, `_POLICY`, `canonical_policy`, `month_bounds`, `ensure_allowed_month`, `ensure_not_holdout`, `validate_development_bounds`, holdout constants and smoke date remain unchanged.

`plan_historical_download(config_path)` produces 68 rows/dictionaries with month, URL, expected_bytes_range and expected_checksum_url, plus draft status `not_yet_downloaded`. Before constructing each row it validates the history allowlist and `ensure_not_holdout` on the month interval. URLs use the official BTCUSDT Spot 1m monthly archive pattern with `.zip` and `.zip.CHECKSUM`. A generated URL is not a verified archive. No network client, request, HEAD, checksum retrieval, archive read, cache read, data directory scan or download resume is implemented/executed by the planner.

Frozen future-planning budget: ZIP payload 402653184 bytes (384 MiB) cumulative including retries; checksum payload 1048576 bytes (1 MiB); disk reservation 8589934592 bytes (8 GiB); max_concurrent=1; per_month_retry=3; backoff_seconds=[1,4,16]; official_checksum_required=true. These limits do not confer transfer permission. The unverified monthly ZIP planning range is [2097152,4718592] bytes (2–4.5 MiB); the 68-month sum is [142606336,320864256] bytes (136–306 MiB). These are planning estimates, not remotely measured sizes or acceptance bounds. No request refines the estimate. Historical monthly CSV parsing inherits 32 MiB and daily parsing 8 MiB; future per-ZIP and per-checksum limits are 8 MiB and 16 KiB. The cumulative cap is separate and no transfer is authorized.

Timestamp policy: `ms_window_end="2024-12-31T23:59:59.999Z"`, `us_window_start="2025-01-01T00:00:00Z"`, `detection="per_month_from_first_row_magnitude"`, `cross_validate=true`. `detect_unit(first_row_open_time)` accepts ≥1e15 as us, <1e14 as ms, and rejects the ambiguous range. Integer validation rejects bool, fractional and nonfinite values before conversion. Cross-check both inferred unit and decoded month/date policy, reject mixed units within a file and reject an open/close unit disagreement. Never convert epoch integers via floating point.

Unit mismatch raises `DataValidationError("timestamp unit mismatch: expected %s for month %s, got %s")`. Synthetic positive cases are 2024-12-31 ms and 2025-01-01 us; inverse units are mandatory negative cases. Preserve the old Phase 2B downloader behavior and existing daily holdout guard behavior. Append parse_historical_daily_klines(csv_bytes, day, config_path=...) and parse_historical_monthly_klines(csv_bytes, month, config_path=...) to data_monthly.py. They enforce the hashed historical timestamp policy before delegating to existing parsers; all old parser/downloader function bytes remain unchanged. The planner returns the frozen download-manifest draft dictionary schema.

The optional future ingestion design must preserve raw URLs, hashes, UTC provenance, integer units/schema and validation reports. It must eventually enforce checksum/ZIP/schema/continuity checks, but no real ingestion is authorized here. Existing licensing records (Binance Vision Dataset Terms v1.0 / CC BY-NC-SA 4.0, no raw redistribution) are cited without new network verification; publication requires a separate terms/permissions review.

## Scope exclusions and hard stops

- No market network request of any kind, including HEAD, CHECKSUM or GET; no 2020–2025 raw market downloads, cache reads or formal historical evaluation. Authorized earlier package-sync failures are recorded separately; D-022 now removes the package-sync step entirely.
- Holdout-A `[2026-07-01,2026-09-01)` and Holdout-B `[2026-09-02,2026-11-01)` remain sealed. Do not download/read/parse/statistically derive anything from any real 2026-07/08/09/10 day, including the 2026-09-01 smoke day in this phase. Synthetic rejection fixtures containing forbidden dates test guards without reading real data.
- No changes to old frozen plans/configs/evidence/results, the seven fingerprinted source files, Phase 2E-created code/evidence, or the existing behavior of `scripts/download_sample.py` and `scripts/run_power_reanalysis.py`.
- Do not edit `src/quantforge/online/**`, `tests/test_online_*`, `test_incremental_ridge.py`, `test_feedback_queue.py`, `CLAUDE.md` or `research/*.md` state files. Running the preserved baseline tests is allowed; changing them is not.
- No tuning, online comparison, forgetting, drift-trigger search, neural model, account access, simulated/live orders, PnL, turnover/execution claims, remote creation, push or PR. Existing forecast-error/statistical summaries do not become strategy return metrics.
- Stop on any failed AP, failed baseline/environment gate, missing required state reference, ff-only failure, fingerprint/manifest mismatch, attempted forbidden data/network access, or inability to obey the fixed computation budget. Report the failure exactly; do not bypass, retune, retry seeds or silently exclude a candidate.
- If any frozen plan error is discovered after A, do not edit frozen files. Record the deviation in `docs/phase3a-record.md` and stop for a new owner instruction.
- No assertion that unrun anchors/tests passed. Distinguish verified facts, planned checks, assumptions and untested claims throughout.

## Acceptance anchors

| Anchor | Required verification |
| --- | --- |
| AP1 | Raw-byte SHA-256 of the three new frozen primary files matches the plan manifest; existing frozen artifacts remain unchanged. |
| AP2 | Existing Phase 2E evidence raw hash is preserved; 15m/validation/Zero/LLSW has cw_mean=1.0702005158244944e-08, cw_se=4.330445321942128e-09, cw_t=2.4713405579833725; retain dm_se=4.326795335294619e-09 and dm_mean=-3.4831310495306305e-09 as distinct fields. |
| AP3 | The complete 56-evaluation-month synthetic pipeline and one unchanged reproduction have byte-identical deterministic artifact sets; both contain 224 Ridge fits and every required month/spec/horizon/diagnostic sensitivity. |
| AP4 | All 68 planned URLs pass the separate history allowlist and holdout guard; zero months intersect A/B or the smoke-day month; every status is not_yet_downloaded and the network-request count is zero. |
| AP5 | All seven actual worktree source-byte hashes equal the copied confirmatory fingerprints; runner asserts them at startup without newline normalization. |
| AP6 | `test_phase3a_power_addendum_anchor.py` reconstructs all 12 full-precision MDE/% rows from the unchanged CSV and fixed formulas at relative error ≤1e-12; readable display at ≤1e-4; missing/duplicate keys rejected. |
| AP6b | Original maturity guard preserved: spies prove every fold's training target access passes through mature_labels; an immature index or bypass attempt fails before fitting. |
| AP7 | Conditional-GW spies prove no immature target value/validity flag is accessed; the instrument uses only previous-calendar-month 1m targets mature by fit_time and retains its source-count/availability audit. |
| AP8 | Synthetic 2024-12 ms and 2025-01 us cases succeed; either reversed policy/unit case raises the required DataValidationError; ambiguous/mixed units fail. |
| AP9 | Private parameterized kernel exactly matches the unchanged research splitter on synthetic 2025-09..2026-06 samples for both horizons (indices/times/audits except role fields); public historical wrapper rejects those samples and accepts legal history; old public splitter rejects legal history; all three split-code hashes remain unchanged. |

Test inventory includes new history policy and planner tests, timestamp-boundary tests, runner and deterministic reproduction tests, maturity-spy tests, cross-month dependence tests, GW information-set/closed-form tests, stability tests, machine-power-anchor tests, AP9 equivalence/strict-range tests and additive-policy tests. Preserve all 594 prior tests and report the actual new count rather than promising an approximate count as a result.

## Required artifacts and final report

Deterministic synthetic products should include monthly fit/sample audits, paired forecast metrics, monthly and pooled CW/DM/IC/R² statistics, primary result, exactly five secondary Holm rows, all fixed lag/pooling sensitivities, S_dep1–3, placebo/concentration/encompassing summaries, GW instrument/OLS/Wald diagnostics, fluctuation CSV and optional figure, prediction/model version trace, budget counts and an artifact hash manifest. Large synthetic predictions/bars stay ignored under `results/phase3a`; commit only small protocol/test/record artifacts required by the task.

`docs/phase3a-report-protocol-only.md` contains scope, frozen definitions, expected conditional power, proposed transfer budget, stop conditions and limitations; it contains no claimed real historical result. `docs/phase3a-record.md` records approval amendments, earlier blocked attempts, environment repair provenance, exact commands/versions/seeds, A/B/C SHAs, raw artifact fingerprints, actual tests, actual fits, elapsed time, and deviations. It does not rewrite Research Lead state files.

Final Chinese report includes: the three commit SHAs and three frozen-file hashes; old/new/full test counts; AP1–AP9 with AP6b; synthetic engineering outcome and runtime (explicitly no historical/Monte Carlo power claim); 12-row planning-anchor recomputation; 68-month offline plan size estimates/caps; all deviations/issues/uncompleted work; clean `git status`; no push/PR/remote; no market-network or holdout access. If any item is not complete, say so without replacing it with an inferred success.

## Additional implementation conventions fixed before coding

- Every concrete setting is in the frozen TOML and passed to pure functions. The manifest holds the complete typed config contract. Runtime checks pin the manifest, its three new frozen files, original confirmatory protocol, source evidence and seven prediction files.
- WP-0 on 86841fd56210aed87ad06af5c165cef2dd00adaa: D-022 versions and project import path verified; 594 tests passed in 99.79s with python -B -m pytest -q -p no:cacheprovider and default basetemp. No uv sync under D-022. Main/2F venv metadata was sampled (10052 files each); require identical size/mtime snapshots before commit C. This metadata-only exception does not permit reading their research files.
- S_dep1 coverage fixture: 1000 independent stationary AR(1) series, phi=.3, innovation SD=1, 256 burn-in, 56 retained months; per-replication SeedSequence([42,replicate]), lag4, normal95% intervals. Acceptance [.85,.99]; report actual coverage and MC SE without tuning. This is a synthetic engineering tolerance, not a uniform coverage guarantee.
- Stability RNG: SeedSequence([42,case_index,stream_id]); cases 0 same-sign,1 reverse-sign; stream0/1 calibrate the two regimes with 50000 draws, stream2 generates the combined trajectory. Use exact mu=2*beta and independently check MC mean/SE. No seed retry; three-SE guards are pointwise.
- GW response is primary v1/15m/Zero DM monthly mean. Regress on intercept and previous-month 1m RV; fixed-n Bartlett score HAC lag4, no finite-sample correction. Wald null: both coefficients zero, chi-square df2. Quantile bins use np.quantile(method="linear") across 56 instruments descriptively only. Read temporal metadata to select mature prior-month indices, then access validity/targets only on selected rows. Undefined diagnostics retain null/reason; never epsilon regularize.
- S_dep3 receives main LLSW monthly mean variances as month_variances; pooled variance=sum(weight squared * variance). If missing, t=null with reason; runner always supplies variances. Bootstrap quantiles use linear interpolation.
- Placebos use only primary v1/15m/Zero, 56 months, 500 UTC60min sign draws, +1440min non-wrapping shift, all lag60/275/LLSW. Reuse unchanged placebo functions. Their abs-mean .3 and SD [.8,1.25] warning flags are reported, not extra selected hypotheses. The old LLSW formula equals the min60 rule for the >=2500-row synthetic segments.
- Concentration uses primary CW within-month top-|c| k=1,10,100 with minute gaps preserved; report changes/shares descriptively and omit p-values. Encompassing uses the existing approximation for primary forecasts/residuals and compares to -DM t with limitations. Descriptive subperiods include every year/half-year including 2025H2.
- R2 always uses sum-SSE ratios, explicitly labeled even in month-equal sensitivity rows; MSE/MAE follow indicated weights. Retain every model/spec/horizon/baseline/lag/pooling. Keep S1's post-hoc specification tag separately from synthetic engineering evidence.
- Required deterministic outputs: monthly-tests.csv, pooled-tests.csv, secondary-tests.csv, fold-audit.json, models.json, predictions.parquet, dependence.json, placebo.csv, concentration.csv, encompassing.csv, subperiods.csv, gw-instrument.csv, conditional-gw.json, conditional-gw.csv, stability.csv, power-planning.csv, download-plan.json, summary.json. Only runtime.json holds wall time and measured peak process memory. UTF-8/LF, CSV %.17g, sorted JSON without NaN, deterministic sorting. Use new descendants of results/phase3a; never overwrite earlier results.
- Standalone synthetic budget: at most224 Ridge fits,1200s,4000000000 measured peak process bytes per run. Formal run and unchanged reproduction counted separately; unit-test fits reported separately. Sparse fixtures do not establish full-history resource feasibility.
