# Phase 2B 执行记录

开始日期：2026-11-02（Asia/Shanghai）。起始提交 `e257efa`，工作区检查为干净状态。所有开发、原始/处理后数据、缓存、临时文件和测试均限定在 `D:\zgs_project\QuantForge`。

## 运行前固定约定

- 本轮只获取 2025-09 至 2026-06 十个月 BTCUSDT Spot 1m。July/August 2026 最终留出禁止下载、解析、查看行情、统计和评估。
- 先用合成数据验证月历、数值、ZIP、安全预算和时间切分；再下载 September 2025 并通过 checksum、月历与 Parquet 精确往返；允许与 September 1 的官方日档交叉验证。首月通过后才下载另外九个月。
- 固定 14 个已有特征、15m 主任务、5m 辅助任务、1 秒数据可用延迟、seed 42、Ridge alpha=1.0。模型为 Zero、Historical Mean、StandardScaler + Ridge，不调参、不引入新模型。
- 四折分别预测 March、April、May、June。拟合时刻为预测月月初 UTC，训练仅使用此前可用特征和已成熟标签；评估标签截止为下月月初 UTC。March/April 为 Validation；May/June 为 Development Evaluation。
- 固定结果后不因 May/June 表现调整特征、alpha、数据范围或超参。允许修复有证据的实现错误，并记录是否影响结果。确定性复跑不构成新的独立研究样本。
- 不做统计显著性宣称、不报告交易收益/Sharpe/回撤。相对 Zero 的误差变化为描述性指标，重叠目标并非 IID。

## 前置检查

- 已阅读用户指定的源码、实验协议、扩展计划、研究候选、全部既有配置与测试。
- 首次实际运行 `.venv\Scripts\python.exe -m pytest -q`：**91 passed in 3.88s**，原测试全部保留。
- 工作目录磁盘实际可用 **521,379,487,744 bytes**（约 485.57 GiB），高于建议 0.5 GiB。未修改系统配置或全局依赖。
- 基础运行依赖已具备；本轮优先复用 pandas、PyArrow、NumPy、scikit-learn，无需 GPU/PyTorch。

## 数据、实验和最终检查

全部本阶段数据和实验均已实际完成。没有用合成数据代替真实研究，没有因 May/June 结果调整模型或配置。

### 真实数据验证

- 首月 2025-09 官方 SHA-256、ZIP/CRC、43,200 行日历契约、Parquet 精确往返均通过；之后与独立 2025-09-01 日档 1,440 行、14 列逐项精确相等，再执行其他月份下载。
- 取得 2025-09、10、11、12 与 2026-01、02、03、04、05、06，**436,320 根真实分钟 bar**；10/10 官方校验和通过，9 个跨月边界连续；缺失、重复、月外、乱序和 OHLC/volume/close_time 异常均为 0。
- ZIP 正文总量 **21,246,829 bytes（20.2626 MiB）**；Parquet **39,004,829 bytes**；月档 CHECKSUM 网络正文共 968 bytes（包括首月再次刷新）。未决网络预算预留为 0。可选日档另行记录。固定预算没有提高。
- 合并 bars 深层内存 **48,867,972 bytes**，特征 DataFrame 深层内存 **69,266,728 bytes**；这些是数组/索引占用，不是进程峰值。首次完整数据复验约 2.667 秒。
- 只有研究首端 60 行 warm-up；完整数据为一个连续片段，月初不重置特征。15m/5m 标签尾部和每月成熟截止排除都有审计记录。
- 详见 [数据验证](multimonth-data-validation.md) 与 [可追溯元数据副本](multimonth-data-manifest.json)。原始数据、处理后数据、网络账本和大实验文件继续被 Git 忽略。

### 固定实验与复现

- 首轮：`results/research-baseline/20261102T091156471478Z-20164d2d/`。
- 同配置复跑：`results/research-baseline/20261102T091351136724Z-2a6fda52/`。
- 四折 × 两个 horizon × 三模型，共 24 组真实指标；产生 **1,053,840 条预测**（同一个样本分别包含三模型预测，不能当成独立观测数量）。
- 两轮逐批比较预测所有列精确相等；非耗时指标、全部拟合状态和 fold 审计相等；预测 Parquet SHA-256 均为 `66f74e1367ed816455d8df2af5c8fdd89009c893aa5fe064df29fe646c86407a`。所有产物哈希及当前源码与运行时哈希逐一验证。独立证据：[phase2b-reproducibility.json](phase2b-reproducibility.json)。
- 首轮总耗时（报告生成前）**14.5636 秒**，其中数据加载/复验 2.6400 秒、特征 0.2957 秒。八次模型拟合累积：Zero 0.0553 秒、Mean 0.0350 秒、Ridge 1.4245 秒；预测累计分别为 0.00523、0.00533、0.03201 秒。其余耗时涉及标签、时间切分、审计和结果序列化，不是交易延迟指标。
- 15m Ridge 的 MSE 四个月均略低于 Zero，但 June 不及 Mean；5m Ridge 的 MSE 只有 June 略低于 Zero/Mean，其余三个月更差。完整 24 行 MSE/MAE/IC、月度训练/评估数及每次 fit/predict 耗时见 [研究报告](research-baseline-report.md)。不将微小差异称为显著 alpha，更不推导成本后收益。
- 两轮是在提交前运行，provenance 诚实记录父提交 `e257efa` 和 dirty 工作树；每个源码、配置及依赖锁文件有内容 SHA-256。整理文档后创建本阶段最终提交，源码和配置未在观察结果后改变。最终 commit hash 由交付时的 `git log -1` 给出，避免在本提交中伪造自引用哈希。

