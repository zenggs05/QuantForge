# Phase 2E: power-calibrated reanalysis and specification audit

Completed 2026-11-04. **Evidence level: engineering/mathematical verification plus exploratory_post_hoc statistics and training_period_diagnostic fits. Confirmatory evidence: none. Tradability: not evaluated.**

The frozen-prediction analysis reproduced A1-A7 and all earlier HAC anchors. It distinguishes predictive information from realized error: two 15m Validation CW tests survive the predeclared exploratory LLSW Holm-eight family, while the corresponding DM tests do not establish lower MSE. This is an additional look at already-seen data, not a new independent confirmation. No new model was evaluated on March-June.

## Protocol, scope and reproducibility [engineering verification]

The [analysis plan](phase2e-analysis-plan.md), [configuration](../configs/power-reanalysis.toml) and [unexecuted confirmatory protocol](confirmatory-protocol-v1.md) were hashed and committed before implementation. Both runs used clean implementation commit `56f26a998d81b35d019a726d432ca3e896ad0efd`. Formal run `20261104T052452783302Z-7d3a5497` took 79.008914 s, reproduction `20261104T052644575077Z-2ebd386a` took 74.527627 s. Peak process memory was 2,608,308,224 and 2,654,752,768 bytes, below 4,000,000,000. CPU float64, all numerical pools one thread, no GPU. The 6 Initial Train fits per run are separate from zero new evaluation fits/predictions. Source validation/loading 22.763307 s, reanalysis 44.626849 s, training audit 11.439158 s in the formal run; remaining time includes serialization.

All **19 deterministic artifacts** are byte-identical. Inputs, dependencies, code hashes, anchors and statistical values match. Only run ID/kind/path and elapsed time differ in summary; budget durations/memory differ separately. See [reproduction manifest](phase2e-reproducibility.json), [execution record](phase2e-record.md) and [aggregate evidence](phase2e-evidence/README.md). All original 484 plus 110 new tests passed (594 total, 97.42 s); initial 484 passed in 85.01 s. No existing test was edited.

## Mathematical and historical anchors [engineering/mathematical verification]

The new monthly/role DM path is checked against every legacy mean, variance, SE, z and p, not merely a rerun of the old implementation. All 96 Phase 2C and 144 Phase 2D-A historical rows, including 6 undefined window p-values, reproduce within 1e-12 relative tolerance. All 24 model/horizon/month MSE/MAE rows reproduce within 1e-12. New HAC delegates to the unchanged fixed-n Bartlett kernel. Normal p-values/critical values use scipy.stats.norm; Q uses its chi-square reference.

| name | actual | expected | absolute_tolerance | passed |
| --- | --- | --- | --- | --- |
| A1_relative_mse_pct | -0.0573456 | -0.057346 | 1e-06 | True |
| A1_abs_dm_z | 0.766912 | 0.7669 | 0.0005 | True |
| A2_cw | 2.35433 | 2.354 | 0.001 | True |
| A3_cw | 1.5753 | 1.575 | 0.001 | True |
| A5_ic | 0.0333781 | 0.03338 | 1e-05 | True |
| A4_legacy-hac-2c_rows | 96 | 96 | 0 | True |
| A4_legacy-hac-2da_rows | 144 | 144 | 0 | True |
| A4_min_p | 0.201193 | 0.201193 | 1e-10 | True |
| A4_window_nulls | 6 | 6 | 0 | True |
| A7_metrics_rows | 24 | 24 | 0 | True |
| A6_train_n_15m | 260564 | 260564 | 0 | True |
| A6_duplicate_corr_15m | 0.999934 | 0.99993 | 1e-05 | True |
| A6_condition | 51607.9 | 51607.9 | 0.05 | True |
| A6_shrinkage | 0.943326 | 0.94333 | 1e-05 | True |
| A6_train_n_5m | 260574 | 260574 | 0 | True |
| A6_duplicate_corr_5m | 0.999934 | 0.99993 | 1e-05 | True |

