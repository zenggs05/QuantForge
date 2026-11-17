# Phase 2D-A 执行记录

日期：2026-11-03（Asia/Shanghai）。工作根确认：初始D:\zgs_project，随后所有项目命令明确workdir=D:\zgs_project\QuantForge；用户附件只读。所有写入、测试临时文件、缓存和实验结果均限独立项目目录。

## 提交顺序

1. 起点干净 `83577059d6081ec39233713b07fa46d96cb6c681`；268原测试实跑通过（18.18s）。
2. 冻结协议提交 `84140cec007a3131872eaeb4dbb764a0639da255`，随后才写算法；计划SHA `263368b6153750ba314dcd024878c61214141976b056d30871a6d384ccba7617`。
3. 先写March6M等价测试，再实现splits/runner；全量345通过后提交 `5acaeb0b74e6a2c528e97ad1f53527c4eb4189a5`。
4. 该干净提交正式run `20261103T030302685501Z-d124511e`，唯一复跑 `20261103T030457400531Z-daa632f2`，两次git_dirty=false。
5. 真实结果审计完成后记录本文、报告和研究发现；最后文档提交可由 `git log -3` 定位。

本地作者用既有授权的 `git -c user.name=zenggs05 -c user.email=zenggaoshan05@gmail.com` 指定；未改全局Git配置。无remote、push、PR或交易。

## 文件清单

- 协议：docs/phase2d-a-analysis-plan.md、docs/phase2d-a-plan-manifest.json、configs/rolling-windows.toml；.gitattributes固定这些冻结文件LF。
- 代码：src/quantforge/datasets/window_splits.py、src/quantforge/window_experiment.py、src/quantforge/diagnostics/window_comparison.py、src/quantforge/diagnostics/window_figures.py。
- CLI：scripts/run_window_comparison.py。
- 测试：tests/test_window_splits.py（34）、tests/test_window_experiment.py（23）、tests/test_window_statistics.py（20），共新增77。
- 文档：docs/phase2d-a-analysis-report.md、docs/phase2d-a-record.md、docs/phase2d-a-reproducibility.json、research/rolling-window-findings.md；README/AGENTS更新本阶段进度/边界。
- 小型证据：docs/phase2d-a-evidence/ 内10个真实产物副本，docs/figures/phase2d-a/ 内7张真实PNG；不含原始行情或逐条预测。
- 被忽略的真实产物：results/rolling-window-comparison/ 两个run目录，每个预测Parquet157,014,852字节；数据、.venv、.tmp和缓存均不提交。
- 被忽略的日志：results/phase2d-a-pytest.txt、phase2d-a-formal-console.txt、phase2d-a-reproduction-console.txt、phase2d-a-figure-contact-sheet.png。

## 实际执行命令

先读AGENTS、README、原协议/holdout/Phase2C报告/Adaptive提案以及源码和相关测试。使用 `Get-Location`、`Get-Content -Encoding UTF8`、`rg`、`git status --short`、`git log -1 --format=fuller`、`git remote -v` 核对。

