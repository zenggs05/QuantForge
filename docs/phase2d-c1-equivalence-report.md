# Phase 2D-C1 numerical equivalence report

Checked 2026-11-03. All synthetic mathematical tests and all **10 real batch checkpoints passed** the unchanged [frozen protocol](phase2d-c1-analysis-plan.md). This establishes a numerical foundation for one prescribed Ridge objective; it does not test improved alpha. See [reproducibility evidence](phase2d-c1-reproducibility.json) and [raw aggregate comparisons](phase2d-c1-evidence/batch-equivalence.csv).

## Objective, initialization and implementation

For each horizon, fit StandardScaler on the original March split's training observations whose labels are mature by `2026-03-01T00:00:00Z`. Thereafter fix its mean, scale and variance. The 15m initial set has 260,564 rows; the 5m set has 260,574 rows. Their feature order and scaler arrays exactly match the frozen March baseline. Zero/near-constant columns retain sklearn's scale handling.

For `z_i=(x_i-mean_initial)/scale_initial`, solve

```text
minimize_(w,c) sum_i (y_i - z_i @ w - c)^2 + alpha * ||w||_2^2
alpha = 1.0
A = sum_zz - outer(sum_z, sum_z)/n + alpha*I
b = sum_zy - sum_z*sum_y/n
w = solve(A, b)
c = sum_y/n - dot(sum_z/n, w)
```

The intercept is unpenalized. The implementation maintains `n, sum_z, sum_y, sum_zz, sum_zy` and compensation terms in float64. Single-label statistics updates are O(p²), p=14; they never rescan the training history. Solving is a distinct O(p³) operation with separate counters. Prediction refuses a dirty, unsolved state. No inverse, forgetting, jitter, adaptive alpha or fallback refit is used.

Each solve symmetrizes the system, computes an eigenspectrum diagnostic, checks the frozen gates and uses a Cholesky factorization. A nonfinite/nonpositive system or condition number above 1e12 fails. Centered-scatter eigenvalues below `-1e-10*max(1,||sum_zz||F)` also fail, even if the penalized matrix is positive definite. A failed solve preserves the last solved coefficients/version but leaves the statistics dirty; the replay engine must recover a prior complete checkpoint instead of continuing ambiguously.

The independent batch oracle is `sklearn.linear_model.Ridge(alpha=1, fit_intercept=True, solver="svd")` on **the same fixed transformed features and exactly the same mature sample indices**. It does not reuse the incremental normal-equation solver. Batch refits are audit work and are charged separately. The original Phase 2B model refits its scaler monthly: after March initialization, that is a different regularized objective and must not be treated as the same numerical oracle.

## Frozen acceptance criteria

Before implementation/real outcomes, plan SHA-256 `7876d1da1d867fa91184bdcfa2f3c2144142acc2ee9d70bbe6cc54a271d18752` fixed:

| Quantity | Acceptance |
| --- | --- |
| Each coefficient | `abs(delta) <= 1e-9 + 1e-8*abs(batch_coef)` |
| Intercept | `abs(delta) <= 1e-10 + 1e-8*abs(batch_intercept)` |
| Prediction maximum absolute error | `<= 1e-9` |
| Prediction relative L2 error | `||delta||2 / max(||batch_predictions||2,1e-12) <= 1e-8` |

Both prediction bounds must pass. Descriptive coefficient/intercept relative errors divide by `max(abs(reference),1e-12)`; near-zero coefficients can have a larger relative quotient despite a negligible absolute error. Cross-platform bitwise equality is not required. The protocol/configuration and tolerances were not revised after outcomes.

## Synthetic evidence, separate from market results

The 48 incremental Ridge tests exercise independent SVD oracles, nonzero means/intercepts, constant/near-constant and correlated columns, sample permutations, one-row versus batch grouping, checkpoint continuation with compensation, 40,000-row accumulation, fixed versus refitted scaler objectives, dirty states and explicit numerical failures. Queue/replay tests independently exercise the maturity rules. These are synthetic fixtures, not market alpha experiments.

The separate small [synthetic evidence table](phase2d-c1-evidence/synthetic-equivalence.json) reports five successful fixtures:

| Fixture | Rows | Max coefficient abs error | Max prediction abs error | Prediction relative L2 |
| --- | ---: | ---: | ---: | ---: |
| Linear | 1,200 | 1.998401e-15 | 1.687539e-14 | 5.440508e-16 |
| Correlated | 1,200 | 2.478018e-13 | 1.776357e-14 | 5.826241e-16 |
| Constant column | 1,200 | 3.330669e-15 | 1.776357e-14 | 4.638399e-16 |
| Shifted nonzero intercept | 1,200 | 1.989520e-13 | 9.663381e-13 | 9.109626e-16 |
| Large accumulation | 40,000 | 1.154632e-14 | 7.105427e-14 | 1.768748e-15 |