The maximum elementwise `-d = c-(f-b)^2` error is 3.9979955110402976e-19, below frozen 1e-18. CW native units are squared log return; correlation-equivalent scaling assumes approximately zero means and is not the centered Pearson IC statistic.

## Monte Carlo [synthetic]

Frozen 400 replications, two 44640-minute months, SV AR1(.98) with logvol SD .5, standardized t5 noise, independent normal signal, overlapping 15m labels, theoretical R2=.001 and known slope .6. Full-suite runtime 7.582784 s; empirical signal R2=.001002683. No DGP or size threshold adjustment followed results.

| scenario | lag | cw_one_sided | dm_two_sided | dm_improvement_one_sided | dm_improvement_t_mean |
| --- | --- | --- | --- | --- | --- |
| null | 60 | 0.065 | 0.385 | 0.0025 | -1.61385 |
| alternative | 60 | 0.915 | 0.31 | 0.415 | 1.46855 |
| null | 275 | 0.055 | 0.3675 | 0.0025 | -1.5797 |
| alternative | 275 | 0.9075 | 0.295 | 0.3925 | 1.4369 |

CW null rejection rates .065/.055 satisfy [.02, .09]. CW alternative power .915/.9075 exceeds both DM power reports. Under the no-information null, an independent noisy forecast has greater MSE than Zero; its DM equal-risk null is false. Therefore DM two-sided null-column rates .385/.3675 must **not** be described as DM size distortion. The requested negative null DM statistic refers to improvement direction `-DM_z`; formal tables retain model-minus-baseline DM sign.

## Four evidence layers [exploratory_post_hoc]

Primary display uses n-weighted LLSW (monthly lags 275/271). CW/IC p-values are one-sided; DM is two-sided. F_CW has exactly 8 tests. MSE/MAE changes are percentages of baseline error, not returns. Each row also has confirmatory_evidence=none and tradability=not evaluated.

| horizon_minutes | role | baseline | cw_t | cw_p_value | cw_holm_p | ic_mean | ic_t | r2_oos | relative_mse_delta_pct | relative_mae_delta_pct | dm_t | dm_p_value |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 15 | validation | zero | 2.47134 | 0.00673038 | 0.0471126 | 0.0333781 | 3.03136 | 0.000573456 | -0.0573456 | -0.116836 | -0.805014 | 0.420812 |
| 15 | development_evaluation | zero | 2.00979 | 0.0222268 | 0.133361 | 0.0261359 | 2.50749 | 0.000358611 | -0.0358611 | -0.0642003 | -0.375129 | 0.707565 |
| 5 | validation | zero | 1.31549 | 0.094172 | 0.204821 | 0.0112442 | 1.58411 | -0.000260806 | 0.0260806 | -0.035339 | 0.598768 | 0.549327 |
| 5 | development_evaluation | zero | 1.85868 | 0.0315362 | 0.157681 | 0.014123 | 2.14099 | 1.15421e-05 | -0.00115421 | -0.00608213 | -0.0255338 | 0.979629 |
| 15 | validation | historical_mean | 2.75142 | 0.00296686 | 0.0237349 | 0.0333781 | 3.03136 | 0.000853114 | -0.0853114 | -0.129136 | -1.23017 | 0.218633 |
| 15 | development_evaluation | historical_mean | 1.63328 | 0.0512052 | 0.204821 | 0.0261359 | 2.50749 | 0.000136728 | -0.0136728 | -0.0512417 | -0.137445 | 0.890679 |
| 5 | validation | historical_mean | 1.51875 | 0.0644127 | 0.204821 | 0.0112442 | 1.58411 | -0.000168566 | 0.0168566 | -0.0408797 | 0.406174 | 0.684614 |
| 5 | development_evaluation | historical_mean | 1.61904 | 0.0527193 | 0.204821 | 0.014123 | 2.14099 | -5.97441e-05 | 0.00597441 | -0.00452248 | 0.128344 | 0.897877 |

