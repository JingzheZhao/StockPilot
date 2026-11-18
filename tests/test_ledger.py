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



def test_resume_after_first_daily_record_without_updating_legacy_checkpoint(tmp_path, world):
    _, market, frames = world
    cfg = BacktestConfig()
    initial = new_state(market.index[399], cfg.initial_cash)
    save_state(tmp_path, initial)
    days = list(market.index[400:402])
    expected, records, _ = advance(initial, frames, cfg, days, no_members)
    save_day(tmp_path, records[0])
    recovered = load_state(tmp_path)
    assert recovered["asof"] == str(days[0].date())
    final, remaining, problems = advance(recovered, frames, cfg, days[1:], no_members)
    assert not problems
    assert final == expected
    save_day(tmp_path, remaining[0])
    assert load_state(tmp_path) == expected


def test_missing_eligible_quote_blocks_session_even_with_good_index_coverage(world):
    _, market, frames = world
    day = market.index[400]
    initial = new_state(market.index[399], 3000.0)
    state, records, issues = advance(initial, frames, BacktestConfig(), [day], no_members, lambda _: {"MISSING"})
    assert not records
    assert state == initial
    assert "missing candidate quotes: MISSING" in issues[0]



def test_paper_run_replaces_stale_snapshot_when_spy_is_missing(tmp_path, monkeypatch):
    import sys
    from scripts import paper

    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    monkeypatch.setattr(paper, "PAPER", paper_dir)
    monkeypatch.setattr(paper, "LEDGER", paper_dir / "ledger")
    monkeypatch.setattr(paper, "ROOT", tmp_path)
    monkeypatch.setattr(paper, "last_completed_session", lambda: pd.Timestamp("2026-11-16"))
    monkeypatch.setattr(paper, "load_market", lambda *_: ({}, None))
    monkeypatch.setattr(sys, "argv", ["paper.py"])
    snapshot = tmp_path / "muse" / "data" / "latest.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text('{"status":"ORDERS QUEUED","next_open_orders":[{"symbol":"KO"}]}', encoding="utf-8")

    assert paper.main() == "DATA ERROR"  # the script then exits non-zero so the job fails visibly

    updated = json.loads(snapshot.read_text(encoding="utf-8"))
    assert updated["status"] == "DATA ERROR"
    assert updated["next_open_orders"] == []
    assert "SPY has no usable bars" in updated["report_markdown"]
    assert (paper_dir / "reports" / "2026-11-16.md").exists()
    assert load_state(paper_dir / "ledger") is None
