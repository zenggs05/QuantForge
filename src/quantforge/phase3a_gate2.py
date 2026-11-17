"""D-056/057 historical adapters; frozen predictors and inference stay unchanged."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from time import perf_counter
import tomllib
import urllib.request

import numpy as np
import pandas as pd

from quantforge import data, research_policy
from quantforge.features import build_features
from quantforge.labels import build_labels
from quantforge.datasets.historical_splits import make_historical_splits
from quantforge.phase3a_config import load_history_config
from quantforge.power_reanalysis import monthly_stat as _legacy_monthly_stat
from quantforge import phase3a_gate2_oracle as oracle

REPOSITORY_ROOT = data.REPOSITORY_ROOT.resolve()
CONTRACT_REL = "configs/phase3a-gate2-execution.toml"
CONTRACT_SHA = "0db8183ee1cbc6af80f429920794209d3ffd4cbe5b37d5ed8cdae94275c99419"
CONTRACT_PATH = REPOSITORY_ROOT / CONTRACT_REL
MANIFEST_REL = "data/processed/binance/spot/BTCUSDT/1m/historical/historical-manifest.json"
MANIFEST_PATH = REPOSITORY_ROOT / MANIFEST_REL
PARQUET_ROOT = MANIFEST_PATH.parent
EVIDENCE_DIR = REPOSITORY_ROOT / "docs/phase3a-gate2-evidence"
RESULTS_ROOT = REPOSITORY_ROOT / "results/phase3a"
LAUNCH_LEDGER = REPOSITORY_ROOT / ".tmp/g2-real-data-launch-ledger.jsonl"
MINUTE = pd.Timedelta(minutes=1)
CORE_EVIDENCE = "historical_external_validation_frozen_design_2026"
SENSITIVITY_EVIDENCE = "prespecified_data_quality_sensitivity"
REHEARSAL_EVIDENCE = "rehearsal_synthetic_engineering_only"


class Gate2Error(ValueError):
    """A Gate 2 invariant or scope check failed."""


class RealDataAccessError(ValueError):
    """The caller is outside the explicit real-data process gate."""


def require(condition, message):
    if not condition:
        raise Gate2Error(message)


def _hash(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _json_value(value):
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                       allow_nan=False, default=_json_value) + "\n").encode("utf-8")


def write_json(path, value):
    with Path(path).open("xb") as handle:
        handle.write(json_bytes(value))


def _safe_repo_path(path):
    path = Path(path)
    if not path.is_absolute():
        path = REPOSITORY_ROOT / path
    require(".." not in path.parts and path.is_relative_to(REPOSITORY_ROOT)
            and path.resolve() == path, "Gate 2 path escapes or redirects the worktree")
    return path


def load_contract(path=CONTRACT_PATH):
    path = _safe_repo_path(path)
    require(path == REPOSITORY_ROOT / CONTRACT_REL, "Gate 2 requires the fixed contract path")
    require(_hash(path) == CONTRACT_SHA, "Gate 2 contract SHA mismatch")
    return tomllib.loads(path.read_text(encoding="utf-8"))


def verify_contract_inputs(path=CONTRACT_PATH):
    """Call the frozen loader and verify every fingerprint before any parquet."""
    config = load_history_config()
    contract = load_contract(path)
    hashes = {CONTRACT_REL: CONTRACT_SHA}
    expected = dict(contract["frozen_sha256"])
    for prefix in ("structural_expectations", "sensitivity_conflicts"):
        expected[contract["contract"][prefix + "_file"]] = contract["contract"][prefix + "_sha256"]
    for relative, digest in expected.items():
        require(_hash(_safe_repo_path(relative)) == digest, "Frozen fingerprint mismatch: " + relative)
        hashes[relative] = digest
    manifest = json.loads(_safe_repo_path(MANIFEST_REL).read_text(encoding="utf-8"))
    require([r["month"] for r in manifest["months"]] ==
            config["months"]["warm_up"] + config["months"]["evaluate"], "Manifest month order mismatch")
    return config, contract, manifest, hashes


def load_result_blind_inputs(contract):
    table = json.loads(_safe_repo_path(contract["contract"]["structural_expectations_file"]).read_text(encoding="utf-8"))
    conflicts = pd.read_csv(_safe_repo_path(contract["contract"]["sensitivity_conflicts_file"]))
    require(len(conflicts) == contract["sensitivity"]["conflict_minutes"], "Conflict count mismatch")
    require(set(conflicts.classification) <= set(contract["sensitivity"]["conflict_classes"]),
            "Unexpected conflict class")
    times = pd.DatetimeIndex(pd.to_datetime(conflicts.open_time_utc, utc=True)).as_unit("ns")
    require(times.is_unique and times.is_monotonic_increasing, "Conflict times must be sorted and unique")
    require(np.array_equal(times.asi8 // 1000, conflicts.open_time_us.to_numpy()), "Conflict timestamp encoding mismatch")
    return table, times


def _require_real_gate():
    if os.environ.get("QF_GATE2_REAL_DATA") != "1":
        raise RealDataAccessError("real historical loader requires QF_GATE2_REAL_DATA=1")
    require("PYTEST_CURRENT_TEST" not in os.environ, "Tests cannot open the real-data gate")


def guard_times(times):
    index = pd.DatetimeIndex(times)
    require(index.tz is not None and str(index.tz) == "UTC" and not index.hasnans,
            "Historical timestamps must be aware UTC")
    require(len(index) > 0 and index.is_unique and index.is_monotonic_increasing
            and index.equals(index.floor("min")), "Historical grid must be sorted, unique minutes")
    require(index[0] >= research_policy.HISTORICAL_START
            and index[-1] < research_policy.HISTORICAL_END, "Historical timestamps outside authorized bounds")
    return index.as_unit("ns")


def _month_path(month):
    research_policy.ensure_historical_month(month)
    start = pd.Timestamp(month + "-01", tz="UTC")
    research_policy.ensure_not_holdout(start, start + pd.offsets.MonthBegin(1))
    return _safe_repo_path(PARQUET_ROOT / (month + ".parquet"))


def project_keep_columns(frame, contract):
    keep = contract["data"]["keep_columns"]
    require(set(keep) <= set(frame), "Missing historical input columns")
    return frame.loc[:, keep].copy()


def audit_close_times(frame, contract, *, months=None, require_raw=False):
    """Vectorized timing audit; only anomaly rows expose OHLC/volume/trades."""
    opened = guard_times(frame.open_time)
    closed = pd.DatetimeIndex(frame.close_time).as_unit("ns")
    require(closed.tz is not None and str(closed.tz) == "UTC" and not closed.hasnans,
            "Close timestamps must be aware UTC")
    ms = opened < pd.Timestamp("2025-01-01", tz="UTC")
    offset = np.where(ms, 59_999_000_000, 59_999_999_000)
    if require_raw:
        require({"open_time_raw", "close_time_raw"} <= set(frame), "Raw timestamp integers missing")
    scale = np.where(ms, 1_000_000, 1000)
    for name, index in (("open_time_raw", opened), ("close_time_raw", closed)):
        if name in frame:
            raw = pd.to_numeric(frame[name], errors="raise").to_numpy(dtype=np.int64)
            require(np.array_equal(raw * scale, index.asi8), "Raw timestamp disagrees with UTC column: " + name)
    require(bool(np.all(closed.asi8 < opened.asi8 + MINUTE.value)),
            "close_time is not before the 60 second boundary")
    abnormal = closed.asi8 != opened.asi8 + offset
    supplied_months = set(opened.strftime("%Y-%m")) if months is None else set(months)
    expected = {pd.Timestamp(r["open"]): r for r in contract["close_time"]["anomalies"]
                if r["open"][:7] in supplied_months}
    require(set(opened[abnormal]) == set(expected), "close_time anomaly whitelist mismatch")
    rows = []
    for position in np.flatnonzero(abnormal):
        row, stamp = frame.iloc[position], opened[position]
        spec = expected[stamp]
        require(closed[position] == pd.Timestamp(spec["close"]), "close_time anomaly value mismatch")
        zero = row.number_of_trades == 0 and row.volume == 0 and row.open == row.high == row.low == row.close
        kind = "zero_trade_placeholder" if zero else "truncated_with_trades"
        require(kind == spec["kind"], "close_time anomaly kind mismatch")
        rows.append(dict(open=stamp.isoformat(), close=closed[position].isoformat(), kind=kind,
                         measurement_advance_ns=int(stamp.value + offset[position] - closed[position].value),
                         close_before_open=bool(closed[position] < stamp)))
    return dict(rows=len(frame), normal_rows=int((~abnormal).sum()), anomaly_rows=len(rows),
                anomalies=rows, all_information_before_frozen_availability=True)


def validate_calendar(frame, entries, contract):
    index = guard_times(frame.open_time)
    require(len(index) == contract["data"]["rows"], "Historical row count mismatch")
    require(index[0] == pd.Timestamp(contract["data"]["start"])
            and index[-1] == pd.Timestamp(contract["data"]["end_exclusive"]) - MINUTE,
            "Historical endpoints mismatch")
    observed_gaps = []
    for i in np.flatnonzero(np.diff(index.asi8) > MINUTE.value):
        observed_gaps.append((index[i] + MINUTE, index[i + 1] - MINUTE))
    expected_gaps = [(pd.Timestamp(g["start_utc"]), pd.Timestamp(g["end_utc"]))
                     for row in entries for g in row["gap_ranges"]]
    require(observed_gaps == expected_gaps, "Historical gap locations differ from manifest")
    missing = sum(int((b-a)/MINUTE)+1 for a, b in observed_gaps)
    require(len(observed_gaps) == contract["data"]["gap_ranges"]
            and missing == contract["data"]["missing_minutes"], "Historical gap totals mismatch")
    months = index.strftime("%Y-%m")
    boundaries = np.flatnonzero(months[1:] != months[:-1])
    require(len(boundaries) == len(entries)-1 and
            all(index[i+1]-index[i] == MINUTE for i in boundaries), "Cross-month boundary gap")
    for row in entries:
        require(int((months == row["month"]).sum()) == row["rows"], "Manifest month row count mismatch")


def load_historical_bars(*, contract_path=CONTRACT_PATH):
    """The only real parquet reader; no flag or synthetic bypass is exposed."""
    _require_real_gate()
    config, contract, manifest, hashes = verify_contract_inputs(contract_path)
    paths = []
    for entry in manifest["months"]:
        path = _month_path(entry["month"])
        require(_hash(path) == entry["parquet_sha256"], "Frozen parquet SHA mismatch: " + entry["month"])
        paths.append(path)
    frames, close_rows = [], []
    for entry, path in zip(manifest["months"], paths, strict=True):
        frame = pd.read_parquet(path)
        times = guard_times(frame.open_time)
        require(bool((times.strftime("%Y-%m") == entry["month"]).all()), "Parquet contains a different month")
        audit = audit_close_times(frame, contract, months=[entry["month"]], require_raw=True)
        close_rows.extend(audit["anomalies"])
        frames.append(project_keep_columns(frame, contract))
    bars = pd.concat(frames, ignore_index=True)
    validate_calendar(bars, manifest["months"], contract)
    require(len(close_rows) == len(contract["close_time"]["anomalies"]), "Incomplete C-1 audit")
    return bars, dict(config=config, contract=contract, manifest=manifest, hashes=hashes,
                      close_time=dict(rows=len(bars), anomalies=close_rows,
                                      all_information_before_frozen_availability=True))


def index_sha(index):
    return hashlib.sha256(pd.DatetimeIndex(index).as_unit("ns").asi8.astype("<i8").tobytes()).hexdigest()


def segments(index):
    index = pd.DatetimeIndex(index).as_unit("ns")
    if len(index) == 0:
        return []
    group = pd.Series(index, index=index).diff().ne(MINUTE).cumsum()
    return [pd.DatetimeIndex(part) for _, part in pd.Series(index, index=index).groupby(group, sort=False)]


def lag(n, config):
    return max(config["inference"]["llsw_min_lag"], math.ceil(config["inference"]["llsw_constant"] * math.sqrt(n)))


def prune(index, config, reason):
    index = pd.DatetimeIndex(index).as_unit("ns")
    threshold = lag(len(index), config)
    retained = np.ones(len(index), dtype=bool)
    removed, offset = [], 0
    for part in segments(index):
        if len(part) <= threshold:
            retained[offset:offset+len(part)] = False
            removed.append(dict(start=part[0].isoformat(), end=part[-1].isoformat(),
                                length=len(part), reason=reason, threshold=threshold))
        offset += len(part)
    return index[retained], removed


def conflict_mask(index, conflicts):
    """Range marking, independent of the oracle's binary-search algorithm."""
    index = pd.DatetimeIndex(index).as_unit("ns")
    mask = np.zeros(len(index), dtype=bool)
    for point in pd.DatetimeIndex(conflicts):
        left = index.searchsorted(point - 15*MINUTE, side="left")
        right = index.searchsorted(point + 60*MINUTE, side="right")
        mask[left:right] = True
    return mask


