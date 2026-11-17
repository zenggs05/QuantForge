# Phase 2F execution record

Protocol freeze from 9894ca25fe30812944e5a933fa5878315d876dd0 under D-025/D-026. Implementation, mutation and both clean synthetic runs completed; optional historical integration remains unexecuted.

WP0: git merge --ff-only main returned Already up to date. Clean branch phase2f-engine-hardening. Earlier baseline command `.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider` completed 794 passed in 295.40 s; only research state documents changed since that run.

Python 3.13.5; uv 0.11.6 (version check only); pandas 3.0.6, numpy 2.5.4, scipy 1.18.1, pyarrow 23.0.1, sklearn 1.9.1, matplotlib 3.11.2. Local executable/import assertions passed. No uv sync/install.

Environment metadata baseline: main 10052 files, Phase 3A 10052 files; full size/mtime_ns snapshots in ignored `.tmp/phase2f-wp0/environment-before.json`. Metadata only.

Frozen plan SHA-256: `7e990f5a1a075860684f8e3ef6331bc97d814a569acffa8a2f03c6f66c5a3d11`.
Frozen config SHA-256: `ebaee87992fedac0ac00c02476bd37fe1c35add7a7c76cf149c4e8e986c50e29`.
Frozen manifest SHA-256 (external anchor, not self-referential): `41412e52bae4669cf2a7b76070c55b4fc38aa6337753e5e7818fe6ebd237fbc0`.

Seven prediction fingerprints match the original protocol. Original .gitattributes 37 lines preserved verbatim.

Pre-freeze diagnostics: original v2 close formula failed the R2 gate and was stopped/reported. D-026 revised close DGP with fixed suggested sigma values and legal OHLC bounds passed synthetic feasibility; no parameters were searched. Initial/full synthetic OLS R2 approximately .23974168/.40803949. No market evidence.

Initial session deviation: before reading the first task attachment, the IDE-default Phase 3A directory received one read-only Git status/log and AGENTS/README inspection. No file was edited there. All subsequent development used Phase 2F; authorized environment isolation reads are metadata-only.


## Completed implementation and tests

Freeze commit A: `296c7a434234c3470ca73eccdc85b6565b8fab06`.
Implementation commit B: `c893d757d18fead4993f2883fa61039c3305b5e5`.
Documentation commit C is the commit introducing this completed record; its object ID is reported after commit rather than self-referenced here.

Implementation B changed 28 files, +2590/-1 lines: src +850/-1, tests +1339/-0, tools +401/-0. The sole removed line is the old solve signature, replaced with solve(cross_check=False). New online modules: audit_log, checkpoint_v2, batch_oracle, hardening_config, qr_reference. New APIs include separated checkpoint save/load, dual maturity-set derivation/audit, raw-row QR update/solve/state_bytes and explicit attach_cross_check_data. Default v1 save bytes, solve math, feature/label/scaler contracts and existing foundation functions remain unchanged.

Final full suite: **929 passed, 1 skipped in 299.54 seconds** (794 old +135 new passing tests; one optional historical test). Full JUnit is ignored `.tmp/phase2f-commit-b-tests.xml`, SHA recorded in evidence/test-summary.json. Immediately afterwards, six new compatibility cases were moved unchanged to the specifically requested test_phase2f_v1_v2_equivalence_on_synthetic.py filename; the affected checkpoint/forgery/compatibility collection passed **31 tests in 10.76 seconds**. A trailing blank EOF was removed before final B; no test behavior changed. Existing test bodies remain byte-preserved; conftest only gained a new fixture.

Earlier full suite: 926 passed, 1 skipped /302.25 s, before three reproduction-gate tests were added. Selected development checks: old Ridge 48 passed; new numerical 38 passed; oracle/replay/foundation 52 passed; checkpoint/replay with additions 95 passed, 1 skipped. These overlap and are not added to the final unique test count.

## Exact commands and paths

All development commands used workdir D:/zgs_project/QuantForge-2f. Session environment set PYTHONDONTWRITEBYTECODE=1, OMP_NUM_THREADS=1, OPENBLAS_NUM_THREADS=1, MKL_NUM_THREADS=1, TEMP/TMP to the project .tmp. Individual agent test runs used distinct subdirectories below .tmp/pytest to avoid mutual cleanup.

```powershell
git merge --ff-only main
uv --version
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp=.tmp/pytest/commit-b --junitxml=.tmp/phase2f-commit-b-tests.xml
.\.venv\Scripts\python.exe -B tools/mutation_check/run_kill_vector.py --output .tmp/phase2f-mutation-final
.\.venv\Scripts\python.exe -B tools/phase2f_validate.py --kind formal
.\.venv\Scripts\python.exe -B tools/phase2f_validate.py --kind reproduction
```

No scripts/run_*.py, download or validate CLI was executed. Existing original tests retain mocked synthetic runner calls. Commands were sequenced with tests before commit B, then clean formal/reproduction, then documentation. Commit identity was passed per command with git -c user.name / -c user.email; no persistent Git configuration changed. B was amended once before any formal run only to remove two trailing blank lines; final B above is the implementation used by both runs.

Formal output: `.tmp/phase2f-runs/20261105T094225281952Z-f4c9af12`.
Reproduction: `.tmp/phase2f-runs/20261105T094351240241Z-05c94889`.
Both start/end clean-source checks passed at B; all 16 deterministic artifacts are byte-identical. Runtime 5.3764397000 /5.3716383000 s; OS peak 181350400 /182120448 bytes. Complete budgets, dependencies and host threadpool records appear in the committed summaries. Exactly one formal and one reproduction reservation exist; no retries.