Maximum intercept error across these cases was 2.842171e-14. The constant-column case's maximum descriptive coefficient relative error was 2.167493e-4 because the reference coefficient was essentially zero; it passes the frozen combined absolute/relative componentwise criterion. This value is retained rather than hidden.

During development four scaler-equality tests initially failed because an intermediate C-array conversion changed the reduction layout relative to sklearn's original DataFrame path. Fitting/transforming the original input representation resolved that discrepancy; tolerances were not loosened. The final 484-test suite passed.

## Initial equivalence to frozen March

The frozen reference is `results/research-baseline/20261103T010111153259Z-a2cb0858/summary.json`. It supplies existing March state, avoiding an additional candidate fit. [Full initial metrics](phase2d-c1-evidence/initial-equivalence.json):

| Horizon | Max coefficient abs error | Intercept abs error | Prediction max abs | Prediction relative L2 |
| --- | ---: | ---: | ---: | ---: |
| 15m | 1.845902e-13 | 0 | 5.901529e-15 | 9.421813e-12 |
| 5m | 1.519089e-13 | 0 | 3.513682e-15 | 1.532338e-11 |

Initial training counts, feature order and scaler mean/scale/variance arrays match. These comparisons use legal initial feature probes; no future test feature or label is needed.

## All ten real checkpoints

Checkpoints are after 0/360/720/1080/1440 completed events for each horizon. Initialization probes use 256 legal training rows; later probes use already observed pilot feature rows. Batch predictions on those probes are audit comparisons and do not overwrite earlier forecasts. The CSV retains each exact mature-index hash, as-of time, probe count and every error metric.

| Horizon | Events | Training n | Coefficient max abs | Intercept abs | Prediction max abs | Prediction relative L2 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 15m | 0 | 260564 | 1.845676e-13 | 5.109303e-18 | 5.910311e-15 | 9.416722e-12 |
| 15m | 360 | 260924 | 1.731722e-13 | 5.180454e-18 | 1.203735e-14 | 8.929169e-12 |
| 15m | 720 | 261284 | 1.772263e-13 | 5.139796e-18 | 1.319951e-14 | 9.137868e-12 |
| 15m | 1080 | 261644 | 1.916557e-13 | 5.014435e-18 | 1.475404e-14 | 1.115861e-11 |
| 15m | 1440 | 262004 | 1.611055e-13 | 5.038152e-18 | 1.217960e-14 | 7.810278e-12 |
| 5m | 0 | 260574 | 1.518646e-13 | 1.521271e-18 | 3.517802e-15 | 1.530415e-11 |
| 5m | 360 | 260934 | 1.506018e-13 | 1.431486e-18 | 1.107762e-14 | 1.608940e-11 |
| 5m | 720 | 261294 | 1.360552e-13 | 1.461979e-18 | 9.880714e-15 | 1.286546e-11 |
| 5m | 1080 | 261654 | 1.425006e-13 | 1.512801e-18 | 1.004036e-14 | 1.295245e-11 |
| 5m | 1440 | 262014 | 1.668899e-13 | 1.544988e-18 | 1.267367e-14 | 1.787632e-11 |

Across these ten checks, maximum coefficient absolute error was **1.916557190728696e-13**, intercept absolute error **5.180453505407301e-18**, prediction absolute error **1.475404000361724e-14**, prediction L2 error **3.98035631763633e-14**, and prediction relative L2 **1.7876320586613474e-11**. Maximum descriptive coefficient relative error was 8.012614e-11 and intercept relative error 1.901344e-13. All pass.

The **recorded checkpoint** condition numbers range from 51,607.949780 to 51,989.059908. These are checkpoint extrema, not a retained trace of every solve's condition number. Every solve enforces the threshold. Independent read-only review reconstructed the checkpoint normal equations/intercepts and verified scaler constancy and `n=initial_n+updates`.

## Stability failures and limits

- A synthetic 300-row initialization followed by a feature of magnitude 1e10 produced condition approximately 4.865195e17 and was correctly rejected. Previous coefficients/version remained intact; statistics were dirty with n=301, solved_n=300. No prediction was permitted from that state.
- Synthetic negative centered scatter `S=-0.1 I` was rejected despite `A=0.9 I` being positive definite. A 1e308 overflow update was rejected before statistics mutation.
- Compensated sums reduce accumulation error; they do not remove catastrophic subtraction in `sum_zz-outer(sum_z,sum_z)/n`. The gates do not prove that every cancellation scenario can be detected. A future method change would require a separate protocol and new equivalence tests.
- No numerical failure occurred in either real pilot. Exact same-environment restart/reproduction is observed; other platforms, dependency versions and a much longer evolving distribution remain untested.

The technical foundation is sufficient to **design** a separately frozen C2 study. It supplies neither fresh out-of-sample predictive evidence nor trading/execution validation.