def build_sensitivity_indices(index, conflicts, *, config, train=None, evaluation=None):
    train_index = pd.DatetimeIndex(index if train is None else train).as_unit("ns")
    eval_index = pd.DatetimeIndex(index if evaluation is None else evaluation).as_unit("ns")
    tr = train_index[~conflict_mask(train_index, conflicts)]
    post = eval_index[~conflict_mask(eval_index, conflicts)]
    ev, removed = prune(post, config, "S_DQ_post_mask_short_segment")
    return dict(train=tr, evaluation_pre_pf6=post, evaluation=ev,
                train_masked=len(train_index)-len(tr), eval_masked=len(eval_index)-len(post),
                removed_segments=removed, lag=lag(len(ev), config))


def describe_fold(fold, final, removed, config):
    return {**fold.audit, "raw_eval_samples": len(fold.eval_indices), "eval_samples": len(final),
            "pre_pf6_first_eval_decision": fold.audit["first_eval_decision"],
            "pre_pf6_last_eval_label_available": fold.audit["last_eval_label_available"],
            "first_eval_decision": None if not len(final) else (final[0]+pd.Timedelta(seconds=61)).isoformat(),
            "last_eval_label_available": None if not len(final) else (final[-1]+pd.Timedelta(minutes=fold.audit["horizon_minutes"]+1, seconds=1)).isoformat(),
            "eval_removed_total": fold.audit["candidate_eval_samples"]-len(final),
            "train_ids_sha256": index_sha(fold.train_indices), "eval_ids_sha256": index_sha(final),
            "pre_pf6_eval_ids_sha256": index_sha(fold.eval_indices),
            "eval_segment_lengths": [len(p) for p in segments(final)],
            "llsw_lag": lag(len(final), config), "pf6_removed_segments": removed}


