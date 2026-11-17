"""Local evidence compilation from completed, independently hashed run artifacts."""
from datetime import datetime, timezone
import hashlib
from itertools import zip_longest
import json
from pathlib import Path
import re

import pyarrow.parquet as pq

root = Path(__file__).resolve().parents[1]

def read(relative):
    return json.loads((root / relative).read_text(encoding="utf-8"))

def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()

def write(relative, value):
    (root / relative).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")

runs = sorted((root / "results/research-baseline").glob("*/summary.json"))
assert len(runs) == 2, f"Expected exactly the primary and repeat run, got {len(runs)}"
first, second = [json.loads(path.read_text(encoding="utf-8")) for path in runs]
assert first["status"] == second["status"] == "completed_research_baseline"
for path, summary in zip(runs, (first, second), strict=True):
    assert not (path.parent / "failure.json").exists()
    for name, expected in summary["artifacts_sha256"].items():
        assert digest(path.parent / name) == expected, name
    for name, expected in summary["provenance"]["source_sha256"].items():
        assert digest(root / name) == expected, f"Source changed: {name}"
strip_timing = lambda rows: [{k: v for k, v in row.items() if k not in ("fit_seconds", "predict_seconds")} for row in rows]
assert strip_timing(first["metrics"]) == strip_timing(second["metrics"])
assert first["models"] == second["models"]
assert first["folds"] == second["folds"]
assert first["config_sha256"] == second["config_sha256"]
assert first["source"]["overall_manifest_sha256"] == second["source"]["overall_manifest_sha256"]
tables = [pq.ParquetFile(path.parent / "predictions.parquet") for path in runs]
rows = 0
for a, b in zip_longest(*(table.iter_batches(batch_size=8192) for table in tables)):
    assert a is not None and b is not None and a.equals(b), "Prediction mismatch"
    rows += len(a)
assert rows == first["prediction_rows"] == second["prediction_rows"]
repro = {"status": "passed", "checked_at_utc": datetime.now(timezone.utc).isoformat(),
         "runs": [item["run_id"] for item in (first, second)], "prediction_rows_compared": rows,
         "all_prediction_columns_exactly_equal": True, "metrics_excluding_timings_exactly_equal": True,
         "model_state_exactly_equal": True, "folds_exactly_equal": True,
         "all_run_artifact_hashes_verified": True, "current_source_hashes_match_both_runs": True,
         "prediction_sha256": [item["artifacts_sha256"]["predictions.parquet"] for item in (first, second)],
         "summary_sha256": [digest(path) for path in runs],
         "limitation": "Deterministic repeat on the same data/config/environment, not independent statistical evidence."}
write("results/research-baseline/reproducibility-check.json", repro)
write("docs/phase2b-reproducibility.json", repro)
manifest_relative = "data/processed/binance/spot/BTCUSDT/1m/development-manifest.json"
overall = read(manifest_relative)
assert digest(root / manifest_relative) == first["source"]["overall_manifest_sha256"]
months = [read(item["manifest_path"]) for item in overall["month_manifests"]]
ledger = read("results/monthly-download/network-ledger.json")
daily = read("results/monthly-download/daily-cross-check.json")
assert all(item["status"] == "passed" for item in months)
assert ledger["zip_reserved_bytes"] == ledger["checksum_reserved_bytes"] == 0
assert all(item["status"] == "received" for item in ledger["attempts"])
assert daily["all_columns_exactly_equal"] and daily["rows_compared"] == 1440
assert not any("2026-07" in item["url"] or "2026-08" in item["url"] for item in ledger["attempts"])
zip_bytes = sum(item["archive"]["bytes"] for item in months)
parquet_bytes = sum(item["parquet_bytes"] for item in overall["validation"]["month_sources"])
write("docs/multimonth-data-manifest.json", {
    "overall_manifest_sha256": digest(root / manifest_relative), "overall": overall,
    "monthly_manifests": months, "monthly_network_ledger": ledger, "daily_cross_check": daily,
    "note": "Metadata and aggregate validation only; raw market rows and predictions are excluded from Git."})
lines = ["# Phase 2B 多月真实数据验证", "", "实际核验日期：2026-11-02（Asia/Shanghai）。本报告来自官方真实归档；合成单元测试另见测试覆盖文档。", "",
    f"十个月 BTCUSDT Spot 1m 共 **436,320 行**，区间 `[2025-09-01, 2026-07-01)` UTC。10/10 官方 SHA-256、ZIP 单 CSV 成员/CRC、完整自然月校验、Parquet 精确往返通过；9 个跨月边界恰好相隔一分钟，缺失/重复/乱序/月外数据均为 0。",
    "", "## 每月来源和哈希", "", "| 月份 | 行数 | ZIP bytes | SHA-256（与官方匹配） | 来源 |", "|---|---:|---:|---|---|"]
