#!/usr/bin/env python3
"""
16_tijori_sectors.py — Scrape Tijori sector pages for all 23 sectors.

Each sector page (NO trailing slash) contains:
  - Company constituent table (name, PE, market cap)
  - Sector/segment area-charts: time series of KPIs (premiums, volumes, etc.)
  - Market-share charts (ms-chart): share by company over time
  - Company OPM charts (company-opm): profitability by company

API endpoints discovered in page HTML:
  /api/v1/sector/area-chart/{id}/  → {metric_name: [[ts_ms, val], ...]}
  /api/v1/sector/ms-chart/{id}/    → [{company_id, short_name, data: [[ts_ms, val]]}]
  /api/v1/sector/company-opm/{id}/ → [{company_id, short_name, data: [[ts_ms, val]]}]

Output: data/tijori_market/sectors/{sector-slug}.json

Usage:
  python 16_tijori_sectors.py               # all 23 sectors
  python 16_tijori_sectors.py --slug banking  # single sector
  python 16_tijori_sectors.py --force         # re-download even if cached
"""
import argparse, os, re, json, logging, time
from pathlib import Path
from dotenv import load_dotenv
from curl_cffi import requests as cffi
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent.parent
OUT_DIR = ROOT / "data" / "tijori_market" / "sectors"
OUT_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(ROOT / ".env")
SID = os.getenv("TIJORI_SESSION_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("sectors")

BASE = "https://www.tijorifinance.com"

# All 23 sector slugs from sitemap (no trailing slash - required!)
SECTORS = [
    "general-insurance", "textiles", "telecom", "nbfc", "steel",
    "real-estate", "sugar", "rating-agency", "fertilizer", "power",
    "pharma", "oil-refining", "life-insurance", "hospitals", "coal",
    "cement", "banking", "aviation", "aquaculture", "asset-management",
    "hotels", "automobile", "chemicals",
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


def parse_sector_page(html, slug):
    """Extract chart IDs, metric names, and company table from sector page HTML."""
    soup = BeautifulSoup(html, "html.parser")
    result = {
        "slug": slug,
        "title": "",
        "constituents": [],
        "area_charts": [],   # [{chart_id, metric_name, group, parent_id}]
        "ms_chart_ids": [],
        "company_opm_ids": [],
    }

    # Title
    h1 = soup.find("h1")
    if h1:
        result["title"] = h1.get_text(strip=True)

    # Company constituents table
    table = soup.find("table")
    if table:
        rows = table.find_all("tr")
        headers = [th.get_text(strip=True) for th in rows[0].find_all(["th", "td"])] if rows else []
        for row in rows[1:]:
            cells = row.find_all("td")
            if not cells:
                continue
            # First cell = company name + link
            link = cells[0].find("a", href=True)
            slug_m = re.search(r"/company/([a-z0-9-]+)", link["href"]) if link else None
            name = cells[0].get_text(strip=True)
            nums = [_parse_num(c.get_text(strip=True)) for c in cells[1:]]
            result["constituents"].append({
                "name": name,
                "company_slug": slug_m.group(1) if slug_m else None,
                "pe": nums[0] if len(nums) > 0 else None,
                "market_cap_cr": nums[1] if len(nums) > 1 else None,
            })

    # Metric list items (area-chart IDs with names)
    for li in soup.find_all("li", onclick=True):
        onclick = li.get("onclick", "")
        if "area-chart" not in onclick:
            continue
        cid_m = re.search(r"area-chart/(\d+)/", onclick)
        if not cid_m:
            continue
        name_span = li.find(class_="metric__list__name")
        name = name_span.get_text(strip=True) if name_span else li.get_text(strip=True)[:80]
        classes = li.get("class") or []
        group = next((c for c in classes if c in ("Segment", "Sector")), "")
        indent = next((c for c in classes if "indent" in c), "")
        parent = li.get("parent", "")
        result["area_charts"].append({
            "chart_id": int(cid_m.group(1)),
            "metric_name": name,
            "group": group,
            "indent": indent,
            "parent_id": parent,
        })

    # ms-chart IDs
    result["ms_chart_ids"] = [int(x) for x in re.findall(r"/api/v1/sector/ms-chart/(\d+)/", html)]

    # company-opm IDs
    result["company_opm_ids"] = [int(x) for x in re.findall(r"/api/v1/sector/company-opm/(\d+)/", html)]

    return result


def fetch_chart(session, endpoint, delay=0.2):
    """Fetch a chart API endpoint. Returns parsed JSON or None."""
    url = f"{BASE}{endpoint}"
    try:
        r = session.get(url, timeout=20)
        if r.status_code == 200:
            return r.json()
        log.debug(f"{endpoint}: HTTP {r.status_code}")
    except Exception as e:
        log.debug(f"{endpoint}: {e}")
    finally:
        time.sleep(delay)
    return None


def scrape_sector(session, slug, delay=0.2):
    """Scrape one sector. Returns dict with all data."""
    url = f"{BASE}/sector/{slug}"  # NO trailing slash
    try:
        r = session.get(url, timeout=40)
    except Exception as e:
        log.error(f"{slug}: request failed: {e}")
        return None
    if r.status_code != 200:
        log.warning(f"{slug}: HTTP {r.status_code}")
        return None

    meta = parse_sector_page(r.text, slug)
    log.info(f"{slug}: {len(meta['area_charts'])} area-charts, "
             f"{len(meta['ms_chart_ids'])} ms-charts, "
             f"{len(meta['company_opm_ids'])} opm-charts, "
             f"{len(meta['constituents'])} companies")
    time.sleep(delay)

    # Fetch all area-chart APIs
    area_data = {}
    for chart in meta["area_charts"]:
        cid = chart["chart_id"]
        data = fetch_chart(session, f"/api/v1/sector/area-chart/{cid}/", delay)
        area_data[str(cid)] = data

    # Fetch ms-chart APIs
    ms_data = {}
    for cid in meta["ms_chart_ids"]:
        data = fetch_chart(session, f"/api/v1/sector/ms-chart/{cid}/", delay)
        ms_data[str(cid)] = data

    # Fetch company-opm APIs
    opm_data = {}
    for cid in meta["company_opm_ids"]:
        data = fetch_chart(session, f"/api/v1/sector/company-opm/{cid}/", delay)
        opm_data[str(cid)] = data

    return {
        "slug": slug,
        "title": meta["title"],
        "constituents": meta["constituents"],
        "area_charts": [
            {**chart, "data": area_data.get(str(chart["chart_id"]))}
            for chart in meta["area_charts"]
        ],
        "ms_charts": [
            {"chart_id": cid, "data": ms_data.get(str(cid))}
            for cid in meta["ms_chart_ids"]
        ],
        "company_opm": [
            {"chart_id": cid, "data": opm_data.get(str(cid))}
            for cid in meta["company_opm_ids"]
        ],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slug", help="Single sector slug to scrape")
    ap.add_argument("--force", action="store_true", help="Re-download even if cached")
    ap.add_argument("--delay", type=float, default=0.2)
    args = ap.parse_args()

    if not SID:
        print("TIJORI_SESSION_ID not set in .env")
        return

    session = make_session()

    slugs = [args.slug] if args.slug else SECTORS

    done = skipped = errors = 0
    for slug in slugs:
        out_file = OUT_DIR / f"{slug}.json"
        if out_file.exists() and not args.force:
            log.info(f"{slug}: already cached, skipping")
            skipped += 1
            continue

        log.info(f"Scraping: {slug}")
        data = scrape_sector(session, slug, args.delay)
        if data:
            out_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            done += 1
        else:
            errors += 1
        time.sleep(args.delay)

    log.info(f"Done: {done} scraped, {skipped} skipped, {errors} errors")
    log.info(f"Output: {OUT_DIR}")


if __name__ == "__main__":
    main()
