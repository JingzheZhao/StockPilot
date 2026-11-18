"""Persistent paper-trading ledger.

The paper account is no longer recomputed from its start date every day.
Instead it keeps:

  paper/ledger/state.json        legacy checkpoint, read only during migration
  paper/ledger/days/<date>.json  immutable fills, orders, equity and end-of-day state

Each run restores the state and steps the shared Simulator through the
sessions that are new since then. Sessions already recorded are never
recomputed, so later data revisions cannot rewrite the record, and running
twice on the same day changes nothing.

A session is only recorded if its data passes the checks in `check_session`.
If not, the ledger stops at the previous session and the run reports a data
error instead of publishing orders.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from tempfile import NamedTemporaryFile

import pandas as pd

from .backtest.engine import BacktestConfig, Order, Position, Simulator

MIN_COVERAGE = 0.95  # share of this month's index members that must have a price
SPLIT_TOLERANCE = 0.005  # stored vs re-downloaded close differing more than this = corporate action


# ---------- (de)serialization ----------

def _jsonable(x):
    if isinstance(x, pd.Timestamp):
        return str(x.date())
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return x  # floats are stored at full precision, so a reload reproduces them exactly


def position_to_dict(p: Position) -> dict:
    return _jsonable(asdict(p))


def position_from_dict(d: dict) -> Position:
    return Position(**{**d, "entry_date": pd.Timestamp(d["entry_date"])})


def order_to_dict(o: Order) -> dict:
    return _jsonable(asdict(o))


def new_state(start_after: pd.Timestamp, cash: float) -> dict:
    return {"asof": str(pd.Timestamp(start_after).date()), "cash": cash, "positions": [],
            "pending_entries": [], "pending_exits": {}, "last_close": {}}


def load_state(ledger_dir: Path) -> dict | None:
    days = sorted((ledger_dir / "days").glob("*.json"))
    if days:
        record = json.loads(days[-1].read_text(encoding="utf-8"))
        if "state" in record:
            return record["state"]
    path = ledger_dir / "state.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save_state(ledger_dir: Path, state: dict) -> None:
    ledger_dir.mkdir(parents=True, exist_ok=True)
    (ledger_dir / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


def atomic_write(path: Path, content: str) -> None:
    with NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent,
                            prefix=f".{path.name}.", delete=False) as tmp:
        temp_path = Path(tmp.name)
        try:
            tmp.write(content)
            tmp.close()
            temp_path.replace(path)
        finally:
            temp_path.unlink(missing_ok=True)


def save_day(ledger_dir: Path, record: dict) -> None:
    days = ledger_dir / "days"
    days.mkdir(parents=True, exist_ok=True)
    path = days / f"{record['date']}.json"
    if path.exists():  # immutable: a recorded session is never rewritten
        raise FileExistsError(f"Session {record['date']} is already recorded")
    atomic_write(path, json.dumps(_jsonable(record), indent=2))


def load_days(ledger_dir: Path) -> list[dict]:
    days = ledger_dir / "days"
    if not days.exists():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(days.glob("*.json"))]


def restore(state: dict, frames: dict, cfg: BacktestConfig) -> Simulator:
    sim = Simulator(frames, cfg, cash=state["cash"])
    sim.positions = {d["symbol"]: position_from_dict(d) for d in state["positions"]}
    sim.pending_entries = [Order(**d) for d in state["pending_entries"]]
    sim.pending_exits = dict(state["pending_exits"])
    sim.last_close = dict(state["last_close"])
    return sim


def snapshot_state(sim: Simulator, asof: pd.Timestamp) -> dict:
    held = {p.symbol for p in sim.positions.values()}
    return {
        "asof": str(pd.Timestamp(asof).date()),
        "cash": sim.cash,
        "positions": [position_to_dict(p) for p in sim.positions.values()],
        "pending_entries": [order_to_dict(o) for o in sim.pending_entries],
        "pending_exits": dict(sim.pending_exits),
        "last_close": {s: v for s, v in sim.last_close.items() if s in held},
    }


# ---------- checks ----------

def check_session(day: pd.Timestamp, frames: dict, must_have: set[str], members: set[str] | None) -> list[str]:
    """Problems that make `day`'s data unusable (empty list = OK)."""
    problems = []
    missing = sorted(s for s in must_have if s not in frames or day not in frames[s].index)
    if missing:
        problems.append(f"no price for held or ordered stocks: {', '.join(missing)}")
    if members:
        priced = sum(1 for s in members if s in frames and day in frames[s].index)
        cov = priced / len(members)
        if cov < MIN_COVERAGE:
            problems.append(f"only {priced}/{len(members)} index members have a price ({cov:.0%} < {MIN_COVERAGE:.0%})")
    return problems


def apply_corporate_actions(state: dict, frames: dict) -> list[dict]:
    """Rescale held positions when re-downloaded history no longer matches the
    stored last close (e.g. a stock split restated past prices)."""
    asof = pd.Timestamp(state["asof"])
    events = []
    for p in state["positions"]:
        sym, stored = p["symbol"], state["last_close"].get(p["symbol"])
        f = frames.get(sym)
        if stored is None or f is None or asof not in f.index:
            continue
        now = float(f.loc[asof, "Close"])
        r = now / stored
        if abs(r - 1) <= SPLIT_TOLERANCE:
            continue
        p["shares"] /= r
        for k in ("entry_price", "stop", "peak_close"):
            p[k] *= r
        state["last_close"][sym] = now
        events.append({"symbol": sym, "price_ratio": round(r, 6),
                       "note": "stored price history no longer matches; position rescaled (likely split)"})
    for o in state["pending_entries"]:
        f = frames.get(o["symbol"])
        if f is not None and asof in f.index and o.get("signal_close"):
            r = float(f.loc[asof, "Close"]) / o["signal_close"]
            if abs(r - 1) > SPLIT_TOLERANCE:
                o["shares"] /= r
                o["atr"] *= r
                events.append({"symbol": o["symbol"], "price_ratio": round(r, 6), "note": "pending order rescaled"})
    return events


# ---------- advance ----------

def advance(state: dict, frames: dict, cfg: BacktestConfig, sessions: list[pd.Timestamp],
            members_for, eligible_for=lambda day: set()) -> tuple[dict, list[dict], list[str]]:
    """Step through new sessions, stopping at the first one with bad data.

    Returns (state after the last recorded session, records of the recorded
    sessions, problems). `members_for(day)` gives that day's index members for
    the coverage check, or None to skip it. The input state is not modified.
    """
    state = json.loads(json.dumps(state))  # work on a copy
    if not sessions:
        return state, [], []
    events = apply_corporate_actions(state, frames)
    sim = restore(state, frames, cfg)
    records, problems = [], []
    for day in sessions:
        must = set(sim.positions) | set(sim.pending_exits) | {o.symbol for o in sim.pending_entries}
        found = check_session(day, frames, must, members_for(day))
        missing_candidates = sorted(s for s in eligible_for(day)
                                    if s not in frames or day not in frames[s].index)
        if missing_candidates:
            found.append(f"missing candidate quotes: {', '.join(missing_candidates)}")
        if found:
            problems = [f"{day.date()}: {p}" for p in found]
            break
        log = _jsonable(sim.step(day))  # dates as "YYYY-MM-DD", ready to save
        log["corporate_actions"] = [] if records else events
        log["state"] = snapshot_state(sim, day)
        records.append(log)
    final = records[-1]["state"] if records else state
    return final, records, problems
