"""Independent result-blind calendar, validity, and index oracle for D-056/057.

No predictor, label builder, temporal helper, split builder, or gate2 adapter is
imported here. Only denominator validity and timestamp sets are computed.
"""
from __future__ import annotations

import hashlib
import math

import numpy as np
import pandas as pd

MINUTE = 60_000_000_000


def index_sha(index):
    return hashlib.sha256(pd.DatetimeIndex(index).as_unit("ns").asi8.astype("<i8").tobytes()).hexdigest()


def segments(index):
    index = pd.DatetimeIndex(index).as_unit("ns")
    if len(index) == 0:
        return []
    cut = np.r_[0, np.flatnonzero(np.diff(index.as_unit("ns").asi8) != MINUTE) + 1, len(index)]
    return [index[a:b] for a, b in zip(cut[:-1], cut[1:])]


def lag(n, config):
    return max(config["inference"]["llsw_min_lag"],
               math.ceil(config["inference"]["llsw_constant"] * math.sqrt(n)))


def prune(index, config, reason):
    before = pd.DatetimeIndex(index).as_unit("ns")
    bandwidth = lag(len(before), config)
    kept, removed = [], []
    for part in segments(before):
        if len(part) <= bandwidth:
            removed.append(dict(start=part[0].isoformat(), end=part[-1].isoformat(),
                                length=len(part), reason=reason, threshold=bandwidth))
        else:
            kept.extend(part.asi8)
    after = pd.to_datetime(np.asarray(kept, dtype=np.int64), utc=True)
    return after, removed


def validity(bars):
    """Return denominator validity without calculating a feature or target."""
    index = pd.DatetimeIndex(bars.open_time).as_unit("ns")
    n = len(index)
    starts = np.r_[0, np.flatnonzero(np.diff(index.asi8) != MINUTE) + 1]
    ends = np.r_[starts[1:], n]
    pos = np.arange(n) - np.repeat(starts, ends - starts)
    remaining = np.repeat(ends, ends - starts) - 1 - np.arange(n)
    causes = {name: np.zeros(n, dtype=bool) for name in (
        "previous_zero_volume", "zero_volume", "zero_price_range",
        "zero_historical_volume_variance", "zero_trades_reference")}
    for a, b in zip(starts, ends):
        volume = pd.Series(bars.volume.iloc[a:b].to_numpy(dtype=np.float64))
        trades = pd.Series(bars.number_of_trades.iloc[a:b].to_numpy(dtype=np.float64))
        causes["previous_zero_volume"][a:b] = volume.shift(1).eq(0)
        causes["zero_volume"][a:b] = volume.eq(0)
        causes["zero_price_range"][a:b] = (bars.high.iloc[a:b].to_numpy() == bars.low.iloc[a:b].to_numpy())
        causes["zero_historical_volume_variance"][a:b] = volume.shift(1).rolling(60, min_periods=60).std(ddof=1).eq(0)
        causes["zero_trades_reference"][a:b] = trades.shift(1).rolling(60, min_periods=60).mean().eq(0)
    structural = pos >= 60
    undefined = structural & np.logical_or.reduce(list(causes.values()))
    reasons = [{"bar_open": index[i].isoformat(), "subreasons": [k for k, v in causes.items() if v[i]]}
               for i in np.flatnonzero(undefined)]
    return dict(index=index, structural=structural, valid=structural & ~undefined,
                remaining=remaining, undefined=undefined, reasons=reasons)


def conflict_mask(index, conflicts):
    """Search sorted conflict timestamps, independently of the adapter's ranges."""
    ts = pd.DatetimeIndex(index).as_unit("ns").asi8
    points = np.sort(pd.DatetimeIndex(conflicts).as_unit("ns").asi8)
    left = np.searchsorted(points, ts - 60 * MINUTE, side="left")
    right = np.searchsorted(points, ts + 15 * MINUTE, side="right")
    return right > left


