import pandas as pd
import pytest

from stockpilot.backtest.engine import BacktestConfig, prepare_frames
from stockpilot.data.synthetic import make_ohlcv
from stockpilot.startbook import initial_state, to_usd_frame
from stockpilot.strategy import Breakout


def test_cad_bars_converted_and_tsx_holiday_carries_last_price():
    cal = pd.DatetimeIndex(["2026-12-23", "2026-12-24", "2026-12-28", "2026-12-29"])  # NYSE days
    cad = pd.DataFrame({"Open": [100.0, 101, 103], "High": [102.0, 102, 104], "Low": [99.0, 100, 102],
                        "Close": [101.0, 101.5, 103.5], "Volume": [10.0, 10, 10]},
                       index=pd.DatetimeIndex(["2026-12-23", "2026-12-24", "2026-12-29"]))  # TSX shut 12-28
    fx = pd.Series([1.40, 1.40, 1.38], index=pd.DatetimeIndex(["2026-12-23", "2026-12-26", "2026-12-29"]))
    usd = to_usd_frame(cad, fx, cal)
    assert list(usd.index) == list(cal)
    assert usd.loc["2026-12-23", "Close"] == pytest.approx(101.0 / 1.40)
    # 12-28: TSX closed -> last CAD close, flat bar, no volume; FX carried from 12-26
    gap = usd.loc["2026-12-28"]
    assert gap["Open"] == gap["High"] == gap["Low"] == gap["Close"] == pytest.approx(101.5 / 1.40)
    assert gap["Volume"] == 0
    assert usd.loc["2026-12-29", "Close"] == pytest.approx(103.5 / 1.38)


def falling_and_rising():
    n = 300
    down = make_ohlcv(n, seed=1, drift=-0.004, vol=0.01, start_price=200)  # below its 50-day MA
    up = make_ohlcv(n, seed=2, drift=0.003, vol=0.01, start_price=100)  # above its 50-day MA
    market = make_ohlcv(n, seed=3, drift=0.001, vol=0.005)
    frames = prepare_frames({"DOWN": down, "UP": up}, Breakout(), market)
    return frames, market.index[-1]


def test_initial_state_from_real_holdings():
    frames, start = falling_and_rising()
    cfg = {"start": str(start.date()), "capital_cad": 2000.0, "initial_positions": [
        {"symbol": "DOWN", "shares": 1, "avg_price": 150.0, "currency": "USD"},
        {"symbol": "UP", "shares": 4, "avg_price": 180.0, "currency": "CAD"}]}
    state, notes = initial_state(cfg, frames, fx_on_start=1.40, bt=BacktestConfig())
    # cash: 2000 CAD - 150 USD * 1.40 - 4 * 180 CAD = 1070 CAD -> USD
    assert state["cash"] == pytest.approx((2000 - 210 - 720) / 1.40)
    pos = {p["symbol"]: p for p in state["positions"]}
    assert pos["UP"]["entry_price"] == pytest.approx(180 / 1.40)
    assert pos["DOWN"]["stop"] < frames["DOWN"].loc[start, "Close"]
    # The falling stock is already below its 50-day MA: queued for sale, like any position.
    assert state["pending_exits"] == {"DOWN": "signal"} and "DOWN" in notes[0]


def test_initial_state_rejects_holdings_above_capital():
    frames, start = falling_and_rising()
    cfg = {"start": str(start.date()), "capital_cad": 100.0,
           "initial_positions": [{"symbol": "UP", "shares": 4, "avg_price": 180.0, "currency": "CAD"}]}
    with pytest.raises(ValueError):
        initial_state(cfg, frames, 1.40, BacktestConfig())
