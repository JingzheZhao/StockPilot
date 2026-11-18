"""Daily paper trading (simulated money, no broker).

Run once per trading day after the US close:
    python scripts/paper.py

How it works
------------
The first run records a start date and starting cash in paper/config.json.
Every run downloads fresh prices and replays the SAME backtest engine from that
start date to the latest completed trading day, with positions left open. The
engine's no-look-ahead rule (tested in tests/test_engine.py) means replaying
gives the same history every day plus the newest day, so paper results and
backtest results come from one piece of code.

Output: a report in paper/reports/<date>.md, and every order the agent
announced is appended to paper/orders_log.csv so it can be audited later.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stockpilot.backtest.engine import BacktestConfig, buy_and_hold, run_backtest  # noqa: E402
from stockpilot.config import BENCHMARK, UNIVERSE  # noqa: E402
from stockpilot.data.market import load_prices  # noqa: E402
from stockpilot.strategy import STRATEGIES  # noqa: E402
from stockpilot.strategy.indicators import sma  # noqa: E402

PAPER = ROOT / "paper"
NY = ZoneInfo("America/New_York")
HISTORY_DAYS = 600  # enough for the 200-day market filter and 60-day momentum
REASON = {"signal": "closed below 50-day MA", "stop": "stop-loss hit"}


def load_config(cash: float, strategy: str) -> dict:
    path = PAPER / "config.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"start": None, "cash": cash, "strategy": strategy}


def drop_incomplete_today(data: dict[str, pd.DataFrame]) -> None:
    """Before 16:30 New York time, today's bar is still moving; ignore it."""
    now = datetime.now(NY)
    today = pd.Timestamp(now.date())
    if now.hour * 60 + now.minute >= 16 * 60 + 30:
        return
    for sym, df in data.items():
        data[sym] = df[df.index < today]


def build_report(res, bench: pd.Series, market: pd.DataFrame, cfg: dict) -> str:
    asof = res.equity.index[-1].date()
    eq = res.equity.iloc[-1]
    ret = eq / cfg["cash"] - 1
    bret = bench.iloc[-1] / bench.iloc[0] - 1
    m = market["Close"]
    filt = m.iloc[-1] > sma(m, 200).iloc[-1]

    L = [f"# Paper trading report {asof}", ""]
    L.append(f"- Started: {cfg['start']} with ${cfg['cash']:,.0f} (simulated money, not real)")
    L.append(f"- Account value: **${eq:,.2f}** ({ret:+.2%})")
    L.append(f"- SPY buy-and-hold over the same period: {bret:+.2%}")
    L.append("- Market filter: " + ("ON (SPY above its 200-day MA, new buys allowed)" if filt
                                   else "OFF (SPY below its 200-day MA, no new buys)"))
    L.append("")

    L.append("## Orders for the next open")
    if not res.pending_exits and not res.pending_entries:
        L.append("None.")
    for sym, why in res.pending_exits.items():
        pos = next(p for p in res.open_positions if p.symbol == sym)
        L.append(f"- **SELL {sym}** {pos.shares:.4f} shares (reason: {REASON.get(why, why)})")
    for o in sorted(res.pending_entries, key=lambda o: -o.score):
        close = res.last_close[o.symbol]
        stop = close - res.config.atr_stop_mult * o.atr
        L.append(f"- **BUY {o.symbol}** {o.shares:.4f} shares, about ${o.shares * close:,.0f} "
                 f"(last close ${close:.2f}; stop about ${stop:.2f}, set from the actual fill price)")
    L.append("")

    L.append("## Open positions")
    if not res.open_positions:
        L.append("None.")
    else:
        L.append("| Symbol | Shares | Entry date | Entry price | Last close | P/L | Stop |")
        L.append("|---|---|---|---|---|---|---|")
        for p in res.open_positions:
            px = res.last_close[p.symbol]
            L.append(f"| {p.symbol} | {p.shares:.4f} | {p.entry_date.date()} | ${p.entry_price:.2f} "
                     f"| ${px:.2f} | {px / p.entry_price - 1:+.1%} | ${p.stop:.2f} |")
    L.append("")

    L.append("## Closed trades")
    t = res.trades
    if t.empty:
        L.append("None yet.")
    else:
        L.append("| Symbol | Entry date | Exit date | P/L | Return | Reason |")
        L.append("|---|---|---|---|---|---|")
        for _, r in t.iterrows():
            L.append(f"| {r.symbol} | {r.entry_date.date()} | {r.exit_date.date()} | ${r.pnl:+,.2f} "
                     f"| {r.return_pct:+.1%} | {REASON.get(r.exit_reason, r.exit_reason)} |")
        L.append("")
        L.append(f"{len(t)} trades, {int((t.pnl > 0).sum())} profitable, total fees ${t.fees.sum():.2f}")
    return "\n".join(L) + "\n"


