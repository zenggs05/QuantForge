# QuantForge Phase 2E analysis plan

**Status: frozen before implementation.** This protocol is an exploratory post-hoc reanalysis of the frozen Phase 2B/2D-A prediction artifacts and a training-period specification audit. It does not execute Holdout-A/B, fit a new model on 2026-03-2026-06, or make a trading claim. The owner-approved scope is D-008 in `research/DECISION_LOG.md`.

## 1. Scope, evidence and immutable inputs

No network request or download is permitted. Existing Binance Vision BTCUSDT Spot 1m development data (2025-09 through 2026-06) and frozen results may be read only. Holdout-A `[2026-07-01, 2026-09-01)` UTC, Holdout-B `[2026-09-02, 2026-11-01)` UTC, and the 2026-09-01 smoke day are never read for research statistics. All statistics on March-June 2026 are labelled `exploratory_post_hoc` and already-seen.

Frozen inputs and hashes, which the runner must verify before opening prediction files:

- Phase 2B predictions: `results/research-baseline/20261103T010111153259Z-a2cb0858/predictions.parquet`, SHA-256 `66f74e1367ed816455d8df2af5c8fdd89009c893aa5fe064df29fe646c86407a`.
- Phase 2B summary: `results/research-baseline/20261103T010111153259Z-a2cb0858/summary.json`, SHA-256 `7aa9f334a9e5b5f1085edd2362676d86d0898fcdd376f92bb1886efbc3dc6f12`.
- Phase 2D-A predictions: frozen path from the configuration, SHA-256 `11aeb1404b4708da6a936487f0db5dade6a3831c22de503a2bd1f20fb07bcc32`.
- Development manifest SHA-256 `6fc42cd53ab30e1282bc9202046edd1fb43e5bc3fa8c44595f1639958c66acb8`.

The source, frozen plans, configurations and evidence files are never overwritten. The runner reads every numeric and categorical parameter from the hashed TOML configuration and checks the plan/config hashes at startup.

## 2. Hypotheses and evidence layers

Clark-West and IC-HAC address predictive information. DM, MSE and MAE address realized forecast error. They are reported independently. A significant CW result is not evidence of profitability. The output labels engineering correctness, mathematical equivalence, exploratory predictive/error evidence, confirmatory evidence (none in this phase), and tradability (not evaluated).

## 3. Frozen design conventions

All arrays are finite float64 and all timestamps are timezone-aware UTC. A monthly segment is sorted by `decision_time`; missing minutes split HAC segments. Exact timestamp matching, not row offsets, is used whenever data are shifted. `lag 15` is retained as an inadmissible historical comparison for both horizons; admissible lags satisfy `L >= 4*h`, hence `{60, 240, LLSW}` are admissible. The role IDs are `validation=0`, `development_evaluation=1`; `month_index` is chronological `0,1,2,3` for March through June. Ties in concentration rankings are resolved by absolute magnitude descending then `decision_time` ascending then stable original row order. After removing top-k observations, retained timestamps and gaps are preserved; For concentration removal only, center the retained monthly observations by their retained mean, keep missing positions at zero in the centered grid, compute only products at the original UTC minute lags, and divide autocovariances by n_remaining. Then LRV/n_remaining is the mean variance. No removed value is imputed and no rows are compressed. This explicitly defined deletion sensitivity differs from the baseline complete-segment estimator.

For MC and placebo tests, an empirical one-sided p is `(1 + count(t_draw >= t_observed))/(B + 1)`; quantiles are `.025, .05, .5, .95, .975`. The formal DM statistic retains the project sign `d=(y-f)^2-(y-b)^2`, positive meaning Ridge is worse. Monte Carlo improvement-power summaries use `t_improvement=-DM_z`; this is a reporting transformation only and never changes the DM definition. The encompassing approximation is compared with `-DM_z` because its positive direction is improvement. `CW` uses native return-squared units; correlation-style interpretations are labelled equivalent only under explicitly stated zero-mean assumptions and use `2*sd(f-b)*sd(y-b)` as the denominator.

The frozen Monte Carlo DGP uses two 44,640-minute months, 1,024 burn-in minutes, log-volatility AR(1) coefficient 0.98 and stationary log-volatility SD 0.5. Volatility is `exp(logvol/2 - stationary_sd^2/4)`. Independent streams generate stochastic-volatility standardized Student-t(5) minute noise `e` and standard-normal minute signal `s`; each 15-minute target is the exact future sum of its 15 minute components. The signal scale is `s*sqrt(R2/(1-R2))/0.6`, with R2=0.001. Under the nested null, `y=e` and `f=0.6*x`; under the known-slope alternative, `y=e+0.6*x` and `f=0.6*x`. Streams are seeded by `SeedSequence([42, replicate, month, stream_id])`; no DGP tuning is allowed after observing rejection rates. Monte Carlo reports CW one-sided, DM two-sided, and improvement-direction `-DM_z` one-sided results. Size must be in [0.02,0.09] for L=60 and LLSW; failure stops all real-data work.

