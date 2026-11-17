# Binance Spot 数据可用性与单日验证

检查日期：2026-11-02（Asia/Shanghai）；机器可读快照见 [data-validation-manifest.json](data-validation-manifest.json)。

## 已验证结论

成功从 Binance 官方公开归档获取 **BTCUSDT、Spot、1m、2026-09-01 UTC** 的真实数据，无需替换日期。仅下载一个交易对的一天 ZIP（70,161 bytes），没有运行任何策略或交易 API。

- 官方 ZIP：[BTCUSDT-1m-2026-09-01.zip](https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2026-09-01.zip)，HTTP 200。
- 官方校验文件：[ZIP.CHECKSUM](https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2026-09-01.zip.CHECKSUM)，HTTP 200。
- ZIP SHA256：`a71f1d7887a3df468117bd3d7701a9831f43379b37e19cae74f12bd822919464`，与官方文件一致。
- ZIP 首次获取时间：`2026-11-02T06:56:15.603826+00:00`；服务端 Last-Modified：`2026-09-02T01:41:27Z`。
- 解压后 CSV SHA256：`d0e045d56cd788a64e7d90c035936330195bcff03b3646acb24d1f06169d5bf3`。
- 当前环境生成的 Parquet SHA256：`5b4b77308d3829cbf8316a7a1bd2acc99f811bedc2f2bc018c771ab74f61e37d`。跨 pyarrow 版本的文件字节可能变化，应以内容一致性与来源哈希为主。

| 检查 | 实际结果 |
|---|---|
| 行数 / 期望行数 | 1,440 / 1,440 |
| 原始时间单位 | 微秒（us） |
| 第一根 open time | 2026-09-01 00:00:00 UTC |
| 最后一根 open time | 2026-09-01 23:59:00 UTC |
| 最后一根 close time | 2026-09-01 23:59:59.999999 UTC |
| 缺失分钟 / 重复 open time / 日外或未对齐时间 | 0 / 0 / 0 |
| 时间严格升序 | 通过（升序且没有重复） |
| OHLC 逻辑错误 / 非有限数值行 | 0 / 0 |
| 负成交量或 taker 成交量超总量 | 0 |
| close time 与一分钟区间不一致 | 0 |
| Parquet 写入后读回 | DataFrame 内容和 dtype 完全一致 |

## 官方格式与时间约定