def log_orders(res) -> None:
    asof = str(res.equity.index[-1].date())
    rows = [{"asof": asof, "side": "SELL", "symbol": s, "shares": None, "reason": w}
            for s, w in res.pending_exits.items()]
    rows += [{"asof": asof, "side": "BUY", "symbol": o.symbol, "shares": round(o.shares, 4),
              "reason": "signal"} for o in res.pending_entries]
    path = PAPER / "orders_log.csv"
    old = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["asof", "side", "symbol", "shares", "reason"])
    old = old[old["asof"].astype(str) != asof]  # rerun on the same day replaces that day
    pd.concat([old, pd.DataFrame(rows)], ignore_index=True).to_csv(path, index=False)


def snapshot(res, bench: pd.Series, market: pd.DataFrame, cfg: dict, report: str) -> dict:
    """Structured copy of today's state, read by the Muse connector (muse/)."""
    m = market["Close"]
    eq = float(res.equity.iloc[-1])
    t = res.trades
    return {
        "asof": str(res.equity.index[-1].date()),
        "start": cfg["start"],
        "strategy": cfg["strategy"],
        "initial_cash": cfg["cash"],
        "equity": round(eq, 2),
        "return_pct": round(eq / cfg["cash"] - 1, 4),
        "spy_return_pct": round(float(bench.iloc[-1] / bench.iloc[0] - 1), 4),
        "market_filter_on": bool(m.iloc[-1] > sma(m, 200).iloc[-1]),
        "next_open_orders": (
            [{"side": "SELL", "symbol": s, "shares": round(next(p.shares for p in res.open_positions
                                                              if p.symbol == s), 4),
              "reason": REASON.get(w, w)} for s, w in res.pending_exits.items()]
            + [{"side": "BUY", "symbol": o.symbol, "shares": round(o.shares, 4),
                "approx_value": round(o.shares * res.last_close[o.symbol], 2)}
               for o in sorted(res.pending_entries, key=lambda o: -o.score)]
        ),
        "positions": [
            {"symbol": p.symbol, "shares": round(p.shares, 4), "entry_date": str(p.entry_date.date()),
             "entry_price": round(p.entry_price, 2), "last_close": round(res.last_close[p.symbol], 2),
             "pnl_pct": round(res.last_close[p.symbol] / p.entry_price - 1, 4), "stop": round(p.stop, 2)}
            for p in res.open_positions
        ],
        "closed_trades": [
            {"symbol": r.symbol, "entry_date": str(r.entry_date.date()), "exit_date": str(r.exit_date.date()),
             "pnl": round(r.pnl, 2), "return_pct": round(r.return_pct, 4),
             "reason": REASON.get(r.exit_reason, r.exit_reason)}
            for _, r in t.iterrows()
        ],
        "fees_paid": round(float(t.fees.sum()), 2) if not t.empty else 0.0,
        "report_markdown": report,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cash", type=float, default=3000.0, help="starting USD (first run only)")
    p.add_argument("--strategy", default="breakout", choices=list(STRATEGIES))
    args = p.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    PAPER.mkdir(exist_ok=True)
    (PAPER / "reports").mkdir(exist_ok=True)

    cfg = load_config(args.cash, args.strategy)
    since = (pd.Timestamp.now() - pd.Timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
    if cfg["start"]:
        since = min(since, (pd.Timestamp(cfg["start"]) - pd.Timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d"))
    data = load_prices([BENCHMARK, *UNIVERSE], since, refresh=True, cache_dir=PAPER / "cache")
    if BENCHMARK not in data:
        sys.exit("SPY download failed; skipping today's run.")
    drop_incomplete_today(data)
    market = data.pop(BENCHMARK)

    if not cfg["start"]:
        cfg["start"] = str(market.index[-1].date())  # first completed day = day 1
        (PAPER / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    bt_cfg = BacktestConfig(initial_cash=cfg["cash"])
    res = run_backtest(data, STRATEGIES[cfg["strategy"]](), market, bt_cfg,
                       start=cfg["start"], close_at_end=False)
    bench = buy_and_hold(market, cfg["cash"], cfg["start"], None, bt_cfg.costs)

    report = build_report(res, bench, market, cfg)
    asof = res.equity.index[-1].date()
    (PAPER / "reports" / f"{asof}.md").write_text(report, encoding="utf-8")
    log_orders(res)
    snap = json.dumps(snapshot(res, bench, market, cfg, report), ensure_ascii=False, indent=2)
    out = ROOT / "muse" / "data" / "latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(snap, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
