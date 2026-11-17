# Phase 2C 执行记录

开始日期：2026-11-03（Asia/Shanghai）。工作目录为 `D:\zgs_project\QuantForge`，初始 HEAD `a431190283c7c508751d4175a45ec84bff5e4bcf`，工作树干净。已阅读指定协议、报告、holdout 政策、研究发现、runner/model/split 源码和配置。所有开发、依赖、临时文件、测试和结果均限制在独立项目内部。

## 干净提交复现与分析计划

在任何源文件/模型/依赖更改前，执行原始 baseline：

```powershell
$env:TEMP = "$PWD\.tmp"
$env:TMP = "$PWD\.tmp"
.venv\Scripts\python.exe scripts\run_research_baseline.py --config configs\research-baseline.toml
```

按 summary 的 Git SHA、git_dirty=false、指定预测 hash 找到唯一新 run `20261103T010111153259Z-a2cb0858`，没有按修改时间猜测。其 summary SHA-256 为 `7aa9f334a9e5b5f1085edd2362676d86d0898fcdd376f92bb1886efbc3dc6f12`；预测 SHA-256 与指定 `66f74e1367ed816455d8df2af5c8fdd89009c893aa5fe064df29fe646c86407a` 一致。1,053,840 条预测所有字段、模型配置/状态、fold 和非耗时指标精确相等。保留原有 Phase 2B 输出，没有覆盖。

随后先写 [分析计划](phase2c-analysis-plan.md)，于 `2026-11-03T01:04:30.804435+00:00` 冻结 SHA-256 `1dcb6c79d947a3c13e478efb367a1d77e8e3d671698fc0b6fb2c1864daac40d3`，再编写统计实现。冻结 manifest 见 [phase2c-plan-manifest.json](phase2c-plan-manifest.json)。明确这是已看过 Phase 2B 后的诊断计划，不冒充事前预注册；参数、比较、月份和校正家族此后不为追求显著性而变更。

## 依赖与实现中发现的问题

仅在项目 `.venv` 中增加 Matplotlib 及其绘图依赖；SciPy 已存在，改为显式直接依赖。`uv.lock` 所有原有包版本保持不变；新增 matplotlib 3.11.2、contourpy 1.4.0、cycler 0.12.1、fonttools 4.68.0、kiwisolver 1.5.1、pillow 13.0.0、pyparsing 3.3.3。更新 `pyproject.toml`、`uv.lock`、`requirements-lock.txt`。UV/MATPLOTLIB 缓存和 TEMP/TMP 位于项目，不改全局 Python 或系统配置。

```powershell
$env:UV_CACHE_DIR = "$PWD\.cache\uv"
$env:UV_PYTHON_DOWNLOADS = 'never'
uv add --no-sync 'matplotlib>=3.9,<4' 'scipy>=1.14,<2'
uv sync --locked --extra dev
uv export --locked --extra dev --format requirements-txt --no-emit-project --output-file requirements-lock.txt --quiet
```

真实预测配对的首次集成校验遇到 pandas 3 时间精度比较差异：预测为 `ns`，新建 date_range 默认 `us`，即使时间值一致 `DatetimeIndex.equals` 也返回 false。定位后改为明确长度及逐时刻比较并加入回归测试，未改模型或预测值；哈希/配对/成熟验证仍保留。

配对后的唯一市场/目标观测为 351,280 条（仍含两个相关 horizon），三模型 1,053,840 条输出不能当作三个独立样本。后续诊断只读冻结预测与已验证 JSON 元数据，不再读取 bar 正文，也不发出行情请求。

## 统计运行与最终验收

已实际完成正式分析和相同配置复跑。

- 首轮：`results/statistical-diagnostics/20261103T012342527623Z-d1bab75b/`。
- 复跑：`results/statistical-diagnostics/20261103T012551082603Z-c847d61f/`。
- 32行描述性配对汇总、128行Bootstrap、96行HAC、24行校准、96个分位桶、864行分布分位、3,856行ACF、14张真实PNG全部生成。23个确定性产物（7表+审计+配置+14图）逐字节相同；report/summary含run ID和耗时，按设计不同。
- 预测文件hash未变化；两轮所有输出和当前源码/config/依赖锁文件哈希复核通过。见 [phase2c-reproducibility.json](phase2c-reproducibility.json)。
- 128组Bootstrap绝对/相对95%区间均含0；96个HAC区间均含0，最小原始p=0.20119299199；主32项及全96项Holm均为1。保留所有指定块长/lag与月份，没有根据结果改参数。
- 主15m、L1440、观测加权、Ridge−Zero：Validation相对MSE差−0.057346%，CI[−0.168153%,0.069068%]；Development相对差−0.035861%，CI[−0.227375%,0.153867%]。主比较未建立稳健优势，也没有证明等效。
- 15m预测std为目标3.16%–4.18%，5m为2.63%–3.20%；偏差、协方差和尺度分解均保留。诊断斜率只报告，不训练/应用校准器。完整统计、全月IC和分布表见 [研究报告](statistical-diagnostics-report.md)。
- 首轮19.528秒：配对8.959、Bootstrap2.300、HAC1.316、校准/ACF0.612、绘图6.053；复跑21.697秒。耗时是整批研究处理，不是交易执行延迟。
- 图像总量约1.74MB，14图经完整缩略总览与主要区间图人工视觉检查；均使用真实冻结预测。docs中的PNG和小型统计表是原产物的逐字节副本，便于公开审阅；原预测和大型中间数据继续Git忽略。

