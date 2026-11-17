# Phase 2A 实验协议：因果数据与预测基准

> 本文保留 Phase 2A 的原始 smoke 协议及当时状态。Phase 2B 复用这里的 14 个特征和标签 API；正式月度评估的冻结边界、拟合时刻和月末成熟截止以 [holdout-policy.md](holdout-policy.md)、`configs/research-periods.toml` 和 `configs/research-baseline.toml` 为准。下文“尚未下载月档”等描述是 Phase 2A 的历史状态；最新结果见 [Phase 2B record](phase2b-record.md)。

状态：**已实现功能级 smoke test，尚未进行正式多月研究**。核查日期：2026-11-02（Asia/Shanghai）。任务名称：*Delayed-Feedback and Transaction-Cost-Aware Adaptive Alpha Learning for Cryptocurrency Markets*。

本协议定义预测研究的数据契约，不实现自适应交易算法、执行账本、下单或实盘。现有一天样本只验证流水线可运行，不能支持 alpha、显著性或盈利结论。

## 数据和时间语义

- 市场：Binance Spot BTCUSDT，1-minute，统一 UTC。首轮文件为官方验证的 2026-09-01 单日 Parquet；它在计划的多月研究区间外，仅用于 smoke test。
- 每个样本以 `bar_open_time`（分钟起点）标识。`bar_end_exclusive = bar_open_time + 1 minute`，所有区间均为左闭右开。
- `feature_available_time = bar_end_exclusive + availability_delay`，首轮 `availability_delay = 1 second`；`decision_time = feature_available_time`。完整 OHLCV（包括 high/low/close/volume）在分钟结束前不可用，不能把 open time 当成信号可用时间。
- 每个预测标签的 `target_start_time = bar_end_exclusive[t]`，`target_end_time = bar_end_exclusive[t] + h minutes`，`label_available_time = target_end_time + availability_delay`。标签区间 `[target_start_time, target_end_time)` 与信息到达时间分开记录。
- 目标是预测标签 `y[t,h] = log(close[t+h] / close[t])`，其中 `h=15` 分钟为主任务，`h=5` 分钟为辅助任务。它是 close-to-close 的统计预测目标，**不是**决策时刻可成交收益；没有执行价格、队列、价差或冲击的含义。
- 由于目标的起点是当前 bar 结束边界，标签区间可能在 decision time 前约 1 秒开始。这是有意的预测标签语义，不得解释为已知未来或交易回报。若未来需要完整区间位于决策之后，必须另设价格锚点并接受额外延迟，不能悄悄改变本协议。
- 在线更新只能消费 `label_available_time <= as_of` 的标签。`mature_labels` 对显式选择的行严格拒绝未成熟、无效或不一致标签；不允许用 `shift(-h)` 后跳过成熟门禁。

## 因果特征契约

`quantforge.features.build_features` 输出与原始 bar 索引一一对应的结构化特征和时间 metadata，不填充行情、不删除原始行。缺失分钟会切断连续片段；lag、rolling、收益窗口不得跨越缺口。warm-up、零分母和零方差保留 NaN 并标记排除原因。

首轮特征为：

- `log_return_1m/5m/15m/60m`：当前已闭合 close 与过去连续 bar close 的对数差；
- `return_std_5m/15m/60m`：过去连续一分钟对数收益的样本标准差（`ddof=1`）；
- `volume_change_1m`：`V[t]/V[t-1]-1`；
- `volume_zscore_60m`：当前成交量相对前 60 根成交量（不含当前）的 z-score；
- `taker_buy_volume_ratio`：taker buy base volume / total base volume；
- `trades_change_60m`：当前成交笔数相对前 60 根均值的变化；
- `log_high_low`、`log_close_open`、`close_range_position`。

所有滚动统计只在训练窗口或当时已可用的历史观测上计算。首轮模型要求 valid feature/label；没有 forward-fill、未来值回填或用缺失行伪造连续分钟。

## 模型、切分与评估

Baseline 固定为零收益预测、训练期历史均值、`StandardScaler`（仅训练行拟合）+ 固定 alpha 的 Ridge。没有交叉验证、自动调参或神经网络。模型记录名称、固定配置、特征顺序、训练行数、库版本和拟合状态。

