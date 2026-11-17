# D-039 定向日度归档核验记录

本记录只记录 D-039 的归档下载、来源校验和逐分钟字节字段比对事实。没有进行 Ridge 拟合、预测、收益、特征、标签、CW、DM、IC 或其他研究统计；没有访问 Holdout-A、Holdout-B 或 2026-09-01 smoke 日。

## 执行

- 工作树：`D:\zgs_project\QuantForge-3a`，分支 `phase3a-protocol-and-impl`。
- C1 提交：`14721cd7a3d0b59d45d92484dd659b76b10bdc68`。
- 命令：`.venv\Scripts\python.exe -B scripts\verify_daily_archives.py acquire`。
- 复跑命令：`.venv\Scripts\python.exe -B scripts\verify_daily_archives.py replay`。
- Python：3.13.5；C1 合成测试和回归测试：`1137 passed, 1 skipped`。跳过项是未设置 `QF_C1_HISTORICAL_RESULTS` 的 Phase 2F 可选向后兼容测试。
- D-039 合成测试单独结果：45 passed。
- acquire 开始前 `VERIFY_DIR` 不存在，证据目录不存在；历史 manifest SHA-256 为 `71920d90d95a3d6600db3b57540865949b8784ce8753b0dccfbea93d7bb1fa02`，七个确认性预测代码指纹均匹配。
- acquire 用时 41.11383020004723 秒；replay 用时 2.9634845999535173 秒。replay 的网络请求数为 0，比较产物逐字节一致。

## 请求与文件

