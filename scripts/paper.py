"""Daily paper trading (simulated money, no broker).

    python scripts/paper.py

Each run:
  1. Finds the latest completed NYSE session (holidays and early closes
     included) and downloads prices adjusted for splits only.
  2. Checks the data: SPY must reach that session, every held, ordered or eligible stock must have a price. Index coverage must also reach 95%.
  3. Restores the paper ledger (stockpilot/ledger.py) and steps the shared
     backtest Simulator through the sessions that are new since the last run.
     Recorded sessions are never recomputed; a second run on the same day
     changes nothing.
  4. Writes a status report, an order log and the snapshot the Muse connector
     serves. The status is one of:
       DATA ERROR    data failed a check; no new orders are published
       ORDERS QUEUED simulated orders for the next session's open
       ORDERS EXPIRED the scheduled opening time has passed
       NO SIGNAL     data complete, no rule fired; the report says why

Universe: each month's 50 most traded S&P 500 members (point-in-time), the
same rule as `scripts/backtest.py --universe sp500`.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stockpilot import ledger  # noqa: E402
from stockpilot.backtest.engine import BacktestConfig, buy_and_hold, prepare_frames  # noqa: E402
from stockpilot.config import BENCHMARK, CURATED, SP500_HISTORY, SP500_TOP_N  # noqa: E402
from stockpilot.data.market import download_many  # noqa: E402
from stockpilot.marketcal import last_completed_session, next_session, session_open, sessions_between  # noqa: E402
from stockpilot.strategy import STRATEGIES  # noqa: E402
from stockpilot.strategy.indicators import sma  # noqa: E402
from stockpilot.universe import monthly_members, top_liquid_eligibility  # noqa: E402

PAPER = ROOT / "paper"
LEDGER = PAPER / "ledger"
HISTORY_DAYS = 600  # enough for the 200-day market filter and 60-day momentum
REASON = {"signal": "closed below 50-day MA", "stop": "stop-loss hit", "end_of_test": "end of test"}
UNIVERSE_LABEL = {
    "sp500": f"S&P 500 members, top {SP500_TOP_N} by 60-day dollar volume, refreshed monthly",
    "curated": "fixed list of 37 large caps and ETFs",
}


# ---------- inputs ----------

def load_config(cash: float, strategy: str, universe: str) -> dict:
    path = PAPER / "config.json"
    if path.exists():
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg.setdefault("universe", "curated")
        return cfg
    return {"start": None, "cash": cash, "strategy": strategy, "universe": universe}


def update_membership() -> pd.DataFrame:
    """Add any missing monthly S&P 500 snapshots, then load the full history."""
    from stockpilot.data.sp500 import build_history, load_history

    path = ROOT / SP500_HISTORY
    try:
        build_history("2014-01-01", None, path)
    except Exception as exc:  # Wikipedia down or rate-limited: keep the saved snapshots
        logging.warning("Could not update S&P 500 membership (%s); using saved snapshots", exc)
    return load_history(path)


def load_market(cfg: dict, since: str):
    if cfg["universe"] == "curated":
        return download_many([BENCHMARK, *CURATED], since, auto_adjust=False), None
    members = monthly_members(update_membership())
    recent = {s for m, syms in members.items() if m >= pd.Timestamp(since) for s in syms}
    return download_many([BENCHMARK, *sorted(recent)], since, auto_adjust=False), members


def members_on(members: dict | None, day: pd.Timestamp) -> set[str] | None:
    if not members:
        return None
    keys = [m for m in members if m <= day]
    return members[max(keys)] if keys else None


# ---------- report ----------

def diagnostics(frames: dict, data: dict, asof: pd.Timestamp, held: set[str]) -> dict:
    """Why there is (or is not) a buy signal on `asof`."""
    elig = [s for s, d in data.items() if "Eligible" in d and asof in d.index and bool(d.loc[asof, "Eligible"])]
    if not elig:  # curated universe: everything is buyable
        elig = [s for s in frames if asof in frames[s].index]
    rows = [(s, frames[s].loc[asof]) for s in elig if s in frames and asof in frames[s].index and s not in held]
    trend = [s for s, b in rows if bool(b["c_trend"])]
    high = [s for s, b in rows if bool(b["c_high"])]
    both = [s for s, b in rows if bool(b["c_trend"]) and bool(b["c_high"])]
    near = sorted(((s, float(b["prior_high"]) / float(b["Close"]) - 1) for s, b in rows
                   if bool(b["c_trend"]) and not bool(b["c_high"]) and b["prior_high"] == b["prior_high"]),
                  key=lambda x: x[1])[:3]
    return {"buyable": len(elig), "evaluated": len(rows), "above_50d_ma": len(trend),
            "new_20d_high": len(high), "both": both, "closest": near}


def risk_summary(state: dict, equity: float) -> dict:
    pos = state["positions"]
    values = {p["symbol"]: p["shares"] * state["last_close"][p["symbol"]] for p in pos}
    to_stops = sum(max(state["last_close"][p["symbol"]] - p["stop"], 0) * p["shares"] for p in pos)
    return {
        "cash_pct": state["cash"] / equity if equity else 1.0,
        "invested_pct": sum(values.values()) / equity if equity else 0.0,
        "largest": max(values.items(), key=lambda kv: kv[1]) if values else None,
        "loss_if_all_stops_hit_pct": to_stops / equity if equity else 0.0,
    }


def build_report(status: str, why: str, state: dict, days: list[dict], cfg: dict, expected: pd.Timestamp,
                 market: pd.DataFrame, diag: dict, problems: list[str], universe_now: list[str]) -> tuple[str, dict]:
    asof = pd.Timestamp(state["asof"])
    equity = days[-1]["equity"] if days else cfg["cash"]
    start = pd.Timestamp(cfg["start"])
    bench = buy_and_hold(market, cfg["cash"], start, asof) if not market.loc[start:asof].empty else None
    bret = float(bench.iloc[-1] / bench.iloc[0] - 1) if bench is not None and len(bench) else 0.0
    m = market["Close"].loc[:asof]
    filt = bool(m.iloc[-1] > sma(m, 200).iloc[-1]) if not m.empty else None
    nxt = next_session(asof)
    last = days[-1] if days else None
    risk = risk_summary(state, equity)
    trades = [t for d in days for t in d["sells"]]

    L = [f"# Paper trading report {asof.date()}", ""]
    L.append(f"**Status: {status}**. {why}")
    L.append("")
    L.append(f"- Data: recorded through {asof.date()}; latest completed NYSE session {expected.date()}")
    L.append(f"- Account (simulated money): **${equity:,.2f}** ({equity / cfg['cash'] - 1:+.2%} since "
             f"{cfg['start']}); SPY buy-and-hold over the same period {bret:+.2%}")
    L.append("- Market filter: unavailable" if filt is None else "- Market filter: " + ("ON, SPY above its 200-day MA, new buys allowed" if filt
                                   else "OFF, SPY below its 200-day MA, no new buys"))
    L.append(f"- Universe: {UNIVERSE_LABEL[cfg['universe']]}")
    if universe_now:
        L.append(f"- Buyable this month ({len(universe_now)}): {' '.join(universe_now)}")
    L.append("")

    if problems:
        L.append("## Data problems (no new orders published)")
        L += [f"- {p}" for p in problems]
        L.append("")

    L.append(f"## Orders for the open of {nxt.date()} (valid for that open only)")
    orders = last["new_orders"] if last and status == "ORDERS QUEUED" else []
    exits = state["pending_exits"] if status == "ORDERS QUEUED" else {}
    if status in {"DATA ERROR", "ORDERS EXPIRED"}:
        L.append("No active orders published.")
    elif not orders and not exits:
        L.append("None.")
    for sym, reason in exits.items():
        p = next(p for p in state["positions"] if p["symbol"] == sym)
        L.append(f"- **SELL {sym}** {p['shares']:.4f} shares: {REASON.get(reason, reason)}")
    for o in orders:
        L.append(f"- **BUY {o['symbol']}** {o['shares']:.4f} shares, about ${o['shares'] * o['signal_close']:,.0f}. "
                 f"Why: new 20-day closing high, above its 50-day MA, market filter on; ranked {o['rank']} of "
                 f"{o['candidates']} candidates by 60-day momentum. Size set by the {o['sized_by']}. "
                 f"Planned stop about ${o['planned_stop']:.2f} (reset from the actual fill price).")
    L.append("")

    if status == "NO SIGNAL":
        L.append("## Why no orders")
        if not filt:
            L.append("- The market filter is off, so no new positions are opened.")
        L.append(f"- Of {diag['evaluated']} buyable stocks with a price (not already held), "
                 f"{diag['above_50d_ma']} closed above their 50-day MA and {diag['new_20d_high']} made a new "
                 f"20-day closing high; {len(diag['both'])} did both.")
        if diag["closest"]:
            near = ", ".join(f"{s} (+{g:.1%})" for s, g in diag["closest"])
            L.append(f"- Closest to a signal (above the 50-day MA, gain needed for a new 20-day high): {near}")
        if last and last.get("free_slots", 1) == 0:
            L.append("- All 5 position slots are in use.")
        L.append("")

    if last and (last["buys"] or last["sells"] or last["skipped_buys"]):
        L.append(f"## Simulated fills on {asof.date()}")
        for b in last["buys"]:
            extra = f"; {b['note']}" if b["note"] else ""
            L.append(f"- Bought {b['symbol']}: planned {b['planned_shares']:.4f}, filled {b['filled_shares']:.4f} "
                     f"shares at ${b['price']:.2f}, stop ${b['stop']:.2f}{extra}")
        for s in last["skipped_buys"]:
            L.append(f"- Skipped {s['symbol']}: {s['reason']}")
        for t in last["sells"]:
            if t["exit_date"] == str(asof.date()):
                L.append(f"- Sold {t['symbol']} at ${t['exit_price']:.2f}: {REASON.get(t['exit_reason'])}, "
                         f"P/L ${t['pnl']:+,.2f} ({t['return_pct']:+.1%})")
        L.append("")

    L.append("## Open positions")
    if not state["positions"]:
        L.append("None.")
    else:
        L.append("| Symbol | Shares | Entry date | Entry price | Last close | P/L | Stop |")
        L.append("|---|---|---|---|---|---|---|")
        for p in state["positions"]:
            px = state["last_close"][p["symbol"]]
            L.append(f"| {p['symbol']} | {p['shares']:.4f} | {p['entry_date']} | ${p['entry_price']:.2f} "
                     f"| ${px:.2f} | {px / p['entry_price'] - 1:+.1%} | ${p['stop']:.2f} |")
    L.append("")
    L.append("## Risk")
    largest = f"{risk['largest'][0]} {risk['largest'][1] / equity:.0%}" if risk["largest"] else "none"
    L.append(f"- Cash {risk['cash_pct']:.0%}, invested {risk['invested_pct']:.0%}, largest position {largest}")
    if state["positions"]:
        L.append(f"- If every stop were hit at its stop price: −{risk['loss_if_all_stops_hit_pct']:.1%} of equity. "
                 "Overnight gaps can make the real loss larger (worst single trade in the backtest: "
                 "−4.96% of equity).")
    else:
        L.append("- No positions, so nothing is at risk.")
    L.append("")
    L.append("## Closed trades")
    if not trades:
        L.append("None yet.")
    else:
        L.append("| Symbol | Entry date | Exit date | P/L | Return | Reason |")
        L.append("|---|---|---|---|---|---|")
        for t in trades:
            L.append(f"| {t['symbol']} | {t['entry_date']} | {t['exit_date']} | ${t['pnl']:+,.2f} "
                     f"| {t['return_pct']:+.1%} | {REASON.get(t['exit_reason'], t['exit_reason'])} |")
    report = "\n".join(L) + "\n"

    snap = {
        "status": status, "status_detail": why, "data_problems": problems,
        "asof": str(asof.date()), "latest_completed_session": str(expected.date()),
        "orders_valid_for_open_of": str(nxt.date()),
        "orders_expire_at": session_open(nxt).isoformat(),
        "start": cfg["start"], "strategy": cfg["strategy"], "universe": UNIVERSE_LABEL[cfg["universe"]],
        "buyable_this_month": universe_now, "initial_cash": cfg["cash"],
        "equity": round(equity, 2), "return_pct": round(equity / cfg["cash"] - 1, 4),
        "spy_return_pct": round(bret, 4), "market_filter_on": filt,
        "next_open_orders": ([{"side": "SELL", "symbol": s, "reason": REASON.get(r, r)} for s, r in exits.items()]
                             + [{"side": "BUY", "symbol": o["symbol"], "shares": round(o["shares"], 4),
                                 "approx_value": round(o["shares"] * o["signal_close"], 2), "rank": o["rank"],
                                 "candidates": o["candidates"], "sized_by": o["sized_by"],
                                 "planned_stop": round(o["planned_stop"], 2)} for o in orders]),
        "no_signal_reasons": diag if status == "NO SIGNAL" else None,
        "positions": [{"symbol": p["symbol"], "shares": round(p["shares"], 4), "entry_date": p["entry_date"],
                       "entry_price": round(p["entry_price"], 2),
                       "last_close": round(state["last_close"][p["symbol"]], 2),
                       "pnl_pct": round(state["last_close"][p["symbol"]] / p["entry_price"] - 1, 4),
                       "stop": round(p["stop"], 2)} for p in state["positions"]],
        "risk": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in risk.items()},
        "closed_trades": [{"symbol": t["symbol"], "entry_date": t["entry_date"], "exit_date": t["exit_date"],
                           "pnl": round(t["pnl"], 2), "return_pct": round(t["return_pct"], 4),
                           "reason": REASON.get(t["exit_reason"], t["exit_reason"])} for t in trades],
        "fees_paid": round(sum(t["fees"] for t in trades), 2),
        "report_markdown": report,
    }
    return report, snap


def write_orders_log(records: list[dict]) -> None:
    """Rebuild the order export from committed daily records."""
    rows = []
    for r in records:
        rows += [{"asof": r["date"], "side": "SELL", "symbol": s, "shares": None, "reason": w}
                 for s, w in r["new_exits"].items()]
        rows += [{"asof": r["date"], "side": "BUY", "symbol": o["symbol"], "shares": round(o["shares"], 4),
                  "reason": f"rank {o['rank']} of {o['candidates']}"} for o in r["new_orders"]]
    columns = ["asof", "side", "symbol", "shares", "reason"]
    ledger.atomic_write(PAPER / "orders_log.csv", pd.DataFrame(rows, columns=columns).to_csv(index=False))


# ---------- main ----------

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cash", type=float, default=3000.0, help="starting USD (new account only)")
    p.add_argument("--strategy", default="breakout", choices=list(STRATEGIES))
    p.add_argument("--universe", default="sp500", choices=list(UNIVERSE_LABEL),
                   help="universe for a NEW account (first run only)")
    args = p.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    (PAPER / "reports").mkdir(parents=True, exist_ok=True)

    cfg = load_config(args.cash, args.strategy, args.universe)
    expected = last_completed_session()
    if not cfg["start"]:
        cfg["start"] = str(expected.date())
        (PAPER / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    since = (min(expected, pd.Timestamp(cfg["start"])) - pd.Timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")

    problems: list[str] = []
    try:
        data, members = load_market(cfg, since)
    except Exception:
        logging.exception("Could not load market data")
        data, members = {}, None
        problems.append("Market data could not be loaded.")
    data = {s: d[d.index <= expected] for s, d in data.items()}  # never use a still-forming bar
    market = data.pop(BENCHMARK, pd.DataFrame(columns=["Close"], index=pd.DatetimeIndex([])))
    if market.empty:
        problems.append("SPY has no usable bars.")
    elif market.index[-1] < expected:
        problems.append(f"SPY data ends {market.index[-1].date()}, but the latest completed session is "
                        f"{expected.date()} (data provider late?)")

    elig = pd.DataFrame()
    if members and not market.empty:
        elig = top_liquid_eligibility(data, members, market.index, top_n=SP500_TOP_N)
        for s in list(data):
            data[s] = data[s].assign(Eligible=elig[s].reindex(data[s].index).fillna(False))
    frames = prepare_frames(data, STRATEGIES[cfg["strategy"]](), market) if not market.empty else {}

    def eligible_on(day):
        if cfg["universe"] == "curated":
            return set(CURATED)
        return set(elig.columns[elig.loc[day]]) if day in elig.index else set()


    state = ledger.load_state(LEDGER)
    if state is None:  # new ledger: start the day before the first session
        first = pd.Timestamp(cfg["start"])
        state = ledger.new_state(first - pd.Timedelta(days=1), cfg["cash"])
    todo = sessions_between(state["asof"], min(expected, market.index[-1])) if not market.empty else []
    missing = [d for d in todo if d not in market.index]
    if missing:
        problems.append(f"SPY has no bar for session(s) {', '.join(str(d.date()) for d in missing)}")
        todo = [d for d in todo if d < missing[0]]

    for day in todo or ([expected] if expected in market.index else []):
        eligible = eligible_on(day)
        ranked = len(eligible)
        missing = sorted(s for s in eligible if s not in frames or day not in frames[s].index)
        if cfg["universe"] == "sp500" and ranked < SP500_TOP_N:
            problems.append(f"{day.date()}: only {ranked}/{SP500_TOP_N} candidates could be ranked.")
        elif missing:
            problems.append(f"{day.date()}: {ranked - len(missing)}/{ranked} candidates have quotes. "
                            f"Missing: {', '.join(missing)}.")
        else:
            continue
        todo = [d for d in todo if d < day]
        break

    bt_cfg = BacktestConfig(initial_cash=cfg["cash"])
    state, records, bad = ledger.advance(state, frames, bt_cfg, todo, lambda d: members_on(members, d), eligible_on)
    problems += bad
    for r in records:
        ledger.save_day(LEDGER, r)
    days = ledger.load_days(LEDGER)
    write_orders_log(days)
    asof = pd.Timestamp(state["asof"])
    if problems:
        status, why = "DATA ERROR", "Some data failed the checks, so the ledger stopped at the last good session."
    elif state["pending_entries"] or state["pending_exits"]:
        target = next_session(asof)
        if pd.Timestamp.now(tz="UTC") >= session_open(target):
            status, why = "ORDERS EXPIRED", f"The opening time for {target.date()} has passed."
        else:
            status, why = "ORDERS QUEUED", f"Simulated orders are queued for the open of {target.date()}."
    else:
        status, why = "NO SIGNAL", "Data is complete and no buy or sell rule fired."
    held = {p["symbol"] for p in state["positions"]}
    diag = diagnostics(frames, data, asof, held) if asof in market.index else {
        "buyable": 0, "evaluated": 0, "above_50d_ma": 0, "new_20d_high": 0, "both": [], "closest": []}
    universe_now = sorted(eligible_on(asof)) if members else []
    report, snap = build_report(status, why, state, days, cfg, expected, market, diag, problems, universe_now)

    ledger.atomic_write(PAPER / "reports" / f"{expected.date()}.md", report)
    out = ROOT / "muse" / "data" / "latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    ledger.atomic_write(out, json.dumps(snap, ensure_ascii=False, indent=2, default=str))
    print(report)


if __name__ == "__main__":
    main()
