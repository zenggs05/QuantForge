from pathlib import Path
import json
import shutil
import hashlib
import pandas as pd

root = Path.cwd()
formal = root / 'results/rolling-window-comparison/20261103T030302685501Z-d124511e'
repeat = root / 'results/rolling-window-comparison/20261103T030457400531Z-daa632f2'
s = json.loads((formal / 'summary.json').read_text())
t = json.loads((repeat / 'summary.json').read_text())
m = pd.read_csv(formal / 'metrics.csv')
b = pd.read_csv(formal / 'bootstrap-results.csv')
h = pd.read_csv(formal / 'hac-results.csv')
timings = pd.read_csv(formal / 'model-timings.csv')
ridge = m[m.model.isin(['expanding','rolling_1m','rolling_3m','rolling_6m'])]

evidence = root / 'docs/phase2d-a-evidence'
figures = root / 'docs/figures/phase2d-a'
evidence.mkdir(parents=True, exist_ok=True)
figures.mkdir(parents=True, exist_ok=True)
copies = ['metrics.csv','model-timings.csv','model-states.json','window-audit.json','paired-losses.csv',
          'bootstrap-results.csv','hac-results.csv','pairing-audit.json','resolved-config.json','summary.json']
for name in copies:
    shutil.copyfile(formal / name, evidence / name)
for p in (formal / 'figures').glob('*.png'):
    shutil.copyfile(p, figures / p.name)

def table(frame, columns):
    def fmt(v):
        if pd.isna(v): return 'null'
        if isinstance(v, float): return f'{v:.9g}'
        return str(v)
    return '\n'.join(['| ' + ' | '.join(columns) + ' |', '| ' + ' | '.join(['---']*len(columns))+' |']+
                     ['| '+' | '.join(fmt(v) for v in row)+' |' for row in frame[columns].itertuples(index=False,name=None)])