def assert_n_train(audits, minimum=50_000):
    for row in audits:
        require(row["train_samples"] >= minimum, "n_train below frozen minimum: " + row["month"])


def assert_pf6(audits, config):
    for row in audits:
        if row["horizon_minutes"] == 15:
            lengths = row["eval_segment_lengths"]
            require(bool(lengths) and min(lengths) > row["llsw_lag"], "PF-6 short evaluation segment: " + row["month"])


def segment_monthly_stat(values, times, lag_value, alternative="two-sided"):
    """Normalize the unmodified monthly_stat output, including null rows."""
    parts = segments(times)
    n = len(times)
    if values is None:
        raw = dict(n=n, n_samples=n, mean=None, variance=None, se=None, t=None, z=None,
                   p_value=None, gamma0=None, n_eff=None, iid_variance=None,
                   status="not_estimable", reason="constant_ic_input", alternative=alternative)
    else:
        raw = _legacy_monthly_stat(np.asarray(values, dtype=np.float64), pd.DatetimeIndex(times),
                                   lag_value, alternative)
    out = {k: raw.get(k) for k in ("mean", "variance", "se", "t", "z", "p_value", "status", "reason",
                                   "alternative", "n", "n_samples", "gamma0", "n_eff", "iid_variance")}
    out.update(lag=lag_value, lrv=None if raw["variance"] is None else raw["variance"]*n,
               segment_count=len(parts), min_segment_length=min(map(len, parts)),
               segment_weights_json=json.dumps(raw.get("weights", [1.0]), separators=(",", ":")),
               cross_segment_covariance="assumed zero",
               segment_rule="independent_continuous_segments_zero_cross_segment_covariance")
    return out


monthly_stat = segment_monthly_stat


def relabel_placebo(rows, contract):
    for row in rows:
        row["placebo_call_label"] = contract["evidence_labels"]["placebo_call_label"]
        row["evidence_level"] = contract["evidence_labels"]["placebo"]
    return rows


