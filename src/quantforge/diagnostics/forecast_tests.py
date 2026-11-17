"""Pure forecast-information tests and explicitly conditional power planning.

The public HAC kernel delegates to the unchanged Phase 2C implementation.
All research choices (lags, thresholds, power, seeds and DGP parameters) are
arguments supplied by the frozen caller configuration; no model is fitted.
"""
from __future__ import annotations

from time import perf_counter
import math

import numpy as np
import pandas as pd
from scipy.signal import lfilter
from scipy.stats import chi2, norm

from quantforge.diagnostics.hac_tests import segment_hac_variance


def _vector(value, name="values") -> np.ndarray:
    raw = np.asarray(value)
    if raw.dtype.kind not in "iuf" or raw.ndim != 1 or not raw.size:
        raise ValueError(f"{name} must be a nonempty real numeric vector")
    result = np.asarray(raw, dtype=np.float64)
    if not np.isfinite(result).all():
        raise ValueError(f"{name} must be finite")
    return result


def _aligned(*values):
    arrays = tuple(_vector(x) for x in values)
    if len({len(x) for x in arrays}) != 1:
        raise ValueError("Input vectors must have equal lengths")
    return arrays


def _finite(result):
    if not np.isfinite(result).all():
        raise ValueError("Forecast arithmetic overflowed")
    return result


def _integer(value, name, minimum=0):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return int(value)


def _number(value, name, *, minimum=None, strict=False):
    if (isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.integer, np.floating))
            or not np.isfinite(value)):
        raise ValueError(f"{name} must be a finite real number")
    result = float(value)
    if minimum is not None and (result <= minimum if strict else result < minimum):
        raise ValueError(f"{name} is outside its permitted range")
    return result


def _alternative(value):
    if value not in ("greater", "less", "two-sided"):
        raise ValueError("alternative must be greater, less or two-sided")
    return value


def validate_times(times, n, *, contiguous=False) -> np.ndarray:
    """Return minute positions; reject unsorted, non-UTC or subminute grids."""
    index = pd.DatetimeIndex(times)
    if (len(index) != n or index.hasnans or index.tz is None or str(index.tz) != "UTC"
            or index.has_duplicates or not index.is_monotonic_increasing):
        raise ValueError("Times must be aligned, sorted, unique timezone-aware UTC")
    ns = index.as_unit("ns").asi8
    delta = np.diff(ns)
    minute = int(pd.Timedelta(minutes=1).value)
    if np.any(delta % minute) or (contiguous and np.any(delta != minute)):
        raise ValueError("Times must retain their original continuous minute grid")
    return (ns-ns[0]) // minute


def dm_differential(y, f, b) -> np.ndarray:
    """Squared loss of model minus baseline; positive means worse error."""
    y, f, b = _aligned(y, f, b)
    with np.errstate(over="ignore", invalid="ignore"):
        return _finite((y-f)**2 - (y-b)**2)


def clark_west_differential(y, f, b) -> np.ndarray:
    """Nested adjusted differential; positive means predictive information."""
    y, f, b = _aligned(y, f, b)
    with np.errstate(over="ignore", invalid="ignore"):
        return _finite(2*(f-b)*(y-b))


def ic_product(y, f) -> np.ndarray | None:
    """Monthly centered product whose mean is ddof=0 Pearson correlation."""
    y, f = _aligned(y, f)
    if np.ptp(y) == 0 or np.ptp(f) == 0:
        return None
    with np.errstate(over="ignore", invalid="ignore"):
        return _finite((f-f.mean())*(y-y.mean()) / (f.std(ddof=0)*y.std(ddof=0)))


def r2_oos(y, f, b) -> float | None:
    """Campbell--Thompson OOS R squared against the supplied baseline."""
    y, f, b = _aligned(y, f, b)
    with np.errstate(over="ignore", invalid="ignore"):
        mse, reference = _finite(np.array([np.mean((y-f)**2), np.mean((y-b)**2)]))
    return None if reference == 0 else float(1-mse/reference)