Cochran Q uses inverse-HAC-variance weights `w_m=1/SE_m^2`, fixed-effect weighted mean, `Q=sum(w_m*(theta_m-theta_FE)^2)`, df=`M-1`, and a chi-square reference. If a month has undefined SE, Q is undefined and retained as such. MDE tables report native units and relative MSE percentages separately; the worst-month sensitivity uses the maximum finite monthly SE. A zero observed effect yields infinite/undefined required months, never a fabricated number.

The three spectral windows are exactly the configured 6M, 3M and 1M Initial-Train windows. Spectra and shrinkage are reported for each window; model fits are only the six full-window-by-horizon fits for the three audit specifications (v1, crash-excluded v1 and v2), with no evaluation forecasts outside Initial Train.

## 4. Reproducibility and resource contract

CPU float64, numerical pools set to one thread, no GPU. One formal and one identical reproduction run, each under 20 minutes and 4,000,000,000 bytes peak process memory (decimal4GB). Synthetic tests are separate and do not open real data. A successful summary is written last; failures retain `failure.json` and never write a success summary. Deterministic numerical/event artifacts are separated from timings, host information and run identity.


## 5. 数学定义（原样写入冻结计划）
记号：每个评估月 m 为一个连续段，配对样本按 decision_time 排序 t=1..T_m；y_t 为冻结标签，f_t 为 Ridge 预测，b_t 为基准（Zero 恒为 0；Historical Mean 为该折常数）。配对必须复用 diagnostics/paired_losses.py 的配对与哈希校验（15m+5m 共 351,280 对）。
5.1 DM 差（沿用项目符号，正=模型更差）：d_t = (y_t − f_t)² − (y_t − b_t)²，双侧检验。
5.2 Clark–West 调整差（正=有预测信息）：c_t = 2(f_t − b_t)(y_t − b_t)，单侧 H1: E[c]>0。恒等式 −d_t = c_t − (f_t − b_t)² 须逐元素测试。
5.3 IC 乘积：z_t = (f_t − f̄_m)(y_t − ȳ_m)/(s_f,m·s_y,m)，均值与标准差（ddof=0）按月计算，mean_m(z) 即月度 Pearson IC；单侧 H1: E[z]>0。
5.4 R²_OOS（Campbell–Thompson）：1 − Σ(y−f)²/Σ(y−b)²；角色级用求和合并。
5.5 段内 Newey–West：γ_k = (1/T_m)Σ_{t=k+1..T_m}(x_t − x̄)(x_{t−k} − x̄)（固定 n 分母），w_k = 1 − k/(L+1)，Var(x̄_m) = (γ_0 + 2Σ_{k=1..L} w_k γ_k)/T_m。必须与 hac_tests.py 数值一致（优先复用其底层实现；不能直接复用时须证明逐值一致，且不得改变其行为）。
5.6 角色合并：n 加权 x̄_R = Σ_m (T_m/T)x̄_m，Var = Σ_m (T_m/T)²Var(x̄_m)（跨月协方差取 0，沿用项目约定）；另报月份等权。t = x̄_R/√Var，正态参考分布；临界值与 p 值一律用 scipy.stats.norm 计算。
5.7 带宽：L ∈ {15, 60, 240, LLSW}，LLSW_m = ceil(1.3·√T_m)（Lazarus–Lewis–Stock–Watson 2018）。可采纳规则 L ≥ 4h：两个 horizon 的可采纳带宽均为 {60, 240, LLSW}；lag 15 一律标记 inadmissible，仅作历史对照。
5.8 n_eff = T·γ_0/LRV，LRV = γ_0 + 2Σ w_k γ_k。
5.9 MDE：单侧 (z_{1−α} + z_π)·SE，双侧用 z_{1−α/2}；α=0.05，π=0.8。报告差分单位、相对基准 MSE 的百分比（DM）与 IC 单位（CW/IC）。所需月数 M_req(δ) = M_obs·((z_{1−α}+z_π)·SE_obs/δ)²，假设见 WP3(d)。
5.10 Encompassing 功效近似：Δ_t = f_B − f_A，e_t = y_t − f_A，ρ′ = corr(Δ, e)，t_approx = √(n_eff,d)·(ρ′ − σ_Δ/(2σ_e))，其中 n_eff,d 为 d_t 的有效样本量。先在 2D-A 的 Rolling{1M,3M,6M} vs Expanding 上计算 ρ′、t_approx 与实际 HAC t（L=60 与 LLSW），报告近似误差；再对假想的月内更新频率对比输出网格 ρ′ ∈ {0.0025,0.005,0.01,0.02} × σ_Δ/σ_{f_A} ∈ {0.01,0.03,0.1,0.3}，给出 t_approx 与达到 t = z_0.95 + z_0.8 ≈ 2.487 所需月数。这是规划工具，不是检验。
5.11 集中度：对序列 x 取 |x_t| 最大的 k 个（k ∈ {1,10,100}），报告其在 Σx 中的有符号占比，以及剔除后的均值、t 与符号是否翻转。