def interpretation(primary, dep1, dep2, dep3, sensitivity=None, *, alpha=.05, z_one_sided=1.6448536269514722):
    reject = primary.get("p_value") is not None and primary["p_value"] < alpha
    finite = lambda x: x is not None and np.isfinite(x)
    fr1 = (not dep1 or not finite(dep1.get("p_value")) or not finite(dep1.get("mean"))
           or dep1["p_value"] >= alpha or dep1["mean"] <= 0)
    rows = list(dep2 or [])
    fr2 = not rows or any(not finite(r.get("ci_low")) or r["ci_low"] <= 0 for r in rows)
    eq, nw = (dep3 or {}).get("month_equal", {}), (dep3 or {}).get("n_weighted", {})
    fr3 = (not all(finite(x) for x in (eq.get("t"), eq.get("mean"), nw.get("mean")))
           or eq["t"] < z_one_sided or np.sign(eq["mean"]) != np.sign(nw["mean"]))
    sensitive = (sensitivity is None or sensitivity.get("p_value") is None
                 or (sensitivity["p_value"] < alpha) != reject)
    return dict(primary_reject=bool(reject), FR1=bool(fr1), FR2=bool(fr2), FR3=bool(fr3),
                classification="R-A" if reject and not (fr1 or fr2) else "R-B" if reject else "R-C",
                sensitivity="data_source_sensitive" if sensitive else "data_source_robust",
                FR1_values=dep1, FR2_values=rows, FR3_values=dep3, changes_primary_decision=False)


@dataclass
class Prepared:
    bars: pd.DataFrame
    features: object
    labels: dict
    config: dict
    contract: dict
    conflicts: pd.DatetimeIndex
    audit: dict
    oracle_audit: dict
    close_audit: dict
    sensitivity: dict

    def folds(self, horizon):
        metadata = self.features.metadata.join(self.labels[horizon].frame.drop(columns="target_return"))
        original = make_historical_splits(metadata, tuple(self.config["months"]["evaluate"]), horizon, "month_end")
        result = []
        for fold in original:
            ev, removed = prune(fold.eval_indices, self.config, "D057_undefined_feature_short_segment") if horizon == 15 else (fold.eval_indices, [])
            result.append(replace(fold, eval_indices=ev))
        return result