def _statistics(mean, variance, alternative):
    _alternative(alternative)
    if variance is None:
        return dict(mean=float(mean), variance=None, se=None, t=None, z=None, p_value=None,
                    status="not_estimable", reason="short_or_undefined_segment", alternative=alternative)
    variance = _number(variance, "variance", minimum=0)
    se = math.sqrt(variance)
    if se == 0:
        return dict(mean=float(mean), variance=variance, se=se, t=None, z=None, p_value=None,
                    status="degenerate", reason="zero_variance", alternative=alternative)
    t = float(mean/se)
    _finite(t)
    p = norm.sf(t) if alternative == "greater" else norm.cdf(t) if alternative == "less" else 2*norm.sf(abs(t))
    return dict(mean=float(mean), variance=variance, se=se, t=t, z=t, p_value=float(p),
                status="estimable", reason=None, alternative=alternative)


def newey_west_segment(x, lag, *, times=None, alternative="two-sided") -> dict:
    """Exact legacy fixed-n Bartlett variance, with scipy normal inference."""
    x = _vector(x)
    lag = _integer(lag, "lag")
    if times is not None:
        validate_times(times, len(x), contiguous=True)
    variance = segment_hac_variance(x, lag)
    centered = x-x.mean()
    gamma0 = float(_finite(np.dot(centered, centered)/len(x)))
    lrv = None if variance is None else float(variance*len(x))
    return {**_statistics(float(x.mean()), variance, alternative), "n": len(x), "n_samples": len(x),
            "lag": lag, "gamma0": gamma0, "lrv": lrv,
            "n_eff": None if lrv is None else effective_sample_size(len(x), gamma0, lrv),
            "iid_variance": gamma0/len(x)}


def pool_roles(segments, *, pooling, alternative="two-sided") -> dict:
    """Pool independent monthly means; do not join month-end observations."""
    if not isinstance(segments, (list, tuple)) or not segments:
        raise ValueError("At least one monthly statistic is required")
    if pooling not in ("n_weighted", "month_equal"):
        raise ValueError("Unknown pooling rule")
    sizes = np.array([_integer(s["n"], "n", 1) for s in segments], dtype=np.float64)
    means = np.array([_number(s["mean"], "mean") for s in segments])
    weights = sizes/sizes.sum() if pooling == "n_weighted" else np.ones(len(sizes))/len(sizes)
    variances = [s["variance"] for s in segments]
    variance = None if any(v is None for v in variances) else float(sum(w*w*_number(v, "variance", minimum=0) for w,v in zip(weights,variances)))
    iid = None if any(s.get("gamma0") is None for s in segments) else float(sum(w*w*_number(s["gamma0"], "gamma0", minimum=0)/n for w,n,s in zip(weights,sizes,segments)))
    n = int(sizes.sum())
    result = _statistics(float(np.dot(weights, means)), variance, alternative)
    result.update(n=n, n_samples=n, month_count=len(sizes), weights=weights.tolist(), pooling=pooling,
                  iid_variance=iid, n_eff=None if variance is None or variance == 0 or iid is None else float(n*iid/variance),
                  cross_month_covariance="assumed zero")
    return result


def llsw_lag(n, constant) -> int:
    n = _integer(n, "n", 1)
    constant = _number(constant, "LLSW constant", minimum=0, strict=True)
    return math.ceil(constant*math.sqrt(n))


def admissible(lag, horizon, min_multiple) -> bool:
    return _integer(lag, "lag") >= _integer(horizon, "horizon", 1)*_number(min_multiple, "minimum multiple", minimum=0, strict=True)


def effective_sample_size(n, gamma0, lrv) -> float | None:
    n = _integer(n, "n", 1)
    gamma0 = _number(gamma0, "gamma0", minimum=0)
    lrv = _number(lrv, "LRV", minimum=0)
    return None if lrv == 0 else float(_finite(n*gamma0/lrv))


def _power_inputs(se, alpha, power=None, alternative="greater"):
    se = _number(se, "SE", minimum=0)
    alpha = _number(alpha, "alpha", minimum=0, strict=True)
    if alpha >= 1:
        raise ValueError("alpha must be below one")
    _alternative(alternative)
    if power is not None:
        power = _number(power, "power", minimum=0, strict=True)
        if power >= 1:
            raise ValueError("power must be below one")
    critical = float(norm.ppf(1-alpha/(2 if alternative == "two-sided" else 1)))
    return se, alpha, power, critical


def mde(se, alpha, power, *, alternative="greater") -> float:
    """Normal-reference planning approximation, not a selected effect estimate."""
    se, _, power, critical = _power_inputs(se, alpha, power, alternative)
    return float(_finite((critical+norm.ppf(power))*se))


