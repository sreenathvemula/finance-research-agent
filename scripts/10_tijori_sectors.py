#!/usr/bin/env python3
"""
10_tijori_sectors.py — Tijori SECTOR-level data (Premium), incl. operational
metrics, segments, market share, and in-sector company comparisons.

Discovered via browser network capture of a logged-in Premium session:
  * Sector page  /sector/<slug>   (NO trailing slash; trailing slash 404s) is
    server-rendered with the metric catalog as <li onclick="plot_*('/api/v1/sector/
    <family>/<id>/', ...)"> + .metric__list__name + unit + category class.
  * Families (all GET, JSON served as text/html):
      - area-chart/<id>   -> {"<MetricName>": [[epoch_ms, val], ...]}   (Sector & Segment time series)
      - ms-chart/<id>     -> [{company_id, short_name, value}, ...]      (market share by company)
      - company-opm/<id>  -> [{company_id, short_name, value}, ...]      (op-metric across constituents)
Auth: TIJORI_SESSION_ID cookie (Premium). 23 sectors in sitemap.

Output: data/tijori_sectors/<slug>.json

Usage:
  python 10_tijori_sectors.py --slug oil-refining
  python 10_tijori_sectors.py --all
"""
import argparse, json, os, re, time, logging
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
from curl_cffi import requests as cffi
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent.parent
OUT = ROOT / "data" / "tijori_sectors"
OUT.mkdir(parents=True, exist_ok=True)
load_dotenv(ROOT / ".env")
SID = os.getenv("TIJORI_SESSION_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("tijori-sec")

BASE = "https://www.tijorifinance.com"
CAT_MAP = {"Sector": "sector_metrics", "Segment": "segments",
           "Marketshare": "market_share", "Companyopmetrics": "company_metrics"}


def make_session():
    s = cffi.Session(impersonate="chrome")
    s.cookies.set("sessionid", SID, domain=".tijorifinance.com")
    s.headers.update({"Referer": BASE + "/", "X-Requested-With": "XMLHttpRequest"})
    return s


def sector_slugs(session):
    sm = session.get(f"{BASE}/sitemap.xml", timeout=40).text
    return sorted(set(re.findall(r"<loc>(?:https?://[^<]*?)/sector/([a-z0-9-]+)/?</loc>", sm)))


def parse_catalog(html):
    soup = BeautifulSoup(html, "lxml")
    cat = []
    for li in soup.select("li[onclick]"):
        m = re.search(r"/api/v1/sector/([a-z\-]+)/(\d+)/", li.get("onclick", ""))
        if not m:
            continue
        nm = li.select_one(".metric__list__name")
        classes = [c for c in (li.get("class") or []) if c != "activesidemenu" and not c.startswith("indent")]
        category = next((classes[i] for i in (1, 0) if len(classes) > i and classes[i] in CAT_MAP), classes[-1] if classes else "?")
        cat.append({"family": m.group(1), "id": m.group(2),
                    "name": (nm.get_text(strip=True) if nm else li.get_text(strip=True))[:80],
                    "unit": (li.get("unit") or "").strip(),
                    "category": CAT_MAP.get(category, category)})
    return cat


def _series(points):
    out = []
    for pt in points or []:
        if isinstance(pt, list) and len(pt) == 2 and pt[1] is not None:
            try:
                out.append([datetime.utcfromtimestamp(pt[0] / 1000).strftime("%Y-%m-%d"), pt[1]])
            except (ValueError, OSError, TypeError):
                continue
    return out


def fetch_metric(session, family, mid):
    try:
        r = session.get(f"{BASE}/api/v1/sector/{family}/{mid}/", timeout=25)
        if r.status_code != 200:
            return None
        j = r.json()
    except Exception:
        return None
    if isinstance(j, dict):                       # area-chart: {name: [[ts,val]]}
        return {"type": "series", "data": {k: _series(v) for k, v in j.items()}}
    if isinstance(j, list):                       # ms-chart / company-opm: company breakdown
        return {"type": "companies",
                "data": [{"company_id": d.get("company_id"), "name": d.get("short_name") or d.get("value"),
                          "value": d.get("value")} for d in j if isinstance(d, dict)]}
    return None


def scrape_sector(session, slug, delay=0.3, force=False):
    out = OUT / f"{slug}.json"
    if out.exists() and not force:
        return "skip"
    try:
        html = session.get(f"{BASE}/sector/{slug}", timeout=40).text
    except Exception as e:
        return f"err:{type(e).__name__}"
    catalog = parse_catalog(html)
    if not catalog:
        return "no-catalog"
    rec = {"slug": slug, "scraped_at": datetime.now(timezone.utc).isoformat(),
           "num_metrics": len(catalog),
           "sector_metrics": [], "segments": [], "market_share": [], "company_metrics": [], "other": []}
    for m in catalog:
        time.sleep(delay)
        d = fetch_metric(session, m["family"], m["id"])
        entry = {**m, "result": d}
        bucket = m["category"] if m["category"] in rec else "other"
        rec[bucket].append(entry)
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return (f"ok:{len(catalog)}m "
            f"sec={len(rec['sector_metrics'])} seg={len(rec['segments'])} "
            f"ms={len(rec['market_share'])} co={len(rec['company_metrics'])}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slug")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if not SID:
        print("TIJORI_SESSION_ID not set"); return
    s = make_session()
    if args.slug:
        print(f"{args.slug}: {scrape_sector(s, args.slug, args.delay, True)}")
    elif args.all:
        slugs = sector_slugs(s)
        log.info(f"{len(slugs)} sectors")
        for sl in slugs:
            log.info(f"  {sl}: {scrape_sector(s, sl, args.delay, args.force)}")
        log.info("Done.")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
