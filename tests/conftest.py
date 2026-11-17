"""Deterministic artificial bars for causal research contract tests."""

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def minute_bars():
    """Return a factory; these values are synthetic, never market observations."""
    def make(periods=240, start="2026-09-01T00:00:00Z"):
        position = np.arange(periods)
        opens = pd.date_range(start, periods=periods, freq="min")
        close = 100 * np.exp(0.0002 * position + 0.001 * np.sin(position / 7))
        opening = close * (1 + 0.0001 * np.cos(position / 4))
        volume = 10 + position % 17 + 0.05 * position
        return pd.DataFrame({
            "open_time": opens, "close_time": opens + pd.Timedelta(minutes=1) - pd.Timedelta(microseconds=1),
            "open": opening, "high": np.maximum(opening, close) * 1.001,
            "low": np.minimum(opening, close) * 0.999, "close": close,
            "volume": volume, "number_of_trades": 20 + position % 23,
            "taker_buy_base_asset_volume": volume * 0.4,
            "quote_asset_volume": volume * close,
            "taker_buy_quote_asset_volume": volume * close * 0.4,
        })
    return make


@pytest.fixture
def noisy_minute_bars():
    """Return D-026 synthetic bars with frozen noise and a measured OLS gate."""
    from quantforge.online.hardening_config import load_hardening_config

    config = load_hardening_config()
    values = config["synthetic"]

    def make(periods=None, start=None, seed=None, sigma_noise=None, sigma_signal=None):
        from quantforge.features import build_features
        from quantforge.labels import build_labels
        from threadpoolctl import threadpool_limits

        periods = values["periods"] if periods is None else periods
        start = values["start"] if start is None else start
        seed = values["seed"] if seed is None else seed
        sigma_noise = values["sigma_noise"] if sigma_noise is None else sigma_noise
        sigma_signal = values["sigma_signal"] if sigma_signal is None else sigma_signal
        if (seed != values["seed"] or sigma_noise != values["sigma_noise"]
                or sigma_signal != values["sigma_signal"]):
            raise ValueError("Phase 2F synthetic seed and noise parameters are frozen")
        rng = np.random.default_rng(seed)
        position = np.arange(periods)
        signal = sigma_signal * np.sin(position / values["signal_period_divisor"])
        noise = sigma_noise * rng.standard_normal(periods)
        close = np.exp(np.log(values["initial_price"]) + np.cumsum(signal + noise))
        opening = np.r_[close[0], close[:-1]]
        high = np.maximum(opening, close) * (1 + np.abs(rng.standard_normal(periods)) * values["wick_scale"])
        low = np.minimum(opening, close) * (1 - np.abs(rng.standard_normal(periods)) * values["wick_scale"])
        volume = (values["volume_base"] + position % values["volume_period"] + values["volume_trend"] * position
                  + rng.gamma(values["volume_gamma_shape"], values["volume_gamma_scale"], periods))
        taker = volume * (values["taker_mean"] + values["taker_sd"] * rng.standard_normal(periods)).clip(
            values["taker_min"], values["taker_max"])
        trades = values["trades_base"] + position % values["trades_period"] + rng.poisson(values["trades_poisson_mean"], periods)
        opens = pd.date_range(start, periods=periods, freq="min")
        frame = pd.DataFrame({
            "open_time": opens, "close_time": opens + pd.Timedelta(minutes=1) - pd.Timedelta(microseconds=1),
            "open": opening, "high": high, "low": low, "close": close,
            "volume": volume, "number_of_trades": trades,
            "taker_buy_base_asset_volume": taker, "quote_asset_volume": volume * close,
            "taker_buy_quote_asset_volume": taker * close,
        })
        features, labels = build_features(frame), build_labels(frame, horizon_minutes=15).frame
        eligible = features.metadata.valid_features & labels.valid_label
        fit_time = pd.Timestamp(values["fit_time"])
        masks = {"initial_train": eligible & labels.label_available_time.le(fit_time), "all_aligned": eligible}
        sanity = {}
        with threadpool_limits(limits=config["budget"]["numeric_threads"]):
            for name, mask in masks.items():
                X = features.frame.loc[mask].to_numpy()
                y = labels.loc[mask, "target_return"].to_numpy()
                if len(y) <= X.shape[1] + 1:
                    raise ValueError("Phase 2F fixture requires enough legal initial OLS observations")
                design = np.c_[X, np.ones(len(X))]
                coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
                r2 = float(1 - np.sum((y - design @ coefficients) ** 2) / np.sum((y - y.mean()) ** 2))
                assert r2 < values["r2_max"], f"noisy_minute_bars {name} R2={r2:.12g} exceeds frozen gate"
                sanity[name] = {"rows": len(y), "r2": r2}
        frame.attrs["phase2f_sanity"] = sanity
        return frame

    return make