The significant exploratory CW family members are 15m Validation versus Zero (adjusted p = .047113) and Historical Mean (.023735). Development 15m versus Zero has raw p = .022227 but adjusted p = .133361. No corresponding realized-error advantage is established by DM. IC-HAC measures centered association; CW assesses its specified nested adjusted differential. Neither is an execution or profit test. Complete MSE/MAE levels, monthly IC and every bandwidth/pooling remain in [role-tests.csv](phase2e-evidence/role-tests.csv), [monthly-tests.csv](phase2e-evidence/monthly-tests.csv), and [baseline-metrics.csv](phase2e-evidence/baseline-metrics.csv).

## Bandwidth and concentration [exploratory_post_hoc]

Lag 15 is inadmissible for both horizons under L >= 4h and retained only as historical comparison. L = 60/240/LLSW and both pooling choices are reported without selecting a favorable result. For 15m versus Zero, CW changes from 2.354331 to 2.471341 in Validation, and 1.575300 to 2.009788 in Development between L = 60 and LLSW; this sensitivity matters.

| model | role | bandwidth | relative_mse_delta_pct | dm_t | dm_p_value | dm_holm_p |
| --- | --- | --- | --- | --- | --- | --- |
| rolling_1m | validation | 15 | 0.215123 | 1.29628 | 0.194879 | 0.739881 |
| rolling_1m | validation | 60 | 0.215123 | 0.987663 | 0.323318 | 1 |
| rolling_1m | validation | 240 | 0.215123 | 0.878739 | 0.379543 | 1 |
| rolling_1m | validation | LLSW | 0.215123 | 0.875662 | 0.381214 | 1 |
| rolling_1m | development_evaluation | 15 | 0.309721 | 2.2364 | 0.0253258 | 0.151955 |
| rolling_1m | development_evaluation | 60 | 0.309721 | 2.32073 | 0.0203014 | 0.121809 |
| rolling_1m | development_evaluation | 240 | 0.309721 | 2.65559 | 0.00791693 | 0.0475016 |
| rolling_1m | development_evaluation | LLSW | 0.309721 | 2.6816 | 0.00732711 | 0.0439626 |
| rolling_3m | validation | 15 | 0.125869 | 1.33145 | 0.183042 | 0.739881 |
| rolling_3m | validation | 60 | 0.125869 | 1.09657 | 0.272831 | 1 |
| rolling_3m | validation | 240 | 0.125869 | 1.03293 | 0.301636 | 1 |
| rolling_3m | validation | LLSW | 0.125869 | 1.03138 | 0.302361 | 1 |
| rolling_3m | development_evaluation | 15 | 0.15491 | 1.44672 | 0.147976 | 0.739881 |
| rolling_3m | development_evaluation | 60 | 0.15491 | 1.36805 | 0.171296 | 0.856481 |
| rolling_3m | development_evaluation | 240 | 0.15491 | 1.61129 | 0.107116 | 0.535582 |
| rolling_3m | development_evaluation | LLSW | 0.15491 | 1.63827 | 0.101366 | 0.506831 |
| rolling_6m | validation | 15 | 0.0127079 | 1.28682 | 0.198158 | 0.739881 |
| rolling_6m | validation | 60 | 0.0127079 | 1.12333 | 0.261297 | 1 |
| rolling_6m | validation | 240 | 0.0127079 | 1.09037 | 0.275549 | 1 |
| rolling_6m | validation | LLSW | 0.0127079 | 1.09143 | 0.275085 | 1 |
| rolling_6m | development_evaluation | 15 | -0.0293906 | -0.346211 | 0.729184 | 0.739881 |
| rolling_6m | development_evaluation | 60 | -0.0293906 | -0.345381 | 0.729808 | 1 |
| rolling_6m | development_evaluation | 240 | -0.0293906 | -0.371199 | 0.71049 | 1 |
| rolling_6m | development_evaluation | LLSW | -0.0293906 | -0.373514 | 0.708766 | 1 |

Holm-six is independently applied at each bandwidth to 15m x 3 windows x 2 roles. Rolling 1M Development becomes significant at L = 240/LLSW (adjusted p = .047502/.043963), with **higher** MSE by .309721%; at L = 60 adjusted p = .121809. This is post-hoc, bandwidth-sensitive evidence of worse realized error, not an improvement claim. All 5m and month-equal comparisons remain in the CSV.