切分只能按时间：支持 chronological smoke fold、expanding-window 和 rolling-window。训练候选必须同时满足：特征在 `fit_time` 前已可用、决策时间不晚于拟合时点、标签已成熟、目标区间在第一个评估目标之前结束。半开区间端点相接可用；`embargo_minutes` 是评估前额外 purge，不是泛化到所有场景的 post-test embargo。评估窗按 UTC 时长，不按行数；末尾非完整但达到最小样本的窗保留并标记。

Smoke runner 为每个评估窗统一使用该窗全部标签的最大 `label_available_time` 作为测量 `evaluation_as_of`，先预测再读取评估目标；这不是逐点在线执行延迟模拟，也不会把评估目标用于 fit。真实研究应预注册逐点成熟评估或明确离线测量边界。

当前 `fit_time` 等于该窗首条评估样本的 `decision_time`，表示零训练耗时的研究评估假设。真实拟合/推理耗时并未计入可执行时序，因此不能用于任何 latency 或交易结论。日档缺失会在 runner 的严格来源校验时失败；通用特征/标签模块可保留含缺口输入并逐样本标记无效，当前 runner 不会静默跳过不完整官方日档。

默认 smoke 切分以首个有效样本的 `bar_open_time + 720 minutes` 为评估边界，取随后的 240 分钟、使用固定 alpha=1.0，不搜索最佳窗口。真实日档首 60 行 warm-up；15m 尾部 15 行、5m 尾部 5 行无标签。故有效样本数分别为 1,365 / 1,375。首评估窗为 bar-open `[13:00,17:00)`，对应决策时间 `[13:01:01,17:01:01)`。15m 的训练候选 720 行中 purge 14 行，剩 706；5m purge 4 行，剩 716。这来自真实目标区间与可用时间判据，不是硬编码按 horizon 删除行。

这里的 smoke 窗口基于 bar-open 锚点，长期计划按 decision time 定义研究角色；扩展时应显式转换界点，不将两种锚点混用。窗口有效性、缺口与标签尾部筛选为离线预测评估规则，不能解释为实时交易的“提前知道未来是否缺数据”。

每个评估窗报告 MSE、MAE 和时间序列 Pearson IC。常数预测或常数目标的 IC 为 null 并记录原因；不输出 p-value，不将重叠分钟标签视为 IID。Phase 2A 不输出 Sharpe、回撤、净收益或交易成本结果，因为尚未有经过验证的执行账本。后续若加入交易评价，必须同时报告毛/净收益、费用、滑点/冲击情景、换手、回撤、暴露及合适的区块 bootstrap/HAC 与多重检验处理。

## 训练、验证和最终留出

正式多月方案见 [data-expansion-plan.md](../research/data-expansion-plan.md)：首选 2025-09-01 至 2026-09-01 UTC 半开区间，初始训练 2025-09–2026-02，验证 2026-03–04，开发评估 2026-05–06，最终留出 2026-07–08；备用六个月方案必须在看结果前预先选择。最终留出冻结后只揭示一次，不能用它调参或选择数据范围。特征选择、scaler、阈值、漂移规则和模型参数均只能由训练/验证/开发数据决定。

月档 ZIP 尚未下载或解析；日档解析器固定 1,440 行，不能假设支持月档。扩展前必须完成月档独立契约、SHA256/CRC、schema、跨月连续性和缺失重下机制。ETHUSDT/SOLUSDT 尚未核验，不能假定可用。

## 可复现性、产物和停止条件

`scripts/run_baseline.py --config configs/baseline.toml` 只允许显式 `smoke` 模式，将输出写入被 Git 忽略的 `results/baseline-smoke/<run-id>/`：预测 Parquet、样本审计、模型状态/配置/依赖/Git/数据 hash 的 `summary.json` 和可读 `report.md`。失败只写 `failure.json`，不生成成功 summary。每次运行保留 seed、版本和源 manifest。

阶段 2A 的通过条件是：所有因果/成熟/切分/模型/runner 测试通过，数据哈希可重验，smoke 结果明确标注限制。任何单日数字都不写成真实 Alpha 或策略收益。
