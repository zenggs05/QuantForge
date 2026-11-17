# Holdout-A 确认性检验报告：D-059

主检验拒绝 H0，单侧 p=0.029484812405491726。这是“Confirmatory Protocol v1 下 Holdout-A 的确认性结果；只涉及预测信息，不涉及盈利、交易或执行”。主族仅含 spec v1、15m、Ridge 对 Zero 的 Clark-West 检验，按冻结阈值 p<0.05 判定。全部次要结果为描述性，不扩大本结论。

## 主检验

每月连续段内采用冻结 monthly_stat 的 Bartlett HAC，跨段协方差为零；7、8 月按 n 加权合并，跨月协方差假定为零，采用单侧正态参考。月份结果不构成额外的确认性主族。

| 月份 | CW 均值 | HAC SE | t | 单侧 p | lag | 样本数 | 成熟截止排除 | 段数 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-07 | 2.778987975716957e-09 | 3.318819693196522e-09 | 0.8373422579761705 | 0.2012001062267637 | 275 | 44625 | 15 | 1 |
| 2026-08 | 8.09968974983373e-09 | 4.708657491450974e-09 | 1.7201696586637494 | 0.04270080327761487 | 275 | 44625 | 15 | 1 |
| n 加权合并 | 5.439338862775343e-09 | 2.880365407710732e-09 | 1.8884197290434905 | 0.029484812405491726 | 月内各 275 | 89250 | 两月共 30 | 两月共 2 |

两个月权重均为 0.5。合并没有另设 HAC lag。1.6 短段规则未触发，被剔除段列表为空，v1 与 v2 使用相同 h=15 评价索引；每月段长 44,625 > 275。详见 [primary.json](holdout-a-evidence/primary.json) 和 [fold-audit.json](holdout-a-evidence/fold-audit.json)。

## 次要族与误差

S1–S5 全部可估计，无未定义 p；原始输出中的 reason=null 表示无不可估计原因。合成测试另覆盖未定义 p 保持 null、Holm 槽按 1.0 处理。Holm 家族大小固定为 5。

| 项目 | 比较 | 原始 p | Holm p | Holm p<0.05 |
|---|---|---:|---:|---|
| S1 | v2 / 15m / Zero / CW | 0.039411274635924871 | 0.078822549271849743 | 否 |
| S2 | v1 / 5m / Zero / CW | 0.0033492558003587374 | 0.01339702320143495 | 是 |
| S3 | v1 / 15m / HistoricalMean / CW | 0.0060712357398995777 | 0.018213707219698734 | 是 |
| S4 | v1 / 15m / Zero / DM，双侧 | 0.95517737783304157 | 0.95517737783304157 | 否 |
| S5 | v1 / 15m / IC-HAC | 0.00045565705011265458 | 0.0022782852505632729 | 是 |

S4 的 DM 定义为模型平方误差减基准平方误差。合并 ΔMSE=+1.7657696808444912e-10，DM t=0.056206406187362487，正号表示模型 MSE 较高。CW 拒绝不等于 MSE 更低。

| 范围 | 模型 MSE | Zero MSE | 模型 MAE | Zero MAE |
|---|---:|---:|---:|---:|
| 2026-07，v1/15m | 3.1911344495478725e-06 | 3.189149102950708e-06 | 0.0012197196628766125 | 0.001219437083912847 |
| 2026-08，v1/15m | 3.5470543111307013e-06 | 3.5486865037916974e-06 | 0.0011513490841926063 | 0.0011515689909215724 |
| 合并，v1/15m | 3.3690943803392867e-06 | 3.368917803371203e-06 | 0.0011855343735346093 | 0.0011855030374172096 |

7 月 MSE 较高，8 月较低，合并略高；合并 R2_OOS=-5.241355782170665e-05。S1、S2、S3 的全部 MSE、MAE 与 S5 IC 均保存在 [secondary.csv](holdout-a-evidence/secondary.csv)，完整 36 行月度比较保存在 [monthly.csv](holdout-a-evidence/monthly.csv)，不只保留有利比较。

## 漂移分解：exploratory_post_hoc

以下描述性分解不属于确认性结论。合并乘积使用合并后的 mean(f) 与 mean(y)，不是月份乘积的平均。

