# Phase 3A Gate 1 execution record

Status on 2026-11-09: **Gate 1 acquisition and GP7 clean offline reverification passed.** D-034 fix X `ea8f78e9c829026a1dbe05beaab8faa51e246c72`; proof Y `e9f8f176454ddd73b110df165e0402851af48e8b`. D-033 failed evidence remains unchanged and is resolved by the separately recorded second run. Gate 2 is not authorized; Research Lead independent acceptance remains pending.

## Verified preflight

- Worktree: `D:/zgs_project/QuantForge-3a`; branch: `phase3a-protocol-and-impl`.
- Gate 1 work began at HEAD `284fad65a9e86965869997db9773f693a3af18d5`; `main` was at the same commit.
- `git merge --ff-only main`: `Already up to date.` No advancement or merge commit.
- D-028, D-029 and D-030 are present in the local decision log.
- Existing venv imports succeeded: Python 3.13.5, pandas 3.0.6, numpy 2.5.4, scipy 1.18.1, pyarrow 23.0.1, scikit-learn 1.9.1, matplotlib 3.11.2. Executable and quantforge editable path resolve within this worktree. No dependency synchronization or installation was run.
- Three frozen primary artifact hashes, seven prediction-code hashes and the auxiliary frozen download-draft hash match the Phase 3A manifest. The read-only frozen-config loader also passed its pinned manifest, source and typed-contract checks.
- All 68 ordered months and all 136 exact ZIP/CHECKSUM URLs passed historical allowlist and independent holdout-interval checks, including month tokens extracted from the URLs. No request was made.
- Frozen ZIP/checksum/disk budgets, serial concurrency, three-attempt setting, backoff list and timestamp cutover values match the Gate 1 instruction.
- Free disk at the preflight check: 495478493184 bytes, above the 8589934592-byte reserve.
- D-030 resolves Gate 1's synthetic-regression-test exception, confirms **60 ms months + 8 us months**, and sets a hard ceiling of **408 HTTP GETs**. These corrections apply to this execution record only; frozen Phase 3A protocol artifacts remain unchanged.
- WP0 baseline command: `.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider -p gate1_test_counter`. The plugin used Python 3.13 `sys.monitoring` PY_START/PY_RETURN events on existing function code objects; it did not replace implementations. Result: **929 passed, 1 skipped, 930 collected, 320.31 seconds pytest time** (329.95 seconds including plugin/report overhead). Skip: optional Phase 2F historical compatibility test (`QF_C1_HISTORICAL_RESULTS` unset).
- Synthetic test call counts during the baseline: `sklearn.linear_model.Ridge.fit` entered/returned **1,341/1,341** times; `clark_west_differential` **2,164/2,158**; `dm_differential` **1,067/1,059**; `ic_product` **1,058/1,058**. The four/success counts differ when input validation raises. Requested CW+DM+IC entered-call total: **4,289**. Additional instrumentation: `r2_oos` 1,065/1,059; HAC `_stats` 3,889/3,889; power MC 1; `mde` 8,267/8,264; `normal_power` 6,918/6,917; `months_required` 5,956/5,956; `encompassing_approx` 403/403. Count scope is entries to these named Python function code objects inside the existing pytest process, including validation failures; inline calculations and native-library internals are not counted as function calls.
- An initial counter-plugin startup failed during `pytest_configure` because the scratch plugin imported a nonexistent `quantforge.metrics`; pytest collected and executed **zero** tests in that failed invocation. The scratch plugin was corrected and the full baseline above passed.
- An initial focused run invoked the machine PATH `pytest` rather than the project `.venv` interpreter and failed two Parquet tests because that interpreter lacks `pyarrow`. No package installation or environment change occurred; the explicit project `.venv` run passed all 51 focused tests.

The owner's WP0.7/GP9 metadata-only isolation audit was treated as a specific exception to the general other-worktree access restriction. No file contents were read from those worktrees. Baseline paths, counts, sizes and nanosecond mtimes are recorded at the explicitly requested `C:/Users/ttrs_/AppData/Local/Temp/qf-3a-gate1-baseline.txt`, with a local ignored copy in `.tmp/gate1-baseline.json`.

| Worktree | Directory | File count | Bytes |
| --- | --- | ---: | ---: |
| main | `.venv` | 10052 | 397794209 |
| main | `results` | 248 | 596100123 |
| main | `data` | 61 | 60685680 |
| Phase 2F | `.venv` | 10052 | 397797395 |
| Phase 2F | `results` | 1 | 2 |
| Phase 2F | `data` | 2 | 4 |

## Resolved instruction conflicts and remaining implementation checks

- D-030 replaces those three preflight conflicts: existing synthetic regression computation is explicitly allowed; historical month counts are 60+8; total HTTP GETs may reach 408, with at most three total per-month attempts.
- The focused Gate 1 suite now has **51 passed in 16.39 seconds** with the project `.venv`; it covers retry re-fetches, conservative partial-read accounting, path/request guards, calendar failures, transactional staging cleanup, and manifest validation. A final full-suite result after adding the two publication-failure cases is recorded below.

## Implementation observations for continuation

