"""Point-in-time S&P 500 membership from Wikipedia page history.

Wikipedia keeps every revision of "List of S&P 500 companies". Taking the
revision that was live on the first day of each month gives the members as
known at that time, without reconstructing them from a list of changes.

Tickers are normalized for Yahoo ("BRK.B" -> "BRK-B"). When an old snapshot
lists a company under a ticker it later changed (e.g. FB -> META), the SEC CIK
number, which never changes, maps it to the current ticker so its full price
history can be downloaded under one symbol.
"""

from __future__ import annotations

import io
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

API = "https://en.wikipedia.org/w/api.php"
PAGE = "List of S&P 500 companies"
UA = {"User-Agent": "stockpilot-research/0.1 (personal backtesting project)"}


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi  # up-to-date CA bundle; some Windows stores carry expired roots

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _get(params: dict, tries: int = 6) -> dict:
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json", "formatversion": 2})
    req = urllib.request.Request(url, headers=UA)
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=60, context=_ssl_context()) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 503) or attempt == tries - 1:
                raise
            wait = float(exc.headers.get("Retry-After") or 0) or 5 * 2 ** attempt
            print(f"  rate limited, waiting {wait:.0f}s", flush=True)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def revision_at(when: pd.Timestamp) -> int:
    """ID of the page revision that was live at `when` (UTC)."""
    data = _get({"action": "query", "prop": "revisions", "titles": PAGE, "rvlimit": 1,
                 "rvdir": "older", "rvstart": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "rvprop": "ids"})
    return data["query"]["pages"][0]["revisions"][0]["revid"]


def members_in_revision(revid: int) -> pd.DataFrame:
    """Symbol (and CIK when the table has it) from the constituents table."""
    html = _get({"action": "parse", "oldid": revid, "prop": "text"})["parse"]["text"]
    for t in pd.read_html(io.StringIO(html)):
        cols = {str(c).strip().lower(): c for c in t.columns}
        sym_col = next((cols[k] for k in ("symbol", "ticker symbol", "ticker") if k in cols), None)
        if sym_col is None or len(t) < 400:
            continue
        out = pd.DataFrame({"symbol": t[sym_col].astype(str).str.strip()})
        out["cik"] = pd.to_numeric(t[cols["cik"]], errors="coerce").astype("Int64") if "cik" in cols else pd.NA
        return out
    raise ValueError(f"No constituents table in revision {revid}")


def yahoo_symbol(sym: str) -> str:
    return sym.replace(".", "-").upper()


def build_history(start: str, end: str | None, out_csv: Path, pause: float = 3.0) -> pd.DataFrame:
    """Monthly membership snapshots -> CSV with columns date, symbol, raw_symbol, cik.

    Resumable: raw snapshots are kept in <out_csv>.raw.csv and months already
    there are not fetched again.
    """
    raw_path = out_csv.with_suffix(".raw.csv")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw = pd.read_csv(raw_path, parse_dates=["date"]) if raw_path.exists() else pd.DataFrame()
    done = set(raw["date"]) if not raw.empty else set()
    months = pd.date_range(pd.Timestamp(start).replace(day=1), pd.Timestamp(end or pd.Timestamp.now()), freq="MS")
    for m in months:
        if m in done:
            continue
        rev = revision_at(m)
        df = members_in_revision(rev).rename(columns={"symbol": "raw_symbol"})
        df.insert(0, "date", m)
        raw = pd.concat([raw, df], ignore_index=True)
        raw.to_csv(raw_path, index=False)  # save progress after every month
        print(f"{m.date()}  revision {rev}  {len(df)} members", flush=True)
        time.sleep(pause)  # be polite to Wikipedia
    hist = raw.copy()
    hist["date"] = pd.to_datetime(hist["date"]).dt.date
    hist["cik"] = pd.to_numeric(hist["cik"], errors="coerce").astype("Int64")

    # Map every CIK to the ticker it uses in the most recent snapshot that has it.
    with_cik = hist.dropna(subset=["cik"]).sort_values("date")
    latest_by_cik = with_cik.groupby("cik")["raw_symbol"].last()
    hist["symbol"] = hist["cik"].map(latest_by_cik).fillna(hist["raw_symbol"]).map(yahoo_symbol)
    hist = hist[["date", "symbol", "raw_symbol", "cik"]]
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    hist.to_csv(out_csv, index=False)
    return hist


def load_history(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"])
    return df
