"""Synthetic mathematical specification audits with training-only prediction spies."""

from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from quantforge.diagnostics import spec_audit as audit
from quantforge.features import build_features
from quantforge.labels import build_labels, mature_labels


def settings():
    return {"experiment":{"horizons":[15,5],"ridge_alpha":1.,"solver":"svd","fit_intercept":True,"availability_delay_seconds":1.},
            "spec_audit":{"initial_fit_time":"2026-03-01T00:00:00Z","initial_start":"2025-09-01T00:00:00Z",
               "duplicate_corr_threshold":.999,"windows":["six_month","three_month","one_month"],
               "window_starts":{"six_month":"2025-09-01T00:00:00Z","three_month":"2025-12-01T00:00:00Z","one_month":"2026-02-01T00:00:00Z"},
               "gap_max_abs_tolerance":1e-12,"top_k":[1,10,100,1000],"gap_quantiles":[0.,.5,1.],
               "top_timestamps_count":10,"crash_session_start":"2026-02-28T04:00:00Z","crash_session_end":"2026-02-28T06:00:00Z",
               "feature_count":14,"specifications":["v1","v1_crash_excluded","v2"],"model_fit_count":6,"correlation_ddof":0},
            "cost_magnitude":{"return_to_bp":10000.},"inputs":{"development_manifest_path":"synthetic"}}


def synthetic_training(minute_bars):
    bars=minute_bars(1440,start="2026-02-28T00:00:00Z")
    features=build_features(bars)
    labels=build_labels(bars,15)
    metadata=features.metadata.join(labels.frame)
    fit=pd.Timestamp("2026-03-01T00:00:00Z")
    valid=metadata.valid_features & metadata.valid_label & metadata.label_available_time.le(fit)
    index=metadata.index[valid]
    return features.frame.loc[index],mature_labels(labels.frame,fit,index),metadata.loc[index],bars


def test_duplicate_features_uses_canonical_later_column():
    rng=np.random.default_rng(42); x=rng.normal(size=1000)
    X=pd.DataFrame({"first":x,"unrelated":rng.normal(size=1000),"later":x+1e-6*rng.normal(size=1000)},index=pd.date_range("2025-09-01",periods=1000,freq="min",tz="UTC"))
    result=audit.duplicate_features(X,.999)
    assert result["deleted_features"]==["later"]
    assert result["retained_features"]==["first","unrelated"]
    assert result["duplicate_pairs"][0]["correlation"]>.999


def test_eigenvalues_condition_and_shrinkage_match_explicit_matrix():
    rng=np.random.default_rng(50)
    X=pd.DataFrame(rng.normal(size=(800,4)),index=pd.date_range("2025-09-01",periods=800,freq="min",tz="UTC"))
    result=audit.spectral_diagnostics(X,1.)
    z=StandardScaler().fit_transform(X); z-=z.mean(axis=0)
    C=z.T@z/len(z); eigen=np.linalg.eigvalsh((C+C.T)/2)
    np.testing.assert_allclose(result["eigenvalues"],eigen,rtol=1e-15)
    np.testing.assert_allclose(result["shrinkage_factors"],len(z)*eigen/(len(z)*eigen+1),rtol=1e-15)
    assert result["ridge_condition"]==pytest.approx(np.linalg.cond(z.T@z+np.eye(4)),rel=1e-13)


def test_gap_identity_on_synthetic_bars_and_detects_mutation(minute_bars):
    X,y,metadata,bars=synthetic_training(minute_bars)
    result=audit.gap_identity(X,bars,1e-12)
    assert result["max_absolute_error"]<=1e-12
    changed=X.copy();changed.iloc[0,changed.columns.get_loc("log_close_open")]+=.01
    with pytest.raises(ValueError,match="Gap identity"):
        audit.gap_identity(changed,bars,1e-12)


def test_leverage_rankings_signed_shares_and_crash_manual():
    times=pd.date_range("2025-10-10T20:00:00Z",periods=4,freq="min")
    result=audit.leverage_shares([1,-2,3,0],[1,2,-1,0],times,top_k=[1,2],quantiles=[.5],top_timestamps_count=2,crash_start=times[0],crash_end=times[2])
    rows={(r["quantity"],r["requested_k"]):r for r in result["top_shares"]}
    assert rows["gap_squared",1]["share"]==pytest.approx(9/14)
    assert rows["gap_y",1]["share"]==pytest.approx(-4/-6)
    assert result["zero_fraction"]==.25
    crash={r["quantity"]:r for r in result["crash_shares"]}
    assert crash["gap_squared"]["share"]==pytest.approx(5/14)
    assert crash["gap_y"]["share"]==pytest.approx(-3/-6)
    assert result["top_timestamps"][0]["times"][0]==times[2].isoformat()
    assert result["top_timestamps"][1]["times"][0]==times[1].isoformat()


