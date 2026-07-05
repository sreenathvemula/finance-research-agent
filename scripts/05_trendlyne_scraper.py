#!/usr/bin/env python3
"""
05_trendlyne_scraper.py — Scrape Trendlyne broker research report listings.

For each NSE-listed company, fetches the Trendlyne research reports page:
  https://trendlyne.com/research-reports/{tl_id}/{SYMBOL}/

Extracts per report (free, no login required):
  - date, stock, broker, analyst, recommendation type (Buy/Hold/Sell/Note)
  - LTP, target price, price at recommendation, upside %
  - post_url: link to the free text summary of the report
  - post_text: scraped text of the report summary

Also scrapes DVM score and analyst consensus from the company equity page.

Output: data/structured/{SYMBOL}_trendlyne.json

Usage:
  python 05_trendlyne_scraper.py --all          # all companies
  python 05_trendlyne_scraper.py --symbol RELIANCE
  python 05_trendlyne_scraper.py --topup        # only missing/empty files
"""
import argparse, json, logging, random, re, time
from pathlib import Path

from curl_cffi import requests as cffi
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent.parent
STRUCT_DIR = ROOT / "data" / "structured"
TL_DIR = ROOT / "data" / "trendlyne"
TL_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("trendlyne")

BASE = "https://trendlyne.com"


def make_session():
    s = cffi.Session(impersonate="chrome")
    s.headers.update({"Referer": BASE + "/"})
    return s


# Trendlyne signals rate-limiting with HTTP 405 (sometimes 429/503).
RATELIMIT_CODES = (405, 429, 503)


def rl_get(session, url, *, timeout=25, allow_redirects=False, max_tries=10):
    """GET that backs off (exponentially) when Trendlyne rate-limits us.

    Returns the Response (last one even if still limited), or None on a
    hard network error. Non-rate-limit statuses (200, 404, 3xx) return
    immediately so genuine no-coverage isn't mistaken for a block.
    """
    delay = 30
    r = None
    for attempt in range(max_tries):
        try:
            r = session.get(url, timeout=timeout, allow_redirects=allow_redirects)
        except Exception:
            return None
        if r.status_code not in RATELIMIT_CODES:
            return r
        log.warning(f"Rate-limited ({r.status_code}); backing off {delay}s "
                    f"(try {attempt+1}/{max_tries}) — {url[:70]}")
        time.sleep(delay)
        delay = min(delay * 2, 600)
    return r


def _parse_num(text):
    t = text.strip().replace(",", "")
    if not t or t in ("—", "-", "N/A", ""):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def fetch_post_text(session, post_url, delay=0.2):
    """Fetch the free text summary from a Trendlyne post page."""
    if not post_url or "/posts/" not in post_url:
        return None
    try:
        r = session.get(post_url, timeout=20)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        # Main content area
        for sel in ["div.post-content", "div.post-body", "article", "div.content-body"]:
            el = soup.select_one(sel)
            if el:
                return el.get_text(" ", strip=True)[:3000]
        # Fallback: largest text block
        divs = soup.find_all("div")
        if divs:
            return max(divs, key=lambda d: len(d.get_text())).get_text(" ", strip=True)[:3000]
    except Exception:
        pass
    finally:
        time.sleep(delay)
    return None


def parse_reports_table(table):
    """Parse one page of the research reports table."""
    rows = table.find_all("tr")
    if not rows:
        return []

    reports = []
    for row in rows[1:]:  # skip header
        cells = row.find_all("td")
        if len(cells) < 8:
            continue

        # Extract links for stock and broker
        links = {a.get_text(strip=True): a["href"]
                 for a in row.find_all("a", href=True) if a.get_text(strip=True)}

        # Cell structure: [thumbnail, date, stock, author, LTP, target, price_at_reco, upside, type, report_links, (empty), discuss]
        date = cells[1].get_text(strip=True)
        stock_name = cells[2].get_text(strip=True)
        author = cells[3].get_text(strip=True) if len(cells) > 3 else ""
        ltp = _parse_num(cells[4].get_text(strip=True)) if len(cells) > 4 else None
        target = _parse_num(cells[5].get_text(strip=True)) if len(cells) > 5 else None
        rec_text = cells[6].get_text(" ", strip=True) if len(cells) > 6 else ""
        price_at_reco_m = re.search(r"^(-?[\d,.]+)", rec_text.replace(",", ""))
        price_at_reco = float(price_at_reco_m.group(1)) if price_at_reco_m else None
        chg_pct_m = re.search(r"\((-?[\d.]+)%\)", rec_text)
        chg_pct = float(chg_pct_m.group(1)) if chg_pct_m else None
        upside = _parse_num(cells[7].get_text(strip=True)) if len(cells) > 7 else None
        rec_type = cells[8].get_text(strip=True) if len(cells) > 8 else ""

        # Find post URL from links
        post_url = next((href for text, href in links.items()
                         if "post" in text.lower() or "/posts/" in href), None)
        # Stock URL to extract trendlyne ID
        stock_url = next((href for href in links.values()
                          if "/research-reports/stock/" in href or "/research-reports/" in href
                          and href != BASE + "/research-reports/"), None)
        # Broker name
        broker = next((text for text, href in links.items()
                       if "/broker/" in href), author)

        reports.append({
            "date": date,
            "stock_name": stock_name,
            "broker": broker,
            "analyst": author,
            "ltp": ltp,
            "target": target,
            "price_at_reco": price_at_reco,
            "chg_since_reco_pct": chg_pct,
            "upside_pct": upside,
            "recommendation": rec_type,
            "post_url": post_url,
            "post_text": None,  # filled later if --posts flag
            "stock_tl_url": stock_url,
        })

    return reports


