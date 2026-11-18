"""Experiment 002: SPY 200-day-MA timing vs breakout_v1 (sp500) vs buy-and-hold.

    python experiments/run_002_spy_trend.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stockpilot.backtest.engine import Costs, buy_and_hold  # noqa: E402
from stockpilot.backtest.metrics import equity_stats  # noqa: E402
from stockpilot.backtest.timing import ma_timing  # noqa: E402
from stockpilot.config import PERIODS  # noqa: E402

CASH = 3000.0
spy = pd.read_csv(ROOT / "data" / "cache" / "SPY.csv", index_col=0, parse_dates=True)
breakout = pd.read_csv(ROOT / "results" / "sp500" / "summary.csv")
costs = Costs()

rows = []
for period, (start, end) in PERIODS.items():
    bh = buy_and_hold(spy, CASH, start, end, costs)
    tm = ma_timing(spy, CASH, 200, start, end, costs)
    b = breakout[breakout["period"] == period].iloc[0]
    for name, s, extra in [
        ("breakout_v1 (sp500)", {"cagr": b["cagr"], "max_drawdown": b["max_drawdown"], "sharpe": b["sharpe"]},
         {"in_market": b["exposure"], "trades": int(b["trades"]), "fees": b["fees_paid"]}),
        ("spy_trend (200-day MA)", equity_stats(tm.equity),
         {"in_market": tm.in_market.mean(), "trades": tm.switches, "fees": tm.fees}),
        ("SPY buy-and-hold", equity_stats(bh), {"in_market": 1.0, "trades": 1, "fees": 1.0}),
    ]:
        rows.append({"period": period, "strategy": name, "cagr": s["cagr"], "max_dd": s["max_drawdown"],
                     "sharpe": s["sharpe"], **extra})

df = pd.DataFrame(rows)
out = df.copy()
for c in ("cagr", "max_dd", "in_market"):
    out[c] = out[c].map(lambda v: f"{v:.1%}")
out["sharpe"] = out["sharpe"].map(lambda v: f"{v:.2f}")
out["fees"] = out["fees"].map(lambda v: f"${v:,.0f}")
print(out.to_markdown(index=False))
