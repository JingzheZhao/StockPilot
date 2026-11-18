"""Indicators. Every value at row t uses only data up to and including t."""

from __future__ import annotations

import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def prior_high(s: pd.Series, n: int) -> pd.Series:
    """Highest value over the n bars BEFORE t (excludes t itself)."""
    return s.shift(1).rolling(n, min_periods=n).max()


def momentum(s: pd.Series, n: int) -> pd.Series:
    return s / s.shift(n) - 1