- The requested reference ledger `results/monthly-download/network-ledger.json` does not exist in this worktree. Its structure and reserve/settle retry accounting were reviewed in the existing Phase 2B source instead; no other-worktree ledger was opened.
- The old ZIP extractor has a 4 MiB limit and the old network ledger has a 28 MiB phase cap. Gate 1 needs additive helpers for its frozen 8 MiB per-ZIP / 384 MiB phase limits; old code and constants must remain unchanged.
- Monthly validation should reuse `parse_historical_monthly_klines`, which calls the unchanged monthly validator after the frozen timestamp gate. Calendar comparison uses `pd.DatetimeIndex(frame.open_time)` because the returned frame's index is not the timestamp index. No gap filling, reordering, duplicate deletion or skipping failed months is authorized.
- Every month must pass before proceeding to the next. Integrity failure must preserve an accurate failure ledger and stop, without claiming a complete manifest or Gate 1 success.
- The README success sentence must follow successful acquisition/audit; it cannot truthfully appear in the pre-download implementation commit. A pending-status sentence may be used before execution.
- Enforce both free disk reserve of at least 8 GiB and Gate 1 data footprint of at most 8 GiB, preserving their separate meanings. Track actual payload bytes, conservative unresolved reservations and HTTP request counts separately.
- The frozen `download.authorized=false` and `synthetic_only=true` describe the earlier phase. Gate 1's separate explicitly authorized entry point must leave those frozen bytes and the synthetic runner untouched.

## Historical execution status at the first stop

The downloader made 4 authorized historical GETs, parsed 85,986 historical rows, and stopped at 2020-02 integrity failure. No later market request or real-data research computation occurred. Existing synthetic test computations are counted above under D-030. No Holdout or smoke URL/content access, uv sync, push, remote or PR. This record is included in commit A; no successful 68-month manifest or commit B exists. Failure report/reproducibility documentation is pending as the final record. Ignored preflight scripts/snapshots and pytest count/log evidence remain local. GP1's offline plan component and frozen-hash preflight checks passed; GP2–GP9 are not yet claimed complete.


## Implementation verification before acquisition

- Final full suite: **980 passed, 1 skipped in 325.19 seconds** (929 previous + 51 new); command uses the explicit project `.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider -p gate1_test_counter` and the default `.tmp/pytest` base directory. The prior full run before the final two tests had 978 passed + 1 skipped in 333.34 seconds.
- Three complete valid pytest invocations (WP0, intermediate full suite, final full suite) each observed 1,341 successful sklearn Ridge fits and 4,289 CW/DM/IC function entries. Cumulative engineering-test totals before acquisition: **4,023 synthetic Ridge fits; 12,867 synthetic CW/DM/IC function entries**. The focused Gate 1 tests perform neither fitting nor forecast statistics.
- The counter is local test instrumentation; entered and returned counts and per-function details are retained in `.tmp/gate1-{baseline,full,final}-test-counts.json`. Counts are not research experiments or achieved power.
- Independent review checked locks, immutable config, exact URL/path guards, retry accounting, bounded decompression, strict calendar validation, atomic publication, and offline SHA/Parquet validation. Two discovered staging failure cases were fixed before acquisition and have dedicated synthetic fault tests.
- `data_monthly.py` is append-only. Its original mixed-line-ending prefix (32,755 bytes) hashes to `924f38975ded3d172d4befd1f5fe3204dc2383f5d5e84f01504c257bf55486e9`; normalized original source exactly matches the base commit. The existing 43 `.gitattributes` lines remain unchanged. Seven frozen source files and three frozen primary artifacts match.
- Metadata-only isolation comparison before acquisition: all six main/2F directory snapshots match original path counts, sizes and nanosecond mtimes exactly (0 changed paths). No foreign-worktree content was read.
- Each successful month records ZIP bytes in `bytes_downloaded`, with CHECKSUM bytes separately recorded. Failed payloads and unresolved byte reservations remain charged. Backoff values are inherited unchanged; three total attempts use the first two waits (1 and 4 seconds), with no fourth attempt.
- The ledger's `final_commit_sha` means the last clean implementation commit used for acquisition; documentation commit hashes are recorded separately after completion.
- The only network execution entrypoint is `scripts/download_historical.py --config configs/history-extension.toml --output-root data/raw/binance/spot/BTCUSDT/1m/historical`. It requires a clean designated branch and blocks entry into model fitting, forecasting diagnostics, research runners and online engines. Cross-check downloads remain disabled.


## Acquisition stop

- The fixed entrypoint ran from clean commit `94dd63bd19321eb8aaa4a6e40b773e41b143bb3d` at 2026-11-06 03:53 UTC. `2020-01` passed with 44,640 rows; `2020-02` stopped after 41,346 rows were read from a checksum-valid ZIP. The strict validator found 414 missing minutes and one invalid close duration against the 41,760-row leap-month contract.
- Four GETs were issued (CHECKSUM and ZIP for each of the two months); no retries were needed. ZIP bytes charged: 4,301,660; CHECKSUM bytes: 176. The persistent ledger status is `failed`, with `failed_month=2020-02`; no files for 2020-02 were published.
- At stop: GP1/GP6/GP8/GP9 pass; GP2 is partial; GP3/GP5 are incomplete; GP4 fails; GP7 was not run. The full table is in the report. No continuation or Gate 2 work is authorized by this result.

