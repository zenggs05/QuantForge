"""Self-contained synthetic mathematical and fixed-DGP power tests."""
import json
from pathlib import Path
import tomllib

import numpy as np
import pandas as pd
import pytest
from scipy.signal import lfilter
from scipy.stats import chi2, norm, pearsonr
from threadpoolctl import threadpool_limits

from quantforge.diagnostics.forecast_tests import (
    admissible, batched_bartlett_variance, clark_west_differential, cochran_q,
    concentration, dm_differential, effective_sample_size, encompassing_approx,
    ic_product, llsw_lag, mde, months_required, newey_west_segment, normal_power,
    pool_roles, r2_oos, run_size_power_monte_carlo, validate_times,
)
from quantforge.diagnostics.hac_tests import segment_hac_variance


def test_cw_dm_identity_each_element_and_zero_baseline():
    rng=np.random.default_rng(91)
    y,f,b=rng.normal(size=(3,1200))
    d=dm_differential(y,f,b); c=clark_west_differential(y,f,b)
    np.testing.assert_allclose(-d,c-(f-b)**2,rtol=3e-14,atol=1e-14)
    np.testing.assert_array_equal(clark_west_differential(y,f,np.zeros_like(y)),2*f*y)
    assert dm_differential([1.],[1.],[0.])[0] == -1


def test_r2_and_ic_match_independent_definitions_and_degeneracy():
    y=np.array([-2.,0.,1.,4.]); f=np.array([-1.,.3,1.2,2.]); b=np.full(4,.2)
    assert r2_oos(y,f,b)==pytest.approx(1-np.sum((y-f)**2)/np.sum((y-b)**2))
    assert r2_oos(y,f,b)==pytest.approx(-dm_differential(y,f,b).sum()/np.sum((y-b)**2))
    assert ic_product(y,f).mean()==pytest.approx(pearsonr(y,f).statistic,rel=1e-15)
    assert ic_product(y,np.ones(4)) is None
    assert r2_oos(y,f,y) is None


@pytest.mark.parametrize("bad",[[1.,np.nan],[1.,np.inf],[],[[1.,2.]],[True,False],[1j,2j]])
def test_differentials_reject_bad_arrays(bad):
    for function in (dm_differential,clark_west_differential,r2_oos):
        with pytest.raises(ValueError): function(bad,bad,bad)


def test_lengths_and_overflow_rejected():
    with pytest.raises(ValueError,match="equal"): dm_differential([1,2],[1],[0,0])
    with pytest.raises(ValueError,match="overflow"): dm_differential([1e308],[-1e308],[0])


@pytest.mark.parametrize("lag",[0,1,15,60,240])
def test_hac_exactly_reuses_legacy_fixed_denominator(lag):
    x=np.random.default_rng(123).normal(size=1200)
    with threadpool_limits(limits=1):
        old=segment_hac_variance(x,lag); new=newey_west_segment(x,lag)
    assert new["variance"] == old
    assert new["variance"] == pytest.approx(old,rel=1e-15)
    assert new["p_value"] == 2*norm.sf(abs(new["t"]))
    assert new["n_eff"] == pytest.approx(len(x)*new["gamma0"]/new["lrv"])


def test_hac_one_sided_short_constant_and_time_validation():
    x=np.arange(100,dtype=float)
    result=newey_west_segment(x,3,alternative="greater")
    assert result["p_value"]==norm.sf(result["t"])
    assert newey_west_segment(x,3,alternative="less")["p_value"]==norm.cdf(result["t"])
    assert newey_west_segment(x,100)["status"]=="not_estimable"
    assert newey_west_segment(np.ones(100),3)["p_value"] is None
    times=pd.date_range("2026-01-01",periods=100,freq="min",tz="UTC")
    newey_west_segment(x,3,times=times)
    for invalid in (times[::-1],times.tz_localize(None),times.delete(1),times[:50].append(times[:50])):
        with pytest.raises(ValueError): newey_west_segment(x,3,times=invalid)
    with pytest.raises(ValueError): newey_west_segment(x[:99],3,times=times.delete(1))
    with pytest.raises(ValueError): newey_west_segment(x,3,alternative="unknown")


