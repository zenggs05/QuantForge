# Phase 3A Gate 1 integrity audit: acquisition and offline reproduction passed

Status on 2026-11-09: **passed**. All 68 authorized monthly archives were acquired and retained. After the D-034-authorized verifier correction, GP7 run 2 passed all 68 raw/Parquet/monthly-manifest records, 67 boundaries and the original-writer JSON byte checks: **207 deterministic files**. The earlier D-033 run remains a preserved failure caused by the verifier's extra sorted-JSON comparison. **Gate 1 integrity validation passed; Gate 2 remains unauthorized.**

At the original D-033 stop, work halted without an unapproved repair or repeat run. D-034 subsequently authorized a narrow verifier correction, three synthetic tests and one further offline GP7 run. That authorized run succeeded without a network request, writer change or data-artifact change. The original failed wrapper, failure report and metadata-only diagnosis remain unchanged. The earlier success-report generator was not executed.

This report supersedes the current narrative of the first stop without replacing its evidence. The original failed ledger `results/phase3a-gate1/network-ledger.json` and failure-documentation commit `1d6e1f2` remain preserved. Its SHA-256 is `d1cfab2fc754ed38f4a1281c109670d9d361d4d12f05b5c4001e0f76e8d72955`. The separate continuation ledger is `results/phase3a-gate1/network-ledger-continuation.json`, SHA-256 `a68517f7dda44775ef6c20dfb1c88a144cebe1b05c1583662101318a533d9379`.

## Provenance and execution stages

D-033 merge: `7c2b246`. Clean tested implementation A: `87aacbe2b6c1c0e967d865ddde2b5b87ef356517`. Manifest/acquisition evidence B: `b4683f3c0051b486ef081f310919ab72e7d6f12b`. Frozen protocol commit: `927145671910df600a1081d4ce6478439ca4b0a4`. The original failure-documentation commit remains in history. D-034/D-035 merges are `139a321` and `7ee5ab1`; verifier/test commit X is `ea8f78e9c829026a1dbe05beaab8faa51e246c72` and successful GP7 evidence commit Y is `e9f8f176454ddd73b110df165e0402851af48e8b`. Documentation commit Z contains this report. No frozen protocol/configuration bytes or seven prediction-source fingerprints changed.

Under D-035, `success_reproduction_commit_c` points to **Y, the evidence-bearing commit**, not Z. Its accompanying semantics are: "points at Y, the evidence-bearing commit; Z is located by Git history and the subsequent merge commit on main". Z's own SHA is obtained from Git history and later merge provenance, never embedded in its own contents. The sequence is X (verifier and tests), Y (successful run evidence), Z (record/report/reproducibility); no fourth metadata commit is required.

The complete manifest's `status=passed` describes the acquisition and graded integrity checks. Overall Gate 1 is now also `status=passed` on the separate evidence from GP7 run 2; the original run remains `status=failed` in its immutable historical evidence. Manifest file SHA-256: `71920d90d95a3d6600db3b57540865949b8784ce8753b0dccfbea93d7bb1fa02`; manifest canonical `overall_sha256`: `864772f0c39367ba0a51a2017d6d75bb71f000fe37ea9f7b939cc601001acc84`.

| Stage | Outcome | New GETs | New ZIP body bytes | New CHECKSUM body bytes |
|---|---|---|---|---|
| Original run, January/February 2020 | Strict validator stopped at February | 4 | 4301660 | 176 |
| D-032 February-only refetch | Passed unchanged-byte checks | 2 | 2077466 | 88 |
| D-032 resume from March 2020 | Stopped during June 2022 transfer | 58 | 61992210 | 2552 |
| Carry-in subtotal before D-033 | Historical incidents remain charged | 64 | 68371336 | 2816 |
| D-033 June 2022-August 2025 | Acquisition passed | 80 | 83723288 | 3520 |
| D-034 GP7 run 2 | Offline reproduction passed | 0 | 0 | 0 |
| All-stage cumulative | Gate 1 integrity passed | 144 / 408 | 152094624 / 402653184 | 6336 / 1048576 |

| Accounting component | ZIP bytes | CHECKSUM bytes |
|---|---|---|
| successful_payload_bytes | 148415988 | 5984 |
| retry_body_bytes | 0 | 176 |
| prior_failed_payload_bytes | 3678636 | 176 |
| cumulative_body_bytes | 152094624 | 6336 |
| unresolved_reserved_bytes | 0 | 0 |
| cumulative_charged_bytes | 152094624 | 6336 |

Body bytes exclude HTTP headers, TCP and TLS. The normal 68-month path is 136 GETs. The actual 144 include two extra original-February GETs, two original-June GETs and two whole-month retries of two GETs each (January 2022 and March 2023). Both retrying ZIP requests received zero body bytes after an SSL unexpected-EOF error; their extra CHECKSUM bodies total 176 bytes. Each retry used the existing at-most-three-attempt policy. No monthly attempt count exceeded three.

Original failed-February ZIP bytes (2,077,466) and partial-June ZIP bytes (1,601,170) remain charged, as do their 88-byte CHECKSUM bodies. Unresolved reservations are zero. The February-refetch endpoint remains 6 GETs / 6,379,126 ZIP bytes / 264 CHECKSUM bytes. The 68,371,336-byte D-033 carry-in equals 65.203987 MiB; approximate prose estimates were not used as measured budgets. The D-033 post-stop metadata measurement recorded **437,508,882 bytes** of historical data footprint and **495,922,466,816 bytes** of free disk. These satisfy the 8 GiB footprint ceiling and reserve floor. The reproducibility JSON and execution record retain those measurements.

D-033 acquisition ran from `2026-11-09T02:36:53.927918+00:00` to `2026-11-09T02:43:01.146533+00:00` for **367.218461 seconds**, peak process memory **272,384,000 bytes**. The first offline reproduction failed after **48.318307 seconds**, peak memory **322,703,360 bytes**. D-034 run 2 passed in **42.9639037 seconds**, peak memory **329,981,952 bytes**. The two-run chronology is detailed below. These process runtimes include integrity work and differ from individual request runtimes.

