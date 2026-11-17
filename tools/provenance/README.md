# Historical evidence finalizers

Archived on 2026-11-04 for Phase 2E provenance: **historical; not re-executed**. These four repository-authored scripts are exact byte copies of the corresponding files in `.tmp/`. No content, line ending, source path, historical assertion or numerical statement was changed. They are not third-party source imports.

The scripts document how earlier committed evidence was compiled from completed runs. They are historical records, not current entry points. Some derive their repository root from their original `.tmp/` location; others assume a particular current directory, exact old run count, source hashes or machine path. Moving them here does not make those assumptions valid for this checkout. Do not run the archived copies to regenerate or overwrite frozen evidence. Current reproducibility commands are documented in each phase record and the repository README.

| Exact copied file | SHA-256 of archived bytes | Original line endings |
| --- | --- | --- |
| [finalize_phase2b.py](finalize_phase2b.py) | `76bb11c6bdc23faaac17c11da2407b12351afe47f72bccfc9c8d3673a5efd698` | LF |
| [finalize_phase2c.py](finalize_phase2c.py) | `082d908e9e7e6a8f9a96ac58cd3acde8bc30964a7d39249d494a8f6311e64846` | CRLF |
| [build_phase2d_docs.py](build_phase2d_docs.py) | `7566b41c3542431f45dfddf66ba8255f20cfcd9f177737c701f65750131c8d3c` | LF |
| [finalize_c1_evidence.py](finalize_c1_evidence.py) | `f909b370835c8b52c41b9b2d9b367882e788fae365a7be1f8338e49e16a5b485` | LF |

The scoped `.gitattributes` rule preserves these bytes with `-text`, including the Phase 2C script's original CRLF. Hashes identify the scripts, not a claim that all historical evidence can be recreated unchanged in another environment. Older evidence line-ending issues remain assigned to the separate publication-readiness phase.

## Generated or copied committed artifacts

The mapping below comes from static inspection of the scripts and their recorded source-run manifests; no finalizer was executed during Phase 2E. It identifies the artifacts these scripts wrote at the time. Later documentation edits and separately generated files are not attributed to them.

### finalize_phase2b.py

Generates:

- [docs/phase2b-reproducibility.json](../../docs/phase2b-reproducibility.json).
- [docs/multimonth-data-manifest.json](../../docs/multimonth-data-manifest.json).
- [docs/multimonth-data-validation.md](../../docs/multimonth-data-validation.md).

It also writes the ignored `results/research-baseline/reproducibility-check.json`. Its historical inputs are the two then-existing Phase 2B baseline runs, development/monthly manifests, download ledger, daily cross-check and test log. The exact-two-runs and unchanged-source assertions are historical prerequisites, not promises about the current repository.

### finalize_phase2c.py

Generates or copies:

- [docs/statistical-diagnostics-report.md](../../docs/statistical-diagnostics-report.md), [docs/statistical-diagnostics-summary.json](../../docs/statistical-diagnostics-summary.json), and [docs/phase2c-reproducibility.json](../../docs/phase2c-reproducibility.json).
- Under `docs/statistical-evidence/`: `paired-loss-summary.csv`, `bootstrap-results.csv`, `hac-results.csv`, `calibration-summary.csv`, `calibration-quantiles.csv`, `distribution-quantiles.csv`, `loss-autocorrelation.csv`, `pairing-audit.json`, and `resolved-config.json`.
- Fourteen existing figures under `docs/figures/phase2c/`: each of `prediction-target`, `quantile-calibration`, `residual-distribution`, `monthly-ic`, `monthly-mse-difference`, `bootstrap-ci`, and `loss-acf`, for `15m` and `5m`.

Its historical runs are `20261103T012342527623Z-d1bab75b` and `20261103T012551082603Z-c847d61f`. The figure mapping is included for provenance only: Phase 2E did not regenerate or add those figures, and does not treat their publication permissions as resolved.

### build_phase2d_docs.py

Generates [docs/phase2d-a-analysis-report.md](../../docs/phase2d-a-analysis-report.md) and [docs/phase2d-a-record.md](../../docs/phase2d-a-record.md).

Copies ten formal-run artifacts into `docs/phase2d-a-evidence/`: `metrics.csv`, `model-timings.csv`, `model-states.json`, `window-audit.json`, `paired-losses.csv`, `bootstrap-results.csv`, `hac-results.csv`, `pairing-audit.json`, `resolved-config.json`, and `summary.json`.

Copies seven existing figures into `docs/figures/phase2d-a/`: `mse-15m.png`, `mse-5m.png`, `ic-15m.png`, `ic-5m.png`, `bootstrap-main-15m.png`, `bootstrap-main-5m.png`, and `training-samples.png`.

Its source runs are `20261103T030302685501Z-d124511e` and `20261103T030457400531Z-daa632f2`. It does **not** generate `phase2d-a-reproducibility.json` or `research/rolling-window-findings.md`; those referenced documents were prepared separately.

### finalize_c1_evidence.py

Generates [docs/phase2d-c1-reproducibility.json](../../docs/phase2d-c1-reproducibility.json) and the compact `docs/phase2d-c1-evidence/formal-summary.json`.

Copies six aggregate files into `docs/phase2d-c1-evidence/`: `batch-equivalence.csv`, `batch-timings.csv`, `initial-equivalence.json`, `budget.json`, `checkpoint-audit.json`, and `synthetic-equivalence.json`. The first five come from the formal run; the sixth is explicitly synthetic evidence from `results/phase2d-c1-synthetic-equivalence.json`.

Its source runs are `20261103T045308206581Z-5632721d` and `20261103T045523927874Z-0b7d1d55`. Its assertions and hash checks are preserved as written. It does not generate the C1 narrative reports, the research interpretation, the actual checkpoint files, or the per-minute prediction/update audits.

## Scope and permission boundary

Archiving these scripts adds provenance, not new experiments, new market access or publication approval. The original source artifacts remain in ignored local result/data directories. Binance Vision data-derived evidence requires attribution, noncommercial use and a separate publication-rights review; raw data are not hosted here. Historical figures and model artifacts retain their existing review requirements. No archived script was imported, executed or modified during this phase.
