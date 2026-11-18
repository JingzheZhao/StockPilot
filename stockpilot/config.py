"""Universe and default settings."""

BENCHMARK = "SPY"

# Large, liquid US stocks across sectors plus a few ETFs.
# Note: this list is chosen with today's knowledge, so backtests carry
# survivorship bias (see README).
UNIVERSE = [
    # Tech / communication
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "AMD", "ORCL",
    "ADBE", "CRM", "NFLX", "INTC", "CSCO",
    # Consumer
    "COST", "WMT", "HD", "PG", "KO", "MCD", "NKE",
    # Financials
    "JPM", "V", "MA", "BAC",
    # Healthcare
    "UNH", "LLY", "JNJ", "PFE",
    # Energy / industrials
    "XOM", "CAT", "BA",
    # ETFs
    "QQQ", "XLK", "XLF", "XLV", "XLE",
]

# Backtest periods. Rules were not tuned on any period, but we still
# report the most recent years separately as an out-of-sample check.
PERIODS = {
    "in_sample": ("2015-01-01", "2021-12-31"),
    "out_of_sample": ("2022-01-01", None),  # None = latest available
    "full": ("2015-01-01", None),
}

# Indicators need ~200 trading days of history before the first trade.
WARMUP_DAYS = 400
