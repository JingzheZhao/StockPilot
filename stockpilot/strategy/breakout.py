"""Trend breakout: buy new 20-day closing highs in an uptrend."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .base import Strategy
from .indicators import atr, momentum, prior_high, sma


@dataclass
class Breakout(Strategy):
    name: str = "breakout_v1"
    breakout_days: int = 20
    trend_ma: int = 50
    atr_days: int = 14

    def prepare(self, df: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
        c = df["Close"]
        ma = sma(c, self.trend_ma)
        hh = prior_high(c, self.breakout_days)
        out = pd.DataFrame(index=df.index)
        out["entry"] = self.market_ok(df, market) & (c > ma) & (c > hh)
        out["exit"] = c < ma
        out["atr"] = atr(df, self.atr_days)
        out["score"] = momentum(c, 60)
        out["entry"] &= out["atr"].notna() & out["score"].notna()
        if "Eligible" in df.columns:  # point-in-time universe filter (stockpilot/universe.py)
            out["entry"] &= df["Eligible"].fillna(False).astype(bool)
        return out