- The successful manifest commit B and completion/reproduction commit C were not made. A separate failure-documentation commit records the mandatory stop; the clean revalidation scratch helper was prepared but never executed. Metadata-only final comparison still found zero changes in main/2F. Freeze hashes are intact.


## Historical D-032 continuation and second stop

- D-030/D-031 merge: `c9165c6`; D-032 merge: `c72d13a`. Original implementation A `94dd63b` and failure record `1d6e1f2` are preserved. Continuation implementation: `60581a1bcbd582ee305ebd7e13c2c37fb16010f5`. No new success manifest commit B or successful reproduction commit C exists.
- Full synthetic regression: `.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider -p gate1_test_counter`; **1067 passed, 1 skipped in 383.27 seconds**. This retains the 980 baseline and adds 87 tests. The default Phase 2F historical test remains skipped. This run entered 1,341 Ridge fits and 4,289 CW/DM/IC functions; cumulative complete-test-run totals are 5,364 and 17,156. No real-data research was run.
- Implementation review added exact old-attempt checks, continuation/event equality, non-resettable carry-in, graded gaps, fixed-denominator cumulative gap hard stops, and complete manifest transfer reconciliation. All code was committed before acquisition. Strict legacy ingestion and frozen prediction/protocol bytes remain unchanged.

### 2020-02: fail-then-cleanup-then-refetch

- Original failure: 414 missing minutes and one anomalous close-time span; the then-strict parser stopped and the transactional cleanup retained no February artifacts. Original failure evidence remains unchanged in commit `1d6e1f2` and the existing evidence directory.
- Executed the committed CLI through `.tmp/gate1_execute_guarded.py refetch`, forwarding `scripts/download_historical.py --config configs/history-extension.toml --output-root data/raw/binance/spot/BTCUSDT/1m/historical --only-2020-02-refetch`.
- Duration 5.927523 seconds; exactly 2 GETs; ZIP 2,077,466 bytes and CHECKSUM 88 bytes. Running totals became 6 GETs, 6,379,126 ZIP bytes and 264 CHECKSUM bytes (6.0836 MiB ZIP; the approximate 8.6 MiB request estimate was not used as a counter).
- JJ.3 passed: CHECKSUM SHA `d4002dcc1c1e9367fe6fa2369db5159802b3755092e8d451b7cd955012e0c3bf`, ZIP SHA `05aedf91c80f579da396951b1d9e84587d02c9684352e1ff17d4468f556c674e`, and the official declaration all match the old failed ledger.
- JJ.4 passed: 41,760 expected, 41,346 actual, 414 missing, one close anomaly, ms timestamps, first `2020-02-01T00:00:00+00:00`, last `2020-02-29T23:59:00+00:00`.
- D-031 tier is normal, at the upper edge of normal acceptance: 0.99137931% missing is only 0.00862069 percentage points below 1%. This is an imperfect archive. Notes retain `refetch after prior cleanup; gap retained from archive`. Success history has its own evidence; no old failure ledger was replaced.
- All February gates passed, authorizing the already-approved continuation from 2020-03. The later June filesystem incident does not invalidate February's refetch.

### Incident log: 2022-06 atomic ledger write failure

- Continuation command: `.tmp/gate1_execute_guarded.py resume`, forwarding `--resume-from 2020-03` to the same committed CLI. Began `2026-11-06T07:24:06.494866+00:00`, stopped `2026-11-06T07:28:56.525428+00:00`, elapsed 290.030431 seconds, peak working set 296,325,120 bytes.
- 2020-01 through 2022-05 are retained: 29 months, 1,267,835 rows, 28 comparable month boundaries. 2021-04 is the only retained soft-warning month. Every gap/anomaly and its UTC range is listed in the report; gaps remain unfilled.
- At 2022-06 ZIP request id 63, `ledger.settle -> save -> _atomic_json -> temporary.replace` raised `PermissionError [WinError 5]`. The failed ZIP attempt received and charged 1,601,170 bytes; it has no completed SHA or published artifact. CHECKSUM id 62 was received. The cause of the Windows refusal is not established; a later unchanged-byte replacement probe succeeded, which does not identify the locking process.
- Main failed ledger and interrupted temporary ledger agree on all 64 attempts/cumulative byte counters; the temporary last attempt is `started`, the final ledger last attempt is `failed`. Final totals: **64 GETs, 68,371,336 ZIP bytes, 2,816 CHECKSUM bytes, zero unresolved reservations**. Original failed February costs and its refetch are both retained. The resume process alone issued 58 GETs; 58 is not the total.
- 2022-01 needed two attempts after a ZIP URLError with zero ZIP body bytes. This retry stayed within the same running process and existing three-attempt policy. 2022-06 did not retry after the PermissionError.
- June was never parsed. Its failure object's `diagnostics` is stale May metadata, explicitly excluded from June quality claims. The ledger, failure object and temporary file are archived unchanged in `docs/phase3a-gate1-evidence/d032-runtime-stop/`.
- After stopping, a proposed PermissionError retry/recovery implementation was briefly edited and 122 focused synthetic tests passed in 191.72 seconds. It was **not committed or used for network execution**, and was reverted. D-032 JJ.1 requires separate case-by-case authorization for any other cleaned-up failed month; no further GET was made. Recovery also requires a safe persisted ledger design and synthetic failure tests before any separately authorized run.
- Post-stop diagnostic parsing read January once and all 29 retained months twice: 2,580,310 CSV row events. Together with the acquisition ledger's 1,484,447 rows, total explicit parsed-row events are 4,064,757. These additional checks used only ingestion helpers; they were not the required clean full reproduction and did not run the monitoring wrapper. No research function was invoked.
- A scratch note check wrongly required the whole February notes field to equal the mandated substring; it failed because valid gap/anomaly text was also present. This was a scratch assertion error, not a JJ.3/JJ.4 or archive failure. A separate test-path deviation placed synthetic fixtures under results; they were moved to `.tmp/pytest`. Neither caused market access.
- Isolation metadata comparison at `2026-11-06T08:19:00.347932+00:00`: all six main/2F venv/data/results path/size/mtime snapshots unchanged. Seven fingerprints, frozen primary artifacts and plan manifest remain valid. No uv sync, new dependencies, Git config edit, Holdout/smoke access, push, remote or PR.
- Full 68-month manifest, 60+8 distribution, transition-month evidence, 67-boundary audit and clean reproduction are **not completed**. Gate 1 remains failed/incomplete, Gate 2 unauthorized. Further 2022-06 acquisition needs a new explicit case-specific decision; no success B/C SHA is invented.