Retained unique archive rows: **2,978,475**. Cumulative CSV parse events are **11,732,347** = **3,195,087** acquisition-ledger events + **2,580,310** prior manual integrity rechecks + **2,978,475** rows from failed GP7 run 1 + **2,978,475** rows from successful GP7 run 2. The total at the D-033 stop was 8,753,872; before D-033 it was 4,064,757. Repeated parse events are not unique observations; Parquet equality checks are not counted again in this CSV-event total. The post-stop failure diagnosis reads only JSON/source metadata and adds zero market rows.

## 2020-02: fail-then-cleanup-then-refetch

The original strict run stopped at 2026-11-06 03:53:47 UTC. February contained **41,346 / 41,760 expected minutes**, **414 missing minutes**, and **1 anomalous close_time row**. Unsuccessful staging was cleaned up and the failure evidence retained. D-031 subsequently accepted these unchanged archive properties under its graded policy; this does not retroactively make the strict run successful.

D-032 allowed exactly one February CHECKSUM GET and one February ZIP GET. The refetch completed at 07:19:05 UTC. ZIP and CHECKSUM content hashes match the original failed transfer:

| Artifact | SHA-256 |
|---|---|
| ZIP | `05aedf91c80f579da396951b1d9e84587d02c9684352e1ff17d4468f556c674e` |
| CHECKSUM file content | `d4002dcc1c1e9367fe6fa2369db5159802b3755092e8d451b7cd955012e0c3bf` |
| CSV | `9729203de674120dfb1eed340059421998e07acd262fffa365b8d182461d959d` |
| Published Parquet | `688c0b29cb7259f9cf1cd87f19322744d779fc307b0a30c32b80d27adce20641` |

The success entry records `attempts=1` for the authorized refetch and `historical_attempts=2` across original failure and refetch. Its history links the cleanup at `1d6e1f2` and refetch success at `60581a1`. The event is retained in `results/phase3a-gate1/continuations/2020-02-refetch.json`. No gaps were filled, rows reordered or anomalous close times rewritten.

Command: `.venv/Scripts/python.exe -B .tmp/gate1_execute_guarded.py refetch`, forwarding `scripts/download_historical.py --config configs/history-extension.toml --output-root data/raw/binance/spot/BTCUSDT/1m/historical --only-2020-02-refetch`. The two official GETs took 5.927523 seconds and transferred 2,077,466 ZIP bytes plus 88 CHECKSUM bytes. JJ.3 verified both original file hashes and the official checksum declaration. JJ.4 reproduced 41,760 expected / 41,346 actual minutes, 414 missing, one anomalous close row, ms units, first open `2020-02-01T00:00:00+00:00` and last open `2020-02-29T23:59:00+00:00`.

February lies at the upper edge of **normal acceptance**: 0.99137931% missing, only **0.00862069 percentage points** below the 1% cutoff. Normal does not mean gap-free. The notes explicitly retain `refetch after prior cleanup; gap retained from archive`. All February gates passed, so the already-authorized March-onward continuation proceeded; the later June filesystem stop is a separate incident. At that earlier D-032 stop no manifest B or reproduction C existed. D-033 subsequently produced manifest commit B `b4683f3c0051b486ef081f310919ab72e7d6f12b`; its first GP7 run failed as described below. The authorized D-034 second run later passed, with evidence committed in Y `e9f8f176454ddd73b110df165e0402851af48e8b`. The original history remains unchanged; the acquisition history links the retained February entry to B and D-035 assigns the successful reproduction evidence field to Y.

## 2022-06: incident recovery after Windows atomic-write transient lock

