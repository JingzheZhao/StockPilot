import numpy as np
import pandas as pd

from stockpilot.backtest.engine import Costs
from stockpilot.backtest.rotation import momentum_rotation
from stockpilot.data.synthetic import make_ohlcv, make_universe

NO_COST = Costs(slippage_bps=0.0, minimum=0.0, per_share=0.0)


def trending(n=400):
    """Three stocks with clearly different trends, plus a rising market."""
    data = {
        "UP": make_ohlcv(n, seed=1, drift=0.004, vol=0.002),
        "FLAT": make_ohlcv(n, seed=2, drift=0.0, vol=0.002),
        "DOWN": make_ohlcv(n, seed=3, drift=-0.004, vol=0.002),
    }
    market = make_ohlcv(n, seed=4, drift=0.001, vol=0.002)
    members = {pd.Timestamp("2014-01-01"): set(data)}
    return data, members, market


def test_holds_the_strongest_stock():
    data, members, market = trending()
    r = momentum_rotation(data, members, market, top_n=1, lookback=60, skip=5,
                          start="2015-06-01", costs=NO_COST)
    up = data["UP"]["Close"]
    # Holding only UP from the first June rebalance: equity tracks UP's price.
    first = r.equity.index[0]
    ratio = r.equity / up.reindex(r.equity.index)
    assert ratio.loc["2015-07-01":].std() / ratio.loc["2015-07-01":].mean() < 1e-9
    assert r.equity.iloc[-1] > r.equity.loc[first] * 1.5


def test_market_filter_holds_cash_in_a_falling_market():
    data, members, _ = trending()
    falling = make_ohlcv(400, seed=4, drift=-0.003, vol=0.002)
    r = momentum_rotation(data, members, falling, top_n=1, lookback=60, skip=5,
                          market_filter=True, start="2015-11-01", costs=NO_COST)
    assert r.trades == 0 and (r.equity == 3000.0).all()


def test_no_lookahead():
    syms = [f"S{i}" for i in range(12)]
    data = make_universe(syms, n_days=900, seed=9)
    market = make_ohlcv(900, seed=10, drift=0.0005)
    members = {pd.Timestamp("2014-01-01"): set(syms)}
    cut = market.index[700]
    base = momentum_rotation(data, members, market, top_n=3)
    rng = np.random.default_rng(0)
    mutated = {}
    for s, d in data.items():
        d = d.copy()
        d.loc[d.index >= cut, ["Open", "High", "Low", "Close"]] *= rng.uniform(0.3, 3.0)
        mutated[s] = d
    alt = momentum_rotation(mutated, members, market, top_n=3)
    before = base.equity.index < cut
    pd.testing.assert_series_equal(base.equity[before], alt.equity[before])


def first_trading_day(idx, month):
    return idx[idx.to_period("M") == pd.Period(month)][0]


def test_missing_open_leaves_buy_unfilled_without_substitute():
    data, members, market = trending()
    day = first_trading_day(market.index, "2015-07")
    data["UP"] = data["UP"].copy()
    data["UP"].loc[day, "Open"] = float("nan")  # no quote at the July rebalance
    members = {pd.Timestamp("2014-01-01"): {"UP", "FLAT"}}
    r = momentum_rotation(data, members, market, top_n=1, lookback=60, skip=5,
                          start="2015-07-01", end="2015-07-31", costs=NO_COST)
    assert r.unfilled_buys == 1
    assert (r.equity == 3000.0).all()  # stayed in cash; FLAT did not take UP's place


def test_sell_without_open_waits_for_next_open():
    data, members, market = trending()
    # Hold UP in June; in July only FLAT is a member, so UP must be sold.
    members = {pd.Timestamp("2014-01-01"): {"UP"}, pd.Timestamp("2015-07-01"): {"FLAT"}}
    jul = first_trading_day(market.index, "2015-07")
    data["UP"] = data["UP"].copy()
    data["UP"].loc[jul, "Open"] = float("nan")
    r = momentum_rotation(data, members, market, top_n=1, lookback=60, skip=5,
                          start="2015-06-01", end="2015-07-31", costs=NO_COST)
    assert r.delayed_sells == 1
    assert r.trades == 2  # bought UP in June, sold it the day after the July rebalance
    assert r.stuck == []


def test_only_members_are_bought():
    data, _, market = trending()
    members = {pd.Timestamp("2014-01-01"): {"FLAT", "DOWN"}}  # UP is not in the index
    r_with = momentum_rotation(data, members, market, top_n=1, lookback=60, skip=5,
                               start="2015-06-01", costs=NO_COST)
    flat = data["FLAT"]["Close"].reindex(r_with.equity.index)
    ratio = (r_with.equity / flat).loc["2015-07-01":]
    assert ratio.std() / ratio.mean() < 1e-9  # held FLAT, the best member
