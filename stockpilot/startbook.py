"""Start a paper account from real holdings, and price Canadian-listed funds.

The paper account books everything in USD because the strategy trades US
stocks. A Canadian-listed holding (e.g. VFV on the TSX) is converted to USD
with the daily USD/CAD rate, and placed on the NYSE calendar: on a day the TSX
is closed but the NYSE is open (e.g. Boxing Day), its last price carries over
and nothing can trade, so a held fund never blocks the ledger.

Config (paper/config.json) for an account started from real holdings:

    {"start": "2026-11-19",               # holdings are as of this session's close
     "capital_cad": 2000,                  # total principal, holdings included
     "initial_positions": [
        {"symbol": "NVDA",   "shares": 1, "avg_price": 154.32, "currency": "USD"},
        {"symbol": "VFV.TO", "shares": 4, "avg_price": 183.78, "currency": "CAD"}],
     ...}
"""

from __future__ import annotations

import pandas as pd

from .backtest.engine import BacktestConfig

FX_SYMBOL = "CAD=X"  # Yahoo: Canadian dollars per US dollar
PRICE_COLS = ["Open", "High", "Low", "Close"]


def to_usd_frame(cad: pd.DataFrame, usdcad: pd.Series, calendar: pd.DatetimeIndex) -> pd.DataFrame:
    """CAD-priced daily bars -> USD bars on the NYSE calendar.

    Missing sessions (TSX closed) repeat the previous close for every price
    field, with zero volume, so no simulated trade or stop can happen there.
    """
    fx = usdcad.reindex(calendar.union(usdcad.index)).ffill().reindex(calendar)
    d = cad.reindex(calendar)
    close = d["Close"].ffill()
    gap = d["Close"].isna()
    for c in ("Open", "High", "Low"):
        d[c] = d[c].where(~gap, close)
    d["Close"] = close
    d["Volume"] = d["Volume"].where(~gap, 0.0) if "Volume" in d else 0.0
    d[PRICE_COLS] = d[PRICE_COLS].div(fx, axis=0)
    return d.dropna(subset=["Close"])


def initial_state(cfg: dict, frames: dict, fx_on_start: float, bt: BacktestConfig) -> tuple[dict, list[str]]:
    """Ledger state at the close of cfg["start"], holding cfg["initial_positions"].

    Prices are converted to USD at the start date's rate. Each holding gets the
    strategy's usual protective stop (close - 2 x ATR), and if its exit rule is
    already true at the start close it is queued for sale at the next open,
    exactly as the strategy would treat any other position.
    Returns (state, notes).
    """
    start = pd.Timestamp(cfg["start"])
    spent_cad, positions, last_close, exits, notes = 0.0, [], {}, {}, []
    for h in cfg["initial_positions"]:
        sym, shares, px = h["symbol"], float(h["shares"]), float(h["avg_price"])
        cost_cad = shares * px * (fx_on_start if h["currency"] == "USD" else 1.0)
        spent_cad += cost_cad
        bar = frames[sym].loc[start]
        close, atr = float(bar["Close"]), float(bar["atr"])
        entry_usd = px if h["currency"] == "USD" else px / fx_on_start
        positions.append({"symbol": sym, "shares": shares, "entry_date": str(start.date()),
                          "entry_price": entry_usd, "entry_fee": 0.0,
                          "stop": close - bt.atr_stop_mult * atr, "bars_held": 0, "peak_close": close})
        last_close[sym] = close
        if bool(bar["exit"]):
            exits[sym] = "signal"
            notes.append(f"{sym}: exit rule already true at the {start.date()} close; sell queued for the next open")
    cash_cad = float(cfg["capital_cad"]) - spent_cad
    if cash_cad < 0:
        raise ValueError(f"Holdings cost {spent_cad:.2f} CAD, more than capital_cad {cfg['capital_cad']}")
    state = {"asof": str(start.date()), "cash": cash_cad / fx_on_start, "positions": positions,
             "pending_entries": [], "pending_exits": exits, "last_close": last_close}
    return state, notes
