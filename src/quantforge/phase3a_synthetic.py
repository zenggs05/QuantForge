"""Frozen, sparse AR(1)/GARCH-t5 OHLCV episodes; no data loader or network."""

from __future__ import annotations

import math
import numpy as np
import pandas as pd


def synthetic_history(config: dict) -> pd.DataFrame:
    """Generate 68 independent monthly episodes using the frozen RNG identities."""
    settings = config["synthetic"]
    months = config["months"]["warm_up"] + config["months"]["evaluate"]
    n, burn = settings["minutes_per_month"], settings["burnin"]
    phi, omega = settings["ar_phi"], settings["garch_omega"]
    alpha, beta, df = settings["garch_alpha"], settings["garch_beta"], settings["student_df"]
    if not (abs(phi) < 1 and alpha >= 0 and beta >= 0 and alpha + beta < 1 and df > 2):
        raise ValueError("Invalid frozen AR/GARCH parameters")
    frames = []
    for ordinal, month in enumerate(months):
        rng = [np.random.default_rng(np.random.SeedSequence(
            [config["experiment"]["seed"], ordinal, stream])) for stream in settings["stream_ids"]]
        innovations = rng[0].standard_t(df, n + burn) * math.sqrt((df - 2) / df)
        returns = np.empty(n + burn, dtype=np.float64)
        variance, previous_return, previous_error = omega / (1 - alpha - beta), 0.0, 0.0
        for t, innovation in enumerate(innovations):
            variance = omega + alpha * previous_error**2 + beta * variance
            error = math.sqrt(variance) * innovation
            previous_return = phi * previous_return + error
            returns[t], previous_error = previous_return, error
        close = settings["initial_price"] * np.exp(np.cumsum(returns[burn:]))
        previous_close = np.r_[settings["initial_price"], close[:-1]]
        opening = previous_close * np.exp(rng[1].normal(0, settings["price_gap_sd"], n))
        width = settings["wick_min"] + settings["wick_scale"] * np.abs(rng[2].normal(size=n))
        volume = rng[3].lognormal(settings["volume_log_mean"], settings["volume_log_sd"], n)
        trades = rng[4].integers(settings["trades_min"], settings["trades_max_exclusive"], n)
        fraction = rng[5].uniform(settings["taker_ratio_min"], settings["taker_ratio_max"], n)
        times = pd.date_range(month + "-01", periods=n, freq="min", tz="UTC")
        frames.append(pd.DataFrame({
            "open_time": times,
            "close_time": times + pd.Timedelta(minutes=1) - pd.Timedelta(microseconds=1),
            "open": opening, "high": np.maximum(opening, close) * np.exp(width),
            "low": np.minimum(opening, close) * np.exp(-width), "close": close,
            "volume": volume, "number_of_trades": trades,
            "taker_buy_base_asset_volume": volume * fraction,
            "quote_asset_volume": volume * close,
            "taker_buy_quote_asset_volume": volume * close * fraction,
        }))
    result = pd.concat(frames, ignore_index=True)
    if len(result) != len(months) * n:
        raise AssertionError("Synthetic month grid is incomplete")
    return result
