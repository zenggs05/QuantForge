import json, sys, hashlib
import numpy as np, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")
MAN = r"D:\zgs_project\QuantForge\data\processed\binance\spot\BTCUSDT\1m\historical\historical-manifest.json"
m = json.load(open(MAN, encoding="utf-8"))
grid = pd.date_range("2020-01-01T00:00Z", "2025-08-31T23:59Z", freq="min").as_unit("ns")
present = np.ones(len(grid), bool)
for e in m["months"]:
    for g in e.get("gap_ranges", []):
        a = grid.get_loc(pd.Timestamp(g["start_utc"])); b = grid.get_loc(pd.Timestamp(g["end_utc"]))
        present[a:b+1] = False
opens = grid[present]
assert len(opens) == 2978475
evals = [e["month"] for e in m["months"] if e["role"] == "evaluate"]
# ---------- Method A: independent minute-grid mask arithmetic ----------
seg_id = np.concatenate([[0], np.cumsum(np.diff(opens.asi8) != 60_000_000_000)])
pos = pd.Series(np.arange(len(opens))).groupby(seg_id).cumcount().to_numpy()
seg_len = pd.Series(seg_id).map(pd.Series(seg_id).value_counts()).to_numpy()
rev = seg_len - 1 - pos                                   # bars remaining after this one in its segment
valid_f = pos >= 60
dec = opens + pd.Timedelta(minutes=1, seconds=1)
def masks(h):
    valid_l = rev >= h
    tstart = opens + pd.Timedelta(minutes=1); tend = tstart + pd.Timedelta(minutes=h); lav = tend + pd.Timedelta(seconds=1)
    return valid_l, tstart, tend, lav
A = {}
for h in (15, 5):
    valid_l, tstart, tend, lav = masks(h)
    valid = valid_f & valid_l
    for mo in evals:
        s = pd.Timestamp(mo + "-01T00:00Z"); e = s + pd.offsets.MonthBegin(1)
        ec = (dec >= s) & (dec < e); em = lav <= e
        ev = ec & valid & em
        first_tstart = tstart[ev][0]
        tc = (dec >= pd.Timestamp("2020-01-01T00:00Z")) & (dec < s)
        mature = lav <= s; known = dec <= s; nonov = tend <= first_tstart; within = tend <= s
        tr = tc & valid & mature & known & nonov & within
        idx = np.flatnonzero(ev); cuts = np.flatnonzero(np.diff(idx) != 1)
        segs = np.diff(np.r_[0, cuts + 1, len(idx)]).tolist()
        A[(mo, h)] = dict(
            candidate_eval_samples=int(ec.sum()), candidate_train_samples=int(tc.sum()),
            eval_invalid_features=int((ec & ~valid_f).sum()), eval_invalid_labels=int((ec & ~valid_l).sum()),
            eval_labels_after_cutoff=int((ec & ~em).sum()), eval_samples=int(ev.sum()),
            train_invalid_features=int((tc & ~valid_f).sum()), train_invalid_labels=int((tc & ~valid_l).sum()),
            train_immature_labels=int((tc & valid & ~mature).sum()), train_purged_overlap=int((tc & valid & ~nonov).sum()),
            train_target_after_fit=int((tc & valid & ~within).sum()), train_samples=int(tr.sum()),
            eval_segment_lengths=segs, llsw_lag=max(60, int(np.ceil(1.3 * np.sqrt(ev.sum())))),
            eval_ids_sha256=hashlib.sha256(opens[ev].as_unit("ns").asi8.astype("<i8").tobytes()).hexdigest(),
            train_ids_sha256=hashlib.sha256(opens[tr].as_unit("ns").asi8.astype("<i8").tobytes()).hexdigest())
# ---------- Method B: frozen builders on SYNTHETIC bars laid on the real calendar/gap layout ----------
from quantforge.features import build_features
from quantforge.labels import build_labels
from quantforge.datasets.historical_splits import make_historical_splits
rng = np.random.default_rng(42)
close = 100 * np.exp(np.cumsum(rng.normal(0, 1e-4, len(opens))))
open_ = np.r_[100.0, close[:-1]]
high = np.maximum(open_, close) * 1.0002; low = np.minimum(open_, close) * 0.9998
vol = rng.lognormal(3, 0.2, len(opens)); trades = rng.integers(10, 100, len(opens)).astype(float)
bars = pd.DataFrame({"open_time": opens, "open": open_, "high": high, "low": low, "close": close, "volume": vol,
                     "number_of_trades": trades, "taker_buy_base_asset_volume": vol * 0.5}, index=opens)
feat = build_features(bars)
B = {}
for h in (15, 5):
    lab = build_labels(bars, h)
    meta = feat.metadata.join(lab.frame.drop(columns="target_return"))
    for fold in make_historical_splits(meta, tuple(evals), h, "month_end"):
        B[(fold.fit_time.strftime("%Y-%m"), h)] = fold
keys = ["candidate_eval_samples","candidate_train_samples","eval_invalid_features","eval_invalid_labels",
        "eval_labels_after_cutoff","eval_samples","train_invalid_features","train_invalid_labels",
        "train_immature_labels","train_purged_overlap","train_target_after_fit","train_samples"]
mism = []
for k, a in A.items():
    f = B[k]; au = f.audit
    for key in keys:
        if a[key] != au[key]: mism.append((k, key, a[key], au[key]))
    if a["eval_ids_sha256"] != hashlib.sha256(f.eval_indices.as_unit("ns").asi8.astype("<i8").tobytes()).hexdigest(): mism.append((k, "eval_ids"))
    if a["train_ids_sha256"] != hashlib.sha256(f.train_indices.as_unit("ns").asi8.astype("<i8").tobytes()).hexdigest(): mism.append((k, "train_ids"))
print("folds A:", len(A), "folds B:", len(B), "MISMATCHES:", len(mism), mism[:5])
print("feature undefined rows in synthetic (should be only position<60):", feat.diagnostics["exclusion_counts"])
for k in [("2021-01",15),("2021-04",15),("2021-04",5),("2025-08",15),("2025-08",5)]:
    a = A[k]; print(k, {x: a[x] for x in keys}, "segs", a["eval_segment_lengths"], "LLSW", a["llsw_lag"])
print("min train_samples:", min(a["train_samples"] for a in A.values()),
      "| min eval segment:", min(min(a["eval_segment_lengths"]) for a in A.values()),
      "| max LLSW:", max(a["llsw_lag"] for a in A.values()),
      "| sum eval h15:", sum(a["eval_samples"] for (mo,h),a in A.items() if h==15),
      "| sum eval h5:", sum(a["eval_samples"] for (mo,h),a in A.items() if h==5))
out = [dict(month=mo, horizon=h, **{k: v for k, v in a.items()}) for (mo, h), a in sorted(A.items(), key=lambda x: (x[0][1]*-1, x[0][0]))]
json.dump(out, open(sys.argv[1], "w", encoding="utf-8"), indent=1)
print("written", len(out), "rows")
