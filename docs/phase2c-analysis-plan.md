# Phase 2C：冻结预测的统计可靠性与校准诊断计划

冻结日期：2026-11-03（Asia/Shanghai）。计划基点为 `a431190283c7c508751d4175a45ec84bff5e4bcf`。**Phase 2B 的表现已经被观察过；这是基于既有结果制定的后续诊断计划，不是首次观察结果前的预注册研究。**本文件先于统计实现与正式诊断运行写入；原始字节 SHA-256、Git 基点和冻结时间保存到独立 manifest，运行器必须验证，不允许静默改计划。

## 输入与不变项

已从干净 `a431190` 完成一次原始 Baseline 复跑，run ID 为 `20261103T010111153259Z-a2cb0858`，其 summary SHA-256 为 `7aa9f334a9e5b5f1085edd2362676d86d0898fcdd376f92bb1886efbc3dc6f12`。`git_dirty=false`；1,053,840 条预测全部字段、模型配置、模型状态、fold 和非时间类指标与 Phase 2B 一致。预测 Parquet SHA-256 固定为 `66f74e1367ed816455d8df2af5c8fdd89009c893aa5fe064df29fe646c86407a`。只能按此 run/summary/hash 定位，不能按修改时间选最新结果。

主任务 15m，辅助 5m；主比较 Ridge−Zero，次比较 Ridge−Historical Mean；March/April 2026 为 Validation，May/June 为 Development Evaluation。不合并两个角色成一个确认性结果，不进行样本内训练表现推断。冻结 Phase 2B 预测器、特征、alpha、输入数据、标签及时间语义，不训练/回填校准器，不新增行情请求，不读取 July/August 2026 最终留出，不模拟或实际下单。

输入先验证固定路径、summary/Parquet SHA-256、模型版本与来源 manifest，再检查唯一预测键、三模型一对一完整配对、相同真实标签和时间字段、月份/fold/role、成熟截止与样本数。不得 inner join 静默丢失行。样本按 decision_time 分月；前月 23:59 bar 可以在本月 00:00:01 决策。三模型重复记录不作为三份独立市场观测。

## 配对量与汇总目标

每个时刻计算平方损失和绝对损失，`d_t = loss_ridge,t − loss_baseline,t`，负值表示 Ridge 误差较小。正式推断仅针对配对**平方损失差**；绝对损失差作为完整描述性结果报告，不另设显著性检验。

每个 horizon、比较分别报告四个月；角色内另报告两种固定加权：观测数量权重 `w_m=n_m/Σn_m` 和月份等权 `w_m=1/M`。合并绝对 MSE 差是 `Σw_m mean(d_m)`；相对差是 `Σw_m mean(d_m) / Σw_m mean(baseline_squared_loss_m)`，不是月份相对差的简单均值。分母为零时相对差记 null 并给原因。结果同时提供自然单位与百分比，百分比不是交易收益。

## Moving Block Bootstrap

固定 seed=42、重复 2,000 次、主块长 1,440 个分钟观测、敏感性块长完整列表 `[60,360,1440,2880]`、95% 双侧 percentile 区间（2.5% 与 97.5%，线性分位数）。使用**非循环、固定长度 moving blocks**：在每一连续分钟片段内均匀抽取所有合法起点 `[0,n−L]`，有放回抽取 `ceil(n/L)` 块，拼接后截断至该片段原始 n。最后一块只保留需要的前缀。不能循环连接月尾和月首。

同一个块索引同时抽取 Ridge 和对照平方损失，等价于抽取配对差与对照损失。月份、角色、缺口分别隔离；每个连续片段独立重采样并保持其原始长度及月内观测权重。若任一片段短于 L，该组合明确不可估计，不缩短 L、不填充、不丢弃片段。相对差的 bootstrap 分母必须随同重采样，以保留分子分母相关性。

角色合并在固定月份构成下独立重采样各月，然后依原定权重组合；不拼接月份、不重采样月份本身。因此其区间以所观察到月份为条件，假设不同月份损失均值的跨月协方差可忽略。共享训练窗与市场持续性可能违反该假设，只有两个角色内月份更不能支持跨状态总体推断。

使用稳定、显式的 SeedSequence 子流（seed、horizon、baseline、block length、month），不使用 Python 随机 hash；同一月的 draw 可复用于两种加权，不改变参数选择。可用累积和计算块内总和，数学上必须与显式成对索引采样等价。恒定差给出退化区间并标注，不宣称无限精度的总体推断。

每月报告 `n/L`、实际抽块数、合法起点数、片段数和最短片段。`n/L<20` 标记“少量块”；它只是数量级诊断，不是真正估计的独立样本数。2,880 分钟通常只有约 15 块，尾部区间可能不稳定。展示全部 128 组区间（4 月 + 2 角色 × 2 权重，共 8 scopes；2 horizons × 2 对照 × 4 块长），不依据显著性挑参数，不把 percentile 区间当成经过多重校正的同时置信带。非循环 MBB 的边缘抽样权重不同，存在有限样本边缘偏差；不隐藏敏感性。

