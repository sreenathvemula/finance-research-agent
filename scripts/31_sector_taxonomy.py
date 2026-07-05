#!/usr/bin/env python3
"""
31_sector_taxonomy.py — Add NSE industry + sector-index membership to the entity
master, and (optionally) scrape Screener for full-universe industry.

NSE publishes per-index constituent CSVs (Company Name, Industry, Symbol, Series,
ISIN) at nsearchives. The broad 'Nifty Total Market' list (~750) carries the
granular Industry; the sector-index lists give which sector basket each stock
sits in (used to benchmark vs the right Nifty sector index).

Outputs (data/reference/):
  nse_industry.csv          symbol, industry  (from Nifty Total Market)
  index_membership.json     symbol -> [sector index names]
  index_universe.json       index name -> {file, symbols[]}
  entities.csv              re-written with an 'nse_industry' column merged in
Usage:
  python 31_sector_taxonomy.py            # NSE lists -> merge
  python 31_sector_taxonomy.py --screener # ALSO scrape screener industry (slow)
"""
import argparse, io, json, logging, re, time
from pathlib import Path

import pandas as pd
from curl_cffi import requests as cffi

ROOT = Path(__file__).parent.parent
REF  = ROOT / "data" / "reference"
COMP = ROOT / "data" / "companies"
REF.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("sector")

BASE = "https://nsearchives.nseindia.com/content/indices/"
BROAD = "ind_niftytotalmarket_list.csv"
SECTOR_FILES = {
    "Nifty Bank": "ind_niftybanklist.csv", "Nifty IT": "ind_niftyitlist.csv",
    "Nifty Pharma": "ind_niftypharmalist.csv", "Nifty FMCG": "ind_niftyfmcglist.csv",
    "Nifty Auto": "ind_niftyautolist.csv", "Nifty Metal": "ind_niftymetallist.csv",
    "Nifty Realty": "ind_niftyrealtylist.csv", "Nifty Energy": "ind_niftyenergylist.csv",
    "Nifty Financial Services": "ind_niftyfinancelist.csv", "Nifty Media": "ind_niftymedialist.csv",
    "Nifty PSU Bank": "ind_niftypsubanklist.csv", "Nifty Private Bank": "ind_nifty_privatebanklist.csv",
    "Nifty Consumer Durables": "ind_niftyconsumerdurableslist.csv",
    "Nifty Oil Gas": "ind_niftyoilgaslist.csv", "Nifty Healthcare": "ind_niftyhealthcarelist.csv",
    "Nifty Infrastructure": "ind_niftyinfralist.csv", "Nifty Commodities": "ind_niftycommoditieslist.csv",
}


def nse_session():
    s = cffi.Session(impersonate="chrome")
    s.get("https://www.nseindia.com", timeout=30)
    return s


def fetch_csv(s, fname):
    r = s.get(BASE + fname, timeout=30)
    if r.status_code != 200 or "Symbol" not in r.text[:200]:
        return None
    return pd.read_csv(io.StringIO(r.text))


def run_nse():
    s = nse_session()
    broad = fetch_csv(s, BROAD)
    industry = {}
    if broad is not None:
        for _, row in broad.iterrows():
            industry[str(row["Symbol"]).strip()] = str(row["Industry"]).strip()
        pd.DataFrame(sorted(industry.items()), columns=["symbol", "industry"]).to_csv(
            REF / "nse_industry.csv", index=False, encoding="utf-8")
        log.info(f"Nifty Total Market industries: {len(industry)}")

    membership, universe = {}, {}
    for name, fname in SECTOR_FILES.items():
        df = fetch_csv(s, fname)
        time.sleep(0.3)
        if df is None:
            log.warning(f"  missing {name} ({fname})"); continue
        syms = [str(x).strip() for x in df["Symbol"].tolist()]
        universe[name] = {"file": fname, "symbols": syms}
        for sym in syms:
            membership.setdefault(sym, []).append(name)
        # also harvest industry from sector lists (extends broad coverage)
        if "Industry" in df.columns:
            for _, row in df.iterrows():
                industry.setdefault(str(row["Symbol"]).strip(), str(row["Industry"]).strip())
        log.info(f"  {name}: {len(syms)}")
    (REF / "index_membership.json").write_text(json.dumps(membership, indent=1), encoding="utf-8")
    (REF / "index_universe.json").write_text(json.dumps(universe, indent=1), encoding="utf-8")

    # merge nse_industry into entities.csv
    ent_path = REF / "entities.csv"
    if ent_path.exists():
        ent = pd.read_csv(ent_path, dtype=str, keep_default_na=False)
        ent["nse_industry"] = ent["nse_symbol"].map(industry).fillna(
            ent["symbol"].map(industry)).fillna("")
        ent.to_csv(ent_path, index=False, encoding="utf-8")
        try: ent.to_parquet(REF / "entities.parquet", index=False)
        except Exception: pass
        log.info(f"entities.csv merged: {(ent['nse_industry']!='').sum()} have nse_industry")
    log.info(f"-> {REF}")