## 测试、数学校验与开发故障

根任务实际执行完整 pytest：**268 passed in 18.69s**，日志保存在 `results/phase2c-pytest-initial.txt`；独立代理另以独立basetemp复核 **268 passed in18.50s**。原有165项未删除/修改，新增103项：配对44、Bootstrap/HAC31、校准8、runner20。

测试均明确synthetic，不把合成图/置信区间冒充正式研究。数学验证包括：配对损失符号、缺失/重复/错标签/版本/来源/哈希拒绝；显式块索引对累积和优化的数值对照；配对分子分母一起重采样；固定seed重复；gap/月份/角色隔离；不等样本量两种权重；HAC直接Bartlett手算、IID/AR序列、点区间退化和Holm完整家族。校准验证MSE分解和分位桶加权均值。runner注入配对、推断、校准、绘图、报告、哈希、summary写入失败，均无成功summary。

开发中修复/记录的问题：

1. 上述ns/us索引精度误拒绝，已加回归测试，预测不变。
2. Holm合成测试曾沿用“忽略undefined缩小家族”的错误期望；改为固定家族含undefined的正确oracle `[0.004,0.06,null,1.0]` 后通过，实现未缩小32/96家族。
3. 没有任何合法连续时间乘积的ACF lag现在输出null和原因，不输出虚假零；平方/方差溢出拒绝；分母零相对误差给null原因。
4. 本地一次性文档整理脚本出现语法/缩进错误，修复后重新运行，仅影响文档生成；两轮正式统计均一次完成，未改变统计输出。

核心推断另外经过独立只读检查：角色加权估计和随机分母定义一致，HAC角色方差按固定权重复算，零协方差假设与长块数量限制完整报告。

## 本轮命令和交付

除前述clean复跑和依赖命令外，实际执行：

```powershell
.venv\Scripts\python.exe -m pytest tests\test_calibration.py -q --basetemp=.tmp/pytest-calibration
.venv\Scripts\python.exe -m pytest -q | Tee-Object -FilePath results\phase2c-pytest-initial.txt
.venv\Scripts\python.exe scripts\run_statistical_diagnostics.py --config configs\statistical-diagnostics.toml
# Exactly the same diagnostic configuration, once more, for reproducibility.
.venv\Scripts\python.exe scripts\run_statistical_diagnostics.py --config configs\statistical-diagnostics.toml
.venv\Scripts\python.exe .tmp\finalize_phase2c.py
git diff --check
```

另执行了配对/统计模块的定向pytest、只读hash/Markdown链接/JSON/图像检查及git状态检查。本地`.tmp/finalize_phase2c.py`仅整理已产生证据，正式统计可用公开CLI复现。分析输入要求已冻结clean run及来源manifest仍在本地；新机器缺这些被忽略数据时会明确失败，不能按目录时间自动选其他输入。没有提供数据再分发包，也未验证跨环境位级复现。

新增源码均位于 `src/quantforge/diagnostics/`：`paired_losses.py`、`block_bootstrap.py`、`hac_tests.py`、`calibration.py`、`figures.py`、`experiment.py` 与 `__init__.py`；新CLI为 `scripts/run_statistical_diagnostics.py`，配置为 `configs/statistical-diagnostics.toml`。新增5份测试文件、冻结计划/manifest、执行/研究报告、复现JSON、summary、统计证据表和14图，以及 `research/adaptive-alpha-proposal.md`。

更新 README、AGENTS、pyproject和两个依赖锁文件；`.gitattributes`仅为被哈希的分析计划固定LF，避免Windows checkout换行改变计划字节。既有预测器、特征、标签、研究切分、Phase2B runner和165项测试文件保持不变。

完成后用原已授权署名 `zenggs05 <zenggaoshan05@gmail.com>` 创建本地提交 `feat: add robust alpha diagnostics and statistical inference`。Git hash通过交付时`git log -1`给出；不在本提交内伪造自引用commit hash。未创建远程仓库、push或PR。

## 当前限制与未实施范围

本轮验收项均已实现，无环境/网络阻碍。当前证据不能支持可靠MSE优势、跨状态泛化或可交易收益。只有四个相邻月份，角色内两月协方差近似、重尾/非平稳、MBB边缘效应/B2000的Monte Carlo误差、长块仅约15个长度单位、已看过数据的后验分析均限制解释。未进行等效检验，不能由不显著断言模型等效。

A/B/C只形成有文献依据的提案，未实现滚动/漂移触发/在线更新算法、任何新校准器、交易执行/成本账本或订单。July/August 2026最终留出行情继续封存；历史Phase2A的HEAD/CHECKSUM元数据探测如实保留。本轮无新行情请求或留出访问，May/June之后的模型研究只能标为探索。