def test_llsw_admissibility_and_normal_quantiles_from_scipy():
    assert llsw_lag(44640,1.3)==275
    for h in (5,15):
        assert not admissible(15,h,4)
        assert all(admissible(lag,h,4) for lag in (60,240,275))
    assert mde(1,.05,.8)==norm.ppf(.95)+norm.ppf(.8)
    assert mde(1,.05,.8,alternative="two-sided")==norm.ppf(.975)+norm.ppf(.8)
    assert mde(1,.05,.8)==pytest.approx(2.486474860524386)


@pytest.mark.parametrize("bad",[-1,True,1.5])
def test_invalid_lag_or_sample_size(bad):
    with pytest.raises(ValueError): newey_west_segment(np.arange(20.),bad)
    with pytest.raises(ValueError): llsw_lag(bad,1.3)


def test_pooling_manual_weights_and_design_effect():
    months=[dict(n=100,mean=.2,variance=.04,gamma0=2.),dict(n=300,mean=.8,variance=.09,gamma0=3.)]
    n=pool_roles(months,pooling="n_weighted",alternative="greater")
    assert n["mean"]==pytest.approx(.25*.2+.75*.8)
    assert n["variance"]==pytest.approx(.25**2*.04+.75**2*.09)
    iid=.25**2*2/100+.75**2*3/300
    assert n["n_eff"]==pytest.approx(400*iid/n["variance"])
    equal=pool_roles(months,pooling="month_equal")
    assert equal["mean"]==.5
    assert equal["variance"]==pytest.approx((.04+.09)/4)
    months[0]["variance"]=None
    assert pool_roles(months,pooling="month_equal")["t"] is None


def test_mde_power_and_months_required_against_normal_reference():
    se=.02; effect=.06
    expected=2*((norm.ppf(.95)+norm.ppf(.8))*se/effect)**2
    assert months_required(effect,se,2,.05,.8)==pytest.approx(expected)
    assert months_required(0,se,2,.05,.8) is None
    assert months_required(-effect,se,2,.05,.8) is None
    assert months_required(-effect,se,2,.05,.8,alternative="less")==pytest.approx(expected)
    assert normal_power(effect,se,.05)==norm.sf(norm.ppf(.95)-effect/se)
    assert normal_power(-effect,se,.05,alternative="less")==normal_power(effect,se,.05)
    expected_two=norm.sf(norm.ppf(.975)-effect/se)+norm.cdf(-norm.ppf(.975)-effect/se)
    assert normal_power(effect,se,.05,alternative="two-sided")==expected_two
    assert normal_power(mde(se,.05,.8),se,.05)==pytest.approx(.8)
    with pytest.raises(ValueError): normal_power(effect,0,.05)
    with pytest.raises(ValueError): mde(se,1,.8)
    with pytest.raises(ValueError): mde(-1,.05,.8)
    with pytest.raises(ValueError): mde(se,.05,1)


def test_ar1_effective_sample_size_analytic_and_finite_bandwidth():
    n=500000; phi=.7
    assert effective_sample_size(n,1, (1+phi)/(1-phi))==pytest.approx(n*(1-phi)/(1+phi))
    x=lfilter([1.],[1.,-phi],np.random.default_rng(47).normal(size=n))
    lag=240
    with threadpool_limits(limits=1): result=newey_west_segment(x,lag)
    finite_lrv_ratio=1+2*sum((1-k/(lag+1))*phi**k for k in range(1,lag+1))
    assert result["n_eff"]==pytest.approx(n/finite_lrv_ratio,rel=.10)
    assert effective_sample_size(n,0,0) is None
    # Negative serial correlation can imply more than n equivalent observations.
    assert effective_sample_size(100,1,.5)==200


@pytest.mark.parametrize("lag",[0,1,15,60,240])
def test_fast_bartlett_equals_independent_legacy_kernel(lag):
    matrix=np.random.default_rng(2).normal(size=(7,700))
    matrix[0]=1
    with threadpool_limits(limits=1):
        expected=np.array([segment_hac_variance(row,lag) for row in matrix])
        actual=batched_bartlett_variance(matrix,lag)
    np.testing.assert_allclose(actual,expected,rtol=1e-12,atol=1e-17)
    assert actual[0]==0


