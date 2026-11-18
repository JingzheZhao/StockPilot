"""Backtest the strategy over in-sample / out-of-sample periods and write a report.

Usage:
    python scripts/backtest.py                     # curated universe, real data (cached)
    python scripts/backtest.py --universe etf      # sector + index ETFs
    python scripts/backtest.py --universe sp500    # point-in-time S&P 500 top 50 by liquidity
    python scripts/backtest.py --refresh           # re-download data
    python scripts/backtest.py --demo              # synthetic data, no network
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stockpilot.backtest.engine import BacktestConfig, buy_and_hold, run_backtest  # noqa: E402
from stockpilot.backtest.metrics import summarize  # noqa: E402
from stockpilot.config import (  # noqa: E402
    BENCHMARK, CURATED, PERIODS, SECTOR_ETFS, SP500_HISTORY, SP500_TOP_N, WARMUP_DAYS,
)
from stockpilot.strategy import STRATEGIES  # noqa: E402

PCT = ["total_return", "cagr", "max_drawdown", "win_rate", "avg_win", "avg_loss",
       "exposure", "bench_cagr", "bench_max_drawdown", "excess_cagr"]


def load(args) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    if args.demo:
        from stockpilot.data.synthetic import make_universe

        data = make_universe([BENCHMARK, *CURATED], n_days=3000, seed=42)
        return data, data.pop(BENCHMARK)

    from stockpilot.data.market import load_prices

    first = min(pd.Timestamp(s) for s, _ in PERIODS.values())
    start = (first - pd.Timedelta(days=WARMUP_DAYS)).strftime("%Y-%m-%d")
    if args.universe == "curated":
        symbols = CURATED
    elif args.universe == "etf":
        symbols = SECTOR_ETFS
    else:  # sp500: every stock that was a member at any point
        from stockpilot.data.sp500 import load_history

        history = load_history(ROOT / SP500_HISTORY)
        symbols = sorted(set(history["symbol"]))
    data = load_prices([BENCHMARK, *symbols], start, refresh=args.refresh,
                       cache_dir=ROOT / "data" / "cache")
    if BENCHMARK not in data:
        sys.exit(f"Benchmark {BENCHMARK} unavailable; cannot continue.")
    market = data.pop(BENCHMARK)

    if args.universe == "sp500":
        from stockpilot.universe import coverage, monthly_members, top_liquid_eligibility

        members = monthly_members(history)
        cov = coverage(members, data, PERIODS["full"][0])
        print(f"Price data found for {cov:.1%} of S&P 500 member-months since {PERIODS['full'][0]}")
        elig = top_liquid_eligibility(data, members, market.index, top_n=SP500_TOP_N)
        ever = elig.columns[elig.any()]
        data = {s: data[s].assign(Eligible=elig[s].reindex(data[s].index).fillna(False))
                for s in ever}  # stocks never in the top N cannot trade; drop them for speed
        print(f"{len(data)} stocks were in the monthly top {SP500_TOP_N} at least once")
    return data, market


def fmt(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if c in PCT:
            out[c] = out[c].map(lambda v: f"{v:.1%}" if pd.notna(v) else "-")
        elif out[c].dtype.kind == "f":
            out[c] = out[c].map(lambda v: f"{v:.2f}" if pd.notna(v) else "-")
    return out


def plot(curves: dict[str, pd.Series], path: Path, title: str) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    fig, ax = plt.subplots(figsize=(10, 5))
    for name, s in curves.items():
        ax.plot(s.index, s.values, label=name, linewidth=1.4)
    ax.set_title(title)
    ax.set_ylabel("Equity (USD)")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--demo", action="store_true", help="use synthetic data")
    p.add_argument("--refresh", action="store_true", help="re-download prices")
    p.add_argument("--cash", type=float, default=3000.0, help="starting USD")
    p.add_argument("--universe", default="curated", choices=["curated", "etf", "sp500"])
    p.add_argument("--out", default=None, help="output folder (default results/<universe>)")
    args = p.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    data, market = load(args)
    cfg = BacktestConfig(initial_cash=args.cash)
    out = Path(args.out or ROOT / "results" / ("demo" if args.demo else args.universe))
    out.mkdir(parents=True, exist_ok=True)

    periods = dict(PERIODS)
    if args.demo:  # synthetic data starts in 2015; shift periods to fit
        idx = market.index
        mid = idx[len(idx) * 2 // 3]
        periods = {"in_sample": (str(idx[300].date()), str(mid.date())),
                   "out_of_sample": (str(mid.date()), None),
                   "full": (str(idx[300].date()), None)}

    rows = []
    for period, (start, end) in periods.items():
        bench = buy_and_hold(market, cfg.initial_cash, start, end, cfg.costs)
        curves = {f"{BENCHMARK} buy & hold": bench}
        for key, cls in STRATEGIES.items():
            res = run_backtest(data, cls(), market, cfg, start, end)
            s = summarize(res, bench)
            s["period"] = period
            s["universe"] = "demo" if args.demo else args.universe
            rows.append(s)
            curves[res.strategy] = res.equity
            res.trades.to_csv(out / f"trades_{key}_{period}.csv", index=False)
            res.equity.to_csv(out / f"equity_{key}_{period}.csv")
        plot(curves, out / f"equity_{period}.png", f"Equity curve: {period}")

    table = pd.DataFrame(rows)
    cols = ["universe", "period", "strategy", "total_return", "cagr", "max_drawdown", "sharpe",
            "trades", "win_rate", "profit_factor", "avg_bars_held", "exposure",
            "fees_paid", "bench_cagr", "bench_max_drawdown", "bench_sharpe", "excess_cagr"]
    table = table[cols]
    table.to_csv(out / "summary.csv", index=False)
    md = fmt(table).to_markdown(index=False) if _has_tabulate() else fmt(table).to_string(index=False)
    header = "# Backtest summary (DEMO: synthetic data)\n\n" if args.demo else "# Backtest summary\n\n"
    (out / "summary.md").write_text(header + md + "\n", encoding="utf-8")
    print(fmt(table).to_string(index=False))
    print(f"\nResults written to {out}")


def _has_tabulate() -> bool:
    try:
        import tabulate  # noqa: F401
        return True
    except ImportError:
        return False


if __name__ == "__main__":
    main()
