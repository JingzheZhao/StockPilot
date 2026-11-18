"""NYSE trading calendar helpers (holidays and early closes included)."""

from __future__ import annotations

import pandas as pd

NY = "America/New_York"
# Yahoo's daily bar is often incomplete for hours after the close, so a
# session counts as "completed" only this long after its official close.
DATA_DELAY = pd.Timedelta(hours=4)


def _calendar():
    import exchange_calendars as xc

    return xc.get_calendar("XNYS")


def _naive(ts) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    return ts.tz_localize(None) if ts.tzinfo else ts


def last_completed_session(now=None) -> pd.Timestamp:
    """Latest session whose close plus DATA_DELAY is at or before `now`.

    `now` may be naive (taken as New York time) or time-zone aware.
    """
    now = pd.Timestamp.now(tz=NY) if now is None else pd.Timestamp(now)
    if now.tzinfo is None:
        now = now.tz_localize(NY)
    cal = _calendar()
    today = _naive(now.tz_convert(NY).normalize())
    for s in reversed(cal.sessions_in_range(today - pd.Timedelta(days=14), today)):
        if cal.session_close(s) + DATA_DELAY <= now:
            return _naive(s)
    raise RuntimeError("No completed session in the last two weeks")


def next_session(day) -> pd.Timestamp:
    return _naive(_calendar().next_session(_naive(day).normalize()))


def sessions_between(after, through) -> list[pd.Timestamp]:
    """Sessions strictly after `after`, up to and including `through`."""
    after, through = _naive(after).normalize(), _naive(through).normalize()
    if through <= after:
        return []
    return [_naive(s) for s in _calendar().sessions_in_range(after, through) if _naive(s) > after]