def prepare_fold_audits(bars, config, contract, conflicts, *, structural=None, entries=None, close_audit=None):
    """Build features/labels once and independently validate all PF counters."""
    guard_times(bars.open_time)
    features = build_features(bars)
    labels = {h: build_labels(bars, h) for h in (*config["experiment"]["horizons"], 1)}
    state = oracle.validity(bars)
    require(np.array_equal(features.metadata.valid_features, state["valid"]), "PF-1 feature validity mismatch")
    structural_valid = features.metadata.feature_exclusion_reason.ne("insufficient_contiguous_history").to_numpy()
    require(np.array_equal(structural_valid, state["structural"]), "PF-1 history validity mismatch")
    undefined = features.metadata.feature_exclusion_reason.eq("undefined_ratio_or_variance")
    cause_columns = dict(previous_zero_volume="volume_change_1m", zero_volume="taker_buy_volume_ratio",
                         zero_price_range="close_range_position", zero_historical_volume_variance="volume_zscore_60m",
                         zero_trades_reference="trades_change_60m")
    reasons = [dict(bar_open=t.isoformat(), subreasons=[k for k, c in cause_columns.items()
                                                       if pd.isna(features.frame.at[t, c])])
               for t in features.metadata.index[undefined]]
    require(reasons == state["reasons"], "PF-1 undefined subreason mismatch")
    conflict_index = pd.DatetimeIndex(conflicts).as_unit("ns")
    index = features.metadata.index
    require(conflict_index.isin(index).all(), "PF-8 conflict absent from source")
    require(np.array_equal(conflict_mask(index, conflict_index), oracle.conflict_mask(index, conflict_index)),
            "PF-8 full mask mismatch")
    references = {} if structural is None else {(r["month"], r["horizon"]): r for r in structural}
    actual_rows, oracle_rows, dq_rows, null_rows = [], [], [], []
    structural_eval_masked, structural_affected, structural_shorts = 0, 0, []
    for horizon in config["experiment"]["horizons"]:
        metadata = features.metadata.join(labels[horizon].frame.drop(columns="target_return"))
        require(np.array_equal(labels[horizon].frame.valid_label, state["remaining"] >= horizon), "PF-1 label validity mismatch")
        original = make_historical_splits(metadata, tuple(config["months"]["evaluate"]), horizon, "month_end")
        for fold in original:
            month = fold.audit["month"]
            otr, oev, oaudit = oracle.fold_sets(state, month, horizon)
            require(fold.audit == oaudit and fold.train_indices.equals(otr) and fold.eval_indices.equals(oev),
                    "PF-1 frozen fold/oracle mismatch: " + month + "/" + str(horizon))
            str_tr, str_ev, str_audit = oracle.fold_sets(state, month, horizon, structural=True)
            undefined_ids = index[undefined]
            require(fold.train_indices.equals(str_tr.difference(undefined_ids))
                    and fold.eval_indices.equals(str_ev.difference(undefined_ids)), "PF-2 D* set mismatch")
            reference = references.get((month, horizon))
            if structural is not None:
                require(reference is not None, "Structural fold missing")
                calculated = {**str_audit, "train_ids_sha256": index_sha(str_tr), "eval_ids_sha256": index_sha(str_ev),
                              "eval_segment_lengths": [len(p) for p in segments(str_ev)], "llsw_lag": lag(len(str_ev), config)}
                for key, value in reference.items():
                    if key not in ("month", "horizon"):
                        require(calculated[key] == value, "PF-2 structural mismatch: " + month + "/" + str(horizon) + "/" + key)
                for key in ("candidate_eval_samples", "candidate_train_samples", "eval_invalid_labels",
                            "eval_labels_after_cutoff", "train_invalid_labels"):
                    require(fold.audit[key] == reference[key], "PF-2 invariant counter mismatch")
            ev, removed = prune(fold.eval_indices, config, "D057_undefined_feature_short_segment") if horizon == 15 else (fold.eval_indices, [])
            row = describe_fold(fold, ev, removed, config)
            row.update(D_star_train=len(str_tr)-len(fold.train_indices), D_star_eval=len(str_ev)-len(fold.eval_indices),
                       structural_train_ids_sha256=index_sha(str_tr), structural_eval_ids_sha256=index_sha(str_ev),
                       structural_audit=str_audit)
            for name, ids in (("D_star_train_rows", str_tr), ("D_star_eval_rows", str_ev)):
                selected_reasons = []
                for item in reasons:
                    stamp = pd.Timestamp(item["bar_open"])
                    pos = ids.searchsorted(stamp)
                    if pos < len(ids) and ids[pos] == stamp:
                        selected_reasons.append(item)
                row[name] = selected_reasons
            oracle_row, oracle_dq = oracle.audit_fold(state, month, horizon, config, conflict_index)
            require(row == oracle_row, "PF-1 final audit mismatch")
            actual_rows.append(row)
            oracle_rows.append(oracle_row)
            for bandwidth in config["inference"]["lags"]:
                bandwidth_lag = lag(len(ev), config) if bandwidth == "LLSW" else bandwidth
                if any(len(p) <= bandwidth_lag for p in segments(ev)):
                    null_rows.append(dict(month=month, horizon_minutes=horizon, bandwidth=str(bandwidth),
                                          lag=bandwidth_lag, reason="short_or_undefined_segment"))
            if horizon == 15:
                sd = build_sensitivity_indices(index, conflict_index, config=config, train=fold.train_indices, evaluation=ev)
                dq = dict(month=month, train_samples=len(sd["train"]), eval_samples=len(sd["evaluation"]),
                          train_masked=sd["train_masked"], eval_masked=sd["eval_masked"],
                          post_mask_eval_samples=len(sd["evaluation_pre_pf6"]), short_segments=sd["removed_segments"],
                          train_ids_sha256=index_sha(sd["train"]), eval_ids_sha256=index_sha(sd["evaluation"]),
                          eval_segment_lengths=[len(p) for p in segments(sd["evaluation"])], llsw_lag=sd["lag"])
                require(dq == oracle_dq, "PF-8 sensitivity/oracle mismatch")
                dq_rows.append(dq)
                masked = conflict_mask(str_ev, conflict_index)
                structural_eval_masked += int(masked.sum())
                structural_affected += int(masked.any())
                _, short = prune(str_ev[~masked], config, "structural")
                structural_shorts.extend([dict(month=month, **item) for item in short])
        del original, metadata
    assert_n_train(actual_rows, contract["gates"]["n_train_min"])
    assert_n_train(dq_rows, contract["gates"]["n_train_min"])
    assert_pf6(actual_rows, config)
    for row in dq_rows:
        require(bool(row["eval_segment_lengths"]) and min(row["eval_segment_lengths"]) > row["llsw_lag"],
                "PF-8 final sensitivity short segment")
    structural_counts = dict(masked_valid_bars_all_months=int((structural_valid & (state["remaining"] >= 15) & conflict_mask(index, conflict_index)).sum()),
                             masked_eval_bars=structural_eval_masked, eval_months_affected=structural_affected,
                             short_segments_dropped=len(structural_shorts))
    if structural is not None:
        expected = contract["sensitivity"]["expected_structural"]
        for key, value in structural_counts.items():
            require(value == expected[key], "PF-8 gap-only structural sensitivity mismatch: " + key)
        if expected["short_segments_dropped"]:
            require(structural_shorts[0]["month"] == expected["short_segment_month"]
                    and structural_shorts[0]["length"] == expected["short_segment_bars"], "PF-8 structural short segment mismatch")
    calendar, month_ids = [], index.strftime("%Y-%m")
    for month in config["months"]["warm_up"] + config["months"]["evaluate"]:
        member = month_ids == month
        count = int(member.sum())
        if not count:
            continue
        undefined_n = int((undefined.to_numpy() & member).sum())
        require(undefined_n/count <= contract["gates"]["feature_undefined_ratio_max_per_calendar_month"],
                "PF-5 undefined feature ratio exceeded: " + month)
        start = pd.Timestamp(month + "-01", tz="UTC")
        expected_n = int((start + pd.offsets.MonthBegin(1)-start)/MINUTE)
        missing_ranges = [] if entries is None else next(r["gap_ranges"] for r in entries if r["month"] == month)
        calendar.append(dict(month=month, expected_minutes=expected_n, present_minutes=count,
                             missing_minutes=expected_n-count, gap_ranges=missing_ranges,
                             insufficient_contiguous_history=int((~structural_valid & member).sum()),
                             undefined_ratio_or_variance=undefined_n,
                             undefined_rows=[r for r in reasons if r["bar_open"][:7] == month]))
    close = dict(close_audit or {})
    for anomaly in close.get("anomalies", []):
        t = pd.Timestamp(anomaly["open"])
        anomaly["valid_features"] = bool(features.metadata.at[t, "valid_features"])
        anomaly["feature_exclusion_reason"] = features.metadata.at[t, "feature_exclusion_reason"]
        anomaly["label_validity"] = {str(h): bool(label.frame.at[t, "valid_label"]) for h, label in labels.items()}
        anomaly["label_close_endpoints"] = {str(h): [u.isoformat() for u in (t, t-h*MINUTE)
            if u in index and bool(label.frame.at[u, "valid_label"])] for h, label in labels.items()}
        anomaly["following_60_feature_inputs"] = [u.isoformat() for u in index[(index > t) & (index <= t+60*MINUTE)]]
        anomaly["interpretation"] = "measurement_time_advanced; availability remains derived only from open_time"
        if t == pd.Timestamp("2020-12-21T14:09:00Z"):
            anomaly["role_note"] = "warmup only; candidate for training at fit_time >= 2021-01-01, validity gates retained"
    sensitivity = dict(folds=dq_rows, structural_counts=structural_counts, structural_short_segments=structural_shorts,
                       actual_masked_valid_bars=int((features.metadata.valid_features.to_numpy() & labels[15].frame.valid_label.to_numpy() & conflict_mask(index, conflict_index)).sum()),
                       differences_accounted_for_by="D_star_and_D057_main_evaluation_pruning")
    audit = dict(folds=actual_rows, calendar=calendar, undefined_rows=reasons, anticipated_null_rows=null_rows,
                 expected_prediction_rows=2*sum(r["eval_samples"] for r in actual_rows),
                 gates={f"PF-{i}": True for i in range(1,9)})
    return Prepared(bars, features, labels, config, contract, conflict_index, audit,
                    dict(folds=oracle_rows, undefined_rows=state["reasons"]), close, sensitivity)


