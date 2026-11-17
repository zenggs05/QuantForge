"""Synthetic Gate 1 contracts and in-memory HTTP responses."""

from __future__ import annotations

import io
import json
from pathlib import Path
import shutil
import zipfile

import pandas as pd


def copy_frozen_contract(source_root: Path, target_root: Path) -> None:
    """Copy only small frozen contract files; never copy market artifacts."""
    manifest = json.loads((source_root / "docs/phase3a-plan-manifest.json").read_text(encoding="utf-8"))
    paths = {"docs/phase3a-plan-manifest.json"}
    paths.update(manifest["files"])
    paths.update(manifest["auxiliary"])
    paths.update(manifest["prediction_code_sha256"])
    paths.add("docs/confirmatory-protocol-v1.md")
    paths.add(manifest["config_contract"]["power"]["source"])
    for relative in paths:
        destination = target_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / relative, destination)


def synthetic_month_csv(month: str) -> bytes:
    start = pd.Timestamp(f"{month}-01", tz="UTC")
    end = start + pd.DateOffset(months=1)
    unit = "us" if start.year >= 2025 else "ms"
    scale = 1_000 if unit == "us" else 1_000_000
    first = start.value // scale
    step = 60_000_000 if unit == "us" else 60_000
    count = int((end - start) / pd.Timedelta(minutes=1))
    return ("\n".join(
        f"{first + i * step},100,102,99,101,10,{first + (i + 1) * step - 1},1005,20,4,402,0"
        for i in range(count)
    ) + "\n").encode()


def synthetic_archive(month: str = "2024-12") -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"BTCUSDT-1m-{month}.csv", synthetic_month_csv(month))
    return out.getvalue()


class FakeResponse:
    def __init__(self, url: str, payload: bytes, *, status: int = 200,
                 content_length: int | None = None):
        self.url = url
        self.status = status
        self.headers = {"Content-Length": str(len(payload) if content_length is None else content_length)}
        self._payload = payload
        self._offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read1(self, count: int) -> bytes:
        chunk = self._payload[self._offset:self._offset + count]
        self._offset += len(chunk)
        return chunk