主要命令（均在项目目录执行）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
git add .gitattributes docs/phase2d-a-analysis-plan.md configs/rolling-windows.toml docs/phase2d-a-plan-manifest.json
git -c user.name=zenggs05 -c user.email=zenggaoshan05@gmail.com commit -m 'docs: freeze phase2d-a rolling window protocol'
.\.venv\Scripts\python.exe -m pytest tests/test_window_statistics.py -q --basetemp=.tmp/pytest-window-statistics
.\.venv\Scripts\python.exe -m pytest tests/test_window_splits.py tests/test_window_experiment.py tests/test_window_statistics.py -q --basetemp=.tmp/pytest-phase2d
.\.venv\Scripts\python.exe -m pytest -q 2>&1 | Tee-Object -FilePath results/phase2d-a-pytest.txt
git diff --check
git add scripts/run_window_comparison.py src/quantforge/datasets/window_splits.py src/quantforge/diagnostics/window_comparison.py src/quantforge/diagnostics/window_figures.py src/quantforge/window_experiment.py tests/test_window_experiment.py tests/test_window_splits.py tests/test_window_statistics.py
git diff --cached --check
git -c user.name=zenggs05 -c user.email=zenggaoshan05@gmail.com commit -m 'feat: implement audited rolling ridge window comparison'
.\.venv\Scripts\python.exe scripts\run_window_comparison.py --config configs\rolling-windows.toml 2>&1 | Tee-Object -FilePath results/phase2d-a-formal-console.txt
.\.venv\Scripts\python.exe scripts\run_window_comparison.py --config configs\rolling-windows.toml --reproduction 2>&1 | Tee-Object -FilePath results/phase2d-a-reproduction-console.txt
```

此外用项目Python运行只读JSON/SHA256审计与两份预测的 `pandas.testing.assert_frame_equal(check_exact=True)`；18份确定性产物逐字节hash比对、48模型状态/指标比对；复制小型证据并由 `.tmp/build_phase2d_docs.py` 生成文档表格。图表总览和主图已视觉核查。命令列表不把代码审查/合成测试假称真实实验。

## 测试与故障记录

- 初始268通过；新增窗口与原split46通过；runner23通过；最终统计20通过；正式前全量 **345 passed in 50.04s**。原有测试文件未更改。
- 首先写March等价测试时新split模块尚不存在，2个测试预期失败；实现后全部通过，未改科学契约。
- 新统计适配器最初出现5个失败，原因是pandas中month同时为索引level与列造成groupby歧义；改为显式按level分组，全部通过。
- 审查发现共同越过月末截止的非法预测应被拒绝，增加成熟截止/规范fit-as_of校验及3测试；没有放宽门禁。
- 运行器最初引用计划草稿hash，正式实验前更正为已提交最终文件hash；协议未修改。配置失败在固定独立目录产生failure.json，成功summary始终最后写入。
- 首次复现审计误将source.elapsed_seconds也要求相等，因此断言失败；差异仅为数据校验耗时2.8585513s与2.6003414s，属于协议允许的运行时间差。排除这一明确计时字段后，其余来源/配置/provenance、模型、指标和全部18份确定性文件均精确一致，未改模型或重跑。
- 正式/复跑均一次成功、没有failure.json、没有遗留partial文件；实际32+32次Ridge分开计数。合成测试和8次独立人工信号复算不计入真实预算。
- 原始附件首次用PowerShell默认编码显示乱码，重读显式UTF8确认；项目新文件UTF8正常。

覆盖包括自然月/闰年、成熟标签、未来排除、warm-up/缺口、月初1秒、共享评估索引、训练期scaler、空窗口、留出路径读取前拒绝、确定性、常数/线性/已知断点、配对符号、全块长/lag与原核等价、Holm缺失计数、故障/缺失产物及冻结文件保护。图表测试使用人工标记数据；实际图表来自正式市场预测。

## 版本、预算与数据保护

Python 3.13.5；依赖版本：`{"matplotlib": "3.11.2", "numpy": "2.5.4", "pandas": "3.0.6", "pyarrow": "23.0.1", "pytest": "9.2.0", "quantforge": "0.1.0", "scikit-learn": "1.9.1", "scipy": "1.18.1", "threadpoolctl": "3.7.0"}`。既有.venv直接使用；本轮没有依赖安装或锁文件修改。数值线程池OpenBLAS×2、OpenMP×1均记录num_threads=1。

正式Ridge32fit、6,090,270训练行访问、拟合3.542204s；辅助16fit、5,228,944训练行访问；总运行60.771809s。复跑Ridge32fit、同样训练量、拟合3.364868s、总58.458400s。计时不作精确速度结论，未测峰值RSS。输入整体/月度manifest、数据hash、源码hash、模型状态和所有输出hash见summary与复现文档。

未下载任何新行情、未访问July/August文件/HEAD/CHECKSUM/API、未回填June标签、未扩大候选或调参。旧Phase2B/2C受追踪源码、计划、配置、测试、证据未改；旧冻结预测和summary运行前后hash不变。图表/CSV是派生小型研究证据，不是逐条市场数据。

验收无未完成事项或环境阻碍；研究上没有确认rolling优势。C/B仅为后续建议，本轮未实现。复现需要本地已有冻结输入；Git不分发这些数据。下一阶段应另立协议并获得用户任务授权。
