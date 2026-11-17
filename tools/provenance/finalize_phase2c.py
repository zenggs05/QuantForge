"""Compile public Phase 2C evidence from the two fixed real diagnostic runs."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd

root=Path(__file__).resolve().parents[1]
ids=('20261103T012342527623Z-d1bab75b','20261103T012551082603Z-c847d61f')
runs=[root/'results/statistical-diagnostics'/x for x in ids]
summaries=[json.loads((p/'summary.json').read_text(encoding='utf-8')) for p in runs]

def sha(path):
    with path.open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()

def write_json(path,obj):
    path.write_text(json.dumps(obj,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')

for path,s in zip(runs,summaries,strict=True):
    assert s['status']=='completed_statistical_diagnostics'
    assert not (path/'failure.json').exists()
    for name,digest in s['artifacts_sha256'].items(): assert sha(path/name)==digest,name
    for name,digest in s['provenance']['source_sha256'].items(): assert sha(root/name)==digest, name
assert summaries[0]['config']==summaries[1]['config']
assert summaries[0]['input_audit']==summaries[1]['input_audit']
deterministic=[name for name in summaries[0]['artifacts_sha256'] if name!='report.md']
for name in deterministic:
    assert sha(runs[0]/name)==sha(runs[1]/name),name
assert sha(root/summaries[0]['input_audit']['predictions_path'])=='66f74e1367ed816455d8df2af5c8fdd89009c893aa5fe064df29fe646c86407a'
evidence=root/'docs/statistical-evidence'; evidence.mkdir(exist_ok=True)
figures=root/'docs/figures/phase2c'; figures.mkdir(parents=True,exist_ok=True)
for name in deterministic:
    if name.startswith('figures/'):
        shutil.copyfile(runs[0]/name,figures/Path(name).name)
    else: shutil.copyfile(runs[0]/name,evidence/name)
shutil.copyfile(runs[0]/'summary.json',root/'docs/statistical-diagnostics-summary.json')
repro={'status':'passed','checked_at_utc':datetime.now(timezone.utc).isoformat(),'run_ids':list(ids),
       'same_frozen_input':True,'same_config':True,'same_input_audit':True,
       'byte_identical_artifacts':deterministic,'count':len(deterministic),
       'all_artifact_hashes_verified':True,'all_runtime_source_hashes_match_current_files':True,
       'frozen_prediction_unchanged':True,'summary_sha256':[sha(p/'summary.json') for p in runs],
       'note':'Seven tables, pairing audit, config and fourteen PNGs are bit-identical. Report/summary include run ID and timings.'}
write_json(root/'docs/phase2c-reproducibility.json',repro)
b=pd.read_csv(runs[0]/'bootstrap-results.csv'); h=pd.read_csv(runs[0]/'hac-results.csv')
c=pd.read_csv(runs[0]/'calibration-summary.csv'); d=pd.read_csv(runs[0]/'distribution-quantiles.csv')
p=pd.read_csv(runs[0]/'paired-loss-summary.csv'); acf=pd.read_csv(runs[0]/'loss-autocorrelation.csv')
assert len(b)==128 and len(h)==96 and len(c)==24
assert ((b.ci_low<=0)&(b.ci_high>=0)).all()
assert ((b.relative_ci_low<=0)&(b.relative_ci_high>=0)).all()
assert ((h.ci_low<=0)&(h.ci_high>=0)).all()
assert h.holm_global96_p.eq(1).all() and h[h.lag==60].holm_main32_p.eq(1).all()

def table(df,columns):
    lines=['| '+' | '.join(columns)+' |','|'+'|'.join(['---']*len(columns))+'|']
    for values in df.loc[:,columns].itertuples(index=False,name=None):
        rendered=['null' if pd.isna(x) else (f'{x:.8g}' if isinstance(x,float) else str(x)) for x in values]
        lines.append('| '+' | '.join(rendered)+' |')
    return '\n'.join(lines)

lines=['# Phase 2C：量化预测统计可靠性与校准诊断','',
 '核查日期：2026-11-03（Asia/Shanghai）。**未发现 Ridge 相对 Zero 或 Historical Mean 的可靠平方误差优势证据。128 组 Bootstrap 的绝对/相对95%区间均含0；96个 HAC 原始 p-value 均大于0.20，Holm校正均为1。**这不等于证明模型等效、没有alpha或没有交易价值。','',
 '## 研究边界与证据','',
 'Phase 2B 四个月表现已经观察过，本计划是后续诊断，不能当作首次观察前的预注册。从干净 `a431190` 重跑原始 Baseline，模型配置/状态、全部预测及非耗时指标一致，`git_dirty=false`；随后先写并哈希冻结计划，再实现统计代码。', '',
 f"正式 run `{ids[0]}`；同配置复跑 `{ids[1]}`。7张统计表、配对审计、配置与14张PNG，共 **23个产物逐字节一致**。见 [复现证据](phase2c-reproducibility.json) 和 [完整summary](statistical-diagnostics-summary.json)。", '',
 '固定预测源为 clean run `20261103T010111153259Z-a2cb0858`，SHA-256 `66f74e1367ed816455d8df2af5c8fdd89009c893aa5fe064df29fe646c86407a`。1,053,840 条模型记录一对一配对成351,280个 horizon 观测，未丢行；模型版本、真值、来源及成熟截止一致。三模型和两个horizon的重叠不能变成独立样本数。', '',
 '计划SHA-256 `1dcb6c79d947a3c13e478efb367a1d77e8e3d671698fc0b6fb2c1864daac40d3`；基点 `a431190283c7c508751d4175a45ec84bff5e4bcf`。诊断在该提交上的新增dirty代码运行，逐源码/配置/锁文件哈希已记录。最终本地提交在验收后创建，不伪称旧提交单独包含诊断。', '',
 '只有clean baseline复跑读取既有开发bar；之后诊断只读冻结预测与JSON元数据。没有新行情下载、最终留出行情访问、模型修改、调参、校准器回填或模拟/实盘订单。', '',
 '## 固定统计契约','',
 '`d = loss_Ridge − loss_baseline`，负值有利于Ridge。正式推断使用平方损失；绝对损失只描述。相对MSE差是同权重平均差/对照MSE，bootstrap分母随同重采样，百分比不是交易收益。主任务15m、主对照Zero；5m和Mean为辅助；March/April Validation与May/June Development Evaluation始终分开。', '',
 'Bootstrap固定seed42、B2000、非循环moving blocks L60/360/**1440**/2880、percentile95%区间。合法起点在连续片段内，最后块按原n截断，不跨月/角色/缺口；角色内按观测加权与月份等权分别组合。HAC采用Bartlett核、lags15/**60**/240、固定n自协方差分母、正态渐近z/p，无小样本修正。角色合并假设跨月协方差为0，不把月份拼接成一条平稳序列。', '',
 '主lag60的32项构成一个Holm家族；另全部96项构成敏感性家族。校正p值不能修复后验方案选择或错误渐近假设；CI为边际95%而非同时区间。', '',
 '## 主Bootstrap：角色内观测加权','',
 table(b[(b.block_length==1440)&(b.aggregation=='n_weighted')],['scope_id','horizon_minutes','baseline','estimate_mse_delta','ci_low','ci_high','relative_mse_delta_pct','relative_ci_low_pct','relative_ci_high_pct']), '',
 '## 主Bootstrap：全部月份与两种汇总','',
 table(b[b.block_length==1440],['scope_id','aggregation','horizon_minutes','baseline','estimate_mse_delta','ci_low','ci_high','relative_ci_low_pct','relative_ci_high_pct']), '',
 '四种块长的128行绝对/相对区间都含0，完整见 [bootstrap-results.csv](statistical-evidence/bootstrap-results.csv)。不能选更窄区间的块长来作结论。真实每月只有一个连续片段；L2880的n/L约14.99–15.50（抽15或16块），都触发少量块提示；这不代表15个独立市场状态。非循环MBB有有限样本边缘偏差，B2000有分位Monte Carlo误差。没有零分母draw被删除。', '',
 '![15m Bootstrap](figures/phase2c/bootstrap-ci-15m.png)', '',
 '![5m Bootstrap](figures/phase2c/bootstrap-ci-5m.png)', '',
 '## HAC主结果及完整敏感性','',
 table(h[h.lag==60],['scope_id','aggregation','horizon_minutes','baseline','estimate_mse_delta','se','ci_low','ci_high','z','p_value','holm_main32_p','holm_global96_p']), '',
 table(h.groupby('lag',as_index=False).agg(min_raw_p=('p_value','min'),max_raw_p=('p_value','max'),tests=('p_value','size')),['lag','min_raw_p','max_raw_p','tests']), '',
 '最小原始p为0.20119299199，来自Validation、月份等权、15m、Ridge−Mean、lag15；主32项和全96项的Holm结果均为1，96个HAC区间全部含0。所有结果均可估计，未删短段/退化检验。完整 [hac-results.csv](statistical-evidence/hac-results.csv)。', '',
 'HAC需弱依赖、适当有限矩及渐近近似；重叠标签、重尾与非平稳可能破坏假设。角色内仅两个月，共享扩张训练窗和市场延续性可能产生未建模的跨月协方差。没有通过“聚合后样本很多”声称多年/跨状态泛化。', '',
 '## 月度IC、偏差与尺度','']
ridge=c[c.model=='ridge'].copy()
for key in ('prediction_mean','target_mean','prediction_std','target_std','prediction_bias'):
    ridge[key+'_bp']=ridge[key]*10000
ridge['std_ratio_pct']=ridge.std_ratio*100
lines += [table(ridge,['month','horizon_minutes','pearson_ic','spearman_correlation','prediction_mean_bp','target_mean_bp',
    'prediction_std_bp','target_std_bp','std_ratio_pct','prediction_bias_bp','diagnostic_slope']), '',
 '均值、std和bias上表以log-return basis points（×10,000）显示；std用ddof0。15m预测std仅目标的3.16%–4.18%，5m为2.63%–3.20%。这本身不能证明预测应放大：低可预测性下收缩可能合理。正IC也只表明共同变化方向，不约束预测幅度/均值。', '',
 '`MSE = bias² + Var(pred) + Var(target) − 2Cov(pred,target)`；相对Zero：`ΔMSE=E[pred²]−2E[pred×target]`。这些是逐月全样本验证的恒等式。', '',
 table(ridge,['month','horizon_minutes','bias_squared','prediction_variance','target_variance','covariance',
    'prediction_second_moment','twice_prediction_target_moment','mse_delta_zero_from_moments']), '',
 '15m的IC约0.019–0.041，弱协方差贡献与预测方差/偏差部分抵消，所以误差改善很小。5m三月到五月的诊断斜率约0.37–0.43，协方差带来的收益不足以覆盖尺度/均值项，正IC仍伴随更高MSE；六月相对Zero略好。这里的误差分解描述当前数据，不是因果根因证明。', '',
 '诊断OLS斜率（Cov/Var）和截距只描述评价集，没有用于重新预测/计分。15m斜率从0.538到1.211、5m从0.372到0.578，不应从已见月份选择校准系数后宣布改进。', '',
 '![15m预测/目标分布](figures/phase2c/prediction-target-15m.png)', '',
 '![15m分位校准](figures/phase2c/quantile-calibration-15m.png)', '',
 '![5m分位校准](figures/phase2c/quantile-calibration-5m.png)', '',
 '## 残差、平方损失分布与相关性','',
 '残差定义pred−target。以下Ridge分位数来自全样本，不裁剪；平方损失为自然log-return²单位。', '']
res=d[(d.model=='ridge') & (d.quantity.isin(['residual','squared_error'])) & (d['quantile'].isin([.01,.5,.99,1.]))]
wide=res.pivot(index=['month','horizon_minutes','quantity'],columns='quantile',values='value').reset_index()
wide.columns=[str(x) for x in wide.columns]
lines += [table(wide,['month','horizon_minutes','quantity','0.01','0.5','0.99','1.0']), '',
 '全部模型/全部固定分位见 [distribution-quantiles.csv](statistical-evidence/distribution-quantiles.csv)；分位校准桶边界、实际n和均值见 [calibration-quantiles.csv](statistical-evidence/calibration-quantiles.csv)，ties不拆分。直方图仅为可读性显示合并0.5%–99.5%范围，各图标注范围外比例；统计仍含全部尾部。', '',
 '![15m残差](figures/phase2c/residual-distribution-15m.png)', '',
 '![15m损失差ACF](figures/phase2c/loss-acf-15m.png)', '',
 '损失差在短lag有明显相关，不能把分钟当IID；ACF逐月计算、缺口不连乘，不给未经修正的IID显著性带。完整 [loss-autocorrelation.csv](statistical-evidence/loss-autocorrelation.csv)。', '',
 '## 全部真实图表','',
 '| 图类型 | 15m | 5m |','|---|---|---|']
for stem,label in [('prediction-target','预测与目标分布'),('quantile-calibration','预测分位校准'),('residual-distribution','残差分布'),('monthly-ic','月度IC'),('monthly-mse-difference','月度MSE差'),('bootstrap-ci','Bootstrap区间'),('loss-acf','损失差自相关')]:
    lines.append(f'| {label} | [PNG](figures/phase2c/{stem}-15m.png) | [PNG](figures/phase2c/{stem}-5m.png) |')
lines += ['', '14张正式PNG全部来自冻结真实预测，已逐页/缩略总览检查。合成测试图占位仅留在pytest临时目录，不作为正式图。', '',
 '## 证据强度与下一步','',
 '本次没有样本内训练性能评价。月度验证/Development Evaluation是已见开发结果；统计显著性没有建立，跨市场状态泛化也未验证。交易收益还需要合法数据、执行价格、持仓账本、成本/风险约束，不能由IC/MSE直接推出。本次也没有证明“效果恰好为零”或“adaptive必定无效”。', '',
 '当前证据足以支持继续做小预算、可证伪的算法比较，不能支持“自适应一定改善alpha”。优先A：固定扩张Ridge对预先指定固定滚动Ridge，保持14特征/成熟标签/alpha和训练预算；先隔离窗口选择的作用，再考虑漂移触发与延迟反馈在线更新。详见 [adaptive-alpha-proposal.md](../research/adaptive-alpha-proposal.md)。May/June后续结果必须标探索；July/August仍封存。', '',
 f"首轮耗时{summaries[0]['elapsed_seconds']:.3f}秒（配对{summaries[0]['timings_seconds']['pairing']:.3f}、Bootstrap{summaries[0]['timings_seconds']['bootstrap']:.3f}、HAC{summaries[0]['timings_seconds']['hac']:.3f}、校准/ACF{summaries[0]['timings_seconds']['calibration_acf']:.3f}、绘图{summaries[0]['timings_seconds']['figures']:.3f}）；测试与依赖、命令和故障记录见 [phase2c-record.md](phase2c-record.md)。", '']
(root/'docs/statistical-diagnostics-report.md').write_text('\n'.join(lines),encoding='utf-8')
print(json.dumps({'reproducibility':repro,'bootstrap_ci_all_include_zero':True,'hac_min_p':h.p_value.min(),'figures':len(list(figures.glob('*.png')))},indent=2))
