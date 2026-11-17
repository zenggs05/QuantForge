# 第一阶段验收记录

日期：2026-11-02（Asia/Shanghai）。仅执行初始化、真实调研和单日数据验证，没有实现策略、实盘、远程仓库或 PR。

## 验收结果

| 要求 | 实际结果 | 证据 |
|---|---|---|
| 目录范围 | 起点 `D:\zgs_project`，确认已有空 `QuantForge` 后，所有项目操作位于 `D:\zgs_project\QuantForge`；没有嵌套项目 | [环境](environment.md) |
| 独立本地 Git | `git init -b main`，show-toplevel 为 QuantForge，remote 列表为空，尚无 commit | [命令](commands.md) |
| 轻量可复现结构 | src 布局、`.venv`、项目内 cache/tmp、pyproject、uv.lock 和 hash-pinned requirements export | [README](../README.md) |
| 长期规范 | AGENTS 含全部十项研究与协作约定及目录边界 | [AGENTS](../AGENTS.md) |
| GitHub 调研 | 五个指定仓库，15 个真实 Issue；维护/贡献指南/PR/合并状态有来源和日期 | [GitHub 报告](../research/github-opportunities.md) |
| 论文调研 | 9 篇完整条目，覆盖 A–G，包含近期工作与必要经典；区分代码/数据可用与已复现 | [论文报告](../research/literature-review.md) |
| 候选排序 | 六个可证伪课题，按指定权重评分 | [研究候选](../research/research-ideas.md) |
| 市场数据 | 2026-09-01 BTCUSDT Spot 1m，官方 ZIP 与 CHECKSUM HTTP 200，1,440 行，SHA256 一致，UTC us，Parquet 精确 roundtrip | [数据报告](data-validation.md)、[manifest](data-validation-manifest.json) |
| 测试 | 最终根任务执行 `python -m pytest -q`：**23 passed in 1.12s**；依赖检查通过；compileall/CLI help 通过 | `tests/test_data.py`、`tests/test_download_cli.py` |
| Git 排除数据 | ZIP、Parquet、results manifest 的 `git check-ignore -v` 都匹配 .gitignore | [命令](commands.md) |
| 12 周路线 | 四阶段含目标、交付、指标、测试、风险、验收与数据闸门 | [路线图](../research/roadmap.md) |

## 创建的主要文件

- 根目录：`AGENTS.md`、`README.md`、`pyproject.toml`、`.gitignore`、`uv.lock`、`requirements-lock.txt`；本地 `.git`、`.venv`。
- `configs/data-sample.toml`；`src/quantforge/__init__.py`、`src/quantforge/data.py`。
- `scripts/check_environment.py`、`scripts/download_sample.py`。
- `tests/test_data.py`、`tests/test_download_cli.py`。
- `docs/environment.md`、`environment-check.json`、`data-validation.md`、`data-validation-manifest.json`、`commands.md`、本验收文件。
- `research/github-opportunities.md`、`literature-review.md`、`research-ideas.md`、`roadmap.md`，以及 `research/evidence/` 中带来源的快照。
- `data/raw/`、`data/processed/`、`notebooks/`、`results/` 的占位文件；真实样本与运行结果只保存在被忽略的子目录。

## Top 3 与第一步

1. **4.75/5**：标签延迟与交易成本约束下的漂移自适应 Crypto Alpha。
2. **4.60/5**：不确定性校准驱动的成本感知“不交易”决策。
3. **4.50/5**：量价 Alpha 的回测语义与成本模型差分验证。

最先实施课题 1 的实验协议和简单基准，以课题 3 的数据/执行契约保障正确性。已有的单日真实数据只用于流水线测试，不能用于有效 alpha 结论。完整下一条 Codex 指令见 [路线图末尾](../research/roadmap.md)。

## 当前限制与未完成事项

- **没有阻断本阶段交付的环境问题**。Python 3.13.5、Git、uv、公开网络与单日数据流程已可用。
- `gh` 不在 PATH；无法通过 gh 核验登录，采用公开 REST API；没有要求安装或登录。
- 检测到 RTX 5070 Laptop GPU，但基础 PyTorch 为 2.7.1+cpu，不能 GPU 训练。本项目没有安装 PyTorch/CUDA，训练栈是以后任务。
- 部分 arXiv 直连曾 TLS EOF/超时，浏览检索与主站探测成功；Nautilus PR 列表两次响应不完整，改用 timeline 与关键词检索。因此不能声称完全排除所有相关 PR。
- 只下载一天 K 线。多月/多年、多资产、完整 L2/L3、真实价差/延迟/冲击数据均未验证，不是本阶段未按要求下载的缺项。
- 论文实现和上游框架未安装运行；不能把论文/Issue 作者的性能或收益当成 QuantForge 实验。
- 官方数据条款核查到非商业用途限制，详见数据文档；尚未选定本项目公开代码许可证，未来发布前需处理。
- 没有策略，所以净收益、交易成本、回撤与统计检验当前不适用；未来实验协议已要求同时报告。

所有已完成事实、限制和假设在对应文档中分开记录。没有自动上传 GitHub。