请求严格限于 11 个日期的官方 `.zip.CHECKSUM` 和 `.zip` URL，串行 GET，未发送 HEAD。成功保存 22 个文件；`2021-01-21` CHECKSUM 的第一次连接尝试出现 SSL `UNEXPECTED_EOF_WHILE_READING`，按连接错误规则重试一次并成功。该失败尝试的 0 字节 SHA 记录在外部 `VERIFY_DIR\failed\` 和 network ledger 中。

| 指标 | 实际 | 上限 |
|---|---:|---:|
| GET | 23 | 66 |
| ZIP 字节（含重试） | 800,482 | 3,145,728 |
| CHECKSUM 字节（含重试） | 1,001 | 65,536 |
| 最大单 ZIP | 80,999 | 1,048,576 |
| 最大单 CHECKSUM | 91 | 4,096 |

每个日期的成功 ZIP SHA-256 和解析行数如下；完整请求时间、HTTP 状态、Last-Modified、字节数和 SHA 在 `network-ledger.json` 与 `download-summary.json` 中保存。

| 日期 | 状态 | 行数 | ZIP SHA-256 |
|---|---|---:|---|
| 2020-11-30 | passed | 1,380 | `8160f7562f88fac4296b46abdf25a33d18c7b3b575b64423c8e91f137fd7f841` |
| 2020-12-21 | passed | 1,188 | `59543b0862faf5953ce8c4e4efe3892d681711de345ced3ec5cf4dd9185a7154` |
| 2021-01-11 | passed | 1,440 | `a2838ad0564be577320ee8d57fa280dae4a62978ff4793f02380f21fe64ca1a1` |
| 2021-01-21 | passed | 1,440 | `5f7f937fdb0be4b9b0cca613dffdf6bef4f03f37db3f4e28a5093f2f29ed84a1` |
| 2021-02-11 | passed | 1,361 | `f2c2fb98b3ac6a4839163f9e10d83a9d7a987ecd3a7d31ca1784ff52ead5aeae` |
| 2021-04-22 | passed | 1,440 | `9044adf686eb593c455bca29b1ffd9f800d5f2bc260a524e286a71dac16be74e` |
| 2021-04-23 | passed | 1,440 | `3950336b09961d1b30c1fbdc6315c7ae3e5a4bd2651d47f43046ef604fb47c2a` |
| 2021-09-23 | passed | 1,440 | `456cabf12b7e1fee5484ac8ad35eeba06c77a740ecbd2d34b748b2da202bcb79` |
| 2021-09-29 | passed | 1,320 | `35dc537929474ad75279dcb614ec0095f5c2d0ee36e64a6a05cfc744ed2018f8` |
| 2022-04-13 | passed | 1,440 | `690a8de5fe743386ac115fa0719bf01f9c3952ac5cf2e70e88e2b9d46744f17b` |
| 2023-03-24 | passed | 1,360 | `305d0f6cdb8a6cfd8063a7d48c66f14b33727d934857746d549663e94b41312d` |

原始日度 ZIP、CHECKSUM 和一个失败尝试文件保留在 `D:\zgs_project\QuantForge-verification\binance-spot-daily-1m-20261110\`，未写入任何 worktree，也未提交 Git。

## 逐分钟比对

每个日期均检查 1,440 个 UTC 分钟。五类合计为：`both_equal=15,137`、`value_conflict=112`、`missing_in_monthly=0`、`extra_in_monthly=22`、`both_missing=569`。字段级冲突合计为：open 36、high 63、low 66、close 110、volume 112、quote_asset_volume 112、number_of_trades 112、taker_buy_base_asset_volume 112、taker_buy_quote_asset_volume 112、close_time 1、ignore 0。

| 日期 | both_equal | value_conflict | missing_in_monthly | extra_in_monthly | both_missing |
|---|---:|---:|---:|---:|---:|
| 2020-11-30 | 1380 | 0 | 0 | 0 | 60 |
| 2020-12-21 | 1159 | 29 | 0 | 22 | 230 |
| 2021-01-11 | 1430 | 10 | 0 | 0 | 0 |
| 2021-01-21 | 1440 | 0 | 0 | 0 | 0 |
| 2021-02-11 | 1361 | 0 | 0 | 0 | 79 |
| 2021-04-22 | 1424 | 16 | 0 | 0 | 0 |
| 2021-04-23 | 1386 | 54 | 0 | 0 | 0 |
| 2021-09-23 | 1439 | 1 | 0 | 0 | 0 |
| 2021-09-29 | 1320 | 0 | 0 | 0 | 120 |
| 2022-04-13 | 1438 | 2 | 0 | 0 | 0 |
| 2023-03-24 | 1360 | 0 | 0 | 0 | 80 |

每个日期各字段冲突计数均单独保留在 [comparison-summary.json](phase3a-daily-verification-evidence/comparison-summary.json)。全部外部文件的名称、大小和 SHA（22 个成功归档文件及 1 个零字节失败尝试）见 [verify-dir-inventory.json](phase3a-daily-verification-evidence/verify-dir-inventory.json)。

预登记窗口内的非 `both_equal` 分钟共 701 个；窗口外共 2 个，分别为 `2021-01-11T08:01:00Z` 和 `2021-01-11T08:10:00Z`，已完整记录在 [minute-diff.csv](phase3a-daily-verification-evidence/minute-diff.csv)，没有扩展核验窗口。连续区间的完整合并结果在 [intervals.csv](phase3a-daily-verification-evidence/intervals.csv)；每个日期的 1,440 分钟两侧占位/close_time 异常标志在 [minute-flags.csv](phase3a-daily-verification-evidence/minute-flags.csv)。

全部窗外字段差异如下（均为 2021-01-11 UTC，差值为日度减月度；价格 bp 和量比完整精度见 CSV）：

| UTC 时分 | 字段 | 月度原值 | 日度原值 | 差值 |
|---|---|---|---|---|
| 08:01 | close | 34350.37000000 | 34364.26000000 | 13.89000000 |
| 08:01 | volume | 114.40445100 | 118.89052000 | 4.48606900 |
| 08:01 | quote_asset_volume | 3927991.40193023 | 4082172.52651130 | 154181.12458107 |
| 08:01 | number_of_trades | 2494 | 2764 | 270 |
| 08:01 | taker_buy_base_asset_volume | 61.57995000 | 64.46374200 | 2.88379200 |
| 08:01 | taker_buy_quote_asset_volume | 2114713.60431451 | 2213829.89549157 | 99116.29117706 |
| 08:10 | open | 35322.79000000 | 35310.82000000 | -11.97000000 |
| 08:10 | low | 35320.31000000 | 35307.57000000 | -12.74000000 |
| 08:10 | volume | 171.65690700 | 209.32702200 | 37.67011500 |
| 08:10 | quote_asset_volume | 6087223.17420075 | 7419777.42376473 | 1332554.24956398 |
| 08:10 | number_of_trades | 4181 | 4926 | 745 |
| 08:10 | taker_buy_base_asset_volume | 110.26188900 | 125.28729700 | 15.02540800 |
| 08:10 | taker_buy_quote_asset_volume | 3911241.21815160 | 4442933.43672389 | 531692.21857229 |

占位与缺失交叉表在 [placeholder-crosstab.json](phase3a-daily-verification-evidence/placeholder-crosstab.json)。全部非零项：2020-11-30 月度缺失/日度缺失 60；2020-12-21 月度占位对应日度缺失 22、对应日度有成交 28，月度缺失/日度缺失 230；2021-01-11 月度占位/日度有成交 8；2021-02-11 月度占位/日度占位 81、月度缺失/日度缺失 79；2021-09-29 月度缺失/日度缺失 120；2023-03-24 月度占位/日度占位 72、月度缺失/日度缺失 80。反向日度占位表仅有 2021-02-11 的 81 和 2023-03-24 的 72，均对应月度占位，未出现日度占位对应月度缺失或有成交。

## Issue #475 三项核对

`issue-475-check.json` 记录了原始字符串核对结果：2020-12-21 日度 ZIP SHA 与 `59543b0862faf5953ce8c4e4efe3892d681711de345ced3ec5cf4dd9185a7154` 相同；给定 `open_time=1608556740000` 的日度行逐字符相同；月度行逐字符相同。三项均为 `true`。

## 隔离与复现

`isolation-before.json` 与 `isolation-after.json` 对 68 个月 ZIP、CHECKSUM、source.json、Parquet、historical-manifest（273 个文件）做了 SHA/大小/mtime 快照；main/2F 的 `.venv`、`results`、`data` 仅检查文件名、大小与 mtime，没有读取行情内容。两次快照字节相同。`determinism-check.json` 记录了 8 个重新生成的比较产物 SHA 与字节相等结果，全部为 `true`，并验证下载账本、下载摘要及两份隔离快照未改写。`runtime.json` 分开记录 acquire 和 replay 时间；replay 在 socket/urllib 阻断下未发出网络请求。pytest.log 在复跑后另行采集，属于测试运行日志，不属于这 8 个比较产物的复现声明。

## 开发期失败与偏离

- 开工基线为 `1092 passed, 1 skipped in 401.86s`。新测试首轮为 3 failed、7 passed、1 skipped：合成时间戳常量和模块/测试接口未对齐；后续还修正过返回结构衔接。均在 C1 前修复，最终新增 45 项全部通过，未改变旧测试。
- 开发时多个测试进程曾共用 `pyproject.toml` 指定的 `.tmp/pytest`，导致全量测试中旧合成测试的临时配置文件消失：`1 failed, 1136 passed, 1 skipped in 452.39s`，异常为 `FileNotFoundError`。停止并行测试后，在最终 C1 代码上串行验证为 `1137 passed, 1 skipped in 400.95s`。没有修改旧测试或 Phase 2F 代码。
- 为保留完整 pytest.log，C1 后又串行运行同一全量命令，结果 `1137 passed, 1 skipped in 429.30s`；日志由 PowerShell Tee-Object 保存为 UTF-16LE。此重复仅为合成回归日志采集，未运行真实研究统计。
- 开发期一次模块编译及少数标准库辅助命令误用了裸 `python`，违反本任务“只用本 worktree .venv”的约束；一次编译生成了 `src/quantforge/__pycache__/archive_verification.cpython-313.pyc`，超出封闭新建清单，已清理。未安装、同步或更改任何 Python 环境；正式 acquire/replay 和 pytest 都使用本 worktree `.venv\Scripts\python.exe -B`。
- 一次合成下载测试因证据路径尚未 monkeypatch，将假响应账本写入本阶段新证据目录。该合成账本在 C1 前清理，测试随后把证据目录自动隔离到 pytest 临时目录。正式 acquire 前两个目标目录均不存在；正式 network-ledger 仅含真实的 23 次允许 URL 尝试。
- 清理本轮新缓存的 PowerShell 命令两次被自动工具策略拒绝，返回理由仅为 `blocked by policy`；之后错误地改用了 Python 删除同一缓存。此处理也列为执行偏离，不影响受保护数据的最终 SHA/mtime 核验。
- 无真实日期的最终下载失败，无 CHECKSUM/ZIP/行准入失败；上述 SSL EOF 为允许重试的连接错误。原始归档差异均如实保留；未填补、替换或重写月度数据。

提交 C2 时一并提交本记录和 `docs/phase3a-daily-verification-evidence/` 下的 JSON/CSV/日志证据。D-039 执行到此停止，等待 Research Lead 独立审计；不合并 main，不 push，不创建 remote 或 PR。
