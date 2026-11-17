# Holdout-A 执行记录：D-059

## 范围与授权

本次执行遵循所有者 D-059 书面授权、冻结 Confirmatory Protocol v1 与只读执行合约。工作目录为 `D:\zgs_project\QuantForge-3a`，分支为 `phase3a-protocol-and-impl`。全程单会话串行，无子代理。仅下载 2026-07、2026-08 的四个合约 URL；main 工作树仅读取指定 development-manifest 与十个月 parquet，不读取其他文件，不访问 2F。2026-09-01 及之后行情、smoke 日和 Holdout-B 未访问。

协议 SHA-256：`38ef1aae687b6cd1a9b8b59709b84047250e2a9f90396cb9089244133b38f1b9`。

合约 SHA-256：`02cea40915339da68ba40befcc893872f885444e46d100787ef91533c9e6c408`。

合约、九个冻结文件、全部既有代码和证据未修改。代码实现只新增 `src/quantforge/holdout_a.py`、`scripts/run_holdout_a.py`、`tests/test_holdout_a.py`。H3 仅新增证据、记录、报告，并修改 README 一行状态。未安装依赖、修改 Git 配置、改写历史、删除或覆盖证据；无 push、remote、PR 或合入 main。

## 提交与执行时间线

快进点为 `1d1cde77ef8f0d265913619349e5f15cc71c18da`。固定 precheck 仅运行一次，输出原文：

```text
PRECHECK_OK {"development_months": 10, "disk_free_bytes": 446600724480, "head": "1d1cde77ef8f0d265913619349e5f15cc71c18da"}
```

H1 为 `31302087a8c25a1c14ecc3b116a39b7810740ad8`，消息为 `feat: holdout-a confirmatory runner (synthetic tests only)`。

H2 为 `b6fd4d8c6695263639c7206563d091b574d790ee`，消息为 `docs: holdout-a download and result-blind data audit (no fitting)`，是结果盲审计锚点。

H3 是直接以 H2 为父提交、消息为 `docs: holdout-a confirmatory run, reproduction, record and report` 的提交，包含本记录。其最终 SHA 由 Git 历史和提交后的所有者汇报定位，不写入自身字节。

真实数据进程台账的时间均为 UTC：

| 进程 | 开始 | 结束 | 退出码 | 拟合数 |
|---|---|---|---:|---:|
| download | 2026-11-17T06:10:10.480806+00:00 | 2026-11-17T06:10:15.354091+00:00 | 0 | 0 |
| audit | 2026-11-17T06:10:51.050774+00:00 | 2026-11-17T06:10:58.655790+00:00 | 0 | 0 |
| formal | 2026-11-17T06:15:33.599996+00:00 | 2026-11-17T06:15:47.432701+00:00 | 0 | 6 |
| reproduction | 2026-11-17T06:16:16.382733+00:00 | 2026-11-17T06:16:32.096765+00:00 | 0 | 6 |

四个进程各执行一次，没有重启。正式运行退出 0 后，未查看结果便直接启动复现。长进程运行期间仅轮询其自身会话。全部结果首次读取在复现退出 0 之后。正式与复现控制台日志均为 `{"status": "passed", "ridge_fits": 6}`。

结果目录分别为 `results/holdout-a/formal-20261117T061540330918Z-b6fd4d8` 与 `results/holdout-a/reproduction-20261117T061624687178Z-b6fd4d8`。

## 固定命令与环境

完整测试固定命令实际运行 NN=01、02 各一次：

```powershell
.venv\Scripts\python.exe -B -c "import subprocess,sys; f=open(r'.tmp\ha-pytest-run-NN.log','xb'); sys.exit(subprocess.call([sys.executable,'-B','-m','pytest','-q','-p','no:cacheprovider'],stdout=f,stderr=subprocess.STDOUT))"
```

真实运行均使用如下原始包装器，NAME 依次为 download-01、audit-01、formal-01、reproduction-01，模式依次为 download、audit、formal、reproduction。授权变量仅设置给子进程，未设置为 shell 环境变量，pytest 未设置该变量。

```powershell
.venv\Scripts\python.exe -B -c "import os,subprocess,sys; f=open(r'.tmp\ha-NAME.log','xb'); rc=subprocess.call([sys.executable,'-X','utf8','-X','faulthandler','-B']+sys.argv[1:],stdout=f,stderr=subprocess.STDOUT,env=dict(os.environ,QF_HOLDOUT_A_AUTH='D-059:38ef1aae687b6cd1a9b8b59709b84047250e2a9f90396cb9089244133b38f1b9')); f.close(); print('EXIT '+str(rc)); sys.exit(rc)" scripts\run_holdout_a.py <模式>
```

