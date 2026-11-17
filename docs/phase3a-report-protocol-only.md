# Phase 3A protocol-only report

**Protocol frozen; download not authorized.** This report describes the historical external-validation design and its synthetic engineering checks. It contains no result from real historical market data and no trading or profitability claim. Actual test outcomes, failures, acceptance anchors, local commit identifiers, resource measurements and reproduction evidence belong in [the execution record](phase3a-record.md).

The governing artifacts are [the frozen analysis plan](phase3a-analysis-plan.md), [the frozen configuration](../configs/history-extension.toml), [the historical supplement](confirmatory-protocol-v1-history-addendum.md) and [the plan manifest](phase3a-plan-manifest.json). They were frozen in commit `927145671910df600a1081d4ce6478439ca4b0a4`. This report is a description, not an amendment to those artifacts. D-023's subsequent stability-test correction is disclosed separately below.

## Research position and scope

Phase 3A is **Historical External Validation under Design Frozen in 2026**. Specification v1 was frozen on 2026-11-02 in commit `e257efa`, before development-data downloads. The designer's general market knowledge cannot be audited as a genuinely prospective design made in 2020. The owner and Research Lead have seen exploratory CW results for previously observed months since 2026-11-03; the protocol acknowledges that prior information without using it to calibrate unseen results.

Specification v2 drops only `log_close_open` from v1's 14 features, following the Phase 2E audit of Initial Train, September 2025 through February 2026. Testing this later-defined specification on earlier history has selection bias. Every relevant result must retain `post_hoc_specification_evaluated_on_earlier_data` independently of any synthetic-data label.

The permitted future conclusion is confined to whether the frozen v1 design rejects the specified historical CW null. It cannot establish generalization across crypto markets, future usefulness, lower realized prediction error, tradability or profitability. No such historical conclusion is available in this protocol-only phase. The original Holdout-A protocol and its release conditions remain separate and unchanged.

The proposed domain has exactly 68 months: January–December 2020 provides 12 initial warm-up/training months; January 2021–August 2025 provides 56 evaluation months. The public historical splitter admits only `[2020-01-01, 2025-09-01)` UTC. A whitelist and generated URLs define scope; neither grants permission to access data.

## Forward evaluation and inference

Each month's fit time is its first day at 00:00:00 UTC. The historical splitter constructs expanding training indices with valid features and labels, mature labels, available features, completed target intervals and the frozen nonoverlap rules. Training targets must then pass through `mature_labels(labels.frame, as_of=fold.fit_time, indices=fold.train_indices)` before fitting. Evaluation decisions lie within the month and their labels must mature by the next month's start. The independent historical splitter preserves the original `ResearchSplit` class; equivalence tests exercise its private parameterized kernel against the unchanged development splitter. All seven prediction-code fingerprints remain pinned.

Both horizons, 15 and 5 minutes, use float64 Ridge with alpha 1, SVD solver, fitted intercept and a scaler fitted only on legal training rows. Zero and HistoricalMean are the fixed baselines, with HistoricalMean calculated from the validated training targets. No tuning, recalibration, month selection or outcome-dependent stopping is allowed.

For forecast `f`, baseline `b` and target `y`, CW uses `c = 2(f-b)(y-b)`; DM uses `d = (y-f)^2 - (y-b)^2`. IC uses monthly centered, standardized products with ddof 0. Within-month inference uses Bartlett HAC with primary lag `L = max(60, ceil(1.3*sqrt(T_m)))`. For `w_m = T_m/sum(T_m)`, primary pooling has mean `sum(w_m*mean_m)` and variance `sum(w_m^2*var_m)`, assuming zero cross-month covariance. Missing calendar minutes are never compressed into contiguous observations.

The single-member primary family tests v1/15m/Zero CW, one-sided `H1: mean(c)>0`, with a normal-reference p-value and rejection only at `p<0.05`. Its claim concerns predictive information. The secondary family applies Holm to exactly these five prespecified p-values:

| Member | Comparison | Test and direction |
| --- | --- | --- |
| S1 | v2 versus Zero, 15m | CW, one-sided positive; post-hoc specification tag |
| S2 | v1 versus Zero, 5m | CW, one-sided positive |
| S3 | v1 versus HistoricalMean, 15m | CW, one-sided positive |
| S4 | v1 versus Zero, 15m | DM, two-sided; accompanied by descriptive MSE/MAE |
| S5 | v1, 15m | IC-HAC, one-sided positive |

An undefined secondary p-value keeps its family slot as 1 for Holm and remains null with an explanation in the raw output. All fixed lag sensitivities, 60/275/LLSW, and both n-weighted/month-equal poolings are reported without selecting the most favorable. R²_OOS is computed from the ratio of total squared-error sums, not by averaging monthly R² values.

Three prespecified cross-month sensitivities remain outside Holm: monthly-mean HAC with fixed lag 4; circular block bootstrap of the 56 monthly CW means using all block lengths 3/6/12, 2,000 draws each and percentile 95% intervals; and month-equal versus n-weighted CW mean/t comparisons. They do not replace the primary decision rule.

Conditional GW regresses primary monthly DM means on an intercept and previous-calendar-month realized volatility. The instrument uses only 1m targets with `label_available_time <= fit_time`, accessing target values and validity only after metadata filtering. The diagnostic reports OLS coefficients, Bartlett HAC SE/t with lag 4 and joint Wald p, tagged `exploratory_not_in_holm`; it supports no confirmatory claim. The fluctuation diagnostic reports signed and absolute 12-month trailing CW means without p-values or significance claims. Placebos, concentration, encompassing and all yearly/half-year summaries remain descriptive.

## Conditional power planning

The source is the unchanged Phase 2E [evidence table](phase2e-evidence/evidence-layers.csv), SHA-256 `723ab3a5563f27157c54a3490a52919eb51c44fed4866f5192cd443f4c71bdc7`. Four Zero/LLSW/n-weighted rows supply CW, DM and IC means and standard errors. For 15m Validation, CW SE is `4.330445321942128e-09`; the distinct DM SE is `4.326795335294619e-09`.

Every extrapolation assumes independent identically distributed months, stable effects and stable variance **[H]**. Shared expanding training, heavy tails and nonstationarity can violate these assumptions. The formulas are `SE_56 = SE_2E*sqrt(2/56)`, `MDE = (z_alpha+z_power)*SE_56` and `%observed = 100*MDE/abs(observed_mean)`. At alpha 0.05 and power 0.8, CW/IC use the one-sided critical value `1.6448536269514722`, DM uses the two-sided value `1.959963984540054`, and `z_power = 0.8416212335729143`. These marginal planning calculations do not claim Holm-adjusted family power.

CW and DM SE/MDE values have squared-return units. IC-HAC SE/MDE values are dimensionless. Percentages compare each test with its own observed effect; raw MDE values from different units are not comparable.

| Horizon | Phase 2E role | Test | SE at 56 months | MDE at 56 months | % of own observed effect |
| ---: | --- | --- | ---: | ---: | ---: |
| 15 | Validation | CW, one-sided | 8.1838e-10 | 2.0349e-09 | 19.014% |
| 15 | Validation | DM, two-sided | 8.1769e-10 | 2.2908e-09 | 65.769% |
| 15 | Validation | IC-HAC, one-sided | 2.0809e-03 | 5.1740e-03 | 15.501% |
| 15 | Development evaluation | CW, one-sided | 9.0394e-10 | 2.2476e-09 | 23.381% |
| 15 | Development evaluation | DM, two-sided | 9.1138e-10 | 2.5533e-09 | 141.138% |
| 15 | Development evaluation | IC-HAC, one-sided | 1.9698e-03 | 4.8978e-03 | 18.740% |
| 5 | Validation | CW, one-sided | 1.6329e-10 | 4.0603e-10 | 35.720% |
| 5 | Validation | DM, two-sided | 1.6792e-10 | 4.7043e-10 | 88.423% |
| 5 | Validation | IC-HAC, one-sided | 1.3414e-03 | 3.3354e-03 | 29.663% |
| 5 | Development evaluation | CW, one-sided | 1.4585e-10 | 3.6265e-10 | 25.281% |
| 5 | Development evaluation | DM, two-sided | 1.4813e-10 | 4.1499e-10 | 2073.524%* |
| 5 | Development evaluation | IC-HAC, one-sided | 1.2466e-03 | 3.0997e-03 | 21.948% |

