# Experiment 003: monthly cross-sectional momentum

Written before running anything. This is the last stock-selection idea tried before deciding whether to stop strategy research.

## Question

Does buying the S&P 500 stocks with the strongest 12-month momentum, rebalanced monthly, beat both benchmarks after costs on a $3,000 account?

## Why this idea

Momentum (past 12-month winners keep outperforming for a few months) is one of the most documented effects in finance research. It trades monthly, which avoids breakout_v1's main problem of frequent trading and high fees. Known risks: the edge has shrunk since it became famous, and it has had sharp crashes (e.g. 2009).

## Rules

- **Universe:** stocks that were S&P 500 members at the start of the month (point-in-time, `data/universe/sp500_monthly.csv`) and have price data. No liquidity filter.
- **Signal (12-1 momentum):** return from 252 trading days ago to 21 trading days ago, using closes up to the last trading day *before* the month starts. The most recent month is skipped, as in the research literature.
- **Portfolio:** hold the top N by momentum, equal weight at entry.
- **Rebalance:** on the first trading day of each month, at the open. Sell stocks that dropped out of the top N; buy new entrants at equity / N each (limited by available cash). Stocks that stay are not traded, so their weights drift; this keeps the number of $1-minimum commissions down.
- **Optional market filter:** if SPY's last close before the month is at or below its 200-day MA, hold all cash that month.
- **Costs:** same as everywhere (IBKR fixed, $1 minimum, 5 bps slippage per side), fractional shares, $3,000 starting cash, no interest on cash.
- **Missing data:** if a held stock has no open price on a rebalance day (e.g. delisted), it is sold at its last known close.

## Variants (fixed in advance)

| Name | Top N | 200-day market filter |
|---|---|---|
| `mom10` | 10 | no |
| `mom20` | 20 | no |
| `mom10_f` | 10 | yes |
| `mom20_f` | 20 | yes |

## Procedure

1. Run all four on **2015-01-01 to 2021-12-31 only**.
2. Pick the highest CAGR; ties within 0.5% go to the smaller max drawdown.
3. Run only the pick once on **2022-01-01 to date**, alongside both benchmarks. No changes after that.

## Pass criterion

Out-of-sample, after all costs, the pick's CAGR must beat **both**:

- SPY buy-and-hold, and
- `spy_trend` (hold SPY only above its 200-day MA, experiment 002).

If it fails, strategy research stops here and the project is written up as is.

## Known bias

About 8% of member-months have no price data, mostly delisted or acquired companies. Momentum's worst losers are likely overrepresented among them, so results are somewhat optimistic.

---

## Results (2026-11-17)

### In-sample (2015–2021)

| Strategy | CAGR | Max DD | Sharpe | Trades | Fees |
|:--|:--|:--|--:|--:|:--|
| mom10 | 8.8% | −45.9% | 0.44 | 582 | $581 |
| mom20 | 6.4% | −41.9% | 0.38 | 1,030 | $1,021 |
| mom10_f | 3.8% | −42.6% | 0.28 | 556 | $553 |
| mom20_f | 0.5% | −39.6% | 0.13 | 1,031 | $1,020 |
| SPY buy-and-hold | 14.8% | −33.7% | 0.87 | 1 | $1 |
| spy_trend | 9.3% | −19.7% | 0.79 | 33 | $33 |

Every variant lost to both benchmarks in-sample. The rule picked **mom10**.

### Out-of-sample (2022 to date), run once

| Strategy | CAGR | Max DD | Sharpe | Trades | Fees |
|:--|:--|:--|--:|--:|:--|
| mom10 | 18.5% | −50.2% | 0.62 | 336 | $335 |
| SPY buy-and-hold | 10.8% | −24.5% | 0.68 | 1 | $1 |
| spy_trend | 7.2% | −20.5% | 0.66 | 31 | $31 |

Calendar years, mom10 vs SPY: 2022 −0.1% vs −18.2%; 2023 15.9% vs 26.2%; 2024 21.3% vs 24.9%; 2025 24.7% vs 17.7%; 2026 to date 28.6% vs 9.4%.

### Verdict: passed the pre-registered criterion, not accepted

By the rule written in advance (out-of-sample CAGR above both benchmarks), mom10 **passes**. The result is reported as such. But the evidence is too weak to act on:

1. **One episode drives it.** The account went from about $5,250 at the end of 2025 to $13,205 in June 2026, then fell 50% to $6,581 by November. The final CAGR depends heavily on when the test happens to end.
2. **It failed in the other period.** In 2015–2021 it trailed buy-and-hold by 6% a year with a −46% drawdown.
3. **Worse risk-adjusted.** Its Sharpe ratio is below buy-and-hold in both periods; the extra return came with about twice the drawdown.
4. **Optimistic data.** Missing delisted stocks flatter momentum.

### Lesson for the protocol

The pass criterion looked only at CAGR. It should also have required a Sharpe ratio at least equal to buy-and-hold and a bounded drawdown. That is not changed after the fact for this experiment; it applies to the next one, through `experiments/TEMPLATE.md`.

The 2022–2026 period has now been seen. Any momentum variant tuned after this point can only be validated on new data, i.e. paper trading.

### Correction: fill rules for missing prices (after external review)

The first version of `momentum_rotation` (1) dropped a target stock with no open price on the rebalance day and let the next-ranked stock take its place, which assumes knowing at the open which stocks will trade, and (2) sold a holding with no open price at its last close, a price that may not have been available.

Fixed: the target list is set from the previous day's data only; a buy with no open price is unfilled and its slot stays in cash; a sell with no open price waits for the next available open; holdings whose data ends for good are reported as `stuck`. Covered by tests.

Rerun with the original settings:

| Period | CAGR | Max DD | Sharpe | Unfilled buys | Delayed sells | Stuck holdings |
|:--|:--|:--|--:|--:|--:|--:|
| 2015–2021 | 8.8% | −45.9% | 0.44 | 0 | 0 | 0 |
| 2022– | 18.5% | −50.2% | 0.62 | 0 | 0 | 0 |

Identical to before: across all 143 monthly rebalances no top-10 pick lacked an open price, and no held stock's data ended early. The fill rules were never exercised here. The real gap is that delisted stocks are absent from the price data altogether, which no fill rule can fix. (Measured correctly, i.e. requiring a price inside each month, 10.1% of member-months lack data, not the "about 8%" written in the plan above.)
