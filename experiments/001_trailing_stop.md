# Experiment 001: trailing stop

Written before running anything. The plan below is fixed; results are appended at the end.

## Question

On the point-in-time `sp500` universe, does a trailing stop that lets winners run beat the current breakout_v1 exits, after costs?

## Motivation

On `sp500`, five trades produced 89% of gross profit. The current exit (close below the 50-day MA) may cut big winners early; a stop that follows the price up may hold them longer.

## Trailing stop rule

After each close, for every open position:

    peak = highest close since entry
    stop = max(previous stop, peak − k × ATR(14) at today's close)

The stop only moves up. It is checked against the next day's intraday low, like the initial stop, so it uses no future data. The initial stop (entry − 2 × ATR) is unchanged.

## Variants (fixed in advance)

| Name | Trailing stop | 50-day MA exit |
|---|---|---|
| `base` | none | yes (current strategy) |
| `trail3_ma` | k = 3 | yes |
| `trail2` | k = 2 | no |
| `trail3` | k = 3 | no |
| `trail4` | k = 4 | no |

Everything else is unchanged: universe, entries, sizing, costs, $3,000 starting cash.

## Procedure

1. Run all five variants on **2015-01-01 to 2021-12-31 only** (in-sample).
2. Pick the single variant with the highest CAGR in-sample. Ties within 0.5% go to the one with the smaller max drawdown.
3. Run **only that variant and `base`** once on **2022-01-01 to 2026-11** (out-of-sample). No further changes after seeing it.

## Pass criterion

The picked variant passes if, out-of-sample and after all costs:

- its CAGR beats SPY buy-and-hold, **and**
- its CAGR beats `base`.

If it fails, it is recorded as a failure and the live paper account keeps `base`.

---

## Results (2026-11-17)

### In-sample (2015–2021)

| variant   | CAGR   | Max DD   | Sharpe | Trades | Win rate | Avg hold (days) | Fees   |
|:----------|:-------|:---------|-------:|-------:|:---------|----------------:|:-------|
| base      | 7.5%   | −22.5%   |   0.51 |    241 | 36.1%    |            31.3 | $485   |
| trail3_ma | −1.2%  | −32.3%   |  −0.00 |    367 | 34.6%    |            19.4 | $739   |
| trail2    | −1.1%  | −30.8%   |   0.00 |    559 | 36.1%    |            12.0 | $1,123 |
| trail3    | 0.4%   | −25.6%   |   0.10 |    353 | 36.8%    |            20.3 | $712   |
| trail4    | 7.0%   | −24.7%   |   0.50 |    265 | 36.6%    |            27.7 | $536   |

SPY buy-and-hold over the same period: 14.8% CAGR, −33.7% max drawdown.

The pre-registered rule picked **`base`**: trail4 was within 0.5% CAGR of it but had a deeper drawdown. No trailing variant improved on the current exits, so the out-of-sample stage was **not run**, and the held-out 2022–2026 data stays unused for the next experiment.

### Verdict: failed

The hypothesis was wrong in direction. A trailing stop at 2–4 × ATR is *tighter* than the 50-day-MA exit, not looser: it shortened the average hold from 31 days to 12–28 days, so normal pullbacks knocked out positions that later recovered. Every variant traded more, paid more fees and earned less. Removing the MA exit did not help, because the trailing stop simply took over as an even earlier exit.

What this suggests for next time: if letting winners run is the goal, the exit needs to be *looser* than the 50-day MA (e.g. a longer MA), not a tighter ATR stop.

The trailing stop stays in the engine (`BacktestConfig.trail_atr_mult`, off by default, covered by tests) so future experiments can reuse it.
