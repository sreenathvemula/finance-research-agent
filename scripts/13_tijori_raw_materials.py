#!/usr/bin/env python3
"""
13_tijori_raw_materials.py — Scrape Tijori raw materials page.

Fetches https://www.tijorifinance.com/in/raw-materials and extracts:
- Chemicals (70+ commodities): name, family, LTP vs 52W high, 1W/1M/3M/6M/1YR % change
- Metals (~8 commodities): same fields
- Spreads (17 spreads): spread name, LTP vs 52W high, 1W/1M/3M/6M/1YR
- Producers: list of {slug, name} for each commodity
- Consumers: list of {slug, name} for each commodity

Output: data/tijori_market/raw_materials.json
"""
import os, re, json, logging, time
from pathlib import Path
from dotenv import load_dotenv
from curl_cffi import requests as cffi
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent.parent
OUT_DIR = ROOT / "data" / "tijori_market"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_FILE = OUT_DIR / "raw_materials.json"

load_dotenv(ROOT / ".env")
SID = os.getenv("TIJORI_SESSION_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("raw_materials")

BASE = "https://www.tijorifinance.com"


def make_session():
    s = cffi.Session(impersonate="chrome")
    s.cookies.set("sessionid", SID, domain=".tijorifinance.com")
    s.headers.update({"Referer": BASE + "/"})
    return s


def _parse_num(text):
    t = text.strip().replace(",", "")
    if not t or t == "—":
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _parse_companies(data_content_html):
    """Extract [{slug, name}] from the popover HTML stored in data-content attr."""
    soup = BeautifulSoup(data_content_html, "html.parser")
    result = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.search(r"/company/([a-z0-9-]+)", href)
        if m:
            result.append({"slug": m.group(1), "name": a.get_text(strip=True)})
    return result


def parse_commodity_table(table):
    """Parse a chemicals or metals table. Returns list of commodity dicts."""
    rows = table.find_all("tr")
    if not rows:
        return []

    # Header row: get column indices
    header_row = rows[0]
    headers = [th.get_text(strip=True) for th in header_row.find_all(["th", "td"])]

    commodities = []
    for row in rows[1:]:
        cells = row.find_all("td")
        if not cells:
            continue

        name = cells[0].get_text(strip=True)
        if not name:
            continue

        # Read numeric cells by position (after name col):
        # family, LTP vs 52W High, 1W, 1M, 3M, 6M, 1YR
        vals = [c.get_text(strip=True) for c in cells]
        family = vals[1] if len(vals) > 1 else None
        nums = [_parse_num(v) for v in vals[2:8]]  # up to 6 numeric cols

        # Producers and consumers: td.view__producers > div[data-content]
        producers = []
        consumers = []
        prod_td = row.find("td", class_=lambda c: c and "producer" in c.lower())
        cons_td = row.find("td", class_=lambda c: c and "consumer" in c.lower())
        if prod_td:
            div = prod_td.find("div", attrs={"data-content": True})
            if div:
                producers = _parse_companies(div["data-content"])
        if cons_td:
            div = cons_td.find("div", attrs={"data-content": True})
            if div:
                consumers = _parse_companies(div["data-content"])

        entry = {
            "name": name,
            "family": family,
            "ltp_vs_52w_high_pct": nums[0] if len(nums) > 0 else None,
            "chg_1w_pct": nums[1] if len(nums) > 1 else None,
            "chg_1m_pct": nums[2] if len(nums) > 2 else None,
            "chg_3m_pct": nums[3] if len(nums) > 3 else None,
            "chg_6m_pct": nums[4] if len(nums) > 4 else None,
            "chg_1yr_pct": nums[5] if len(nums) > 5 else None,
            "producers": producers,
            "consumers": consumers,
        }
        commodities.append(entry)

    return commodities


def parse_spreads_table(table):
    """Parse the spreads table. Returns list of spread dicts."""
    rows = table.find_all("tr")
    spreads = []
    for row in rows[1:]:
        cells = row.find_all("td")
        if not cells:
            continue
        name = cells[0].get_text(strip=True)
        if not name:
            continue
        vals = [c.get_text(strip=True) for c in cells]
        nums = [_parse_num(v) for v in vals[1:7]]
        spreads.append({
            "name": name,
            "ltp_vs_52w_high_pct": nums[0] if len(nums) > 0 else None,
            "chg_1w_pct": nums[1] if len(nums) > 1 else None,
            "chg_1m_pct": nums[2] if len(nums) > 2 else None,
            "chg_3m_pct": nums[3] if len(nums) > 3 else None,
            "chg_6m_pct": nums[4] if len(nums) > 4 else None,
            "chg_1yr_pct": nums[5] if len(nums) > 5 else None,
        })
    return spreads


def scrape(session):
    log.info("Fetching raw materials page ...")
    r = session.get(f"{BASE}/in/raw-materials", timeout=40)
    if r.status_code != 200:
        log.error(f"HTTP {r.status_code}")
        return None

    soup = BeautifulSoup(r.text, "html.parser")

    # Page has multiple sections: Chemicals, Spreads, Metals
    # Each section has a tab pane div containing a table
    # Identify by section header text or table id
    result = {
        "chemicals": [],
        "metals": [],
        "spreads": [],
    }

    # Find all tables with myid attributes (commodity rows)
    tables = soup.find_all("table")
    log.info(f"Found {len(tables)} tables")

    # Determine table types by content
    for table in tables:
        rows = table.find_all("tr")
        if not rows:
            continue

        # Check if it's a spreads table (no producers/consumers columns)
        has_producers = bool(table.find("td", class_=lambda c: c and "producer" in c.lower()))
        row_texts = [r.get_text(" ", strip=True)[:60] for r in rows[:3]]

        # Spreads table: first data row will have "spread" names like "ABS Resins - Acrylonitrile"
        first_row = rows[1].get_text(strip=True) if len(rows) > 1 else ""

        # Use the section tabs to identify: check parent element classes/ids
        parent = table.find_parent(id=True)
        parent_id = parent.get("id", "") if parent else ""

        if "spread" in parent_id.lower() or "Spread" in first_row:
            result["spreads"] = parse_spreads_table(table)
            log.info(f"Spreads: {len(result['spreads'])} rows")
        elif "metal" in parent_id.lower():
            result["metals"] = parse_commodity_table(table)
            log.info(f"Metals: {len(result['metals'])} rows")
        else:
            # Check row myid attributes to distinguish chemicals vs metals
            row_ids = [r.get("myid", "") for r in rows if r.get("myid")]
            if row_ids:
                # Metals have IDs like "MetCoke", "FerroChrome"
                # Chemicals have IDs like "IsopropylAlcohol", "Benzene"
                items = parse_commodity_table(table)
                if items:
                    if result["chemicals"]:
                        result["metals"] = items
                        log.info(f"Metals: {len(items)} rows")
                    else:
                        result["chemicals"] = items
                        log.info(f"Chemicals: {len(items)} rows")

    # Fallback: if section detection failed, parse by tab content divs
    if not result["chemicals"] and not result["metals"]:
        # Try by tab pane IDs
        for tab_id, key in [("chemicals", "chemicals"), ("metals", "metals"), ("spreads", "spreads")]:
            pane = soup.find(id=tab_id) or soup.find(id=tab_id.capitalize())
            if pane:
                t = pane.find("table")
                if t:
                    if key == "spreads":
                        result[key] = parse_spreads_table(t)
                    else:
                        result[key] = parse_commodity_table(t)
                    log.info(f"{key}: {len(result[key])} items")

    return result


def main():
    if not SID:
        print("TIJORI_SESSION_ID not set in .env")
        return

    session = make_session()
    data = scrape(session)

    if data:
        total = sum(len(v) for v in data.values())
        log.info(f"Total commodities/spreads: {total}")
        log.info(f"  chemicals: {len(data['chemicals'])}")
        log.info(f"  metals: {len(data['metals'])}")
        log.info(f"  spreads: {len(data['spreads'])}")

        OUT_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info(f"Saved to {OUT_FILE}")

        # Sample output
        if data["chemicals"]:
            s = data["chemicals"][0]
            log.info(f"Sample chemical: {s['name']} | {s['family']} | "
                     f"1YR={s['chg_1yr_pct']}% | producers={len(s['producers'])} | consumers={len(s['consumers'])}")
    else:
        log.error("No data extracted")


if __name__ == "__main__":
    main()