for item in months:
    lines.append(f"| {item['month']} | {item['validation']['rows']:,} | {item['archive']['bytes']:,} | `{item['archive_sha256']}` | [ZIP]({item['source']['archive_url']}) / [CHECKSUM]({item['source']['checksum_url']}) |")
lines += ["", "## 格式与交叉核验", "",
    "每份 CSV 为无表头 12 列官方 schema；保留原始 open/close 整数时间戳，解析后共 14 列。2025+ Spot 使用微秒（us），`close_raw = open_raw + 60,000,000 - 1`。UTC 首条为月初 00:00，末条 open 为月末 23:59，close 为 23:59:59.999999。OHLC 正值与区间关系、非负 volume/trade count、taker buy 不超总量、数值有限性均已检查。", "",
    "首先只取得 September 2025，通过官方哈希、解析和精确 Parquet 往返后，另取 September 1 日档。两来源 **1,440 行 × 14 列精确相等**，包括 raw timestamps、UTC、OHLC、量、笔数及 taker buy。随后才启动其他月份。",
    f"日档 ZIP SHA-256：`{daily['daily_manifest']['archive_sha256']}`。交叉检查记录保存在 `results/monthly-download/daily-cross-check.json`。", "",
    "## 预算、持久性和文件", "",
    f"月档 ZIP 实际正文合计 **{zip_bytes:,} bytes（{zip_bytes/1024**2:.4f} MiB）**；Parquet 合计 **{parquet_bytes:,} bytes（{parquet_bytes/1024**2:.4f} MiB）**。月档网络账本 ZIP bytes 为 {ledger['zip_body_bytes']:,}、CHECKSUM 为 {ledger['checksum_body_bytes']:,}，未决预留均为 0；CHECKSUM 包括首月批量前的再次刷新。实际月档请求均成功，没有真实网络重试失败；中断恢复通过合成 HTTP 测试验证。",
    f"可选日档另行计量：ZIP {daily['daily_manifest']['archive']['bytes']:,} bytes，CHECKSUM {daily['daily_manifest']['official_checksum']['bytes']:,} bytes，不混入 28 MiB 月档 ZIP 预算。",
    "", "账本记录 HTTP 响应正文，不宣称包含无法由 urllib 测得的 TCP/TLS/响应头开销。固定 4 MiB ZIP / 32 MiB CSV / 28 MiB 累计 ZIP 限额未提高。完成文件可以复用，但每次完整校验；中断部分从零重传并继续计费；超时接收量无法确定时保留保守额度。崩溃遗留锁/损坏缓存需要调查，不自动清除。",
    "", "原始归档和 CHECKSUM 位于 `data/raw/binance/spot/BTCUSDT/1m/monthly/`；验证后 Parquet、单月 manifest 位于 `data/processed/binance/spot/BTCUSDT/1m/monthly/`。整体 manifest 为 `data/processed/binance/spot/BTCUSDT/1m/development-manifest.json`。以上文件及大实验产物全部被 Git 忽略；小型审计元数据公开副本见 [multimonth-data-manifest.json](multimonth-data-manifest.json)。",
    f"整体复验处理耗时 {overall['validation']['elapsed_seconds']:.6f} 秒；合并 bars DataFrame 深层内存 {overall['validation']['memory_bytes']:,} bytes。不是进程峰值内存。", "",
    "## 留出与限制", "", "本轮仅访问白名单十个月月档和可选 September 1 日档；未下载、解析、查看行情或统计/评估 July/August 2026 留出集，也未向其发起 HEAD/CHECKSUM 请求。历史 Phase 2A 曾查询这些留出文件的 HEAD/CHECKSUM 元数据，见 [holdout-policy.md](holdout-policy.md)，不把历史元数据查询说成从未发生。",
    "", "官方 SHA-256 与两个归档一致证明传输/来源一致性，不证明市场真值或不会被上游修订。数据按 Binance Vision 的独立使用条款处理，原始行情不随代码发布。本轮未发现数据异常；无数据可用性阻碍。", ""]
(root / "docs/multimonth-data-validation.md").write_text("\n".join(lines), encoding="utf-8")
test_raw = (root / "results/phase2b-pytest.txt").read_bytes()
test_text = test_raw.decode("utf-16") if test_raw.startswith(b"\xff\xfe") else test_raw.decode("utf-8-sig")
test_result = re.search(r"\d+ passed in [\d.]+s", test_text).group()
print(json.dumps({"reproducibility":repro,"test_result":test_result,"zip_bytes":zip_bytes,"parquet_bytes":parquet_bytes},indent=2))
