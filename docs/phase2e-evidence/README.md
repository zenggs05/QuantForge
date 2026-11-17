# Phase 2E aggregate evidence

These CSV/JSON aggregates derive from **Binance Vision** BTCUSDT Spot 1m data. The existing source-rights audit records Binance Vision Dataset Terms v1.0 and **CC BY-NC-SA 4.0**: attribution to Binance Vision, noncommercial use and applicable share-alike obligations. Public-release permission for derived tables, figures and model artifacts must be checked separately during publication preparation. This phase did not re-fetch terms, authorize commercial use, host raw datasets or publish anything remotely.

Formal run: `results/power-reanalysis/20261104T052452783302Z-7d3a5497/`; reproduction: `results/power-reanalysis/20261104T052644575077Z-2ebd386a/`. Both use clean implementation commit `56f26a9` with the frozen Phase 2E configuration. All 19 deterministic artifacts agree byte for byte in this environment. The [reproduction manifest](../phase2e-reproducibility.json) lists their hashes; copied files preserve original bytes with the scoped Git `-text` attribute.

| File group | Evidence and purpose |
| --- | --- |
| `evidence-layers.csv`, `monthly-tests.csv`, `role-tests.csv` | Post-hoc CW/IC/R2 and realized MSE/MAE/DM, every declared bandwidth/pooling |
| `window-tests.csv`, `window-monthly.csv` | Frozen Rolling-versus-Expanding realized error and DM; no new window fits |
| `mde-power.csv` | Conditional power/MDE, pooled/worst-month SE and effect scenarios |
| `encompassing-calibration.csv`, `encompassing-grid.csv` | Approximation-error audit and hypothetical planning; not experiments |
| `concentration.csv` | Top-k aggregate deletion sensitivities retaining original time gaps |
| `placebo.csv` | Deterministic P1/P2 diagnostics and warning status |
| `cost-magnitude.csv` | Forecast magnitude versus illustrative thresholds; no fees verified or PnL |
| `legacy-hac-2c.csv`, `legacy-hac-2da.csv`, `baseline-metrics.csv` | Reproduced numerical anchors; new DM path separately checked against old rows |
| `specification-audit.json` | Initial-Train-only correlation, spectra, gap leverage, six diagnostic fits |
| `anchors.json`, `identity.json`, `resolved-config.json` | Acceptance outcomes and frozen parameters |
| `formal-budget.json`, `formal-provenance.json` | Measured formal resource use and input/code/dependency provenance |
| `monte-carlo.json` | Explicitly synthetic, recovered exactly from the full pytest log's JSON output |

All real forecast statistics are `exploratory_post_hoc`, specification results are `training_period_diagnostic`, and Monte Carlo is `synthetic`. Administrative JSON wrappers preserve the applicable evidence label; nested configuration/hash dictionaries are metadata. There is no confirmatory result and tradability is not evaluated. Raw data, full prediction artifacts and large run histories remain in ignored directories; no PNG was added in this phase.
