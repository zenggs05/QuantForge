"""D-061 exploratory supplementary analyses (A1-A4, B6a) on stored forecasts.

Reads only the stored prediction files of the Gate 2 formal run and the
Holdout-A formal run (read-only) and small evidence JSON files. No model is
fitted, no market data are read, no network is used. Every output is labelled
exploratory_post_hoc; nothing here changes a frozen confirmatory decision.

Usage (from the repository root):
    .venv\\Scripts\\python.exe -B analysis\\supplementary_a.py --out docs\\supplementary-evidence
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from scipy.fft import dct

ROOT = Path(__file__).resolve().parents[1]
GATE2_PRED = Path(r"D:\zgs_project\QuantForge-3a\results\phase3a\gate2\formal-20261116T005902063597Z-3a9b936\predictions.parquet")
HOLDOUT_PRED = Path(r"D:\zgs_project\QuantForge-3a\results\holdout-a\formal-20261117T061540330918Z-b6fd4d8\predictions.parquet")
GATE2_EVID = ROOT / "docs/phase3a-gate2-evidence"
HOLDOUT_EVID = ROOT / "docs/holdout-a-evidence"
LABEL = "exploratory_post_hoc"
NS_MIN = 60_000_000_000
PERIODS_PER_YEAR = {15: 365 * 24 * 4, 5: 365 * 24 * 12}
FEES_BP = [0.0, 0.5, 1.0, 2.0, 4.0, 7.5, 10.0]
THRESH_BP = [0.0, 1.0, 2.0, 3.0, 5.0]
STRESS = {"2021-01": "month", "2021-05-19": "day", "2022-05": "month", "2022-06": "month", "2022-11": "month"}
SEED = 20261117


# ---------------------------------------------------------------- utilities
def sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def clean(x):
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        x = float(x)
        return None if not math.isfinite(x) else float(f"{x:.12g}")
    if isinstance(x, np.bool_):
        return bool(x)
    return x


def dump(path: Path, obj) -> None:
    path.write_bytes((json.dumps(clean(obj), indent=1, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii"))


def var_mean_bartlett(x: np.ndarray, lag: int):
    """Frozen fixed-n Bartlett variance of the mean (hac_tests.segment_hac_variance), O(n)."""
    n = len(x)
    if n <= lag:
        return None
    u = x - x.mean()
    q = lag + 1
    pad = np.concatenate([np.zeros(lag), u, np.zeros(lag)])
    cs = np.concatenate([[0.0], np.cumsum(pad)])
    w = cs[q:] - cs[:-q]
    return float(w @ w / q / n**2)


def llsw(n: int) -> int:
    return max(60, math.ceil(1.3 * math.sqrt(n)))


def segments(times: np.ndarray):
    if len(times) == 0:
        return []
    cut = np.flatnonzero(np.diff(times) != NS_MIN) + 1
    return np.split(np.arange(len(times)), cut)


def month_stat(values: np.ndarray, times: np.ndarray, lag: int):
    """Frozen power_reanalysis.monthly_stat: Bartlett within contiguous segments, n-weighted."""
    parts = []
    for idx in segments(times):
        v = var_mean_bartlett(values[idx], lag)
        parts.append((len(idx), float(values[idx].mean()), v))
    n = sum(p[0] for p in parts)
    mean = sum(p[0] * p[1] for p in parts) / n
    var = None if any(p[2] is None for p in parts) else sum((p[0] / n) ** 2 * p[2] for p in parts)
    return n, mean, var, len(parts)


def pooled(groups, lag_rule="llsw", alternative="greater"):
    """groups: list of (values, times). Monthly HAC then n-weighted pooling (zero cross-month cov)."""
    rows = []
    for values, times in groups:
        if len(values) == 0:
            continue
        lag = llsw(len(values)) if lag_rule == "llsw" else (2 * llsw(len(values)) if lag_rule == "2llsw" else int(lag_rule))
        rows.append(month_stat(values, times, lag))
    n = sum(r[0] for r in rows)
    mean = sum(r[0] * r[1] for r in rows) / n
    if any(r[2] is None for r in rows):
        return dict(n=n, mean=mean, se=None, t=None, p=None, months=len(rows))
    var = sum((r[0] / n) ** 2 * r[2] for r in rows)
    se = math.sqrt(var)
    t = mean / se
    p = stats.norm.sf(t) if alternative == "greater" else 2 * stats.norm.sf(abs(t))
    return dict(n=n, mean=mean, se=se, t=t, p=p, months=len(rows))


def ewc(x: np.ndarray):
    """Equal-weighted cosine LRV with nu = floor(0.4 T^(2/3)) and t_nu reference (Lazarus et al. 2018)."""
    T = len(x)
    nu = int(math.floor(0.4 * T ** (2 / 3)))
    u = x - x.mean()
    X = dct(u, type=2)  # X_k = 2 sum u_t cos(pi k (t+0.5)/T)
    lam = math.sqrt(2 / T) * X[1:nu + 1] / 2
    omega = float(lam @ lam / nu)
    se = math.sqrt(omega / T)
    t = float(x.mean() / se)
    return dict(n=T, nu=nu, mean=float(x.mean()), se=se, t=t, p=float(stats.t.sf(t, nu)))


def by_month(df: pd.DataFrame, col: str):
    return [(g[col].to_numpy(), g["t_ns"].to_numpy()) for _, g in df.groupby("month", sort=True)]


def ic_pooled(df: pd.DataFrame):
    """Frozen IC-HAC: monthly ddof0 centred standardized products, LLSW, n-weighted."""
    groups = []
    for _, g in df.groupby("month", sort=True):
        f, y = g.f.to_numpy(), g.y.to_numpy()
        z = (f - f.mean()) * (y - y.mean()) / (f.std() * y.std())
        groups.append((z, g.t_ns.to_numpy()))
    return pooled(groups)


# -------------------------------------------------------------- data loading
def load():
    g = pd.read_parquet(GATE2_PRED, columns=["decision_time", "prediction", "target_return", "historical_mean",
                                             "month", "horizon_minutes", "specification"])
    g = g[g.specification == "v1"].rename(columns={"prediction": "f", "target_return": "y", "historical_mean": "hm",
                                                    "horizon_minutes": "h"})
    g["t_ns"] = g.decision_time.astype("int64")
    h = pd.read_parquet(HOLDOUT_PRED)
    h = h[h.spec == "v1"].rename(columns={"prediction": "f", "target": "y", "horizon": "h"})
    h["t_ns"] = h.decision_time.astype("int64")
    h["month"] = h.decision_time.dt.strftime("%Y-%m")
    out = {}
    for name, d in (("history", g), ("holdout", h)):
        for hz in (15, 5):
            x = d[d.h == hz].sort_values("t_ns").reset_index(drop=True)
            x["year"] = x.month.str[:4]
            out[(name, hz)] = x[[c for c in ("t_ns", "month", "year", "f", "y", "hm") if c in x.columns]]
    return out


# ---------------------------------------------------------------- A0 check
def a0_reproduce(D):
    res = {}
    for name in ("history", "holdout"):
        x = D[(name, 15)]
        c = 2 * x.f.to_numpy() * x.y.to_numpy()
        x = x.assign(c=c)
        res[name] = pooled(by_month(x, "c"))
    prim = json.loads((GATE2_EVID / "summary.json").read_bytes())["primary"]
    ha = json.loads((HOLDOUT_EVID / "primary.json").read_bytes())
    ha_pooled = ha.get("pooled") or ha.get("primary") or {}
    checks = {
        "history_t_matches_frozen": abs(res["history"]["t"] - prim["t"]) < 1e-9 * abs(prim["t"]),
        "history_mean_matches_frozen": abs(res["history"]["mean"] - prim["mean"]) < 1e-12 * abs(prim["mean"]) + 1e-22,
        "holdout_t": res["holdout"]["t"], "holdout_frozen_t": ha_pooled.get("t"),
    }
    if ha_pooled.get("t") is not None:
        checks["holdout_t_matches_frozen"] = abs(res["holdout"]["t"] - ha_pooled["t"]) < 1e-9 * abs(ha_pooled["t"])
    if not checks["history_t_matches_frozen"] or not checks["history_mean_matches_frozen"] or checks.get("holdout_t_matches_frozen") is False:
        raise SystemExit("A0 FAILED: own implementation does not reproduce frozen primary: " + json.dumps(clean(checks)))
    return dict(label=LABEL, recomputed=res, checks=checks)


# ---------------------------------------------------------------- A1 economics
def strategy(x: pd.DataFrame, hz: int, k_bp: float, phase: int | None = None):
    bar_min = ((x.t_ns.to_numpy() - 61_000_000_000) // NS_MIN) % 1440
    ph = (hz - 1) if phase is None else phase
    sel = (bar_min % hz) == ph
    t = x.t_ns.to_numpy()[sel]
    f = x.f.to_numpy()[sel]
    y = x.y.to_numpy()[sel]
    months = x.month.to_numpy()[sel]
    p = np.sign(f) * (np.abs(f) > k_bp * 1e-4)
    cont = np.concatenate([[False], np.diff(t) == hz * NS_MIN])
    prev = np.concatenate([[0.0], p[:-1]]) * cont
    nxt_cont = np.concatenate([cont[1:], [False]])
    turnover = np.abs(p - prev) + np.abs(p) * (~nxt_cont)  # open/adjust + close before gap/end
    r = p * y
    return dict(t=t, r=r, turn=turnover, p=p, months=months)


def breakeven(s, rng, draws=2000):
    tot_r, tot_turn = s["r"].sum(), s["turn"].sum()
    be = tot_r / tot_turn * 1e4 if tot_turn > 0 else None
    rt = s["r"].sum() / (2 * np.abs(s["p"]).sum()) * 1e4 if np.abs(s["p"]).sum() > 0 else None
    um, inv = np.unique(s["months"], return_inverse=True)
    mr = np.bincount(inv, weights=s["r"], minlength=len(um))
    mt = np.bincount(inv, weights=s["turn"], minlength=len(um))
    ci = [None, None]
    if len(um) >= 2 and tot_turn > 0:
        idx = rng.integers(0, len(um), size=(draws, len(um)))
        num, den = mr[idx].sum(1), mt[idx].sum(1)
        ok = den > 0
        q = np.quantile(num[ok] / den[ok] * 1e4, [0.025, 0.975])
        ci = [float(q[0]), float(q[1])]
    return be, rt, ci


def a1(D):
    rng = np.random.default_rng(SEED)
    out = dict(label=LABEL, note="Descriptive economics on stored forecasts. Not a strategy backtest; no execution, latency, adverse selection or capacity modelled.",
               magnitudes={}, buckets={}, strategy={}, fee_grid={}, phase_robustness={}, campbell_thompson={}, delay={})
    for (name, hz), x in D.items():
        key = f"{name}_h{hz}"
        periods = sorted(x.year.unique()) if name == "history" else sorted(x.month.unique())
        col = "year" if name == "history" else "month"
        mag = {}
        for per in periods + ["all"] + (["2022-2025"] if name == "history" else []):
            sub = x if per == "all" else (x[x.year >= "2022"] if per == "2022-2025" else x[x[col] == per])
            af = np.abs(sub.f.to_numpy()) * 1e4
            sy = sub.y.std() * 1e4
            mag[per] = dict(n=len(sub), abs_f_bp=dict(zip(["p50", "p90", "p99", "p999", "max"], np.quantile(af, [0.5, 0.9, 0.99, 0.999, 1.0]))),
                            mean_f_bp=sub.f.mean() * 1e4, share_positive=float((sub.f > 0).mean()), sd_y_bp=sy,
                            median_abs_f_over_sd_y=float(np.median(af) / sy),
                            share_abs_f_above_bp={str(k): float((af > k).mean()) for k in (1, 2, 3, 5, 7.5, 10)})
        out["magnitudes"][key] = mag
        # buckets by |f| threshold, all minutes, month-cluster SE
        bk = {}
        for k in THRESH_BP:
            m = np.abs(x.f.to_numpy()) > k * 1e-4
            sub = x[m]
            sr = np.sign(sub.f.to_numpy()) * sub.y.to_numpy() * 1e4
            if len(sub) == 0:
                continue
            mm = pd.Series(sr).groupby(sub.month.to_numpy()).mean()
            nz = sub.y.to_numpy() != 0
            bk[str(k)] = dict(n=len(sub), signed_return_bp=sr.mean(),
                              month_cluster_se_bp=float(mm.std(ddof=1) / math.sqrt(len(mm))) if len(mm) > 1 else None,
                              hit_rate=float((np.sign(sub.f.to_numpy()[nz]) == np.sign(sub.y.to_numpy()[nz])).mean()))
        # deciles of f within each year (history) or pooled (holdout)
        dec = {}
        grp = x.groupby(col) if name == "history" else [("all", x)]
        parts = []
        for _, g in grp:
            q = pd.qcut(g.f, 10, labels=False, duplicates="drop")
            parts.append(pd.DataFrame({"d": q.to_numpy(), "y": g.y.to_numpy() * 1e4, "f": g.f.to_numpy() * 1e4}))
        P = pd.concat(parts)
        for d, g in P.groupby("d"):
            dec[str(int(d) + 1)] = dict(n=len(g), mean_f_bp=g.f.mean(), mean_y_bp=g.y.mean())
        out["buckets"][key] = dict(abs_f_threshold=bk, deciles_of_f=dec)
        # non-overlapping strategy
        st, fg = {}, {}
        subsets = {"all": x}
        if name == "history":
            subsets.update({yr: x[x.year == yr] for yr in periods})
            subsets["2022-2025"] = x[x.year >= "2022"]
        for sname, sx in subsets.items():
            st[sname] = {}
            for k in THRESH_BP:
                s = strategy(sx, hz, k)
                be, rt, ci = breakeven(s, rng)
                r = s["r"]
                sd = r.std()
                st[sname][str(k)] = dict(periods=len(r), traded_share=float(np.mean(s["p"] != 0)),
                                         gross_mean_bp_per_period=r.mean() * 1e4,
                                         sides_per_period=s["turn"].mean(),
                                         breakeven_fee_bp_per_side=be, breakeven_fee_bp_per_side_ci95=ci,
                                         breakeven_fee_bp_per_side_round_trip_each_trade=rt,
                                         gross_sharpe_annualized=(r.mean() / sd * math.sqrt(PERIODS_PER_YEAR[hz])) if sd > 0 else None)
                if k in (0.0, 2.0):
                    fg.setdefault(sname, {})[str(k)] = {str(c): dict(
                        net_mean_bp_per_period=(r - c * 1e-4 * s["turn"]).mean() * 1e4,
                        net_sharpe_annualized=((r - c * 1e-4 * s["turn"]).mean() / (r - c * 1e-4 * s["turn"]).std() * math.sqrt(PERIODS_PER_YEAR[hz])))
                        for c in FEES_BP}
        out["strategy"][key] = st
        out["fee_grid"][key] = fg
        bes = []
        for phs in range(hz):
            s = strategy(x, hz, 0.0, phase=phs)
            bes.append(s["r"].sum() / s["turn"].sum() * 1e4)
        out["phase_robustness"][key] = dict(k_bp=0.0, breakeven_min=min(bes), breakeven_max=max(bes), breakeven_mean=float(np.mean(bes)))
        # Campbell-Thompson mapping per period
        ct = {}
        for per in periods + ["all"]:
            sub = x if per == "all" else x[x[col] == per]
            r2 = 1 - ((sub.y - sub.f) ** 2).sum() / (sub.y ** 2).sum()
            ct[per] = dict(r2_oos=r2, implied_annual_sharpe_gain=(math.sqrt(r2 / (1 - r2) * PERIODS_PER_YEAR[hz]) if r2 > 0 else None))
        out["campbell_thompson"][key] = ct
        # execution delay: pair f_t with y at decision t + k minutes
        dl = {}
        t = x.t_ns.to_numpy()
        for kmin in (0, 1, 5):
            j = np.searchsorted(t, t + kmin * NS_MIN)
            ok = (j < len(t)) & (t[np.minimum(j, len(t) - 1)] == t + kmin * NS_MIN)
            z = x[ok].assign(y=x.y.to_numpy()[j[ok]])
            cw = pooled(by_month(z.assign(c=2 * z.f * z.y), "c"))
            ic = ic_pooled(z)
            s = strategy(z, hz, 0.0)
            dl[str(kmin)] = dict(cw_t=cw["t"], cw_mean=cw["mean"], ic_mean=ic["mean"], ic_t=ic["t"], n=cw["n"],
                                 breakeven_fee_bp_per_side_k0=s["r"].sum() / s["turn"].sum() * 1e4)
        out["delay"][key] = dl
    return out


# ---------------------------------------------------------------- A2 drift
def realtime_means(x: pd.DataFrame, hz: int, window_days=7, min_obs=1000):
    t = x.t_ns.to_numpy()
    y, f = x.y.to_numpy(), x.f.to_numpy()
    cy = np.concatenate([[0.0], np.cumsum(y)])
    cy2 = np.concatenate([[0.0], np.cumsum(y * y)])
    cf = np.concatenate([[0.0], np.cumsum(f)])
    lo = np.searchsorted(t, t - window_days * 1440 * NS_MIN, "left")
    hi_y = np.searchsorted(t, t - hz * NS_MIN, "right")  # mature labels only
    hi_f = np.searchsorted(t, t, "left")
    ny, nf = hi_y - lo, hi_f - lo
    with np.errstate(invalid="ignore", divide="ignore"):
        ybar = np.where(ny >= min_obs, (cy[hi_y] - cy[lo]) / ny, np.nan)
        ysd = np.where(ny >= min_obs, np.sqrt(np.maximum((cy2[hi_y] - cy2[lo]) / ny - ybar ** 2, 0)), np.nan)
        fbar = np.where(nf >= min_obs, (cf[hi_f] - cf[lo]) / nf, np.nan)
    return ybar, ysd, fbar


def shift_t(x: pd.DataFrame, shift_min: int):
    t = x.t_ns.to_numpy()
    j = np.searchsorted(t, t + shift_min * NS_MIN)
    ok = (j < len(t)) & (t[np.minimum(j, len(t) - 1)] == t + shift_min * NS_MIN)
    z = x[ok]
    c = 2 * z.f.to_numpy() * x.y.to_numpy()[j[ok]]
    return pooled(by_month(z.assign(c=c), "c"))["t"]


def a2(D):
    rng = np.random.default_rng(SEED + 2)
    out = dict(label=LABEL, decomposition={}, timing_only={}, realtime={}, shift_placebo={})
    for name in ("history", "holdout"):
        x = D[(name, 15)]
        rows = {}
        for m, g in x.groupby("month"):
            f, y = g.f.to_numpy(), g.y.to_numpy()
            rows[m] = dict(n=len(g), mean_c=2 * np.mean(f * y), drift_2fbar_ybar=2 * f.mean() * y.mean(),
                           timing_2cov=2 * np.mean((f - f.mean()) * (y - y.mean())), fbar_bp=f.mean() * 1e4, ybar_bp=y.mean() * 1e4)
        R = pd.DataFrame(rows).T
        agg = {}
        groups = {"all": R}
        if name == "history":
            groups.update({yr: R[R.index.str[:4] == yr] for yr in sorted(set(R.index.str[:4]))})
            groups["2022-2025"] = R[R.index.str[:4] >= "2022"]
        for gname, G in groups.items():
            w = G.n / G.n.sum()
            agg[gname] = dict(mean_c=float((w * G.mean_c).sum()), drift=float((w * G.drift_2fbar_ybar).sum()), timing=float((w * G.timing_2cov).sum()))
        out["decomposition"][name] = dict(by_month=rows, aggregated_n_weighted=agg)
        # timing-only CW: within-month demeaned
        d = []
        for _, g in x.groupby("month", sort=True):
            f, y = g.f.to_numpy(), g.y.to_numpy()
            d.append((2 * (f - f.mean()) * (y - y.mean()), g.t_ns.to_numpy()))
        tim = {"all": pooled(d)}
        if name == "history":
            months = sorted(x.month.unique())
            tim["2021"] = pooled([d[i] for i, m in enumerate(months) if m < "2022"])
            tim["2022-2025"] = pooled([d[i] for i, m in enumerate(months) if m >= "2022"])
        out["timing_only"][name] = tim
        # real-time demeaned (past 7 days, mature labels only) and CW vs past-week mean benchmark
        ybar, ysd, fbar = realtime_means(x, 15)
        ok = np.isfinite(ybar) & np.isfinite(fbar)
        z = x[ok]
        f, y, yb, fb = z.f.to_numpy(), z.y.to_numpy(), ybar[ok], fbar[ok]
        out["realtime"][name] = dict(
            dropped_rows_without_7d_history=int((~ok).sum()),
            demeaned_cw=pooled(by_month(z.assign(c=2 * (f - fb) * (y - yb)), "c")),
            cw_vs_past_week_mean=pooled(by_month(z.assign(c=2 * (f - yb) * (y - yb)), "c")),
            primary_same_rows=pooled(by_month(z.assign(c=2 * f * y), "c")))
        # shift placebo distribution (forecast t vs outcome t + shift)
        obs = pooled(by_month(x.assign(c=2 * x.f * x.y), "c"))["t"]
        if name == "history":
            fixed = [k * 1440 for k in range(1, 31)]
            rnd = sorted(int(v) for v in rng.integers(1440, 43201, size=200))
        else:
            fixed = [k * 1440 for k in range(1, 15)]
            rnd = sorted(int(v) for v in rng.integers(1440, 20161, size=100))
        res = {}
        subs = {"all": x}
        if name == "history":
            subs.update({"2021": x[x.year == "2021"], "2022-2025": x[x.year >= "2022"]})
        for sname, sx in subs.items():
            o = pooled(by_month(sx.assign(c=2 * sx.f * sx.y), "c"))["t"]
            tf = [shift_t(sx, s) for s in fixed]
            tr = [shift_t(sx, s) for s in rnd]
            allt = np.array([v for v in tf + tr if v is not None])
            res[sname] = dict(observed_t=o, fixed_day_shifts_t=dict(zip([str(s) for s in fixed], tf)),
                              random_shift_t_quantiles=dict(zip(["p05", "p50", "p95", "p99"], np.quantile(np.array([v for v in tr if v is not None]), [0.05, 0.5, 0.95, 0.99]))),
                              n_shifts=len(allt), share_shift_t_ge_observed=float((allt >= o).mean()),
                              empirical_p=float((1 + (allt >= o).sum()) / (1 + len(allt))))
        out["shift_placebo"][name] = dict(observed_primary_t=obs, results=res)
    return out


# ---------------------------------------------------------------- A3 robustness
def gr_critical(mu: float, sims=20000, grid=2000, seed=SEED + 3):
    rng = np.random.default_rng(seed)
    w = int(round(mu * grid))
    sup2, sup1 = [], []
    for _ in range(sims // 1000):
        inc = rng.standard_normal((1000, grid)) / math.sqrt(grid)
        B = np.concatenate([np.zeros((1000, 1)), np.cumsum(inc, axis=1)], axis=1)
        F = (B[:, w:] - B[:, :-w]) / math.sqrt(mu)
        sup2.append(np.abs(F).max(1)); sup1.append(F.max(1))
    return float(np.quantile(np.concatenate(sup2), 0.95)), float(np.quantile(np.concatenate(sup1), 0.95))


def a3(D):
    rng = np.random.default_rng(SEED + 4)
    out = dict(label=LABEL)
    sens = {}
    for name in ("history", "holdout"):
        x = D[(name, 15)].copy()
        x["c"] = 2 * x.f * x.y
        g = by_month(x, "c")
        row = {f"bartlett_within_month_{r}": pooled(g, lag_rule=r) for r in (60, "llsw", "2llsw")}
        c_all = x.c.to_numpy()
        row["bartlett_concatenated_llsw"] = (lambda v: dict(n=len(c_all), lag=llsw(len(c_all)), mean=float(c_all.mean()), se=math.sqrt(v), t=float(c_all.mean() / math.sqrt(v)), p=float(stats.norm.sf(c_all.mean() / math.sqrt(v)))))(var_mean_bartlett(c_all, llsw(len(c_all))))
        row["ewc_concatenated_fixed_b"] = ewc(c_all)
        if name == "holdout":
            row["by_month"] = {m: pooled([gg], lag_rule=r) for m, gg in zip(sorted(x.month.unique()), g) for r in ["llsw"]}
            # P1 sign-flip placebo, 60-minute UTC blocks, 500 draws
            obs = pooled(g)["t"]
            blocks = (x.t_ns.to_numpy() // (60 * NS_MIN))
            ub, inv = np.unique(blocks, return_inverse=True)
            tt = []
            for _ in range(500):
                s = rng.choice([-1.0, 1.0], size=len(ub))[inv]
                tt.append(pooled(by_month(x.assign(c=x.c * s), "c"))["t"])
            tt = np.array(tt)
            row["P1_sign_flip_60min_500"] = dict(observed_t=obs, empirical_p=float((1 + (tt >= obs).sum()) / 501),
                                                 placebo_t_mean=float(tt.mean()), placebo_t_sd=float(tt.std()))
        sens[name] = row
    sens["history"]["S_dep1_monthly_mean_hac_lag4"] = json.loads((GATE2_EVID / "dependence.json").read_bytes())["S_dep1"]
    out["inference_sensitivity"] = sens
    # subsamples and yearly
    x = D[("history", 15)].copy(); x["c"] = 2 * x.f * x.y
    sub = {}
    for lab, lo in (("2022-01..2025-08", "2022-01"), ("2021-07..2025-08", "2021-07"), ("2023-01..2025-08", "2023-01"), ("2021-01..2025-08", "2021-01")):
        z = x[x.month >= lo]
        prim = pooled(by_month(z, "c"))
        mm = z.groupby("month").c.mean().to_numpy()
        v = var_mean_bartlett(mm, 4)
        sub[lab] = dict(primary=prim, monthly_mean_hac_lag4=dict(months=len(mm), mean=float(mm.mean()), se=math.sqrt(v), t=float(mm.mean() / math.sqrt(v)), p=float(stats.norm.sf(mm.mean() / math.sqrt(v)))),
                        ic=ic_pooled(z), negative_months=int((mm < 0).sum()))
    yearly = {}
    for yr, z in x.groupby("year"):
        mm = z.groupby("month").c.mean().to_numpy()
        yearly[yr] = dict(primary=pooled(by_month(z, "c")), ic=ic_pooled(z), negative_months=int((mm < 0).sum()), months=len(mm))
    tot = (x.c).sum()
    yearly_share = {yr: float(z.c.sum() / tot) for yr, z in x.groupby("year")}
    out["subsamples"] = sub; out["yearly"] = yearly; out["share_of_summed_c_by_year"] = yearly_share
    # extremes
    ext = {}
    order = np.argsort(-np.abs(x.c.to_numpy()))
    for k in (10, 100, 1000):
        keep = np.ones(len(x), bool); keep[order[:k]] = False
        z = x[keep]
        # removal splits segments; treat each month's remaining rows as one contiguous series
        ext[f"drop_top_{k}_abs_c"] = pooled([(g.c.to_numpy(), np.arange(len(g)) * NS_MIN) for _, g in z.groupby("month", sort=True)])
    for q in (0.999, 0.9999):
        lim = np.quantile(np.abs(x.c), q)
        ext[f"winsor_abs_c_{q}"] = pooled(by_month(x.assign(c=x.c.clip(-lim, lim)), "c"))
    day = pd.to_datetime(x.t_ns - 61_000_000_000, utc=True).dt.strftime("%Y-%m-%d")
    stress = np.zeros(len(x), bool)
    for k, kind in STRESS.items():
        stress |= (x.month.to_numpy() == k) if kind == "month" else (day.to_numpy() == k)
    ext["exclude_stress_windows"] = dict(windows=STRESS, excluded_rows=int(stress.sum()), result=pooled(by_month(x[~stress], "c")))
    out["extremes"] = ext
    # Giacomini-Rossi fluctuation on monthly mean CW
    mm = x.groupby("month").c.mean()
    v = var_mean_bartlett(mm.to_numpy(), 4) * len(mm)
    sig = math.sqrt(v)
    m = 12
    roll = mm.rolling(m).mean().dropna()
    F = math.sqrt(m) * roll / sig
    cv2, cv1 = gr_critical(m / len(mm))
    out["giacomini_rossi"] = dict(window_months=m, mu=m / len(mm), sigma_hac_lag4=sig, max_F=float(F.max()), min_F=float(F.min()),
                                  argmax=str(F.idxmax()), argmin=str(F.idxmin()), cv95_two_sided_sim=cv2, cv95_one_sided_sim=cv1,
                                  reject_two_sided=bool(np.abs(F).max() > cv2), share_windows_F_above_one_sided_cv=float((F > cv1).mean()),
                                  F_path=dict(zip(F.index, F.to_numpy())))
    # existing results that were not reported
    out["previously_computed_not_reported"] = dict(
        conditional_gw=json.loads((GATE2_EVID / "conditional-gw.json").read_bytes()),
        concentration_rows=pd.read_csv(GATE2_EVID / "concentration.csv").to_dict(orient="records")[:12])
    # volatility-standardized CW (past-week sd of mature labels)
    vs = {}
    for name in ("history", "holdout"):
        z = D[(name, 15)]
        _, ysd, _ = realtime_means(z, 15)
        ok = np.isfinite(ysd) & (ysd > 0)
        zz = z[ok]
        s = ysd[ok]
        vs[name] = dict(dropped=int((~ok).sum()), result=pooled(by_month(zz.assign(c=2 * (zz.f / s) * (zz.y / s)), "c")))
    out["volatility_standardized_cw"] = vs
    return out


# ---------------------------------------------------------------- A4 bounds and calibration
def a4(D):
    out = dict(label=LABEL)
    sec = {r["test_id"]: r for r in pd.read_csv(GATE2_EVID / "secondary-tests.csv").to_dict(orient="records")}
    ha = {r["test_id"]: r for r in pd.read_csv(HOLDOUT_EVID / "secondary.csv").to_dict(orient="records")}
    bounds = {}
    for name, S in (("history", sec), ("holdout", ha)):
        dm = S["S4"]
        mean = float(dm["mean"]); se = abs(mean / float(dm["t"])) if "se" not in dm or pd.isna(dm.get("se")) else float(dm["se"])
        base = float(dm["baseline_mse"])
        r2 = -mean / base
        ic = S["S5"]; icm = float(ic["mean"]); ics = abs(icm / float(ic["t"])) if pd.isna(ic.get("se", np.nan)) else float(ic["se"])
        bounds[name] = dict(
            delta_mse=mean, delta_mse_se=se, delta_mse_ci95=[mean - 1.96 * se, mean + 1.96 * se],
            r2_oos=r2, r2_oos_ci95=[-(mean + 1.96 * se) / base, -(mean - 1.96 * se) / base],
            r2_oos_one_sided_upper95=-(mean - 1.645 * se) / base,
            tost_vs_r2_0p5pct=dict(z=(r2 - 0.005) / (se / base), p=float(stats.norm.cdf((r2 - 0.005) / (se / base)))),
            ic=icm, ic_se=ics, ic_ci95=[icm - 1.96 * ics, icm + 1.96 * ics],
            implied_r2_of_optimally_scaled_forecast=icm ** 2,
            implied_r2_ci95=[max(icm - 1.96 * ics, 0) ** 2, (icm + 1.96 * ics) ** 2])
    out["bounds"] = bounds
    # Mincer-Zarnowitz with month-cluster SE, and Theil decomposition
    mz = {}
    for name in ("history", "holdout"):
        x = D[(name, 15)]
        col = "year" if name == "history" else "month"
        res = {}
        for per, z in list(x.groupby(col)) + [("all", x)]:
            f, y = z.f.to_numpy(), z.y.to_numpy()
            X = np.column_stack([np.ones_like(f), f])
            XtX_inv = np.linalg.inv(X.T @ X)
            b = XtX_inv @ X.T @ y
            e = y - X @ b
            # Newey-West sandwich, Bartlett kernel, lag LLSW(n), via zero-extended moving sums
            gmat = X * e[:, None]
            lag = llsw(len(z)); q = lag + 1
            pad = np.vstack([np.zeros((lag, 2)), gmat, np.zeros((lag, 2))])
            cs = np.vstack([np.zeros((1, 2)), np.cumsum(pad, axis=0)])
            w = cs[q:] - cs[:-q]
            meat = w.T @ w / q
            V = XtX_inv @ meat @ XtX_inv
            se = np.sqrt(np.diag(V))
            G = z.month.nunique()
            rho = np.corrcoef(f, y)[0, 1]
            mse = np.mean((y - f) ** 2)
            res[per] = dict(n=len(z), a=b[0], a_se=se[0], beta=b[1], beta_se=se[1], clusters=int(G),
                            theil=dict(mse=mse, bias=(y.mean() - f.mean()) ** 2, regression=(f.std() - rho * y.std()) ** 2,
                                       disturbance=(1 - rho ** 2) * y.var(), zero_forecast_mse=np.mean(y ** 2)),
                            corr=rho)
        mz[name] = res
    out["mincer_zarnowitz"] = mz
    # real-time beta scaling (expanding, prior months only, no intercept)
    x = D[("history", 15)]
    months = sorted(x.month.unique())
    num = den = 0.0
    scaled = []
    for m in months:
        z = x[x.month == m]
        f, y = z.f.to_numpy(), z.y.to_numpy()
        if den > 0:
            beta = num / den
            scaled.append(pd.DataFrame({"year": z.year.to_numpy(), "y": y, "fs": beta * f, "f": f, "beta": beta}))
        num += float(f @ y); den += float(f @ f)
    S = pd.concat(scaled)
    rs = {}
    for per, z in list(S.groupby("year")) + [("2021-02..2025-08", S)]:
        rs[per] = dict(r2_oos_scaled=1 - ((z.y - z.fs) ** 2).sum() / (z.y ** 2).sum(), r2_oos_raw=1 - ((z.y - z.f) ** 2).sum() / (z.y ** 2).sum(),
                       beta_first=float(z.beta.iloc[0]), beta_last=float(z.beta.iloc[-1]))
    h = D[("holdout", 15)]
    jul, aug = h[h.month == "2026-07"], h[h.month == "2026-08"]
    b = float(jul.f @ jul.y / (jul.f @ jul.f))
    rs["holdout_aug_with_july_beta"] = dict(beta=b, r2_oos_scaled=1 - ((aug.y - b * aug.f) ** 2).sum() / (aug.y ** 2).sum(),
                                            r2_oos_raw=1 - ((aug.y - aug.f) ** 2).sum() / (aug.y ** 2).sum())
    out["realtime_beta_scaling"] = rs
    return out


# ---------------------------------------------------------------- B6a coefficient paths
def b6a():
    feats = ["log_return_1m", "log_return_5m", "log_return_15m", "log_return_60m", "return_std_5m", "return_std_15m", "return_std_60m",
             "volume_change_1m", "volume_zscore_60m", "taker_buy_volume_ratio", "trades_change_60m", "log_high_low", "log_close_open", "close_range_position"]
    rows = []
    for src, path in (("history", GATE2_EVID / "models.json"), ("holdout", HOLDOUT_EVID / "models.json")):
        M = json.loads(path.read_bytes())
        items = M if isinstance(M, list) else (M.get("models") if isinstance(M, dict) and "models" in M else list(M.values()))
        for m in items:
            spec = m.get("specification", m.get("spec"))
            hz = m.get("horizon_minutes", m.get("horizon"))
            if spec != "v1" or int(hz) != 15:
                continue
            coef = m.get("coefficients") or m.get("state", {}).get("coefficients")
            month = m.get("month") or m.get("evaluation_month") or str(m.get("fit_time", ""))[:7]
            rows.append(dict(source=src, month=month, **{f: c for f, c in zip(feats, coef)}))
    return pd.DataFrame(rows).sort_values(["source", "month"]).reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=False)
    D = load()
    results = {"A0-reproduce-frozen.json": a0_reproduce(D)}
    results["A1-economic-magnitude.json"] = a1(D)
    results["A2-drift-and-timing.json"] = a2(D)
    results["A3-inference-robustness.json"] = a3(D)
    results["A4-bounds-and-calibration.json"] = a4(D)
    for name, obj in results.items():
        dump(out / name, obj)
    coef = b6a()
    (out / "B6a-coefficient-paths.csv").write_bytes(coef.to_csv(index=False, float_format="%.10g", lineterminator="\n").encode("ascii"))
    inputs = {str(p): sha(p) for p in (GATE2_PRED, HOLDOUT_PRED)}
    for p in sorted(list(GATE2_EVID.glob("*.json")) + list(GATE2_EVID.glob("*.csv")) + list(HOLDOUT_EVID.glob("*.json")) + list(HOLDOUT_EVID.glob("*.csv"))):
        inputs[p.relative_to(ROOT).as_posix()] = sha(p)
    dump(out / "inputs.json", dict(label=LABEL, script=sha(Path(__file__)), inputs=inputs))
    print("DONE " + json.dumps({p.name: sha(p)[:12] for p in sorted(out.iterdir())}))


if __name__ == "__main__":
    main()