来源：[binance-public-data README / Spot](https://github.com/binance/binance-public-data#spot)；直接核验的 [README 原文](https://raw.githubusercontent.com/binance/binance-public-data/master/README.md) 明确说明：**Spot 数据从 2025-01-01 开始使用微秒时间戳**。不能把 2026 年样本当作毫秒，也不能据此推断所有 futures 档案具有相同单位。

日归档中的 CSV 无表头，共 12 列，顺序如下：

| 位置 | 字段 | 本项目处理 |
|---|---|---|
| 1 | Open time | int64 原始值另存；转换为 UTC 时间 |
| 2–5 | Open / High / Low / Close | float64；正值且 `low <= open, close <= high` |
| 6 | Volume | base asset 成交量；非负 |
| 7 | Close time | int64 原始值另存；UTC；须为下一分钟前一个时间单位 |
| 8 | Quote asset volume | quote asset 成交量；非负 |
| 9 | Number of trades | 非负 int64 整数 |
| 10 | Taker buy base asset volume | 非负且不大于总 base 量（绝对容差 1e-8） |
| 11 | Taker buy quote asset volume | 非负且不大于总 quote 量（绝对容差 1e-8） |
| 12 | Ignore | 保留为数值，但不作为研究特征 |

解析器检查每一行 open/close 数值量级和日期约定，拒绝混合单位。2025 年前测试使用 ms，2025 年起使用 us。时间列保留 UTC 时区；Parquet 额外保留 `open_time_raw`、`close_time_raw`，避免丢失原始整数精度。本次 pandas 3.0.6 输出的时序 dtype 是 `datetime64[us, UTC]`，价格/成交量为 float64。价格计算若需要撮合或 tick 精度，后续应转整数 tick 或 Decimal；本阶段浮点数用于数据质量检查。

一分钟 K 线由该分钟内全部成交汇总产生，其 high、low、close、volume 不能在 open time 当作已知信息；之后的预测任务必须将信息可用时间至少移至该分钟结束并加入合理处理延迟。

## 复现方式与产物

所有命令均在 `D:\zgs_project\QuantForge` 下运行。安装依赖的步骤见 [README](../README.md)，日参数来自 [data-sample.toml](../configs/data-sample.toml)，也可显式指定：

```powershell
.venv\Scripts\python.exe scripts\download_sample.py --date 2026-09-01
.venv\Scripts\python.exe -m pytest tests -q
```

脚本仅支持单日 BTCUSDT Spot 1m，不会自动遍历日期或补下载其它市场。第一次从官方归档下载，后续使用本地 ZIP，并重新下载官方 CHECKSUM 核验；缓存来源元数据的 URL、字节数及已记录的哈希也必须匹配。官方归档若变更或本地文件损坏，校验失败会终止，须人工调查后处理，不静默覆盖。ZIP 只允许包含预期名称的一个 CSV，不向文件系统解包；压缩和未压缩内容均限制为 8 MiB，缓存读取也遵守相同上限。所有目标路径 resolve 后必须位于项目内。

本地文件（全部由 `.gitignore` 排除，只有文档快照纳入项目）包括：

- `data/raw/binance/spot/BTCUSDT/1m/BTCUSDT-1m-2026-09-01.zip`、`.zip.CHECKSUM` 和 `.zip.source.json`。
- `data/processed/binance/spot/BTCUSDT/1m/BTCUSDT-1m-2026-09-01.parquet`。
- `results/data-validation/BTCUSDT-1m-2026-09-01.json`：来源、HTTP、原始获取时间、复验时间、三种文件哈希、字段类型和检查结果。

首个真实 ZIP 在依赖下载期间通过 Python 标准库 `urllib.request` 获取并检查 SHA256；随后正式 CLI 复用了该已校验文件并完成 CSV 验证和 Parquet 转换，再次以 README 的标准命令重跑通过。初次直接 CLI 因 editable 包尚未安装出现 `ModuleNotFoundError: quantforge`；完成本地 editable 安装后标准命令已成功。没有把这次安装中间状态记作数据网络失败。

## 实际测试结果

复查修正后的 `python -m pytest tests -q`：**23 passed in 1.18s**（首轮 19 项通过，复查增加 4 个缓存/CLI 回归用例）。测试均为离线确定性合成 fixture，与真实数据验证结果分开。覆盖：ms/us 与 UTC 日界、Parquet 精确往返、缺失、重复、OHLC 错误、非有限值、负成交量、错误 close time、混合单位、字段数量、乱序、非整数成交数、日期不符、2025 单位切换、SHA256 损坏/错误文件名、ZIP 路径保护、项目目录逃逸、下载全流程/缓存复验、校验失败不写入未经验证的文件、缓存大小/来源检查，以及 CLI 配置错误和后处理失败报告。pytest 临时目录固定在项目内 `.tmp/pytest`。

CLI 以退出码 2 报告配置错误，以退出码 1 报告下载/验证/转换失败，并将后者记录为 `*-failure.json`。失败只意味着这次端到端流程未完成，不能由 Parquet 写入失败推断真实 ZIP 从未下载；已有来源记录和成功快照会保留。修正后正式 CLI 再次运行成功，精确 roundtrip 及原来的 ZIP/CSV/Parquet 哈希不变。

## 数据使用条款与研究边界

本次实际读取的官方 [TERMS_AND_CONDITIONS.md](https://github.com/binance/binance-public-data/blob/master/TERMS_AND_CONDITIONS.md) 为版本 1.0、更新于 **2026-08-26**；其第 3–4 节规定数据采用 **CC BY-NC-SA 4.0**，允许个人非生产研究与非商业开源评估，商业用途需要另外许可。仓库代码的 MIT 标识不能推导为市场数据同样采用 MIT。当前项目仅做公开数据的个人研究验证，不再分发样本；日后发布衍生成果时应再次核验归属和许可要求。

这个样本只能证明当前端点可达、该日归档存在、checksum 一致以及本解析器的质量门禁通过；不能证明历史上所有日子完整、价格绝对正确、数据不会被后续修订，也不能估计稳定 alpha、净收益、成本、回撤或统计显著性。OHLCV 不含完整 order book、队列位置、实际滑点或订单延迟；LOB 课题需要另行确认可授权取得的数据。本阶段没有构建策略，因此收益/交易成本/回撤/显著性检验不适用，未报告或虚构这些实验指标。
