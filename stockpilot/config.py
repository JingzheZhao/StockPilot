"""Universe and default settings."""

BENCHMARK = "SPY"

# Universe "curated": large, liquid US stocks across sectors plus a few ETFs.
# Note: this list is chosen with today's knowledge, so backtests carry
# survivorship bias (see README).
CURATED = [
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
UNIVERSE = CURATED  # used by paper trading

# Universe "etf": the 11 SPDR sector funds plus broad US index funds. No
# survivorship bias: none of these closed during the test window. XLRE (2015)
# and XLC (2018) simply become tradable once they have enough history.
SECTOR_ETFS = [
    "XLK", "XLF", "XLV", "XLE", "XLY", "XLP", "XLI", "XLB", "XLU", "XLRE", "XLC",
    "QQQ", "IWM", "DIA",
]

# Universe "sp500": each month, the TOP_N most traded (by 60-day average
# dollar volume) among stocks that were in the S&P 500 at that time.
SP500_TOP_N = 50
SP500_HISTORY = "data/universe/sp500_monthly.csv"

# Backtest periods. Rules were not tuned on any period, but we still
# report the most recent years separately as an out-of-sample check.
PERIODS = {
    "in_sample": ("2015-01-01", "2021-12-31"),
    "out_of_sample": ("2022-01-01", None),  # None = latest available
    "full": ("2015-01-01", None),
}

# Indicators need ~200 trading days of history before the first trade.
WARMUP_DAYS = 400
