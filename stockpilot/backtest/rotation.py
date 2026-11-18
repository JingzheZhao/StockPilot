"""Monthly cross-sectional momentum rotation (experiments/003).

On the first trading day of each month, at the open:
  * rank point-in-time index members by 12-1 momentum, using closes up to the
    previous trading day only. The target list is fixed from that data alone;
    whether a stock trades today is not known in advance and is not used;
  * sell holdings that left the top N, buy new entrants at equity / N each.
Holdings that stay are not traded. Costs follow the main engine.

Fills when a price is missing (after external review):
  * a buy with no open price on the rebalance day is unfilled; its slot stays
    in cash until the next rebalance, and no lower-ranked stock takes its place;
  * a sell with no open price stays pending and fills at the next available
    open;
  * a holding whose data ends for good (delisted, acquired) can never be sold
    in the simulation; it is reported in `stuck` and valued at its last close,
    which is optimistic.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..strategy.indicators import sma
from .engine import Costs


@dataclass
class RotationResult:
    equity: pd.Series
    trades: int
    fees: float
    months_in_cash: int
    unfilled_buys: int = 0  # target stocks with no open price on the rebalance day
    delayed_sells: int = 0  # sells that waited for a later open
    stuck: list = None  # holdings whose data ended before the window did


def momentum_rotation(
    data: dict[str, pd.DataFrame],
    members: dict[pd.Timestamp, set[str]],
    market: pd.DataFrame,
    top_n: int = 10,
    market_filter: bool = False,
    initial_cash: float = 3000.0,
    start=None,
    end=None,
    lookback: int = 252,
    skip: int = 21,
    costs: Costs | None = None,
) -> RotationResult:
    costs = costs or Costs()
    cal = market.index
    opens = pd.DataFrame({s: d["Open"] for s, d in data.items()}).reindex(cal)
    closes_raw = pd.DataFrame({s: d["Close"] for s, d in data.items()}).reindex(cal)
    closes = closes_raw.ffill()  # value holdings through gaps; last price if delisted
    mom = closes_raw.shift(skip) / closes_raw.shift(lookback) - 1  # row t: known at close of t
    spy_ok = market["Close"] > sma(market["Close"], 200)
    month_keys = sorted(members)

    days = cal
    if start is not None:
        days = days[days >= pd.Timestamp(start)]
    if end is not None:
        days = days[days <= pd.Timestamp(end)]

    cash, fees, trades, cash_months = initial_cash, 0.0, 0, 0
    unfilled_buys = delayed_sells = 0
    holdings: dict[str, float] = {}
    pending_sells: set[str] = set()
    equity: dict[pd.Timestamp, float] = {}
    prev_month = None

    def try_sell(sym: str, day) -> bool:
        nonlocal cash, fees, trades
        o = opens.at[day, sym]
        if o != o:  # no open price today
            return False
        price = costs.sell_price(float(o))
        fee = costs.commission(holdings[sym], price)
        cash += holdings[sym] * price - fee
        fees, trades = fees + fee, trades + 1
        del holdings[sym]
        return True

    for day in days:
        pos = cal.get_loc(day)
        month = day.to_period("M")
        rebalance = month != prev_month and pos > 0
        target: list[str] = []
        if rebalance:  # first trading day of a month
            prev_month = month
            prev = cal[pos - 1]
            snap = [m for m in month_keys if m <= day]
            if snap and (not market_filter or bool(spy_ok.iloc[pos - 1])):
                mem = members[snap[-1]] & set(mom.columns)
                ranks = mom.loc[prev, sorted(mem)].dropna()
                target = list(ranks.sort_values(ascending=False).index[:top_n])
            if not target:
                cash_months += 1
            pending_sells |= {s for s in holdings if s not in target}
            pending_sells -= set(target)  # back in the top N: keep it

        for sym in sorted(pending_sells):
            if try_sell(sym, day):
                pending_sells.discard(sym)
            elif rebalance:
                delayed_sells += 1

        if rebalance and target:
            value = cash + sum(q * float(closes.at[prev, s]) for s, q in holdings.items())
            slot = value / top_n
            for sym in [s for s in target if s not in holdings]:
                o = opens.at[day, sym]
                if o != o:  # no quote at the open: unfilled, slot stays in cash
                    unfilled_buys += 1
                    continue
                price = costs.buy_price(float(o))
                spend = min(slot, cash - costs.minimum)
                if spend < 50:  # too little cash left for a sensible order
                    continue
                qty = spend / price
                fee = costs.commission(qty, price)
                cash -= qty * price + fee
                holdings[sym] = qty
                fees, trades = fees + fee, trades + 1

        equity[day] = cash + sum(q * float(closes.at[day, s]) for s, q in holdings.items())

    last_day = days[-1]
    stuck = [s for s in holdings if closes_raw[s].last_valid_index() < last_day]
    return RotationResult(pd.Series(equity, name="equity"), trades, fees, cash_months,
                          unfilled_buys, delayed_sells, stuck)