每份日志和产物均按以下固定命令单独独占复制，随后只读比较原字节，全部相同：

```powershell
.venv\Scripts\python.exe -B -c "import sys; d=open(sys.argv[2],'xb'); d.write(open(sys.argv[1],'rb').read()); d.close()" <源> <目标>
```

环境为本 worktree 的 Python 3.13.5，numpy 2.5.4、pandas 3.0.6、scipy 1.18.1、scikit-learn 1.9.1、pyarrow 23.0.1、pytest 9.2.0。冻结 seed=42、cpu_threads=1、float64、Ridge alpha=1.0、svd solver 均沿用既有路径。特征只构建一次，标签每个 horizon 各构建一次；训练标签通过既有 fit_predict_fold 的 mature_labels 路径。

## 网络与数据审计

4 个 URL 各 GET 一次，无重试；ZIP 合计 4,197,221 B，CHECKSUM 合计 176 B。两个 CHECKSUM 原文各 88 B，与合约逐字节一致，ZIP 的长度和 SHA 均一致。下载原始数据位于 `data/raw/binance/spot/BTCUSDT/1m/holdout-a/`，未入 Git。完整账本见 [network-ledger.jsonl](holdout-a-evidence/network-ledger.jsonl)。

PA-1 至 PA-6 均通过。十个开发月共 436,320 行，拼接后共 525,600 行；12 个月全部无缺口、重复和 close_time 异常，微秒单位核验通过，六月至七月边界相距一分钟。7、8 月各 44,640 行，未定义特征均为 0；D* 训练和评价均为 0。h=15 每月评价 44,625 行、lag=275、一个连续段，成熟截止排除各 15 行；h=5 每月评价 44,635 行。四个训练样本数依次为 436,244、480,884、436,254、480,894。短段规则未触发，没有剔除段。独立时间戳网格 oracle 与实际集合完全一致。详见 [data-audit.json](holdout-a-evidence/data-audit.json)。

## 测试与原字节日志

基线：1481 passed, 1 skipped in 613.70s。新增 82 项合成测试，最后定向测试为 82 passed in 11.05s。完整回归：1563 passed, 1 skipped in 586.88s。唯一跳过项是原有可选历史 C1 集成测试，因 `QF_C1_HISTORICAL_RESULTS is unset`。未重跑完整回归。所有日志源文件保留在 `.tmp/`，副本在 `docs/holdout-a-evidence/logs/`，字节数和 SHA 均相同。

| 日志名 | 字节数 | SHA-256 |
|---|---:|---|
| ha-pytest-run-01.log | 1947 | f71739b021e06f061791bf735b95cf81587daf649df2d9e09a4e71fb64b17a5c |
| ha-pytest-run-02.log | 2028 | f42d5d17dd28e817be1ad36c006ad89a3d08e3728db0e4ec2e8535f60cf080d5 |
| ha-download-01.log | 42 | 4843b84ce8eaaa588b7345e515d5a1b56159ce0a3a07c4ea7183ee3335af77a9 |
| ha-audit-01.log | 140 | a585043277b3101b6b99d3fa2fe9a6894d4514f8369ee6334624360d7c9424ac |
| ha-formal-01.log | 39 | 91f45e3dcd62ca8fda335f2207bb07026ac4c74fec0e9593f55d8fdefd1e092a |
| ha-reproduction-01.log | 39 | 91f45e3dcd62ca8fda335f2207bb07026ac4c74fec0e9593f55d8fdefd1e092a |
| ha-real-data-launch-ledger.jsonl | 1712 | ff4ed09d745b982c2652e5bb63f9b527b57e23debc91dead74ae42ebcc975b9e |

## 结果、复现与资源

完整结果见 [报告](holdout-a-report.md)。主检验合并 CW=5.439338862775343e-09，SE=2.880365407710732e-09，t=1.8884197290434905，单侧 p=0.029484812405491726，按冻结规则拒绝 H0。仅涉及预测信息，不涉及盈利、交易或执行。DM 与 MSE、MAE 及全部次要结果同时保留，不以主检验替代误差结论。

正式与复现各 6 次拟合，共 12 次，均产生 267,770 行预测。包括 predictions.parquet 在内的九个确定性文件全部逐字节相同；runtime 不参与相等判定。predictions.parquet 为 14,208,491 B，SHA-256 为 `5c1efd6b0e956bb13f2a1fbdeef9d3961bddb5439a7621735cd18ca60ff8fee3`，只归档大小与 SHA，不提交预测文件。逐文件证据见 [determinism-check.json](holdout-a-evidence/determinism-check.json) 与 [artifact-index.json](holdout-a-evidence/artifact-index.json)。