Concentration selects top absolute contributions at k=1/10/100 with deterministic timestamp ties. Deletion preserves actual minute lags; missing centered grid positions contribute zero to lag products and the retained sample count is the denominator. It does not concatenate neighbors across removed minutes. All 672 cases retain signed shares, new mean/t and sign-flip status; 76 signs flip across the complete diagnostic set, often where the original DM mean is close to zero. For 15m Ridge-Zero CW at LLSW none of the monthly top-k deletions flips the sign, but that does not establish general robustness. Signed shares may exceed 100% due to cancellation. See [concentration.csv](phase2e-evidence/concentration.csv).

## Placebos [exploratory_post_hoc]

P1 uses 200 deterministic UTC-hour Rademacher draws with frozen SeedSequence; plus-one empirical p. All 8 diagnostics pass: maximum absolute t mean .128043, SD range .968454-1.070165. Thus no P1 pipeline stop is triggered. This is a diagnostic, not proof that every confound is absent. P2 aligns exact UTC timestamps with targets 1440 minutes later inside each month and discards the unavailable tail; no wraparound or compressed-gap shift.

| horizon_minutes | role | bandwidth | observed_t | placebo_t_mean | placebo_t_sd | empirical_one_sided_p | pipeline_warning |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 15 | validation | 60 | 2.35433 | 0.0483685 | 1.04275 | 0.0199005 | False |
| 15 | validation | LLSW | 2.47134 | 0.0459844 | 1.02907 | 0.0149254 | False |
| 15 | development_evaluation | 60 | 1.5753 | 0.0370301 | 0.997919 | 0.079602 | False |
| 15 | development_evaluation | LLSW | 2.00979 | 0.0470093 | 1.00753 | 0.039801 | False |
| 5 | validation | 60 | 1.25869 | -0.0119552 | 1.06083 | 0.124378 | False |
| 5 | validation | LLSW | 1.31549 | -0.0103226 | 1.07017 | 0.119403 | False |
| 5 | development_evaluation | 60 | 1.50753 | -0.127797 | 0.99876 | 0.0547264 | False |
| 5 | development_evaluation | LLSW | 1.85868 | -0.128043 | 0.968454 | 0.0199005 | False |

| horizon_minutes | role | bandwidth | shifted_t | shifted_p | shifted_n_samples |
| --- | --- | --- | --- | --- | --- |
| 15 | validation | 60 | -0.815461 | 0.792596 | 84930 |
| 15 | validation | LLSW | -0.772895 | 0.780208 | 84930 |
| 15 | development_evaluation | 60 | -0.582604 | 0.71992 | 84930 |
| 15 | development_evaluation | LLSW | -0.550014 | 0.708845 | 84930 |
| 5 | validation | 60 | -0.518183 | 0.697835 | 84950 |
| 5 | validation | LLSW | -0.518211 | 0.697845 | 84950 |
| 5 | development_evaluation | 60 | -0.135216 | 0.55378 | 84950 |
| 5 | development_evaluation | LLSW | -0.133438 | 0.553077 | 84950 |

P2 t is negative in every reported cell; no positive-information claim follows. Quantiles and exact lag/sample counts are preserved in [placebo.csv](phase2e-evidence/placebo.csv).

## MDE, power and assumptions [exploratory_post_hoc planning]

The complete 1728-row [MDE/power table](phase2e-evidence/mde-power.csv) reports native units, relative-MSE units for DM, correlation-equivalent CW units and IC units; pooled and worst-month SE; and 0.5/.75/1x beneficial observed effects. Negative/zero beneficial effects have no finite required months. DM two-sided power means detecting any difference, potentially worse forecasts, rather than power to establish an improvement.

