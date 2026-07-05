#!/usr/bin/env python3
"""
15_tijori_market_monitor.py — Scrape Tijori market monitor page.

Fetches https://www.tijorifinance.com/in/markets and extracts:
  - Headline indices (Nifty 50, Next 50, Midcap 150, Smallcap 250, Microcap 250)
    with 1D/1W/1M/3M/6M/1YR/2YR/3YR/5YR returns
  - Niche sector indices (TJI Aviation, TJI Banks, etc.)
    with weight, no. of companies, LTP vs 52W high, returns
  - Conglomerates (Murugappa, Tata, Adani, etc.)
    with historical ROE by fiscal year

Output: data/tijori_market/market_monitor.json
"""
import os, re, json, logging
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from curl_cffi import requests as cffi
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent.parent
OUT_DIR = ROOT / "data" / "tijori_market"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_FILE = OUT_DIR / "market_monitor.json"

load_dotenv(ROOT / ".env")
SID = os.getenv("TIJORI_SESSION_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("market_monitor")

BASE = "https://www.tijorifinance.com"

RETURN_COLS = ["ltp_vs_52w_high_pct", "ret_1d", "ret_1w", "ret_1m", "ret_3m",
               "ret_6m", "ret_1yr", "ret_2yr", "ret_3yr", "ret_5yr"]


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


def parse_indices_table(table):
    """Parse headline indices table (Nifty 50, etc.)."""
    rows = table.find_all("tr")
    result = []
    for row in rows[1:]:
        cells = row.find_all("td")
        if not cells:
            continue
        name = cells[0].get_text(strip=True)
        if not name:
            continue
        vals = [_parse_num(c.get_text(strip=True)) for c in cells[1:]]
        entry = {"name": name}
        for i, col in enumerate(RETURN_COLS):
            entry[col] = vals[i] if i < len(vals) else None
        result.append(entry)
    return result


def parse_niche_table(table):
    """Parse niche sector indices table (TJI sector indices)."""
    rows = table.find_all("tr")
    result = []
    header = rows[0].get_text(strip=True) if rows else ""

    for row in rows[1:]:
        cells = row.find_all("td")
        if not cells:
            continue
        name = cells[0].get_text(strip=True).rstrip(" +").strip()
        if not name:
            continue

        # Cols: name | weight | num_companies | LTP vs 52W | 1D | 1W | 1M | 3M | 6M | 1YR | 2YR | 3YR | 5YR
        nums = [_parse_num(c.get_text(strip=True)) for c in cells[1:]]
        entry = {
            "name": name,
            "weight": nums[0] if len(nums) > 0 else None,
            "num_companies": int(nums[1]) if len(nums) > 1 and nums[1] is not None else None,
        }
        ret_nums = nums[2:]
        for i, col in enumerate(RETURN_COLS):
            entry[col] = ret_nums[i] if i < len(ret_nums) else None
        result.append(entry)
    return result


def parse_conglomerates_table(table):
    """Parse conglomerates ROE table."""
    rows = table.find_all("tr")
    if not rows:
        return []

    # Header contains fiscal year labels: WEIGHT, Mar25, Mar24, ...
    header_row = rows[0]
    headers = [th.get_text(strip=True) for th in header_row.find_all(["th", "td"])]
    year_cols = headers[1:]  # skip name col; first is WEIGHT but may be empty

    result = []
    for row in rows[1:]:
        cells = row.find_all("td")
        if not cells:
            continue
        name = cells[0].get_text(strip=True).rstrip(" +").strip()
        if not name:
            continue
        vals = {year_cols[i]: _parse_num(c.get_text(strip=True))
                for i, c in enumerate(cells[1:]) if i < len(year_cols)}
        result.append({"name": name, "roe_by_year": vals})
    return result


def scrape(session):
    log.info("Fetching market monitor page ...")
    r = session.get(f"{BASE}/in/markets", timeout=40)
    if r.status_code != 200:
        log.error(f"HTTP {r.status_code}")
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    tables = soup.find_all("table")
    log.info(f"Found {len(tables)} tables")

    result = {
        "scraped_at": datetime.utcnow().isoformat() + "Z",
        "headline_indices": [],
        "niche_sector_indices": [],
        "conglomerates": [],
    }

    if len(tables) >= 1:
        result["headline_indices"] = parse_indices_table(tables[0])
        log.info(f"Headline indices: {len(result['headline_indices'])}")

    if len(tables) >= 2:
        result["niche_sector_indices"] = parse_niche_table(tables[1])
        log.info(f"Niche sector indices: {len(result['niche_sector_indices'])}")

    if len(tables) >= 3:
        result["conglomerates"] = parse_conglomerates_table(tables[2])
        log.info(f"Conglomerates: {len(result['conglomerates'])}")

    return result


def main():
    if not SID:
        print("TIJORI_SESSION_ID not set in .env")
        return

    session = make_session()
    data = scrape(session)

    if data:
        OUT_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info(f"Saved to {OUT_FILE}")

        if data["headline_indices"]:
            s = data["headline_indices"][0]
            log.info(f"Sample: {s['name']} | 1YR={s['ret_1yr']}% | 5YR={s['ret_5yr']}%")
        if data["niche_sector_indices"]:
            s = data["niche_sector_indices"][0]
            log.info(f"Sample niche: {s['name']} | weight={s['weight']} | 1YR={s['ret_1yr']}%")
    else:
        log.error("No data extracted")


if __name__ == "__main__":
    main()
