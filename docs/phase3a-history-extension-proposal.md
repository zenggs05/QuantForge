# Phase 3A history extension proposal

Draft prepared 2026-11-04 (Asia/Shanghai); **not frozen and not a download authorization**. Evidence level: planning based on Phase 2E exploratory_post_hoc diagnostics. No new archive availability or size was checked over the network. Confirmatory evidence: none; tradability: not evaluated.

This proposal follows D-010 and the completed Phase 2E power analysis. Its purpose is to obtain a longer, strictly forward evaluation history for the existing forecast procedure. It does not authorize C2, neural models, drift-trigger tuning, trading simulations, or any Holdout-A/B access. The owner and Research Lead must approve a separate protocol and transfer budget before implementation or download.

## Proposed data interval and roles

Primary proposed source: official Binance Vision BTCUSDT Spot 1m monthly kline archives, **2020-01 through 2025-08 inclusive: 68 months**. Calendar arithmetic gives 2,070 days and 2,980,800 expected minute opens if every archive is complete. Availability, market interruptions and exact archive sizes are unverified until a separately authorized download phase. Missing archives or gaps must be recorded, not silently substituted.

Use 2020 as the initial warmup/training year. Evaluation starts **2021-01** and advances one calendar month at a time through 2025-08: 56 proposed evaluation months. Later 2025-09 through 2026-06 data already exist, but must not be used to fit models that predict the earlier history. The existing March-June 2026 estimates remain exploratory calibration evidence, not additional unseen observations in a new primary test.

Optional 2018-2019 may be considered as a separately authorized robustness extension. It adds 24 months; it is not part of the proposed 68-month transfer budget or a prerequisite for the primary design. The protocol must state how the shorter exchange history and unavailable/incomplete archives would be handled. This draft has not verified their availability.

## Transfer and storage budget proposal

The existing ten-month project sample used approximately 20.26 MiB of compressed ZIP payload, about 2.03 MiB/month. This is only a rough empirical reference from a different period. Historical volume and representation can differ; it is not a remotely measured estimate for 2020-2025.

| Item | Planning estimate / proposed hard cap | Interpretation |
| --- | --- | --- |
|68 monthly ZIPs | Rough midpoint138MiB; planning range140-300MiB | Extrapolation, not HEAD/GET evidence |
|Per-archive compressed response | 8MiB maximum | Proposed allowance, separate from old4MiB policy |
|Total ZIP payload including retries | 384MiB maximum | Fail closed before exceeding cumulative ledger reservations |
|CHECKSUM/metadata responses | 1MiB cumulative,16KiB each | Separate accounting; no unbounded responses |
|Uncompressed single CSV | 48MiB maximum | Enforce before extraction/parse |
|Uncompressed CSV calendar ceiling | 3,264MiB =68x48MiB | Ceiling, not predicted storage |
|Local disk reservation | 8GiB | ZIP/Parquet/manifests/temp; stream CSV in memory or discard verified temporary CSV |
|Initial process memory target | <=4GB decimal | Month-at-a-time processing; revise only before protocol freeze |

The total 384 MiB cap is intentionally below 68 x 8 MiB: unexpectedly large archives can exhaust the ledger and stop the transfer. Failed/interrupted response payload is charged. Resume verifies completed cached archives and never treats retries as free. TCP/TLS/header overhead is outside the payload estimate and must be described. No archive should be requested solely to improve a size estimate before approval. Optional 2018-2019 requires an additional explicit cap and owner decision.

## Integrity, continuity and timestamp handling

Use official monthly ZIPs and their official `.CHECKSUM`. Verify exact filename and SHA-256, bounded single-member ZIP/CRC checks and expected CSV schema. Preserve a hash chain from official archive bytes to CSV, typed Parquet, per-month manifest and a versioned overall history manifest. Record source URL, request time, actual payload bytes, archive/checksum hashes, parser version and UTC bounds. A checksum or content mismatch stops the run; it cannot be repaired by overwriting the accepted source.

Check sorted unique minute opens, calendar endpoints, close timestamp duration, duplicates, missing minutes, OHLC consistency, finite values, nonnegative volume/trades and Parquet round-trip equality. Cross-month continuity must be checked as well as each archive. No forward-fill or synthetic price/label is allowed. A gap resets rolling features and invalidates overlapping labels. Freeze an exclusion policy before download; publish per-month counts even when that prevents an evaluation month from being estimable.

