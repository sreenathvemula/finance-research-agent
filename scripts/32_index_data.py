#!/usr/bin/env python3
"""
32_index_data.py — NSE/BSE index levels + valuation (P/E, P/B, Div Yield) over time.

Three sources:
  * niftyindices.com AJAX  -> historical P/E / P/B / Div-Yield per index (the gold
    for relative valuation: a sector's valuation band over time).
  * NSE ind_close_all daily -> a one-file snapshot of ALL ~148 indices' OHLC+PE+PB+DY.
  * yfinance               -> daily index LEVEL history for the major tickers.

Outputs (data/reference/):
  index_snapshot.csv               latest all-index OHLC + P/E/P/B/DY
  index_valuation/{slug}.csv       date, pe, pb, divYield  (history per index)
  indices/{slug}.parquet           date, OHLC level history (yfinance)
Usage:
  python 32_index_data.py --snapshot         # just the latest all-index snapshot
  python 32_index_data.py --all              # snapshot + P/E history + levels
"""
import argparse, datetime as dt, io, json, logging, time
from pathlib import Path

import pandas as pd
from curl_cffi import requests as cffi

ROOT = Path(__file__).parent.parent
REF  = ROOT / "data" / "reference"
(REF / "index_valuation").mkdir(parents=True, exist_ok=True)
(REF / "indices").mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("index")

# (slug, niftyindices name, yfinance ticker or None)
INDICES = [
    ("nifty50", "NIFTY 50", "^NSEI"), ("niftynext50", "NIFTY NEXT 50", "^NSMIDCP"),
    ("nifty500", "NIFTY 500", "^CRSLDX"), ("niftymidcap100", "NIFTY MIDCAP 100", None),
    ("niftysmallcap100", "NIFTY SMALLCAP 100", None), ("sensex", "NIFTY 50", "^BSESN"),
    ("bank", "NIFTY BANK", "^NSEBANK"), ("it", "NIFTY IT", "^CNXIT"),
    ("pharma", "NIFTY PHARMA", "^CNXPHARMA"), ("fmcg", "NIFTY FMCG", "^CNXFMCG"),
    ("auto", "NIFTY AUTO", "^CNXAUTO"), ("metal", "NIFTY METAL", "^CNXMETAL"),
    ("realty", "NIFTY REALTY", "^CNXREALTY"), ("energy", "NIFTY ENERGY", "^CNXENERGY"),
    ("finservice", "NIFTY FINANCIAL SERVICES", None), ("media", "NIFTY MEDIA", "^CNXMEDIA"),
    ("psubank", "NIFTY PSU BANK", None), ("pvtbank", "NIFTY PVT BANK", None),
    ("condurables", "NIFTY CONSUMER DURABLES", None), ("oilgas", "NIFTY OIL & GAS", None),
    ("healthcare", "NIFTY HEALTHCARE INDEX", None), ("infra", "NIFTY INFRASTRUCTURE", None),
    ("commodities", "NIFTY COMMODITIES", None),
]


def ni_session():
    s = cffi.Session(impersonate="chrome")
    for _ in range(3):
        try:
            s.get("https://www.niftyindices.com", timeout=15); break
        except Exception:
            time.sleep(2)
    return s


def snapshot():
    s = cffi.Session(impersonate="chrome"); s.get("https://www.nseindia.com", timeout=30)
    for back in range(0, 8):
        d = dt.date.today() - dt.timedelta(days=back)
        fn = f"ind_close_all_{d.strftime('%d%m%Y')}.csv"
        r = s.get("https://nsearchives.nseindia.com/content/indices/" + fn, timeout=30)
        if r.status_code == 200 and "Index" in r.text[:100]:
            (REF / "index_snapshot.csv").write_text(r.text, encoding="utf-8")
            log.info(f"snapshot {fn}: {len(r.text.splitlines())-1} indices -> index_snapshot.csv")
            return
    log.warning("no recent ind_close_all snapshot found")


def pe_history(s, name, start_year=2011):
    hdr = {"Content-Type": "application/json; charset=UTF-8",
           "Referer": "https://www.niftyindices.com/reports/historical-data",
           "X-Requested-With": "XMLHttpRequest"}
    rows = []
    this_year = dt.date.today().year
    for yr in range(start_year, this_year + 1):
        body = {"cinfo": json.dumps({"name": name, "indexName": name,
                                     "startDate": f"01-Jan-{yr}", "endDate": f"31-Dec-{yr}"})}
        for attempt in range(3):
            try:
                r = s.post("https://www.niftyindices.com/Backpage.aspx/getpepbHistoricaldataDBtoString",
                           json=body, headers=hdr, timeout=40)
                data = json.loads(r.json()["d"])
                for it in data:
                    rows.append({"date": it.get("HistoricalDate") or it.get("Date"),
                                 "pe": it.get("pe"), "pb": it.get("pb"), "divYield": it.get("divYield")})
                break
            except Exception:
                time.sleep(3)
        time.sleep(0.2)
    return pd.DataFrame(rows)


def run(do_all):
    snapshot()
    if not do_all:
        return
    s = ni_session()
    for slug, name, yf_t in INDICES:
        vp = REF / "index_valuation" / f"{slug}.csv"
        if not vp.exists():
            df = pe_history(s, name)
            if len(df):
                df.to_csv(vp, index=False, encoding="utf-8")
            log.info(f"  P/E history {slug} ({name}): {len(df)} days")
        if yf_t:
            lp = REF / "indices" / f"{slug}.parquet"
            if not lp.exists():
                try:
                    import yfinance as yf
                    h = yf.Ticker(yf_t).history(period="max", auto_adjust=False)
                    if len(h):
                        h.reset_index()[["Date", "Open", "High", "Low", "Close", "Volume"]].to_parquet(lp, index=False)
                    log.info(f"  levels {slug} ({yf_t}): {len(h)} days")
                except Exception as e:
                    log.warning(f"  levels {slug} {yf_t}: {e}")
    log.info(f"-> {REF}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    run(a.all)


if __name__ == "__main__":
    main()
