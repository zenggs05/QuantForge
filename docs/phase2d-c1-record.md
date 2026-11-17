# Phase 2D-C1 execution record

Completed on 2026-11-03 (Asia/Shanghai), entirely inside `D:\zgs_project\QuantForge`. Initial parent cwd was checked before explicitly selecting the existing project. No nested project, parent Git repository, global environment changes, remote, push or PR was created.

The work implements the user's Phase 2D-C1 request: delayed-feedback events, fixed-initial-scaler incremental Ridge, independent mathematical tests, one real March1 engineering pilot, one unchanged reproduction and auditable local records. Existing September2025–June2026 development data were reused; no market requests, July/August holdout reads, frozen-result overwrite, tuning, forgetting, drift detector, neural model, trading or PnL was introduced.

## Local commit sequence and frozen inputs

| Step | Commit | Message |
| --- | --- | --- |
| Starting state | `685d9a324766f3d19de9faaa4a213b9c210b5d6a` | Existing completed Phase 2D-A |
| Freeze protocol first | `f28bb2ebeca4d2c255a894f57aab93ee4c447685` | `docs: freeze delayed-feedback incremental ridge foundation` |
| Commit tested implementation | `9d35923625b0760b9b00d5fae861ef567ffb41b3` | `feat: add delayed-feedback incremental ridge foundation` |
| Final audit documentation | The commit containing this record; inspect `git log -1` | `docs: record online ridge equivalence and replay audit` |

Both real runs record clean implementation commit `9d35923`, before these report/README edits. The final documentation commit necessarily follows those runs; it does not imply an extra real-data run from the documentation commit. Author identity is supplied per command, with no global Git configuration edit.

- Plan SHA-256: `7876d1da1d867fa91184bdcfa2f3c2144142acc2ee9d70bbe6cc54a271d18752`.
- Config SHA-256: `e823ec9d599892ba98085162de22c00d08691338d3d19598d9a1bbf0115afc78`.
- Development-manifest SHA-256: `6fc42cd53ab30e1282bc9202046edd1fb43e5bc3fa8c44595f1639958c66acb8`.
- Frozen reference summary SHA-256: `7aa9f334a9e5b5f1085edd2362676d86d0898fcdd376f92bb1886efbc3dc6f12`.
- Frozen reference predictions SHA-256: `66f74e1367ed816455d8df2af5c8fdd89009c893aa5fe064df29fe646c86407a`.

The source/test/protocol contracts from previous phases were preserved. Only README, AGENTS and scoped `.gitattributes` entries change existing tracked files; all C1 code/tests/reports are new. The existing 345 tests were retained. The hash manifest and runner reject altered C1 plan/configuration before execution.

## Files delivered

| Group | Files / role |
| --- | --- |
| Protocol | `docs/phase2d-c1-analysis-plan.md`, `docs/phase2d-c1-plan-manifest.json`, `configs/online-foundation.toml` |
| Numerical and events | `src/quantforge/online/{__init__,feedback_queue,incremental_ridge,replay,foundation}.py` |
| CLI | `scripts/run_online_foundation.py` |
| Tests | `tests/test_feedback_queue.py`, `test_incremental_ridge.py`, `test_online_replay.py`, `test_online_checkpoint.py`, `test_online_foundation.py` |
| Reports | This record, [equivalence](phase2d-c1-equivalence-report.md), [pilot](phase2d-c1-pilot-report.md), [reproducibility JSON](phase2d-c1-reproducibility.json) |
| Research | [Delayed-feedback online learning](../research/delayed-feedback-online-learning.md) |
| Aggregate evidence | `docs/phase2d-c1-evidence/`: batch metrics/times, initial equivalence, budget, checkpoint metadata, synthetic metrics and compact formal provenance |
| Project guidance | README, AGENTS, `.gitattributes` |

Full run artifacts remain Git-ignored under `results/online-foundation/`: `summary.json`, `report.md`, `prediction-audit.csv`, `update-audit.csv`, `batch-equivalence.csv`, `batch-timings.csv`, `final-state.json`, `initial-equivalence.json`, `checkpoint-audit.json`, `budget.json`, `resolved-config.json` and10 complete checkpoints per run. Tracked aggregate evidence contains no per-minute prediction/update table or full checkpoint history. The compact formal summary omits nested detailed budget/metrics because separate small evidence files retain those fields.

Copied evidence preserves its original bytes, including Windows CRLF, so its SHA-256 remains identical to the source artifact. An initial staged whitespace check flagged those CR bytes; the scoped `.gitattributes` rule now recognizes CR-at-EOL without normalizing the evidence or disabling ordinary whitespace checks. Staged blob bytes were separately verified against each working evidence file.

## Checks and actual execution

