"""Download monthly point-in-time S&P 500 membership from Wikipedia history.

    python scripts/build_sp500_history.py          # 2014-01 to now, ~2-3 minutes

Writes data/universe/sp500_monthly.csv (committed, so backtests are reproducible).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stockpilot.data.sp500 import build_history  # noqa: E402

if __name__ == "__main__":
    hist = build_history("2014-01-01", None, ROOT / "data" / "universe" / "sp500_monthly.csv")
    print(f"\n{hist['date'].nunique()} months, {hist['symbol'].nunique()} distinct symbols")
