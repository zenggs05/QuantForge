# Phase 2E execution record

Completed 2026-11-04, Asia/Shanghai. Every created/modified project file, environment operation and test remained in `D:\zgs_project\QuantForge`. The user-supplied attachment was read from its provided path. No network request, market download, account action, GPU use, dependency install, global setting/driver/CUDA change or Git configuration edit was performed.

## Preflight and authorization

Starting HEAD was exactly `7f5d2ae3bbefc333296f86a1ee19d904a245b5b6`, main, clean worktree, no remote. Read CLAUDE/AGENTS, Research Lead state/decisions/questions/registry, README, research/holdout/time protocols, prior phase plans/reports and the relevant source/tests. The approved Phase 2E task and D-008 authorize the new phase; CLAUDE and all Research Lead state files remain unchanged.

Before protocol freeze, verified all four prescribed inputs and additionally the legacy HAC CSVs, baseline metrics and window summary used as anchors. The original 484 tests passed in 85.01 s. Initial source and frozen-artifact checks were read-only and did not perform new statistical analysis or model fitting.

## Three local commits

| Stage | Commit | Message |
| --- | --- | --- |
| A | `fc7d6fadd66dc776ba14aecc7a06f3a82f1bd340` | `docs: freeze phase2e power-calibrated reanalysis and confirmatory protocol` |
| B | `56f26a998d81b35d019a726d432ca3e896ad0efd` | `feat: add nested forecast tests, power analysis, specification audit and holdout guards` |
| C | The commit containing this record | `docs: record phase2e power reanalysis, specification audit and holdout pre-registration` |

Commit A contains only the three frozen documents/configuration plus manifest, `.gitattributes`, AGENTS authorization and the appended Holdout-B policy. Its attributes also preserve the four historical script bytes. Commit B follows successful complete tests; both real runs were executed from clean B, with `git_dirty=false`. Commit C adds reports/aggregate evidence, README and historical provenance copies; no third real run was required for documentation.

Frozen UTF-8 LF hashes:

| File | SHA-256 |
| --- | --- |
| `docs/phase2e-analysis-plan.md` | `14fc9100e87a4056906dd398f80859e6871ecc0b1eb91e143f159f69f919e411` |
| `configs/power-reanalysis.toml` | `3dd5e36a454cec2425587c9bd2b3f219f514ce930a58db8e0ced659a8b2b31b6` |
| `docs/confirmatory-protocol-v1.md` | `38ef1aae687b6cd1a9b8b59709b84047250e2a9f90396cb9089244133b38f1b9` |

The manifest also stores the exact typed configuration contract. The runner checks its source-bound manifest digest, all three file hashes, the parsed configuration contract and runtime Holdout constants before reading inputs. Numerical choices flow from the validated configuration; existing legacy anchor helpers retain their original immutable definitions.

## Implementation and tests

New modules: `diagnostics/forecast_tests.py`, `diagnostics/placebo.py`, `diagnostics/spec_audit.py`, `power_reanalysis.py`; CLI `scripts/run_power_reanalysis.py`. Existing edits are limited to daily safety gates in `data.py`, `experiment.py`, `research_policy.py` and `scripts/download_sample.py`. The old canonical development policy stays unchanged; FINAL_HOLDOUT_END is reused by Holdout-A constants and validate_development_bounds is used by the training audit. Original feature/label/model/statistical/C1 behavior and all frozen predecessor plans/configs/evidence/results are unchanged.

New tests: `test_forecast_tests.py` (32), `test_placebo.py` (27), `test_spec_audit.py` and `test_holdout_guards_daily.py` (41) combined, `test_power_reanalysis.py` (10). Total 110 new, all synthetic; no new test calls real Git/network or writes outside `.tmp/pytest`. Runner tests stub provenance and data/fit-audit boundaries; mathematical integration uses actual new kernels on synthetic frozen predictions. A mutation of the new DM result is rejected by the legacy anchor comparison.

Final complete suite: **594 passed in 97.42 s**, preserving all original 484. The default 400-rep Monte Carlo consumed 7.582784 s in that suite, below 60 s; initial standalone MC consumed 6.619 s with identical numerical rejection rates. Tests legitimately rerun the same frozen synthetic DGP during module/full-suite validation; none counts as a real-data candidate run. No size failure or DGP retuning occurred. The full rates and caveats are in [the report](phase2e-report.md).

The first module tests and targeted regressions also passed: forecast 32, placebo 27, guard/spec 41; previous daily parser 21 and experiment/research loader regression checks were retained. No original test was weakened or edited. After the final implementation tests passed, only documentation and evidence copies changed.

## Commands actually executed

Read-only inspection used `Get-Location`, `Get-Content -Encoding UTF8`, `rg`, `git status --short`, `git log`, and project Python hash/JSON checks. Essential execution commands, all from the project directory:

```powershell
$env:TEMP = "$PWD\.tmp"
$env:TMP = "$PWD\.tmp"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q tests/test_power_reanalysis.py
.\.venv\Scripts\python.exe -m pytest -q -s 2>&1 | Tee-Object -FilePath results/phase2e-full-pytest.txt
.\.venv\Scripts\python.exe scripts/run_power_reanalysis.py --config configs/power-reanalysis.toml
.\.venv\Scripts\python.exe scripts/run_power_reanalysis.py --config configs/power-reanalysis.toml --reproduction
.\.venv\Scripts\python.exe .tmp/finalize_phase2e.py
git diff --cached --check
git status --porcelain
git remote -v
```