| role | series | n_eff | mde_native | mde_relative_mse_pct | effect_factor | months_required | power |
| --- | --- | --- | --- | --- | --- | --- | --- |
| validation | dm | 16581.9 | 1.21219e-08 | 0.199572 | 0.5 | 96.8926 | 0.0687578 |
| validation | dm | 16581.9 | 1.21219e-08 | 0.199572 | 0.75 | 43.0634 | 0.092695 |
| validation | dm | 16581.9 | 1.21219e-08 | 0.199572 | 1 | 24.2232 | 0.126902 |
| validation | cw | 16452.1 | 1.07675e-08 | null | 0.5 | 8.09828 | 0.341203 |
| validation | cw | 16452.1 | 1.07675e-08 | null | 0.75 | 3.59924 | 0.58264 |
| validation | cw | 16452.1 | 1.07675e-08 | null | 1 | 2.02457 | 0.795736 |
| development_evaluation | dm | 28197.7 | 1.35108e-08 | 0.267823 | 0.5 | 446.208 | 0.0540397 |
| development_evaluation | dm | 28197.7 | 1.35108e-08 | 0.267823 | 0.75 | 198.315 | 0.0591163 |
| development_evaluation | dm | 28197.7 | 1.35108e-08 | 0.267823 | 1 | 111.552 | 0.0662712 |
| development_evaluation | cw | 29224.3 | 1.18933e-08 | null | 0.5 | 12.245 | 0.261099 |
| development_evaluation | cw | 29224.3 | 1.18933e-08 | null | 0.75 | 5.44221 | 0.445313 |
| development_evaluation | cw | 29224.3 | 1.18933e-08 | null | 1 | 3.06124 | 0.64242 |

**Assumptions and sensitivity:** autocorrelation is represented by each declared monthly HAC bandwidth and n_eff (a design-effect equivalent, not an actual independent sample count). Role variances set cross-month covariance to zero despite shared training history. Cochran Q uses inverse-HAC-variance weights; two-month non-rejection has weak power and does not establish stationarity. Worst-month SE is used directly as a conservative sensitivity, without quietly scaling it down. Square-root-month extrapolation assumes comparable independent identically distributed months; heterogeneity or persistent dependence makes it optimistic. Observed effects are post-hoc and selected; 0.5/.75/1x scenarios are assumptions, not confidence bounds.

For 15m Validation CW/Zero/LLSW, pooled SE 4.330445e-9, n_eff 16452.09, MDE 1.076754e-8. Required months 8.10/3.60/2.02 for .5/.75/1x rise to 21.12/9.39/5.28 under worst-month SE. Development pooled requires 12.24/5.44/3.06 months. In contrast, DM MDEs are .199572% and .267823% of baseline MSE; at 1x observed improvement the planning counts are 24.22 and 111.55 months. These endpoint-dependent values cannot be substituted for one another.

## Encompassing calibration and hypothetical grid [exploratory_post_hoc planning]

The approximation uses centered monthly covariance summaries, observed n_eff,d and the supplied formula. Its positive sign is improvement, so comparison is against minus the formal DM z. Across role comparisons, absolute discrepancy reaches 2.474; omitted mean terms and heterogeneous products make the approximation crude. It is not a replacement for actual HAC, nor a finding about online updating.