def write_csv(path, rows, config):
    frame = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    with Path(path).open("x", encoding="utf-8", newline="") as handle:
        frame.to_csv(handle, index=False, float_format=config["output"]["float_format"], lineterminator="\n")


def check_disk(contract):
    free = shutil.disk_usage(REPOSITORY_ROOT.anchor).free
    require(free >= contract["resources"]["disk_free_bytes_min"], "Gate 2 disk reserve exceeded")
    return free


def git(*arguments):
    return subprocess.check_output(["git", *arguments], cwd=REPOSITORY_ROOT).decode("utf-8").strip()


def runtime_fields(mode, output):
    if mode == "real":
        mode = output.name.split("-")[0]
    return dict(mode=mode, output_directory=str(output), utc_timestamps=[datetime.now(timezone.utc).isoformat()],
                head_sha=git("rev-parse", "HEAD"), pid=os.getpid(),
                disk_free_bytes=shutil.disk_usage(REPOSITORY_ROOT.anchor).free)


def check_resources(contract, mode, started):
    from quantforge.phase3a_runner import peak_process_bytes
    fraction = contract["resources"]["rehearsal_fraction_max"] if mode == "rehearsal" else 1
    require(perf_counter()-started <= fraction*contract["resources"]["run_seconds_max"],
            mode + " computation time budget exceeded")
    require(peak_process_bytes() <= fraction*contract["resources"]["peak_process_bytes_max"],
            mode + " computation memory budget exceeded")
    check_disk(contract)


