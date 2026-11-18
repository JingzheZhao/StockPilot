"""Synthetic OHLCV data for tests and offline demos."""

from __future__ import annotations

import numpy as np
import pandas as pd


def make_ohlcv(
    n_days: int = 1500,
    start: str = "2015-01-02",
    seed: int = 0,
    drift: float = 0.0004,
    vol: float = 0.018,
    start_price: float = 100.0,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n_days)
    rets = rng.normal(drift, vol, n_days)
    close = start_price * np.exp(np.cumsum(rets))
    prev = np.concatenate([[start_price], close[:-1]])
    open_ = prev * np.exp(rng.normal(0, vol / 3, n_days))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 2, n_days)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 2, n_days)))
    vol_ = rng.integers(1_000_000, 5_000_000, n_days)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol_},
        index=idx,
    )


def make_universe(symbols: list[str], n_days: int = 1500, seed: int = 0) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    return {
        s: make_ohlcv(
            n_days,
            seed=seed + i + 1,
            drift=float(rng.normal(0.0004, 0.0003)),
            vol=float(rng.uniform(0.012, 0.03)),
            start_price=float(rng.uniform(20, 400)),
        )
        for i, s in enumerate(symbols)
    }
