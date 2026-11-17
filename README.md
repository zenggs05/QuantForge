# QuantForge

**Statistically Detectable, Economically Small: A Pre-Registered Out-of-Sample Test of Minute-Level Bitcoin Return Predictability**

This repository contains the code, frozen protocols, execution contracts and evidence for the preprint in [`paper/`](paper/). It is **source-available, not open source**: noncommercial use only, citation required. See [Licensing](#licensing).

## Summary

We test whether a fixed forecasting program, built from public one-minute candles of Binance BTCUSDT spot, carries out-of-sample information about 15-minute returns, and how large that information is in economic terms. The program has fourteen causal features and uses Ridge regression with an expanding window. Its design and its primary test (Clark-West against a zero forecast) were frozen and hashed before any evaluation data were read.

| Test | Data | Primary result | Accuracy (Diebold-Mariano) |
| --- | --- | --- | --- |
| Historical external validation (pre-registered) | 56 months, 2021-01 to 2025-08 | t = 5.44, p = 2.6e-8 | no rejection (p = 0.125) |
| Confirmatory Holdout A (pre-registered) | 2026-07 to 2026-08 | t = 1.89, p = 0.029 | no rejection (p = 0.955) |
| Confirmatory Holdout B (pre-registered; drift-free information coefficient) | 2026-09-02 to 2026-10-31 | IC = 0.021, t = 1.33, p = 0.092: **not rejected** | no rejection (p = 0.914) |

The forecasts carry information about timing, not about the sample drift; the within-month demeaned test gives t = 5.74 historically and t = 3.48 on Holdout A. The information is economically small. The 95% upper bound on the out-of-sample R squared is about 0.15%, and a non-overlapping 15-minute rule breaks even at about 0.2 basis points per side, against a retail taker fee of 10 basis points. The historical evidence is concentrated in 2021, and the test on 56 monthly means gives t = 2.14. The second holdout does not confirm the result: its estimate is close to the historical one, but with two months it is not significant (ex-ante power about 0.6). All results, including the weak and negative ones, are reported in the paper.

A minute-by-minute census of 2,070 days of Binance monthly against daily archives finds 157 conflicting minutes. A pre-registered sensitivity analysis that removes them leaves the conclusions unchanged.

## Repository map

| Path | Contents |
| --- | --- |
| `paper/` | Preprint (PDF) |
| `docs/confirmatory-protocol-v1.md` | Frozen Holdout A protocol (SHA-256 `38ef1aae...b9`) |
| `docs/confirmatory-protocol-holdout-b.md` | Frozen Holdout B protocol (SHA-256 `6843156f...15`) |
| `docs/phase3a-analysis-plan.md`, `configs/history-extension.toml` | Frozen historical-extension protocol and configuration |
| `configs/phase3a-gate2-execution.toml`, `configs/holdout-a-execution.toml` | Execution contracts fixed before each real-data run |
| `docs/phase3a-*-evidence/`, `docs/holdout-a-evidence/`, `docs/holdout-b-evidence/` | Download ledgers, audits, results, reproduction checks |
| `docs/holdout-b-report.md` | Holdout B report, including the result-blind crash and rerun (decision D-064) |
| `docs/supplementary-evidence/` | Exploratory supplementary analyses (stored forecasts only) |
| `src/quantforge/` | Library: data validation, causal features, delayed labels, splits, models, forecast tests |
| `scripts/`, `analysis/`, `tools/` | Entry points, supplementary analysis, fixed precondition checks |
| `tests/` | Synthetic test suite |

The project's internal working logs are not published. Every decision that affects the results is documented in the protocols, contracts, records and reports under `docs/`.

## Reproducing

Python 3.13 and the pinned environment in `uv.lock` / `requirements-lock.txt`. Raw Binance data are not redistributed. The download entry points fetch the public archives from `https://data.binance.vision` and verify every file against its official checksum and against the hashes recorded in the evidence files.

```
mkdir .tmp            # pytest's base temporary directory (configured in pyproject.toml)
python -m pytest -q
```

The real-data runs check the frozen hashes before reading any data and refuse to run if anything differs.

## Licensing

| Material | License |
| --- | --- |
| Software (`src/`, `scripts/`, `tests/`, `tools/`, `analysis/`, `configs/`) | [PolyForm Noncommercial 1.0.0](LICENSE) |
| Paper and documentation text | [CC BY-NC-ND 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/) |
| Evidence derived from Binance data | [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) (required by the Binance Vision dataset terms) |

- **No commercial use** of any part without a separate written license; see [COMMERCIAL-LICENSING.md](COMMERCIAL-LICENSING.md).
- **Citation is required.** Copies must keep the Required Notice lines in [NOTICE](NOTICE), and any work that uses or builds on this repository must cite the paper ([CITATION.cff](CITATION.cff)).
- Details by path: [LICENSES/README.md](LICENSES/README.md).

Nothing in this repository is investment advice. The study involves no trading and no account access.

## Citation

```
Gaoshan Zeng (2026). Statistically Detectable, Economically Small: A Pre-Registered
Out-of-Sample Test of Minute-Level Bitcoin Return Predictability. Preprint.
https://github.com/zenggs05/QuantForge
```