Read existing AGENTS/README, experiment/holdout protocols, Phase2D-A report, adaptive proposal, temporal/features/labels, baseline and split/data-policy code and existing tests before implementation. Environment/Git/source checks were read-only. Commands used included `Get-Location`, `git status --short`, `git log`, `Get-Content`, `rg`, project Python version/import metadata checks, CPU detection, `nvidia-smi` and `Get-Command nvcc`. An initial combined environment command returned nonzero because `nvcc` was absent from PATH; no CUDA install followed.

Key executed commands from the project directory (test commands were also run by module during development):

```powershell
# All Python/test work uses the existing project environment.
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q tests/test_feedback_queue.py tests/test_incremental_ridge.py tests/test_online_replay.py tests/test_online_checkpoint.py tests/test_online_foundation.py
.\.venv\Scripts\python.exe scripts\run_online_foundation.py --config configs\online-foundation.toml
.\.venv\Scripts\python.exe scripts\run_online_foundation.py --config configs\online-foundation.toml --reproduction
.\.venv\Scripts\python.exe .tmp/finalize_c1_evidence.py
git status --short
git log -3 --format='%H %s'
```

The post-run `.tmp/finalize_c1_evidence.py` is a local audit helper, not a new experiment: it checks existing artifacts/hashes/counts, classifies differences and copies aggregate evidence. Reproduction commands remain in the tracked CLI/README. Test and runner output was captured using PowerShell `Tee-Object` under `results/phase2d-c1-pytest.txt`, `results/phase2d-c1-formal-console.txt` and `results/phase2d-c1-reproduction-console.txt`. Temporary/cache locations remain repository-local, as configured by the project; no package install or lockfile change was necessary.

Local commits use:

```powershell
git -c user.name=zenggs05 -c user.email=zenggaoshan05@gmail.com commit -m "docs: freeze delayed-feedback incremental ridge foundation"
git -c user.name=zenggs05 -c user.email=zenggaoshan05@gmail.com commit -m "feat: add delayed-feedback incremental ridge foundation"
git -c user.name=zenggs05 -c user.email=zenggaoshan05@gmail.com commit -m "docs: record online ridge equivalence and replay audit"
```

Only explicit project paths were staged. No `git push`, remote creation or PR action was executed.

## Tests and failures retained

| Scope | Passing tests | Recorded seconds |
| --- | ---: | ---: |
| Existing suite before C1 | 345 | 48.83 |
| New C1 tests together | 139 | 30.91 |
| Full integrated suite before implementation commit | **484** | **77.22** |

New coverage:38 feedback queue tests,48 numerical Ridge tests,45 replay/checkpoint tests,8 foundation-runner tests. They assert actual mathematics and state invariants: premature access refusal before value fetch, exact maturity, deterministic ties, horizon isolation, carry-in/month boundary, invalid/gapped labels, consumed-ID persistence, input perturbation invariance, update-before-forecast, fixed scaler, constant/correlated features, intercept, order/grouping/large accumulation, corrupt checkpoint/provenance/version rejection, path and holdout rejection before reads, exact restart and operation budgets. The runner's failed-equivalence test checks that no success marker is written. Full synthetic end-to-end tests are explicitly synthetic.

Development failures are not suppressed: four initial numerical tests exposed DataFrame-to-C-array scaler reduction differences, corrected by preserving sklearn's input path without widening tolerances. An ad-hoc synthetic evidence command had an `else300` syntax typo and was corrected before execution; an independent read-only audit print had an undefined variable and was corrected. Neither changed a model result or required extra real pilots. Early synthetic checkpoint tests left an explicitly synthetic ignored output directory; later tests use isolated temporary output. No original test was removed or changed.

The synthetic stress tests intentionally reject approximately4.865e17 condition, materially negative centered scatter, and overflow; details and residual cancellation limits are in the numerical report. Both real runs completed without numerical failure or a `failure.json`. No rerun or parameter selection followed an unfavorable alpha metric: no alpha metric was computed at all.

## Outcomes and next boundary

Formal run `20261103T045308206581Z-5632721d`:2,880 predictions,2,880 valid updates,10 successful batch checkpoints, exact restored continuation,17.085246s. Reproduction `20261103T045523927874Z-0b7d1d55`:same numerical/event outcomes,17.886687s. All16 deterministic artifacts match byte for byte; all20 artifact hashes per run and original frozen-reference hashes passed rechecking. Read-only numerical and temporal reviewers independently confirmed mature-event/version integrity, fixed scaler, checkpoint equations and counters. All differences are documented timing/memory/run-identity fields.

There is no outstanding blocker to planning C2, but C1 does not authorize running it. First freeze a separate delayed-online comparison protocol that separates fixed-scaler update schedule from monthly refitted-scaler objectives, specifies mature-label schedules, comparison family/metrics/uncertainty and resource accounting. All available development months have already been observed, so results there remain exploratory. Full audit-history growth and extreme-shift stability require explicit limits; the final holdout stays locked. No remote publication or trading work is part of this handoff.