def complete_analysis(prepared, output, primary, dependence, placebo, secondary, concentration,
                      primary_months, mode, started):
    """Run exactly the separate S-DQ fits through the frozen fold fitter."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from quantforge import phase3a_runner as runner
    from quantforge.diagnostics import forecast_tests as ft
    config, contract = prepared.config, prepared.contract
    evidence = contract["evidence_labels"]["rehearsal" if mode == "rehearsal" else "sensitivity"]
    rows, models, actual_audit = [], [], []
    fits = 0
    with (output/"sensitivity-predictions.parquet").open("xb") as handle:
        writer = None
        try:
            for fold in prepared.folds(15):
                require(fits < contract["gates"]["sensitivity_ridge_fits_per_run"], "S-DQ fit budget exceeded")
                selected = build_sensitivity_indices(prepared.features.metadata.index, prepared.conflicts,
                                                    config=config, train=fold.train_indices, evaluation=fold.eval_indices)
                dq_fold = replace(fold, train_indices=selected["train"], eval_indices=selected["evaluation"])
                month = fold.audit["month"]
                audited = next(r for r in prepared.sensitivity["folds"] if r["month"] == month)
                require(index_sha(dq_fold.train_indices) == audited["train_ids_sha256"]
                        and index_sha(dq_fold.eval_indices) == audited["eval_ids_sha256"], "S-DQ fit index changed")
                f, truth, state = runner.fit_predict_fold(prepared.features, prepared.labels[15], dq_fold,
                                                         config["specification"]["v1_features"], config)
                fits += 1
                y = truth.to_numpy(dtype=np.float64)
                times = pd.DatetimeIndex(prepared.features.metadata.loc[dq_fold.eval_indices, "decision_time"])
                state.update(specification="v1", horizon_minutes=15, month=month, evidence_level=evidence)
                models.append(state)
                record = pd.DataFrame(dict(bar_open_time=dq_fold.eval_indices, decision_time=times,
                                           label_available_time=prepared.labels[15].frame.loc[dq_fold.eval_indices, "label_available_time"].to_numpy(),
                                           prediction=f, target_return=y, historical_mean=state["train_target_mean"],
                                           model_version=state["model_version"], fit_time=dq_fold.fit_time,
                                           month=month, horizon_minutes=15, specification="v1", evidence_level=evidence))
                table = pa.Table.from_pandas(record, preserve_index=False)
                if writer is None:
                    writer = pq.ParquetWriter(handle, table.schema, compression="zstd")
                writer.write_table(table)
                key = dict(month=month, specification="v1", horizon_minutes=15, baseline="zero",
                           specification_evidence_level="design_frozen_v1")
                candidates = runner._monthly(y, f, np.zeros(len(y)), times, key, config, mode=mode, evidence=evidence)
                rows.append(next(r for r in candidates if r["metric"] == "CW" and r["bandwidth"] == "LLSW"))
                actual_audit.append(audited)
                check_resources(contract, mode, started)
        finally:
            if writer is not None:
                writer.close()
    require(fits == contract["gates"]["sensitivity_ridge_fits_per_run"], "S-DQ fit count mismatch")
    pooled = runner._pool(rows, config, evidence=evidence)
    endpoint = runner._select(pooled, "v1", 15, "zero", "CW")
    decisions = interpretation(primary, dependence["S_dep1"], dependence["S_dep2"], dependence["S_dep3"],
                               endpoint, alpha=config["inference"]["alpha"],
                               z_one_sided=contract["interpretation"]["z_one_sided"])
    sensitivity = dict(evidence_level=evidence, primary=endpoint, pooling=pooled,
                       reject=None if endpoint["p_value"] is None else endpoint["p_value"] < config["inference"]["alpha"],
                       flag=decisions["sensitivity"], changes_primary_decision=False,
                       folds=actual_audit, structural_reconciliation=prepared.sensitivity)
    write_json(output/"sensitivity-dq.json", sensitivity)
    write_json(output/"sensitivity-models.json", models)
    write_json(output/"sensitivity-fold-audit.json", actual_audit)
    write_csv(output/"sensitivity-dq-monthly.csv", rows, config)
    subperiods = runner._descriptive_subperiods(primary_months)
    ratios = [primary["se"]/reference for reference in contract["interpretation"]["se_calibration"]]
    decisions.update(evidence_level=contract["evidence_labels"]["rehearsal" if mode == "rehearsal" else "core"],
                     primary=primary, dm=secondary[3],
                     delta_mse=secondary[3]["model_mse"]-secondary[3]["baseline_mse"],
                     placebo=placebo,
                     placebo_pipeline_warning=any(r.get("pipeline_warning") for r in placebo),
                     primary_se_ratios=ratios,
                     se_ratio_highlight=any(r < .5 or r > 2 for r in ratios),
                     descriptive_ex_post_mde=ft.mde(primary["se"], config["inference"]["alpha"], config["power"]["target_power"]),
                     mde_label="descriptive_ex_post",
                     concentration_sign_flipped_months=len({r["month"] for r in concentration if r["sign_flipped"]}),
                     negative_years=sum(r["cw_mean"] < 0 for r in subperiods if r["period_type"] == "year"),
                     negative_half_years=sum(r["cw_mean"] < 0 for r in subperiods if r["period_type"] == "half_year"),
                     S1_evidence_tag=config["specification"]["v2_evidence_tag"],
                     assumption_statement=contract["hac"]["assumption_statement"],
                     R_B_wording=contract["interpretation"]["R_B_wording"] if decisions["classification"] == "R-B" else None)
    write_json(output/"interpretation.json", decisions)
    hashes = dict(historical_manifest_sha256=contract["frozen_sha256"][MANIFEST_REL],
                  contract_sha256=CONTRACT_SHA,
                  structural_expectations_sha256=contract["contract"]["structural_expectations_sha256"],
                  sensitivity_conflicts_sha256=contract["contract"]["sensitivity_conflicts_sha256"],
                  prefit_audit_sha256=_hash(EVIDENCE_DIR/"prefit-audit.json") if mode == "real" else None)
    return dict(**hashes, main_ridge_fits=config["budget"]["ridge_fits_per_run"],
                sensitivity_ridge_fits=fits, total_ridge_fits=config["budget"]["ridge_fits_per_run"]+fits,
                interpretation=decisions, sensitivity_flag=decisions["sensitivity"])


def isolation_snapshot():
    """Read size/mtime metadata only for the explicitly authorized roots."""
    result = {}
    for sibling in ("QuantForge", "QuantForge-2f"):
        for leaf in (".venv", "results", "data"):
            base = REPOSITORY_ROOT.parent/sibling/leaf
            rows = []
            for directory, folders, files in os.walk(base, followlinks=False):
                folders.sort()
                for name in sorted(files):
                    path = Path(directory)/name
                    stat = path.stat()
                    rows.append((path.relative_to(base).as_posix(), stat.st_size, stat.st_mtime_ns))
            result[f"{sibling}/{leaf}"] = dict(files=len(rows), bytes=sum(r[1] for r in rows),
                                                size_mtime_sha256=hashlib.sha256(json_bytes(rows)).hexdigest())
    return result


@contextmanager
def offline():
    """Prevent accidental network calls before any historical processing."""
    def denied(*args, **kwargs):
        raise Gate2Error("Network attempt prohibited in Gate 2")
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
    original_create, original_urlopen = socket.create_connection, urllib.request.urlopen
    socket.socket.connect, socket.socket.connect_ex = denied, denied
    socket.create_connection, urllib.request.urlopen = denied, denied
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex = original_connect, original_connect_ex
        socket.create_connection, urllib.request.urlopen = original_create, original_urlopen


def append_launch(event):
    with LAUNCH_LEDGER.open("ab") as handle:
        handle.write((json.dumps(event, ensure_ascii=True, sort_keys=True) + "\n").encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())


def launch_begin(stage):
    _require_real_gate()
    prior = [json.loads(line) for line in LAUNCH_LEDGER.read_text(encoding="utf-8").splitlines()] if LAUNCH_LEDGER.exists() else []
    starts, ends = [r for r in prior if r["event"] == "start"], [r for r in prior if r["event"] == "end"]
    require(len(starts) == len(ends), "Previous real process did not finish")
    require(len(starts) < 3 and stage == ("prefit", "formal", "reproduction")[len(starts)],
            "Real-data process count or order exceeded")
    require(all(r["exit_code"] == 0 for r in ends), "Previous real process is terminal")
    if stage == "reproduction":
        require(git("rev-parse", "HEAD") == starts[-1]["head"], "Reproduction HEAD differs from formal C2")
    event = dict(event="start", launch=len(starts)+1, stage=stage, argv=sys.argv,
                 utc=datetime.now(timezone.utc).isoformat(), head=git("rev-parse", "HEAD"), exit_code=None)
    append_launch(event)
    return event


def launch_end(event, rc):
    append_launch({**event, "event": "end", "utc": datetime.now(timezone.utc).isoformat(), "exit_code": rc})


def require_clean_commit(stage):
    require(git("status", "--porcelain") == "", "Gate 2 execution requires a clean worktree")
    expected = "docs: phase3a gate2 prefit audit and rehearsal (no fitting)" if stage in ("formal", "reproduction") else "feat: phase3a gate2 real-data adapter, segment HAC, S-DQ mask and prefit audit (no real fitting)"
    require(git("log", "-1", "--format=%s") == expected, "Gate 2 HEAD is not the required C1/C2 anchor")
    if stage in ("formal", "reproduction"):
        require(git("status", "--porcelain", "--untracked-files=all") == "", "Formal worktree is not clean")
        prefit_runtime = json.loads((EVIDENCE_DIR/"runtime-prefit.json").read_text(encoding="utf-8"))
        require(git("rev-parse", "HEAD^") == prefit_runtime["head_sha"], "C2 parent differs from prefit C1")


def artifact_index(output):
    return {p.name: dict(bytes=p.stat().st_size, sha256=_hash(p))
            for p in sorted(output.iterdir()) if p.is_file() and p.name != "runtime.json"}


def execute_prepared(prepared, output, mode, started):
    from threadpoolctl import threadpool_limits
    from quantforge.phase3a_runner import _execute_synthetic_inner
    with threadpool_limits(limits=prepared.config["experiment"]["cpu_threads"]):
        summary = _execute_synthetic_inner(prepared.config, output, mode=mode, prepared=prepared, started_at=started)
    expected = {"monthly-tests.csv": 4032, "pooled-tests.csv":144, "secondary-tests.csv":5,
                "concentration.csv":168, "encompassing.csv":56, "placebo.csv":6, "subperiods.csv":71,
                "gw-instrument.csv":56, "conditional-gw.csv":2, "stability.csv":45, "power-planning.csv":12,
                "sensitivity-dq-monthly.csv":56}
    for name, count in expected.items():
        require(len(pd.read_csv(output/name)) == count, "Artifact row count mismatch: " + name)
    require(summary["total_ridge_fits"] == prepared.contract["gates"]["total_ridge_fits_per_run"], "Total fit count mismatch")
    import pyarrow.parquet as pq
    require(pq.read_metadata(output/"predictions.parquet").num_rows == prepared.audit["expected_prediction_rows"],
            "Prediction row count mismatch")
    require(pq.read_metadata(output/"sensitivity-predictions.parquet").num_rows ==
            sum(r["eval_samples"] for r in prepared.sensitivity["folds"]), "S-DQ prediction row count mismatch")
    write_json(output/"artifact-index.json", artifact_index(output))
    return summary


def _real_stage(stage, contract_path, started):
    require_clean_commit(stage)
    config, contract, manifest, hashes = verify_contract_inputs(contract_path)
    check_disk(contract)
    if stage == "prefit":
        EVIDENCE_DIR.mkdir(exist_ok=False)
        write_json(EVIDENCE_DIR/"isolation-before.json", isolation_snapshot())
    bars, loaded = load_historical_bars(contract_path=contract_path)
    structural, conflicts = load_result_blind_inputs(contract)
    prepared = prepare_fold_audits(bars, config, contract, conflicts, structural=structural,
                                  entries=manifest["months"], close_audit=loaded["close_time"])
    check_resources(contract, "real", started)
    if stage == "prefit":
        for name, value in (("prefit-audit.json", prepared.audit), ("prefit-oracle.json", prepared.oracle_audit),
                            ("close-time-audit.json", prepared.close_audit), ("sensitivity-prefit.json", prepared.sensitivity)):
            write_json(EVIDENCE_DIR/name, value)
        from quantforge.phase3a_runner import peak_process_bytes
        write_json(EVIDENCE_DIR/"runtime-prefit.json",
                   dict(**runtime_fields("prefit", EVIDENCE_DIR), elapsed_seconds=perf_counter()-started,
                        peak_process_bytes=peak_process_bytes(), ridge_fits=0))
        return dict(status="prefit_passed", folds=len(prepared.audit["folds"]), ridge_fits=0)
    for name, value in (("prefit-audit.json", prepared.audit), ("sensitivity-prefit.json", prepared.sensitivity),
                        ("prefit-oracle.json", prepared.oracle_audit), ("close-time-audit.json", prepared.close_audit)):
        require(json_bytes(value) == (EVIDENCE_DIR/name).read_bytes(), "Formal/prefit audit differs: " + name)
    parent = RESULTS_ROOT/"gate2"
    stamp, head = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"), git("rev-parse", "HEAD")
    output = parent/f"{stage}-{stamp}-{head[:7]}"
    require(output.resolve() == output and not output.exists(), "Output directory exists or redirects")
    output.mkdir(parents=True, exist_ok=False)
    summary = execute_prepared(prepared, output, "real", started)
    if stage == "reproduction":
        formals = list(parent.glob("formal-*"))
        require(len(formals) == 1, "Exactly one formal run required")
        formal = formals[0]
        names = {p.name for p in output.iterdir() if p.name != "runtime.json"}
        require(names == {p.name for p in formal.iterdir() if p.name != "runtime.json"}, "Deterministic artifact names differ")
        checks = {name: dict(formal_sha256=_hash(formal/name), reproduction_sha256=_hash(output/name),
                             byte_identical=_hash(formal/name) == _hash(output/name)) for name in sorted(names)}
        write_json(output/"determinism-check.json", dict(files=checks, all_byte_identical=all(v["byte_identical"] for v in checks.values())))
        require(all(v["byte_identical"] for v in checks.values()), "Formal/reproduction bytes differ")
        after = isolation_snapshot()
        write_json(output/"isolation-after.json", after)
        require(json_bytes(after) == (EVIDENCE_DIR/"isolation-before.json").read_bytes(), "Isolation snapshot changed")
    check_resources(contract, "real", started)
    return dict(status="passed", ridge_fits=summary["total_ridge_fits"])


def run_real(stage, contract_path=CONTRACT_PATH):
    require(stage in ("prefit", "formal", "reproduction"), "Unsupported real stage")
    event = launch_begin(stage)
    started = perf_counter()
    try:
        with offline():
            result = _real_stage(stage, contract_path, started)
    except OSError:
        # A refused or failed write never triggers fallback evidence writes.
        raise
    except Exception as exc:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        if not EVIDENCE_DIR.exists():
            EVIDENCE_DIR.mkdir()
        write_json(EVIDENCE_DIR/f"stop-{stamp}.json",
                   dict(stage=stage, error_type=type(exc).__name__, error=str(exc)))
        launch_end(event, 1)
        raise
    launch_end(event, 0)
    return result
