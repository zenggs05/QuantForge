# Phase 2A 执行记录

日期：2026-11-02（Asia/Shanghai）。阶段一基线提交：`b971f1b`（`chore: establish validated QuantForge research foundation`）。

## 本阶段授权范围

在既有 QuantForge 独立仓库内完成实验协议、因果特征/标签和时间划分的轻量实现/验证，并整理供审阅的历史数据扩展方案。现有 BTCUSDT 2026-09-01 单日真实样本用于流水线 smoke test；不将一天预测结果当作可复现 alpha，不增加批量市场数据、不实盘、不推送或创建远程仓库。

阶段一基线提交前，各子任务只读现有源码/文档并做有限来源查询。基线提交完成后才创建本阶段文件。实验时间契约和后续研究方案见 [experiment-protocol.md](experiment-protocol.md)，数据范围/预算见 [data-expansion-plan.md](../research/data-expansion-plan.md)。

## 已完成的来源核验

- 实际读取既有 AGENTS、README、研究候选、文献、路线图、数据源码、现有测试和配置。
- 对 2025-09 至 2026-08 的 12 个 BTCUSDT Spot 1m 月档做 HEAD，每个对应 CHECKSUM 做小型 GET，全部 HTTP 200；ZIP body 下载量为 **0**。
- 服务端 ZIP Content-Length 合计 **25,444,050 bytes（约 24.27 MiB）**，理论分钟数 **525,600**；仅为计划和元数据，未核验月档正文、checksum 匹配、schema 或连续性。
- CHECKSUM 正文共 **1,056 bytes**；记录全部官方声明 SHA256、URL、检查时间 UTC、HTTP、Content-Length、Last-Modified、ETag。证据保存为 [data-expansion-probes.json](../research/evidence/data-expansion-probes.json)。
- 重新读取 Binance 官方 README 和条款（HTTP 200）：2025 年起 Spot 微秒时间戳，月档发布安排，归档可修订，以及数据 CC BY-NC-SA 4.0/非商业条款。代码 MIT 不能当作数据许可。

## 计划中的研究数据边界

首选 12 个月 `[2025-09-01, 2026-09-01)` UTC：训练 2025-09–2026-02、验证 2026-03–04、开发评估 2026-05–06、最终留出 2026-07–08。六个月备选为 2026-03–05 训练、06 验证、07–08 最终留出。每个阶段均须应用标签成熟、区间不重叠和可配置 gap 规则，不能仅按源 CSV 行划分。

已见的 2026-09-01 单日样本在计划研究区间之外，仅供技术验证。ETHUSDT/SOLUSDT 只作为后续候选，本阶段未查询或下载其归档。

## 实现与验证记录

新增实现：

- `src/quantforge/temporal.py`：UTC/分钟对齐、bar 合约、缺口分段和时间诊断。
- `src/quantforge/features/__init__.py`：14 个透明分钟特征、可用时间 metadata、warm-up/缺口排除。
- `src/quantforge/labels/__init__.py`：5/15 分钟 close-to-close 标签、区间和严格成熟门禁。
- `src/quantforge/datasets/splits.py`：chronological/expanding/rolling 时间窗、成熟标签筛选、目标重叠 purge 和 embargo 诊断。
- `src/quantforge/models/baselines.py`：zero、historical mean、train-only StandardScaler + Ridge，以及描述性 MSE/MAE/时间序列 IC。
- `src/quantforge/experiment.py`、`scripts/run_baseline.py`、`configs/baseline.toml`：来源 hash 重验、特征/标签/切分/基准整合、版本/seed/Git provenance、Parquet 和 Markdown 输出。