def normal_power(effect, se, alpha, *, alternative="greater") -> float:
    """Normal test power under a stipulated mean shift and fixed SE."""
    effect = _number(effect, "effect")
    se, _, _, critical = _power_inputs(se, alpha, alternative=alternative)
    if se == 0:
        raise ValueError("Power requires positive SE")
    shift = effect/se
    if alternative == "two-sided":
        return float(norm.sf(critical-shift)+norm.cdf(-critical-shift))
    return float(norm.sf(critical-(shift if alternative == "greater" else -shift)))


def months_required(effect, se, observed_months, alpha, power, *, alternative="greater") -> float | None:
    """Required months assuming independent identically distributed months."""
    effect = _number(effect, "effect")
    observed_months = _number(observed_months, "observed months", minimum=0, strict=True)
    threshold = mde(se, alpha, power, alternative=alternative)
    magnitude = abs(effect) if alternative == "two-sided" else effect if alternative == "greater" else -effect
    return None if magnitude <= 0 else float(_finite(observed_months*(threshold/magnitude)**2))


def encompassing_approx(delta, residual, n_eff) -> dict:
    """Improvement-direction planning approximation, compared with minus DM z."""
    delta, residual = _aligned(delta, residual)
    if n_eff is None:
        return dict(rho=None, sigma_delta=float(delta.std()), sigma_error=float(residual.std()), t_approx=None, reason="undefined_effective_sample_size")
    n_eff = _number(n_eff, "n_eff", minimum=0, strict=True)
    sd, se = float(delta.std(ddof=0)), float(residual.std(ddof=0))
    if sd == 0 or se == 0:
        return dict(rho=None, sigma_delta=sd, sigma_error=se, t_approx=None, reason="constant_delta_or_residual")
    rho = float(np.mean((delta-delta.mean())*(residual-residual.mean()))/(sd*se))
    value = float(_finite(math.sqrt(n_eff)*(rho-sd/(2*se))))
    return dict(rho=rho, sigma_delta=sd, sigma_error=se, n_eff=n_eff, t_approx=value, reason=None)


def _bartlett_sum(centered: np.ndarray, lag: int) -> np.ndarray:
    """Sum of Bartlett quadratic products via zero-extended moving sums."""
    q = lag+1
    padded = np.pad(centered, ((0,0),(lag,lag)))
    cumulative = np.pad(np.cumsum(padded, axis=1), ((0,0),(1,0)))
    windows = cumulative[:,q:]-cumulative[:,:-q]
    return np.einsum("ij,ij->i", windows, windows)/q


def batched_bartlett_variance(values, lag) -> np.ndarray:
    """Fast O(B*n) equivalent for synthetic MC/placebo, independent of lag.

    For zero-extended centered u, sum of squared (L+1)-term moving sums
    divided by L+1 equals sum(u**2)+2*sum_k Bartlett(k)*sum(u_t*u_t-k).
    Production segment inference above still uses the unchanged legacy kernel.
    """
    raw = np.asarray(values)
    lag = _integer(lag, "lag")
    if raw.ndim != 2 or raw.dtype.kind not in "iuf" or not raw.shape[0] or raw.shape[1] <= lag:
        raise ValueError("Batched Bartlett requires finite rows with n > lag")
    matrix = np.asarray(raw, dtype=np.float64)
    if not np.isfinite(matrix).all():
        raise ValueError("Batched Bartlett input must be finite")
    centered = matrix-matrix.mean(axis=1,keepdims=True)
    return _finite(_bartlett_sum(centered, lag)/(matrix.shape[1]**2))


