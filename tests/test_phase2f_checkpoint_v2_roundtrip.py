"""Exact synthetic v1/v2 state, continuation, and immutable-pair checks."""
import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from quantforge import data
from quantforge.online.audit_log import write_record
from quantforge.online.checkpoint_v2 import load_v2
from quantforge.online.replay import ReplayEngine, _canonical
from tests.test_online_replay import synthetic_replay


def pair_paths(tmp_path, name="v2"):
    directory = tmp_path / "results" / "online-foundation" / name
    return directory / "state-core.json", directory / "audit.jsonl"


def test_published_pair_refuses_overwrite_and_later_log_append(minute_bars, tmp_path, monkeypatch):
    engine, fx, ly, events, *_ = synthetic_replay(minute_bars)
    engine.step(events[0], fx, ly)
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    core, log = pair_paths(tmp_path)
    engine.save_checkpoint_v2(core, log)
    original = {p.name: p.read_bytes() for p in core.parent.iterdir()}
    with pytest.raises(FileExistsError):
        engine.save_checkpoint_v2(core, log)
    assert original == {p.name: p.read_bytes() for p in core.parent.iterdir()}
    write_record(log, {"uncommitted": True})
    with pytest.raises(ValueError, match="log file size mismatch"):
        load_v2(core, log, "a"*64, "b"*64, 15)


def test_v2_load_rejects_incompatible_bindings(minute_bars, tmp_path, monkeypatch):
    engine, *_ = synthetic_replay(minute_bars)
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    core, log = pair_paths(tmp_path)
    engine.save_checkpoint_v2(core, log)
    with pytest.raises(ValueError, match="source/config/horizon"):
        load_v2(core, log, "c"*64, "b"*64, 15)


@pytest.mark.parametrize("path", ["../outside.json", "data/processed/2026-07.json", "results/research-baseline/a.json"])
def test_dispatch_rejects_paths_before_stat_or_read(monkeypatch, path):
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected filesystem access")
    monkeypatch.setattr(Path, "stat", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    with pytest.raises(ValueError, match="Checkpoint path"):
        ReplayEngine.load_checkpoint(path, source_sha256="a"*64, config_sha256="b"*64, horizon_minutes=15)


@pytest.mark.parametrize("sidecar", [b"not json", b'{"schema_version":2,"core":{}}', b"[]"])
def test_unrelated_sidecar_does_not_change_valid_v1_load(minute_bars, tmp_path, monkeypatch, sidecar):
    engine, *_ = synthetic_replay(minute_bars)
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    v1 = tmp_path / "results" / "online-foundation" / "v1.json"
    engine.save_checkpoint(v1)
    (v1.parent / "checkpoint-audit.json").write_bytes(sidecar)
    restored = ReplayEngine.load_checkpoint(v1, source_sha256="a"*64, config_sha256="b"*64, horizon_minutes=15)
    assert restored.state_dict() == engine.state_dict()


def test_history_over_32mib_is_recoverable_only_in_separated_format(minute_bars, tmp_path, monkeypatch):
    engine, fx, ly, events, *_ = synthetic_replay(minute_bars, 5)
    # Artificial long IDs amplify audit history without altering any bar or
    # feature. At save time every long-ID sample has matured and left pending.
    for index, event in enumerate(events[:12]):
        if index < 6:
            event = replace(event, sample_id=f"synthetic-long-{index}:" + "x"*(2*1024*1024))
        engine.step(event, fx, ly)
    monkeypatch.setattr(data, "REPOSITORY_ROOT", tmp_path)
    core, log = pair_paths(tmp_path)
    v1 = tmp_path / "results" / "online-foundation" / "large-v1.json"
    engine.save_checkpoint(v1)
    assert v1.stat().st_size > 33_554_432
    with pytest.raises(ValueError, match="32 MiB safety bound"):
        ReplayEngine.load_checkpoint(v1, source_sha256="a"*64, config_sha256="b"*64, horizon_minutes=5)
    engine.save_checkpoint_v2(core, log)
    assert log.stat().st_size > 33_554_432
    assert core.stat().st_size < 33_554_432
    restored = ReplayEngine.load_checkpoint(core, source_sha256="a"*64, config_sha256="b"*64, horizon_minutes=5)
    assert restored.state_dict()["sha256"] == engine.state_dict()["sha256"]
    assert restored.step(events[12], fx, ly) == engine.step(events[12], fx, ly)
