# Experiment NNN: <title>

Copy this file before running anything. Fill in every section, commit it, and only then run code. Results are appended below the line at the end; nothing above it changes after a run, except clearly marked corrections.

## Question

One sentence: what would we learn, and what decision does it feed?

## Rules

Exact entry, exit, sizing, universe, rebalance timing and costs. Anything not listed stays as in the main backtest.

## Variants (fixed in advance)

| Name | What differs |
|---|---|

Keep the list short. Every extra variant raises the chance that the best one looks good by luck.

## Data and procedure

- **Selection data:** which dates are used to compare variants and pick one.
- **Selection rule:** the metric that picks the winner, and the tie-break.
- **Validation data:** which dates the single pick is tested on, once. Write down whether these dates have already been looked at by an earlier experiment. If they have (2022-01 to 2026-11 has been seen by experiments 002 and 003), they are not a clean test; the real validation has to be future paper trading.

## Acceptance criteria (all must hold on the validation data, after all costs)

| Criterion | Requirement |
|---|---|
| Return | CAGR above SPY buy-and-hold **and** above `spy_trend` |
| Risk-adjusted return | Sharpe ratio at least that of SPY buy-and-hold |
| Worst loss | Max drawdown no deeper than ___% (choose before running; SPY buy-and-hold was −33.7% over 2015–2026) |
| Consistency | Beats SPY buy-and-hold in at least ___ of the calendar years tested; report every year |
| Dependence on a few trades or one episode | Report the share of profit from the top 5 trades and the result with the single best year removed |

Also state what happens on pass (e.g. start a paper account alongside the current one) and on fail.

## Known biases

Data gaps, survivorship, anything that makes the result look better or worse than reality.

---

## Results (date)

Tables, verdict against each criterion, and what was learned.