Formal/reproduction console output was captured separately under `results/phase2e-formal-console.txt` and `results/phase2e-reproduction-console.txt`; preflight log is `results/phase2e-preflight-pytest.txt`. The finalizer only reads completed outputs, verifies comparisons/hashes, copies aggregates and writes the English report; it never fits a model or reads raw market data. It is distinct from the four historical finalizers, which were copied exactly but **not executed**.

Each authorized commit uses `git -c user.name=zenggs05 -c user.email=zenggaoshan05@gmail.com commit -m <exact message>` with explicit staged paths. No global/local Git config was changed; no push, remote creation, PR or message to another service was sent.

## Real runs and resource accounting

| Run | ID | Seconds | Peak process bytes | Initial-Train Ridge fits | New evaluation fits |
| --- | --- | ---: | ---: | ---: | ---: |
| Formal | `20261104T052452783302Z-7d3a5497` |79.008914 |2,608,308,224 |6 |0 |
| Reproduction | `20261104T052644575077Z-2ebd386a` |74.527627 |2,654,752,768 |6 |0 |

Peak memory is Windows process-lifetime PeakWorkingSetSize, including prediction loading/pairing and audit tables, not model arrays alone. The measured totals satisfy 20 minutes and 4 GB decimal. All numerical pools were 1, CPU float64. Reproduction repeats the six training fits and all diagnostics; it is separately charged work, not another model selection candidate.

Host/dependencies recorded by both runs: Windows 11 build 26200, Python 3.13.5, NumPy 2.5.4, pandas 3.0.6, SciPy 1.18.1, scikit-learn 1.9.1, PyArrow 23.0.1, pytest 9.2.0, threadpoolctl 3.7.0. No dependency or lockfile change was necessary.

All 19 deterministic artifacts are byte-identical, and input/source/config/provenance fields agree. Every A1-A7 gate passed, including new-path versus legacy HAC values; P1's 8 mean/SD diagnostics all passed. No failure.json occurred in either real run. Complete records are in [phase2e-reproducibility.json](phase2e-reproducibility.json).

Final read-only integrity checks confirmed that the three frozen protocol/configuration hashes and the manifest still match commit A; all 40 formal-run source hashes and eight frozen input hashes match; all 19 deterministic artifacts match between formal and reproduction runs; and the four archived historical scripts are byte-identical. Evidence tags, UTF-8/JSON parsing, relative documentation links and `git diff --check` passed. The eight modified pre-existing tracked paths are exactly the authorized attributes, AGENTS, README, holdout policy and four daily-guard source files. No file was deleted; original tests, protected state files and earlier frozen artifacts remain unchanged. The pre-commit staged audit also passed: exactly 33 authorized delivery files, all 22 evidence files and four historical scripts preserving working-file bytes, valid JSON/UTF-8/relative links, and no unstaged changes.

## Clarifications, development errors and limitations

- Before freeze, clarified the user's negative-MC-DM requirement as improvement-direction `-DM_z`, while formal DM remains model loss minus baseline. Encompassing uses the same improvement direction. CW correlation-equivalent MDE requires a zero-mean approximation, while IC-HAC is explicitly centered. Q appropriately uses chi-square. These conventions were frozen before outcomes, not adjusted afterward.
- A draft-writing command corrupted Chinese/Unicode into question marks before freezing. The draft was rebuilt directly from the original UTF-8 attachment; exact inclusion of request sections 5/6/10/11 and absence of corruption were asserted before hashing. No frozen file was subsequently altered.
- One read-only agent command used Bash heredoc syntax in PowerShell and failed to parse without executing. A short synthetic placebo test fixture initially remained long enough for LLSW; the fixture was corrected to exercise the intended non-estimable path without changing the estimator.
- A post-suite attempt to copy Monte Carlo's pytest-temporary JSON found that file no longer present. The exact printed JSON was recovered from the captured full-suite log, parsed and saved as `results/phase2e-monte-carlo.json`; its origin is disclosed in aggregate evidence. No additional real or synthetic run was needed for that recovery.
- `pytest -s` exposes expected negative-test stderr (sealed dates and deliberately corrupt synthetic CSV/Parquet). These are successful rejection assertions; the full suite ended 594 passed.
- Frozen config key `legacy_global_family_2c=32` has an imprecise historical name: 32 is the old main-lag family, 96 is the old global family. No family was changed; historical anchors call the immutable helpers and retain both correct old family columns. The new CW family is exactly 8 and window primary family exactly 6 per bandwidth. This unused legacy annotation is not used to select p-values.
- No statistical stop condition fired and no post-freeze protocol deviation affected results. Cross-platform bitwise equality remains untested; byte identity is observed only on this environment. Existing publication/licensing/test-self-containment and C1 hardening issues remain outside scope.

## Handoff

Deliverables include [English full report](phase2e-report.md), aggregate CSV/JSON and attribution README, the [power addendum](confirmatory-protocol-v1-power-addendum.md), [unfrozen Phase 3A history proposal](phase3a-history-extension-proposal.md), and [historical script archive](../tools/provenance/README.md). No PNG or third-party source copy was added.

Holdout-A/B remain sealed. The confirmatory protocol requires explicit future written owner authorization citing its hash and unchanged predictor-code fingerprints; it is not an unlock switch. Phase 3A requires a separate frozen protocol, reviewed budget and download authorization. CLAUDE.md, PROJECT_STATE, DECISION_LOG, EXPERIMENT_REGISTRY, OPEN_QUESTIONS and roadmap were not edited; the Research Lead can now independently review and update them.
