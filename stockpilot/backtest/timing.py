"""Single-asset trend timing: hold the asset while it closes above its moving
average, otherwise hold cash. Used as a benchmark (experiments/002).

Timing matches the main engine: the signal uses the close of day t, the trade
fills at the open of day t+1 with the same commission and slippage model.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..strategy.indicators import sma
from .engine import Costs


@dataclass
class TimingResult:
    equity: pd.Series
    in_market: pd.Series  # True on days the asset was held at the close
    switches: int
    fees: float


def ma_timing(
    df: pd.DataFrame,
    initial_cash: float,
    ma_days: int = 200,
    start=None,
    end=None,
    costs: Costs | None = None,
) -> TimingResult:
    costs = costs or Costs()
    signal = df["Close"] > sma(df["Close"], ma_days)  # known at the close of t
    days = df.index
    if start is not None:
        days = days[days >= pd.Timestamp(start)]
    if end is not None:
        days = days[days <= pd.Timestamp(end)]

    cash, shares, fees, switches = initial_cash, 0.0, 0.0, 0
    want_in = None  # decided at yesterday's close, acted on at today's open
    equity, held = {}, {}
    for day in days:
        bar = df.loc[day]
        if want_in is True and shares == 0:
            price = costs.buy_price(float(bar["Open"]))
            qty = max(cash - costs.minimum, 0) / price
            fee = costs.commission(qty, price)
            cash -= qty * price + fee
            shares, fees, switches = qty, fees + fee, switches + 1
        elif want_in is False and shares > 0:
            price = costs.sell_price(float(bar["Open"]))
            fee = costs.commission(shares, price)
            cash += shares * price - fee
            shares, fees, switches = 0.0, fees + fee, switches + 1
        equity[day] = cash + shares * float(bar["Close"])
        held[day] = shares > 0
        want_in = bool(signal.loc[day])
    return TimingResult(pd.Series(equity, name="equity"), pd.Series(held), switches, fees)
