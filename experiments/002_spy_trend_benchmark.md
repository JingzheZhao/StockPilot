# Experiment 002: SPY 200-day-MA timing as a benchmark

Written before running anything. Suggested by an external code review.

## Question

breakout_v1 (on the `sp500` universe) does three things on top of "only buy when SPY is above its 200-day MA": it picks stocks each month, ranks candidates daily, and sets a stop for every position. Does all that extra work beat the simplest version of the same idea?

## The benchmark: `spy_trend`

- Trade only SPY.
- After each close: if SPY closed above its 200-day simple moving average, hold SPY; otherwise hold cash.
- Every change fills at the **next open**, with the same costs as the main backtest (IBKR fixed commission with $1 minimum, 5 bps slippage per side), fractional shares, $3,000 starting cash, no interest on cash.

No parameters are tuned; 200 days is the same filter breakout_v1 already uses.

## Compared against

- `breakout_v1` on `sp500` (existing results)
- SPY buy-and-hold

Periods: 2015–2021, 2022 to date, full. Because `spy_trend` is a fixed benchmark rather than a variant being selected, running it on 2022 onward does not use up the held-out data for strategy selection.

## How to read it

- `spy_trend` ≥ `breakout_v1`: the stock selection layer is not adding value; work should focus there (or be dropped).
- `spy_trend` < buy-and-hold on return but with a much smaller drawdown: the market filter has some value as risk control.
- `spy_trend` worse than buy-and-hold on both: the 200-day filter itself is not helping.

---

## Results (2026-11-17)

| Period | Strategy | CAGR | Max DD | Sharpe | Time in market | Trades | Fees |
|:--|:--|:--|:--|--:|:--|--:|:--|
| 2015–2021 | breakout_v1 (sp500) | 7.5% | −22.5% | 0.51 | 90.5% | 241 | $485 |
| 2015–2021 | spy_trend | 9.3% | −19.7% | 0.79 | 87.0% | 33 | $33 |
| 2015–2021 | SPY buy-and-hold | 14.8% | −33.7% | 0.87 | 100% | 1 | $1 |
| 2022– | breakout_v1 (sp500) | 7.7% | −26.1% | 0.43 | 87.0% | 184 | $368 |
| 2022– | spy_trend | 7.2% | −20.5% | 0.66 | 77.5% | 31 | $31 |
| 2022– | SPY buy-and-hold | 10.8% | −24.5% | 0.68 | 100% | 1 | $1 |
| Full | breakout_v1 (sp500) | 7.6% | −25.8% | 0.47 | 89.1% | 423 | $849 |
| Full | spy_trend | 8.6% | −19.9% | 0.75 | 83.1% | 63 | $63 |
| Full | SPY buy-and-hold | 13.2% | −33.7% | 0.80 | 100% | 1 | $1 |

### Findings

1. **The stock-selection layer subtracts value.** Over the full period `spy_trend` earns more than breakout_v1 (8.6% vs 7.6%) with a smaller drawdown (−19.9% vs −25.8%), a much higher Sharpe ratio (0.75 vs 0.47), and one-seventh of the trades. From 2022 breakout_v1 earns 0.5% more a year but takes a 5.6-point deeper drawdown. Picking stocks, ranking them and stopping each one out added risk and fees without adding return.
2. **The 200-day filter is risk control, not extra return.** Against buy-and-hold, `spy_trend` gives up 4.6% a year to cut the worst drawdown from −33.7% to −19.9%. Its Sharpe ratio is slightly lower than buy-and-hold in every period, so the filter does not improve return per unit of risk; it trades return for a shallower drawdown.
3. Caveat: cash earns nothing here. Out of the market 17% of the time, real cash in a money-market fund would have earned interest (notably 2023–2026), which would raise `spy_trend` somewhat.

### Implication

Improving breakout_v1's stock-picking details (stops, sector caps) starts from a position that is already worse than the simplest version of its own idea. Any future stock-selection strategy should be required to beat `spy_trend`, not only buy-and-hold, before it is worth its extra complexity.
