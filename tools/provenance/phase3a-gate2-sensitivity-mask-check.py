import csv, json, sys, math, hashlib
import numpy as np, pandas as pd
sys.stdout.reconfigure(encoding="utf-8")
R = r"D:\zgs_project\QuantForge"
man = json.load(open(R + r"\data\processed\binance\spot\BTCUSDT\1m\historical\historical-manifest.json", encoding="utf-8"))
grid = pd.date_range("2020-01-01T00:00Z", "2025-08-31T23:59Z", freq="min").as_unit("ns")
present = np.ones(len(grid), bool)
for e in man["months"]:
    for g in e.get("gap_ranges", []):
        a = grid.get_loc(pd.Timestamp(g["start_utc"])); b = grid.get_loc(pd.Timestamp(g["end_utc"])); present[a:b+1] = False
rows = list(csv.DictReader(open(R + r"\docs\phase3a-retry-verification-evidence\final-v2\conflict-minutes.csv", encoding="utf-8")))
assert len(rows) == 157
cidx = np.array(sorted(grid.get_loc(pd.Timestamp(int(r["open_time_us"]) * 1000, tz="UTC")) for r in rows))
# write frozen conflict list (LF)
out = sorted(rows, key=lambda r: int(r["open_time_us"]))
with open("phase3a-gate2-sensitivity-conflicts.csv", "w", encoding="utf-8", newline="\n") as f:
    w = csv.writer(f, lineterminator="\n"); w.writerow(["open_time_utc", "open_time_us", "day", "source", "classification", "severity"])
    for r in out: w.writerow([r["open_time_utc"], r["open_time_us"], r["day"], r["source"], r["classification"], r["severity"]])
b = open("phase3a-gate2-sensitivity-conflicts.csv", "rb").read(); print("conflicts file", len(b), hashlib.sha256(b).hexdigest())
n = len(grid); H = 15
chg = np.zeros(n + 1, np.int32)
np.add.at(chg, np.maximum(0, cidx - H), 1); np.add.at(chg, np.minimum(n, cidx + 60 + 1), -1)
mask = np.cumsum(chg[:-1]) > 0   # bar i masked iff some conflict p in [i-60, i+H]
# structural validity on the bar grid (gaps only)
idx = np.arange(n); miss = np.r_[0, np.cumsum(~present)]
vf = (idx >= 60) & (miss[idx + 1] == miss[np.maximum(0, idx - 60)])
le = np.minimum(n, idx + H + 1); vl = (idx + H < n) & (miss[le] == miss[idx])
valid = present & vf & vl
print("masked present bars total", int((mask & present).sum()), "masked valid", int((mask & valid).sum()))
evals = [e["month"] for e in man["months"] if e["role"] == "evaluate"]
rep = []
for mo in evals:
    start = pd.Timestamp(mo + "-01T00:00Z"); end = start + pd.offsets.MonthBegin(1)
    dec = grid + pd.Timedelta(minutes=1, seconds=1)
    lav = grid + pd.Timedelta(minutes=1 + H, seconds=1)
    inm = (dec >= start) & (dec < end) & (lav <= end)
    base = valid & inm
    sens = base & ~mask
    def segs(sel):
        p = np.flatnonzero(sel)
        if len(p) == 0: return []
        cuts = np.r_[0, np.flatnonzero(np.diff(p) != 1) + 1, len(p)]
        return [int(b - a) for a, b in zip(cuts[:-1], cuts[1:])]
    s0, s1 = segs(base), segs(sens)
    T = int(sens.sum()); lag = max(60, math.ceil(1.3 * math.sqrt(T))) if T else None
    if int((base & mask).sum()):
        rep.append((mo, int(base.sum()), int((base & mask).sum()), len(s0), len(s1), min(s1), lag, [x for x in s1 if x <= lag]))
for r in rep: print(r)
