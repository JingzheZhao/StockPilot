"""Performance metrics."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    return float((equity / peak - 1).min())


def cagr(equity: pd.Series) -> float:
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    if years <= 0 or equity.iloc[0] <= 0:
        return float("nan")
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1)


def sharpe(equity: pd.Series) -> float:
    r = equity.pct_change().dropna()
    if len(r) < 2 or r.std() == 0:
        return float("nan")
    return float(r.mean() / r.std() * math.sqrt(252))


def equity_stats(equity: pd.Series) -> dict:
    return {
        "total_return": float(equity.iloc[-1] / equity.iloc[0] - 1),
        "cagr": cagr(equity),
        "max_drawdown": max_drawdown(equity),
        "sharpe": sharpe(equity),
    }


def trade_stats(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"trades": 0, "win_rate": np.nan, "profit_factor": np.nan,
                "avg_win": np.nan, "avg_loss": np.nan, "avg_bars_held": np.nan,
                "fees_paid": 0.0}
    wins = trades[trades["pnl"] > 0]
    losses = trades[trades["pnl"] <= 0]
    gross_loss = -losses["pnl"].sum()
    return {
        "trades": int(len(trades)),
        "win_rate": float(len(wins) / len(trades)),
        "profit_factor": float(wins["pnl"].sum() / gross_loss) if gross_loss > 0 else np.inf,
        "avg_win": float(wins["return_pct"].mean()) if len(wins) else np.nan,
        "avg_loss": float(losses["return_pct"].mean()) if len(losses) else np.nan,
        "avg_bars_held": float(trades["bars_held"].mean()),
        "fees_paid": float(trades["fees"].sum()),
    }


def summarize(result, benchmark: pd.Series | None = None) -> dict:
    s = {"strategy": result.strategy, **equity_stats(result.equity), **trade_stats(result.trades)}
    s["exposure"] = float((result.positions_count > 0).mean())
    if benchmark is not None:
        b = equity_stats(benchmark)
        s["bench_cagr"] = b["cagr"]
        s["bench_max_drawdown"] = b["max_drawdown"]
        s["bench_sharpe"] = b["sharpe"]
        s["excess_cagr"] = s["cagr"] - b["cagr"]
    return s
