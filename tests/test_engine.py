from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest

from stockpilot.backtest.engine import BacktestConfig, Costs, run_backtest
from stockpilot.data.synthetic import make_ohlcv, make_universe
from stockpilot.strategy import Breakout
from stockpilot.strategy.base import Strategy
from stockpilot.strategy.indicators import prior_high


@dataclass
class Scripted(Strategy):
    """Enter on given dates, exit on given dates. Fixed ATR."""

    name: str = "scripted"
    entries: tuple = ()
    exits: tuple = ()
    atr_value: float = 1.0

    def prepare(self, df, market):
        out = pd.DataFrame(index=df.index)
        out["entry"] = df.index.isin(pd.to_datetime(list(self.entries)))
        out["exit"] = df.index.isin(pd.to_datetime(list(self.exits)))
        out["atr"] = self.atr_value
        out["score"] = 1.0
        return out


def flat_bars(n=10, price=100.0, start="2024-01-01"):
    idx = pd.bdate_range(start, periods=n)
    return pd.DataFrame({"Open": price, "High": price + 1, "Low": price - 1,
                         "Close": price, "Volume": 1e6}, index=idx)


NO_SLIP = Costs(slippage_bps=0.0)


def test_entry_fills_at_next_open():
    df = flat_bars()
    df.loc[df.index[3], "Open"] = 105.0  # day after the signal
    strat = Scripted(entries=(df.index[2],), exits=(df.index[5],), atr_value=10.0)
    res = run_backtest({"X": df}, strat, df, BacktestConfig(costs=NO_SLIP))
    t = res.trades.iloc[0]
    assert t["entry_date"] == df.index[3]
    assert t["entry_price"] == pytest.approx(105.0)
    assert t["exit_date"] == df.index[6]  # exit signal day 5 -> fill day 6


def test_signal_on_last_day_never_fills():
    df = flat_bars()
    strat = Scripted(entries=(df.index[-1],))
    res = run_backtest({"X": df}, strat, df, BacktestConfig())
    assert res.trades.empty


def test_gap_below_stop_fills_at_open():
    df = flat_bars()
    strat = Scripted(entries=(df.index[1],), atr_value=2.0)  # stop = 100 - 4 = 96
    df.loc[df.index[4], ["Open", "High", "Low", "Close"]] = [90.0, 91.0, 89.0, 90.0]
    res = run_backtest({"X": df}, strat, df, BacktestConfig(costs=NO_SLIP))
    t = res.trades.iloc[0]
    assert t["exit_reason"] == "stop"
    assert t["exit_date"] == df.index[4]
    assert t["exit_price"] == pytest.approx(90.0)  # gap: open, not 96


def test_intraday_stop_fills_at_stop_price():
    df = flat_bars()
    strat = Scripted(entries=(df.index[1],), atr_value=2.0)  # stop 96
    df.loc[df.index[4], ["Open", "High", "Low", "Close"]] = [99.0, 99.5, 95.0, 97.0]
    res = run_backtest({"X": df}, strat, df, BacktestConfig(costs=NO_SLIP))
    assert res.trades.iloc[0]["exit_price"] == pytest.approx(96.0)


def test_position_sizing_respects_risk_and_cap():
    df = flat_bars()
    cfg = BacktestConfig(initial_cash=10_000, risk_per_trade=0.02,
                         max_position_pct=0.20, costs=NO_SLIP)
    # wide ATR -> risk-limited: 10000*0.02 / (2*10) = 10 shares
    res = run_backtest({"X": df}, Scripted(entries=(df.index[1],), atr_value=10.0), df, cfg)
    assert res.trades.iloc[0]["shares"] == pytest.approx(10.0)
    # tiny ATR -> capped at 20% of equity: 2000 / 100 = 20 shares
    res = run_backtest({"X": df}, Scripted(entries=(df.index[1],), atr_value=0.1), df, cfg)
    assert res.trades.iloc[0]["shares"] == pytest.approx(20.0)


def test_commission_min_and_cap():
    c = Costs()
    assert c.commission(10, 100) == pytest.approx(1.0)  # minimum
    assert c.commission(1000, 100) == pytest.approx(5.0)  # per share
    assert c.commission(0.5, 50) == pytest.approx(0.25)  # 1% cap on tiny order


def test_cash_never_negative_and_positions_capped():
    data = make_universe([f"S{i}" for i in range(15)], n_days=1200, seed=3)
    market = make_ohlcv(1200, seed=99, drift=0.0005)
    cfg = BacktestConfig(max_positions=4)
    res = run_backtest(data, Breakout(), market, cfg)
    assert res.positions_count.max() <= 4
    assert (res.equity > 0).all()


