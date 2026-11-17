"""Deterministic full-calendar synthetic data for the Gate 2 rehearsal.

The generator follows the frozen synthetic distribution but deliberately has
no connection to Gate 1 files.  Manifest metadata is used only for its public
month names and gap ranges; values are generated locally.
"""
from __future__ import annotations

import math
from pathlib import Path
import json
from datetime import datetime, timezone
from time import perf_counter

import numpy as np
import pandas as pd

from quantforge.phase3a_config import load_history_config
from quantforge import phase3a_gate2 as g2
from quantforge.phase3a_gate2 import audit_close_times, load_contract


def _month_frame(config: dict, month: str, ordinal: int) -> pd.DataFrame:
    settings = config["synthetic"]
    start = pd.Timestamp(f"{month}-01", tz="UTC")
    end = start + pd.offsets.MonthBegin(1)
    times = pd.date_range(start, end, freq="min", inclusive="left", tz="UTC")
    n = len(times)
    burn = int(settings["burnin"])
    streams = [np.random.default_rng(np.random.SeedSequence([config["experiment"]["seed"], ordinal, stream]))
               for stream in settings["stream_ids"]]
    phi, omega = settings["ar_phi"], settings["garch_omega"]
    alpha, beta, df = settings["garch_alpha"], settings["garch_beta"], settings["student_df"]
    innovations = streams[0].standard_t(df, n + burn) * math.sqrt((df - 2) / df)
    returns = np.empty(n + burn, dtype=np.float64)
    variance, previous_return, previous_error = omega / (1 - alpha - beta), 0.0, 0.0
    for t, innovation in enumerate(innovations):
        variance = omega + alpha * previous_error**2 + beta * variance
        error = math.sqrt(variance) * innovation
        previous_return = phi * previous_return + error
        returns[t], previous_error = previous_return, error
    close = settings["initial_price"] * np.exp(np.cumsum(returns[burn:]))
    previous_close = np.r_[settings["initial_price"], close[:-1]]
    opening = previous_close * np.exp(streams[1].normal(0, settings["price_gap_sd"], n))
    width = settings["wick_min"] + settings["wick_scale"] * np.abs(streams[2].normal(size=n))
    volume = streams[3].lognormal(settings["volume_log_mean"], settings["volume_log_sd"], n)
    trades = streams[4].integers(settings["trades_min"], settings["trades_max_exclusive"], n)
    fraction = streams[5].uniform(settings["taker_ratio_min"], settings["taker_ratio_max"], n)
    return pd.DataFrame({
        "open_time": times,
        "close_time": times + pd.Timedelta(minutes=1) - (pd.Timedelta(milliseconds=1) if month < "2025-01" else pd.Timedelta(microseconds=1)),
        "open": opening, "high": np.maximum(opening, close) * np.exp(width),
        "low": np.minimum(opening, close) * np.exp(-width), "close": close,
        "volume": volume, "number_of_trades": trades,
        "taker_buy_base_asset_volume": volume * fraction,
        "quote_asset_volume": volume * close,
        "taker_buy_quote_asset_volume": volume * close * fraction,
    })


def _drop_ranges(frame: pd.DataFrame, entry: dict) -> pd.DataFrame:
    ranges = entry.get("gap_ranges") or entry.get("audit", {}).get("gap_ranges", [])
    mask = np.ones(len(frame), dtype=bool)
    for item in ranges:
        start = pd.Timestamp(item["start_utc"], tz="UTC")
        end = pd.Timestamp(item["end_utc"], tz="UTC")
        mask &= ~frame.open_time.between(start, end)
    return frame.loc[mask].reset_index(drop=True)


def make_rehearsal_bars(config: dict, *, manifest: dict, contract: dict) -> pd.DataFrame:
    """Generate all 68 calendar months with the frozen gap layout."""
    entries = {row["month"]: row for row in manifest["months"]}
    months = tuple(config["months"]["warm_up"] + config["months"]["evaluate"])
    frames = [_drop_ranges(_month_frame(config, month, ordinal), entries[month])
              for ordinal, month in enumerate(months)]
    bars = pd.concat(frames, ignore_index=True)
    anomalies = {pd.Timestamp(item["open"], tz="UTC"): item for item in contract["close_time"]["anomalies"]}
    by_time = bars.set_index("open_time")
    for opened, spec in anomalies.items():
        if opened not in by_time.index:
            raise ValueError(f"Rehearsal anomaly falls in a synthetic gap: {opened.isoformat()}")
        by_time.loc[opened, "close_time"] = pd.Timestamp(spec["close"], tz="UTC")
        if spec["kind"] == "zero_trade_placeholder":
            by_time.loc[opened, ["open", "high", "low", "close"]] = float(by_time.loc[opened, "open"])
            by_time.loc[opened, ["volume", "number_of_trades", "taker_buy_base_asset_volume",
                                 "quote_asset_volume", "taker_buy_quote_asset_volume"]] = 0.0
    return by_time.reset_index().sort_values("open_time", kind="stable").reset_index(drop=True)


def _run_rehearsal():
    """One clean C1 process; full 224+56 computation on generated values."""
    g2.require_clean_commit("rehearsal")
    started = perf_counter()
    config, contract, manifest, hashes = g2.verify_contract_inputs()
    g2.check_disk(contract)
    root = g2.RESULTS_ROOT/"gate2-rehearsal"
    g2.require(not root.exists(), "Rehearsal already started")
    output = root/datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output.mkdir(parents=True, exist_ok=False)
    with g2.offline():
        bars = make_rehearsal_bars(config, manifest=manifest, contract=contract)
        g2.validate_calendar(bars, manifest["months"], contract)
        close = audit_close_times(bars, contract)
        bars = g2.project_keep_columns(bars, contract)
        structural, conflicts = g2.load_result_blind_inputs(contract)
        prepared = g2.prepare_fold_audits(bars, config, contract, conflicts, structural=structural,
                                         entries=manifest["months"], close_audit=close)
        g2.write_json(output/"rehearsal-prefit.json", prepared.audit)
        g2.write_json(output/"rehearsal-oracle.json", prepared.oracle_audit)
        g2.write_json(output/"close-time-audit.json", prepared.close_audit)
        g2.write_json(output/"sensitivity-prefit.json", prepared.sensitivity)
        summary = g2.execute_prepared(prepared, output, "rehearsal", started)
        g2.check_resources(contract, "rehearsal", started)
    return dict(status="rehearsal_passed", main_ridge_fits=summary["main_ridge_fits"],
                sensitivity_ridge_fits=summary["sensitivity_ridge_fits"])


def run_rehearsal():
    try:
        return _run_rehearsal()
    except OSError:
        raise
    except Exception as exc:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        if not g2.EVIDENCE_DIR.exists():
            g2.EVIDENCE_DIR.mkdir()
        g2.write_json(g2.EVIDENCE_DIR/f"stop-{stamp}.json",
                      dict(stage="rehearsal", error_type=type(exc).__name__, error=str(exc),
                           evidence_level=g2.REHEARSAL_EVIDENCE))
        raise


__all__ = ["make_rehearsal_bars", "run_rehearsal"]
