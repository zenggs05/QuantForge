# Phase 2B 数据访问与最终留出政策

冻结日期：2026-11-02（Asia/Shanghai）。本阶段唯一正式市场为 Binance Spot BTCUSDT 1m；所有时间区间均为 UTC 左闭右开。

| 角色 | 决策时间区间 | 本轮权限 |
|---|---|---|
| Initial Train | [2025-09-01, 2026-03-01) | 获取、校验、训练 |
| Validation | [2026-03-01, 2026-05-01) | 固定方案下评估；本轮无超参搜索 |
| Development Evaluation | [2026-05-01, 2026-07-01) | 固定方案下评估；不得根据结果反复修改模型 |
| Final Holdout | [2026-07-01, 2026-09-01) | **禁止下载、解析、查看行情、统计或评估** |

正式源文件仅允许 2025-09、10、11、12 和 2026-01、02、03、04、05、06，共十个自然月，理论 436,320 根 bar。源文件按 `bar_open_time` 归属月份；模型训练和预测角色按 `decision_time` 定义，两者不混用。2026-09-01 已见的单日数据只作技术 smoke，不能进入正式多月研究。

本阶段不对 2026-07/08 发起新的 HEAD、CHECKSUM、ZIP 或其他数据请求。Phase 2A 既有扩展计划曾记录这些月档的文件级 HEAD/CHECKSUM 元数据；那不包含市场行或模型指标。既有文档作为历史记录保留，本轮不进一步访问这些文件，亦不把此前的元数据查询描述成“从未发生”。

## 可执行约束

`configs/research-periods.toml` 是冻结约定的可审阅副本；`research_policy.py` 中的固定白名单和区间才是 Phase 2B 的强制上限。修改配置不能扩大权限。月档下载器、整体 manifest、研究加载器和 runner 都应在读取市场文件或发起网络请求前核对月份、市场、交易对、时间角色和规范路径。拒绝 symlink/路径逃逸、未经验证的文件、被篡改的 hash 或非白名单月份。

纯合成单元测试可以构造其他自然月检验闰年/日数逻辑，但不得用真实 2026-07/08 行情作为 fixture；针对留出区间的测试仅验证拒绝行为以及在打开文件/请求网络前失败。

## 月度 forward folds

固定四折：March 用 September–February 历史，April 用 September–March，May 用 September–April，June 用 September–May。月份边界由 `decision_time` 定义；一个前月最后一分钟的 bar 在新月才可用，因而可以属于新月的预测样本，不能因其文件归属前月而提前用于训练。

每折 `fit_time` 为预测月月初 00:00:00 UTC。训练行必须满足 feature/decision 已可用、`label_available_time <= fit_time`、目标结束不越过允许的信息边界，并与首评估目标不重叠。首轮固定 1 秒 availability delay、15m 主任务、5m 辅助任务、Ridge alpha=1.0；采用预注册的即时模型就绪假设，不把测得训练耗时解释为真实执行延迟模型。

每月评估截止为下一月月初 00:00:00 UTC。只有 `decision_time` 在本月且 `label_available_time <= evaluation_cutoff` 的标签参与指标。即使下一月仍为开发数据，也不为当前月补齐越过截止的标签。June 末尾标签自然排除，绝不读取 July 价格来补完。所有排除计数进入 fold audit。

March/April 为 Validation，May/June 为 Development Evaluation，报告分别展示。预先固定模型和配置后运行；负结果保留。重复执行只能核验确定性、修复明确实现错误或确认提交对应代码，不能依据 May/June 指标优化模型。

## 下一阶段与证据

最终留出必须留到用户另行明确授权的阶段。任何访问权限变化都应另立协议、配置和审计记录；本轮无开关可解锁。研究只有预测指标，不输出策略净收益、Sharpe 或回撤。实际下载与测试证据见 Phase 2B 执行记录和多月数据验证报告；若首月校验失败，后续批量下载停止。

## Holdout-B candidate and Phase 2E protection

Added 2026-11-04 under D-008/D-011. Holdout-B is the sealed candidate interval `[2026-09-02T00:00:00Z, 2026-11-01T00:00:00Z)`. Without explicit owner authorization and a separately frozen applicable protocol, do not download, read, parse, analyze, derive statistics from, or use these rows for tuning or model selection. Holdout-A `[2026-07-01, 2026-09-01)` remains sealed. September1 is technical-smoke-only; neither its prices nor metrics may enter research or confirmatory evaluation. Phase2E only freezes the Holdout-A protocol and adds daily-path guards; it does not unseal either interval.

D-011 records the Research Lead's exposure audit: no local market files or research analysis for September2 onward; no September/October requests in the network ledger; caches contain only the two existing daily archives. Synthetic fixtures with September timestamps and GitHub issue timestamps are not market exposure. Four earlier external review archives contained no market or prediction artifacts. These statements are the recorded audit, not new Phase2E access to the sealed intervals.

Known exposure: the final September1 smoke bar fixes Holdout-B's initial price level, so drift-dependent statistics cannot support a confirmatory claim. Its server Last-Modified `2026-09-02T01:41:27Z` is metadata only. The owner's and agents' general market knowledge cannot be audited; D-011 records the Research Lead model's June2026 knowledge cutoff. The smoke day also fixes Holdout-A's ending price level, and earlier Phase2A recorded July/August ZIP Content-Length/CHECKSUM metadata. These caveats must accompany any later approved protocol.

## Phase 3A protocol and infrastructure only

The separate history whitelist covers2020-01 through2025-08. It grants no download authority and leaves the Phase2B whitelist unchanged. No HEAD/CHECKSUM/GET or real historical market-file access is authorized. Holdout-A [2026-07-01,2026-09-01), Holdout-B [2026-09-02,2026-11-01) and the2026-09-01 smoke day remain sealed. Daily guards stay unchanged. The historical public splitter is strict; its parameterized private kernel only uses artificial development-range metadata for equivalence tests. See phase3a-analysis-plan.md and confirmatory-protocol-v1-history-addendum.md.