RX_IND = re.compile(r'Industry["\']?>([^<]+)</a>', re.I)         # industry link text
RX_MKT = re.compile(r'/market/(IN\d+)/(IN\d+)/')                 # macro/sector codes
# polite pacing (mirror 04_screener_scraper) + backoff ladder for timeouts/429
DELAY = 2.0
BACKOFF = [60, 120, 300, 600, 900]


def _screener_session():
    import os
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    s = cffi.Session(impersonate="chrome")
    s.headers.update({"Referer": "https://www.screener.in/", "Accept-Language": "en-US,en;q=0.9"})
    sid = os.getenv("SCREENER_SESSION_ID", "")
    if sid:
        s.cookies.set("sessionid", sid, domain="www.screener.in")
    return s


def _fetch_industry(s, sym):
    """Return {industry,macro,sector} or None. Backs off on timeout/429/503 so it
    rides out a rate-limit cooldown rather than hammering."""
    for attempt in range(len(BACKOFF) + 1):
        try:
            r = s.get(f"https://www.screener.in/company/{sym}/", timeout=30)
            if r.status_code == 404:
                return {}
            if r.status_code in (429, 502, 503):
                raise RuntimeError(f"http{r.status_code}")
            m = RX_IND.search(r.text)
            if m:
                mc = RX_MKT.search(r.text)
                return {"industry": m.group(1).strip(),
                        "macro": mc.group(1) if mc else "", "sector": mc.group(2) if mc else ""}
            return {}                       # 200 but no industry link (rare)
        except Exception:
            if attempt < len(BACKOFF):
                log.warning(f"  {sym}: throttled/timeout — backoff {BACKOFF[attempt]}s")
                time.sleep(BACKOFF[attempt])
            else:
                return None                 # give up this symbol (left un-cached -> retried next run)


def run_screener():
    """Per-company industry from screener.in — RESPECTFUL (auth + 2s + backoff), resumable."""
    out = REF / "sector_screener.json"
    have = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    syms = sorted(d.name for d in COMP.iterdir() if d.is_dir() and (d / "screener.json").exists())
    # retry symbols that previously yielded nothing, plus the un-scraped ones
    todo = [x for x in syms if x not in have or not have.get(x)]
    log.info(f"Screener industry scrape (respectful): {len(todo)} to do "
             f"({sum(1 for v in have.values() if v)} already have industry)")
    s = _screener_session()
    # wait out any active rate-limit cooldown before starting the loop
    for w in (0, 60, 120, 300, 600, 900, 1200):
        if w:
            log.info(f"screener not reachable — cooldown wait {w}s"); time.sleep(w)
        try:
            r = s.get("https://www.screener.in/company/RELIANCE/", timeout=30)
            if r.status_code == 200 and RX_IND.search(r.text):
                log.info("screener reachable — starting scrape"); break
        except Exception:
            pass
    for i, sym in enumerate(todo, 1):
        rec = _fetch_industry(s, sym)
        if rec is not None:
            have[sym] = rec
        time.sleep(DELAY)
        if i % 50 == 0:
            out.write_text(json.dumps(have, indent=1), encoding="utf-8")
            got = sum(1 for v in have.values() if v.get("industry"))
            log.info(f"  {i}/{len(todo)} — {got} industries")
    out.write_text(json.dumps(have, indent=1), encoding="utf-8")
    got = sum(1 for v in have.values() if v.get("industry"))
    log.info(f"screener industries: {got}/{len(have)} -> {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--screener", action="store_true")
    args = ap.parse_args()
    if args.screener:
        run_screener()
    else:
        run_nse()


if __name__ == "__main__":
    main()