def concentration(x, top_k, lag, *, times=None, alternative="two-sided") -> list[dict]:
    """Top-|x| deletion sensitivity on the original minute grid, not compressed."""
    x = _vector(x)
    lag = _integer(lag, "lag")
    positions = np.arange(len(x)) if times is None else validate_times(times, len(x))
    if times is not None and pd.DatetimeIndex(times).strftime("%Y-%m").nunique() != 1:
        raise ValueError("Concentration is a within-month sensitivity")
    ks = [_integer(k,"top_k",1) for k in top_k]
    if not ks or len(set(ks)) != len(ks):
        raise ValueError("top_k must be nonempty and unique")
    selected = np.argsort(-np.abs(x),kind="stable")
    total, original_mean = float(x.sum()), float(x.mean())
    rows=[]
    for k in ks:
        if k >= len(x):
            raise ValueError("top_k must leave at least one retained observation")
        removed = selected[:k]
        keep = np.ones(len(x),dtype=bool); keep[removed]=False
        retained=x[keep]; mean=float(retained.mean()); n=len(retained)
        grid=np.zeros(int(positions[-1])+1,dtype=np.float64)
        grid[positions[keep]]=retained-mean
        variance=None if len(grid)<=lag else float(_bartlett_sum(grid[None,:],lag)[0]/n**2)
        row={**_statistics(mean,variance,alternative),"top_k":k,"removed_count":k,"remaining_n":n,
             "original_mean":original_mean,"original_sum":total,"removed_sum":float(x[removed].sum()),
             "signed_share":None if total==0 else float(x[removed].sum()/total),
             "sign_flipped":bool(mean*original_mean<0),"selected_indices":removed.tolist(),
             "preserved_time_gaps":True,"gamma_denominator":"remaining_n",
             "centering":"retained monthly mean; missing centered grid positions zero"}
        rows.append(row)
    return rows


def cochran_q(means, variances) -> dict:
    """Inverse-HAC-variance heterogeneity diagnostic; weak with two months."""
    means=_vector(means,"monthly means")
    if len(means)<2 or len(variances)!=len(means):
        raise ValueError("Cochran Q requires aligned estimates from at least two months")
    if any(v is None for v in variances):
        return dict(q=None,Q=None,df=len(means)-1,p_value=None,inverse_variance_mean=None,status="not_estimable",reason="undefined_month_variance")
    variances=_vector(variances,"monthly variances")
    if np.any(variances<0):
        raise ValueError("Variances must be nonnegative")
    if np.any(variances==0):
        return dict(q=None,Q=None,df=len(means)-1,p_value=None,inverse_variance_mean=None,status="degenerate",reason="zero_month_variance")
    weights=1/variances
    center=float(np.dot(weights,means)/weights.sum())
    q=float(_finite(np.dot(weights,(means-center)**2)))
    return dict(q=q,Q=q,df=len(means)-1,p_value=float(chi2.sf(q,len(means)-1)),inverse_variance_mean=center,status="estimable",reason=None)


