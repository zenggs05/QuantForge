# Phase 3A execution record

**Completed: protocol freeze and synthetic-only infrastructure; download not authorized.** The default full suite passed **794 tests**, retaining all 594 existing tests and adding 200. Both clean synthetic runs used implementation commit `53b3e3f2440fe6cf87c6c2bd646158437444ac43`; all **19 deterministic artifacts agree byte for byte**. No historical market result, market power estimate or trading result is claimed.

## Commits and synchronization

| Purpose | Commit |
| --- | --- |
| Synchronized D-022 pre-freeze base | `86841fd56210aed87ad06af5c165cef2dd00adaa` |
| A: frozen protocol/config/supplement | `927145671910df600a1081d4ce6478439ca4b0a4` |
| Main D-023 state update | `98fe356a91d2dbe6e34d0ea54c404a44d1a554b5` |
| Authorized no-FF merge of D-023 | `f1fc5f6175f20d35ebe5105d500ab4f9bece8317` |
| B: tested implementation | `53b3e3f2440fe6cf87c6c2bd646158437444ac43` |
| C: documentation | The documentation commit containing this record; its SHA is supplied in the owner-facing delivery. |

The original requested starting commit was `69adaa3`; Research Lead synchronized/repaired the baseline before A. D-023 explicitly replaced the earlier fast-forward-only continuation rule because main and the Phase 3A branch had diverged after `86841fd`. `git merge --no-ff main` completed without conflict and brought in only the two Research Lead state-file changes. The D-023 heading occurs exactly once. Codex did not hand-edit research state files.

Before the merge, all 21 WIP files were saved with unique tag `wp5-wip-before-d023-20261105135605515904` and stash SHA `471ea4a9eb0d7681683aa98730d8c875d7d3079d`. `git stash push -u` directly captured untracked files; the preliminary intent-to-add step was unnecessary. A local byte backup and per-file SHA list guarded restoration. Git's EOL conversion was reversed from that backup, and all 21 raw hashes matched before locating and dropping the exact stash. No unrelated stash was dropped. Local recovery evidence remains in `.tmp/d023-wip.json` and `.tmp/d023-wip-backup.zip`.

## WP-0 environment and isolation

D-022's preinstalled independent venv was used; **no uv sync, offline sync, installation, added dependency or Git configuration edit** occurred under D-022. Synchronization mode/time is not applicable: this was preinstalled-environment validation. The executable is this worktree's `.venv/Scripts/python.exe`, and the editable import points at `D:/zgs_project/QuantForge-3a/src`.

Verified versions: Python 3.13.5; pandas 3.0.6; numpy 2.5.4; scipy 1.18.1; pyarrow 23.0.1; scikit-learn 1.9.1; matplotlib 3.11.2. `TMP` and `TEMP` were set to this worktree's `.tmp`, and Python bytecode writes were disabled.

The original default-basetemp baseline was **594 passed in 99.79s**, shell elapsed 101.46s. Its log is `.tmp/phase3a-baseline-pytest.log`. The final authorized metadata-only audit, immediately before documentation completion, found **10052 files in main's venv and 10052 in Phase 2F's venv**, with every relative path, size and nanosecond mtime unchanged from WP-0. No other-worktree file contents were read for this check. Evidence is summarized in [reproducibility.json](phase3a-evidence/reproducibility.json); full local snapshots remain ignored in `.tmp`.

Research Lead's separate three-worktree METADATA and `_virtualenv.pth` merge checklist remains a Research Lead acceptance step. No permission to inspect other-worktree research data was inferred from the metadata exception.

## Frozen integrity and ownership

| Primary frozen artifact | SHA-256 |
| --- | --- |
| `configs/history-extension.toml` | `e9cbd19d884d046199f468f29e912cd6a9fbec1852e90ffc125b86d8df0bd9af` |
| `docs/confirmatory-protocol-v1-history-addendum.md` | `a4a99cfbfb9e33105d98ad44b439a57020a050b64b46b5a8e4a0bbc4b204fc07` |
| `docs/phase3a-analysis-plan.md` | `fea667f7689d2c45ab0697b0dd2e8877cf0894ae27e4a5f0277efae48e50cb3c` |

Manifest SHA-256 remains `ceb56c5683ff682240563c87991c96298b3f6c45baa93eee03134efc7d34c54a`. The original confirmatory protocol remains `38ef1aae687b6cd1a9b8b59709b84047250e2a9f90396cb9089244133b38f1b9`; the Phase 2E evidence CSV remains `723ab3a5563f27157c54a3490a52919eb51c44fed4866f5192cd443f4c71bdc7`. All **14 pinned raw-byte hashes** match, including seven prediction sources. Runner startup verifies these before constructing synthetic data.