### 测试与审查

- 首次完整集成：**165 passed in 16.36s**。
- 真实数据实验后的最终完整回归：**165 passed in 16.72s**，输出保存在 `results/phase2b-pytest.txt`。
- 保留全部原有 **91** 项，新增 **74** 项；没有删除旧测试或修改旧数据/特征/标签/模型接口。新增月档 31、研究加载/切分 26、runner 15、日月交叉验证 2。18 类要求映射见 [覆盖矩阵](phase2b-test-coverage.md)。
- 开发中曾有一项合成预算测试将最后一次读取长度错误地硬编码为整块 131,072；真实实现会按剩余正文长度读取 84,209。已修正测试期望后重新全量通过，未提高预算或降低实现校验。
- 独立审阅了下载和文件边界、manifest 预检、成熟标签、训练期 scaler、decision-time 月界及失败产物。未发现当前实现中的泄漏或真实数据异常；结论是本轮检查结果，不是对所有未来改动的保证。

### 本轮实际执行的关键命令

以下均从 `D:\zgs_project\QuantForge` 运行，TEMP/TMP 指向项目 `.tmp`，没有安装/升级全局依赖。

```powershell
git status --short
git log -3 --oneline
git rev-parse --show-toplevel
git remote -v
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe scripts\download_monthly.py --month 2025-09
.venv\Scripts\python.exe scripts\validate_monthly_data.py --cross-check-first-day
.venv\Scripts\python.exe scripts\download_monthly.py --development
.venv\Scripts\python.exe scripts\validate_monthly_data.py --build-development-manifest
.venv\Scripts\python.exe scripts\run_research_baseline.py --config configs\research-baseline.toml
# Repeat the identical offline command once; do not tune.
.venv\Scripts\python.exe scripts\run_research_baseline.py --config configs\research-baseline.toml
.venv\Scripts\python.exe -m pytest -q | Tee-Object -FilePath results\phase2b-pytest.txt
.venv\Scripts\python.exe .tmp\finalize_phase2b.py
```

最后一条是本地一次性文档/证据整理脚本，逐项验证两轮结果和源码哈希并生成小型审计副本；不参与研究指标计算，保留在忽略的 `.tmp`。公开复现只需 README 中的数据与 runner 命令。另实际执行了针对模块的 pytest、文件读取/编译检查、`git check-ignore` 和提交前 diff 审查。最终以 `git -c user.name=zenggs05 -c user.email=zenggaoshan05@gmail.com commit -m "feat: add verified monthly data and research baselines"` 创建本地提交，未改全局身份配置。

### 文件清单

- 新源码：`data_monthly.py`、`research_policy.py`、`research_experiment.py`、`datasets/research_dataset.py`、`datasets/research_splits.py`（均在 `src/quantforge/`）。
- 新 CLI：`scripts/download_monthly.py`、`scripts/validate_monthly_data.py`、`scripts/run_research_baseline.py`。
- 新配置：`configs/research-periods.toml`、`configs/research-baseline.toml`。
- 新测试：`test_data_monthly.py`、`test_monthly_validation.py`、`test_research_dataset.py`、`test_research_splits.py`、`test_research_experiment.py`（均在 `tests/`）。
- 新文档：本记录、`docs/holdout-policy.md`、`docs/multimonth-data-validation.md`、`docs/multimonth-data-manifest.json`、`docs/research-baseline-report.md`、`docs/research-baseline-summary.json`、`docs/phase2b-reproducibility.json`、`docs/phase2b-test-coverage.md`、`research/baseline-findings.md`。
- 更新：`README.md`、`AGENTS.md`、`docs/experiment-protocol.md`、`research/data-expansion-plan.md`。历史研究记录保留，并注明当前阶段的新约束。

### 当前限制与下一步

本轮无环境/网络阻碍，十项 Phase 2B 验收均已实证覆盖。没有执行 push、远程建库、PR 或交易。July/August 2026 最终留出行情仍未获取/解析/查看/统计/评估；历史 Phase 2A 的 HEAD/CHECKSUM 元数据探测记录如实保留。本轮网络账本仅含白名单月档，未追加其他月份。

恢复粒度为完整已验证文件，部分响应重试从零开始并计费；未知接收量的保守预算预留、陈旧锁和损坏缓存不能自动清除。跨机器/操作系统/依赖版本的位级一致性没有测试。统计显著性、成本、交易执行、GPU 模型和最终留出评估均未实现或宣称。

下一阶段优先**冻结预测并评估微小误差改进的稳定性**：预注册配对时间块 bootstrap/HAC（包含块长敏感性和多重比较），然后在相同成熟标签与算力预算下比较扩张窗口、固定滚动窗口及简单遗忘更新。May/June 已经被查看；后续设计调整属于开发，不能再称独立未见评估。先提交协议再实现，继续封存最终留出，不因本轮小 IC 直接升级复杂模型或交易系统。
