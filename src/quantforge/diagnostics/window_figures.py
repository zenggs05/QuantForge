"""Deterministic scientific figures for the frozen window experiment."""
from __future__ import annotations

import os
from pathlib import Path

from quantforge import data

FIGURE_NAMES = ("mse-15m.png", "mse-5m.png", "ic-15m.png", "ic-5m.png",
                "bootstrap-main-15m.png", "bootstrap-main-5m.png", "training-samples.png")


def create_window_figures(metrics, bootstrap, hac, output: Path) -> list[str]:
    # Matplotlib's cache must stay inside this project, including on first import.
    os.environ["MPLCONFIGDIR"] = str(data.repository_path(".cache/matplotlib"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    output = data.repository_path(output)
    output.mkdir(parents=True, exist_ok=True)
    programs = ("expanding", "rolling_1m", "rolling_3m", "rolling_6m")
    colors = ("#111827", "#df7025", "#1769a6", "#198766")
    months = ("2026-03", "2026-04", "2026-05", "2026-06")
    with plt.rc_context({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False}):
        def save(fig, name):
            fig.tight_layout()
            fig.savefig(output / name, dpi=150, metadata={"Software": "QuantForge"})
            plt.close(fig)
        for h in (15, 5):
            for metric, title in (("mse", "Mean squared prediction error"), ("pearson_ic", "Pearson time-series IC")):
                fig, ax = plt.subplots(figsize=(8, 4.5))
                for program, color in zip(programs, colors):
                    part = metrics.loc[metrics.model.eq(program) & metrics.horizon_minutes.eq(h)].set_index("month").loc[list(months)]
                    ax.plot(months, part[metric], marker="o", label=program, color=color)
                if metric == "pearson_ic":
                    ax.axhline(0, color="#aaaaaa", linewidth=.7)
                ax.set_title(f"{title}, {h}m | exploratory development data")
                ax.set_ylabel("Squared log-return" if metric == "mse" else "IC")
                ax.legend(fontsize=8, ncol=2)
                ax.grid(alpha=.2)
                save(fig, f"{'mse' if metric == 'mse' else 'ic'}-{h}m.png")
            fig, ax = plt.subplots(figsize=(9, 4.8))
            selected = bootstrap.loc[bootstrap.horizon_minutes.eq(h) & bootstrap.block_length.eq(1440)
                                     & bootstrap.scope_type.eq("role") & bootstrap.aggregation.eq("n_weighted")]
            labels = []
            for position, (_, row) in enumerate(selected.iterrows()):
                labels.append(f"{row.window} / {'Mar-Apr' if row.role == 'validation' else 'May-Jun'}")
                ax.plot([row.relative_ci_low_pct, row.relative_ci_high_pct], [position, position], color="#1769a6", linewidth=2)
                ax.scatter([row.relative_mse_delta_pct], [position], color="#111827", zorder=3)
            ax.set_yticks(np.arange(len(labels)), labels)
            ax.axvline(0, color="#888888", linestyle="--", linewidth=1)
            ax.set_xlabel("MSE change vs Expanding (%) | negative favors Rolling; not returns")
            ax.set_title(f"{h}m paired MBB: L=1440, B=2000, marginal 95% CI")
            ax.grid(axis="x", alpha=.2)
            save(fig, f"bootstrap-main-{h}m.png")
        fig, ax = plt.subplots(figsize=(8, 4.5))
        for program, color in zip(programs, colors):
            part = metrics.loc[metrics.model.eq(program) & metrics.horizon_minutes.eq(15)].set_index("month").loc[list(months)]
            ax.plot(months, part.train_samples, marker="o", color=color, label=program)
        ax.set_title("Legal training observations per 15m fit")
        ax.set_ylabel("Training rows")
        ax.legend(ncol=2, fontsize=8)
        ax.grid(alpha=.2)
        save(fig, "training-samples.png")
    return list(FIGURE_NAMES)