The old policy and monthly downloader/parser code prefixes are preserved; new historical entry points are additive. Existing `.gitattributes` content is preserved, with only A's authorized appended rules. Old tests, Phase 2E code/evidence, protected feature/label/model/split files, online code and CLAUDE.md are unchanged. Research state changes arrived solely through the owner-authorized merge.

## Tests, corrections and computation counts

Final command, with the repository's default `.tmp/pytest` basetemp:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
```

Result: **794 passed in 301.24s** (594 existing + 200 new; no skip/failure). Log: `.tmp/phase3a-full-pytest.log`. Independent module basetemps remained inside this worktree. The full workflow test executes all 56 months twice and checks every deterministic artifact, 224 fits/112 folds per run, all lag/pooling/spec/horizon rows, the complete Holm family, training-index audit, maturity, sparse-GW labels and resource budgets. Maturity spies reject direct training-target access and require training validation before fit, with evaluation truth revealed after prediction. Configuration tests reject every pinned fingerprint drift, typed-value drift, runtime-policy drift and output escape before execution.

| Earlier targeted verification | Result |
| --- | --- |
| Infrastructure five-file suite | 49 passed, 6.58s |
| Infrastructure + power after configuration integration | 103 passed, 6.24s |
| AP10 | 9 passed, 1.04s |
| Three diagnostic modules | 35 passed, 1.41s |
| Initial full-workflow module (before final audit-field additions) | 4 passed, 196.30s |
| Config + maturity guards | 53 passed, 2.62s; expanded config alone 52 passed, 2.21s |

These are invocations, not counts to add to 794. The new Phase 3A tests performed **899 Ridge fits** across development checks and the final suite: 448 in the initial two-run workflow test, 2 in the guard test invocations, and 449 in the final suite. Existing legacy-test fits are outside this new-phase count. The two subsequent formal engineering runs add **224 + 224**, counted separately. No real-history Ridge fit was performed.

Initial test implementation errors were corrected without modifying frozen behavior: the legacy policy AST test initially hashed the whole module instead of the frozen `_POLICY` assignment; the maturity negative fixture needed nanosecond dtype before assigning a one-nanosecond boundary. The initial diagnostic tests also used sample SE instead of the then-frozen known-SD bound. Review added Monte Carlo precision and excluded-pair fields, an explicit partial-episode volatility label, mandatory runtime constants and last-written success markers. Only redundant final blank lines in four new tests were removed after the full suite; no executable code changed before B.

S_dep1's fixed 1000-replication AR(1) fixture returned coverage **0.895**, MC SE approximately **0.009694**, within [0.85,0.99]. This tolerance is a synthetic check, not a uniform coverage guarantee. All 12 full-precision power anchors passed relative tolerance 1e-12, with display tolerance 1e-4. Their formulas, units, assumptions and full values appear in [the protocol-only report](phase3a-report-protocol-only.md).

## Original stop and authorized D-023 resolution

The original correct known-SD stability guard was `SE=0.005773502691896258`, `3SE=0.017320508075688773`. The fixed same-sign trajectory exceeded it in windows ending at months 16/17/18; maximum absolute error was 0.019801724801711496, or **3.429759343406106 SE**. The first pytest run reported 2 failed/33 passed, but its sample-SE denominator was erroneous: a read-only correction established one substantive fixed-trajectory failure. Work stopped before B/C, with no frozen-file changes or seed retries.

D-023 and the owner's v2.5 instruction explicitly authorized resumption and the revised **test** assertion. The frozen plan still contains the old three-SE paragraph and the frozen TOML retains its old value. D-023's statement that this detail was absent from the plan is therefore not relied upon: the owner's explicit test-only override is recorded here while all A hashes remain valid. Production fluctuation behavior is unchanged.

AP10 uses single-regime MC calibration (50000 c observations per regime, 49989 full trailing means), seeds 202611051 and 202611052, the retained analytic means and unchanged `SeedSequence([42,case_index,2])` trajectories. Mixed windows use the specifically approved squared-weight SE approximation. The bound is computed from the **45 valid windows**, not 56 input months: `norm.ppf(1-0.01/(2*45))`. This calibrated synthetic tolerance does not become a market drift p-value or a general family-wise validity claim for the approximate SE.

| Case | T | Bonferroni bound | max absolute error / SE | Exceedances | Midpoint error (months) | max absolute-square difference |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Same sign | 45 | 3.692314726024295 | 3.4509418779209704 | 0 | 5 | 0 |
| Sign flip | 45 | 3.692314726024295 | 2.9423598589915754 | 0 | 5 | 0 |

Both midpoint errors are <= 6; both square differences are <= 1e-18. No failed t remains under the authorized rule. Exact stdout is in `.tmp/phase3a-ap10-d023-pytest.log`; the aggregate values are archived in the reproducibility JSON. Neither calibration seeds nor trajectory seeds were retried or selected for passing behavior.

## Clean formal synthetic execution

Both invocations began and ended with clean `git status` at B. WP6 drafts were temporarily moved into this worktree's ignored `.tmp` and restored only after the two runs, so documentation WIP did not compromise the clean-run condition. Commands were:

```powershell
.\.venv\Scripts\python.exe -B scripts/run_phase3a.py --synthetic-only --output-directory results/phase3a/20261105-53b3e3f-formal
.\.venv\Scripts\python.exe -B scripts/run_phase3a.py --synthetic-only --output-directory results/phase3a/20261105-53b3e3f-reproduction
```

| Run | Pipeline seconds | Peak process bytes | Ridge fits | Output directory |
| --- | ---: | ---: | ---: | --- |
| formal | 98.224268 | 718491648 | 224 | `results/phase3a/20261105-53b3e3f-formal` |
| reproduction | 99.407723 | 717918208 | 224 | `results/phase3a/20261105-53b3e3f-reproduction` |

Each run generated 278528 synthetic bars, evaluated 56 months with both horizons/specifications, produced 901824 prediction rows, and stayed within 1200 seconds and 4000000000 measured process bytes. Measured memory is the process lifetime peak working set, not Python allocations alone. Each run retains all primary/secondary and sensitivity outputs, monthly train indices, model/scaler states, prediction timing, GW instrument audit, placebo precision and descriptive subperiods through 2025H2.

All **19 deterministic files**, including predictions, train-index archive and summary, are byte-identical. `runtime.json` alone is excluded from equality. Artifact names/hashes, source commit, command invocations and timings are archived in [reproducibility.json](phase3a-evidence/reproducibility.json). Large synthetic products remain Git-ignored; no raw market dataset is present in these outputs. The sparse monthly fixture does not establish full-history resource feasibility or estimate market power.

## Acceptance anchors

| Anchor | Verified result |
| --- | --- |
| AP1 | Three frozen primary hashes match manifest; no frozen artifact rewritten. |
| AP2 | Phase 2E source hash and corrected CW/DM inputs preserved and verified. |
| AP3 | 56-month formal/reproduction pair; 19/19 deterministic artifacts byte-identical; 224 fits each. |
| AP4 | 68 planned URLs pass history/holdout gates, no overlap, all not_yet_downloaded, zero requests. |
| AP5 | Seven raw prediction fingerprints pinned at startup; mutation guards tested. |
| AP6 | Twelve full-precision MDE/percentage anchors and displayed values verified. |
| AP6b | Maturity-only spy enforces training reader, fit/predict/truth order and training-only scaler; immature label fails before fitting. |
| AP7 | GW spies prevent immature target/validity access; prior-month maturity audit retained. |
| AP8 | Synthetic ms/us boundary positive and inverse/ambiguous/mixed negative cases pass. |
| AP9 | New private split kernel equals frozen development splitter for both horizons; public historical bounds stay strict. |
| AP10 | Both DGPs satisfy authorized Bonferroni rule, midpoint and square consistency; zero exceedances. |

## Planning budget, limitations and remaining authorization

The 68-month offline URL draft estimates **142606336–320864256 ZIP bytes (136–306 MiB)**. Hard transfer caps remain 402653184 ZIP bytes and 1048576 checksum bytes; disk reserve is 8589934592 bytes. Availability, remote sizes and checksums remain unverified. No request was made to refine these estimates.

For 15m Validation, conditional planning gives CW MDE **2.034874438662173e-9 (19.01395494184081%)**, DM MDE **2.290821099184106e-9 (65.76901835183048%)**, and IC MDE **0.005174042675772705 (15.501303730194117%)**, each in its own units and using the correct one-/two-sided formula. This assumes IID comparable months and stable effects; it is not achieved power. No historical CW conclusion, MSE superiority, generalization or profitability is asserted.

Scope is complete for protocol and synthetic infrastructure. Historical download/evaluation, Holdout-A/B execution, Research Lead's independent acceptance and future full-history capacity checks remain separate. No new dependency was needed. No market request, historical/smoke/holdout market-row access, trading, push, remote creation or PR occurred. Earlier package-sync failures were superseded by D-022; no sync was repeated afterward. The final documentation commit closes the tracked worktree; ignored local logs/results remain for audit.
