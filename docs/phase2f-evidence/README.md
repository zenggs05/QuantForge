# Phase 2F synthetic engineering evidence

All files describe fixed synthetic tests or local provenance. No market observations or historical checkpoints are included. Formal and reproduction use the clean implementation commit named in their summaries. The complete 16-artifact hashes (including ignored synthetic checkpoint/log files) are in ../phase2f-reproducibility.json.

- numerical-stress.json: all 18 frozen cases, three pairwise errors, condition/residual and operation counts.
- kahan.json: million-increment scalar comparison and plain-sum negative control.
- replay-equivalence.json: two horizons, 90 independent maturity/SVD checks each, restart equality and actual budgets.
- computation-budget.json: updates, solves, oracle work, recovery and validation separately.
- checkpoint-anchors.json: small audit/summary anchors for the generated synthetic v2 snapshots.
- checkpoint-capacity.json: measured synthetic >32 MiB regression.
- mutation-matrix.json: all ten semantic kills and three controls, with source/output hashes.
- environment-isolation.json, fingerprint-check.json, test-summary.json, implementation-diff.json: isolation and implementation checks.
- formal-summary.json, reproduction-summary.json: exact measured process/timing/provenance records.

Only elapsed time, memory and run identity differ. These files provide no trading-performance or market generalization evidence. Optional historical v1 integration remained skipped.