*The 5m Development DM observed mean is approximately `-2.0014e-11`, near zero, making observed-effect-relative planning uninformative. Its row and denominator are retained. The arithmetic MDE remains defined; CW and IC planning inputs remain separately available.

The full-precision planning anchors below are copied from the frozen contract, not new historical observations. Machine checks use relative tolerance `1e-12`; the rounded display table uses `1e-4`.

```csv
key,se_2E,se_56,mde_56,pct_obs,obs_mean
15_validation_CW,4.330445321942128e-09,8.183772420015648e-10,2.034874438662173e-09,19.01395494184081,1.0702005158244944e-08
15_validation_DM,4.326795335294619e-09,8.176874593617067e-10,2.290821099184106e-09,65.76901835183048,-3.4831310495306305e-09
15_validation_IC,0.01101095402994968,0.0020808747186293786,0.005174042675772705,15.501303730194117,0.033378113001517926
15_development_evaluation_CW,4.783212507512544e-09,9.039421973465615e-10,2.247629549069399e-09,23.38055282897031,9.613243816392623e-09
15_development_evaluation_DM,4.8225709984444895e-09,9.113802529883271e-10,2.5533094448521543e-09,141.13818846342443,-1.8090847506618222e-09
15_development_evaluation_IC,0.010423137780364284,0.0019697878891289764,0.004897828066884597,18.7398434923726,0.026135906998786235
5_validation_CW,8.640729996716182e-10,1.6329444798119267e-10,4.060275397684427e-10,35.7203885037303,1.1366828771362356e-09
5_validation_DM,8.885343560209244e-10,1.6791720981202088e-10,4.704343728761316e-10,88.42313825448302,5.320263249673575e-10
5_validation_IC,0.00709812709482857,0.0013414199333746987,0.003335406941742485,29.663262229627513,0.011244235094315075
5_development_evaluation_CW,7.717520543806254e-10,1.4584742926388075e-10,3.6264596633674817e-10,25.28135242840264,1.4344405322609606e-09
5_development_evaluation_DM,7.838104675873235e-10,1.4812625516037933e-10,4.1498832687174854e-10,2073.5237704055958,-2.001367588810298e-11
5_development_evaluation_IC,0.0065965028843915086,0.0012466218682014418,0.0030996939358628297,21.94780824033855,0.014123022681443914
```

These values are conditional ex-ante planning inputs, not rejection, stopping or acceptance criteria for market results. A future separately authorized report must highlight an actual CW SE differing by more than a factor of two from its planning expectation, without changing the primary test or tuning the model. A synthetic run and its reproduction do not estimate historical market power.

## Synthetic checks and D-023

The fixed engineering fixture consists of 68 independent monthly episodes of 4,096 consecutive minute bars: 278,528 generated bars, with calendar gaps preserved. Returns follow AR(1) with phi 0.2 and GARCH(1,1) innovations using standardized Student-t5 draws, omega `1e-8`, alpha 0.05 and beta 0.9. Each episode discards 1,024 burn-in steps. Master seed 42 and separate `SeedSequence` streams fix reproducibility. Sparse episodes exercise plumbing and cannot establish full-history runtime, memory use or market inference.

The CLI supports synthetic-only execution. A complete run has exactly `56*2*2 = 224` Ridge fits; one unchanged reproduction is separately counted. Per-run limits are 1,200 seconds and 4,000,000,000 measured peak process bytes. Deterministic products must agree byte for byte across the two runs; runtime and memory measurements remain in a separate nondeterministic record. Actual completion and results are reported only in the execution record.

