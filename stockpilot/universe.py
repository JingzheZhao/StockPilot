"""Point-in-time universe selection.

For the "sp500" universe a stock may open a new position on day d only if, as
of the start of d's month, it was (1) an S&P 500 member and (2) among the top N
members by 60-day average dollar volume measured up to the previous trading
day. Both inputs are known before the month starts, so there is no look-ahead.
Positions already open are not forced out when a stock leaves the universe;
the strategy's normal exit rules still apply.
"""

from __future__ import annotations

import pandas as pd


def monthly_members(history: pd.DataFrame) -> dict[pd.Timestamp, set[str]]:
    """{month start: set of symbols} from data/universe/sp500_monthly.csv."""
    return {pd.Timestamp(d): set(g["symbol"]) for d, g in history.groupby("date")}


def top_liquid_eligibility(
    data: dict[str, pd.DataFrame],
    members: dict[pd.Timestamp, set[str]],
    calendar: pd.DatetimeIndex,
    top_n: int = 50,
    lookback: int = 60,
) -> pd.DataFrame:
    """Boolean frame (calendar x symbols): True where a stock may open a position."""
    close = pd.DataFrame({s: d["Close"] for s, d in data.items()}).reindex(calendar)
    volume = pd.DataFrame({s: d["Volume"] for s, d in data.items()}).reindex(calendar)
    dollar_vol = (close * volume).rolling(lookback, min_periods=lookback * 2 // 3).mean()

    eligible = pd.DataFrame(False, index=calendar, columns=close.columns)
    month_keys = sorted(members)
    month_of_day = calendar.to_period("M")
    for period in month_of_day.unique():
        days = calendar[month_of_day == period]
        first = days[0]
        prev_pos = calendar.get_loc(first) - 1
        if prev_pos < 0:
            continue
        # Latest membership snapshot taken on or before this month's start.
        snap = [m for m in month_keys if m <= first]
        if not snap:
            continue
        mem = members[snap[-1]] & set(close.columns)
        ranks = dollar_vol.iloc[prev_pos][sorted(mem)].dropna().sort_values(ascending=False)
        chosen = ranks.index[:top_n]
        eligible.loc[days, chosen] = True
    return eligible


def coverage(members: dict[pd.Timestamp, set[str]], data: dict[str, pd.DataFrame], start: str) -> float:
    """Share of member-months (from `start`) whose stock has at least one price
    inside that month. Having data for some other period does not count."""
    months_with_prices = {s: set(d.index.to_period("M")) for s, d in data.items()}
    total = hit = 0
    for m, syms in members.items():
        if m < pd.Timestamp(start):
            continue
        month = pd.Timestamp(m).to_period("M")
        total += len(syms)
        hit += sum(1 for s in syms if month in months_with_prices.get(s, ()))
    return hit / total if total else float("nan")
