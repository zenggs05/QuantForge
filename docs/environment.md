# 开发环境核查

核查日期：2026-11-02（Asia/Shanghai）。机器可读证据：[environment-check.json](environment-check.json)。复查命令：在仓库根目录运行 `python scripts/check_environment.py`。

## 工作区边界

- 最初工作目录：`D:\zgs_project`；只列目录，确认已有空目录 `QuantForge` 后进入。
- 项目根目录及后续所有命令工作目录：`D:\zgs_project\QuantForge`。
- 在该目录执行 `git init -b main`，`git rev-parse --show-toplevel` 返回 `D:/zgs_project/QuantForge`。
- 未在父目录初始化 Git，未创建嵌套 QuantForge，未配置远程仓库、提交或推送。
- 虚拟环境 `.venv`、uv 缓存 `.cache/uv`、临时目录 `.tmp`、pytest 临时数据都位于项目内部；未修改系统配置和全局依赖。

## 检测结果

| 项目 | 实际结果 | 影响 |
|---|---|---|
| OS | Windows 11，10.0.26200 | 原生 PowerShell 可运行阶段一 |
| PowerShell | 5.1.26100.9711 | 命令使用 Windows 兼容语法 |
| 基础 Python | CPython 3.13.5，Miniconda 安装 | 基于现有解释器创建隔离环境 |
| 基础 pip | 25.1 | 仅检测，没有安装全局包 |
| uv | 0.11.6 | 用于局部环境和锁定依赖 |
| Git | 2.50.1.windows.1 | 已初始化本地 `main` 分支 |
| GitHub CLI | PATH 中未找到 `gh` | 无法通过 gh 验证登录；不是“已确认未登录” |
| GitHub 访问 | 主站、REST API 均 HTTP 200 | 公开 API 无需登录，受匿名配额限制 |
| arXiv 访问 | 主站 HTTP 200 | 具体 API/全文端点的成功与否另行记录 |
| Binance Public Data | 归档首页、官方 GitHub README 均 HTTP 200 | 具体 ZIP 和校验结果见数据验证文档 |
| GPU | NVIDIA GeForce RTX 5070 Laptop GPU，8151 MiB，驱动 592.01 | 硬件和驱动可检测 |
| 基础环境 PyTorch | 2.7.1+cpu，`torch.version.cuda=None`，CUDA 不可用 | 当前 Python 不能使用 GPU；硬件存在不等于训练环境可用 |

未安装 CUDA 或 GPU 版 PyTorch。阶段一的 pandas/Arrow 数据检查只需要 CPU；研究训练阶段再根据模型与兼容性创建受控依赖配置。GPU 内存约 8 GiB，后续大模型与订单簿训练需考虑批量、精度和 HPC 资源。

## 依赖管理

项目使用 `src/` 布局、`pyproject.toml` 和本地 `.venv`。运行依赖仅 pandas 和 PyArrow；开发依赖 pytest。精确解析结果保存在 `uv.lock`，含哈希的跨环境依赖导出保存在 `requirements-lock.txt`；本机实际关键版本见数据 manifest。README 给出已执行的 `uv sync --locked --extra dev` 复现命令。未安装量化框架、交易客户端或模型训练栈。

本机锁定环境关键版本：pandas 3.0.6、NumPy 2.5.4、PyArrow 23.0.1、pytest 9.2.0。首次 PyPI 下载重试后成功，未改用不明来源；完成约需 5 分钟。构建时使用项目环境内 setuptools，`uv sync` 随后移除非运行依赖；未触及基础环境。声明 Python >=3.11，但实际只测试 Windows/Python 3.13.5，其他组合暂未验证。

脚本仅探测现有解释器中的 PyTorch，不会将基础环境的包加入项目环境。`gh` 是可选项，阶段一不需要安装；未来需要账户操作时再自行安装和登录，不能把登录状态、token 或私密配置写入仓库。

网络成功只表示该次检查时可达，不保证持续可达或所有区域都可访问。研究文档将网页检索与本机直连结果分别记录。
