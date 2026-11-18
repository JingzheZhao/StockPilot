# StockPilot

A personal, rule-based US stock trading agent. Strategies are validated by backtest first, then run on a paper-trading account with simulated money. It runs daily in the cloud and can be queried from a phone through Meta Muse.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

pytest                             # all tests should pass
python scripts/backtest.py         # download data and backtest (~1 minute the first time)
python scripts/paper.py            # paper trading: run one trading day
```

## Strategy: breakout_v1

| | Rule |
|---|---|
| Market filter | Buy only when SPY closes above its 200-day MA |
| Entry | Close above the 50-day MA and a new 20-day closing high |
| Exit | Close below the 50-day MA |
| Stop-loss | Entry price − 2 × ATR(14) |
| Ranking | 60-day momentum |

Parameters were not tuned on any period. To add a strategy, implement `prepare()` under `stockpilot/strategy/`.

## Backtest

`python scripts/backtest.py` tests the strategy over three periods (2015–2021, 2022 to date, full) and compares it with SPY buy-and-hold. Results go to `results/`: `summary.md`, equity curves `equity_<period>.png`, and per-trade logs `trades_<strategy>_<period>.csv`. Options: `--cash 5000` sets starting cash, `--refresh` re-downloads prices, `--demo` runs offline on synthetic data.

Results on real data ($3,000 starting cash, after commissions and slippage):

| Period | CAGR | Max drawdown | SPY CAGR | SPY max drawdown |
|---|---|---|---|---|
| 2015–2021 | 13.1% | −20.5% | 14.8% | −33.7% |
| 2022 to date | 12.8% | −22.8% | 10.8% | −24.5% |
| Full | 12.1% | −24.5% | 13.2% | −33.7% |

Over the full period the strategy trails SPY slightly; its advantage is a smaller drawdown.

**Rejected strategy.** A trend-pullback strategy (buy when the close reclaims the 20-day MA) was tested alongside. It returned 16.0% CAGR in 2015–2021 but −8.9% from 2022, with a −46.9% max drawdown. The cause: entries sat too close to the 50-day MA exit line. 37% of trades entered within 1% of it and were shaken out by small moves, and fees reached almost half of the starting cash. Adding entry and exit buffers cut the drawdown to −18%, but CAGR from 2022 was only 3.3%, far behind breakout, so the strategy was removed.

## Backtest assumptions

- **Timing**: signals form after the close of day t and fill at the open of day t+1. Share counts use the close of day t, because the next open is unknown when the order is placed.
- **Stops**: checked against the intraday low from the fill day onward. If the open gaps below the stop, the fill is the open, not the stop price.
- **Costs**: IBKR Pro fixed pricing ($0.005 per share, $1 minimum, capped at 1% of trade value), plus 5 bps slippage per side.
- **Sizing**: a stop-out loses at most 2% of equity; one stock is at most 20% of equity; at most 5 positions; no leverage; fractional shares allowed.
- **Currency**: everything in USD, assuming a one-time CAD→USD conversion at the start; conversion cost is not modeled.
- **Prices**: yfinance adjusted prices (splits and dividends handled).

## Paper trading

Run `python scripts/paper.py` once per trading day. The first run records a start date and starting cash in `paper/config.json`. Each later run downloads fresh prices and replays the same backtest engine from the start date to the latest completed session with positions left open, which yields the orders for the next open. Paper trading and backtesting share one code path, so their results are directly comparable.

- Daily report: `paper/reports/<date>.md`
- Every order announced each day: `paper/orders_log.csv`, for later auditing
- Structured snapshot for Muse: `muse/data/latest.json`

To start over, delete `paper/config.json`.

## Cloud runs and the Meta Muse connector

- `.github/workflows/daily.yml`: GitHub Actions runs paper trading early each morning (New York time, before the open) after every trading day and commits the report back to the repo. No local computer needs to be on.
- `muse/`: a read-only MCP server on Vercel that uses only the Python standard library. Meta Muse uses it to query the summary, positions, orders for the next open, closed trades and the full report. It only reads `muse/data/latest.json`; it cannot place orders or change anything.
- Auth: every request must carry the key from the Vercel environment variable `MUSE_API_KEY` (`Authorization: Bearer <key>`). If the variable is unset, the server rejects every request.
- Each commit from Actions triggers a Vercel redeploy, so Muse always reads the latest day.

## Known limitations

- **Survivorship bias**: the universe is large caps picked with today's knowledge; companies that failed or were delisted are missing, so backtests are optimistic.
- **No earnings filter**: free data sources lack reliable historical earnings dates, so "no new positions before earnings" is not implemented.
- **yfinance is not an official data source**: fine for a prototype; long-term use should switch to a data API with clear terms of service.

## Tests

`tests/test_engine.py`:
- Signals fill at the next open; a signal on the last day never fills
- A gap below the stop fills at the open; an intraday hit fills at the stop price
- Sizing respects both the risk budget and the per-stock cap
- Commission minimum and cap
- **No look-ahead**: rewriting prices after day k leaves the equity curve and closed trades before day k unchanged
- Paper mode (positions kept open) produces the same equity curve as a normal backtest

`tests/test_muse.py`: rejects missing or wrong keys; MCP handshake works; each tool returns the right data; tools that do not exist (such as placing an order) cannot be called.

## Roadmap

1. ✅ Backtest engine and rule-based strategy
2. ✅ Validate on real data; keep breakout
3. ✅ Paper trading, daily cloud runs, Meta Muse queries
4. Run paper trading for at least 3 months and compare it with the backtest
5. LLM news-risk filter, as a controlled experiment: rules vs rules + LLM
6. Confirm IBKR Canada API order permissions before considering real money