实际执行命令：

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe scripts\run_baseline.py --config configs\baseline.toml
```

全量测试结果：**91 passed in 3.76s**（第一阶段原有 23 项 + 新增因果特征/标签 31 项 + 时间切分/模型 29 项 + runner 集成 8 项），无失败或跳过。依赖锁检查和 `uv sync --locked --extra dev` 已完成；新增直接依赖为 NumPy、scikit-learn 和 threadpoolctl，SciPy 是由 scikit-learn 引入的传递依赖。实际环境 scikit-learn 1.9.1、SciPy 1.18.1、NumPy 2.5.4。runner 使用 CPU 单线程限制，未安装 PyTorch/CUDA。

真实单日 smoke runner 成功生成两次被忽略的输出目录；正式记录的最新运行结果为 `results/baseline-smoke/20261102T080930542169Z-e7b054b3/`。2026-09-01 样本、训练和评估均只用于功能检查：15m 为 706 train / 240 eval，5m 为 716 train / 240 eval。

| Horizon | Model | MSE | MAE | Pearson IC |
|---:|---|---:|---:|---:|
| 15m | zero | 6.040171e-06 | 0.00185275 | null（constant prediction） |
| 15m | historical mean | 5.993612e-06 | 0.00185849 | null（constant prediction） |
| 15m | Ridge | 1.023632e-05 | 0.00263910 | -0.270444 |
| 5m | zero | 2.201886e-06 | 0.00110700 | null（constant prediction） |
| 5m | historical mean | 2.196430e-06 | 0.00110718 | null（constant prediction） |
| 5m | Ridge | 2.871188e-06 | 0.00129069 | -0.120054 |

这些数值是**单日 smoke-test 描述性输出**，不是正式实验、显著性、可交易收益或策略表现；没有报告 Sharpe、回撤或成本。Ridge 评估在固定配置下进行，无调参；训练 scaler 只使用对应 fold 的训练行，预测先于统一评估时点的目标读取。

## Git 与实现审查

- 首先实际执行原有两份测试：**23 passed in 1.12s**，再创建第一阶段基线提交 `b971f1b`。Git 作者身份最初未配置，用户提供提交邮箱并同意使用 GitHub 用户名；仅通过每条提交命令的 `git -c user.name=... -c user.email=...` 设置，没有修改全局身份。
- `.gitignore` 已补凭据、虚拟环境别名、训练输出与常见缓存规则；保留无真实密钥的 `.env.example`。基线 `git add --dry-run .` 和候选文件检查无市场数据、环境目录、秘密模式或 >5 MiB 文件；已有研究证据中的第三方文档 whitespace/reST 下划线引发 `git diff --check` 提示，保留原样以维护来源记录，没有当成项目合并冲突。
- 本阶段没有修改 `src/quantforge/data.py` 或第一阶段两份测试，没有删除既有断言。新的 runner 重用严格单日解析器，不能降级原始验证要求。
- 审查中修正：runtime 不强制安装 pytest；provenance 失败纳入失败记录；先完成报告再写成功 summary，并以同目录临时文件原子替换 JSON，避免报告失败仍宣称成功。对应失败路径有回归测试。
- 单日 smoke 两次早期运行在功能修正前已成功。后续相同配置的运行用于确认最终提交对应源码，运行标识/commit/dirty 状态以各自 `summary.json` 为准，不将本文件中的数字解释为新的独立市场实验。
- 最终特性提交使用 `feat: add causal alpha baseline and delayed-label protocol`，实际 commit hash 以 `git log -2` 为准；最终 smoke 输出和所有市场数据继续被 Git 忽略。没有 remote、push、PR 或实盘操作。

本阶段其他实际命令包括 `uv lock`、`uv sync --locked --extra dev`、`uv export --locked --extra dev --format requirements-txt --no-emit-project --output-file requirements-lock.txt`、`uv pip check`、`git diff --check`、`git add --dry-run .` 以及本地提交。所有缓存/临时目录和命令工作目录位于 QuantForge 内。

## 依赖和停止边界

- 长期 baseline 必须先获得明确的下载范围/预算，完成月档专用解析和跨月测试；现有单日解析器不能直接视作月档支持。
- 1m OHLCV 不能确定盘口价差、队列、冲击或真实可成交价；预测目标收益不等于策略成交收益。
- 最终留出和时间分割先冻结，再训练/比较；若开发评估被用于改设计，必须如实视为开发数据。
- 后续涉及策略时，同时报告净收益、成本、回撤和适用统计检验；Phase 2A 仅做预测流水线，不生成策略盈利结论。