## 6. 工作包
WP0 冻结（提交 A）
- docs/phase2e-analysis-plan.md：完整写入第 5 节、第 6 节全部工作包、第 10 节停止规则、第 11 节锚点、全部族与种子、输出清单。冻结后不得修改。
- configs/power-reanalysis.toml，至少包含：
  [experiment] mode="power_calibrated_reanalysis", horizons=[15,5], seed=42, cpu_threads=1, precision="float64"
  [inputs] phase2b_predictions_path、phase2b_predictions_sha256="66f74e1367ed816455d8df2af5c8fdd89009c893aa5fe064df29fe646c86407a"、phase2b_summary_sha256="7aa9f334a9e5b5f1085edd2362676d86d0898fcdd376f92bb1886efbc3dc6f12"、phase2d_a_predictions_path、phase2d_a_predictions_sha256="11aeb1404b4708da6a936487f0db5dade6a3831c22de503a2bd1f20fb07bcc32"、development_manifest_sha256="6fc42cd53ab30e1282bc9202046edd1fb43e5bc3fa8c44595f1639958c66acb8"
  [inference] lags=[15,60,240], llsw_constant=1.3, admissible_min_lag_multiple=4, pooling=["n_weighted","month_equal"], alpha=0.05, power=0.8, effect_scenarios=[0.5,0.75,1.0]
  [placebo] sign_flip_block_minutes=60, sign_flip_draws=200, time_shift_minutes=1440
  [concentration] top_k=[1,10,100]
  [encompassing] rho_grid=[0.0025,0.005,0.01,0.02], delta_ratio_grid=[0.01,0.03,0.1,0.3]
  [spec_audit] initial_fit_time="2026-03-01T00:00:00Z", duplicate_corr_threshold=0.999, crash_session=["2025-10-10T20:00:00Z","2025-10-11T00:00:00Z"], top_k=[1,10,100,1000], window_starts 1M="2026-02-01T00:00:00Z", 3M="2025-12-01T00:00:00Z", 6M="2025-09-01T00:00:00Z"
  [cost_magnitude] thresholds_bp=[1,2,5,10,15,20]
  [holdouts] holdout_a=["2026-07-01T00:00:00Z","2026-09-01T00:00:00Z"], holdout_b=["2026-09-02T00:00:00Z","2026-11-01T00:00:00Z"], smoke_day="2026-09-01"
  [output] directory="results/power-reanalysis"
  runner 必须从此配置读取全部参数，并在启动时断言与冻结计划一致；不得硬编码。