The original June event remains preserved in `docs/phase3a-gate1-evidence/d032-runtime-stop/2022-06-failure.json` and `network-ledger-at-stop.json`, with documentation commit `e0caaaf` and its byte-audit follow-up `011a631`. At **2026-11-06T07:28:56 UTC**, ZIP GET id 63 received **1,601,170 bytes** before `PermissionError [WinError 5]` while replacing the network ledger. It produced no completed ZIP SHA, parsed June rows or published June entry. Its `failure.diagnostics` contains stale **May 2022** metadata, not June quality evidence. An unchanged-byte replacement probe later succeeded; the original locking process or operating-system cause was not identified. See [the original incident record](phase3a-gate1-record.md#incident-log-2022-06-atomic-ledger-write-failure).

D-033 added `_atomic_json_with_retry` solely for the mutable ledger path. The original `_atomic_json`, ZIP/CSV/Parquet writers and failure-evidence writers remain unchanged. Only atomic-replace `PermissionError` is retried, with `(0.1, 0.2, 0.5, 1.0, 2.0, 3.0, 3.0, 3.0, 3.0, 3.0)` seconds; 11 failed attempts raise `LedgerPersistenceError`. Other `OSError` values pass through; there is no non-atomic fallback. The specified tuple sums to **18.8 seconds**, correcting the attachment's 18.9-second arithmetic. The six required synthetic cases passed: ordinary success, one transient failure, five transient failures, exhaustion cleanup, other-error passthrough and interruption preservation.

Executed `.venv/Scripts/python.exe -B .tmp/gate1_execute_d033.py`, forwarding `scripts/download_historical.py --config configs/history-extension.toml --output-root data/raw/binance/spot/BTCUSDT/1m/historical --resume-from 2022-06`. Fresh June acquisition started `2026-11-09T02:36:55.036227+00:00` and finished `2026-11-09T02:37:03.966521+00:00`. It used **2 GETs**, **2,158,139 ZIP bytes**, **88 CHECKSUM bytes** and **1 attempt**.

PP.1 matched the new CHECKSUM text SHA against the digest preserved in the archived incident before issuing the ZIP GET. The old ledger preserves that digest, not the original CHECKSUM payload. PP.2 matched the newly completed ZIP SHA against its official CHECKSUM declaration. No nonexistent old June ZIP SHA was invented.

| June recovery field | Measured result |
|---|---|
| CHECKSUM text SHA-256 | 30ed4c4ce515aceb2df64d36fd1c87e720f7747c0d3f26c3579b7d718d05905d |
| ZIP SHA-256 | 0ced8899e9c3b2c098e1c5fc4993250bac22d8684d2d38404310351f35e32c0c |
| CSV SHA-256 | c7fc93851aa95f0297e2bb2910cdbd3253b6ab154190054c510a1318f5eb04e7 |
| Parquet SHA-256 | 99ceb72b8bbb76c9020fcb8c6f4d0122626f7e3ab5e401231179afe2ebc53688 |
| Actual / expected minutes | 43200 / 43200 |
| Missing minutes | 0 |
| Anomalous close_time rows | 0 |
| Tier | normal |
| Timestamp unit | ms |

The new June entry is **normal**, with no missing minutes or close-time anomalies. It is linked to manifest B `b4683f3c0051b486ef081f310919ab72e7d6f12b`; the original 1,601,170 partial bytes remain charged and incident evidence unchanged. June passed, so acquisition continued through August 2025. July 2022-August 2025 contains **38 months**, correcting the attachment's 37; June plus that suffix is **39 months**. D-033 made 80 GETs, including the March 2023 retry, taking the total from 64 to 144.

Observed D-033 persistence counters: **persistence_retries_count_total=1**, **persistence_wait_seconds_total=0.1**, **raw_permission_error_count=1**, **permission_error_hard_fail_count=0**. These cover the new helper process only; the old uninstrumented June PermissionError is disclosed separately. Acquisition completed; the later GP7 run-1 harness failure initially prevented overall success. D-034 resolved that verifier defect and run 2 passed, without changing these D-033 persistence counters or transfer totals.

## Historical GP7 run 1 failure and metadata-only diagnosis

The first offline reproduction started from clean manifest B `b4683f3c0051b486ef081f310919ab72e7d6f12b`. Before the failure it verified all 68 official ZIP/CHECKSUM/CSV digests, exact Parquet bytes and round trips, sidecar/monthly-manifest values, and all 67 cross-month boundaries. Its JSON records 68 verified entries and **2,978,475 rows_revalidated**. Thus **204 local ZIP/CHECKSUM/Parquet artifacts** were verified, along with embedded CSV digests and sidecar metadata. Run 1 was not a successful 207-file GP7 completion; the later successful run is recorded separately below.

The scratch wrapper then compared `boundary.read_bytes()` with `data_monthly._historical_json_bytes(expected)`. That serializer uses `sort_keys=True`; the acquisition CLI wrote timestamp evidence using `_atomic_json`, whose `json.dumps(..., indent=2, ensure_ascii=False, allow_nan=False)` preserves insertion order. The first byte assertion failed at the December 2024 boundary artifact. The final manifest-byte assertion and success update were not reached. The failed wrapper emitted `status=failed`, `error.type=AssertionError` and an empty exception message.

A subsequent read-only diagnosis compared the two existing JSON artifacts, reconstructed expected metadata from the existing manifest, and inspected the two serializers. Both decoded values are equal and both artifacts match the original writer's bytes; the sorted serializer produces different byte hashes. This identifies a verification-harness serialization mismatch, not a measured timestamp-unit or market-payload mismatch. It does not satisfy the failed GP7 anchor retroactively.

| Month | Original artifact SHA-256 | Sorted expectation SHA-256 | JSON values equal | Matches original writer bytes |
|---|---|---|---|---|
| 2024-12 | 28899d1bb0b22d5f3bff8635213f11f2bb01363c2273c55b11b6e2a17be5130d | 97b561423286205492e2fa6c854e92cb95b197e1cfb6e2e1da899295584e7a71 | True | True |
| 2025-01 | 197437de1f247315840b5c600a1b8dfdf50c03db9bcb0f242d63920fc29822ab | 3b93570ab364b6184e2f6ce628f3be957212b41d51f8bd7c81d9371f0bb38caf | True | True |

Original key order: `month`, `expected_unit`, `detected_unit`, `first_open_raw`, `first_open_parsed_utc`, `last_open_parsed_utc`. Sorted expectation order: `detected_unit`, `expected_unit`, `first_open_parsed_utc`, `first_open_raw`, `last_open_parsed_utc`, `month`.

The failed run is archived as `docs/phase3a-gate1-evidence/d033-revalidation.json`, its stdout as `d033-revalidation.log`, and its unchanged scratch wrapper as `d033-revalidation-wrapper.py`. The metadata-only diagnosis is `d033-revalidation-failure-diagnosis.json`; it records **network_requests=0** and **market_rows_parsed=0**. No helper, wrapper, timestamp evidence, manifest, market artifact or frozen file was repaired during that stopped stage, and no reproduction was then rerun. D-034 later authorized the new verifier and run 2. The archived D-033 wrapper, failed run and diagnosis were preserved rather than overwritten.

## GP7 writer-defined byte reproducibility and authorized run 2 (WW.3)

GP7 means re-parsing the ZIP archives, recomputing artifact SHA-256 values and matching the manifest, with byte-for-byte equality defined by the original writer. The existing JSON writer deliberately preserves insertion order. Its serializer is `(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")`, without `sort_keys`. This writer choice is not a data defect. The old scratch verifier added a sorted-JSON comparison that did not belong to GP7 and caused run 1 to fail.

The narrow D-034 correction in `scripts/revalidate_historical.py` removes that extra comparison and uses the same serializer as the unchanged writer. It checks both reconstructed byte equality and SHA equality against the existing boundary files and manifest. Boundary expected values come from the newly re-parsed monthly records. No writer, ZIP, CSV, Parquet, monthly sidecar, historical manifest, boundary JSON or frozen protocol was changed. A future canonical-JSON migration would require a separate protocol covering both writers and historical artifacts; it is outside Gate 1.

The three synthetic harness tests demonstrate: original-writer bytes match; a different `sort_keys=True` hash does not reject an otherwise correct writer match; and a one-byte mutation still fails against the independent original expected value. These tests passed before run 2 and are committed in X `ea8f78e9c829026a1dbe05beaab8faa51e246c72`. The original D-033 failure evidence remains immutable.

Executed `.venv/Scripts/python.exe -B scripts/revalidate_historical.py` from clean X. `d034-revalidation.json` records `revalidation_run_number=2`, `prior_run=docs/phase3a-gate1-evidence/d033-revalidation.json`, and `status=passed`. The successful evidence and stdout are preserved in commit Y `e9f8f176454ddd73b110df165e0402851af48e8b`.

| Run | Status | Clean implementation commit | Started UTC | Finished UTC | Elapsed seconds | Rows revalidated | Deterministic-file result |
|---|---|---|---|---|---|---|---|
| 1 (D-033) | Failed: harness sorted-JSON assertion | `b4683f3c0051b486ef081f310919ab72e7d6f12b` | Not recorded | Not recorded | 48.31830680009443 | 2978475 | 204 market artifacts verified; overall GP7 failed |
| 2 (D-034) | Passed | `ea8f78e9c829026a1dbe05beaab8faa51e246c72` | `2026-11-09T03:28:42.254672+00:00` | `2026-11-09T03:29:25.218559+00:00` | 42.9639037 | 2978475 | 207 files verified |

Run 1's wrapper did not record UTC start or finish. D-034 preflight preserves the original local report's file mtime, **2026-11-09T02:46:17.847124+00:00**, only as a filesystem last-write proxy. It is not a measured execution endpoint; no start time has been inferred by subtracting the elapsed duration. The proxy provenance is `d034-preflight.json` / `prior_run_time_metadata`, originally sampled from `.tmp/gate1-d033-revalidation.json`. Run 2 records explicit process and per-month UTC timestamps.

Run 2 reproduced **68 months**, **2,978,475 rows**, and **67 boundary pairs**. Its **207 deterministic files** comprise 204 ZIP/CHECKSUM/Parquet files, the manifest and two timestamp-boundary JSON files; 68 embedded CSV digests and sidecar metadata were additionally audited. All three JSON checks matched the original bytes and their original SHA-256 values:

| Artifact | Original SHA-256 = reconstructed SHA-256 | Original writer bytes match |
|---|---|---|
| docs/phase3a-gate1-evidence/timestamp-boundary-2024-12.json | `28899d1bb0b22d5f3bff8635213f11f2bb01363c2273c55b11b6e2a17be5130d` | true |
| docs/phase3a-gate1-evidence/timestamp-boundary-2025-01.json | `197437de1f247315840b5c600a1b8dfdf50c03db9bcb0f242d63920fc29822ab` | true |
| data/processed/binance/spot/BTCUSDT/1m/historical/historical-manifest.json | `71920d90d95a3d6600db3b57540865949b8784ce8753b0dccfbea93d7bb1fa02` | true |

The D-034 preservation audit reports 45 protected byte hashes unchanged and 273 market-artifact size/mtime records unchanged; it reads no market rows and sends no network requests. New evidence is stored under `docs/phase3a-gate1-evidence/d034-*`. Both the original failed and corrected successful runs remain visible; the success does not rewrite the original incident.

## Full 68-month gap and anomaly audit

Every month with `missing_ratio > 0` appears in the first table; every month with `anomalous_close_time_rows > 0` appears separately in the second. Values are transcribed from existing manifest metadata; report preparation opens no market rows. Archive gaps and anomalous close times remain unchanged, without filling, skipping months or moving boundaries.

| Month | Role | Actual | Expected | Missing | Missing % | Tier | Notes |
|---|---|---|---|---|---|---|---|
| 2020-02 | warmup | 41346 | 41760 | 414 | 0.99137931% | normal | 414 missing minutes in 2 exact ranges; no filling; 1 close-time span anomalies retained in source rows; refetch after prior cleanup; gap retained from archive |
| 2020-03 | warmup | 44512 | 44640 | 128 | 0.28673835% | normal | 128 missing minutes in 1 exact ranges; no filling; 1 close-time span anomalies retained in source rows |
| 2020-04 | warmup | 43050 | 43200 | 150 | 0.34722222% | normal | 150 missing minutes in 1 exact ranges; no filling |
| 2020-06 | warmup | 42990 | 43200 | 210 | 0.48611111% | normal | 210 missing minutes in 1 exact ranges; no filling |
| 2020-11 | warmup | 43140 | 43200 | 60 | 0.13888889% | normal | 60 missing minutes in 1 exact ranges; no filling |
| 2020-12 | warmup | 44350 | 44640 | 290 | 0.64964158% | normal | 290 missing minutes in 2 exact ranges; no filling; 1 close-time span anomalies retained in source rows |
| 2021-02 | evaluation | 40241 | 40320 | 79 | 0.19593254% | normal | 79 missing minutes in 1 exact ranges; no filling; 1 close-time span anomalies retained in source rows |
| 2021-03 | evaluation | 44550 | 44640 | 90 | 0.20161290% | normal | 90 missing minutes in 1 exact ranges; no filling |
| 2021-04 | evaluation | 42766 | 43200 | 434 | 1.00462963% | soft_warning | 434 missing minutes in 2 exact ranges; no filling; 1 close-time span anomalies retained in source rows |
| 2021-08 | evaluation | 44370 | 44640 | 270 | 0.60483871% | normal | 270 missing minutes in 1 exact ranges; no filling; 1 close-time span anomalies retained in source rows |
| 2021-09 | evaluation | 43080 | 43200 | 120 | 0.27777778% | normal | 120 missing minutes in 1 exact ranges; no filling |
| 2023-03 | evaluation | 44560 | 44640 | 80 | 0.17921147% | normal | 80 missing minutes in 1 exact ranges; no filling; 1 close-time span anomalies retained in source rows |

| Month | Anomalous close_time rows | Tier | Notes |
|---|---|---|---|
| 2020-02 | 1 | normal | 414 missing minutes in 2 exact ranges; no filling; 1 close-time span anomalies retained in source rows; refetch after prior cleanup; gap retained from archive |
| 2020-03 | 1 | normal | 128 missing minutes in 1 exact ranges; no filling; 1 close-time span anomalies retained in source rows |
| 2020-12 | 1 | normal | 290 missing minutes in 2 exact ranges; no filling; 1 close-time span anomalies retained in source rows |
| 2021-02 | 1 | normal | 79 missing minutes in 1 exact ranges; no filling; 1 close-time span anomalies retained in source rows |
| 2021-04 | 1 | soft_warning | 434 missing minutes in 2 exact ranges; no filling; 1 close-time span anomalies retained in source rows |
| 2021-08 | 1 | normal | 270 missing minutes in 1 exact ranges; no filling; 1 close-time span anomalies retained in source rows |
| 2021-12 | 1 | normal | 1 close-time span anomalies retained in source rows |
| 2023-03 | 1 | normal | 80 missing minutes in 1 exact ranges; no filling; 1 close-time span anomalies retained in source rows |

### Every recorded gap interval in UTC

Endpoints are inclusive missing-minute open timestamps. Separate gaps are listed separately; no interval has been silently combined or omitted.

| Month | Gap start UTC | Gap end UTC | Missing minutes |
|---|---|---|---|
| 2020-02 | 2020-02-09T02:00:00+00:00 | 2020-02-09T02:59:00+00:00 | 60 |
| 2020-02 | 2020-02-19T11:36:00+00:00 | 2020-02-19T17:29:00+00:00 | 354 |
| 2020-03 | 2020-03-04T09:22:00+00:00 | 2020-03-04T11:29:00+00:00 | 128 |
| 2020-04 | 2020-04-25T02:00:00+00:00 | 2020-04-25T04:29:00+00:00 | 150 |
| 2020-06 | 2020-06-28T02:00:00+00:00 | 2020-06-28T05:29:00+00:00 | 210 |
| 2020-11 | 2020-11-30T06:00:00+00:00 | 2020-11-30T06:59:00+00:00 | 60 |
| 2020-12 | 2020-12-21T14:10:00+00:00 | 2020-12-21T17:59:00+00:00 | 230 |
| 2020-12 | 2020-12-25T02:00:00+00:00 | 2020-12-25T02:59:00+00:00 | 60 |
| 2021-02 | 2021-02-11T03:41:00+00:00 | 2021-02-11T04:59:00+00:00 | 79 |
| 2021-03 | 2021-03-06T02:00:00+00:00 | 2021-03-06T03:29:00+00:00 | 90 |
| 2021-04 | 2021-04-20T02:00:00+00:00 | 2021-04-20T04:29:00+00:00 | 150 |
| 2021-04 | 2021-04-25T04:01:00+00:00 | 2021-04-25T08:44:00+00:00 | 284 |
| 2021-08 | 2021-08-13T02:00:00+00:00 | 2021-08-13T06:29:00+00:00 | 270 |
| 2021-09 | 2021-09-29T07:00:00+00:00 | 2021-09-29T08:59:00+00:00 | 120 |
| 2023-03 | 2023-03-24T12:40:00+00:00 | 2023-03-24T13:59:00+00:00 | 80 |

| Coverage | Missing / expected minutes | Missing % | Applicable aggregate hard ceiling |
|---|---|---|---|
| All 68 months | 2325 / 2980800 | 0.07799919% | 1.0% |
| Warm-up 12 months | 1252 / 527040 | 0.23755313% | Per-month 5% |
| Evaluation 56 months | 1073 / 2453760 | 0.04372881% | 0.3% |

All **2,325 missing minutes** and **8 anomalous close_time rows** are disclosed above. No warm-up month exceeds 5%, no evaluation month exceeds 3%, and no month exceeds 100 anomalous close-time rows. The only soft-warning month is **2021-04**: 434 missing minutes / 43,200 = **1.00462963%**, with one anomalous close-time row. It was retained and accepted under D-031. February 2020 is near the upper normal threshold as detailed in its dedicated subsection. All other months are normal. GP10's data thresholds pass; the separate GP7 verifier defect was resolved by D-034 run 2.

## Timestamp distribution and complete month inventory

Measured distribution: **60 ms months** (January 2020-December 2024) and **8 us months** (January-August 2025). December 2024 and January 2025 market artifacts both passed their unit policy and raw/Parquet verification. The timestamp JSON values agree with these records. D-034 run 2 also reproduced both original JSON bytes and hashes after correcting the earlier verifier serialization mismatch.

| Transition month | Detected / expected unit | First raw open | First open UTC | Last open UTC |
|---|---|---|---|---|
| 2024-12 | ms / ms | 1733011200000 | 2024-12-01T00:00:00+00:00 | 2024-12-31T23:59:00+00:00 |
| 2025-01 | us / us | 1735689600000000 | 2025-01-01T00:00:00+00:00 | 2025-01-31T23:59:00+00:00 |

| Month | Role | Unit | Expected minutes | Retained minutes | Missing | Anomalous close rows | Tier |
|---|---|---|---|---|---|---|---|
| 2020-01 | warmup | ms | 44640 | 44640 | 0 | 0 | normal |
| 2020-02 | warmup | ms | 41760 | 41346 | 414 | 1 | normal |
| 2020-03 | warmup | ms | 44640 | 44512 | 128 | 1 | normal |
| 2020-04 | warmup | ms | 43200 | 43050 | 150 | 0 | normal |
| 2020-05 | warmup | ms | 44640 | 44640 | 0 | 0 | normal |
| 2020-06 | warmup | ms | 43200 | 42990 | 210 | 0 | normal |
| 2020-07 | warmup | ms | 44640 | 44640 | 0 | 0 | normal |
| 2020-08 | warmup | ms | 44640 | 44640 | 0 | 0 | normal |
| 2020-09 | warmup | ms | 43200 | 43200 | 0 | 0 | normal |
| 2020-10 | warmup | ms | 44640 | 44640 | 0 | 0 | normal |
| 2020-11 | warmup | ms | 43200 | 43140 | 60 | 0 | normal |
| 2020-12 | warmup | ms | 44640 | 44350 | 290 | 1 | normal |
| 2021-01 | evaluation | ms | 44640 | 44640 | 0 | 0 | normal |
| 2021-02 | evaluation | ms | 40320 | 40241 | 79 | 1 | normal |
| 2021-03 | evaluation | ms | 44640 | 44550 | 90 | 0 | normal |
| 2021-04 | evaluation | ms | 43200 | 42766 | 434 | 1 | soft_warning |
| 2021-05 | evaluation | ms | 44640 | 44640 | 0 | 0 | normal |
| 2021-06 | evaluation | ms | 43200 | 43200 | 0 | 0 | normal |
| 2021-07 | evaluation | ms | 44640 | 44640 | 0 | 0 | normal |
| 2021-08 | evaluation | ms | 44640 | 44370 | 270 | 1 | normal |
| 2021-09 | evaluation | ms | 43200 | 43080 | 120 | 0 | normal |
| 2021-10 | evaluation | ms | 44640 | 44640 | 0 | 0 | normal |
| 2021-11 | evaluation | ms | 43200 | 43200 | 0 | 0 | normal |
| 2021-12 | evaluation | ms | 44640 | 44640 | 0 | 1 | normal |
| 2022-01 | evaluation | ms | 44640 | 44640 | 0 | 0 | normal |
| 2022-02 | evaluation | ms | 40320 | 40320 | 0 | 0 | normal |
| 2022-03 | evaluation | ms | 44640 | 44640 | 0 | 0 | normal |
| 2022-04 | evaluation | ms | 43200 | 43200 | 0 | 0 | normal |
| 2022-05 | evaluation | ms | 44640 | 44640 | 0 | 0 | normal |
| 2022-06 | evaluation | ms | 43200 | 43200 | 0 | 0 | normal |
| 2022-07 | evaluation | ms | 44640 | 44640 | 0 | 0 | normal |
| 2022-08 | evaluation | ms | 44640 | 44640 | 0 | 0 | normal |
| 2022-09 | evaluation | ms | 43200 | 43200 | 0 | 0 | normal |
| 2022-10 | evaluation | ms | 44640 | 44640 | 0 | 0 | normal |
| 2022-11 | evaluation | ms | 43200 | 43200 | 0 | 0 | normal |
| 2022-12 | evaluation | ms | 44640 | 44640 | 0 | 0 | normal |
| 2023-01 | evaluation | ms | 44640 | 44640 | 0 | 0 | normal |
| 2023-02 | evaluation | ms | 40320 | 40320 | 0 | 0 | normal |
| 2023-03 | evaluation | ms | 44640 | 44560 | 80 | 1 | normal |
| 2023-04 | evaluation | ms | 43200 | 43200 | 0 | 0 | normal |
| 2023-05 | evaluation | ms | 44640 | 44640 | 0 | 0 | normal |
| 2023-06 | evaluation | ms | 43200 | 43200 | 0 | 0 | normal |
| 2023-07 | evaluation | ms | 44640 | 44640 | 0 | 0 | normal |
| 2023-08 | evaluation | ms | 44640 | 44640 | 0 | 0 | normal |
| 2023-09 | evaluation | ms | 43200 | 43200 | 0 | 0 | normal |
| 2023-10 | evaluation | ms | 44640 | 44640 | 0 | 0 | normal |
| 2023-11 | evaluation | ms | 43200 | 43200 | 0 | 0 | normal |
| 2023-12 | evaluation | ms | 44640 | 44640 | 0 | 0 | normal |
| 2024-01 | evaluation | ms | 44640 | 44640 | 0 | 0 | normal |
| 2024-02 | evaluation | ms | 41760 | 41760 | 0 | 0 | normal |
| 2024-03 | evaluation | ms | 44640 | 44640 | 0 | 0 | normal |
| 2024-04 | evaluation | ms | 43200 | 43200 | 0 | 0 | normal |
| 2024-05 | evaluation | ms | 44640 | 44640 | 0 | 0 | normal |
| 2024-06 | evaluation | ms | 43200 | 43200 | 0 | 0 | normal |
| 2024-07 | evaluation | ms | 44640 | 44640 | 0 | 0 | normal |
| 2024-08 | evaluation | ms | 44640 | 44640 | 0 | 0 | normal |
| 2024-09 | evaluation | ms | 43200 | 43200 | 0 | 0 | normal |
| 2024-10 | evaluation | ms | 44640 | 44640 | 0 | 0 | normal |
| 2024-11 | evaluation | ms | 43200 | 43200 | 0 | 0 | normal |
| 2024-12 | evaluation | ms | 44640 | 44640 | 0 | 0 | normal |
| 2025-01 | evaluation | us | 44640 | 44640 | 0 | 0 | normal |
| 2025-02 | evaluation | us | 40320 | 40320 | 0 | 0 | normal |
| 2025-03 | evaluation | us | 44640 | 44640 | 0 | 0 | normal |
| 2025-04 | evaluation | us | 43200 | 43200 | 0 | 0 | normal |
| 2025-05 | evaluation | us | 44640 | 44640 | 0 | 0 | normal |
| 2025-06 | evaluation | us | 43200 | 43200 | 0 | 0 | normal |
| 2025-07 | evaluation | us | 44640 | 44640 | 0 | 0 | normal |
| 2025-08 | evaluation | us | 44640 | 44640 | 0 | 0 | normal |

## GP11: all 67 adjacent month boundaries

All 68 months have zero leading and trailing missing minutes. Every comparable adjacent pair below differs by exactly one minute: `span_minutes = previous_trailing_missing_minutes + next_leading_missing_minutes + 1`. The total boundary-gap count is zero, with no exceptional boundary to report. Both GP7 attempts reproduced the complete boundary table; run 2 additionally completed the original-writer JSON byte checks and passed overall.

| Previous month | Next month | Previous last open UTC | Next first open UTC | Missing edge minutes | Span minutes | Status |
|---|---|---|---|---|---|---|
| 2020-01 | 2020-02 | 2020-01-31T23:59:00+00:00 | 2020-02-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2020-02 | 2020-03 | 2020-02-29T23:59:00+00:00 | 2020-03-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2020-03 | 2020-04 | 2020-03-31T23:59:00+00:00 | 2020-04-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2020-04 | 2020-05 | 2020-04-30T23:59:00+00:00 | 2020-05-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2020-05 | 2020-06 | 2020-05-31T23:59:00+00:00 | 2020-06-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2020-06 | 2020-07 | 2020-06-30T23:59:00+00:00 | 2020-07-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2020-07 | 2020-08 | 2020-07-31T23:59:00+00:00 | 2020-08-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2020-08 | 2020-09 | 2020-08-31T23:59:00+00:00 | 2020-09-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2020-09 | 2020-10 | 2020-09-30T23:59:00+00:00 | 2020-10-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2020-10 | 2020-11 | 2020-10-31T23:59:00+00:00 | 2020-11-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2020-11 | 2020-12 | 2020-11-30T23:59:00+00:00 | 2020-12-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2020-12 | 2021-01 | 2020-12-31T23:59:00+00:00 | 2021-01-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2021-01 | 2021-02 | 2021-01-31T23:59:00+00:00 | 2021-02-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2021-02 | 2021-03 | 2021-02-28T23:59:00+00:00 | 2021-03-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2021-03 | 2021-04 | 2021-03-31T23:59:00+00:00 | 2021-04-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2021-04 | 2021-05 | 2021-04-30T23:59:00+00:00 | 2021-05-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2021-05 | 2021-06 | 2021-05-31T23:59:00+00:00 | 2021-06-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2021-06 | 2021-07 | 2021-06-30T23:59:00+00:00 | 2021-07-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2021-07 | 2021-08 | 2021-07-31T23:59:00+00:00 | 2021-08-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2021-08 | 2021-09 | 2021-08-31T23:59:00+00:00 | 2021-09-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2021-09 | 2021-10 | 2021-09-30T23:59:00+00:00 | 2021-10-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2021-10 | 2021-11 | 2021-10-31T23:59:00+00:00 | 2021-11-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2021-11 | 2021-12 | 2021-11-30T23:59:00+00:00 | 2021-12-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2021-12 | 2022-01 | 2021-12-31T23:59:00+00:00 | 2022-01-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2022-01 | 2022-02 | 2022-01-31T23:59:00+00:00 | 2022-02-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2022-02 | 2022-03 | 2022-02-28T23:59:00+00:00 | 2022-03-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2022-03 | 2022-04 | 2022-03-31T23:59:00+00:00 | 2022-04-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2022-04 | 2022-05 | 2022-04-30T23:59:00+00:00 | 2022-05-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2022-05 | 2022-06 | 2022-05-31T23:59:00+00:00 | 2022-06-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2022-06 | 2022-07 | 2022-06-30T23:59:00+00:00 | 2022-07-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2022-07 | 2022-08 | 2022-07-31T23:59:00+00:00 | 2022-08-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2022-08 | 2022-09 | 2022-08-31T23:59:00+00:00 | 2022-09-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2022-09 | 2022-10 | 2022-09-30T23:59:00+00:00 | 2022-10-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2022-10 | 2022-11 | 2022-10-31T23:59:00+00:00 | 2022-11-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2022-11 | 2022-12 | 2022-11-30T23:59:00+00:00 | 2022-12-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2022-12 | 2023-01 | 2022-12-31T23:59:00+00:00 | 2023-01-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2023-01 | 2023-02 | 2023-01-31T23:59:00+00:00 | 2023-02-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2023-02 | 2023-03 | 2023-02-28T23:59:00+00:00 | 2023-03-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2023-03 | 2023-04 | 2023-03-31T23:59:00+00:00 | 2023-04-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2023-04 | 2023-05 | 2023-04-30T23:59:00+00:00 | 2023-05-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2023-05 | 2023-06 | 2023-05-31T23:59:00+00:00 | 2023-06-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2023-06 | 2023-07 | 2023-06-30T23:59:00+00:00 | 2023-07-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2023-07 | 2023-08 | 2023-07-31T23:59:00+00:00 | 2023-08-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2023-08 | 2023-09 | 2023-08-31T23:59:00+00:00 | 2023-09-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2023-09 | 2023-10 | 2023-09-30T23:59:00+00:00 | 2023-10-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2023-10 | 2023-11 | 2023-10-31T23:59:00+00:00 | 2023-11-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2023-11 | 2023-12 | 2023-11-30T23:59:00+00:00 | 2023-12-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2023-12 | 2024-01 | 2023-12-31T23:59:00+00:00 | 2024-01-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2024-01 | 2024-02 | 2024-01-31T23:59:00+00:00 | 2024-02-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2024-02 | 2024-03 | 2024-02-29T23:59:00+00:00 | 2024-03-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2024-03 | 2024-04 | 2024-03-31T23:59:00+00:00 | 2024-04-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2024-04 | 2024-05 | 2024-04-30T23:59:00+00:00 | 2024-05-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2024-05 | 2024-06 | 2024-05-31T23:59:00+00:00 | 2024-06-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2024-06 | 2024-07 | 2024-06-30T23:59:00+00:00 | 2024-07-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2024-07 | 2024-08 | 2024-07-31T23:59:00+00:00 | 2024-08-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2024-08 | 2024-09 | 2024-08-31T23:59:00+00:00 | 2024-09-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2024-09 | 2024-10 | 2024-09-30T23:59:00+00:00 | 2024-10-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2024-10 | 2024-11 | 2024-10-31T23:59:00+00:00 | 2024-11-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2024-11 | 2024-12 | 2024-11-30T23:59:00+00:00 | 2024-12-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2024-12 | 2025-01 | 2024-12-31T23:59:00+00:00 | 2025-01-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2025-01 | 2025-02 | 2025-01-31T23:59:00+00:00 | 2025-02-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2025-02 | 2025-03 | 2025-02-28T23:59:00+00:00 | 2025-03-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2025-03 | 2025-04 | 2025-03-31T23:59:00+00:00 | 2025-04-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2025-04 | 2025-05 | 2025-04-30T23:59:00+00:00 | 2025-05-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2025-05 | 2025-06 | 2025-05-31T23:59:00+00:00 | 2025-06-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2025-06 | 2025-07 | 2025-06-30T23:59:00+00:00 | 2025-07-01T00:00:00+00:00 | 0 | 1 | continuous |
| 2025-07 | 2025-08 | 2025-07-31T23:59:00+00:00 | 2025-08-01T00:00:00+00:00 | 0 | 1 | continuous |

## Synthetic regression, research counters and isolation

Latest full regression: **1,092 passed, 1 skipped in 390.02 seconds** in pytest stdout; the monitoring process measured **392.230815 seconds**. Command: `.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider -p gate1_test_counter`. D-034 adds three synthetic verifier tests to the prior D-033 suite of **1,089 passed, 1 skipped in 402.43 seconds**. D-033 had added 22 tests to 1,067. The default-skipped Phase 2F historical compatibility check remains skipped because `QF_C1_HISTORICAL_RESULTS` is unset. The new tests and full suite passed before the authorized second GP7 run.

The latest full suite observed **1,341 synthetic Ridge.fit entries/normal returns** and **4,289 CW/DM/IC entries**, including input-validation failures. Across six complete instrumented suites totals are **8,046 synthetic Ridge entries** and **25,734 synthetic CW/DM/IC entries**; the five-suite D-033 totals remain 6,705 and 21,445. These are engineering regression counters. The six required atomic-write cases and three new verifier cases are included in the passing suite. Latest logs/counters are `d034-pytest.log` and `d034-synthetic-test-counts.json`; the D-033 files are preserved.

D-033 acquisition, failed GP7 run 1 and successful D-034 GP7 run 2 each report **real_data_ridge_fits=0**, **real_data_statistical_tests_count=0**. Named function monitors record zero Ridge, CW, DM, IC, R2, power/MDE and encompassing entries. Holdout requests/access and smoke-day rows are zero. Both offline reproductions report **market_network_requests=0** and **network_attempts=0**; D-034 consumed no additional GET or transfer budget. No real-data statistical research, achieved-power, profit or generalization claim follows from these integrity records.

The final D-034 metadata isolation snapshot at **2026-11-09T03:30:08.620545+00:00** reports zero changed file sizes/mtimes in all six main/2F directories. It is archived as `docs/phase3a-gate1-evidence/d034-post-isolation.json`; the earlier D-033 post-stop snapshot at 2026-11-09T02:49:38.560642+00:00 remains preserved in `d033-final-isolation.json`. The final reproducibility measurement records **437,508,882 bytes** of historical data footprint and **495,905,128,448 bytes** of free disk. The three primary frozen hashes, auxiliary download draft and seven prediction-source fingerprints remain unchanged.

| Worktree | Directory | Files before | Files after | Bytes before | Bytes after | Changed size/mtime paths |
|---|---|---|---|---|---|---|
| main | .venv | 10052 | 10052 | 397794209 | 397794209 | 0 |
| main | results | 248 | 248 | 596100123 | 596100123 | 0 |
| main | data | 61 | 61 | 60685680 | 60685680 | 0 |
| phase2f | .venv | 10052 | 10052 | 397797395 | 397797395 | 0 |
| phase2f | results | 1 | 1 | 2 | 2 | 0 |
| phase2f | data | 2 | 2 | 4 | 4 | 0 |

| Frozen artifact or prediction source | SHA-256 | Unchanged |
|---|---|---|
| configs/history-extension.toml | e9cbd19d884d046199f468f29e912cd6a9fbec1852e90ffc125b86d8df0bd9af | True |
| docs/confirmatory-protocol-v1-history-addendum.md | a4a99cfbfb9e33105d98ad44b439a57020a050b64b46b5a8e4a0bbc4b204fc07 | True |
| docs/phase3a-analysis-plan.md | fea667f7689d2c45ab0697b0dd2e8877cf0894ae27e4a5f0277efae48e50cb3c | True |
| src/quantforge/datasets/research_splits.py | 22c862f6e8e232dedfe17c3a9832f5b7d78737de1593d6b904e2c29c5949919c | True |
| src/quantforge/datasets/splits.py | 3459e5be5185eee2638709c3db4250e150d8e1deff140d1459882cb5257d93cd | True |
| src/quantforge/datasets/window_splits.py | 24de7804527d3dc805f46ee969d17267927e345b94af434303cd19793675fc22 | True |
| src/quantforge/features/__init__.py | 857819a0c3c718cf2b1100ea72995db989ed149f65d1ad3d5e4cd3eda1fcbec1 | True |
| src/quantforge/labels/__init__.py | b968218639387cb82ab1beccef5c5bf3ed3674e5975c3d2b25cb4d318037746e | True |
| src/quantforge/models/baselines.py | a9269ca7f6147f653f608051746304596e898f49ec30f9e1714567e25a1ded83 | True |
| src/quantforge/temporal.py | 6c262bbd1c77e6696c7d310eb31b321f3f27ffeea02fbd64a70f3dcee9bd648c | True |
| docs/phase3a-download-manifest-draft.json | 1a865966ff55094a5fa54fc245ed7f0e7543360fbd8a0a4da8459dda306ab0f6 | True |

## Final Gate 1 anchors after D-034 run 2

| Anchor | Status | Evidence and limit |
|---|---|---|
| GP1 | Passed | Exact 68-month allowlist and official URLs; 144 GETs within authorized scope; zero Holdout/smoke access |
| GP2 | Passed | All 68 ZIP/CHECKSUM/CSV/Parquet and sidecar records reproduced in successful run 2; original bytes unchanged |
| GP3 | Passed for timestamp data | 60 ms and 8 us months; December 2024/January 2025 unit values and market artifacts verified; boundary JSON values match |
| GP4 | Passed under D-031 | Archive gaps retained and graded; no duplicate open times |
| GP5 | Superseded by GP11 | D-031 replaced the original unconditional one-minute rule with explicit missing-edge accounting |
| GP6 | Passed | 144 GETs; ZIP 152094624 bytes; CHECKSUM 6336 bytes; zero reservations; disk checks reported separately |
| GP7 | Passed in run 2 | 68 months, 67 boundaries and 207 deterministic files verified from clean X with the unchanged writer serializer; original failed run retained |
| GP8 | Passed | 1092 passed / 1 unchanged default skip; three D-034 verifier tests added to the passing D-033 suite |
| GP9 | Passed | Final D-034 six-directory metadata unchanged; 45 protected byte hashes and 273 artifact stat records preserved; frozen hashes and seven prediction fingerprints match |
| GP10 | Passed | All monthly/aggregate thresholds within limits; April 2021 soft warning disclosed and retained |
| GP11 | Passed | All 67 pairs reproduce missing-edge accounting; zero boundary gaps |

Gate 1 integrity work is complete after the authorized verifier correction and successful GP7 run 2. The historical failure fields and evidence remain preserved, with resolution linked to X `ea8f78e9c829026a1dbe05beaab8faa51e246c72`. Manifest B remains unchanged. Under D-035, successful reproduction evidence is Y `e9f8f176454ddd73b110df165e0402851af48e8b`; Z is this documentation commit and is located through Git history. This establishes archive integrity and reproducibility only. Gate 2 is not authorized. Cross-check remains disabled. No dependency sync, new dependency, trading, push, remote creation or PR occurred.

Documentation byte audit: two nested metadata JSON files were normalized by Git from CRLF to LF without any value change. Original byte copies and the corresponding committed-blob hash mapping are preserved in `docs/phase3a-gate1-evidence/d032-original-*.json` and `d032-git-object-sha256.json`. See the execution record; market artifact and frozen protocol hashes are unaffected.
