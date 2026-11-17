# Confirmatory protocol v1: historical external validation supplement

Frozen before implementation and historical download. This separate supplement does not edit, execute, replace or unseal the original Holdout-A protocol v1, SHA-256 38ef1aae687b6cd1a9b8b59709b84047250e2a9f90396cb9089244133b38f1b9. Its seven prediction-code fingerprints remain unchanged.

Historical External Validation under Design Frozen in 2026 uses 2020 warm-up/training followed by 56 expanding monthly evaluations, January2021–August2025. Exact definitions, one primary CW test, five secondary Holm tests, fixed sensitivities, power assumptions, gates, synthetic fixtures and stop rules are frozen in phase3a-analysis-plan.md and configs/history-extension.toml.

Primary: one-sided 15m v1 Ridge versus Zero CW; monthly Bartlett L=max(60,ceil(1.3*sqrt(T))), n-weighted pooling, zero cross-month covariance; reject at p<.05. This concerns predictive information, not lower realized MSE, generalization, future usefulness or tradability. DM/MSE/MAE and IC remain distinct. No historical result exists in this phase.

Spec v2 deletes log_close_open based on the later 2025-09–2026-02 Initial Train audit; evaluation on earlier history is post_hoc_specification_evaluated_on_earlier_data, with selection bias disclosed. Already-observed CW results and general market knowledge are acknowledged; the historical exercise is not a truly prospective2020 design.

Only synthetic infrastructure and an offline68-URL plan are authorized. Hashes and URLs confer no market access. Holdout-A [2026-07-01,2026-09-01), Holdout-B [2026-09-02,2026-11-01), and2026-09-01 smoke day remain sealed for this phase. Future downloads and holdout releases need separate explicit owner authorization and an applicable frozen protocol. No orders, PnL, accounts or remote publication.
