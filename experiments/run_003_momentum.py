"""Experiment 003: monthly momentum. The plan is in 003_monthly_momentum.md.

    python experiments/run_003_momentum.py in-sample
    python experiments/run_003_momentum.py out-of-sample --variant <name picked in-sample>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stockpilot.backtest.engine import Costs, buy_and_hold  # noqa: E402
from stockpilot.backtest.metrics import equity_stats  # noqa: E402
from stockpilot.backtest.rotation import momentum_rotation  # noqa: E402
from stockpilot.backtest.timing import ma_timing  # noqa: E402
from stockpilot.config import BENCHMARK, PERIODS, SP500_HISTORY, WARMUP_DAYS  # noqa: E402
from stockpilot.data.market import load_prices  # noqa: E402
from stockpilot.data.sp500 import load_history  # noqa: E402
from stockpilot.universe import monthly_members  # noqa: E402

VARIANTS = {  # fixed in advance; see 003_monthly_momentum.md
    "mom10": dict(top_n=10, market_filter=False),
    "mom20": dict(top_n=20, market_filter=False),
    "mom10_f": dict(top_n=10, market_filter=True),
    "mom20_f": dict(top_n=20, market_filter=True),
}
CASH = 3000.0


def load():
    history = load_history(ROOT / SP500_HISTORY)
    first = min(pd.Timestamp(s) for s, _ in PERIODS.values())
    start = (first - pd.Timedelta(days=WARMUP_DAYS)).strftime("%Y-%m-%d")
    data = load_prices([BENCHMARK, *sorted(set(history["symbol"]))], start,
                       cache_dir=ROOT / "data" / "cache")
    market = data.pop(BENCHMARK)
    return data, monthly_members(history), market


def row(name, equity, trades, fees):
    s = equity_stats(equity)
    return {"strategy": name, "cagr": s["cagr"], "max_dd": s["max_drawdown"], "sharpe": s["sharpe"],
            "trades": trades, "fees": fees}


def show(rows) -> str:
    df = pd.DataFrame(rows)
    for c in ("cagr", "max_dd"):
        df[c] = df[c].map(lambda v: f"{v:.1%}")
    df["sharpe"] = df["sharpe"].map(lambda v: f"{v:.2f}")
    df["fees"] = df["fees"].map(lambda v: f"${v:,.0f}")
    return df.to_markdown(index=False)


def benchmarks(market, start, end):
    costs = Costs()
    bh = buy_and_hold(market, CASH, start, end, costs)
    tm = ma_timing(market, CASH, 200, start, end, costs)
    return [row("SPY buy-and-hold", bh, 1, 1.0), row("spy_trend (200-day MA)", tm.equity, tm.switches, tm.fees)]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("stage", choices=["in-sample", "out-of-sample"])
    p.add_argument("--variant", choices=list(VARIANTS))
    args = p.parse_args()
    if args.stage == "out-of-sample" and not args.variant:
        sys.exit("Pick the variant from the in-sample stage first: --variant <name>")

    data, members, market = load()
    period = "in_sample" if args.stage == "in-sample" else "out_of_sample"
    start, end = PERIODS[period]
    names = list(VARIANTS) if args.stage == "in-sample" else [args.variant]
    rows = []
    for name in names:
        r = momentum_rotation(data, members, market, initial_cash=CASH, start=start, end=end,
                              **VARIANTS[name])
        rows.append(row(name, r.equity, r.trades, r.fees))
    picks = pd.DataFrame(rows)
    rows += benchmarks(market, start, end)
    label = "2015-2021" if period == "in_sample" else "2022 to date, run once"
    print(f"## {label}\n\n{show(rows)}")
    if args.stage == "in-sample":
        top = picks["cagr"].max()
        close = picks[picks["cagr"] >= top - 0.005]
        pick = close.sort_values("max_dd", ascending=False).iloc[0]["strategy"]
        print(f"\nPicked by the pre-registered rule: **{pick}**")


if __name__ == "__main__":
    main()
