# 阶段一实际命令与复现记录

日期：2026-11-02（Asia/Shanghai）。初次在 `D:\zgs_project` 只运行目录检查：

```powershell
Get-Location
Get-ChildItem -Force
Get-ChildItem -Force .\QuantForge
```

此后所有命令都以 `D:\zgs_project\QuantForge` 为工作目录。文件通过补丁或项目内 Python/PowerShell 脚本写入，没有修改系统配置。

## 环境和初始化（已执行）

```powershell
Get-Location
[System.Environment]::OSVersion.VersionString
$PSVersionTable.PSVersion
Get-Command python,py,git,pip,uv,gh,nvidia-smi -ErrorAction SilentlyContinue
rg --files -g AGENTS.md -g '*.md' .
git init -b main
python --version
python -m pip --version
uv --version
git --version
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
python scripts\check_environment.py
git rev-parse --show-toplevel
git remote -v
```

空目录首次 `rg` 未发现文件时返回 1，这是“无匹配”，不是环境故障。`gh` 不存在，未执行认证或任何远程写操作。

## 项目内依赖安装（已执行）

```powershell
$env:UV_CACHE_DIR = "$PWD\.cache\uv"
$env:UV_PYTHON_DOWNLOADS = 'never'
$env:TEMP = "$PWD\.tmp"
$env:TMP = "$PWD\.tmp"
uv venv --python (Get-Command python).Source .venv
uv pip install --python .venv\Scripts\python.exe 'pandas>=2.2.3,<3.1' 'pyarrow>=19,<24' 'pytest>=8,<10' 'setuptools>=77'
```

以上环境变量只作用于命令进程；没有持久修改用户或系统变量。联网研究使用公开 GitHub REST API、arXiv/官方论文页面和官方公开数据 URL；详见各研究文档的来源与抓取证据。

## 安装收尾、依赖锁与脚本检查（已执行）

```powershell
uv pip install --python .venv\Scripts\python.exe --no-build-isolation --no-deps -e .
uv lock
uv sync --locked --extra dev
uv export --locked --extra dev --format requirements-txt --no-emit-project --output-file requirements-lock.txt
uv pip list --python .venv\Scripts\python.exe
.venv\Scripts\python.exe scripts\download_sample.py --help
.venv\Scripts\python.exe -m compileall -q src scripts
```

初次下载 pandas、NumPy、PyArrow 等包发生重试，最终安装成功。`uv sync` 精确同步运行/开发依赖并移除仅初始构建所需的 setuptools。没有全局安装或升级。

## 数据与测试（已执行）

```powershell
.venv\Scripts\python.exe -m pytest tests/test_data.py -q
.venv\Scripts\python.exe scripts\download_sample.py --date 2026-09-01
git check-ignore data/raw/binance/spot/BTCUSDT/1m/BTCUSDT-1m-2026-09-01.zip data/processed/binance/spot/BTCUSDT/1m/BTCUSDT-1m-2026-09-01.parquet results/data-validation/BTCUSDT-1m-2026-09-01.json
git status --short --ignored
git rev-parse --show-toplevel
git remote -v
```

在依赖下载期间先用标准库 `urllib.request` 取得官方 ZIP、CHECKSUM 和原始 HTTP 元数据并验证 SHA256；正式 CLI 重新核验 checksum、解析完整数据、写 Parquet、执行 roundtrip，重复运行成功。一次在 editable 安装前执行 CLI 出现 `ModuleNotFoundError: quantforge`，安装完成后已解决；此事没有记成数据下载失败。

离线 tests 的合成 fixture 与实际市场数据是两套独立验证。最终结果和检查时间见 [数据验证](data-validation.md) 与 [阶段一验收](phase-one-summary.md)。没有策略训练或收益测试。

## 研究检索（已执行）

- GitHub：Python `urllib` 对 `api.github.com/repos/...`、`issues`、`pulls`、`commits`、Issue timeline、`search/issues` 发起公开 GET；原始地址/时间和状态保存在 `research/evidence/`。读取官方 README、Contributing 与相关源码；没有 clone 整个量化框架、安装其环境或执行上游脚本。
- 论文：公开网页搜索、arXiv 摘要/HTML、作者/会议页面及公开实现 README；具体条目、证据层级、代码/数据限制见 `research/literature-review.md`。
- 网络检测：`scripts/check_environment.py` 实际请求 GitHub 主站/API、arXiv 主站、Binance Vision 和官方数据 README；输出为 `docs/environment-check.json`。

仅本地初始化 Git。当前无 remote、无 commit，也没有 push、创建 PR、向维护者发消息或自动创建 GitHub 仓库。