Mutation final vector: 103.816376 s, 10 valid kills, all return-code triples 0/1/0 and expected semantic AssertionError. Earlier development vector: 101.446706 s, also 10/10; repeated because replay validation/sidecar code changed before final B. Combined 205.263082 s remains below 1800 s. Final mutation source hashes match B working bytes. Mutation copies remain isolated below the explicitly authorized system-temp qf-2f-mutants subtree; no cleanup/deletion of important files occurred. Full stdout records remain ignored in .tmp/phase2f-mutation-final.

## Acceptance anchors

| Anchor | Result |
| --- | --- |
| BP1(a) | Optional; skipped, environment variable unset. No historical checkpoint I/O; Research Lead may perform before merge. |
| BP1(b) | Passed: same synthetic events, v1/v2 restore and continuation payload/bytes agree for both horizons. |
| BP2 | Passed: f1/f4/r3/c4/c5/c6/i9/i15/a8/h3 valid semantic kills; all three controls. |
| BP3 | Passed: all 16 mean-shift cases within graded errors; both extreme cases reject. |
| BP4 | Passed: compensated 0 ULP, plain 475712 ULP, fsum reference 10000000001.0. |
| BP5 | Passed: 794 old +135 new tests; 929 passed and one optional skip. |
| BP6 | Passed: released and valid-update mismatch channels independently reject; valid mixed state passes. |
| BP7 | Passed: AST forbidden-attribute/subscript checks on both independent references. |
| BP8 | Passed: exact mandatory limitation paragraph in plan/report; no positive absolute security claim. |
| BP9 | Passed: main/3A each 10052 before/after, zero size/mtime_ns changes. |
| BP10(a) | Passed: plan/config byte hashes match manifest. |
| BP10(b) | Passed: external manifest SHA in this record matches manifest bytes. |
| BP10(c) | Passed: all seven original confirmatory source fingerprints unchanged. |

## Failures, interpretations and deviations

- v1/v2 task preflight stops and D-025/D-026 repairs occurred before A, as documented by Research Lead. Original low-noise sine fixture failed R2; fixed log-random-walk values now pass. No old protocol or evidence was rewritten.
- D-026 sample high/low surrounded only close while open was previous close. The implementation brackets both open and close using existing OHLC semantics. Close DGP, RNG draw order, seed and sigma defaults remain fixed. No sigma adjustment or seed search was needed.
- D-026 omitted-invalid-release example says both hashes differ, but the specified algorithm changes only release SHA; valid-update SHA correctly remains unchanged. Tests and report use the explicit two-set algorithm. Version validation is structural; absent historical raw targets prevent recomputing all old solve fingerprints.
- WP0 metadata snapshot permission is the explicit exception to the broader cross-worktree content restriction. Entire environments were stat-checked (stronger than the specified depth-limited sampling); only names/sizes/mtime were read. Main and 3A each stayed 10052 files with zero changes before implementation and after both runs.
- Default v1 save remains version 1; separated v2 is opt-in. Published v2 log pairs are immutable because the full-file SHA anchors the snapshot; new checkpoint logs repeat history. Memory is not bounded after reconstruction. This interpretation was frozen in A.
- A development dispatch draft attempted v2 probing before path validation; code review caught and replaced it before final tests. No forbidden file was actually accessed. The final dispatcher rejects invalid paths before stat/open and verifies v2 file SHA before state/log parsing. Malformed unrelated sidecars do not obstruct valid v1 reads.
- Initial >32 MiB fixture sizing produced a 25,187,739-byte log, below the intended test threshold; the pure synthetic ID length was doubled. Final 50,353,563-byte log passes without changing any numerical input, bound or seed. Capacity proof does not simulate 13 days of market activity.
- Additional developer-only numerical sanity probes used seed42, seeds0..4 and seed2 before the frozen stress evaluator, contrary to the intended single development seed convention. They were small in-memory implementation checks, not parameter selection, formal evidence or market research; disclosed rather than omitted. Frozen acceptance/formal cases exclusively use seed20261105. Independent review also used normal/constant/offset synthetic old-source comparisons; they are not claimed as extra formal runs.
- The configuration retains numerical_cross_check_tolerance=1e-10 but graded acceptance supersedes a flat threshold. This field does not drive the decision. drift_warning_factor does not activate drift monitoring. Other active thresholds are read from the hashed config; legacy math remains untouched.
- Formal tooling reuses shared synthetic test helpers and pytest fixture factories; the existing dev environment is therefore required. It never invokes the old real-data runner. Raw synthetic outputs stay in ignored .tmp; only aggregates and small hash anchors are committed.
- One PowerShell command mistakenly contained a Bash heredoc and failed during shell parsing; it ran no Python or filesystem action. A direct pytest wildcard attempt did not expand; explicit file paths resolved it. Neither was a mutation kill or formal attempt. No package/tool upgrade was used.
- The initial IDE-default read-only Phase 3A inspection is disclosed above. No external-worktree content write, market request, Holdout/smoke I/O, trading or remote publication occurred.

No requested implementation is left unfinished. Optional BP1(a) and Research Lead independent acceptance remain explicitly unexecuted review steps. The documentation commit leaves the tracked worktree clean; local ignored test logs, synthetic outputs and reservations remain for audit. No push, remote, PR, network, global environment change or CLAUDE/research-state edit was performed.
