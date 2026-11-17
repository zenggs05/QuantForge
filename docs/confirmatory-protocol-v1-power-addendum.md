# Confirmatory protocol v1: exploratory power addendum

Prepared 2026-11-04 (Asia/Shanghai). Evidence level: **exploratory_post_hoc planning**, derived from already-seen March-June 2026 predictions. Confirmatory evidence: none. Tradability: not evaluated.

This addendum refers to [confirmatory protocol v1](confirmatory-protocol-v1.md), SHA-256 **`38ef1aae687b6cd1a9b8b59709b84047250e2a9f90396cb9089244133b38f1b9`**. It does not amend that frozen document, choose another primary hypothesis, change its significance level, or authorize Holdout access. **Holdout-A and Holdout-B remain sealed.**

## Target and source of the planning inputs

The future primary test remains 15m specification v1 Ridge versus Zero, Clark-West (CW), one-sided alpha 0.05, with July/August monthly Bartlett lags `max(60,ceil(1.3*sqrt(T_m)))`, n-weighted pooling and zero cross-month covariance. There is exactly one primary test. A rejection concerns predictive information, not lower realized MSE, profitability or execution.

Inputs below are from the completed Phase 2E formal run `results/power-reanalysis/20261104T052452783302Z-7d3a5497/`, specifically `role-tests.csv` and `mde-power.csv`. The unchanged reproduction is `results/power-reanalysis/20261104T052644575077Z-2ebd386a/`. [The Phase 2E report](phase2e-report.md) and [reproducibility record](phase2e-reproducibility.json) document full provenance. Tables select 15m, Ridge versus Zero, n-weighted, LLSW; these choices match the primary test's bandwidth rule rather than selecting the largest observed t.

| Already-observed calibration role | Months | Rows | Mean CW (return squared) | Pooled HAC SE | CW effective n | Cochran Q p |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Validation: March/April | 2 | 87,810 | 1.0702005158e-8 | 4.3304453219e-9 | 16,452.09 | 0.598530 |
| Development: May/June | 2 | 87,810 | 9.6132438164e-9 | 4.7832125075e-9 | 29,224.32 | 0.755648 |

Effective n is the frozen design-effect equivalent, `T*Var_IID(pooled_mean)/Var_HAC(pooled_mean)`, with monthly centering and the prescribed weights; it is not a count of independent market observations. Individual monthly lags are 275/271 for March/April and 275/271 for May/June. Q has only one degree of freedom here and little power; its non-rejection cannot establish stationary effects or variances.

## Normal-reference calculation and all effect scenarios

For a stipulated positive CW mean delta and fixed SE,

```text
power(delta, SE) = Phi(delta/SE - z_(1-alpha))
MDE_80 = (z_(1-alpha) + z_0.8) * SE
M_required(delta) = M_observed * (MDE_80 / delta)^2
alpha = 0.05; M_observed = 2
z_0.95 + z_0.8 = 2.486474860524386
```

Critical values use `scipy.stats.norm`. Effects are 0.5, 0.75 and 1.0 times each role's own observed CW mean. They are scenario assumptions, not estimated future effects. No scenario is chosen as the true effect.

The main calculation treats the future two-month test as having the same information content/SE as the observed two-month role. July/August has 62 calendar days versus 61 in each calibration pair; the tables deliberately do not claim an exact daily-length or volatility correction without holdout data. A naive sqrt-time adjustment would be a planning assumption, not a measured improvement.

| Calibration | SE sensitivity | Power at0.5x | Power at0.75x | Power at1.0x | MDE80, return squared |
| --- | --- | ---: | ---: | ---: | ---: |
| Validation | Pooled two-month SE | 34.12% | 58.26% | 79.57% | 1.0767543e-8 |
| Validation | Worst observed monthly SE | 18.95% | 30.96% | 45.44% | 1.7388076e-8 |
| Development | Pooled two-month SE | 26.11% | 44.53% | 64.24% | 1.1893338e-8 |
| Development | Worst observed monthly SE | 13.14% | 19.57% | 27.61% | 2.2754104e-8 |

The worst-month sensitivity substitutes the largest observed monthly SE directly: 6.993063e-9 for Validation and 9.151150e-9 for Development. It is a conservative stress scenario for the planned statistic, not an alternative estimate of the original pooled SE. Its use does not change the actual preregistered pooling.

| Calibration | SE sensitivity | Required months at0.5x | At0.75x | At1.0x |
| --- | --- | ---: | ---: | ---: |
| Validation | Pooled | 8.10 | 3.60 | 2.02 |
| Validation | Worst month | 21.12 | 9.39 | 5.28 |
| Development | Pooled | 12.24 | 5.44 | 3.06 |
| Development | Worst month | 44.82 | 19.92 | 11.20 |

These continuous month counts are variance-scaling approximations. An operational plan would round up and separately freeze its actual month grid. The present addendum does not extend Holdout-A, reuse another holdout, add stopping looks or change the rejection rule to obtain 80% power.

## Assumptions and sensitivity

- **Autocorrelation:** overlapping 15m labels and dependence in the CW products are represented by monthly Bartlett HAC at LLSW. The much smaller effective n than 87,810 raw observations matters. Alternative bandwidth results remain in Phase 2E evidence; they are not used to select the planned test.
- **Non-stationarity:** future variance, effect size, tail behavior or regime composition can differ. The pooled and worst-month SE scenarios expose some sensitivity, but neither bounds all future shifts. Two-month Q tests cannot validate stationarity; changing markets may make sqrt-month scaling optimistic even when Q does not reject.
- **Months and dependence:** required-month calculations assume independent identically distributed months, stable signal and inverse-square-root scaling of SE. Shared expanding training sets can induce cross-month dependence, while the frozen pooling convention sets it to zero. The calculation is conditional on that convention.
- **Post-selection:** observed effects came from already-seen periods and multiple earlier examinations. They may be upward biased. The three effect multipliers show shrinkage sensitivity; none removes selection bias or guarantees that the true future effect is positive.
- **Units:** CW has native return-squared units. The Phase 2E correlation-equivalent MDE divides by `2*sd(f-b)*sd(y-b)` and is only a zero-mean approximation; it is not the independently defined centered Pearson IC-HAC statistic. This addendum uses native CW units for the primary power calculation.
- **Evidence layers:** CW power is not DM/MSE power. Phase 2E's 15m LLSW two-sided DM relative-MSE MDE is 0.19957% for Validation and 0.26782% for Development using pooled SE. The tests answer different questions and are retained separately. Neither yields a claim about trading costs or profitability.

At the full observed effect and pooled SE, the two-month approximation ranges from 64.24% to 79.57%; at half the observed effect it is 26.11% to 34.12%. The worst-month scenarios are lower. A future non-rejection would therefore not establish absence of all predictive information. Equally, these scenarios do not predict that the future primary test will reject.

The preregistration remains unchanged. Execution still requires explicit written owner authorization citing its hash and verification of the frozen feature/label/model code fingerprints. Holdout-B requires its own separately approved protocol. No holdout, smoke-day research statistic or new model evaluation was used to prepare this addendum.