| horizon_minutes | window | role | bandwidth | rho | t_approx | actual_improvement_t | absolute_approximation_error |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 15 | rolling_1m | validation | 60 | 0.017281 | -0.974195 | -0.987663 | 0.0134683 |
| 15 | rolling_1m | validation | LLSW | 0.017281 | -0.863721 | -0.875662 | 0.011941 |
| 15 | rolling_1m | development_evaluation | 60 | -0.00785905 | -4.2473 | -2.32073 | 1.92657 |
| 15 | rolling_1m | development_evaluation | LLSW | -0.00785905 | -4.90775 | -2.6816 | 2.22615 |
| 5 | rolling_1m | validation | 60 | 0.00877606 | -1.95202 | -1.30235 | 0.649664 |
| 5 | rolling_1m | validation | LLSW | 0.00877606 | -1.80606 | -1.20498 | 0.601088 |
| 5 | rolling_1m | development_evaluation | 60 | 0.00374404 | -4.53537 | -2.0792 | 2.45616 |
| 5 | rolling_1m | development_evaluation | LLSW | 0.00374404 | -4.56868 | -2.09448 | 2.4742 |
| 15 | rolling_3m | validation | 60 | 0.00566046 | -1.49726 | -1.09657 | 0.400697 |
| 15 | rolling_3m | validation | LLSW | 0.00566046 | -1.40826 | -1.03138 | 0.376878 |
| 15 | rolling_3m | development_evaluation | 60 | 0.00577584 | -1.13643 | -1.36805 | 0.231619 |
| 15 | rolling_3m | development_evaluation | LLSW | 0.00577584 | -1.3609 | -1.63827 | 0.277368 |
| 5 | rolling_3m | validation | 60 | 0.00913254 | -1.06379 | -0.711035 | 0.352757 |
| 5 | rolling_3m | validation | LLSW | 0.00913254 | -1.08315 | -0.723976 | 0.359177 |
| 5 | rolling_3m | development_evaluation | 60 | 0.0036204 | -2.3535 | -1.54653 | 0.806969 |
| 5 | rolling_3m | development_evaluation | LLSW | 0.0036204 | -2.69534 | -1.77116 | 0.924178 |
| 15 | rolling_6m | validation | 60 | 0.00935754 | 0.763131 | -1.12333 | 1.88646 |
| 15 | rolling_6m | validation | LLSW | 0.00935754 | 0.741457 | -1.09143 | 1.83288 |
| 15 | rolling_6m | development_evaluation | 60 | 0.018908 | 0.62172 | 0.345381 | 0.276338 |
| 15 | rolling_6m | development_evaluation | LLSW | 0.018908 | 0.672361 | 0.373514 | 0.298847 |
| 5 | rolling_6m | validation | 60 | -0.00178386 | -0.46614 | -2.3077 | 1.84156 |
| 5 | rolling_6m | validation | LLSW | -0.00178386 | -0.438178 | -2.16927 | 1.73109 |
| 5 | rolling_6m | development_evaluation | 60 | 0.026612 | 2.71969 | 1.65253 | 1.06715 |
| 5 | rolling_6m | development_evaluation | LLSW | 0.026612 | 2.70004 | 1.6406 | 1.05945 |

The complete 384-row [grid](phase2e-evidence/encompassing-grid.csv) keeps all rho=.0025/.005/.01/.02 and delta/forecast-SD=.01/.03/.1/.3 cases for every existing calibration reference. The following is the 15m Validation LLSW grid using Rolling 3M's observed dependence as a transparent example, not a selected schedule or an experiment:

| rho | delta_ratio | n_eff_d | t_approx | target_t | months_required |
| --- | --- | --- | --- | --- | --- |
| 0.0025 | 0.01 | 10552.9 | 0.240179 | 2.48647 | 214.352 |
| 0.0025 | 0.03 | 10552.9 | 0.2069 | 2.48647 | 288.852 |
| 0.0025 | 0.1 | 10552.9 | 0.0904249 | 2.48647 | 1512.25 |
| 0.0025 | 0.3 | 10552.9 | -0.242363 | 2.48647 | null |
| 0.005 | 0.01 | 10552.9 | 0.496998 | 2.48647 | 50.0598 |
| 0.005 | 0.03 | 10552.9 | 0.463719 | 2.48647 | 57.5027 |
| 0.005 | 0.1 | 10552.9 | 0.347243 | 2.48647 | 102.549 |
| 0.005 | 0.3 | 10552.9 | 0.014456 | 2.48647 | 59169.7 |
| 0.01 | 0.01 | 10552.9 | 1.01063 | 2.48647 | 12.1062 |
| 0.01 | 0.03 | 10552.9 | 0.977356 | 2.48647 | 12.9447 |
| 0.01 | 0.1 | 10552.9 | 0.860881 | 2.48647 | 16.6845 |
| 0.01 | 0.3 | 10552.9 | 0.528093 | 2.48647 | 44.3381 |
| 0.02 | 0.01 | 10552.9 | 2.03791 | 2.48647 | 2.97734 |
| 0.02 | 0.03 | 10552.9 | 2.00463 | 2.48647 | 3.07701 |
| 0.02 | 0.1 | 10552.9 | 1.88816 | 2.48647 | 3.46835 |
| 0.02 | 0.3 | 10552.9 | 1.55537 | 2.48647 | 5.11131 |