def run_size_power_monte_carlo(settings: dict, *, llsw_constant: float) -> dict:
    """Frozen synthetic SV/t simulation; never reads data or fits any model.

    The caller owns the stop rule. This function returns every rejection rate,
    including a failed level check; it never retunes or retries its DGP.
    """
    started=perf_counter()
    seed=_integer(settings["seed"],"seed")
    reps=_integer(settings["replications"],"replications",1)
    n=_integer(settings["minute_points_per_month"],"monthly observations",1)
    months=_integer(settings["months"],"months",1)
    h=_integer(settings["label_horizon_minutes"],"horizon",1)
    if settings["future_horizon_minutes"] != h:
        raise ValueError("Future and label horizons must agree")
    burn=_integer(settings["burnin_minutes"],"burnin")
    batch=_integer(settings["batch_size"],"batch size",1)
    phi=_number(settings["logvol_ar1_phi"],"AR coefficient")
    if abs(phi)>=1: raise ValueError("Log volatility must be stationary")
    volsd=_number(settings["stationary_logvol_sd"],"stationary logvol SD",minimum=0)
    df=_number(settings["student_t_df"],"Student-t df",minimum=2,strict=True)
    r2=_number(settings["signal_r2"],"signal R squared",minimum=0,strict=True)
    if r2>=1: raise ValueError("Signal R squared must be below one")
    slope=_number(settings["known_slope"],"known slope",minimum=0,strict=True)
    alpha=settings["alpha"]
    _,_,_,critical_one=_power_inputs(1.0,alpha,alternative="greater")
    _,_,_,critical_two=_power_inputs(1.0,alpha,alternative="two-sided")
    stream_ids=[_integer(v,"stream id") for v in settings["stream_ids"]]
    if len(stream_ids)!=3 or len(set(stream_ids))!=3:
        raise ValueError("Three independent stream IDs required")
    if (settings["innovation_distribution"]!="standardized_student_t" or settings["signal_distribution"]!="standard_normal"
            or settings["volatility_process"]!="log_vol_ar1"):
        raise ValueError("Unsupported frozen Monte Carlo distribution")
    lags=[_integer(l,"level lag") for l in settings["level_lags"]]
    if settings["include_llsw"]: lags.append(llsw_lag(n,llsw_constant))
    if any(l>=n for l in lags) or len(set(lags))!=len(lags): raise ValueError("Distinct estimable MC lags required")
    # Fields: null CW, null DM, alternative CW, alternative DM; monthly pooling
    # is equal to n weighting because every simulated month has the same n.
    statistics={lag:np.empty((reps,4)) for lag in lags}
    empirical_r2=[]
    for start in range(0,reps,batch):
        stop=min(reps,start+batch); count=stop-start
        pooled_means=np.zeros((count,4)); pooled_variances={lag:np.zeros((count,4)) for lag in lags}
        for month in range(months):
            e=np.empty((count,n)); f=np.empty((count,n))
            for local,rep in enumerate(range(start,stop)):
                rngv,rnge,rngs=[np.random.default_rng(np.random.SeedSequence([seed,rep,month,s])) for s in stream_ids]
                length=burn+n+h
                innovations=rngv.normal(0,volsd*math.sqrt(1-phi**2),length)
                initial=rngv.normal(0,volsd)
                logvol=lfilter([1.0],[1.0,-phi],innovations,zi=[phi*initial])[0]
                noise=np.exp(logvol/2-volsd**2/4)*rnge.standard_t(df,length)*math.sqrt((df-2)/df)
                signal=rngs.normal(size=length)*math.sqrt(r2/(1-r2))/slope
                # Future sum for decisions burn .. burn+n-1: components t+1..t+h.
                for source,target in ((noise,e),(signal,f)):
                    cumulative=np.r_[0.0,np.cumsum(source)]
                    target[local]=cumulative[burn+h+1:burn+h+n+1]-cumulative[burn+1:burn+n+1]
                f[local]*=slope
                empirical_r2.append(float(np.var(f[local])/(np.var(e[local])+np.var(f[local]))))
            cw_null=2*f*e; f2=f*f
            sequences=(cw_null,f2-cw_null,cw_null+2*f2,-cw_null-f2)
            for i,seq in enumerate(sequences):
                pooled_means[:,i]+=seq.mean(axis=1)/months
                for lag in lags:
                    pooled_variances[lag][:,i]+=batched_bartlett_variance(seq,lag)/(months**2)
        for lag in lags:
            statistics[lag][start:stop]=pooled_means/np.sqrt(pooled_variances[lag])
    rows=[]
    for lag in lags:
        ts=statistics[lag]
        for scenario,cw_index,dm_index in (("null",0,1),("alternative",2,3)):
            cw,dm=ts[:,cw_index],ts[:,dm_index]
            rates={"cw_one_sided":float(np.mean(cw>critical_one)),"dm_two_sided":float(np.mean(np.abs(dm)>critical_two)),
                   "dm_improvement_one_sided":float(np.mean(-dm>critical_one))}
            rows.append({"evidence_level":"synthetic","scenario":scenario,"lag":lag,"replications":reps,
                         **rates,"cw_t_mean":float(cw.mean()),"cw_t_sd":float(cw.std(ddof=1)),
                         "dm_z_mean":float(dm.mean()),"dm_improvement_t_mean":float(-dm.mean()),
                         "rejection_mc_se":{key:math.sqrt(rate*(1-rate)/reps) for key,rate in rates.items()}})
    level_passed=all(settings["level_min"]<=row["cw_one_sided"]<=settings["level_max"] for row in rows if row["scenario"]=="null")
    power_passed=all(row["cw_one_sided"]>max(row["dm_two_sided"],row["dm_improvement_one_sided"]) for row in rows if row["scenario"]=="alternative")
    return {"evidence_level":"synthetic","settings":dict(settings),"llsw_constant":llsw_constant,
            "rows":rows,"level_passed":level_passed,"power_passed":power_passed,
            "mean_empirical_signal_r2":float(np.mean(empirical_r2)),"normal_critical_one_sided":critical_one,
            "normal_critical_two_sided":critical_two,"elapsed_seconds":perf_counter()-started,
            "note":"DM improvement t is minus the formal model-minus-baseline loss statistic; no DGP tuning."}