counts = ridge.pivot(index=['month','horizon_minutes'], columns='model',values='train_samples').reset_index()
main_b = b[(b.horizon_minutes==15)&(b.scope_type=='role')&(b.aggregation=='n_weighted')&(b.block_length==1440)]
main_h = h[(h.horizon_minutes==15)&(h.scope_type=='role')&(h.aggregation=='n_weighted')&(h.lag==60)]
role_b = b[(b.scope_type=='role')&(b.aggregation=='n_weighted')&(b.block_length==1440)]
budget = pd.DataFrame([{'model':k,**v} for k,v in s['budget']['by_program'].items()])
report = f'''# Phase 2D-A：固定窗口与扩张窗口的探索性预测实验

核查日期：2026-11-03（Asia/Shanghai）。**没有发现 Rolling 优于 Expanding 的稳健平方误差证据。主 15m/3M Validation 对照 MSE 反而高 0.125869%，95% MBB 相对差区间为 [−0.086806%, +0.351043%]，HAC p=0.272831、Holm6 p=1。**这既不证明严格等效，也不证明市场没有概念漂移。

本研究重复使用 Phase 2B/2C 已见的四个月。协议在本轮开发前冻结，但结果仍是探索性开发证据，**不是新的确认性 OOS 证据**；May/June 从未重新命名为独立测试集。无需显著性即可验收工程，全部负结果保留。

## 冻结协议与完整设置

- 协议提交：`84140cec007a3131872eaeb4dbb764a0639da255`，先于算法实现。
- 实现提交：`5acaeb0b74e6a2c528e97ad1f53527c4eb4189a5`；正式与复跑均 `git_dirty=false`。
- 协议 SHA-256：`{s['plan_sha256']}`；配置 SHA-256：`{s['config_sha256']}`。
- 正式 run：`{s['run_id']}`；唯一同配置复跑：`{t['run_id']}`。
- 仅用既有 Binance Spot BTCUSDT 1m、2025-09 至 2026-06 的 436,320 根真实 bar。14 个原有因果特征，1 秒可用延迟，15m 主标签、5m 辅标签。
- StandardScaler 仅拟合本窗口合法训练行；Ridge alpha=1、solver=svd、seed42，三个数值线程池实际均为 1。无新依赖或全局环境变更。
- 四种 Ridge × 四个月 × 两个 horizon = **32 次正式 Ridge fit**；另有16次 expanding Zero/Mean参考拟合。复跑32次单独计数，不是候选搜索。

完整冻结约定见 [分析计划](phase2d-a-analysis-plan.md)、[计划清单](phase2d-a-plan-manifest.json) 和 [配置副本](phase2d-a-evidence/resolved-config.json)。

## 四种训练程序与时间边界

Expanding 从2025-09-01起积累；Rolling 1M/3M/6M 分别从 `fit_time - 1/3/6 calendar months` 开始。fit_time 固定为预测月初UTC00:00:00，窗口按 **decision_time** 归属，不能用bar_open_time或文件月份替代。

|预测月|1M起点|3M起点|6M起点|排他训练上限|
|---|---|---|---|---|
|2026-03|2026-02-01|2025-12-01|2025-09-01|2026-03-01|
|2026-04|2026-03-01|2026-01-01|2025-10-01|2026-04-01|
|2026-05|2026-04-01|2026-02-01|2025-11-01|2026-05-01|
|2026-06|2026-05-01|2026-03-01|2025-12-01|2026-06-01|

候选满足 start ≤ decision_time < fit_time，随后要求特征和标签已可用、特征与标签有效、目标不越界、不重叠、连续性合法。Rolling始终是原Expanding合法训练索引子集，原始排序保留。因果特征使用全部已观察历史计算，不在rolling起点凭空重置warm-up。例如Jan31最后一分钟bar的decision_time在Feb1，因此能属于February训练窗口；Feb末最后一分钟bar不能进入March月初拟合。

所有模型共用完整相同的评估键、标签与时点。评估标签必须在下一月初成熟；June不读取July补齐标签。预测2,107,680行对应 **351,280个配对horizon观测**，不是这么多个独立样本。

## 实际样本数与排除审计

{table(counts,['month','horizon_minutes','expanding','rolling_1m','rolling_3m','rolling_6m'])}

每月评估数15m/5m分别为March44,625/44,635、April43,185/43,195、May44,625/44,635、June43,185/43,195。初始60行特征warm-up无效；没有额外缺口或无效值。每个训练窗口排除h个未成熟标签（h=15或5），各原因计数可能重叠，不能直接相加。独立自然月算术与全部32个audit的候选/排除/有效数一致。

初始Expanding/March6M的候选数比完整日历分钟数少1：缺少开发起点前的Aug31末bar；再排除60 warm-up和h个未成熟标签。其他rolling包含合法的前月最后bar，实际训练数为窗口日历分钟数减h。完整计数、索引hash、成熟时间极值见 [window-audit.json](phase2d-a-evidence/window-audit.json)。

## 全部月度指标

MSE为平方对数收益单位，MAE为对数收益单位。`mse_improvement_percent_expanding` = 100×(Expanding MSE−模型MSE)/Expanding MSE，**正为改善、负为退化，不是收益率**。这里IC为月内时间序列Pearson相关。Zero/Mean及Spearman、偏差、尺度、校准全部列在 [metrics.csv](phase2d-a-evidence/metrics.csv)，没有删掉不利月份。

{table(ridge,['month','horizon_minutes','model','mse','mae','pearson_ic','mse_improvement_percent_expanding','mse_improvement_percent_zero'])}

1M与3M在15m四个月全部更差；6M在March精确相同、April/May更差、June改善0.053388%。5m的3M只在April微弱改善0.003020%，其余更差；6M在May/June有小幅改善，但角色区间跨0。不能根据5m或单月改写15m主结论。

![15m MSE](figures/phase2d-a/mse-15m.png)
![5m MSE](figures/phase2d-a/mse-5m.png)
![15m IC](figures/phase2d-a/ic-15m.png)
![5m IC](figures/phase2d-a/ic-5m.png)

## 预测尺度与校准

{table(ridge,['month','horizon_minutes','model','spearman_correlation','prediction_bias','prediction_std','target_std','std_ratio','diagnostic_slope'])}

均为描述性诊断；std用ddof=0，bias=mean(pred−target)。校准斜率没有用于重估/放大/回填预测。正IC或较大预测尺度不自动意味着较低MSE。固定alpha也不等于不同训练n下相同的单位样本正则化；训练n、scaler和估计方差共同变化，因此不能把窗口差异全部归因于概念漂移。

## 主要Bootstrap结论及敏感性

定义d=Rolling平方损失−Expanding平方损失，负值才有利于Rolling。固定B=2000、seed42、L=60/360/**1440**/2880、非循环固定块95% percentile区间；同一抽样索引同步重采样配对差与相对分母，不跨月、角色或缺口。角色分别按观测数加权与月份等权；不平均月度百分比。

15m六个预先声明的主角色对照：

{table(main_b,['window','role','estimate_mse_delta','ci_low','ci_high','relative_mse_delta_pct','relative_ci_low_pct','relative_ci_high_pct'])}

15m主3M Validation在所有块长下均跨0：

{table(b[b.primary_contrast],['block_length','estimate_mse_delta','ci_low','ci_high','relative_ci_low_pct','relative_ci_high_pct'])}

5m辅助角色结果也完整保留：

{table(role_b[role_b.horizon_minutes==5],['window','role','relative_mse_delta_pct','relative_ci_low_pct','relative_ci_high_pct'])}

完整192行均可估计；其中42个绝对区间排除0，**方向全部是Rolling更差**，没有有利于Rolling的排零区间。1M Development在主L1440的15m与5m都有退化的边际区间，但不是经多重比较校正后的同时区间。March6M同模型零差的区间为[0,0]。

完整月份/角色、两种加权及四种块长见 [bootstrap-results.csv](phase2d-a-evidence/bootstrap-results.csv)，完整配对描述表见 [paired-losses.csv](phase2d-a-evidence/paired-losses.csv)。L2880每月n/L约15，触发少量块提示；非循环边缘与Monte Carlo误差保留。

![15m主要区间](figures/phase2d-a/bootstrap-main-15m.png)
![5m主要区间](figures/phase2d-a/bootstrap-main-5m.png)

## HAC与Holm

复用原Bartlett/Newey–West核，lags15/**60**/240，固定n自协方差分母，无新小样本修正，正态双侧p/95%CI。主家族为15m×3候选×2角色、观测加权、lag60共6项；另对全部144项做敏感性Holm。下表为完整主家族：

{table(main_h,['window','role','estimate_mse_delta','ci_low','ci_high','p_value','holm_primary6_p','holm_global144_p'])}

主3M Validation的lag敏感性：

{table(h[h.primary_contrast],['lag','ci_low','ci_high','p_value','holm_primary6_p','holm_global144_p'])}

主家族**0/6拒绝**；全家族**0/144拒绝**。1M Development名义p=0.020301，Holm6=0.121809，不能称多重校正后显著退化。144行中6个March6M/月度/horizon/lag条目因精确零差而p=null；它们仍计入完整144家族，null不是p=1。26个HAC边际CI排除0，均指向Rolling更差。完整全部原始与调整p及CI见 [hac-results.csv](phase2d-a-evidence/hac-results.csv)。

## 计算成本与资源

{table(budget,['model','fits','training_row_visits','fit_seconds','predict_seconds'])}

正式Ridge训练行访问总量 **6,090,270**，Ridge拟合合计 **{s['budget']['ridge_fit_seconds']:.6f}s**；加辅助模型全部拟合{s['budget']['all_fit_seconds']:.6f}s，全部预测{s['budget']['all_predict_seconds']:.6f}s。辅助模型另访问5,228,944训练行；全部模型合计11,319,214行访问。这是fit接收行数量账，不是精确CPU指令/内存流量计量。

正式完整流水线{s['elapsed_seconds']:.3f}s，复跑{t['elapsed_seconds']:.3f}s；复跑Ridge拟合{t['budget']['ridge_fit_seconds']:.6f}s。数据校验、序列化、统计和绘图也计入总耗时，不能仅把fit秒数当全实验成本。计时受缓存和系统负载影响；未测多次分布，不能宣称稳健加速倍数。Rolling1M/3M/6M训练行量为Expanding的13.22%/39.76%/79.97%，相同拟合次数不等计算量。

内存审计仅记录bars deep bytes={s['memory_bytes']['bars_deep']}、features deep bytes={s['memory_bytes']['features_deep']}，**不是进程峰值内存**。没有使用GPU或安装CUDA。逐模型实际耗时见 [model-timings.csv](phase2d-a-evidence/model-timings.csv)。

![训练规模](figures/phase2d-a/training-samples.png)

## 等价性、合成实验与复现

March6M的两个horizon与Expanding在训练索引、标签、特征顺序、scaler均值/方差/尺度、Ridge系数/截距及预测上完全相同。所有四月/两个horizon的Expanding、Zero、Mean均精确重现冻结参考，预测源hash保持 `66f74e1367ed816455d8df2af5c8fdd89009c893aa5fe064df29fe646c86407a`。未发现时间泄漏、评估样本不一致或数值异常。

**Synthetic独立验证，不是市场结果**：固定seed42，人工小时网格、一维高斯特征，March评价744点。训练行Exp/1M/3M/6M为4344/672/2160/4344；原时间门禁仍应用。无噪声y=x时MSE为5.28310e−8 / 2.20165e−6 / 2.14750e−7 / 5.28310e−8，更多样本减弱固定alpha相对收缩；Feb1关系从+1变−1时为2.87191 / 2.20165e−6 / 1.91782 / 2.87191。已知关系突变时短窗可以改善，无漂移时无需预设其更好。此人工场景不复刻重尾/波动聚集真实市场，也不能反推真实数据有无漂移。

原268项测试全部保留，新77项，共 **345 passed in 50.04s，0 failed**。18个确定性产物（全部预测、指标、模型状态、审计/配置、报告、7张PNG）逐字节一致；全部2,107,680行×全部列精确一致。计时与run身份单独比较，不要求相等。见 [复现审计](phase2d-a-reproducibility.json)。仅证明当前环境内复现，未证明跨平台位级复现。

## 研究判断、限制与停止

主3M改善假设未获支持：15m四个月均退化，角色点估计同方向，但统计区间跨0，不能进一步断言系统性退化。6M的小幅后期改善仍不足以证明自适应优势或已解决漂移；不扩展窗口、不调alpha、不更换主指标追求显著性。

后续优先 **C的最小正确性子任务**：严格成熟反馈队列、固定特征坐标的无遗忘增量线性充分统计量、与同目标batch解等价性和资源账。暂不把它称为新的alpha方法，不默认添加遗忘或更频繁更新。B漂移触发需要先在相关、重尾、波动聚集合成负对照上控制误报并定义匹配预算；当前短窗负结果与极低Ridge拟合成本削弱了直接投入检测器调参的理由。完整依据与停止条件见 [研究发现](../research/rolling-window-findings.md)。B/C本轮均未实现。

主要限制：仅四个相邻已见月份，每角色两月；共享历史/持续状态使跨月协方差为0的近似可能失效；重叠标签、重尾、非平稳影响渐近推断；长块有效样本少，边际CI非同时CI；固定alpha、训练n和scaler混杂；统计与合成诊断不证明可交易性或跨市场状态泛化；逻辑月初就绪不是真实延迟模型；未计交易费、收益、Sharpe、回撤，因为本轮没有执行策略；精确复现需本地冻结数据/预测（Git不分发）。

本阶段验收无未完成项或环境阻碍。未下载新行情，未访问July/August留出（含HEAD/CHECKSUM/API），未改冻结Phase2B/2C文件，未创建remote/PR、push或真实/模拟订单。实际命令、故障和文件清单见 [执行记录](phase2d-a-record.md)。
'''
(root/'docs/phase2d-a-analysis-report.md').write_text(report,encoding='utf-8')

