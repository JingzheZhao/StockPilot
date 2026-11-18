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
    signal_close: float | None = None  # close on the signal day; detects later splits


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


def prepare_frames(data: dict[str, pd.DataFrame], strategy: Strategy,
                   market: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """OHLC plus the strategy's per-day columns, computed on the full history."""
    return {sym: df[["Open", "High", "Low", "Close"]].join(strategy.prepare(df, market))
            for sym, df in data.items()}


class Simulator:
    """Steps the portfolio one trading day at a time.

    Backtests call step() for every day in a window. Paper trading restores
    the saved state (cash, positions, queued orders) and steps only the new
    days, so days already recorded are never recomputed.
    """

    def __init__(self, frames: dict[str, pd.DataFrame], cfg: BacktestConfig, cash: float | None = None):
        self.frames = frames
        self.cfg = cfg
        self.cash = cfg.initial_cash if cash is None else cash
        self.positions: dict[str, Position] = {}
        self.pending_entries: list[Order] = []
        self.pending_exits: dict[str, str] = {}
        self.last_close: dict[str, float] = {}
        self.trades: list[dict] = []

    def bar(self, sym: str, day: pd.Timestamp):
        f = self.frames.get(sym)
        if f is None or day not in f.index:
            return None
        return f.loc[day]

    def equity(self) -> float:
        return self.cash + sum(p.shares * self.last_close[p.symbol] for p in self.positions.values())

    def close_position(self, pos: Position, day, raw_price: float, reason: str) -> dict:
        costs = self.cfg.costs
        price = costs.sell_price(raw_price)
        fee = costs.commission(pos.shares, price)
        self.cash += pos.shares * price - fee
        cost_basis = pos.shares * pos.entry_price + pos.entry_fee
        pnl = pos.shares * price - fee - cost_basis
        trade = {
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
        }
        self.trades.append(trade)
        del self.positions[pos.symbol]
        return trade

    def step(self, day: pd.Timestamp) -> dict:
        """Simulate one session. Returns a log of fills, skips and new orders."""
        cfg, costs = self.cfg, self.cfg.costs
        log = {"date": day, "sells": [], "buys": [], "skipped_buys": [], "new_orders": [], "new_exits": {}}

        # 1) Fill yesterday's orders at today's open. Exits first to free cash.
        for sym, reason in list(self.pending_exits.items()):
            b = self.bar(sym, day)
            if b is None or sym not in self.positions:
                continue  # no quote today; retry tomorrow
            t = self.close_position(self.positions[sym], day, float(b["Open"]), reason)
            log["sells"].append(t)
            del self.pending_exits[sym]

        for order in sorted(self.pending_entries, key=lambda o: -o.score):
            skip = {"symbol": order.symbol, "planned_shares": order.shares}
            if order.symbol in self.positions:
                continue
            if len(self.positions) >= cfg.max_positions:
                log["skipped_buys"].append({**skip, "reason": "no free position slot"})
                continue
            b = self.bar(order.symbol, day)
            if b is None:
                log["skipped_buys"].append({**skip, "reason": "no price at the open"})
                continue
            price = costs.buy_price(float(b["Open"]))
            shares = order.shares
            fee = costs.commission(shares, price)
            note = ""
            if shares * price + fee > self.cash:  # not enough cash: shrink order
                shares = max(self.cash - costs.minimum, 0) / price
                if not cfg.fractional:
                    shares = math.floor(shares)
                fee = costs.commission(shares, price)
                note = "reduced: not enough cash"
            if shares * price < cfg.min_order_value:
                log["skipped_buys"].append({**skip, "reason": "order too small after cash limit"})
                continue
            self.cash -= shares * price + fee
            pos = Position(
                symbol=order.symbol,
                shares=shares,
                entry_date=day,
                entry_price=price,
                entry_fee=fee,
                stop=price - cfg.atr_stop_mult * order.atr,
                peak_close=price,
            )
            self.positions[order.symbol] = pos
            log["buys"].append({"symbol": order.symbol, "planned_shares": order.shares,
                                "filled_shares": shares, "price": price, "fee": fee,
                                "stop": pos.stop, "note": note})
        self.pending_entries = []

        # 2) Intraday protective stops.
        for sym, pos in list(self.positions.items()):
            b = self.bar(sym, day)
            if b is None:
                continue
            if float(b["Low"]) <= pos.stop:
                fill = min(float(b["Open"]), pos.stop)
                log["sells"].append(self.close_position(pos, day, fill, "stop"))
                self.pending_exits.pop(sym, None)

        # 3) Mark to market at the close.
        for sym in self.frames:
            b = self.bar(sym, day)
            if b is not None:
                self.last_close[sym] = float(b["Close"])
        for pos in self.positions.values():
            pos.bars_held += 1
            # Trailing stop: uses today's close and ATR, takes effect from
            # tomorrow's intraday check, so no future data is involved.
            if cfg.trail_atr_mult is not None:
                b = self.bar(pos.symbol, day)
                if b is not None:
                    pos.peak_close = max(pos.peak_close, float(b["Close"]))
                    a = float(b["atr"])
                    if a == a:  # not NaN
                        pos.stop = max(pos.stop, pos.peak_close - cfg.trail_atr_mult * a)
        equity = self.equity()
        log["equity"] = equity
        log["positions"] = len(self.positions)

        # 4) Signals at today's close -> orders for tomorrow's open.
        for sym in self.positions:
            b = self.bar(sym, day)
            if b is None:
                continue
            if bool(b["exit"]):
                self.pending_exits[sym] = "signal"
                log["new_exits"][sym] = "signal"

        slots = cfg.max_positions - (len(self.positions) - len(self.pending_exits))
        log["free_slots"] = max(slots, 0)
        if slots <= 0:
            return log
        candidates = []
        for sym in self.frames:
            if sym in self.positions:
                continue
            b = self.bar(sym, day)
            if b is None or not bool(b["entry"]):
                continue
            candidates.append((float(b["score"]), sym, b))
        candidates.sort(key=lambda x: -x[0])
        for rank, (score, sym, b) in enumerate(candidates[:slots], start=1):
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
            order = Order(sym, shares, a, score, close)
            self.pending_entries.append(order)
            log["new_orders"].append({
                "symbol": sym, "shares": shares, "signal_close": close, "atr": a, "score": score,
                "rank": rank, "candidates": len(candidates),
                "sized_by": "risk budget" if risk_shares < cap_shares else "20% position cap",
                "planned_stop": close - cfg.atr_stop_mult * a,
            })
        return log


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
    for the next open."""
    cfg = config or BacktestConfig()

    # Indicators are computed on the full history (incl. warmup), then the
    # simulation only trades inside [start, end].
    sim = Simulator(prepare_frames(data, strategy, market), cfg)

    calendar = market.index
    if start is not None:
        calendar = calendar[calendar >= pd.Timestamp(start)]
    if end is not None:
        calendar = calendar[calendar <= pd.Timestamp(end)]
    if len(calendar) == 0:
        raise ValueError("Empty backtest window")

    equity_curve: dict[pd.Timestamp, float] = {}
    pos_count: dict[pd.Timestamp, int] = {}
    for day in calendar:
        log = sim.step(day)
        equity_curve[day] = log["equity"]
        pos_count[day] = log["positions"]

    if close_at_end:
        # Close anything still open at the final close so trade stats are
        # complete, and book those exit costs in the final equity point too,
        # so the equity curve and the trade list agree.
        final_day = calendar[-1]
        for pos in list(sim.positions.values()):
            sim.close_position(pos, final_day, sim.last_close[pos.symbol], "end_of_test")
        sim.pending_entries, sim.pending_exits = [], {}
        equity_curve[final_day] = sim.cash

    return BacktestResult(
        strategy=strategy.name,
        equity=pd.Series(equity_curve, name="equity"),
        positions_count=pd.Series(pos_count, name="positions"),
        trades=pd.DataFrame(sim.trades, columns=TRADE_COLUMNS),
        config=cfg,
        open_positions=list(sim.positions.values()),
        pending_entries=list(sim.pending_entries),
        pending_exits=dict(sim.pending_exits),
        last_close=dict(sim.last_close),
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