[D-023](../research/DECISION_LOG.md#d-023-phase-3a-稳定性测试断言多重比较校正--mc-known-secodex-a-第-13-处拦截) authorizes a correction to stability **test acceptance**, with no change to the production fluctuation calculation or frozen artifact bytes. The frozen plan retains its original pointwise three-SE paragraph and the frozen configuration retains that old setting; this report explicitly records that the subsequent instruction governs the test. The superseded text is not silently rewritten.

The revised test retains both fixed coefficient trajectories, the break at month 25, trailing width 12 and existing seeds. It estimates each regime's rolling-mean SD from at least 50,000 single-regime MC draws and uses the D-023 weighted mixed-regime SE rule. The acceptance bound is `norm.ppf(1 - 0.01/(2*T))`, where `T` is the number of full-window fluctuation values. The 56-month fixture has 45 such values. This stipulated Bonferroni rule and approximate MC SE are engineering tolerances, not market significance output. AP10 reports T, bound, maximum absolute standardized error and exceedance count; the midpoint error must remain at most 6 months, and signed/absolute square consistency remains within `1e-18`. The execution record retains the original failure and the results under the authorized correction.

## Offline transfer plan and protected data

The [download draft](phase3a-download-manifest-draft.json) lists 68 official-pattern archive and checksum URLs, all `not_yet_downloaded`. Each month passes the historical allowlist and holdout guard before URL construction. Availability, sizes and checksums are unverified; planning generates no request.

| Planning quantity | Limit or unverified estimate |
| --- | ---: |
| Aggregate ZIP size estimate | 142,606,336–320,864,256 bytes (136–306 MiB) |
| Cumulative ZIP transfer cap, including retries | 402,653,184 bytes (384 MiB) |
| Cumulative checksum cap | 1,048,576 bytes (1 MiB) |
| Required free disk reserve | 8,589,934,592 bytes (8 GiB) |
| Concurrent requests, if separately authorized later | 1 |
| Per-month retries and fixed backoff | 3; 1/4/16 seconds |

The timestamp gate distinguishes millisecond archives through 2024-12-31 from microsecond archives beginning 2025-01-01. Synthetic positive and reversed-unit cases, ambiguous magnitudes and mixed-unit checks validate this boundary without requesting archives. Existing Phase 2B download behavior and daily holdout guards remain unchanged. Any future ingestion requires separate owner authorization and official checksum enforcement; this budget provides no transfer permission.

Holdout-A `[2026-07-01,2026-09-01)` and Holdout-B `[2026-09-02,2026-11-01)` remain sealed. No real day in July–October 2026 may be accessed in this phase, including the September 1 technical-smoke day. No existing raw market cache is a substitute for download authorization.

## Environment and stop conditions

D-022 requires the preinstalled independent worktree venv. Do not run `uv sync`, install dependencies, change global packages or alter Git configuration. The baseline gate requires exactly 594 passing existing tests. Authorized metadata-only snapshots must show that main and Phase 2F venv file sizes/mtimes did not change; their Research Lead merge checklist remains separate. Protected source fingerprints, old tests, frozen artifacts, online code, `CLAUDE.md` and research state files remain immutable for this implementation.

Stop and record the reason on a failed acceptance anchor or environment/baseline gate, missing required state entry, failed synchronization gate, manifest/fingerprint mismatch, forbidden data/network attempt, inability to meet fixed computation budgets, or a newly discovered frozen-plan error. Do not repair a failure by changing frozen artifacts, seeds, candidate coverage or thresholds. Any new dependency requires a stop and Research Lead assessment. D-023 is a specific authorized test correction, not a general waiver of these rules.

No network request to market services, real-data execution, account access, simulated/live order, PnL claim, push, remote creation or PR is authorized. The final execution record must distinguish completed checks from outstanding work and disclose every deviation; protocol publication alone does not imply engineering acceptance or any historical predictive result.
