#!/usr/bin/env python3
"""
18_trendlyne_pdf_downloader.py — Download Trendlyne broker research PDFs via Playwright.

Trendlyne now has AWS WAF CAPTCHA blocking headless HTTP libraries (curl_cffi, requests).
This script uses Playwright Chromium (real browser) to bypass WAF, scrape research report
metadata including report IDs, resolve each report ID to its actual broker PDF URL
(plindia.com, motilaloswalesecurities.com, S3, etc.), then downloads PDFs with curl_cffi.

Workflow per company:
  1. Navigate to /research-reports/stock/{tl_id}/{SYMBOL}/ in real browser
  2. Extract all rows: date, broker, recommendation, report_id, post_url
  3. For each report_id, navigate to /get-document/report/pdf/{id}/ — browser follows
     redirect to actual broker URL — capture that URL
  4. Download from broker URL with curl_cffi (no auth required)
  5. Save report metadata + pdf_url to data/trendlyne/{SYMBOL}_trendlyne.json

Output: data/trendlyne_pdfs/{SYMBOL}/{date}_{broker}_{report_id}.pdf

Usage:
  python 18_trendlyne_pdf_downloader.py --symbol RELIANCE
  python 18_trendlyne_pdf_downloader.py --topup         # companies with reports, no PDFs yet
  python 18_trendlyne_pdf_downloader.py --all
  python 18_trendlyne_pdf_downloader.py --scrape-only   # update JSONs with report_ids, no download
  python 18_trendlyne_pdf_downloader.py --download-only # download from already-resolved URLs
"""
import argparse, asyncio, json, logging, os, re, sys, time
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

