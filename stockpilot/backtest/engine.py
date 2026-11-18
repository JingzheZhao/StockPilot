"""Daily, portfolio-level backtest engine.

Timing model (the core anti-look-ahead rule):
  * Signals are computed from data up to the close of day t.
  * Orders from those signals fill at the OPEN of day t+1, with slippage.
  * Order quantity is fixed at signal time from the close of t (the open of
    t+1 is unknown when the order is placed).
  * Protective stops are checked intraday from t+1 onward. If the open gaps
    below the stop, the fill is the open, not the stop price.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from ..strategy.base import Strategy


@dataclass
class Costs:
    """IBKR Pro Fixed-style US stock pricing (approximate)."""

    per_share: float = 0.005
    minimum: float = 1.0
    max_pct: float = 0.01  # cap: 1% of trade value
    slippage_bps: float = 5.0

    def commission(self, shares: float, price: float) -> float:
        if shares <= 0:
            return 0.0
        fee = max(self.minimum, shares * self.per_share)
        return min(fee, shares * price * self.max_pct)

    def buy_price(self, price: float) -> float:
        return price * (1 + self.slippage_bps / 1e4)

    def sell_price(self, price: float) -> float:
        return price * (1 - self.slippage_bps / 1e4)


@dataclass
class BacktestConfig:
    initial_cash: float = 3000.0  # USD
    risk_per_trade: float = 0.02  # max loss at the stop, as share of equity
    max_position_pct: float = 0.20
    max_positions: int = 5
    atr_stop_mult: float = 2.0
    # Trailing stop: after each close, stop = max(stop, highest close since
    # entry - trail_atr_mult * ATR). None disables it.
    trail_atr_mult: float | None = None
    fractional: bool = True  # IBKR supports fractional US shares
    min_order_value: float = 50.0
    costs: Costs = field(default_factory=Costs)


@dataclass
class Position:
    symbol: str
    shares: float
    entry_date: pd.Timestamp
    entry_price: float
    entry_fee: float
    stop: float
    bars_held: int = 0
    peak_close: float = 0.0  # highest close since entry, for the trailing stop


@dataclass
class Order:
    symbol: str
    shares: float
    atr: float
    score: float


@dataclass
class BacktestResult:
    strategy: str
    equity: pd.Series
    positions_count: pd.Series
    trades: pd.DataFrame
    config: BacktestConfig
    # Filled only when run with close_at_end=False (paper trading):
    open_positions: list = field(default_factory=list)  # list[Position]
    pending_entries: list = field(default_factory=list)  # list[Order], fill at next open
    pending_exits: dict = field(default_factory=dict)  # {symbol: reason}, fill at next open
    last_close: dict = field(default_factory=dict)  # {symbol: price}


TRADE_COLUMNS = [
    "symbol", "entry_date", "entry_price", "exit_date", "exit_price", "shares",
    "fees", "pnl", "return_pct", "bars_held", "exit_reason",
]


def run_backtest(
    data: dict[str, pd.DataFrame],
    strategy: Strategy,
    market: pd.DataFrame,
    config: BacktestConfig | None = None,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
    close_at_end: bool = True,
) -> BacktestResult:
    """close_at_end=False keeps positions open and returns the orders queued
    for the next open. Paper trading uses this to get "what to do tomorrow"."""
    cfg = config or BacktestConfig()
    costs = cfg.costs

    # Indicators are computed on the full history (incl. warmup), then the
    # simulation only trades inside [start, end].
    frames: dict[str, pd.DataFrame] = {}
    for sym, df in data.items():
        sig = strategy.prepare(df, market)
        frames[sym] = df[["Open", "High", "Low", "Close"]].join(sig)

    calendar = market.index
    if start is not None:
        calendar = calendar[calendar >= pd.Timestamp(start)]
    if end is not None:
        calendar = calendar[calendar <= pd.Timestamp(end)]
    if len(calendar) == 0:
        raise ValueError("Empty backtest window")

    cash = cfg.initial_cash
    positions: dict[str, Position] = {}
    last_close: dict[str, float] = {}
    pending_entries: list[Order] = []
    pending_exits: dict[str, str] = {}
    trades: list[dict] = []
    equity_curve: dict[pd.Timestamp, float] = {}
    pos_count: dict[pd.Timestamp, int] = {}

    def bar(sym: str, day: pd.Timestamp):
        f = frames[sym]
        if day not in f.index:
            return None
        return f.loc[day]

    def close_position(pos: Position, day, raw_price: float, reason: str):
        nonlocal cash
        price = costs.sell_price(raw_price)
        fee = costs.commission(pos.shares, price)
        cash += pos.shares * price - fee
        cost_basis = pos.shares * pos.entry_price + pos.entry_fee
        pnl = pos.shares * price - fee - cost_basis
        trades.append({
            "symbol": pos.symbol,
            "entry_date": pos.entry_date,
            "entry_price": pos.entry_price,
            "exit_date": day,
            "exit_price": price,
            "shares": pos.shares,
            "fees": pos.entry_fee + fee,
            "pnl": pnl,
            "return_pct": pnl / cost_basis,
            "bars_held": pos.bars_held,
            "exit_reason": reason,
        })
        del positions[pos.symbol]

    for day in calendar:
        # 1) Fill yesterday's orders at today's open. Exits first to free cash.
        for sym, reason in list(pending_exits.items()):
            b = bar(sym, day)
            if b is None or sym not in positions:
                continue  # no quote today; retry tomorrow
            close_position(positions[sym], day, float(b["Open"]), reason)
            del pending_exits[sym]

        for order in sorted(pending_entries, key=lambda o: -o.score):
            if len(positions) >= cfg.max_positions or order.symbol in positions:
                continue
            b = bar(order.symbol, day)
            if b is None:
                continue
            price = costs.buy_price(float(b["Open"]))
            shares = order.shares
            fee = costs.commission(shares, price)
            if shares * price + fee > cash:  # not enough cash: shrink order
                shares = max(cash - costs.minimum, 0) / price
                if not cfg.fractional:
                    shares = math.floor(shares)
                fee = costs.commission(shares, price)
            if shares * price < cfg.min_order_value:
                continue
            cash -= shares * price + fee
            positions[order.symbol] = Position(
                symbol=order.symbol,
                shares=shares,
                entry_date=day,
                entry_price=price,
                entry_fee=fee,
                stop=price - cfg.atr_stop_mult * order.atr,
                peak_close=price,
            )
        pending_entries = []

        # 2) Intraday protective stops.
        for sym, pos in list(positions.items()):
            b = bar(sym, day)
            if b is None:
                continue
            if float(b["Low"]) <= pos.stop:
                fill = min(float(b["Open"]), pos.stop)
                close_position(pos, day, fill, "stop")
                pending_exits.pop(sym, None)

        # 3) Mark to market at the close.
        for sym in frames:
            b = bar(sym, day)
            if b is not None:
                last_close[sym] = float(b["Close"])
        for pos in positions.values():
            pos.bars_held += 1
            # Trailing stop: uses today's close and ATR, takes effect from
            # tomorrow's intraday check, so no future data is involved.
            if cfg.trail_atr_mult is not None:
                b = bar(pos.symbol, day)
                if b is not None:
                    pos.peak_close = max(pos.peak_close, float(b["Close"]))
                    a = float(b["atr"])
                    if a == a:  # not NaN
                        pos.stop = max(pos.stop, pos.peak_close - cfg.trail_atr_mult * a)
        equity = cash + sum(p.shares * last_close[p.symbol] for p in positions.values())
        equity_curve[day] = equity
        pos_count[day] = len(positions)

        # 4) Signals at today's close -> orders for tomorrow's open.
        for sym, pos in positions.items():
            b = bar(sym, day)
            if b is None:
                continue
            if bool(b["exit"]):
                pending_exits[sym] = "signal"

        slots = cfg.max_positions - (len(positions) - len(pending_exits))
        if slots <= 0:
            continue
        candidates = []
        for sym in frames:
            if sym in positions:
                continue
            b = bar(sym, day)
            if b is None or not bool(b["entry"]):
                continue
            candidates.append((float(b["score"]), sym, b))
        candidates.sort(key=lambda x: -x[0])
        for score, sym, b in candidates[:slots]:
            close, a = float(b["Close"]), float(b["atr"])
            if a <= 0:
                continue
            risk_shares = equity * cfg.risk_per_trade / (cfg.atr_stop_mult * a)
            cap_shares = equity * cfg.max_position_pct / close
            shares = min(risk_shares, cap_shares)
            if not cfg.fractional:
                shares = math.floor(shares)
            if shares * close < cfg.min_order_value:
                continue
            pending_entries.append(Order(sym, shares, a, score))

    if close_at_end:
        # Close anything still open at the final close so trade stats are complete.
        final_day = calendar[-1]
        for pos in list(positions.values()):
            close_position(pos, final_day, last_close[pos.symbol], "end_of_test")
        pending_entries, pending_exits = [], {}

    return BacktestResult(
        strategy=strategy.name,
        equity=pd.Series(equity_curve, name="equity"),
        positions_count=pd.Series(pos_count, name="positions"),
        trades=pd.DataFrame(trades, columns=TRADE_COLUMNS),
        config=cfg,
        open_positions=list(positions.values()),
        pending_entries=list(pending_entries),
        pending_exits=dict(pending_exits),
        last_close=dict(last_close),
    )


def buy_and_hold(
    df: pd.DataFrame,
    initial_cash: float,
    start=None,
    end=None,
    costs: Costs | None = None,
) -> pd.Series:
    """Benchmark: buy at the first open in the window, hold to the end."""
    costs = costs or Costs()
    d = df
    if start is not None:
        d = d[d.index >= pd.Timestamp(start)]
    if end is not None:
        d = d[d.index <= pd.Timestamp(end)]
    price = costs.buy_price(float(d["Open"].iloc[0]))
    shares = (initial_cash - costs.minimum) / price
    fee = costs.commission(shares, price)
    cash = initial_cash - shares * price - fee
    return (cash + shares * d["Close"]).rename("benchmark")