### Documentation byte audit

The nested runtime-stop directory did not inherit the existing direct-child `-text` attribute. Git normalized CRLF to LF in `environment-isolation.json` and `synthetic-test-counts.json`; their JSON content is unchanged. The initial `evidence-sha256.json` records original local bytes, not those two normalized Git blobs. The new direct-child `d032-original-environment-isolation.json` and `d032-original-synthetic-test-counts.json` preserve original bytes under `-text`. `d032-git-object-sha256.json` records the Git blob SHA and source commit for each artifact plus this exact mapping. Earlier evidence files are not rewritten. This packaging discrepancy is disclosed separately from all market-file hashes and does not affect frozen protocol hashes.


## Historical D-033 implementation, acquisition and GP7 stop

- Merge `7c2b246` includes D-033 once. Initial merge lacked Git identity; established identity was supplied per command, without changing Git configuration.
- Venv imports and paths matched D-022. No uv sync or new dependencies. Focused tests: **22 passed in 19.22 s**; full regression: **1089 passed, 1 skipped in 402.43 s**, retaining all 1067 previous passes. Counter process elapsed: 405.027306 s.
- Implementation `87aacbe2b6c1c0e967d865ddde2b5b87ef356517` preceded network. Old `_atomic_json` and Phase 2B ASTs unchanged; 29 retained artifact sets and original ledger SHA verified before resume.
- This suite observed 1341 synthetic Ridge entries and 4289 CW/DM/IC entries; five-suite cumulative totals are **6705** and **21445**. Real-data counters remain zero. Raw test log and per-function counts are archived.
- Acquisition manifest B `b4683f3c0051b486ef081f310919ab72e7d6f12b` contains all 68 accepted months. The 39-month D-033 suffix took 367.218461 s and issued 80 new GETs. Total acquisition process time including earlier failures: 676.033881 s.
- All-stage totals: **144/408 GETs; 152,094,624/402,653,184 ZIP bytes; 6,336/1,048,576 CHECKSUM bytes**; zero reservations. January 2022 and March 2023 each used two whole-month attempts. March ZIP GET id 83 failed with SSL EOF and zero payload; its second attempt succeeded within the unchanged policy.

### 2022-06: incident recovery after Windows atomic-write transient lock