正式运行耗时 14.01504460000433 s，峰值进程内存 860,585,984 B，结束时磁盘可用 446,453,096,448 B。复现耗时 15.935187600087374 s，峰值 862,646,272 B，磁盘可用 446,438,785,024 B。均低于 2 h、16 GiB 内存上限且满足 8 GiB 磁盘余量。

## 隔离与文件指纹

冻结文件、开发输入和 Holdout 输入的完整 SHA 表列于报告，并保存在 data-audit.json。该文件还保存十三个执行路径代码的原始字节 SHA，含新入口、新模块、历史拆分、oracle、runner、power_reanalysis、HAC 与下载复用函数来源。新模块 SHA 为 `cfe028815d1c4235d7ca02a3d92c4f2e31e804d55934b1944464dc89ef78b207`，入口 SHA 为 `12c75532e7c3726ea822702cbe49f5be9a91b674ce3dc6acd3ddc0027ab8d134`。正式与复现均重新验证冻结和输入 SHA，并要求审计与 H2 的 data-audit.json 完全相同。

## 执行中的错误与修正

H1 前一次定向合成测试失败，E6 允许修正。命令为：

```powershell
.venv\Scripts\python.exe -X utf8 -B -m pytest -q -p no:cacheprovider tests/test_holdout_a.py
```

原始可恢复输出如下：

```text
FAILED tests/test_holdout_a.py::test_six_fits_reproduction_and_direct_statistics
tests\test_holdout_a.py:418: AssertionError
E           assert 2.0234737799263095e-18 == 2.02347377992631e-18
1 failed, 70 passed in 18.14s
```

原因是独立数值算式的舍入差异却使用浮点逐位相等。直接统计对照改为 `pytest.approx(..., rel=1e-12, abs=0)`；正式与复现产物逐字节断言保持不变。随后定向测试先为 80 passed in 15.84s，增加装配测试后为 82 passed in 11.05s，最终完整回归通过。失败发生的精确时间为 evidence_missing：当时未采集时间戳；已知早于 2026-11-17T05:43:26.157620+00:00。定向运行只输出控制台，未创建或覆盖日志。

H1 暂存时 Git 发出以下非失败警告，命令退出码为 0，未更改换行配置：

```text
warning: in the working copy of 'scripts/run_holdout_a.py', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'src/quantforge/holdout_a.py', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'tests/test_holdout_a.py', LF will be replaced by CRLF the next time Git touches it
```

截至文档写入，其他错误、执行偏离、拒绝、写入失败、日志覆盖或历史改写：无。较长的只读输出有工具显示截断，命令均退出 0；这不是运行失败。没有因真实结果修改代码、统计或样本规则。

## 指令缺口与采用的解释

1. 2026-11-17 H1 前采用，精确 UTC 为 evidence_missing。2.3 原文为“同一份合成 CSV 以开发月名交给旧解析器、以 Holdout 月名交给新解析器，DataFrame 逐值相同”。同一时间戳内容不能同时满足两个不同月份的边界检查。按 E6 引用的 D-057 常设规则，使用同一开发月 CSV 比较旧解析器与新纯内存 decode_csv 的 frame 值和 dtype，再分别用 7、8 月合成 CSV 验证新授权解析器的月份边界、成员名与 schema。只澄清合成验收，不改变真实解析、样本、预算或访问规则。
2. 2026-11-17T06:25:46+00:00 后、H3 文档写入前登记。报告要求列 H1–H3 SHA，但 H3 包含报告自身，最终 SHA 不能写进自身字节。按报告格式缺口处理：完整记录 H1、H2，H3 以父提交和唯一提交消息在 Git 历史中定位，完成后在所有者汇报给出最终 SHA。没有占位符或追加自引用提交。
3. 可选复用的 daily read_response 带 1 MiB ZIP 上限，而本合约单 ZIP 上限为 4 MiB。实现独立有界读取函数，严格执行本合约长度、SHA、预算和超时；旧函数不修改。这是可选实现选择，不是预算放宽。

本记录与报告各用原生新增工具一次性写入。其后按 D-059 固定命令分别核验 UTF-8、无 BOM、仅 LF 控制字符、无 U+FFFD 和半角问号，再进行 H3 提交；核验原始输出由最终汇报提供，避免修改记录自身。