Target t is computed exactly from norm.ppf(.95)+norm.ppf(.8)=2.4864748605. Nonpositive approximate improvement has no finite planning count. No actual within-month update model was fitted or evaluated.

## Specification audit [training_period_diagnostic]

All 6 model fits and in-sample predictions are restricted to March's original mature Initial Train. No new feature set or model was scored on March-June. Both horizons identify only log_return_1m/log_close_open above .999 correlation; the rule removes the later canonical column log_close_open, leaving 13 features. This is a fixed training-based specification rule, not a validated superior model.

| horizon | window | n | covariance_condition | ridge_condition | weakest_shrinkage |
| --- | --- | --- | --- | --- | --- |
| 15 | six_month | 260564 | 54708.4 | 51607.9 | 0.943326 |
| 15 | three_month | 129585 | 479492 | 232717 | 0.485339 |
| 15 | one_month | 40305 | 375558 | 101924 | 0.271392 |
| 5 | six_month | 260574 | 54709.5 | 51609 | 0.943327 |
| 5 | three_month | 129595 | 479510 | 232729 | 0.485346 |
| 5 | one_month | 40315 | 375592 | 101945 | 0.271423 |

| horizon | specification | n | prediction_sd_bp | condition | weakest_shrinkage | return_1m_coef_bp | close_open_coef_bp |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 15 | v1 | 260564 | 1.73127 | 51607.9 | 0.943326 | 115.443 | -115.562 |
| 15 | v1_crash_excluded | 260324 | 0.467761 | 326958 | 0.638465 | -9.38745 | 9.23129 |
| 15 | v2 | 260564 | 1.13614 | 33.8578 | 0.999963 | -0.123682 | null |
| 5 | v1 | 260574 | 0.798128 | 51609 | 0.943327 | 60.2905 | -60.463 |
| 5 | v1_crash_excluded | 260334 | 0.277723 | 326967 | 0.638467 | -7.00512 | 6.86622 |
| 5 | v2 | 260574 | 0.412095 | 33.8575 | 0.999963 | -0.175168 | null |

| horizon | quantity | requested_k | share |
| --- | --- | --- | --- |
| 15 | gap_squared | 1 | 0.189192 |
| 15 | gap_squared | 10 | 0.852361 |
| 15 | gap_squared | 100 | 0.951078 |
| 15 | gap_squared | 1000 | 0.992316 |
| 15 | gap_y | 1 | 0.374839 |
| 15 | gap_y | 10 | 0.991063 |
| 15 | gap_y | 100 | 0.987437 |
| 15 | gap_y | 1000 | 1.0007 |
| 5 | gap_squared | 1 | 0.189192 |
| 5 | gap_squared | 10 | 0.852361 |
| 5 | gap_squared | 100 | 0.951078 |
| 5 | gap_squared | 1000 | 0.992316 |
| 5 | gap_y | 1 | 0.458126 |
| 5 | gap_y | 10 | 1.02715 |
| 5 | gap_y | 100 | 0.995954 |
| 5 | gap_y | 1000 | 0.999202 |

The gap identity maximum error is 1.862727e-15 and zero-gap share is about 51.8997%. Top 10 minutes contribute 85.2361% of gap squared mass and 99.1063% of signed gap*y for 15m (102.7151% for 5m). The 240-minute crash session contributes 90.19075% of gap squared mass and 101.0565%/101.7784% of signed gap*y for 15m/5m; signed percentages above 100 are possible. Top timestamps, full 14x14 correlation matrices, every eigenvalue/shrinkage, all coefficients (bp per SD) and quantiles are retained in [specification-audit.json](phase2e-evidence/specification-audit.json).

Excluding the crash reverses the two gap-direction coefficients and increases the ill-conditioning of v1; dropping the redundant column improves algebraic conditioning. Neither observation establishes evaluation performance or justifies choosing a model based on March-June scores. The audit has no such scores.