def test_concentration_hand_computation_and_preserved_missing_positions():
    x=np.array([10.,-2.,-3.,1.,-.5])
    times=pd.date_range("2026-01-01",periods=5,freq="min",tz="UTC")
    rows=concentration(x,[1,2],1,times=times)
    row=rows[0]
    retained=x[1:]; mean=retained.mean(); u=np.r_[0.,retained-mean]
    expected_var=(np.dot(u,u)+np.dot(u[1:],u[:-1]))/len(retained)**2
    assert row["signed_share"]==pytest.approx(10/x.sum())
    assert row["mean"]==mean and row["sign_flipped"]
    assert row["variance"]==pytest.approx(expected_var)
    # Remove an interior maximum: original time-lag adjacency differs from compression.
    x=np.array([1.,-1.,9.,2.,-2.]); row=concentration(x,[1],1,times=times)[0]
    retained=x[[0,1,3,4]]; centered=retained-retained.mean()
    grid=np.array([centered[0],centered[1],0,centered[2],centered[3]])
    expected=(np.dot(grid,grid)+np.dot(grid[1:],grid[:-1]))/16
    assert row["variance"]==pytest.approx(expected)
    assert row["variance"] != pytest.approx(segment_hac_variance(retained,1))


def test_concentration_ties_zero_sum_and_invalid_topk():
    row=concentration([2.,-2.,1.,-1.],[1],0)[0]
    assert row["selected_indices"]==[0] and row["signed_share"] is None
    assert not row["sign_flipped"]
    with pytest.raises(ValueError): concentration([1.,2.],[2],0)
    with pytest.raises(ValueError): concentration([1.,2.,3.],[1,1],0)


def test_cochran_hand_example_and_degenerate_months():
    result=cochran_q([1.,3.],[1.,1.])
    assert result["q"]==2 and result["df"]==1
    assert result["inverse_variance_mean"]==2
    assert result["p_value"]==chi2.sf(2,1)
    assert cochran_q([1.,3.],[1.,None])["q"] is None
    assert cochran_q([1.,3.],[1.,0.])["q"] is None
    with pytest.raises(ValueError): cochran_q([1.,3.],[1.,-1.])


def test_encompassing_formula_and_sign_against_improvement():
    e=np.array([-2.,-1.,.5,1.,2.]); delta=.2*e
    result=encompassing_approx(delta,e,100)
    assert result["rho"]==pytest.approx(1)
    assert result["t_approx"]==pytest.approx(10*(1-.1))
    assert encompassing_approx(np.zeros(5),e,100)["t_approx"] is None
    assert encompassing_approx(delta,e,None)["t_approx"] is None


def test_frozen_heavy_tail_overlap_monte_carlo_level_and_power(tmp_path):
    # Configuration is protocol text, not market data or an external fixture.
    config=tomllib.loads((Path(__file__).resolve().parents[1]/"configs/power-reanalysis.toml").read_text(encoding="utf-8"))
    with threadpool_limits(limits=config["experiment"]["cpu_threads"]):
        result=run_size_power_monte_carlo(config["monte_carlo"],llsw_constant=config["inference"]["llsw_constant"])
    (tmp_path/"monte-carlo.json").write_text(json.dumps(result,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print("MONTE_CARLO="+json.dumps(result,sort_keys=True))
    assert result["level_passed"], "Hard stop: frozen Monte Carlo size outside protocol; no real run permitted"
    assert result["power_passed"]
    assert result["elapsed_seconds"]<=config["monte_carlo"]["max_seconds"]
    assert result["mean_empirical_signal_r2"]==pytest.approx(config["monte_carlo"]["signal_r2"],rel=.05)
    null=[row for row in result["rows"] if row["scenario"]=="null"]
    assert all(row["dm_improvement_t_mean"]<0 for row in null)
    assert config["monte_carlo"]["replications"]>=400