The original event was at `2026-11-06T07:28:56.525428+00:00`, ZIP GET id 63, with 1,601,170 partial bytes charged and no completed ZIP SHA. The unchanged-byte replacement probe is documented in the [prior incident record](phase3a-gate1-record.md#incident-log-2022-06-atomic-ledger-write-failure) and [original archived ledger](phase3a-gate1-evidence/d032-runtime-stop/network-ledger-at-stop.json). The locking process was not identified. **Original incident June diagnostics are stale May 2022 metadata, not a June quality claim.**

The new helper fsyncs temporary bytes and retries only atomic replace PermissionError, at most 11 attempts, fixed cumulative wait 18.8 s (within 18.9 s). Other OSError subclasses fail immediately; no non-atomic fallback. The six required tests passed: ordinary success, one/five transient failures, exhaustion, non-PermissionError and interruption. ZIP/Parquet/failure writers and the old helper are unchanged.

Executed `.venv/Scripts/python.exe -B .tmp/gate1_execute_d033.py`, forwarding `scripts/download_historical.py --config configs/history-extension.toml --output-root data/raw/binance/spot/BTCUSDT/1m/historical --resume-from 2022-06`. June used 2 GETs, 2,158,139 ZIP bytes and 88 CHECKSUM bytes, 1 attempt. CHECKSUM SHA `30ed4c4ce515aceb2df64d36fd1c87e720f7747c0d3f26c3579b7d718d05905d` matched the old incident digest before ZIP GET; ZIP SHA `0ced8899e9c3b2c098e1c5fc4993250bac22d8684d2d38404310351f35e32c0c` matched the official declaration. New quality: 43,200/43,200 rows, 0 missing, 0 anomalous close rows, `normal`. Acquisition success is linked to B `b4683f3c0051b486ef081f310919ab72e7d6f12b`.

New-helper process counters: retries=1, wait=0.1 s, raw PermissionError=1, hard failures=0. The observed transient replacement error recovered after 0.1 s; per-write events are archived. The prior uninstrumented failure remains separate and is not removed from history.

### Incident log: GP7 timestamp evidence serialization assertion

One clean attempt ran from B using `.venv/Scripts/python.exe -B .tmp/gate1_d033_revalidate.py`, elapsed **48.318307 s**. It verified all 68 raw ZIP/CHECKSUM/CSV/Parquet and source/manifest entries, **2,978,475 rows**, and the 67-boundary comparison before line 66 raised `AssertionError`. The first timestamp artifact was serialized by the CLI via `_atomic_json` in insertion order, while the scratch comparator expected `_historical_json_bytes(..., sort_keys=True)`. Post-stop metadata-only diagnosis found both timestamp files have exactly equal field values/types and exactly match the original writer bytes; their first differing byte against the sorted representation is index 5. This is an authored verification-harness error, not evidence of a market-file hash mismatch. **At the D-033 stop GP7 remained failed; no repair or rerun occurred in that stage. D-034 subsequently resolved it as recorded below.** Failed JSON/stdout, the exact harness, and diagnosis are separately archived. No additional market rows or network were accessed after the stop.

CSV parse events: 3,195,087 acquisition-ledger + 2,580,310 earlier manual checks + 2,978,475 GP7 attempt = **8,753,872**. Unique retained rows: 2,978,475. Partial deterministic coverage is 204 ZIP/CHECKSUM/Parquet files; full 207-file completion is not claimed.

Stop-time isolation at 2026-11-09T02:49:38.560642+00:00: all six main/2F metadata snapshots unchanged; frozen artifacts and seven code fingerprints match. Free disk 495,922,466,816 bytes and historical footprint 437,508,882 bytes satisfy both 8 GiB constraints. Peak across recorded runtime processes: 322,703,360 bytes.

### Final acquisition ledger retained at the stop

Soft-warning month: 2021-04, retained unchanged. All gaps/anomalies and separate UTC intervals appear in the [report](phase3a-gate1-report.md). Aggregates: `{"aggregate_expected_minutes": 2980800, "aggregate_missing_minutes": 2325, "aggregate_missing_ratio": 0.0007799919484702093, "aggregate_status_tier": "normal", "boundary_gap_minutes_total": 0, "evaluate_aggregate_missing_minutes": 1073, "evaluate_aggregate_missing_ratio": 0.000437288080333855, "evaluate_aggregate_status_tier": "normal", "evaluate_expected_minutes": 2453760, "evaluate_months_over_3pct_count": 0, "warmup_aggregate_expected_minutes": 527040, "warmup_aggregate_missing_minutes": 1252, "warmup_aggregate_missing_ratio": 0.002375531268973892, "warmup_months_over_5pct_count": 0}`.

| Month | Started UTC | Finished UTC | ZIP transfer bytes | CHECKSUM bytes | HTTP status | Attempts | Tier |
|---|---|---|---:|---:|---:|---:|---|
| 2020-01 | 2026-11-06T03:53:37.420279+00:00 | 2026-11-06T03:53:41.844419+00:00 | 2224194 | 88 | 200 | 1 | normal |
| 2020-02 | 2026-11-06T07:19:01.176300+00:00 | 2026-11-06T07:19:05.732515+00:00 | 2077466 | 88 | 200 | 1 | normal |
| 2020-03 | 2026-11-06T07:24:08.095933+00:00 | 2026-11-06T07:24:14.606406+00:00 | 2277959 | 88 | 200 | 1 | normal |
| 2020-04 | 2026-11-06T07:24:14.715116+00:00 | 2026-11-06T07:24:19.107221+00:00 | 2164104 | 88 | 200 | 1 | normal |
| 2020-05 | 2026-11-06T07:24:19.215722+00:00 | 2026-11-06T07:24:28.513398+00:00 | 2265162 | 88 | 200 | 1 | normal |
| 2020-06 | 2026-11-06T07:24:28.625661+00:00 | 2026-11-06T07:24:38.600407+00:00 | 2131275 | 88 | 200 | 1 | normal |
| 2020-07 | 2026-11-06T07:24:38.716729+00:00 | 2026-11-06T07:25:19.883190+00:00 | 2203867 | 88 | 200 | 1 | normal |
| 2020-08 | 2026-11-06T07:25:20.003381+00:00 | 2026-11-06T07:25:27.161865+00:00 | 2257706 | 88 | 200 | 1 | normal |
| 2020-09 | 2026-11-06T07:25:27.282520+00:00 | 2026-11-06T07:25:32.970587+00:00 | 2174596 | 88 | 200 | 1 | normal |
| 2020-10 | 2026-11-06T07:25:33.096108+00:00 | 2026-11-06T07:25:38.850676+00:00 | 2220925 | 88 | 200 | 1 | normal |
| 2020-11 | 2026-11-06T07:25:38.996296+00:00 | 2026-11-06T07:25:45.983115+00:00 | 2228577 | 88 | 200 | 1 | normal |
| 2020-12 | 2026-11-06T07:25:46.118869+00:00 | 2026-11-06T07:25:51.544309+00:00 | 2304125 | 88 | 200 | 1 | normal |
| 2021-01 | 2026-11-06T07:25:51.679955+00:00 | 2026-11-06T07:25:59.214732+00:00 | 2399851 | 88 | 200 | 1 | normal |
| 2021-02 | 2026-11-06T07:25:59.353676+00:00 | 2026-11-06T07:26:07.987509+00:00 | 2157176 | 88 | 200 | 1 | normal |
| 2021-03 | 2026-11-06T07:26:08.133814+00:00 | 2026-11-06T07:26:15.645651+00:00 | 2375057 | 88 | 200 | 1 | normal |
| 2021-04 | 2026-11-06T07:26:15.791615+00:00 | 2026-11-06T07:26:21.732105+00:00 | 2265385 | 88 | 200 | 1 | soft_warning |
| 2021-05 | 2026-11-06T07:26:21.884918+00:00 | 2026-11-06T07:26:31.542426+00:00 | 2407992 | 88 | 200 | 1 | normal |
| 2021-06 | 2026-11-06T07:26:31.701791+00:00 | 2026-11-06T07:26:38.871515+00:00 | 2299606 | 88 | 200 | 1 | normal |
| 2021-07 | 2026-11-06T07:26:39.032706+00:00 | 2026-11-06T07:26:46.922672+00:00 | 2322442 | 88 | 200 | 1 | normal |
| 2021-08 | 2026-11-06T07:26:47.084789+00:00 | 2026-11-06T07:26:53.372063+00:00 | 2309698 | 88 | 200 | 1 | normal |
| 2021-09 | 2026-11-06T07:26:53.542179+00:00 | 2026-11-06T07:27:00.081337+00:00 | 2172157 | 88 | 200 | 1 | normal |
| 2021-10 | 2026-11-06T07:27:00.248830+00:00 | 2026-11-06T07:27:10.125092+00:00 | 2259946 | 88 | 200 | 1 | normal |
| 2021-11 | 2026-11-06T07:27:10.297921+00:00 | 2026-11-06T07:27:21.201565+00:00 | 2183167 | 88 | 200 | 1 | normal |
| 2021-12 | 2026-11-06T07:27:21.382866+00:00 | 2026-11-06T07:27:28.148673+00:00 | 2242006 | 88 | 200 | 1 | normal |
| 2022-01 | 2026-11-06T07:27:28.336531+00:00 | 2026-11-06T07:27:53.340694+00:00 | 2229634 | 176 | 200 | 2 | normal |
| 2022-02 | 2026-11-06T07:27:53.532109+00:00 | 2026-11-06T07:28:30.474885+00:00 | 2011133 | 88 | 200 | 1 | normal |
| 2022-03 | 2026-11-06T07:28:30.670505+00:00 | 2026-11-06T07:28:38.972109+00:00 | 2207992 | 88 | 200 | 1 | normal |
| 2022-04 | 2026-11-06T07:28:39.165409+00:00 | 2026-11-06T07:28:44.539598+00:00 | 2103010 | 88 | 200 | 1 | normal |
| 2022-05 | 2026-11-06T07:28:44.765473+00:00 | 2026-11-06T07:28:51.403528+00:00 | 2216492 | 88 | 200 | 1 | normal |
| 2022-06 | 2026-11-09T02:36:55.036227+00:00 | 2026-11-09T02:37:03.966521+00:00 | 2158139 | 88 | 200 | 1 | normal |
| 2022-07 | 2026-11-09T02:37:04.225441+00:00 | 2026-11-09T02:37:09.986564+00:00 | 2298791 | 88 | 200 | 1 | normal |
| 2022-08 | 2026-11-09T02:37:10.234943+00:00 | 2026-11-09T02:37:16.828660+00:00 | 2318381 | 88 | 200 | 1 | normal |
| 2022-09 | 2026-11-09T02:37:17.097709+00:00 | 2026-11-09T02:37:22.230058+00:00 | 2260540 | 88 | 200 | 1 | normal |
| 2022-10 | 2026-11-09T02:37:22.499448+00:00 | 2026-11-09T02:37:29.528786+00:00 | 2300603 | 88 | 200 | 1 | normal |
| 2022-11 | 2026-11-09T02:37:29.748265+00:00 | 2026-11-09T02:37:34.582116+00:00 | 2241831 | 88 | 200 | 1 | normal |
| 2022-12 | 2026-11-09T02:37:34.820733+00:00 | 2026-11-09T02:37:40.734110+00:00 | 2261860 | 88 | 200 | 1 | normal |
| 2023-01 | 2026-11-09T02:37:40.978733+00:00 | 2026-11-09T02:37:48.480745+00:00 | 2304509 | 88 | 200 | 1 | normal |
| 2023-02 | 2026-11-09T02:37:48.728977+00:00 | 2026-11-09T02:37:54.567149+00:00 | 2109501 | 88 | 200 | 1 | normal |
| 2023-03 | 2026-11-09T02:37:54.792278+00:00 | 2026-11-09T02:38:06.216833+00:00 | 2300446 | 176 | 200 | 2 | normal |
| 2023-04 | 2026-11-09T02:38:06.451401+00:00 | 2026-11-09T02:38:12.044662+00:00 | 2064504 | 88 | 200 | 1 | normal |
| 2023-05 | 2026-11-09T02:38:12.335591+00:00 | 2026-11-09T02:38:18.587702+00:00 | 2111728 | 88 | 200 | 1 | normal |
| 2023-06 | 2026-11-09T02:38:18.854117+00:00 | 2026-11-09T02:38:25.318255+00:00 | 2043702 | 88 | 200 | 1 | normal |
| 2023-07 | 2026-11-09T02:38:25.605650+00:00 | 2026-11-09T02:38:32.144430+00:00 | 2033186 | 88 | 200 | 1 | normal |
| 2023-08 | 2026-11-09T02:38:32.443900+00:00 | 2026-11-09T02:38:46.542431+00:00 | 2018512 | 88 | 200 | 1 | normal |
| 2023-09 | 2026-11-09T02:38:46.869506+00:00 | 2026-11-09T02:39:12.542735+00:00 | 1967613 | 88 | 200 | 1 | normal |
| 2023-10 | 2026-11-09T02:39:12.824450+00:00 | 2026-11-09T02:39:29.422761+00:00 | 2099200 | 88 | 200 | 1 | normal |
| 2023-11 | 2026-11-09T02:39:29.686715+00:00 | 2026-11-09T02:39:36.429864+00:00 | 2056098 | 88 | 200 | 1 | normal |
| 2023-12 | 2026-11-09T02:39:36.752348+00:00 | 2026-11-09T02:39:42.195029+00:00 | 2144300 | 88 | 200 | 1 | normal |
| 2024-01 | 2026-11-09T02:39:42.538883+00:00 | 2026-11-09T02:39:48.025935+00:00 | 2169570 | 88 | 200 | 1 | normal |
| 2024-02 | 2026-11-09T02:39:48.368948+00:00 | 2026-11-09T02:39:53.057075+00:00 | 2013843 | 88 | 200 | 1 | normal |
| 2024-03 | 2026-11-09T02:39:53.348467+00:00 | 2026-11-09T02:39:59.526865+00:00 | 2221094 | 88 | 200 | 1 | normal |
| 2024-04 | 2026-11-09T02:39:59.811252+00:00 | 2026-11-09T02:40:05.090827+00:00 | 2120723 | 88 | 200 | 1 | normal |
| 2024-05 | 2026-11-09T02:40:05.347976+00:00 | 2026-11-09T02:40:10.751995+00:00 | 2144250 | 88 | 200 | 1 | normal |
| 2024-06 | 2026-11-09T02:40:11.097186+00:00 | 2026-11-09T02:40:17.465791+00:00 | 2018857 | 88 | 200 | 1 | normal |
| 2024-07 | 2026-11-09T02:40:17.826814+00:00 | 2026-11-09T02:40:23.677242+00:00 | 2141506 | 88 | 200 | 1 | normal |
| 2024-08 | 2026-11-09T02:40:24.040947+00:00 | 2026-11-09T02:40:29.581870+00:00 | 2164472 | 88 | 200 | 1 | normal |
| 2024-09 | 2026-11-09T02:40:29.932506+00:00 | 2026-11-09T02:40:34.457936+00:00 | 2049758 | 88 | 200 | 1 | normal |
| 2024-10 | 2026-11-09T02:40:34.759856+00:00 | 2026-11-09T02:41:00.139355+00:00 | 2121607 | 88 | 200 | 1 | normal |
| 2024-11 | 2026-11-09T02:41:00.523958+00:00 | 2026-11-09T02:41:29.024758+00:00 | 2146495 | 88 | 200 | 1 | normal |
| 2024-12 | 2026-11-09T02:41:29.415498+00:00 | 2026-11-09T02:41:37.692057+00:00 | 2218893 | 88 | 200 | 1 | normal |
| 2025-01 | 2026-11-09T02:41:38.107734+00:00 | 2026-11-09T02:41:50.539127+00:00 | 2222830 | 88 | 200 | 1 | normal |
| 2025-02 | 2026-11-09T02:41:50.889381+00:00 | 2026-11-09T02:42:04.571902+00:00 | 1993970 | 88 | 200 | 1 | normal |
| 2025-03 | 2026-11-09T02:42:05.467789+00:00 | 2026-11-09T02:42:17.571802+00:00 | 2215591 | 88 | 200 | 1 | normal |
| 2025-04 | 2026-11-09T02:42:17.991839+00:00 | 2026-11-09T02:42:33.482116+00:00 | 2120466 | 88 | 200 | 1 | normal |
| 2025-05 | 2026-11-09T02:42:33.829652+00:00 | 2026-11-09T02:42:39.354560+00:00 | 2184868 | 88 | 200 | 1 | normal |
| 2025-06 | 2026-11-09T02:42:39.744750+00:00 | 2026-11-09T02:42:46.041180+00:00 | 2074513 | 88 | 200 | 1 | normal |
| 2025-07 | 2026-11-09T02:42:46.457555+00:00 | 2026-11-09T02:42:53.083957+00:00 | 2136490 | 88 | 200 | 1 | normal |
| 2025-08 | 2026-11-09T02:42:53.457161+00:00 | 2026-11-09T02:42:59.478137+00:00 | 2150048 | 88 | 200 | 1 | normal |

This table covers successful acquisition events. Original February failure and June partial transfer remain additional charged costs. Every GET and retry remains in `d033-network-ledger.json`.

D-033 date prose counts were corrected arithmetically without changing frozen bytes: June 2022 through August 2025 is 39 months; 38 follow June. Original carry-in 68,371,336 bytes is 65.203987 MiB. Raw CRLF evidence stays byte-preserved; per-command diff whitespace checking recognized CR-at-EOL without a Git configuration change.

A local failure-documentation commit records this stop, not a successful completion C. Gate 2 remains unauthorized. No Holdout/smoke access, real-data research, dependency sync, push, remote creation or PR. This was the historical D-033 stop; the subsequent D-034/D-035 continuation is recorded below.


## GP7 clean reverification

**harness bug fix: dropped the redundant sort_keys=True comparison channel per D-034**. Only the new `scripts/revalidate_historical.py` and its three independent synthetic tests comprise fix X `ea8f78e9c829026a1dbe05beaab8faa51e246c72`. The old `d033-revalidation-wrapper.py`, failed run, diagnosis, writers, data artifacts, manifest and boundary JSON bytes remain unchanged. GP7 verifies writer-defined byte reproducibility; the insertion-order writer is the existing design. Canonical sorted migration would require a separate protocol and is outside this fix.

The D-034 UU.2 acceptance criterion is:

```python
rewritten = writer.serialize(reparsed_value)   # 用与写入时相同的 serializer
file_bytes = Path(target).read_bytes()
assert hashlib.sha256(rewritten).hexdigest() == hashlib.sha256(file_bytes).hexdigest()
```

The unchanged writer uses `json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"`, encoded as UTF-8, without key sorting. The verifier also requires direct byte equality. Boundary expected values come from the freshly verified monthly values; all prior ZIP/CSV/Parquet and typed manifest comparisons remain.

Preflight merges: D-034 `139a321`, D-035 `7ee5ab1`, each heading visible once. D-035 arrived during two-file WIP; unique stash `d034-verifier-wip-before-d035-20261109112009853`, SHA `9ff14e170e7157f85574264c502a5dd640b8e95e`, was applied and dropped only after exact pre-stash hashes matched. Git checkout converted LF to CRLF; only those two new files were restored to their prior LF bytes. No Git configuration changed. D-022 environment and imports matched; no uv sync or installation.

Command: `.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider -p gate1_test_counter`. Result: **1092 passed, 1 skipped in 390.02 s**, retaining 1089 passes plus 3 new serializer/tamper tests. This run observed 1341 synthetic Ridge entries and 4289 synthetic CW/DM/IC entries; six-suite totals are 8046 and 25734. The optional real-artifact dry-run test was not added; the single explicit GP7 execution below supplies that evidence.

After clean X, ran `.venv/Scripts/python.exe -B scripts/revalidate_historical.py`. All 68 months, 2,978,475 rows, 67 boundaries and 207 deterministic files passed; no network or real-data research was invoked. Three JSON writer checks passed (two timestamp artifacts and the manifest). Proof is [d034-revalidation.json](phase3a-gate1-evidence/d034-revalidation.json), committed in Y `e9f8f176454ddd73b110df165e0402851af48e8b`.

| Run | Result | Start UTC | Finish UTC | Artifact timestamp | Seconds |
|---|---|---|---|---|---:|
| D-033 run 1 | Failed harness comparison | Not recorded | Not recorded | 2026-11-09T02:46:17.847124+00:00 (local report mtime only) | 48.318307 |
| D-034 run 2 | Passed | 2026-11-09T03:28:42.254672+00:00 | 2026-11-09T03:29:25.218559+00:00 | Recorded by harness | 42.963904 |

The prior artifact mtime is not a measured finish time; no prior start/finish UTC was fabricated.

[The immutable incident diagnosis](phase3a-gate1-evidence/d033-revalidation-failure-diagnosis.json) already recorded `matches_original_writer_bytes: true`. The second run independently matches the same hashes:

| JSON | Original and second-run SHA-256 | Match |
|---|---|---|
| docs/phase3a-gate1-evidence/timestamp-boundary-2024-12.json | `28899d1bb0b22d5f3bff8635213f11f2bb01363c2273c55b11b6e2a17be5130d` | True |
| docs/phase3a-gate1-evidence/timestamp-boundary-2025-01.json | `197437de1f247315840b5c600a1b8dfdf50c03db9bcb0f242d63920fc29822ab` | True |
| data/processed/binance/spot/BTCUSDT/1m/historical/historical-manifest.json | `71920d90d95a3d6600db3b57540865949b8784ce8753b0dccfbea93d7bb1fa02` | True |

Cumulative parsed-row events are now **11,732,347**, including both GP7 attempts. Unique rows remain 2,978,475. Download totals are unchanged: 144 GETs, 152,094,624 ZIP bytes, 6,336 CHECKSUM bytes. Post-run isolation at 2026-11-09T03:30:08.620545+00:00: all six main/2F snapshots unchanged. All 273 historical artifact stat records and 45 protected local byte hashes match preflight; frozen hashes and seven fingerprints match.

Under D-035, `success_reproduction_commit_c` points to evidence commit Y, with the adjacent `semantics` explanation. Z is located by Git history and the subsequent Research Lead merge, so no self SHA or placeholder is embedded. Historical `failure_type` and `failure_reason` remain, with `resolved_in_commit` pointing to X. No Gate 2, Holdout/smoke access, trading, push, remote, PR or dependency change.