def fold_sets(state, month, horizon, *, structural=False):
    index = state["index"]
    stamp = index.asi8
    decision = stamp + MINUTE + 1_000_000_000
    target_end = stamp + (horizon + 1) * MINUTE
    available = target_end + 1_000_000_000
    start = pd.Timestamp(month + "-01", tz="UTC")
    end = start + pd.offsets.MonthBegin(1)
    fv = state["structural"] if structural else state["valid"]
    lv = state["remaining"] >= horizon
    valid = fv & lv
    ec = (decision >= start.value) & (decision < end.value)
    tc = (decision >= pd.Timestamp("2020-01-01", tz="UTC").value) & (decision < start.value)
    em, mature = available <= end.value, available <= start.value
    ev = ec & valid & em
    if not ev.any():
        raise ValueError("oracle has no evaluation samples")
    nonoverlap = target_end <= stamp[ev][0] + MINUTE
    within = target_end <= start.value
    tr = tc & valid & mature & (decision <= start.value) & nonoverlap & within
    audit = dict(
        month=month, horizon_minutes=horizon, fit_time=start.isoformat(), evaluation_as_of=end.isoformat(),
        train_samples=int(tr.sum()), eval_samples=int(ev.sum()),
        candidate_train_samples=int(tc.sum()), candidate_eval_samples=int(ec.sum()),
        train_invalid_features=int((tc & ~fv).sum()), train_invalid_labels=int((tc & ~lv).sum()),
        train_immature_labels=int((tc & valid & ~mature).sum()),
        train_purged_overlap=int((tc & valid & ~nonoverlap).sum()),
        train_target_after_fit=int((tc & valid & ~within).sum()),
        eval_invalid_features=int((ec & ~fv).sum()), eval_invalid_labels=int((ec & ~lv).sum()),
        eval_labels_after_cutoff=int((ec & ~em).sum()),
        train_removed_total=int(tc.sum()-tr.sum()), eval_removed_total=int(ec.sum()-ev.sum()),
        counts_may_overlap=True, boundary_basis="decision_time",
        last_train_label_available=pd.Timestamp(available[tr][-1], tz="UTC").isoformat(),
        first_eval_decision=pd.Timestamp(decision[ev][0], tz="UTC").isoformat(),
        last_eval_label_available=pd.Timestamp(available[ev][-1], tz="UTC").isoformat(),
        fit_at="month_start", evaluation_cutoff="month_end", role_bucket=f"{start.year}H{1 if start.month<=6 else 2}")
    return index[tr], index[ev], audit


def describe(train, evaluation, audit, raw_evaluation, removed, config):
    return {**audit, "raw_eval_samples": len(raw_evaluation), "eval_samples": len(evaluation),
            "pre_pf6_first_eval_decision": audit["first_eval_decision"],
            "pre_pf6_last_eval_label_available": audit["last_eval_label_available"],
            "first_eval_decision": None if not len(evaluation) else (evaluation[0]+pd.Timedelta(seconds=61)).isoformat(),
            "last_eval_label_available": None if not len(evaluation) else (evaluation[-1]+pd.Timedelta(minutes=audit["horizon_minutes"]+1, seconds=1)).isoformat(),
            "eval_removed_total": audit["candidate_eval_samples"]-len(evaluation),
            "train_ids_sha256": index_sha(train), "eval_ids_sha256": index_sha(evaluation),
            "pre_pf6_eval_ids_sha256": index_sha(raw_evaluation),
            "eval_segment_lengths": [len(p) for p in segments(evaluation)],
            "llsw_lag": lag(len(evaluation), config), "pf6_removed_segments": removed}


def audit_fold(state, month, horizon, config, conflicts):
    tr, ev, audit = fold_sets(state, month, horizon)
    structural_tr, structural_ev, structural_audit = fold_sets(state, month, horizon, structural=True)
    removed = []
    final = ev
    if horizon == 15:
        final, removed = prune(ev, config, "D057_undefined_feature_short_segment")
    out = describe(tr, final, audit, ev, removed, config)
    out.update(D_star_train=len(structural_tr)-len(tr), D_star_eval=len(structural_ev)-len(ev),
               structural_train_ids_sha256=index_sha(structural_tr),
               structural_eval_ids_sha256=index_sha(structural_ev),
               structural_audit=structural_audit)
    for name, ids in (("D_star_train_rows", structural_tr), ("D_star_eval_rows", structural_ev)):
        found = []
        for item in state["reasons"]:
            stamp = pd.Timestamp(item["bar_open"])
            position = ids.searchsorted(stamp)
            if position < len(ids) and ids[position] == stamp:
                found.append(item)
        out[name] = found
    sensitivity = None
    if horizon == 15:
        train = tr[~conflict_mask(tr, conflicts)]
        post = final[~conflict_mask(final, conflicts)]
        kept, short = prune(post, config, "S_DQ_post_mask_short_segment")
        sensitivity = dict(month=month, train_samples=len(train), eval_samples=len(kept),
                           train_masked=len(tr)-len(train), eval_masked=len(final)-len(post),
                           post_mask_eval_samples=len(post), short_segments=short,
                           train_ids_sha256=index_sha(train), eval_ids_sha256=index_sha(kept),
                           eval_segment_lengths=[len(p) for p in segments(kept)], llsw_lag=lag(len(kept), config))
    return out, sensitivity