## HAC / Newey–West

对每个连续片段的配对平方损失差 `d`，取 `u=d−mean(d)`；`gamma_k = Σ(u_t u_{t−k})/n`（固定 n 分母）。Bartlett 权重 `1−k/(q+1)`；`LRV=gamma_0+2Σ_{k=1}^q weight_k gamma_k`，均值方差为 `LRV/n`，不额外套有限样本修正。主 lag=60，敏感性 `[15,60,240]` 全部报告；片段长度须大于 lag，否则报告不可估计，不能截短 lag。

有缺口时片段独立估计并按 `n_segment/n_month` 权重合并均值与方差；月度之间不计算滞后协方差。角色内 `mean=Σw_m mean_m`，`variance=Σw_m² variance_m`，显式使用零跨月协方差的条件近似。不同月均值不必相同，但该方法不能估计共同市场冲击或共享训练带来的跨月相关。禁止将月尾接到月首后当平稳长序列计算 HAC。

统计量 z=mean/SE；区间 mean±1.95996398454×SE；两侧 p=2×Normal.sf(|z|)。假设各片段内弱平稳/弱依赖、有限适当阶矩与渐近正态近似；HAC 处理相应自相关/异方差，不修复非平稳、极端重尾、后验选择或模型误设。SE=0 时保留点区间、z/p=null 并注明退化（不制造无限统计量和 p=0）。

共 96 个 HAC 比较（8 scopes × 2 horizons × 2 对照 × 3 lags）。主展示为 15m、Ridge−Zero、lag60，但四个月及两个角色都报告。主 lag60 的 32 个结果组成一个 Holm 家族；额外对全部 96 个 lag 敏感性结果一起 Holm 校正。两个校正列都保留，不能改家族筛出有利结果；区间为边际 95%，Holm 校正的是 p-value。原始 p、校正 p、检验数量和未定义项均报告。5m、Mean 对照和等权合并是辅助诊断，不能取代主问题。

## 校准与图表

对每月、horizon、模型报告 n、mean(pred)、mean(target)、population std（ddof=0）、std 比值、bias=mean(pred−target)、MSE/MAE、Pearson/Spearman、残差/平方损失的固定分位数 `[0,.01,.05,.25,.5,.75,.95,.99,1]`。常数相关系数/斜率记 null。残差定义 pred−target；不裁剪误差统计。

给出恒等式 `MSE=bias²+var(pred)+var(target)−2cov(pred,target)`，以及 `MSE−MSE_zero=mean(pred²)−2mean(pred×target)`，用来区分预测尺度、偏置与协方差贡献。描述性 OLS 斜率为 cov(pred,target)/var(pred)，截距为 mean(target)−slope×mean(pred)；仅诊断，不将其用于同一评价集校准后重新计分。

预测十分位桶用月内实际分位边界、相同预测值不任意拆分，ties 合并/常数归为一桶；保留实际 bin 边界、n、平均预测和平均真实收益及全样本加权一致性。不画未经相关性修正的 IID 标准误条。正式图均来自冻结真实预测：预测/目标分布、分位校准、残差分布、月度 IC、月度 MSE 差、bootstrap CI、loss-difference ACF。图至少覆盖两 horizon 和全部月份，角色明确；显示范围可以使用固定分位裁剪，但必须标注被裁掉的尾部比例，统计仍使用全样本。图像不输出任何 PnL/Sharpe。

## 验证、证据与后续研究

原 165 项测试保留；新增明确 synthetic 测试验证配对拒绝行为、损失符号、固定种子复现、合法块起点与边界、不混角色、恒定差/IID/自相关序列、直接手算 HAC 与置信区间、Holm、校准恒等式及 bin 加权均值。正式结果与 synthetic 测试严格区分。冻结配置重复正式运行时要求非耗时结果一致；失败不写成功 summary。

结果保存在 `results/statistical-diagnostics/<run-id>/`；success summary 最后写。记录分析计划哈希、冻结配置、输入/数据/源码哈希、Git SHA/dirty、依赖、seed、UTC 分析时间（用户日期为 Asia/Shanghai）、耗时、失败及限制。小型统计表/审计副本与代表性真实图可纳入 docs，原始预测和中间配对行情不进 Git。

下一阶段只写 A expanding vs rolling、B drift-triggered、C delayed-feedback online adaptation 提案，先核查既有文献，不实施模型、不宣称组合世界首创。May/June 已见，后续设计用其结果一律探索性；最终留出继续封存。即使出现 nominal 显著，也不代表跨状态泛化或成本后收益；不同参数与月份冲突如实报告，不改变计划来追求显著性。