| 范围 | mean(c_t) | 2·mean(f)·mean(y) | 两者之差 |
|---|---:|---:|---:|
| 2026-07 | 2.778987975716957e-09 | -1.6472549069506752e-09 | 4.426242882667632e-09 |
| 2026-08 | 8.09968974983373e-09 | -5.16426176300159e-09 | 1.326395151283532e-08 |
| 合并 | 5.439338862775343e-09 | -3.4234028922765973e-09 | 8.86274175505194e-09 |

完整数值见 [descriptive-drift.json](holdout-a-evidence/descriptive-drift.json)。没有新增检验、筛选或重新拟合。

## 数据、审计与复现

只使用十个既有开发月 parquet 与本次两个月度 ZIP。12 月拼接为 525,600 行，其中开发 436,320 行，7、8 月各 44,640 行。全部月份的缺口、重复和 close_time 异常均为 0，单位均为微秒；六月至七月边界连续。7、8 月未定义特征均为 0/44,640，四折的 D* 均为 0。h=15 训练集 436,244 / 480,884，h=5 为 436,254 / 480,894；PA-1…PA-6 全部通过。

4 次 GET，无重试，ZIP 4,197,221 B，CHECKSUM 176 B；原文、长度和 SHA 与合约记录一致。没有进行日度来源核验。正式与复现各 6 次拟合，共 12 次；每次 267,770 行预测，全部九个确定性产物逐字节相同。预测文件 14,208,491 B，SHA-256 为 `5c1efd6b0e956bb13f2a1fbdeef9d3961bddb5439a7621735cd18ca60ff8fee3`，留在 Git 忽略的结果目录。

| 运行 | 秒 | 峰值进程内存 B | 结束时磁盘可用 B |
|---|---:|---:|---:|
| formal | 14.01504460000433 | 860585984 | 446453096448 |
| reproduction | 15.935187600087374 | 862646272 | 446438785024 |

资源均在预算内。复现使用相同 H2、冻结代码与输入，不参与选择结果。H1 前回归为 1563 passed, 1 skipped，保留原 1481 项并新增 82 项合成测试。预检查、日志 SHA、四进程时间线、开发期错误及解释见 [执行记录](holdout-a-record.md)。

## 五项既有暴露：合约原文

1. 2026-09-01 smoke day fixes the price level at the end of Holdout-A (protocol text)
2. Phase 2A metadata-only requests for 2026-07/08 file lengths and checksums (protocol text)
3. March-June 2026 results viewed repeatedly (protocol text)
4. Gate 2 historical results (D-058) were known before this run; the protocol, code and this contract were fixed before any Holdout-A byte was read
5. no daily-archive source verification for 2026-07/08 (owner decision D-059); monthly archives only

上述是既有暴露披露，本次未读取 smoke 日或 Holdout-B。两月结果条件于冻结特征、规格、成熟规则以及零跨月协方差假设；重叠目标与共享训练窗口的依赖由预定方法处理，没有依据结果修改方案。未估计成本、成交、净收益或执行效果。

## 提交与冻结指纹

H1：`31302087a8c25a1c14ecc3b116a39b7810740ad8`。

H2：`b6fd4d8c6695263639c7206563d091b574d790ee`。

H3：包含本报告、父提交为 H2、消息为 `docs: holdout-a confirmatory run, reproduction, record and report` 的提交；最终 SHA 在提交后由 Git 历史与所有者汇报提供，避免自引用。

执行合约 `configs/holdout-a-execution.toml` SHA-256：`02cea40915339da68ba40befcc893872f885444e46d100787ef91533c9e6c408`。

九个冻结文件原始字节 SHA-256：