record = f'''# Phase 2D-A 执行记录

日期：2026-11-03（Asia/Shanghai）。工作根确认：初始D:\\zgs_project，随后所有项目命令明确workdir=D:\\zgs_project\\QuantForge；用户附件只读。所有写入、测试临时文件、缓存和实验结果均限独立项目目录。

## 提交顺序

1. 起点干净 `83577059d6081ec39233713b07fa46d96cb6c681`；268原测试实跑通过（18.18s）。
2. 冻结协议提交 `84140cec007a3131872eaeb4dbb764a0639da255`，随后才写算法；计划SHA `{s['plan_sha256']}`。
3. 先写March6M等价测试，再实现splits/runner；全量345通过后提交 `5acaeb0b74e6a2c528e97ad1f53527c4eb4189a5`。
4. 该干净提交正式run `{s['run_id']}`，唯一复跑 `{t['run_id']}`，两次git_dirty=false。
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
.\\.venv\\Scripts\\python.exe -m pytest -q
git add .gitattributes docs/phase2d-a-analysis-plan.md configs/rolling-windows.toml docs/phase2d-a-plan-manifest.json
git -c user.name=zenggs05 -c user.email=zenggaoshan05@gmail.com commit -m 'docs: freeze phase2d-a rolling window protocol'
.\\.venv\\Scripts\\python.exe -m pytest tests/test_window_statistics.py -q --basetemp=.tmp/pytest-window-statistics
.\\.venv\\Scripts\\python.exe -m pytest tests/test_window_splits.py tests/test_window_experiment.py tests/test_window_statistics.py -q --basetemp=.tmp/pytest-phase2d
.\\.venv\\Scripts\\python.exe -m pytest -q 2>&1 | Tee-Object -FilePath results/phase2d-a-pytest.txt
git diff --check
git add scripts/run_window_comparison.py src/quantforge/datasets/window_splits.py src/quantforge/diagnostics/window_comparison.py src/quantforge/diagnostics/window_figures.py src/quantforge/window_experiment.py tests/test_window_experiment.py tests/test_window_splits.py tests/test_window_statistics.py
git diff --cached --check
git -c user.name=zenggs05 -c user.email=zenggaoshan05@gmail.com commit -m 'feat: implement audited rolling ridge window comparison'
.\\.venv\\Scripts\\python.exe scripts\\run_window_comparison.py --config configs\\rolling-windows.toml 2>&1 | Tee-Object -FilePath results/phase2d-a-formal-console.txt
.\\.venv\\Scripts\\python.exe scripts\\run_window_comparison.py --config configs\\rolling-windows.toml --reproduction 2>&1 | Tee-Object -FilePath results/phase2d-a-reproduction-console.txt
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

Python {s['provenance']['python']}；依赖版本：`{json.dumps(s['provenance']['dependency_versions'],sort_keys=True)}`。既有.venv直接使用；本轮没有依赖安装或锁文件修改。数值线程池OpenBLAS×2、OpenMP×1均记录num_threads=1。

正式Ridge32fit、6,090,270训练行访问、拟合3.542204s；辅助16fit、5,228,944训练行访问；总运行60.771809s。复跑Ridge32fit、同样训练量、拟合3.364868s、总58.458400s。计时不作精确速度结论，未测峰值RSS。输入整体/月度manifest、数据hash、源码hash、模型状态和所有输出hash见summary与复现文档。

未下载任何新行情、未访问July/August文件/HEAD/CHECKSUM/API、未回填June标签、未扩大候选或调参。旧Phase2B/2C受追踪源码、计划、配置、测试、证据未改；旧冻结预测和summary运行前后hash不变。图表/CSV是派生小型研究证据，不是逐条市场数据。

验收无未完成事项或环境阻碍；研究上没有确认rolling优势。C/B仅为后续建议，本轮未实现。复现需要本地已有冻结输入；Git不分发这些数据。下一阶段应另立协议并获得用户任务授权。
'''
(root/'docs/phase2d-a-record.md').write_text(record,encoding='utf-8')
print('Wrote report, record, 10 evidence copies and 7 figures.')