def test_zero_leverage_denominators_are_explicit_nulls():
    times=pd.date_range("2025-10-10T20:00:00Z",periods=3,freq="min")
    result=audit.leverage_shares([0,0,0],[1,2,3],times,top_k=[1],quantiles=[.5],top_timestamps_count=2,crash_start=times[0],crash_end=times[-1])
    assert all(r["share"] is None and r["reason"]=="zero_denominator" for r in result["top_shares"])


def test_training_audit_predicts_only_initial_train(monkeypatch,minute_bars):
    X,y,metadata,bars=synthetic_training(minute_bars)
    calls=[];original_predict=Pipeline.predict
    def guarded_predict(self,frame,*args,**kwargs):
        assert frame.index.max()+pd.Timedelta(minutes=1,seconds=1)<pd.Timestamp("2026-03-01T00:00:00Z")
        calls.append(frame.index.copy())
        return original_predict(self,frame,*args,**kwargs)
    monkeypatch.setattr(Pipeline,"predict",guarded_predict)
    result=audit.audit_training_set(X,y,metadata,bars,settings(),15)
    assert len(calls)==3
    assert result["model_fit_count"]==3 and result["evaluation_predictions"]==0
    assert [f["specification"] for f in result["fits"]]==["v1","v1_crash_excluded","v2"]
    assert result["fits"][1]["n"]<result["fits"][0]["n"]
    assert all(f["last_decision"]<"2026-03-01" for f in result["fits"])


@pytest.mark.parametrize("column",["decision_time","label_available_time","feature_available_time"])
def test_future_training_metadata_rejected_before_any_fit(monkeypatch,minute_bars,column):
    X,y,metadata,bars=synthetic_training(minute_bars)
    metadata=metadata.copy();metadata.iloc[-1,metadata.columns.get_loc(column)]=pd.Timestamp("2026-03-01T00:00:01Z")
    def forbidden(*args,**kwargs):raise AssertionError("Invalid audit reached fit/predict")
    monkeypatch.setattr(Pipeline,"fit",forbidden);monkeypatch.setattr(Pipeline,"predict",forbidden)
    with pytest.raises(ValueError,match="Initial Train"):
        audit.audit_training_set(X,y,metadata,bars,settings(),15)


def test_spectral_and_duplicate_reject_nonfinite_inputs():
    X=pd.DataFrame({"x":[1.,np.nan]},index=pd.date_range("2025-09-01",periods=2,freq="min",tz="UTC"))
    with pytest.raises(ValueError,match="finite"):audit.duplicate_features(X,.999)
    with pytest.raises(ValueError,match="finite"):audit.spectral_diagnostics(X,1.)


def test_runner_reuses_march_split_and_performs_exactly_six_training_fits(monkeypatch,minute_bars):
    bars=minute_bars(1440,start="2026-02-28T00:00:00Z")
    calls=[]
    def synthetic_split(metadata,horizon):
        fit=pd.Timestamp("2026-03-01T00:00:00Z")
        mask=metadata.valid_features & metadata.valid_label & metadata.label_available_time.le(fit)
        calls.append(horizon)
        return [SimpleNamespace(fit_time=fit,train_indices=metadata.index[mask])]
    monkeypatch.setattr(audit,"make_research_splits",synthetic_split)
    monkeypatch.setattr(audit,"load_research_dataset",lambda *a:(bars,{"overall_manifest_sha256":"synthetic"}))
    original=Pipeline.predict
    predictions=[]
    def training_only(self,X,*args,**kwargs):
        assert X.index.max()<pd.Timestamp("2026-03-01T00:00:00Z")
        predictions.append(len(X))
        return original(self,X,*args,**kwargs)
    monkeypatch.setattr(Pipeline,"predict",training_only)
    result=audit.run_specification_audit(settings())
    assert calls==[15,5]
    assert len(predictions)==result["model_fit_count"]==6
    assert result["evaluation_predictions"]==0
    assert result["source_manifest_sha256"]=="synthetic"