try:
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout
except ImportError:
    print("ERROR: playwright not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(1)

try:
    from curl_cffi import requests as cffi
except ImportError:
    cffi = None

ROOT = Path(__file__).parent.parent
TL_DIR = ROOT / "data" / "trendlyne"
PDF_DIR = ROOT / "data" / "trendlyne_pdfs"
STRUCT_DIR = ROOT / "data" / "structured"
PDF_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("tl_pdfs")

BASE = "https://trendlyne.com"

# Trendlyne cookies — loaded from env or hard-coded fallback
def _load_cookies():
    env_file = ROOT / ".env"
    csrf = session = ""
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("TRENDLYNE_CSRF="):
                csrf = line.split("=", 1)[1].strip()
            elif line.startswith("TRENDLYNE_SESSION="):
                session = line.split("=", 1)[1].strip()
    return csrf, session

CSRF, SESSION = _load_cookies()


# ─── Page scraping ────────────────────────────────────────────────────────────

JS_EXTRACT_ROWS = """
() => {
  const table = document.querySelector('table');
  if (!table) return [];
  const rows = [...table.querySelectorAll('tr')];
  return rows.flatMap(row => {
    const links = [...row.querySelectorAll('a[href]')].map(a => ({t: a.innerText.trim(), h: a.href}));
    const pdfLink = links.find(l => l.t === 'PDF' && l.h.includes('get-document/report/pdf'));
    if (!pdfLink) return [];
    const m = pdfLink.h.match(/\\/pdf\\/(\\d+)\\//);
    const cells = [...row.querySelectorAll('td')];
    const cacheLink = links.find(l => l.t === 'CACHE');
    const postLink = links.find(l => l.t === 'POST');
    return [{
      report_id: m ? m[1] : null,
      pdf_tl_url: pdfLink.h,
      cache_url: cacheLink ? cacheLink.h : null,
      post_url: postLink ? postLink.h : null,
      row_text: cells.map(c => c.innerText.replace(/\\s+/g, ' ').trim()).join(' | ').slice(0, 200)
    }];
  });
}
"""

def _parse_row_text(row_text):
    """Extract date and broker from row_text (e.g. '26 APR 2026 | Reliance Industries | Prabhudas ...')"""
    parts = [p.strip() for p in row_text.split('|')]
    date = parts[0].strip() if parts else ""
    broker = parts[2].strip() if len(parts) > 2 else ""
    return date, broker


async def scrape_company_page(page, tl_id, symbol):
    """Navigate to company report page, return list of report dicts with report_id."""
    url = f"{BASE}/research-reports/stock/{tl_id}/{symbol}/"
    all_rows = []

    for page_num in range(1, 20):  # up to 20 pages
        page_url = url if page_num == 1 else f"{url}?page={page_num}"
        try:
            await page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(1.5)
        except Exception as e:
            log.debug(f"{symbol} page {page_num}: {e}")
            break

        rows = await page.evaluate(JS_EXTRACT_ROWS)
        if not rows:
            break

        all_rows.extend(rows)

        # Check for next page link
        has_next = await page.evaluate(
            f"() => !!document.querySelector('a[href*=\"page={page_num + 1}\"]')"
        )
        if not has_next:
            break
        await asyncio.sleep(1.0)

    return all_rows


# ─── PDF URL resolution ────────────────────────────────────────────────────────

async def resolve_pdf_url(context, tl_pdf_url, timeout_ms=12000):
    """
    Open a new page, navigate to Trendlyne PDF redirect URL, capture final broker URL.
    Returns (actual_pdf_url, is_trendlyne_s3) or (None, False) on failure.
    """
    broker_url = None
    page = await context.new_page()

    def on_response(response):
        nonlocal broker_url
        url = response.url
        # Capture redirect targets (non-Trendlyne pages, or Trendlyne S3)
        if "trendlyne.com" not in url and "trendlyne-media" not in url:
            # External broker URL
            if any(url.lower().endswith(ext) for ext in ['.pdf', '.aspx', '.php']) or \
               any(host in url for host in ['plindia', 'motilal', 'geojit', 'edelweiss',
                                             'sbiresearch', 'hdfc', 'icici', 'kotak',
                                             'axisdirect', 'sbisec', 'sharekhan', 'iifl',
                                             'cholamandalam', 'karvy', 'nirmal', 'bob',
                                             'aum', 'emkay', 'prabhudas', 'religare']):
                broker_url = url
        elif "trendlyne-media" in url or ".s3.amazonaws.com" in url:
            broker_url = url

    page.on("response", on_response)

    try:
        # Try navigating — will follow redirect
        resp = await page.goto(tl_pdf_url, wait_until="commit", timeout=timeout_ms)
        await asyncio.sleep(1.5)
        # If page URL changed (redirect), capture it
        final_url = page.url
        if "get-document" not in final_url and final_url != "about:blank":
            broker_url = broker_url or final_url
    except PWTimeout:
        pass
    except Exception as e:
        log.debug(f"resolve {tl_pdf_url}: {e}")
    finally:
        await page.close()

    # If response event didn't fire, fallback: any URL that looks like a PDF
    return broker_url


# ─── PDF download ─────────────────────────────────────────────────────────────

def download_pdf_cffi(url, out_path, session=None):
    """Download a PDF from a URL using curl_cffi. Returns 'ok', 'skip', or error."""
    if out_path.exists() and out_path.stat().st_size > 500:
        return "skip"
    if cffi is None:
        return "no_cffi"

    if session is None:
        session = cffi.Session(impersonate="chrome")

    headers = {}
    if "bseindia.com" in url:
        headers["Referer"] = "https://www.bseindia.com/"
    elif "trendlyne" in url or "amazonaws" in url:
        headers["Referer"] = "https://trendlyne.com/"

    try:
        r = session.get(url, timeout=60, headers=headers, allow_redirects=True)
        if r.status_code == 200 and len(r.content) > 500:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(r.content)
            return "ok"
        return f"http:{r.status_code}"
    except Exception as e:
        return f"err:{type(e).__name__}"


# ─── Orchestration ─────────────────────────────────────────────────────────────

def _safe_name(s, maxlen=50):
    s = re.sub(r'[<>:"/\\|?*\s]+', '_', s.strip())
    return s[:maxlen]


async def process_company(context, symbol, tl_id, args):
    """Full pipeline for one company: scrape → resolve → download."""
    tl_json = TL_DIR / f"{symbol}_trendlyne.json"
    pdf_company_dir = PDF_DIR / symbol

    # Load existing data
    existing = {}
    if tl_json.exists():
        try:
            d = json.loads(tl_json.read_text(encoding="utf-8"))
            existing = {r.get("report_id"): r for r in d.get("reports", []) if r.get("report_id")}
        except Exception:
            pass

    # Phase 1: Scrape report page (get report_ids)
    log.info(f"  {symbol}: scraping report page ...")
    page = await context.new_page()
    try:
        raw_rows = await scrape_company_page(page, tl_id, symbol)
    finally:
        await page.close()

    if not raw_rows:
        log.info(f"  {symbol}: no reports found")
        return {"symbol": symbol, "reports": 0, "resolved": 0, "downloaded": 0}

    log.info(f"  {symbol}: {len(raw_rows)} reports found")

    # Phase 2: Resolve PDF URLs for rows without one
    resolved = 0
    for row in raw_rows:
        rid = row.get("report_id")
        if not rid:
            continue
        # Check if already resolved in existing data
        if rid in existing and existing[rid].get("actual_pdf_url"):
            row["actual_pdf_url"] = existing[rid]["actual_pdf_url"]
            continue

        if not args.download_only:
            pdf_url = await resolve_pdf_url(context, row["pdf_tl_url"])
            row["actual_pdf_url"] = pdf_url
            if pdf_url:
                resolved += 1
            await asyncio.sleep(0.3)

    # Update trendlyne JSON with resolved URLs
    if not args.download_only:
        if tl_json.exists():
            try:
                data = json.loads(tl_json.read_text(encoding="utf-8"))
            except Exception:
                data = {"symbol": symbol, "tl_id": tl_id, "reports": []}
        else:
            data = {"symbol": symbol, "tl_id": tl_id, "reports": []}

        # Merge: add report_id + actual_pdf_url to matching reports
        rid_to_row = {r["report_id"]: r for r in raw_rows if r.get("report_id")}
        for rep in data.get("reports", []):
            rid = rep.get("report_id")
            if rid and rid in rid_to_row:
                rep["actual_pdf_url"] = rid_to_row[rid].get("actual_pdf_url")
                rep["post_url"] = rep.get("post_url") or rid_to_row[rid].get("post_url")
        # Add any reports only in raw_rows (new ones)
        existing_rids = {r.get("report_id") for r in data.get("reports", [])}
        for row in raw_rows:
            if row.get("report_id") and row["report_id"] not in existing_rids:
                date_str, broker = _parse_row_text(row.get("row_text", ""))
                data.setdefault("reports", []).append({
                    "date": date_str,
                    "broker": broker,
                    "report_id": row["report_id"],
                    "actual_pdf_url": row.get("actual_pdf_url"),
                    "post_url": row.get("post_url"),
                    "pdf_tl_url": row.get("pdf_tl_url"),
                })
        tl_json.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # Phase 3: Download PDFs
    downloaded = 0
    if not args.scrape_only:
        dl_session = cffi.Session(impersonate="chrome") if cffi else None
        for row in raw_rows:
            pdf_url = row.get("actual_pdf_url")
            if not pdf_url:
                continue
            rid = row.get("report_id", "unknown")
            date_str, broker = _parse_row_text(row.get("row_text", ""))
            filename = f"{_safe_name(date_str)}_{_safe_name(broker)}_{rid}.pdf"
            out_path = pdf_company_dir / filename
            result = download_pdf_cffi(pdf_url, out_path, dl_session)
            if result == "ok":
                downloaded += 1
            elif result != "skip":
                log.debug(f"  {symbol} {rid}: {result}")

    return {"symbol": symbol, "reports": len(raw_rows), "resolved": resolved, "downloaded": downloaded}


def get_companies_to_process(args):
    """Return list of (symbol, tl_id) tuples."""
    companies = []
    for f in sorted(TL_DIR.glob("*_trendlyne.json")):
        symbol = f.stem.replace("_trendlyne", "")
        if args.symbol and symbol != args.symbol:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        tl_id = data.get("tl_id")
        if not tl_id:
            continue
        # In --topup mode, only process if there are reports but no PDFs downloaded
        if args.topup:
            num_reports = data.get("num_reports", 0)
            if num_reports == 0:
                continue
            pdf_dir = PDF_DIR / symbol
            if pdf_dir.exists() and any(pdf_dir.glob("*.pdf")):
                continue  # already has PDFs
        companies.append((symbol, tl_id))
    return companies


async def run(args):
    companies = get_companies_to_process(args)
    if not companies:
        log.info("No companies to process")
        return

    log.info(f"Processing {len(companies)} companies")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
        )
        # Inject Trendlyne login cookies
        if CSRF and SESSION:
            await context.add_cookies([
                {"name": "csrftoken", "value": CSRF, "domain": "trendlyne.com", "path": "/"},
                {"name": ".trendlyne", "value": SESSION, "domain": "trendlyne.com", "path": "/"},
            ])

        totals = {"reports": 0, "resolved": 0, "downloaded": 0}
        for i, (symbol, tl_id) in enumerate(companies):
            log.info(f"[{i+1}/{len(companies)}] {symbol} (tl_id={tl_id})")
            try:
                result = await process_company(context, symbol, tl_id, args)
                for k in totals:
                    totals[k] += result.get(k, 0)
            except Exception as e:
                log.warning(f"  {symbol}: ERROR — {e}")

            if (i + 1) % 20 == 0:
                log.info(f"  Progress: {totals}")

        await browser.close()

    log.info(f"Done: {totals}")
    pdf_count = sum(1 for _ in PDF_DIR.rglob("*.pdf"))
    log.info(f"Total PDFs on disk: {pdf_count}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", help="Single NSE symbol (e.g. RELIANCE)")
    ap.add_argument("--topup", action="store_true",
                    help="Only companies that have reports but no PDFs yet")
    ap.add_argument("--all", action="store_true", help="All companies in trendlyne dir")
    ap.add_argument("--scrape-only", action="store_true",
                    help="Only update JSONs with report_ids, skip download")
    ap.add_argument("--download-only", action="store_true",
                    help="Only download from already-resolved URLs in JSONs")
    args = ap.parse_args()

    if not args.symbol and not args.topup and not args.all:
        ap.print_help()
        return

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
