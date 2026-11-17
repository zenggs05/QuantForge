"""Static research figures from frozen real predictions and derived diagnostics."""
from __future__ import annotations

from pathlib import Path
import os

import numpy as np
import pandas as pd

from quantforge import data


def generate_figures(paired, calibration, quantiles, bootstrap, acf, output: Path) -> list[Path]:
    # Keep Matplotlib's font/cache writes inside the project, including on Windows.
    cache=data.repository_path('.cache/matplotlib')
    cache.mkdir(parents=True, exist_ok=True)
    os.environ['MPLCONFIGDIR']=str(cache)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size':9,'axes.spines.top':False,'axes.spines.right':False,'savefig.dpi':160})
    output=data.repository_path(output); output.mkdir(parents=True,exist_ok=True)
    months=('2026-03','2026-04','2026-05','2026-06')
    labels=['Mar (validation)','Apr (validation)','May (development)','Jun (development)']
    paths=[]

    def save(fig, name, caption):
        fig.text(.01,.01,caption,fontsize=8,va='bottom')
        fig.tight_layout(rect=(0,.055,1,.96))
        path=data.repository_path(output/name); fig.savefig(path); plt.close(fig); paths.append(path)

    for h in (15,5):
        subset=paired[paired.horizon_minutes==h]
        cal=calibration[calibration.horizon_minutes==h]
        fig,axes=plt.subplots(2,2,figsize=(10,7))
        for ax,month,label in zip(axes.flat,months,labels,strict=True):
            frame=subset[subset.month==month]
            y=frame.target_return.to_numpy()*10000; p=frame.prediction_ridge.to_numpy()*10000
            low,high=np.quantile(np.r_[y,p],[.005,.995]); edges=np.linspace(low,high,81)
            for values,name in ((y,'Target'),(p,'Ridge prediction')):
                outside=np.mean((values<low)|(values>high))*100
                ax.hist(values,bins=edges,density=True,histtype='step',label=f'{name}; outside {outside:.2f}%')
            ax.set(title=label,xlabel='Log return (basis points)',ylabel='Density'); ax.legend(fontsize=7)
        fig.suptitle(f'{h}m prediction and target distributions')
        save(fig,f'prediction-target-{h}m.png','Display range: combined 0.5%-99.5% quantiles per panel. All statistics retain every observation.')

        fig,axes=plt.subplots(2,2,figsize=(10,7))
        for ax,month,label in zip(axes.flat,months,labels,strict=True):
            q=quantiles[(quantiles.horizon_minutes==h)&(quantiles.month==month)&(quantiles.model=='ridge')]
            x=q.mean_prediction.to_numpy()*10000; y=q.mean_target.to_numpy()*10000
            ax.plot(x,y,'o-',label='Prediction-quantile means')
            bounds=[min(x.min(),y.min()),max(x.max(),y.max())]
            ax.plot(bounds,bounds,'--',color='gray',label='Identity')
            ax.set(title=label,xlabel='Mean Ridge prediction (bp)',ylabel='Mean target (bp)'); ax.legend(fontsize=7)
        fig.suptitle(f'{h}m Ridge quantile calibration (descriptive)')
        save(fig,f'quantile-calibration-{h}m.png','Value-based deciles; ties stay together. No IID error bars. No recalibration is applied.')

        fig,axes=plt.subplots(2,2,figsize=(10,7))
        for ax,month,label in zip(axes.flat,months,labels,strict=True):
            frame=subset[subset.month==month]
            values={m:(frame[f'prediction_{m}']-frame.target_return).to_numpy()*10000 for m in ('zero','historical_mean','ridge')}
            low,high=np.quantile(np.concatenate(list(values.values())),[.005,.995]); edges=np.linspace(low,high,81)
            for name,residual in values.items():
                outside=np.mean((residual<low)|(residual>high))*100
                ax.hist(residual,bins=edges,density=True,histtype='step',label=f'{name}; outside {outside:.2f}%')
            ax.set(title=label,xlabel='Residual: prediction - target (bp)',ylabel='Density'); ax.legend(fontsize=7)
        fig.suptitle(f'{h}m residual distributions')
        save(fig,f'residual-distribution-{h}m.png','Display range: combined 0.5%-99.5% residual quantiles per panel. Summaries include all tails.')

        fig,ax=plt.subplots(figsize=(9,4.6))
        c=cal[cal.model=='ridge'].set_index('month').loc[list(months)]
        ax.plot(labels,c.pearson_ic,'o-',label='Ridge Pearson IC')
        ax.plot(labels,c.spearman_correlation,'s-',label='Ridge Spearman')
        ax.axhline(0,color='gray',lw=.8); ax.axvline(1.5,color='gray',ls=':')
        ax.set(ylabel='Within-month correlation',title=f'{h}m monthly correlations'); ax.legend()
        save(fig,f'monthly-ic-{h}m.png','Zero/Mean are constant within month: correlations undefined. Positive IC is not evidence of lower MSE or profit.')

        fig,ax=plt.subplots(figsize=(9,4.6))
        x=np.arange(4)
        for offset,base in ((-.17,'zero'),(.17,'historical_mean')):
            base_mse=cal[cal.model==base].set_index('month').loc[list(months)].mse.to_numpy()
            delta=c.mse.to_numpy()-base_mse
            ax.bar(x+offset,delta*1e9,width=.32,label=f'Ridge - {base}')
        ax.axhline(0,color='gray',lw=.8); ax.axvline(1.5,color='gray',ls=':')
        ax.set(xticks=x,xticklabels=labels,ylabel='MSE difference (x 1e-9)',title=f'{h}m paired monthly MSE difference'); ax.legend()
        save(fig,f'monthly-mse-difference-{h}m.png','Negative means Ridge has lower squared error. Full months shown; no selection by significance.')

        fig,axes=plt.subplots(1,2,figsize=(13,7),sharex=False)
        bs=bootstrap[bootstrap.horizon_minutes==h]
        for ax,base in zip(axes,('zero','historical_mean'),strict=True):
            rows=bs[bs.baseline==base].copy()
            rows['label']=rows.scope_id.astype(str)+' / '+rows.aggregation.astype(str)
            order=list(dict.fromkeys(rows['label']))
            for k,L in enumerate((60,360,1440,2880)):
                selected=rows[rows.block_length==L].set_index('label').reindex(order)
                lo=selected.ci_low.to_numpy(float)*1e9; hi=selected.ci_high.to_numpy(float)*1e9
                mean=selected.estimate_mse_delta.to_numpy(float)*1e9; positions=np.arange(len(order))+(k-1.5)*.16
                color=f'C{k}'
                ax.hlines(positions,lo,hi,color=color,lw=1.6)
                ax.plot(mean,positions,'o',color=color,ms=3,label=f'L={L}'+(' (main)' if L==1440 else ''))
            ax.axvline(0,color='gray',ls='--'); ax.set(yticks=np.arange(len(order)),yticklabels=order,
                xlabel='Paired MSE difference (x 1e-9)',title=f'{h}m Ridge - {base}'); ax.invert_yaxis(); ax.legend(fontsize=8)
        save(fig,f'bootstrap-ci-{h}m.png','2,000 paired moving-block replicates; marginal 95% percentile CIs. Role estimates condition on two observed months.')

        fig,axes=plt.subplots(2,2,figsize=(10,7))
        for ax,month,label in zip(axes.flat,months,labels,strict=True):
            for base in ('zero','historical_mean'):
                rows=acf[(acf.horizon_minutes==h)&(acf.month==month)&(acf.baseline==base)]
                ax.plot(rows.lag,rows.acf,label=f'Ridge - {base}')
            ax.axhline(0,color='gray',lw=.8); ax.set(title=label,xlabel='Lag (minute observations)',ylabel='Loss-difference ACF'); ax.legend(fontsize=7)
        fig.suptitle(f'{h}m paired squared-loss autocorrelation')
        save(fig,f'loss-acf-{h}m.png','Products never cross month or gap boundaries. Descriptive ACF; no IID significance bands.')
    return paths
