import pandas as pd
import pytest

from stockpilot.backtest.engine import Costs
from stockpilot.backtest.timing import ma_timing
from stockpilot.data.synthetic import make_ohlcv

NO_COST = Costs(slippage_bps=0.0, minimum=0.0, per_share=0.0)


def bars(closes, opens=None, start="2024-01-01"):
    idx = pd.bdate_range(start, periods=len(closes))
    opens = opens or closes
    return pd.DataFrame({"Open": opens, "High": [max(o, c) for o, c in zip(opens, closes)],
                         "Low": [min(o, c) for o, c in zip(opens, closes)],
                         "Close": closes, "Volume": 1e6}, index=idx)


def test_buys_at_next_open_after_crossing_above_ma():
    # MA(3): the close on day 3 (13) is first above the prior average -> buy at day 4's open.
    df = bars([10, 10, 10, 13, 14, 15], opens=[10, 10, 10, 12, 20, 15])
    r = ma_timing(df, 1000.0, ma_days=3, costs=NO_COST)
    assert not r.in_market.iloc[3] and r.in_market.iloc[4]
    # Bought at 20 (day 4 open), so day 4 equity = 1000 / 20 * 14 = 700.
    assert r.equity.iloc[4] == pytest.approx(700.0)


def test_sells_at_next_open_after_crossing_below_ma():
    df = bars([10, 10, 10, 13, 14, 15, 9, 9], opens=[10, 10, 10, 12, 13, 15, 15, 8])
    r = ma_timing(df, 1000.0, ma_days=3, costs=NO_COST)
    assert r.in_market.iloc[5] and r.in_market.iloc[6]  # close below MA on day 6...
    assert not r.in_market.iloc[7]  # ...sold at day 7's open
    assert r.switches == 2


def test_no_lookahead():
    df = make_ohlcv(800, seed=4)
    cut = df.index[600]
    base = ma_timing(df, 3000.0)
    alt_df = df.copy()
    alt_df.loc[alt_df.index >= cut, ["Open", "High", "Low", "Close"]] *= 0.5
    alt = ma_timing(alt_df, 3000.0)
    before = base.equity.index < cut
    pd.testing.assert_series_equal(base.equity[before], alt.equity[before])