def test_no_lookahead_future_changes_do_not_affect_past():
    """Rewriting prices after day k must not change anything before day k."""
    syms = [f"S{i}" for i in range(8)]
    data = make_universe(syms, n_days=1000, seed=7)
    market = make_ohlcv(1000, seed=11, drift=0.0005)
    k = 700
    cut = market.index[k]

    base = run_backtest(data, Breakout(), market)

    rng = np.random.default_rng(1)
    mutated = {}
    for s, df in data.items():
        d = df.copy()
        shock = rng.uniform(0.3, 3.0)
        d.loc[d.index >= cut, ["Open", "High", "Low", "Close"]] *= shock
        mutated[s] = d
    m2 = market.copy()
    m2.loc[m2.index >= cut, ["Open", "High", "Low", "Close"]] *= 0.5
    alt = run_backtest(mutated, Breakout(), m2)

    before = base.equity.index < cut
    pd.testing.assert_series_equal(base.equity[before], alt.equity[before])
    closed = lambda t: t[t["exit_date"] < cut].reset_index(drop=True)
    pd.testing.assert_frame_equal(closed(base.trades), closed(alt.trades))


def test_keep_open_reports_positions_and_tomorrows_orders():
    """Paper trading mode: nothing is force-closed, queued orders are returned."""
    df = flat_bars()
    strat = Scripted(entries=(df.index[2], df.index[-1]), exits=(df.index[-1],), atr_value=2.0)
    res = run_backtest({"X": df, "Y": df.copy()}, strat, df,
                       BacktestConfig(costs=NO_SLIP), close_at_end=False)
    assert res.trades.empty  # no "end_of_test" exits
    assert {p.symbol for p in res.open_positions} == {"X", "Y"}
    assert set(res.pending_exits) == {"X", "Y"}  # exit signal on the last day
    assert res.pending_entries == []  # already held, so no new buy


def test_keep_open_matches_closed_run_until_the_end():
    data = make_universe([f"S{i}" for i in range(8)], n_days=900, seed=5)
    market = make_ohlcv(900, seed=6, drift=0.0005)
    a = run_backtest(data, Breakout(), market)
    b = run_backtest(data, Breakout(), market, close_at_end=False)
    pd.testing.assert_series_equal(a.equity, b.equity)
    assert len(a.trades) == len(b.trades) + len(b.open_positions)


def test_trailing_stop_follows_price_up_and_never_down():
    df = flat_bars(n=14)
    # Entry on day 2 at 100. Price climbs to 120 by day 6, then slides.
    closes = [100, 100, 100, 105, 110, 115, 120, 118, 116, 114, 112, 110, 108, 106]
    for i, c in enumerate(closes):
        df.loc[df.index[i], ["Open", "High", "Low", "Close"]] = [c, c + 0.5, c - 0.5, c]
    strat = Scripted(entries=(df.index[1],), atr_value=2.0)  # initial stop 96
    cfg = BacktestConfig(costs=NO_SLIP, trail_atr_mult=3.0)
    res = run_backtest({"X": df}, strat, df, cfg, close_at_end=False)
    # Peak close 120 (day 6) -> trailing stop 120 - 3*2 = 114. Day 9 opens at
    # 114 with a low of 113.5, so the stop fills there at 114. Without the
    # trailing stop the position would still be open (initial stop 96).
    t = res.trades.iloc[0]
    assert t["exit_reason"] == "stop"
    assert t["exit_date"] == df.index[9]
    assert t["exit_price"] == pytest.approx(114.0)


def test_trailing_stop_off_keeps_initial_stop():
    df = flat_bars(n=14)
    closes = [100, 100, 100, 105, 110, 115, 120, 118, 116, 114, 112, 110, 108, 106]
    for i, c in enumerate(closes):
        df.loc[df.index[i], ["Open", "High", "Low", "Close"]] = [c, c + 0.5, c - 0.5, c]
    strat = Scripted(entries=(df.index[1],), atr_value=2.0)
    res = run_backtest({"X": df}, strat, df, BacktestConfig(costs=NO_SLIP), close_at_end=False)
    assert res.trades.empty and res.open_positions[0].stop == pytest.approx(96.0)


def test_no_lookahead_with_trailing_stop():
    syms = [f"S{i}" for i in range(8)]
    data = make_universe(syms, n_days=1000, seed=7)
    market = make_ohlcv(1000, seed=11, drift=0.0005)
    cut = market.index[700]
    cfg = BacktestConfig(trail_atr_mult=3.0)
    strat = Breakout(ma_exit=False)
    base = run_backtest(data, strat, market, cfg)
    mutated = {s: d.copy() for s, d in data.items()}
    for d in mutated.values():
        d.loc[d.index >= cut, ["Open", "High", "Low", "Close"]] *= 0.6
    alt = run_backtest(mutated, strat, market, cfg)
    before = base.equity.index < cut
    pd.testing.assert_series_equal(base.equity[before], alt.equity[before])


def test_prior_high_excludes_today():
    s = pd.Series([1, 2, 3, 10, 4], dtype=float)
    ph = prior_high(s, 3)
    assert ph.iloc[3] == 3  # 10 is today, not included
    assert ph.iloc[4] == 10