def scrape_company_reports(session, tl_id, symbol, fetch_posts=False, delay=0.3):
    """Scrape all research report pages for a company. Returns list of reports."""
    url = f"{BASE}/research-reports/stock/{tl_id}/{symbol}/"
    all_reports = []
    page = 1

    while True:
        page_url = url if page == 1 else f"{url}?page={page}"
        r = rl_get(session, page_url, timeout=25, allow_redirects=True)
        if r is None or r.status_code != 200:
            break

        soup = BeautifulSoup(r.text, "html.parser")
        table = soup.find("table")
        if not table:
            break

        reports = parse_reports_table(table)
        if not reports:
            break

        all_reports.extend(reports)

        # Check for next page
        next_link = soup.find("a", string=re.compile(r"next|»", re.I)) or \
                    soup.find("a", href=re.compile(rf"page={page+1}"))
        if not next_link:
            break
        page += 1
        time.sleep(delay)

    if fetch_posts:
        for rep in all_reports:
            if rep.get("post_url") and not rep.get("post_text"):
                rep["post_text"] = fetch_post_text(session, rep["post_url"], delay)

    return all_reports


def get_tl_id_for_symbol(session, symbol, delay=0.2):
    """Find Trendlyne company ID by fetching the share-price-target redirect."""
    try:
        r = rl_get(session, f"{BASE}/equity/{symbol}/share-price-target/",
                   timeout=20, allow_redirects=True)
        # Redirects to /equity/{id}/{SYMBOL}/...
        if r is not None:
            m = re.search(r"/equity/(\d+)/", str(r.url))
            if m:
                return int(m.group(1))
    finally:
        time.sleep(delay)

    # Fallback: research-reports redirect
    try:
        r2 = rl_get(session, f"{BASE}/research-reports/stock/{symbol}/",
                    timeout=20, allow_redirects=True)
        if r2 is not None:
            m2 = re.search(r"/research-reports/stock/(\d+)/", str(r2.url))
            if m2:
                return int(m2.group(1))
    finally:
        time.sleep(delay)

    return None


def scrape_company(session, symbol, tl_id=None, fetch_posts=False, delay=0.3):
    """Full scrape for one company. Returns data dict."""
    if tl_id is None:
        tl_id = get_tl_id_for_symbol(session, symbol, delay)

    if tl_id is None:
        return {"symbol": symbol, "tl_id": None, "reports": [], "error": "id_not_found"}

    reports = scrape_company_reports(session, tl_id, symbol, fetch_posts, delay)

    return {
        "symbol": symbol,
        "tl_id": tl_id,
        "reports": reports,
        "num_reports": len(reports),
    }


def get_symbols():
    """Get all NSE symbols from structured dir."""
    return [f.stem.replace("_screener", "")
            for f in sorted(STRUCT_DIR.glob("*_screener.json"))]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="Scrape all companies")
    ap.add_argument("--symbol", help="Single NSE symbol")
    ap.add_argument("--topup", action="store_true",
                    help="Only scrape companies with no/empty trendlyne file")
    ap.add_argument("--posts", action="store_true",
                    help="Also fetch post text summaries (slower)")
    ap.add_argument("--delay", type=float, default=0.35)
    args = ap.parse_args()

    session = make_session()

    if args.symbol:
        data = scrape_company(session, args.symbol, fetch_posts=args.posts, delay=args.delay)
        out = TL_DIR / f"{args.symbol}_trendlyne.json"
        out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info(f"{args.symbol}: {data['num_reports']} reports, tl_id={data['tl_id']}")
        if data["reports"]:
            r0 = data["reports"][0]
            log.info(f"  Latest: {r0['date']} | {r0['broker']} | {r0['recommendation']} | target={r0['target']}")
        return

    symbols = get_symbols()
    if args.topup:
        symbols = [s for s in symbols
                   if not (TL_DIR / f"{s}_trendlyne.json").exists()
                   or (TL_DIR / f"{s}_trendlyne.json").stat().st_size < 100]

    log.info(f"Scraping {len(symbols)} companies ...")
    done = skipped = errors = total_reports = 0

    for i, symbol in enumerate(symbols):
        out = TL_DIR / f"{symbol}_trendlyne.json"
        if out.exists() and not args.topup:
            skipped += 1
            continue

        data = scrape_company(session, symbol, fetch_posts=args.posts, delay=args.delay)
        out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        total_reports += data.get("num_reports", 0)
        done += 1
        if data.get("error"):
            errors += 1

        if (i + 1) % 100 == 0:
            log.info(f"  {i+1}/{len(symbols)} — done={done}, reports={total_reports}, errors={errors}")

        # Jittered delay (0.8x–1.6x base) so requests don't form a fixed cadence.
        time.sleep(args.delay * random.uniform(0.8, 1.6))

    log.info(f"Done: {done} scraped, {skipped} skipped, {errors} errors, {total_reports} total reports")


if __name__ == "__main__":
    main()
