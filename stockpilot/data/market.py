"""Daily OHLCV download with a local CSV cache."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def _download(symbol: str, start: str, end: str | None) -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(
        symbol, start=start, end=end, auto_adjust=True, progress=False, threads=False
    )
    if df is None or df.empty:
        raise RuntimeError(f"No data returned for {symbol}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def clean(df: pd.DataFrame, symbol: str = "") -> pd.DataFrame:
    """Normalize columns/index and drop bad rows."""
    df = df[COLUMNS].copy()
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    bad = (df["High"] < df["Low"]) | (df["Close"] <= 0) | (df["Open"] <= 0)
    if bad.any():
        log.warning("%s: dropping %d malformed rows", symbol, int(bad.sum()))
        df = df[~bad]
    return df


def load_prices(
    symbols: list[str],
    start: str,
    end: str | None = None,
    cache_dir: str | Path = "data/cache",
    refresh: bool = False,
) -> dict[str, pd.DataFrame]:
    """Return {symbol: OHLCV DataFrame}. Cached files are reused unless refresh=True."""
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        path = cache / f"{sym}.csv"
        if path.exists() and not refresh:
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            if df.index.min() <= pd.Timestamp(start) + pd.Timedelta(days=7):
                out[sym] = clean(df, sym)
                continue
        try:
            df = clean(_download(sym, start, end), sym)
        except Exception as exc:  # network errors, delisted tickers
            log.warning("Skipping %s: %s", sym, exc)
            continue
        df.to_csv(path)
        out[sym] = df
        log.info("Downloaded %s: %d rows", sym, len(df))
    return out
