"""Read-only environment probes; write reports only inside QuantForge."""

from __future__ import annotations

import concurrent.futures
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def command(args: list[str], timeout: int = 25) -> dict:
    try:
        result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        return {"command": args, "returncode": result.returncode,
                "output": (result.stdout + result.stderr).strip()}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": args, "error": str(exc)}


def probe(url: str) -> dict:
    try:
        request = Request(url, headers={"User-Agent": "QuantForge/0.1 public-research-probe"})
        with urlopen(request, timeout=20) as response:
            response.read(1024)
            return {"url": url, "status": response.status, "reachable": True}
    except HTTPError as exc:
        return {"url": url, "status": exc.code, "reachable": False, "error": str(exc)}
    except Exception as exc:
        return {"url": url, "reachable": False, "error": f"{type(exc).__name__}: {exc}"}


def main() -> None:
    if Path.cwd().resolve() != ROOT:
        raise SystemExit(f"Run from the repository root: {ROOT}")
    (ROOT / ".tmp").mkdir(exist_ok=True)
    for name in ("TEMP", "TMP", "TMPDIR"):
        os.environ[name] = str(ROOT / ".tmp")
    tools = {name: shutil.which(name) for name in ("python", "git", "pip", "uv", "gh", "nvidia-smi")}
    commands = [command([sys.executable, "--version"]), command([sys.executable, "-m", "pip", "--version"])]
    for name in ("git", "uv"):
        if tools[name]:
            commands.append(command([tools[name], "--version"]))
    gh_logged_in = None
    if tools["gh"]:
        # Record only a boolean, never authentication output or tokens.
        gh_logged_in = command([tools["gh"], "auth", "status"]).get("returncode") == 0
    gpu = command([tools["nvidia-smi"], "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"]) if tools["nvidia-smi"] else {"error": "nvidia-smi unavailable"}
    torch = command([sys.executable, "-c", "import torch; print('version=',torch.__version__); print('cuda_build=',torch.version.cuda); print('cuda_available=',torch.cuda.is_available()); print('device_count=',torch.cuda.device_count())"])
    urls = ["https://github.com", "https://api.github.com", "https://arxiv.org", "https://data.binance.vision/", "https://raw.githubusercontent.com/binance/binance-public-data/master/README.md"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        network = list(pool.map(probe, urls))
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "review_date_asia_shanghai": "2026-11-02", "cwd": str(Path.cwd()), "repository_root": str(ROOT), "os": platform.platform(), "python_executable": sys.executable, "tools": tools, "versions": commands, "gh_logged_in": gh_logged_in, "gpu": gpu, "base_python_torch": torch, "network": network}
    target = ROOT / "docs" / "environment-check.json"
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
