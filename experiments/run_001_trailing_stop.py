"""Experiment 001: trailing stop. The plan is in 001_trailing_stop.md.

    python experiments/run_001_trailing_stop.py in-sample
    python experiments/run_001_trailing_stop.py out-of-sample --variant <name picked in-sample>

The out-of-sample stage refuses to run without an explicit variant, and runs
only that variant and `base`, so the held-out years are looked at once.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stockpilot.backtest.engine import BacktestConfig, buy_and_hold, run_backtest  # noqa: E402
from stockpilot.backtest.metrics import summarize  # noqa: E402
from stockpilot.config import PERIODS  # noqa: E402
from stockpilot.strategy import Breakout  # noqa: E402

# Fixed in advance; see 001_trailing_stop.md.
VARIANTS = {
    "base": (Breakout(name="base"), None),
    "trail3_ma": (Breakout(name="trail3_ma"), 3.0),
    "trail2": (Breakout(name="trail2", ma_exit=False), 2.0),
    "trail3": (Breakout(name="trail3", ma_exit=False), 3.0),
    "trail4": (Breakout(name="trail4", ma_exit=False), 4.0),
}


def load_sp500():
    spec = importlib.util.spec_from_file_location("bt", ROOT / "scripts" / "backtest.py")
    bt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bt)
    return bt.load(argparse.Namespace(demo=False, universe="sp500", refresh=False))


def run(names, period, data, market):
    start, end = PERIODS[period]
    rows = []
    for name in names:
        strat, trail = VARIANTS[name]
        cfg = BacktestConfig(initial_cash=3000.0, trail_atr_mult=trail)
        bench = buy_and_hold(market, cfg.initial_cash, start, end, cfg.costs)
        s = summarize(run_backtest(data, strat, market, cfg, start, end), bench)
        rows.append({"variant": name, "cagr": s["cagr"], "max_dd": s["max_drawdown"],
                     "sharpe": s["sharpe"], "trades": s["trades"], "win_rate": s["win_rate"],
                     "avg_hold_days": s["avg_bars_held"], "fees": s["fees_paid"],
                     "spy_cagr": s["bench_cagr"], "spy_max_dd": s["bench_max_drawdown"]})
    return pd.DataFrame(rows)


def show(df: pd.DataFrame) -> str:
    out = df.copy()
    for c in ("cagr", "max_dd", "win_rate", "spy_cagr", "spy_max_dd"):
        out[c] = out[c].map(lambda v: f"{v:.1%}")
    for c in ("sharpe", "avg_hold_days"):
        out[c] = out[c].map(lambda v: f"{v:.2f}")
    out["fees"] = out["fees"].map(lambda v: f"${v:,.0f}")
    return out.to_markdown(index=False)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("stage", choices=["in-sample", "out-of-sample"])
    p.add_argument("--variant", choices=[v for v in VARIANTS if v != "base"])
    args = p.parse_args()
    if args.stage == "out-of-sample" and not args.variant:
        sys.exit("Pick the variant from the in-sample stage first: --variant <name>")

    data, market = load_sp500()
    if args.stage == "in-sample":
        df = run(list(VARIANTS), "in_sample", data, market)
        best = df.sort_values("cagr", ascending=False)
        top = best.iloc[0]
        close = best[best["cagr"] >= top["cagr"] - 0.005]  # tie rule: within 0.5% CAGR
        pick = close.sort_values("max_dd", ascending=False).iloc[0]["variant"]
        print(f"## In-sample (2015-2021)\n\n{show(df)}\n\nPicked by the pre-registered rule: **{pick}**")
    else:
        df = run(["base", args.variant], "out_of_sample", data, market)
        print(f"## Out-of-sample (2022 to date), run once\n\n{show(df)}")


if __name__ == "__main__":
    main()
