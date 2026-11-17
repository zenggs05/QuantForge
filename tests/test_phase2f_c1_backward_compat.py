"""Optional, read-only historical checkpoint format check; no market reads."""
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from quantforge import data
from quantforge.online.replay import ReplayEngine

AUTHORIZED_ROOT = Path(r"D:\zgs_project\QuantForge\results\online-foundation")
RUN = "20261103T045308206581Z-5632721d"
CHECKPOINTS = tuple((horizon, event) for horizon in (15, 5) for event in (0, 360, 720, 1080, 1440))


def test_historical_v1_read_write_bytes_only_when_explicitly_enabled(tmp_path, monkeypatch):
    requested = os.environ.get("QF_C1_HISTORICAL_RESULTS")
    if requested is None:
        pytest.skip("Optional owner integration: QF_C1_HISTORICAL_RESULTS is unset")
    if Path(requested) != AUTHORIZED_ROOT:
        raise ValueError("QF_C1_HISTORICAL_RESULTS must equal the authorized historical root")
    # Read only these ten named checkpoint files. No directory glob or market
    # source is accessed, and only temporary roots are ever used for writing.
    originals = {}
    try:
        for horizon, event in CHECKPOINTS:
            path = AUTHORIZED_ROOT / RUN / "checkpoints" / f"h{horizon}-event{event}.json"
            originals[path] = path.stat().st_mtime_ns
            raw = path.read_bytes()
            state = json.loads(raw)
            payload = state["payload"]
            with monkeypatch.context() as read_context:
                read_context.setattr(data, "REPOSITORY_ROOT", AUTHORIZED_ROOT.parent.parent)
                engine = ReplayEngine.load_checkpoint(path, source_sha256=payload["source_sha256"],
                    config_sha256=payload["config_sha256"], horizon_minutes=horizon)
            assert engine.state_dict()["sha256"] == state["sha256"]
            with monkeypatch.context() as write_context:
                write_context.setattr(data, "REPOSITORY_ROOT", tmp_path)
                copied = tmp_path / "results" / "online-foundation" / f"{uuid4().hex}.json"
                engine.save_checkpoint(copied)
                assert copied.read_bytes() == raw
    finally:
        for path, before in originals.items():
            assert path.stat().st_mtime_ns == before, "Historical checkpoint mtime changed"
