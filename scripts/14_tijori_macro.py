#!/usr/bin/env python3
"""
14_tijori_macro.py — Scrape Tijori macro indicators with 5-year history.

Fetches https://www.tijorifinance.com/in/macro and extracts:
  - 3 tables (Industry, Demand, GDP & Trade): current monthly/quarterly values
  - For each indicator: full historical time series via /api/v1/macro/chart/{id}/

Output: data/tijori_market/macro.json
  {
    "industry": [{name, chart_id, current: {date: value}, history: [[ts_ms, val], ...]}, ...],
    "demand": [...],
    "gdp_trade": [...],
  }

Usage:
  python 14_tijori_macro.py               # full scrape
  python 14_tijori_macro.py --no-history  # table values only (no chart API calls)
"""
import argparse, os, re, json, logging, time
from pathlib import Path
from dotenv import load_dotenv
from curl_cffi import requests as cffi
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent.parent
OUT_DIR = ROOT / "data" / "tijori_market"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_FILE = OUT_DIR / "macro.json"

load_dotenv(ROOT / ".env")
SID = os.getenv("TIJORI_SESSION_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("macro")

BASE = "https://www.tijorifinance.com"

TABLE_SECTIONS = [
    ("macro__Industry", "industry"),
    ("macro__Demand", "demand"),
    ("macro__GDP", "gdp_trade"),
]


def make_session():
    s = cffi.Session(impersonate="chrome")
    s.cookies.set("sessionid", SID, domain=".tijorifinance.com")
    s.headers.update({"Referer": BASE + "/"})
    return s


def _parse_num(text):
    t = text.strip().replace(",", "")
    if not t or t in ("—", "-", ""):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def parse_macro_table(table):
    """Parse a macro table. Returns (headers, list_of_indicator_dicts)."""
    rows = table.find_all("tr")
    if not rows:
        return [], []

    # Header: first row with th elements
    header_row = rows[0]
    headers = [th.get_text(strip=True) for th in header_row.find_all(["th", "td"])]
    date_cols = headers[2:]  # skip empty first col and icon col

    indicators = []
    for row in rows[1:]:
        cells = row.find_all("td")
        if not cells:
            continue
        name = cells[0].get_text(strip=True)
        if not name:
            continue

        # Chart IDs embedded in onclick or data attributes
        row_html = str(row)
        chart_ids = re.findall(r"/api/v1/macro/chart/(\d+)/", row_html)

        # Current period values (columns 2 onwards)
        current = {}
        for i, date_label in enumerate(date_cols):
            cell_idx = i + 2
            if cell_idx < len(cells):
                val = _parse_num(cells[cell_idx].get_text(strip=True))
                if val is not None:
                    current[date_label] = val

        indicators.append({
            "name": name,
            "chart_ids": [int(c) for c in chart_ids],
            "current": current,
            "history": None,  # filled later
        })

    return date_cols, indicators


def fetch_chart_history(session, chart_id, delay=0.2):
    """Fetch full time series for a chart ID. Returns [[ts_ms, value], ...]."""
    url = f"{BASE}/api/v1/macro/chart/{chart_id}/"
    try:
        r = session.get(url, timeout=20)
        if r.status_code == 200:
            return r.json()
        log.warning(f"Chart {chart_id}: HTTP {r.status_code}")
    except Exception as e:
        log.warning(f"Chart {chart_id}: {e}")
    return None


def scrape(session, fetch_history=True, delay=0.15):
    log.info("Fetching macro page ...")
    r = session.get(f"{BASE}/in/macro", timeout=40)
    if r.status_code != 200:
        log.error(f"HTTP {r.status_code}")
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    result = {}

    for table_id, section_key in TABLE_SECTIONS:
        container = soup.find(id=table_id)
        if not container:
            # Try finding table directly if no wrapper ID
            tables = soup.find_all("table")
            idx = list(TABLE_SECTIONS).index((table_id, section_key))
            container = tables[idx] if idx < len(tables) else None
            if not container:
                log.warning(f"Section {table_id} not found")
                continue
            table = container
        else:
            table = container.find("table") or container

        date_cols, indicators = parse_macro_table(table)
        result[section_key] = indicators
        log.info(f"{section_key}: {len(indicators)} indicators, dates: {date_cols[:5]}")

    if not fetch_history:
        return result

    # Fetch historical data for all unique chart IDs
    all_ids = {}
    for section_key, indicators in result.items():
        for ind in indicators:
            for cid in ind["chart_ids"]:
                all_ids[cid] = all_ids.get(cid, [])
                all_ids[cid].append((section_key, ind["name"]))

    log.info(f"Fetching history for {len(all_ids)} unique chart IDs ...")
    chart_cache = {}
    for i, cid in enumerate(sorted(all_ids.keys())):
        history = fetch_chart_history(session, cid, delay)
        chart_cache[cid] = history
        if (i + 1) % 20 == 0:
            log.info(f"  {i+1}/{len(all_ids)} charts fetched")
        time.sleep(delay)

    # Attach history to indicators
    for section_key, indicators in result.items():
        for ind in indicators:
            if ind["chart_ids"]:
                # Primary chart ID is first one
                primary_cid = ind["chart_ids"][0]
                ind["history"] = chart_cache.get(primary_cid)
                # Additional chart IDs (sub-series)
                if len(ind["chart_ids"]) > 1:
                    ind["extra_history"] = {
                        str(cid): chart_cache.get(cid)
                        for cid in ind["chart_ids"][1:]
                    }

    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-history", action="store_true",
                    help="Skip fetching historical chart data (table values only)")
    ap.add_argument("--delay", type=float, default=0.15,
                    help="Delay between chart API calls (seconds)")
    args = ap.parse_args()

    if not SID:
        print("TIJORI_SESSION_ID not set in .env")
        return

    session = make_session()
    data = scrape(session, fetch_history=not args.no_history, delay=args.delay)

    if data:
        total = sum(len(v) for v in data.values())
        log.info(f"Total indicators: {total}")
        for k, v in data.items():
            has_hist = sum(1 for ind in v if ind.get("history"))
            log.info(f"  {k}: {len(v)} indicators, {has_hist} with history")

        OUT_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info(f"Saved to {OUT_FILE}")

        # Sample
        if data.get("industry"):
            s = data["industry"][0]
            hist_len = len(s["history"]) if s.get("history") else 0
            log.info(f"Sample: {s['name']} | current={list(s['current'].items())[:2]} | "
                     f"history_points={hist_len}")
    else:
        log.error("No data extracted")


if __name__ == "__main__":
    main()
