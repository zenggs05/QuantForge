# D-041 Full Daily Archive Verification Record

执行日期：2026-11-10（UTC）；记录整理：2026-11-11（Asia/Shanghai）。本记录只陈述归档来源核验事实，不包含 Ridge、预测、收益、特征、标签、CW、DM、IC 或交易研究。

## 执行边界与代码

- 分支：`phase3a-protocol-and-impl`；C1：`790606b`（`feat: D-041 full daily archive verification tool (synthetic tests only)`）。C1 之后未修改代码。
- 范围为 2020-01-01 至 2025-08-31 的 2,059 个 D-041 日期，排除 D-039 的 11 个日期；没有访问 2025-09-01 及以后行情、Holdout-A/B、smoke 日或交易所 REST API。
- 下载目录为仓库外 `D:\zgs_project\QuantForge-verification\binance-spot-daily-1m-full-d041\`。Gate 1 月度数据、D-039 文件和证据、冻结文件均只读。
- 联网前置检查通过：7 个确认性预测代码指纹、68 月 ZIP/Parquet 与 historical manifest、manifest SHA-256 `71920d90d95a3d6600db3b57540865949b8784ce8753b0dccfbea93d7bb1fa02`、D-039 23 文件清单均一致。

## 六批下载与预算

| 批次 | 日期数 | verified | unverifiable_network | GET | 重试 | ZIP 字节 | CHECKSUM 字节 | elapsed s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| B2020 | 364 | 351 | 13 | 760 | 43 | 25,735,329 | 32,123 | 1829.588 |
| B2021 | 358 | 358 | 0 | 731 | 15 | 27,182,528 | 32,578 | 1732.648 |
| B2022 | 364 | 364 | 0 | 743 | 15 | 26,826,792 | 33,124 | 1705.228 |
| B2023 | 364 | 364 | 0 | 739 | 11 | 25,374,670 | 33,124 | 1674.205 |
| B2024 | 366 | 365 | 1 | 752 | 20 | 25,850,979 | 33,306 | 1823.044 |
| B2025 | 243 | 243 | 0 | 492 | 6 | 17,226,740 | 22,113 | 1158.411 |
| **合计** | **2,059** | **2,045** | **14** | **4,217** | **110** | **148,197,038** | **186,368** | **9923.124** |

GET 使用 4,217/4,600；不同 URL 4,107/4,118；ZIP 148,197,038/201,326,592 B；CHECKSUM 186,368/1,048,576 B。失败和重试收到的 ZIP 字节 461,626 B 已计入预算。最大下载峰值 486,649,856 B。

14 个不可核验日期为 2020-02-13、2020-02-14 至 2020-02-24、2020-12-09、2024-03-17，均为 `unverifiable_network`，每个 URL 用尽三次重试。错误包括 SSL EOF、握手超时、RemoteDisconnected、IncompleteRead 和读取超时；没有 404、429、5xx、重定向、证书拒绝、权限拒绝或 checksum 失败。账本 4,217 个 intent 均有唯一 settle，无未结算 intent、终态 URL 重复请求或越界 URL。

## 离线逐分钟核验

`compare` 在进程内阻断 socket 和 urllib 后运行，覆盖 2,059 个 D-041 日期与 11 个 D-039 日期，共 2,070 天、2,980,800 个分钟格。D-041 中 2,045 个日期通过，日度已存在分钟数 2,978,475；14 个日期不可核验。D-041：both_equal 2,943,375，value_conflict 23，missing_in_monthly 0，extra_in_monthly 0，both_missing 1,402；严重度 major 12、moderate 7、minor 4。

D-039 对账通过且历史证据未变：value_conflict 112，extra_in_monthly 22，missing_in_monthly 0，both_missing 569，`minute_field_sets_equal=true`。合计为 value_conflict 135、extra_in_monthly 22、both_missing 1,971；D-039 的 11 天为有目的挑选，不能用于推断整体缺陷率。

时间异常共 16 个事件、15 个异常行、9 个异常分钟：15 个 `native_close_span_anomaly`，1 个 `close_before_open`；跨来源单位不可比分钟为 0。

## 污染窗口与描述性暴露

污染计算只使用时间戳、分钟分类和结构存在性，不计算特征、标签、收益或研究统计。污染表 552 行，全部样本 bar 2,978,475；all-population 的 U5/U15/F5/F15 feature∪label 计数为 1,823/2,083/1,732/1,772；结构有效 population 为 1,818/2,068/1,727/1,757。GW 工具变量暴露表覆盖 56 个评估月，冲突分钟合计 146，未核验分钟合计 2,880。以上均为描述性来源质量结果。

## 确定性、隔离与测试

- `replay-check` 离线完成，`determinism-check.json` 的 `passed=true`；17 个重算产物逐字节匹配，网络请求 0，隔离未变。重放耗时 317.089 s，峰值 761,458,688 B。
- `compare` 耗时 337.662 s，峰值 756,310,016 B，网络请求和 research_computations 均为 0；隔离前后清单一致。
- derived `minute-flags.csv.gz` 位于外部 D-041 目录，SHA-256 为 `71283ab3b619bb370f22dae62db5111ff5f5accb3d5e226b6b7e068372ebf984`。
- 最终串行测试命令为 `.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider`，结果 **1292 passed, 1 skipped**，完整输出在 `pytest.log`。第一次直接写入证据目录触发隔离断言并得到 1 error；随后改写到临时目录，测试通过后复制日志，未修改代码或受保护数据。
- 本阶段没有工具、权限或安全策略拒绝事件；没有 push、remote、PR、Gate 2 或研究计算。

## 证据文件

`network-ledger.jsonl`、`download-summary.json`、六个 `runtime-acquire-*`、`runtime-compare.json`、`runtime-replay.json`、逐日/逐分钟 CSV、汇总 JSON、污染 CSV、`coverage.json`、`d039-reconciliation.json`、`artifact-index.json`、隔离快照、`determinism-check.json` 和 `pytest.log` 均在 `docs/phase3a-full-verification-evidence/`。原始日度 ZIP/CHECKSUM 和 derived 大文件不进入 Git；其库存与 SHA 在清单中记录。