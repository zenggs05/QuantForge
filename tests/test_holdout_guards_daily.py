"""Synthetic daily access tests: reject sealed dates before I/O."""

from datetime import date
import importlib.util
import json
from pathlib import Path
import sys

import pandas as pd
import pytest

from quantforge import data
from quantforge.experiment import ExperimentConfig, load_verified_sample
from quantforge.research_policy import HOLDOUT_A, HOLDOUT_B, SMOKE_DAY, ResearchPolicyError, ensure_not_holdout


SEALED_DAYS = ["2026-07-15", "2026-08-31", "2026-09-02", "2026-10-31"]


def fail_io(*args, **kwargs):
    raise AssertionError("Sealed interval reached forbidden I/O")


@pytest.mark.parametrize("day", SEALED_DAYS)
def test_download_refused_before_fetch_or_path_resolution(monkeypatch, day):
    monkeypatch.setattr(data,"fetch",fail_io)
    monkeypatch.setattr(data,"repository_path",fail_io)
    with pytest.raises(data.DataValidationError,match="sealed"):
        data.download_day(date.fromisoformat(day))


@pytest.mark.parametrize("day", SEALED_DAYS)
def test_parser_refused_before_csv_open(monkeypatch, day):
    monkeypatch.setattr(pd,"read_csv",fail_io)
    with pytest.raises(data.DataValidationError,match="sealed"):
        data.parse_klines(b"never parsed",date.fromisoformat(day))


@pytest.mark.parametrize("day", SEALED_DAYS)
def test_smoke_loader_refused_by_path_before_manifest_open(monkeypatch, tmp_path, day):
    monkeypatch.setattr(data,"REPOSITORY_ROOT",tmp_path)
    config=ExperimentConfig(parquet_path=f"sample-{day}.parquet",manifest_path=f"sample-{day}.json")
    monkeypatch.setattr(Path,"read_text",fail_io)
    monkeypatch.setattr(Path,"read_bytes",fail_io)
    with pytest.raises(data.DataValidationError,match="sealed"):
        load_verified_sample(config)


@pytest.mark.parametrize("day", SEALED_DAYS)
def test_manifest_date_refused_before_market_file_reads(monkeypatch, tmp_path, day):
    monkeypatch.setattr(data,"REPOSITORY_ROOT",tmp_path)
    config=ExperimentConfig(parquet_path="sample.parquet",manifest_path="sample.json")
    monkeypatch.setattr(Path,"read_text",lambda *a,**k:json.dumps({"date_utc":day}))
    monkeypatch.setattr(Path,"read_bytes",fail_io)
    monkeypatch.setattr(pd,"read_parquet",fail_io)
    with pytest.raises(data.DataValidationError,match="sealed"):
        load_verified_sample(config)


@pytest.mark.parametrize("day", SEALED_DAYS)
def test_cli_refuses_explicit_date_before_config_open_or_downloader(monkeypatch, day):
    path=Path(__file__).resolve().parents[1]/"scripts/download_sample.py"
    spec=importlib.util.spec_from_file_location("synthetic_daily_cli",path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module,"download_day",fail_io)
    monkeypatch.setattr(module,"repository_path",fail_io)
    monkeypatch.setattr(sys,"argv",["download_sample.py","--date",day])
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code==2


@pytest.mark.parametrize("day", ["2025-09-01","2026-09-01"])
def test_allowed_existing_dates_reach_parser_and_downloader(monkeypatch, day):
    calls=[]
    def reached(*args,**kwargs):
        calls.append(True)
        raise RuntimeError("allowed boundary reached")
    monkeypatch.setattr(pd,"read_csv",reached)
    with pytest.raises(RuntimeError,match="allowed boundary"):
        data.parse_klines(b"synthetic",date.fromisoformat(day))
    monkeypatch.setattr(data,"repository_path",reached)
    with pytest.raises(RuntimeError,match="allowed boundary"):
        data.download_day(date.fromisoformat(day))
    assert len(calls)==2


@pytest.mark.parametrize("start,end,allowed",[
    ("2026-06-30T00:00:00Z","2026-07-01T00:00:00Z",True),
    ("2026-09-01T00:00:00Z","2026-09-02T00:00:00Z",True),
    ("2026-11-01T00:00:00Z","2026-11-02T00:00:00Z",True),
    ("2026-06-30T00:00:00Z","2026-07-01T00:00:01Z",False),
    ("2026-09-01T23:59:59Z","2026-09-02T00:00:01Z",False),
])
def test_half_open_intersection_boundaries(start,end,allowed):
    if allowed:
        ensure_not_holdout(start,end)
    else:
        with pytest.raises(ResearchPolicyError,match="sealed"):
            ensure_not_holdout(start,end)


def test_policy_constants_and_invalid_intervals():
    assert HOLDOUT_A==(pd.Timestamp("2026-07-01T00:00:00Z"),SMOKE_DAY)
    assert HOLDOUT_B==(pd.Timestamp("2026-09-02T00:00:00Z"),pd.Timestamp("2026-11-01T00:00:00Z"))
    for start,end in [("2026-01-01","2026-01-02"),(SMOKE_DAY,SMOKE_DAY),(pd.NaT,SMOKE_DAY)]:
        with pytest.raises(ResearchPolicyError):ensure_not_holdout(start,end)


def test_actual_range_refuses_a_misnamed_payload():
    # Only synthetic bytes are constructed; no sealed market source exists.
    start=int(pd.Timestamp("2026-07-15T00:00:00Z").timestamp()*1_000_000)
    payload=f"{start},100,101,99,100,1,{start+60_000_000-1},100,1,0.5,50,0\n".encode()
    with pytest.raises(data.DataValidationError,match="Holdout-A"):
        data.parse_klines(payload,date(2026,9,1))


def test_manifest_declared_range_checked_before_market_reads(monkeypatch,tmp_path):
    monkeypatch.setattr(data,"REPOSITORY_ROOT",tmp_path)
    config=ExperimentConfig(parquet_path="sample.parquet",manifest_path="sample.json")
    manifest={"date_utc":"2026-09-01","raw_path":"sample.zip","parquet_path":"sample.parquet",
              "validation":{"first_open_utc":"2026-09-02T00:00:00Z","last_open_utc":"2026-09-02T23:59:00Z"}}
    monkeypatch.setattr(Path,"read_text",lambda *a,**k:json.dumps(manifest))
    monkeypatch.setattr(Path,"read_bytes",fail_io)
    with pytest.raises(data.DataValidationError,match="Holdout-B"):
        load_verified_sample(config)
