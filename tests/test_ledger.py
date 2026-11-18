import json

import pandas as pd
import pytest

from stockpilot.backtest.engine import BacktestConfig, prepare_frames, run_backtest
from stockpilot.data.synthetic import make_ohlcv, make_universe
from stockpilot.ledger import (
    advance, apply_corporate_actions, check_session, load_days, new_state, save_day, save_state,
    load_state,
)
from stockpilot.strategy import Breakout


@pytest.fixture()
def world():
    data = make_universe([f"S{i}" for i in range(8)], n_days=900, seed=5)
    market = make_ohlcv(900, seed=6, drift=0.0005)
    frames = prepare_frames(data, Breakout(), market)
    return data, market, frames


def no_members(day):
    return None


def roundtrip(state):
    return json.loads(json.dumps(state))


def test_ledger_in_chunks_matches_one_continuous_backtest(world):
    data, market, frames = world
    cfg = BacktestConfig()
    days = list(market.index[400:])
    ref = run_backtest(data, Breakout(), market, cfg, start=days[0], close_at_end=False)

    state = new_state(market.index[399], cfg.initial_cash)
    equity = {}
    for chunk in (days[:120], days[120:121], days[121:300], days[300:]):
        state, records, problems = advance(roundtrip(state), frames, cfg, chunk, no_members)
        assert problems == []
        equity.update({pd.Timestamp(r["date"]): r["equity"] for r in records})
        state = roundtrip(state)  # as if saved to disk and reloaded
    got = pd.Series(equity)
    pd.testing.assert_series_equal(got, ref.equity, check_names=False, rtol=1e-9)
    assert state["cash"] == pytest.approx(ref.equity.iloc[-1] - sum(
        p.shares * ref.last_close[p.symbol] for p in ref.open_positions))


def test_rerun_with_no_new_session_changes_nothing(world):
    _, market, frames = world
    cfg = BacktestConfig()
    state, _, _ = advance(new_state(market.index[399], 3000.0), frames, cfg, list(market.index[400:500]), no_members)
    again, records, problems = advance(roundtrip(state), frames, cfg, [], no_members)
    assert records == [] and problems == [] and again == state


def test_missing_price_for_held_stock_stops_the_ledger(world):
    _, market, frames = world
    cfg = BacktestConfig()
    days = list(market.index[400:700])
    state, _, _ = advance(new_state(market.index[399], 3000.0), frames, cfg, days, no_members)
    held = [p["symbol"] for p in state["positions"]] + [o["symbol"] for o in state["pending_entries"]]
    assert held, "fixture should leave something held or ordered"
    nxt = market.index[700]
    broken = dict(frames)
    broken[held[0]] = frames[held[0]].drop(index=nxt)
    after, records, problems = advance(roundtrip(state), broken, cfg, [nxt, market.index[701]], no_members)
    assert records == [] and after == state
    assert held[0] in problems[0] and str(nxt.date()) in problems[0]


def test_low_index_coverage_is_a_data_error(world):
    _, market, frames = world
    day = market.index[500]
    members = set(frames) | {f"X{i}" for i in range(10)}  # 10 members with no data at all
    problems = check_session(day, frames, set(), members)
    assert problems and "index members have a price" in problems[0]


def test_split_rescales_position(world):
    state = {"asof": "2020-01-02", "cash": 0.0, "pending_entries": [], "pending_exits": {},
             "positions": [{"symbol": "S0", "shares": 10.0, "entry_date": "2019-12-01", "entry_price": 100.0,
                            "entry_fee": 1.0, "stop": 90.0, "bars_held": 5, "peak_close": 110.0}],
             "last_close": {"S0": 110.0}}
    idx = pd.DatetimeIndex(["2020-01-02"])
    frames = {"S0": pd.DataFrame({"Open": 55.0, "High": 55.0, "Low": 55.0, "Close": 55.0}, index=idx)}
    events = apply_corporate_actions(state, frames)
    p = state["positions"][0]
    assert events and p["shares"] == pytest.approx(20.0) and p["stop"] == pytest.approx(45.0)
    assert p["entry_price"] == pytest.approx(50.0)


def test_recorded_session_cannot_be_overwritten(tmp_path):
    save_day(tmp_path, {"date": "2026-11-17", "equity": 3000.0})
    with pytest.raises(FileExistsError):
        save_day(tmp_path, {"date": "2026-11-17", "equity": 9999.0})
    assert load_days(tmp_path)[0]["equity"] == 3000.0


def test_state_roundtrip_on_disk(tmp_path, world):
    _, market, frames = world
    state, _, _ = advance(new_state(market.index[399], 3000.0), frames, BacktestConfig(),
                          list(market.index[400:600]), no_members)
    save_state(tmp_path, state)
    assert load_state(tmp_path) == roundtrip(state)