- docs/confirmatory-protocol-v1.md（内容见 WP5）。
- docs/phase2e-plan-manifest.json：上述三文件的 SHA-256（UTF-8 LF 字节）、frozen_at、起点提交。
- .gitattributes：新冻结文件设为 eol=lf；docs/phase2e-evidence/* 使用与 C1 证据相同的 -text 规则；不改已有条目。
- AGENTS.md：新增 Phase 2E 授权条目（英文，与第 3 节一致，并写入 Holdout-B 保护规则）。
- docs/holdout-policy.md：新增 Holdout-B 小节——区间、保护规则（未经所有者明确批准，不得下载、读取、解析、分析或用于调参/模型选择）、D-011 列出的已知暴露。不修改既有段落。

WP1 推断库 src/quantforge/diagnostics/forecast_tests.py：实现 5.1–5.11（dm_differential、clark_west_differential、ic_product、r2_oos、newey_west_segment、pool_roles、llsw_lag、admissible、effective_sample_size、mde、months_required、encompassing_approx、concentration、cochran_q）。纯函数、float64，显式输入校验（有限值、等长、时间有序）。

WP2 安慰剂 src/quantforge/diagnostics/placebo.py：
- P1：段内按 decision_time 的 UTC 小时分块，y 乘以块级 Rademacher ε，种子 SeedSequence([42, h, role_id, month_index, draw])，B=200；对 Zero 基准重算角色级 CW t；输出安慰剂 t 的均值、SD、分位数，以及观测 t 的经验单侧 p。
- P2：段内把 f_t 与 y_{t+1440} 配对（丢弃无法配对的尾部），重算 CW t。

WP3 探索性重分析 runner：src/quantforge/power_reanalysis.py + scripts/run_power_reanalysis.py
- 读取前核对全部输入哈希；只读冻结预测；重分析路径不得调用任何 fit（测试 spy 保证）。
- 每张输出表与 JSON 记录都带 evidence_level 字段：真实数据统计为 "exploratory_post_hoc"，合成为 "synthetic"，设定审计为 "training_period_diagnostic"。
- 输出到 results/power-reanalysis/<run-id>/：
  a) 证据分层总表：对 {15m,5m} × {Validation, Development} × {Zero, Historical Mean} 并列四层，任何一层都不得省略——① 预测信息：CW t 与单侧 p、月度 IC 与 IC-HAC t、R²_OOS；② 实际预测误差：MSE、MAE 及相对基准的变化，原 DM 型平方损失检验（z、双侧 p，与 2C 定义一致）；③ 统计显著性状态：所属族、Holm 调整 p、"exploratory_post_hoc"；④ 可交易性："not evaluated"。
  b) 全部带宽 × 两种合并的 CW、IC、R²_OOS、DM 明细；月度表；n_eff。
  c) 2D-A：Rolling{1M,3M,6M} − Expanding 的 DM（全部带宽；标注 lag 240 与 LLSW 下 Holm-6 的变化）、MSE/MAE、集中度、encompassing 校准。
  d) MDE/功效表：Ridge vs Zero（DM 与 CW）、窗口对比、5.10 的假想更新频率网格，含所需月数。必须附“假设与敏感性”小节：自相关（所用带宽与 n_eff）；非平稳（以 HAC SE 加权的 Cochran Q 月间异质性检验，同时给出“合并 SE”与“最差月份 SE”两种 MDE）；月份数量（观测月数，以及 √M 缩放所假设的月间独立同分布，异质性存在时注明该假设偏乐观）；效应大小（事后观测效应有选择偏差，规划效应同时给出观测值的 0.5×、0.75×、1.0× 三档）。
  e) 安慰剂 P1/P2。
  f) 5.2 恒等式的最大误差。
  g) 费用量级表：冻结 2B Ridge 的 |f|（bp）p50/p90/p99/max，以及 |f| ≥ c 的分钟占比，c ∈ thresholds_bp，按 horizon × month。标注：阈值是示意情景，未核实当前 Binance 费率；不计算 PnL、不模拟成交。
  h) 探索性 Holm：只在预先声明的族 F_CW = {15m,5m} × {Validation,Development} × {Zero,Mean}（LLSW，8 项）内进行，并标注 "exploratory, post-hoc, already-seen months"。
- 先写全部产物，最后写 summary.json；失败写 failure.json，不写成功 summary。确定性产物与计时、主机信息分开存放。

WP4 设定审计 src/quantforge/diagnostics/spec_audit.py（由 runner 调用）
- 数据：March 折初始训练集（只读复用 load_research_dataset、build_features、build_labels、make_research_splits），预期 15m 260,564 行、5m 260,574 行。
- 输出：14×14 相关矩阵；|corr| > 0.999 的特征对；标准化协方差（ddof=0）的特征值与条件数；A = n·C + αI 的条件数；α=1 下各方向收缩因子 nλ/(nλ+α)，分别针对 Expanding(6M)、3M、1M 三个训练集合（起点见配置，均在 Initial Train 内）；gap 恒等式 g = log_return_1m − log_close_open 与 log(open_t/close_{t−1}) 的最大绝对差（应 ≤1e-12）；g 的分位数与零值比例；前 k ∈ {1,10,100,1000} 分钟在 Σg² 与 Σg·y（训练标签）中的占比，以及前 10 分钟的时间戳；crash_session 的占比。
- 只在 Initial Train 上拟合 StandardScaler + Ridge(alpha=1, solver="svd")，三种设定：(i) v1 基线；(ii) v1 剔除 crash_session 行；(iii) spec v2。报告系数（bp per SD）、样本内预测 SD、条件数、最弱方向收缩。不对 2026-03…2026-06 产生任何预测（测试 spy 保证）。
- spec v2 的冻结规则：Initial Train 上 |corr| > 0.999 的特征对，删除规范顺序中靠后的特征；按现有数据即删除 log_close_open，其余 13 个特征保持原顺序。若规则识别出不同的特征对，按规则执行并如实记录，不得人工挑选。

WP5 确认性协议 v1（docs/confirmatory-protocol-v1.md，在 WP0 中冻结，本阶段不执行）
- 主假设 H1 的对象：Phase 2B 原程序（spec v1：14 特征，StandardScaler + Ridge(alpha=1, solver="svd", fit_intercept=True)），训练自 2025-09-01（预热后），在 2026-07-01T00:00:00Z 与 2026-08-01T00:00:00Z 各做一次扩展窗口重拟合，只用 label_available_time ≤ fit_time 的标签；评估 decision_time ∈ [2026-07-01, 2026-09-01)，标签成熟截止为各月末（与 2B 相同）；horizon 15m。
- 主检验：CW vs Zero，单侧，α=0.05；每月 Bartlett L_m = max(60, LLSW_m)；七月与八月 n 加权合并，跨月协方差取 0；当且仅当 p < 0.05 时拒绝 H0（无预测信息）。主族只有这一项。主检验结论只涉及预测信息，不涉及预测误差或可交易性。
- 次要族（报告，族内 Holm，不作确认性主张）：S1 spec v2 同一检验；S2 5m spec v1 CW；S3 15m spec v1 vs Historical Mean CW；S4 15m spec v1 vs Zero 的 DM（双侧，有限样本 MSE 优势）及 MSE/MAE；S5 15m spec v1 的 IC-HAC。
- 已知暴露：2026-09-01 smoke 日固定了 Holdout-A 期末价格水平（依赖漂移的统计不得作确认性主张）；Phase 2A 曾记录 2026-07/08 ZIP 的 Content-Length 与 CHECKSUM 元数据；2026-03…06 的结果均已被多次查看。
- Holdout-B 不属于本协议；任何使用都需要单独冻结协议并获所有者批准。
- 解封条件：所有者明确书面授权 + 引用本协议哈希 + 冻结后特征/标签/模型代码未改动（列出代码文件哈希）。
- 功效声明不写进冻结协议；2E 完成后另写 docs/confirmatory-protocol-v1-power-addendum.md，引用协议哈希，不得修改协议本身。

WP6 Holdout 防护
- research_policy 提供 HOLDOUT_A、HOLDOUT_B、SMOKE_DAY 常量（取值与配置一致）以及 ensure_not_holdout(start, end_exclusive)，任何与 Holdout-A 或 Holdout-B 有交集的请求一律抛错；使用 FINAL_HOLDOUT_END 与 validate_development_bounds，或删除它们并说明原因。
- 调用位置：data.download_day（任何网络请求之前）；data.parse_klines（读取前按文件名日期检查，解析后按 open_time 范围再检查）；experiment.load_verified_sample（检查 manifest 的 date_utc 与 open_time 范围）；scripts/download_sample.py。
- 2025-09-01 与 2026-09-01 两个已存在日文件的现有行为不变；研究加载器对 smoke 日的既有拒绝保持不变。

WP7 溯源与卫生
- 把 .tmp/finalize_phase2b.py、.tmp/finalize_phase2c.py、.tmp/build_phase2d_docs.py、.tmp/finalize_c1_evidence.py 原样复制到 tools/provenance/，并附 README.md（每个文件的 SHA-256、它生成了哪些已提交文件，以及 "historical; not re-executed"）。不得修改内容。
- docs/phase2e-evidence/README.md：说明这些聚合表派生自 Binance Vision 数据（CC BY-NC-SA 4.0，须署名 Binance Vision，仅限非商业），公开发布权限须在发布准备阶段另行核查。
- README：把顶部标语改为与当前实现一致的范围（delayed-label prediction research, causal features, robust forecast evaluation, online-learning infrastructure），把 market microstructure 与 cost-aware decisions 明确写成未来方向；新增 Phase 2E 小节，按证据层级陈述结果。
- 发布相关的存量问题（第三方源码许可、旧证据 CRLF 哈希、测试自包含）不在本阶段处理，留给发布准备阶段；但本阶段新增的文件必须从一开始满足：新增证据使用 -text、新测试自包含、不新增第三方源码拷贝。

WP8 文档
- docs/phase2e-report.md（英文）：包含全部结果。每节标注证据层级（工程/数学正确性、探索性统计证据、确认性证据=无、可交易性=无）；预测信息与实际预测误差分开陈述；不得写“存在 alpha”“v2 更好”“在线更新有用/无用”。
- docs/phase2e-record.md：命令、版本、耗时、偏离、失败。
- docs/phase2e-reproducibility.json：正式运行与复现运行的产物哈希及允许的差异项。
- docs/phase2e-evidence/：只放聚合表（CSV/JSON，-text）。
- docs/phase3a-history-extension-proposal.md（草案，不冻结，不下载），必须包含：
  - 数据范围（BTCUSDT 现货 1m 月度 2020-01…2025-08；2018–2019 仅作可选稳健性）；
  - 下载规模估算与预算上限；
  - 校验方式（官方 CHECKSUM、哈希链、缺口/重复检查）；
  - 时间戳格式（2025 年前为毫秒、之后为微秒）及解析策略；
  - ALLOWED_MONTHS 策略的变更方式；
  - 严格前向滚动设计：每个评估月只用该月 fit_time 之前已成熟的标签训练，评估月份从 2021-01 起逐月推进；禁止用后期训练的模型预测更早的月份并称之为样本外；
  - 检验对象：spec v1（主）与 spec v2（次）；主检验 CW 及其族；参数稳定性检验与以波动率为工具变量的条件 GW；
  - 基于 2E MDE 表的功效论证（写明假设）；
  - 许可（Binance Vision Terms v1.0，CC BY-NC-SA 4.0，非商业，禁止托管原始数据）；
  - 明确不触碰 Holdout-A/B；
  - 需要所有者决定的问题清单。
- docs/confirmatory-protocol-v1-power-addendum.md：用 2E 的 SE 与 n_eff，在 0.5×/0.75×/1.0× 观测效应下给出主检验的功效，并写明假设。

## 7. 测试（新增测试全部用合成数据；现有 484 项保持通过）
- tests/test_forecast_tests.py：5.2 恒等式逐元素；Zero 基准下 c = 2fy；R²_OOS 恒等式；与 hac_tests.py 在同一序列上 HAC 一致（相对误差 ≤1e-15）；LLSW 与可采纳标记；MDE、功效、所需月数对照 scipy；Z 常数对照 scipy.stats.norm.ppf；AR(1) 的 n_eff 解析值；集中度与剔除 top-k 的手算向量；合并权重；Cochran Q 的手算案例。
- 蒙特卡洛（固定种子，合计 ≤60 s，默认运行）：使用重尾且波动聚集的合成分钟收益（例如对数波动率 AR(1) 随机波动 + t5 新息，用 numpy/scipy.signal.lfilter 向量化），聚合成重叠的 15m 标签，每月 44,640 点 × 2 个月。(a) 检验水平：f 与 y 独立，R ≥ 400，CW 单侧 5% 拒绝率在 L=60 与 LLSW 下都须落在 [0.02, 0.09]；(b) 嵌套零假设下 DM 统计量的均值为负；(c) 功效：已知斜率 0.6、信号 R²≈0.001 时，CW 拒绝率高于 DM。
- tests/test_placebo.py：种子确定性；合成零假设下安慰剂 t 近似 N(0,1)；时间平移的对齐正确。
- tests/test_spec_audit.py：合成近重复特征对的识别；特征值与收缩公式；合成 K 线上的 gap 恒等式；杠杆占比；spy 证明设定审计不对 2026-03…06 产生预测。
- tests/test_power_reanalysis.py：用合成“冻结预测”夹具（不读真实数据）跑通 runner；哈希不符时拒绝；spy 证明重分析路径不调用任何 fit；两次运行确定性产物逐字节一致；evidence_level 字段齐全；成功 summary 最后写入；失败时写 failure.json。
- tests/test_holdout_guards_daily.py：对 2026-07-15、2026-08-31（A）以及 2026-09-02、2026-10-31（B），用 spy 证明在 fetch/open 之前即抛错；2025-09-01 与 2026-09-01 不受影响。
- 新测试不得写出 .tmp/pytest 之外，不得调用真实 git 或网络。

## 8. 计算预算
仅 CPU、float64、数值线程池为 1（沿用 threadpoolctl 的做法）；不使用 GPU（全部是 O(n) 矩统计，GPU 只会引入非确定性）。单次正式运行 ≤20 分钟、内存 ≤4 GB；正式运行 1 次 + 复现运行 1 次；合成测试不计入。

## 9. 执行顺序与提交
1) 确认工作树干净与 HEAD；核对全部输入哈希；运行现有测试（预期 484 passed）并记录。
2) 提交 A（只含冻结文件、.gitattributes、AGENTS.md、docs/holdout-policy.md）："docs: freeze phase2e power-calibrated reanalysis and confirmatory protocol"
3) 实现 WP1–WP4、WP6 与全部测试，完整测试通过后提交 B："feat: add nested forecast tests, power analysis, specification audit and holdout guards"
4) 在干净的提交 B 上执行正式 run 与复现 run（独立目录、分别标注），核对确定性产物逐字节一致。
5) 提交 C（报告、聚合证据与其 README、复现清单、3A 提案草案、功效附录、tools/provenance、README）："docs: record phase2e power reanalysis, specification audit and holdout pre-registration"
不 push、不建 remote、不开 PR。

## 10. 停止条件（立即停止并汇报，不得放宽或绕过）
- 工作树不干净、任何输入哈希不符、现有测试失败。
- 检验水平蒙特卡洛不在 [0.02, 0.09]：停止，不运行真实数据部分。
- 任一锚点（第 11 节）在容差内无法复现：停止排查，不得调整定义去凑数。
- 安慰剂 P1 的 t 均值绝对值 > 0.3 或 SD ∉ [0.8, 1.25]：完成报告，但标记“管线问题，真实 CW 不得解释”，并在汇报中突出。
- 出现任何需要网络、下载、Holdout-A/B 数据，或在 2026-03…06 拟合/评估新模型的需求。
- 冻结后发现计划错误：不得修改冻结文件；在 record 中如实记录偏离；若影响结论，停止并请求新协议。

## 11. 锚点（新代码路径必须复现）
A1 15m Validation、n 加权、L=60、Ridge vs Zero：相对 MSE 差 −0.057346%；DM |z| = 0.7669 ± 0.0005，方向为 Ridge 误差更低。
A2 同一范围 CW t = 2.354 ± 0.001（单侧 p ≈ 0.0093）。
A3 15m Development、n 加权、L=60、vs Zero：CW t = 1.575 ± 0.001。
A4 新代码路径重算 Phase 2C 的 96 行 HAC 结果，与 docs/statistical-evidence/hac-results.csv 逐行一致（相对误差 ≤1e-12；最小原始 p = 0.2011929920）；Phase 2D-A 的 144 行与 docs/phase2d-a-evidence/hac-results.csv 一致（含 6 个 null）。
A5 15m Validation 的 n 加权月度 Pearson IC = 0.03338 ± 0.00001。IC-HAC t 按 5.3/5.5/5.6 计算并报告（审计中不同定义的参考值为 2.8–3.1），不作硬锚点。
A6 设定审计：初始训练行数 260,564（15m）/ 260,574（5m）；corr(log_return_1m, log_close_open) = 0.99993 ± 0.00001；15m 下 A = n·C + I 的条件数 = 51,607.95 ± 0.05；最弱方向收缩因子 = 0.94333 ± 0.00001。
A7 2B 月度 MSE 与 MAE（ridge / zero / historical_mean × 15m/5m × 4 个月）与 results/research-baseline/20261103T010111153259Z-a2cb0858/metrics.csv 一致（相对误差 ≤1e-12）。

## 12. 最终汇报（中文，按此顺序）
1) 执行摘要（≤10 行）。
2) 三个提交的 SHA 与冻结文件哈希。
3) 测试总数、新增数、耗时；检验水平与功效蒙特卡洛结果。
4) 锚点 A1–A7 实测值对照表。
5) 证据分层总表（预测信息 / 实际预测误差 / 显著性状态 / 可交易性）。
6) 安慰剂 P1/P2。
7) 带宽敏感性与集中度（含 2D-A 在 lag 240 与 LLSW 下 Holm-6 的变化）。
8) encompassing 校准误差与假想更新频率的 MDE 网格（所需月数），以及“假设与敏感性”小节摘要。
9) 设定审计（gap 杠杆、收缩、v2 规则结果、三种训练期拟合对比）。
10) 费用量级表。
11) 确认性协议 v1 的摘要、哈希与功效附录要点，并声明 Holdout-A/B 仍然封存。
12) 3A 历史扩展提案要点与需所有者决定的问题。
13) 偏离、问题与未完成事项。
14) git status、未 push、无网络、无 Holdout 访问，以及未修改 CLAUDE.md 与 research 状态文件的声明。

## Additional implementation conventions fixed before outcomes

- Every statistical parameter, threshold, source path, seed, family, quantile and resource bound is obtained from the hashed TOML. The runner validates the manifest and three frozen-file digests before reading artifacts, and validates runtime policy constants against configured holdouts. Algorithmic constants (zero, one, the algebraic factor two, minute-to-second conversion) are definitions, not tunable settings.
- The complete request sections above are authoritative. The English conventions resolve notation or implementation ambiguities without replacing their definitions. Config keys with start/end names encode the same half-open intervals requested as arrays.
- Baseline contrasts are ridge versus zero and historical_mean. Roles are validation (March/April) and development_evaluation (May/June); month indices 0..3. Window contrasts are rolling_1m, rolling_3m, rolling_6m versus expanding. Every bandwidth and pooling is retained. F_CW contains exactly eight n-weighted LLSW tests (two horizons, two roles, two references). For window DM, preserve the original six-test primary family (15m, three windows, two roles, n-weighted) independently at each bandwidth. Additional anchor-reproduction multiplicity follows old code exactly and is labeled historical.
- The combined effective sample size is a design-effect equivalent: T * Var_IID(pooled_mean) / Var_HAC(pooled_mean), where Var_IID=sum(w_m^2 * gamma0_m/T_m). It is not clipped at T. Month-wise values use the requested T*gamma0/LRV formula. Degenerate variance/denominators produce null with a reason, never arbitrary epsilon regularization.
- Cochran Q uses scipy.stats.chi2.sf with M-1 degrees of freedom, as required by its definition; scipy.stats.norm governs the forecast mean tests and power critical values. Non-rejection of Q with two months does not establish stationarity.
- Primary evidence-layer table uses n-weighted LLSW; all other lags/poolings remain in detailed tables. Role R2 uses sums, including in the month_equal reporting table (explicitly marked sum-pooled R2). Role MSE/MAE follow selected weights; native n-weighted role R2 is 1-MSE_model/MSE_reference.
- MDE and hypothetical-grid calculations use the exact norm.ppf critical sum, never rounded 2.487. Worst-month sensitivity uses max(monthly SE) directly. Negative or zero beneficial observed effects have no finite required month count; rows retain null/reason. Effect scenarios are 0.5/0.75/1.0 times the beneficial observed effect, with explicit post-selection and stationarity caveats. CW correlation-equivalent MDE divides by 2*sd(f-b)*sd(y-b), a zero-mean planning approximation, not Pearson IC.
- Encompassing calibration reports monthly and role statistics; role rho and SD are n-weighted monthly-centered covariance summaries. Its approximate improvement t is compared with -DM_z, retaining the original DM_z and absolute approximation error. Hypothetical grid uses the observed frozen Expanding forecast/error SDs and n_eff,d from each existing window contrast; results are keyed by that calibration reference, not pooled into a selected favorable scenario. Required months are M_obs*(critical/t_approx)^2 only when t_approx>0.
- P1/P2 use n-weighted pooling at lag60 and LLSW; observed and shifted/perturbed tests have explicit sample counts. P1 stops interpretation if any horizon/role/bandwidth fails the specified mean or SD interval. Empirical p uses the plus-one correction. UTC-hour sign flips share one epsilon for a given month/draw/hour; no row-resampling.
- Monte Carlo stream IDs:0 logvol innovations and its stationary initial draw,1 standardized Student-t noise,2 independent normal signal. Burn in1024, then form h-minute future rolling sums yielding44640 observations/month. Per-replicate/month RNG makes batching irrelevant. All400 replicates count; report CW size and power, DM two-sided and improvement one-sided power, and mean improvement-direction DM under null. A fast Bartlett rolling-sum identity may be used only after testing against the legacy estimator; synthetic Monte Carlo is not a real-data result. No DGP/threshold change after its first level result.
- Specification gap rankings: independently rank by abs(g) for sum(g^2), and abs(g*y) for signed sum(g*y); retain both denominators, rankings and top timestamps. Zero-denominator shares are null. Crash interval uses decision_time. Duplicate-feature deletion uses the union of later canonical features in all threshold-exceeding pairs, independently per horizon. Audit variants are fitted only on the full6M legal Initial Train (six total Ridge fits);3M/1M only have scaling/spectral diagnostics. No new evaluation forecast is produced.
- Required deterministic outputs: evidence-layers.csv, monthly-tests.csv, role-tests.csv, window-tests.csv, window-monthly.csv, concentration.csv, mde-power.csv, encompassing-calibration.csv, encompassing-grid.csv, placebo.csv, cost-magnitude.csv, legacy-hac-2c.csv, legacy-hac-2da.csv, baseline-metrics.csv, specification-audit.json, anchors.json, identity.json, resolved-config.json, report.md. Additional small diagnostic tables may be added without omitting these. Each table row and JSON evidence record carries an evidence_level. Runtime/provenance/budget are separate. Summary is written last.
- Baseline preflight: specified HEAD clean, four required input hashes matched,484 existing tests passed in85.01s on2026-11-04. No implementation has started and no new real analysis has run. Freeze-draft encoding corruption was detected before hashing and replaced using the original UTF-8 attachment; it is not a post-freeze amendment.
