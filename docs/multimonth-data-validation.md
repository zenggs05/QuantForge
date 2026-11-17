# Phase 2B 多月真实数据验证

实际核验日期：2026-11-02（Asia/Shanghai）。本报告来自官方真实归档；合成单元测试另见测试覆盖文档。

十个月 BTCUSDT Spot 1m 共 **436,320 行**，区间 `[2025-09-01, 2026-07-01)` UTC。10/10 官方 SHA-256、ZIP 单 CSV 成员/CRC、完整自然月校验、Parquet 精确往返通过；9 个跨月边界恰好相隔一分钟，缺失/重复/乱序/月外数据均为 0。

## 每月来源和哈希

| 月份 | 行数 | ZIP bytes | SHA-256（与官方匹配） | 来源 |
|---|---:|---:|---|---|
| 2025-09 | 43,200 | 2,045,189 | `52bb4a4a52afbcc0af77e3eb92c114bf810982ca63bf02f8fe15062e59c5f44b` | [ZIP](https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2025-09.zip) / [CHECKSUM](https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2025-09.zip.CHECKSUM) |
| 2025-10 | 44,640 | 2,216,628 | `985d878cacbfdde85c9e0da2e8830ab913fe01bfdcde6e488563d8194df11f6b` | [ZIP](https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2025-10.zip) / [CHECKSUM](https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2025-10.zip.CHECKSUM) |
| 2025-11 | 43,200 | 2,165,376 | `c54159d1216ab07f040a96d6678748e8746ac4200cba373fb6b758f2cdd1e5e5` | [ZIP](https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2025-11.zip) / [CHECKSUM](https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2025-11.zip.CHECKSUM) |
| 2025-12 | 44,640 | 2,154,846 | `7b47647352b01356dcd678a1438efb8baac03ed798785ed2179d428aab0cca9b` | [ZIP](https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2025-12.zip) / [CHECKSUM](https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2025-12.zip.CHECKSUM) |
| 2026-01 | 44,640 | 2,127,988 | `d28b369da32a6ea84ba0af517b1847ab67b2fcc1e0e111c1b24d56ae07240cb9` | [ZIP](https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-01.zip) / [CHECKSUM](https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-01.zip.CHECKSUM) |
| 2026-02 | 40,320 | 2,021,779 | `cee2e913ed07e02834e2387c65ca430fe3576f620588bad8a02229238f385407` | [ZIP](https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-02.zip) / [CHECKSUM](https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-02.zip.CHECKSUM) |
| 2026-03 | 44,640 | 2,213,444 | `a2c1e689d39aa7877e218f436f15162e9a4436778a377d32e9d9b81b081381cd` | [ZIP](https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-03.zip) / [CHECKSUM](https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-03.zip.CHECKSUM) |
| 2026-04 | 43,200 | 2,088,776 | `318afd4f5acfbcfbb40fbe727a77eee8e9e7b54d8941589c78a32283d90b4a01` | [ZIP](https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-04.zip) / [CHECKSUM](https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-04.zip.CHECKSUM) |
| 2026-05 | 44,640 | 2,120,776 | `62983d93c35e26ec6c29116c629e443b7ddfe3d301aca83331ff58112199d8d0` | [ZIP](https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-05.zip) / [CHECKSUM](https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-05.zip.CHECKSUM) |
| 2026-06 | 43,200 | 2,092,027 | `c8e1d5e70b766dd2312fc9cd7085785bb25708e27e75c21a5a5aaba61a6d07f5` | [ZIP](https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-06.zip) / [CHECKSUM](https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-06.zip.CHECKSUM) |

## 格式与交叉核验

每份 CSV 为无表头 12 列官方 schema；保留原始 open/close 整数时间戳，解析后共 14 列。2025+ Spot 使用微秒（us），`close_raw = open_raw + 60,000,000 - 1`。UTC 首条为月初 00:00，末条 open 为月末 23:59，close 为 23:59:59.999999。OHLC 正值与区间关系、非负 volume/trade count、taker buy 不超总量、数值有限性均已检查。

首先只取得 September 2025，通过官方哈希、解析和精确 Parquet 往返后，另取 September 1 日档。两来源 **1,440 行 × 14 列精确相等**，包括 raw timestamps、UTC、OHLC、量、笔数及 taker buy。随后才启动其他月份。
日档 ZIP SHA-256：`97d4542f81d292a532fd18d24d17ff4e7f198f86cb9f134fdd025807f371af2a`。交叉检查记录保存在 `results/monthly-download/daily-cross-check.json`。

## 预算、持久性和文件

月档 ZIP 实际正文合计 **21,246,829 bytes（20.2626 MiB）**；Parquet 合计 **39,004,829 bytes（37.1979 MiB）**。月档网络账本 ZIP bytes 为 21,246,829、CHECKSUM 为 968，未决预留均为 0；CHECKSUM 包括首月批量前的再次刷新。实际月档请求均成功，没有真实网络重试失败；中断恢复通过合成 HTTP 测试验证。
可选日档另行计量：ZIP 71,954 bytes，CHECKSUM 91 bytes，不混入 28 MiB 月档 ZIP 预算。

账本记录 HTTP 响应正文，不宣称包含无法由 urllib 测得的 TCP/TLS/响应头开销。固定 4 MiB ZIP / 32 MiB CSV / 28 MiB 累计 ZIP 限额未提高。完成文件可以复用，但每次完整校验；中断部分从零重传并继续计费；超时接收量无法确定时保留保守额度。崩溃遗留锁/损坏缓存需要调查，不自动清除。

原始归档和 CHECKSUM 位于 `data/raw/binance/spot/BTCUSDT/1m/monthly/`；验证后 Parquet、单月 manifest 位于 `data/processed/binance/spot/BTCUSDT/1m/monthly/`。整体 manifest 为 `data/processed/binance/spot/BTCUSDT/1m/development-manifest.json`。以上文件及大实验产物全部被 Git 忽略；小型审计元数据公开副本见 [multimonth-data-manifest.json](multimonth-data-manifest.json)。
整体复验处理耗时 2.667375 秒；合并 bars DataFrame 深层内存 48,867,972 bytes。不是进程峰值内存。

## 留出与限制

本轮仅访问白名单十个月月档和可选 September 1 日档；未下载、解析、查看行情或统计/评估 July/August 2026 留出集，也未向其发起 HEAD/CHECKSUM 请求。历史 Phase 2A 曾查询这些留出文件的 HEAD/CHECKSUM 元数据，见 [holdout-policy.md](holdout-policy.md)，不把历史元数据查询说成从未发生。

官方 SHA-256 与两个归档一致证明传输/来源一致性，不证明市场真值或不会被上游修订。数据按 Binance Vision 的独立使用条款处理，原始行情不随代码发布。本轮未发现数据异常；无数据可用性阻碍。