## Prediction magnitude versus illustrative thresholds [exploratory_post_hoc]

| horizon_minutes | month | p50_bp | p90_bp | p99_bp | max_bp | fraction_ge_1bp | fraction_ge_2bp | fraction_ge_5bp | fraction_ge_10bp | fraction_ge_15bp | fraction_ge_20bp |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 5 | 2026-03 | 0.221817 | 0.582235 | 1.64168 | 13.9005 | 0.0306262 | 0.00555618 | 0.000380867 | 2.24039e-05 | 0 | 0 |
| 5 | 2026-04 | 0.207999 | 0.476994 | 1.10117 | 12.1341 | 0.0129645 | 0.00212988 | 4.63017e-05 | 2.31508e-05 | 0 | 0 |
| 5 | 2026-05 | 0.181748 | 0.399828 | 0.840284 | 31.0253 | 0.00629551 | 0.00118741 | 6.72118e-05 | 2.24039e-05 | 2.24039e-05 | 2.24039e-05 |
| 5 | 2026-06 | 0.191803 | 0.520187 | 1.39203 | 24.3981 | 0.024864 | 0.00384304 | 0.00027781 | 6.94525e-05 | 4.63017e-05 | 2.31508e-05 |
| 15 | 2026-03 | 0.481994 | 1.24862 | 3.21296 | 18.7711 | 0.166387 | 0.0313277 | 0.00271148 | 0.000291317 | 8.96359e-05 | 0 |
| 15 | 2026-04 | 0.478624 | 1.08354 | 2.34599 | 21.6186 | 0.127799 | 0.0149126 | 0.000926248 | 4.63124e-05 | 2.31562e-05 | 2.31562e-05 |
| 15 | 2026-05 | 0.431519 | 1.00733 | 2.07577 | 60.3965 | 0.101804 | 0.0115182 | 0.000717087 | 0.000134454 | 6.72269e-05 | 4.48179e-05 |
| 15 | 2026-06 | 0.433698 | 1.22716 | 3.44459 | 47.6498 | 0.15246 | 0.0373278 | 0.00365868 | 0.00048628 | 0.000162093 | 9.26248e-05 |

Fractions are proportions, not percentages. Thresholds 1/2/5/10/15/20 bp are illustrative scenarios, not verified current Binance fees. Close-to-close labels are not executable returns; comparing a forecast magnitude with a threshold is neither PnL nor an execution model. No spread, impact, fill, turnover or net-return calculation is present.

## Confirmation and future history [confirmatory evidence: none]

The [confirmatory protocol v1](confirmatory-protocol-v1.md) SHA-256 is `38ef1aae687b6cd1a9b8b59709b84047250e2a9f90396cb9089244133b38f1b9`. It freezes one 15m v1 CW-versus-Zero primary test on Holdout-A, monthly L=max(60,LLSW), n-weighted pooling, one-sided p < .05. Its five-test secondary Holm family remains nonconfirmatory. No power result was inserted into the frozen protocol; the separate [power addendum](confirmatory-protocol-v1-power-addendum.md) records conditional scenarios. Smoke-day boundary-price and historical metadata exposures remain disclosed. Holdout-A/B are still sealed and require explicit future authorization; protocol hashes alone do not authorize access.

The [Phase 3A proposal](phase3a-history-extension-proposal.md) is an unfrozen draft for strictly forward historical evaluation, not download authorization. A power argument does not remove nonstationarity, licensing, data-quality or computational constraints. The Research Lead's state files are deliberately unchanged.

## Limitations and preserved negative results

Every real result above is post-hoc or training-period diagnostic. The two-month normal/HAC and month-independence approximations may be weak under nonstationarity/heavy tails; Monte Carlo validates only its declared synthetic DGP. CW information evidence does not imply lower realized error, generalization, an alpha strategy, or execution. Encompassing is approximate; concentration and bandwidth sensitivities are retained. Existing online-engine and publication-preparation issues listed by the Research Lead are outside this phase and remain unresolved. No frozen predecessor file or original predictor behavior was changed; the only old-code edits are the explicitly authorized daily safety gates.
