# Confirmatory Protocol v1 - Holdout-A (frozen; not executed)

**Status:** frozen before any Holdout-A access. This document is a pre-registration only. Phase 2E does not download, read, parse, fit, evaluate, or tune on Holdout-A or Holdout-B.

## Objective and evidence status

The single confirmatory question is whether the already frozen Phase 2B specification v1 contains predictive information on Holdout-A. Any result would be confirmatory only if the release conditions in this document are met. Phase 2E exploratory reanalysis of March-June is post-hoc and cannot be substituted for this test. Prediction information, realized error, statistical significance, generalization and tradability remain separate evidence layers.

## Data and time contract

- Dataset: Binance Spot BTCUSDT 1m, UTC, with the existing causal feature and delayed-label definitions.
- Holdout-A: `[2026-07-01T00:00:00Z, 2026-09-01T00:00:00Z)`; July and August decision times are evaluated separately then n-weighted pooled. Labels must have `label_available_time <=` each month-end cutoff, as in Phase 2B.
- Training: expanding from 2025-09-01, with fits at 2026-07-01T00:00:00Z and 2026-08-01T00:00:00Z only. Each fit uses only labels mature by its fit time and the untouched causal feature history. No future Holdout-A labels enter a fit.
- Horizon: primary 15m. Evaluation boundary is `decision_time`; the two monthly segments retain gaps and use exact timestamps.
- Specification v1: the Phase 2B original program, 14 features in their frozen order, StandardScaler fitted on the legal training rows, Ridge(alpha=1, solver="svd", fit_intercept=True). No tuning, recalibration, feature deletion, model selection, or spec-v2 substitution is allowed after this freeze.

## Hypothesis and primary test

H0: the Phase 2B v1 forecasts contain no predictive information relative to Zero, expressed as `E[c_t] <= 0`, where `c_t = 2(f_t-b_t)(y_t-b_t)` and `b_t=0`.

H1: `E[c_t] > 0`. The primary statistic is the monthly Bartlett HAC mean of `c_t` with `L_m = max(60, ceil(1.3*sqrt(T_m)))`, then n-weighted pooling across July and August. Cross-month covariance is set to zero, matching the project's frozen convention. The one-sided normal-reference p-value is computed with `scipy.stats.norm`; reject H0 if and only if `p < 0.05`. The primary family contains this one test only. Its conclusion concerns predictive information, never profitability or execution.

The final report will include monthly estimates, HAC SEs, lags, sample counts, maturity exclusions, the n-weighted estimate and p-value. The 2026-09-01 smoke day is excluded from all confirmatory calculations.

## Secondary reported family (not confirmatory claims)

Report, with Holm correction within this pre-declared secondary family and without confirmatory language:

- S1: spec v2 versus Zero, 15m, same Holdout-A procedure.
- S2: spec v1 versus Zero, 5m.
- S3: spec v1 versus Historical Mean, 15m.
- S4: spec v1 versus Zero, 15m DM (two-sided) plus MSE and MAE.
- S5: spec v1 IC-HAC, 15m.

The secondary family is descriptive and does not turn a rejection into a claim about tradability. The family contains exactly the five inferential p-values S1-S5; MSE/MAE are descriptive. Undefined p-values occupy their predeclared slot conservatively as1 for adjustment and remain null in raw output, with an explicit reason. Record row-level hashes at execution.

## Known exposures and exclusions

The 2026-09-01 technical smoke day fixes the price level at the end of Holdout-A; drift-dependent results cannot be presented as fully uncontaminated confirmation without this caveat. Earlier Phase 2A metadata requests for July/August file lengths and checksums are recorded exposure. March-June results have been viewed repeatedly. Holdout-B `[2026-09-02, 2026-11-01)` is outside this protocol and requires a separate owner-approved frozen protocol.

No Holdout-A/B data, network requests, account access, orders, PnL or simulated fills are allowed during Phase 2E. Any attempted access is a stop condition and must be recorded.

## Release conditions

Before any future execution, the owner must give explicit written authorization and cite this document's SHA-256. The following must remain unchanged after the freeze: this protocol, feature/label code, split code, model code, and their hashes, which must be listed in the execution record. The protocol hash is not a permission switch; owner authorization is still required. A failed or incomplete run writes a failure record and cannot be interpreted as confirmation.

## Power addendum boundary

This protocol intentionally contains no power claim. Phase 2E may append `docs/confirmatory-protocol-v1-power-addendum.md`, which must cite this document's hash and use only the frozen exploratory estimates and assumptions. The addendum must not change this protocol, its hypotheses, primary family, endpoints, or release conditions.

## Version and provenance

Frozen feature/label/model/temporal code paths are the existing Phase 2B implementation. The execution record must include exact Git commit and SHA-256 values for `src/quantforge/features/__init__.py`, `src/quantforge/labels/__init__.py`, `src/quantforge/datasets/research_splits.py`, `src/quantforge/models/baselines.py`, `src/quantforge/temporal.py`, and any other imported path. No future code change may silently alter the target or evaluation boundary.

## Fixed spec v2 rule and code fingerprints

Spec v2 removes the later feature in canonical order from every Initial-Train pair whose absolute correlation exceeds0.999; all other features retain their order. The deletion decision is based solely on the legal March initial training set and is frozen before Holdout-A access. No July/August result may revise it. IC-HAC uses monthly ddof0 centered products; secondary tests use the same monthly lag and pooling as the primary test.

These are the exact UTF-8 source byte hashes at the protocol freeze. Changes to these mathematical/time/feature/model contracts require a new protocol before unsealing. Safety-only loader protections do not change these files.

| File | SHA-256 |
| --- | --- |
| `src/quantforge/features/__init__.py` | `857819a0c3c718cf2b1100ea72995db989ed149f65d1ad3d5e4cd3eda1fcbec1` |
| `src/quantforge/labels/__init__.py` | `b968218639387cb82ab1beccef5c5bf3ed3674e5975c3d2b25cb4d318037746e` |
| `src/quantforge/datasets/research_splits.py` | `22c862f6e8e232dedfe17c3a9832f5b7d78737de1593d6b904e2c29c5949919c` |
| `src/quantforge/datasets/splits.py` | `3459e5be5185eee2638709c3db4250e150d8e1deff140d1459882cb5257d93cd` |
| `src/quantforge/datasets/window_splits.py` | `24de7804527d3dc805f46ee969d17267927e345b94af434303cd19793675fc22` |
| `src/quantforge/models/baselines.py` | `a9269ca7f6147f653f608051746304596e898f49ec30f9e1714567e25a1ded83` |
| `src/quantforge/temporal.py` | `6c262bbd1c77e6696c7d310eb31b321f3f27ffeea02fbd64a70f3dcee9bd648c` |
