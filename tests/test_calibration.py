"""Mathematical oracles using explicitly synthetic predictions and targets."""
import numpy as np
import pandas as pd
import pytest
from quantforge.diagnostics.calibration import calibration_metrics, prediction_quantiles, loss_autocorrelation


def test_synthetic_exact_calibration_moments_and_decomposition():
    p = np.array([-2., -1., 0., 1., 2.])
    y = 2*p+3
    m=calibration_metrics(p,y)
    assert m['diagnostic_slope']==2 and m['diagnostic_intercept']==3
    assert m['prediction_mean']==0 and m['target_mean']==3
    assert m['std_ratio']==.5 and m['prediction_bias']==-3
    assert m['pearson_ic']==pytest.approx(1) and m['spearman_correlation']==pytest.approx(1)
    assert m['mse']==pytest.approx(np.mean((p-y)**2))
    assert m['mse_from_decomposition']==pytest.approx(m['mse'])
    assert m['mse_delta_zero_from_moments']==pytest.approx(m['mse']-np.mean(y*y))


def test_synthetic_constant_prediction_is_not_a_correlation_or_calibrator():
    m=calibration_metrics(np.zeros(8),np.arange(8.))
    assert m['pearson_ic'] is None and m['spearman_correlation'] is None
    assert m['diagnostic_slope'] is None and m['std_ratio']==0
    bins=prediction_quantiles(np.zeros(8),np.arange(8.))
    assert len(bins)==1 and bins.n_samples.iloc[0]==8 and bins.mean_target.iloc[0]==3.5


def test_synthetic_quantile_bins_preserve_ties_weighted_means_and_counts():
    p=np.repeat([0.,1.,2.,3.],[40,30,20,10]); y=np.arange(100.)
    bins=prediction_quantiles(p,y)
    assert bins.n_samples.sum()==100
    assert np.average(bins.mean_target,weights=bins.n_samples)==pytest.approx(y.mean())
    assert np.average(bins.mean_prediction,weights=bins.n_samples)==pytest.approx(p.mean())
    assert all(bins.prediction_max.to_numpy()[:-1] < bins.prediction_min.to_numpy()[1:])


@pytest.mark.parametrize('prediction,target',[([],[]),([1,2],[1]),([np.nan],[1]),([1],[np.inf])])
def test_synthetic_invalid_calibration_rejected(prediction,target):
    with pytest.raises(ValueError): calibration_metrics(prediction,target)


def test_synthetic_acf_cannot_bridge_a_gap():
    frame=pd.DataFrame({'month':'2026-03','role':'validation','horizon_minutes':15,
        'decision_time':pd.to_datetime(['2026-03-01T00:01:01Z','2026-03-01T00:02:01Z','2026-03-01T00:05:01Z','2026-03-01T00:06:01Z']),
        'squared_loss_ridge':[1.,2.,3.,4.],'squared_loss_zero':0.,'squared_loss_historical_mean':0.})
    out=loss_autocorrelation(frame,2).query("baseline=='zero'")
    assert out.loc[out.lag==1,'paired_products'].iloc[0]==2
    assert out.loc[out.lag==2,'paired_products'].iloc[0]==0
    assert pd.isna(out.loc[out.lag==2,'acf'].iloc[0])
    assert out.loc[out.lag==2,'null_reason'].iloc[0]=='no_contiguous_products'
    assert out.loc[out.lag==1,'acf'].iloc[0]==pytest.approx(1.5/5.)