Binance Spot historical timestamps are **milliseconds before 2025-01-01 and microseconds from 2025-01-01 onward**. Detect units from every raw open/close value and check them against the archive date; reject mixed or inconsistent units. Convert exactly to aware UTC without float timestamps. Preserve raw integer times and the source-unit field. Validate the close tick as one millisecond or one microsecond before the next minute boundary, then use the project's exclusive bar boundary and one-second information-availability convention for research. Test the 2024-12/2025-01 transition with synthetic fixtures before downloading history.

## Versioned access policy, keeping old phases reproducible

Do **not** silently expand the existing `research_policy.ALLOWED_MONTHS`, rewrite its canonical policy, or modify old frozen manifests. Introduce a separately named Phase 3A history policy/configuration, with its own explicit month whitelist, expected calendar counts, checksums and read limits. The existing Phase 2B/C/2D/2E loaders must continue to accept only their frozen development inputs and reproduce their old hashes.

The new loader should validate the complete requested interval and all referenced paths before opening the first archive. It must call the common Holdout-A/B interval guard before fetch, cache read, parse and any derived analysis. Requested months outside the history whitelist fail before I/O. Smoke day 2026-09-01 remains technical only and must be rejected by research loading. No configuration flag should unlock a holdout.

The Phase 3A protocol, config, access policy and source-code fingerprints must be hashed and committed before the first request. Then test with synthetic archives, approve the concrete budget, download a first authorized month as a parser/integrity gate, and proceed only under the approved ledger. This sequence is a proposal for a later task; it has not been executed.

## Strict forward rolling evaluation

For each evaluation month m from 2021-01, fit at that month's first 00:00:00 UTC using only causal features and labels with `label_available_time <= fit_time`. Training begins at 2020-01 after feature warmup and expands using only data preceding the fit. StandardScaler is fitted only on that legal training set. Features may use past contiguous bars; no future rows enter preprocessing or sample selection.

Predict decision times in `[month_start,next_month_start)` using the frozen model for that month. Evaluate only targets mature by the month's end, matching the original Phase 2B label cutoff. A July price must never complete a June label; more generally a next-month price must not complete an excluded current-month target. Keep feature/label availability, fit time, model version, source hashes and maturity exclusions with the predictions.

**Never fit on later 2025/2026 data and predict earlier years while calling that out-of-sample.** Spec v1 was selected before any pre-2025-09 history was accessed, but that chronological fact does not by itself make a backward-prediction experiment valid. Every simulated fit must precede its evaluation month. The entire history is dated before the present and general market knowledge cannot be fully audited; disclose that limitation.

Freeze specification v1 as the primary procedure: the original 14 features, 15m horizon, StandardScaler, Ridge(alpha = 1, SVD, unpenalized intercept), one-second availability delay, no tuning. Specification v2 is secondary, with the Initial-Train rule already fixed in 2E deleting `log_close_open` while keeping the other 13 features in order; do not re-select that feature set using the new historical outcomes. No crash-session exclusion variant is proposed as an evaluation model. Both fit and prediction budgets must be declared in the separate protocol: 56 months x 2 specifications = 112 Ridge fits for this minimal 15m design, before any authorized reproduction. Adding 5m would double that count and needs explicit pre-freeze inclusion.

## Proposed inferential family and stability questions

Candidate primary family: a single all-evaluation-month 15m spec-v1 CW-versus-Zero test, monthly Bartlett `max(60,ceil(1.3*sqrt(T_m)))`, n-weighted pooled means and an explicit cross-month covariance convention. Do not select favorable years after results. Report individual months, predeclared calendar blocks and all negative results without turning those descriptive splits into extra confirmatory claims. If the final protocol adopts multiple primary contrasts, it must name every member and multiplicity rule before download.

Keep realized error distinct: MSE, MAE, R2_OOS and two-sided DM remain required beside CW. A secondary family can contain spec-v2 CW, spec-v1 IC-HAC, CW versus Historical Mean and DM versus Zero, with within-family Holm adjustment and no substitution for the primary endpoint. Define the family and handling of null tests explicitly before execution. Historical knowledge and any exposure audit determine whether “previously unaccessed historical evaluation” supports confirmatory wording; this draft makes no such result claim.

Parameter-stability diagnostics should examine monthly coefficients in a common raw-feature coordinate system, with uncertainty and collinearity noted, and predeclared calendar contrasts/structural-stability tests. Do not interpret changing StandardScaler coordinates as economic drift. The final protocol must specify the statistic, null, finite-sample calibration, lag/bootstrap choice and multiplicity before seeing history.

