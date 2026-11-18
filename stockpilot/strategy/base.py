"""Strategy interface.

A strategy turns one symbol's OHLCV (plus the benchmark) into per-day columns:
  entry (bool)  - open a position at the NEXT session's open
  exit  (bool)  - close a held position at the NEXT session's open
  atr   (float) - volatility used for stop distance and sizing
  score (float) - ranks competing entry candidates (higher first)
All columns at row t may only use information available at the close of t.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .indicators import sma


@dataclass
class Strategy:
    name: str = "base"
    market_ma: int = 200

    def market_ok(self, df: pd.DataFrame, market: pd.DataFrame) -> pd.Series:
        """Benchmark close above its long moving average, aligned to df."""
        m = market["Close"]
        ok = (m > sma(m, self.market_ma)).reindex(df.index, method="ffill")
        return ok.fillna(False).astype(bool)

    def prepare(self, df: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError
