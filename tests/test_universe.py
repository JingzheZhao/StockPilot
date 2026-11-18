import pandas as pd

from stockpilot.data.synthetic import make_ohlcv
from stockpilot.strategy import Breakout
from stockpilot.universe import coverage, top_liquid_eligibility


def three_stocks(n=200):
    data = {}
    for i, (sym, vol) in enumerate([("A", 3e6), ("B", 2e6), ("C", 1e6)]):
        df = make_ohlcv(n, start="2020-01-01", seed=i, drift=0.0, vol=0.001, start_price=100.0)
        df["Volume"] = vol
        data[sym] = df
    return data


def test_picks_most_traded_members_only():
    data = three_stocks()
    cal = data["A"].index
    members = {pd.Timestamp("2020-01-01"): {"B", "C"}}  # A is never a member
    elig = top_liquid_eligibility(data, members, cal, top_n=1, lookback=20)
    later = elig.loc["2020-04-01":]
    assert later["B"].all()  # most traded member
    assert not later["A"].any()  # more traded, but not in the index
    assert not later["C"].any()  # member, but outside the top 1


def test_month_ranking_ignores_data_from_inside_the_month():
    data = three_stocks()
    cal = data["A"].index
    members = {pd.Timestamp("2020-01-01"): {"A", "B", "C"}}
    base = top_liquid_eligibility(data, members, cal, top_n=1, lookback=20)
    # From mid-May on, C suddenly trades 100x more. May's choice was made from
    # April data, so May must not change; June should switch to C.
    bumped = {s: d.copy() for s, d in data.items()}
    bumped["C"].loc["2020-05-15":, "Volume"] = 3e8
    alt = top_liquid_eligibility(bumped, members, cal, top_n=1, lookback=20)
    may = slice("2020-05-01", "2020-05-31")
    pd.testing.assert_frame_equal(base.loc[may], alt.loc[may])
    assert alt.loc["2020-06-15":"2020-06-30", "C"].all()


def test_breakout_respects_eligible_column():
    df = make_ohlcv(400, seed=1, drift=0.002, vol=0.01)
    market = make_ohlcv(400, seed=2, drift=0.002, vol=0.01)
    free = Breakout().prepare(df, market)["entry"]
    assert free.any()
    blocked = Breakout().prepare(df.assign(Eligible=False), market)["entry"]
    assert not blocked.any()


def test_coverage_counts_member_months_with_data():
    members = {pd.Timestamp("2020-01-01"): {"A", "B"}, pd.Timestamp("2020-02-01"): {"A", "C"}}
    assert coverage(members, {"A"}, "2020-01-01") == 0.5