A conditional Giacomini-White (GW) comparison may use **lagged realized volatility as an instrument**, available at decision time and calculated only from past bars. It would ask whether relative predictive performance varies with the instrument, not whether a proposed strategy profits. The instrument horizon, transformation, contrast, moment vector, covariance estimator and null must be preregistered. Expanding estimated nested models complicate standard GW assumptions; theory/synthetic size validation and the exact intended forecast experiment must be reviewed before treating a conditional-Wald result as inferential evidence. This draft does not invent a valid formula by name or choose a volatility threshold from outcomes.

## Power rationale from Phase 2E, with sensitivity

The relevant [Phase 2E evidence](phase2e-report.md) is exploratory and selected after multiple looks. For 15m Ridge versus Zero, n-weighted LLSW CW, each calibration role has 87,810 rows over two months. CW effective n is 16,452 for Validation and 29,224 for Development; raw minute count is not the information count. Pooled native-unit MDE 80 is 1.07675e-8 and 1.18933e-8, respectively.

| Calibration and SE assumption | Required months,0.5x observed CW |0.75x |1.0x |
| --- | ---: | ---: | ---: |
|Validation, pooled SE |8.10 |3.60 |2.02 |
|Development, pooled SE |12.24 |5.44 |3.06 |
|Validation, worst-month SE |21.12 |9.39 |5.28 |
|Development, worst-month SE |44.82 |19.92 |11.20 |

The proposed 56 evaluation months exceed these **conditional planning counts** even at half the observed CW effect under the listed scenarios. It does not guarantee power: the model could have a smaller, absent, reversed or less stable historical effect, a different dependence structure, changing volatility or dependent months. The approximation assumes the signal and SE regime remain fixed and SE scales as 1/sqrt(months). The two-month Cochran Q tests have one degree of freedom and do not validate that assumption. Worst-month calculations substitute the maximum observed monthly SE directly; they are sensitivity scenarios, not mathematical upper bounds.

The distinction from realized-error power is material. Phase 2E two-sided DM relative-MSE MDE 80 is 0.19957% and 0.26782% using pooled SE. At the full observed improvement, the extrapolated required months are 24.22 (Validation calibration) and 111.55 (Development calibration); at half the observed improvement they are 96.89 and 446.21. Even 56 evaluation months may therefore remain insufficient for a small realized-MSE advantage under these assumptions. Do not relabel CW significance as an MSE advantage to avoid this limitation. Neither calculation establishes tradability.

Longer history also provides more calendar regimes for stability questions, but the minimum detectable parameter or conditional-performance effect has not yet been computed. That is a required protocol-design task, not an assumed benefit. The full 2E MDE grid and [Holdout-A power addendum](confirmatory-protocol-v1-power-addendum.md) retain both favorable and unfavorable calibration scenarios.

## Licensing and publication

The existing Research Lead audit records Binance Vision Dataset Terms v1.0, dated 2026-08-26, and **CC BY-NC-SA 4.0**: attribution to Binance Vision, noncommercial use, share-alike obligations where applicable, and no hosting of raw datasets. Phase 2E did not re-fetch these terms. Before any public release, separately verify the current terms and permissions for raw data, derived aggregate tables, figures and model artifacts; a code license alone does not resolve data rights. Keep raw archives/Parquet and large predictions Git-ignored. This draft does not upload or authorize redistribution of any artifact.

Holdout-A `[2026-07-01,2026-09-01)` and Holdout-B `[2026-09-02,2026-11-01)` are outside every proposed history path and remain sealed. The independent Holdout-A preregistration is not an authorization to download or analyze it, and Holdout-B has no applicable execution protocol here.

## Decisions required before a separate Phase 3A task

1. Approve or decline the 68-month primary interval, the 384 MiB ZIP/1 MiB metadata caps and 8 GiB disk reservation; decide whether to defer 2018-2019 entirely.
2. Approve the strict 2021-01 onward monthly forward design, minimum training history, feature warmup, gap/missing-month failure policy and 112-fit minimal budget.
3. Freeze the one-member primary family and exact secondary family; decide whether 5m, stability tests and conditional GW belong in the first run or a later phase with their own power/size validation.
4. Approve the explicit power assumptions, exposure language, cross-month dependence treatment and the criterion for stopping after inconclusive or negative results without tuning.
5. Approve the separate versioned history access policy while retaining all old allowlists and locked Holdout-A/B guards.
6. Review data terms and publication limits, then issue explicit download authorization referencing the final protocol/configuration hashes. No approval is inferred from this draft or from D-010's approval in principle.

No download, HEAD/CHECKSUM request, historical model fit, holdout access or publication was performed for this proposal.