| 文件 | SHA-256 |
|---|---|
| docs/confirmatory-protocol-v1.md | 38ef1aae687b6cd1a9b8b59709b84047250e2a9f90396cb9089244133b38f1b9 |
| configs/history-extension.toml | e9cbd19d884d046199f468f29e912cd6a9fbec1852e90ffc125b86d8df0bd9af |
| src/quantforge/features/__init__.py | 857819a0c3c718cf2b1100ea72995db989ed149f65d1ad3d5e4cd3eda1fcbec1 |
| src/quantforge/labels/__init__.py | b968218639387cb82ab1beccef5c5bf3ed3674e5975c3d2b25cb4d318037746e |
| src/quantforge/datasets/research_splits.py | 22c862f6e8e232dedfe17c3a9832f5b7d78737de1593d6b904e2c29c5949919c |
| src/quantforge/datasets/splits.py | 3459e5be5185eee2638709c3db4250e150d8e1deff140d1459882cb5257d93cd |
| src/quantforge/datasets/window_splits.py | 24de7804527d3dc805f46ee969d17267927e345b94af434303cd19793675fc22 |
| src/quantforge/models/baselines.py | a9269ca7f6147f653f608051746304596e898f49ec30f9e1714567e25a1ded83 |
| src/quantforge/temporal.py | 6c262bbd1c77e6696c7d310eb31b321f3f27ffeea02fbd64a70f3dcee9bd648c |

## 输入文件 SHA-256

开发文件根目录是获准只读的 `D:\zgs_project\QuantForge\data\processed\binance\spot\BTCUSDT\1m\`，十个 parquet 位于其 monthly 子目录。

| 文件 | SHA-256 |
|---|---|
| development-manifest.json | 6fc42cd53ab30e1282bc9202046edd1fb43e5bc3fa8c44595f1639958c66acb8 |
| BTCUSDT-1m-2025-09.parquet | d8fae895e80dffb1167a2e28daa10ca3add62c4e66146bb107cc5c1bf8cbcae4 |
| BTCUSDT-1m-2025-10.parquet | 2ae90a293b897b2ddc104f6371c81aacf932ac3ee15a8e0e8ec55de96145fe7f |
| BTCUSDT-1m-2025-11.parquet | 9c764376d0fd248d349ff8d30072d06ecd9daae043a98ea5eabd663c534de116 |
| BTCUSDT-1m-2025-12.parquet | ad05078d9280d071e3870db15bb5bea7ec63664083dedc91f797b53e8ac5ea27 |
| BTCUSDT-1m-2026-01.parquet | 1d075c01cd6420245ff0ccc32a21d99a524c948ad0a8eb7ec35bb73781f12fab |
| BTCUSDT-1m-2026-02.parquet | 76e53e461854b0b90dde2adc77ea4108785b62038fd2453457a5435cad91ece3 |
| BTCUSDT-1m-2026-03.parquet | 3d55d765c88a2f7dea16ec34b5bb0d80448b92e0a632711489a5f55b82ac3ff5 |
| BTCUSDT-1m-2026-04.parquet | 4a773e04fd5dcc2c7f905f307510c7eb6215c644e8d969180b7cc1d6c2cfce3b |
| BTCUSDT-1m-2026-05.parquet | 9ed49bcb2d55ea2776cb558994ac8464639365e3a0512217b67f7c84354ed26d |
| BTCUSDT-1m-2026-06.parquet | cb98ce788b75e930323cdc90baed41933f26af9814dc9effeeb82a390ee89b8c |

Holdout 文件位于本 worktree 的 `data/raw/binance/spot/BTCUSDT/1m/holdout-a/`：

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| BTCUSDT-1m-2026-07.zip | 2112275 | 3ef15c84353da48e1c7c5fd99b9751cb036bf8986ed47f7cd1be63359c0d4f6b |
| BTCUSDT-1m-2026-07.zip.CHECKSUM | 88 | e182119afc15ded08bb0292c56418c34e975bba38e6f7e80bde2597676ba976d |
| BTCUSDT-1m-2026-08.zip | 2084946 | acab442e02745177063031d402929703ae983715010064b40cf811c4beb843d4 |
| BTCUSDT-1m-2026-08.zip.CHECKSUM | 88 | 7e5beb54a36e7e7da778f844f93ea28a62daeaad94013c28a33715610fc7c3f6 |

代码执行路径的额外 SHA、逐折训练和评价 index SHA、模型/scaler 状态均在 [data-audit.json](holdout-a-evidence/data-audit.json)、[fold-audit.json](holdout-a-evidence/fold-audit.json) 和 [models.json](holdout-a-evidence/models.json) 中完整保留。
