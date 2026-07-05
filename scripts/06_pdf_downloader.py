#!/usr/bin/env python3
"""
06_pdf_downloader.py â€” Download document PDFs from screener JSON files.

Date limits (from today):
  annual_reports : last 3 financial years  (FY label year >= current_year - 2)
  concalls       : last 1 year
  credit_ratings : last 3 years
  announcements  : 30 most recent per company (no reliable date field)

Output: data/pdfs/{SYMBOL}/annual_reports/   concalls/   credit_ratings/   announcements/
Skips files already on disk (>5 KB). CRISIL/HTML pages saved as .html.

Usage:
  python 06_pdf_downloader.py --all               # all companies
  python 06_pdf_downloader.py --symbol RELIANCE   # single company
  python 06_pdf_downloader.py --workers 8         # concurrency (default 6)
"""
import argparse, json, logging, re, time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FTimeout
from datetime import date
from pathlib import Path

from curl_cffi import requests as cffi

ROOT    = Path(__file__).parent.parent
STRUCT  = ROOT / "data" / "structured"
PDF_DIR = ROOT / "data" / "pdfs"
MD_DIR  = ROOT / "data" / "companies"   # if a doc was already converted to md, don't re-download it
PDF_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("pdf_dl")

MIN_BYTES   = 5_000
ANN_LIMIT   = 30          # max announcements per company (newest first)
TODAY       = date.today()
CY          = TODAY.year

AR_MIN_YEAR = CY - 2      # FY label year >= this  (e.g. 2024 in year 2026)
CC_CUTOFF   = date(CY - 5, TODAY.month, TODAY.day)   # concalls: 5 years back
CR_CUTOFF   = date(CY - 10, TODAY.month, TODAY.day)  # credit ratings: 10 years back

_MONTHS = {m: i for i, m in enumerate(
    ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"], 1)}


def _year_from_text(text: str) -> int:
    years = [int(y) for y in re.findall(r'\b20\d{2}\b', text)]
    return max(years) if years else 0


def _date_from_label(label: str) -> date | None:
    """Parse 'Rating update 15 Oct 2025 from ...' â†’ date."""
    m = re.search(r'(\d{1,2})\s+([A-Za-z]{3})\s+(20\d{2})', label)
    if not m:
        return None
    day, mon, yr = int(m.group(1)), _MONTHS.get(m.group(2).lower(), 0), int(m.group(3))
    if not mon:
        return None
    try:
        return date(yr, mon, day)
    except ValueError:
        return None


def _date_from_concall(date_str: str) -> date | None:
    """Parse 'May 2026', 'Jan 2026' â†’ date (1st of month)."""
    m = re.match(r'([A-Za-z]{3})\s+(20\d{2})', date_str.strip())
    if not m:
        return None
    mon = _MONTHS.get(m.group(1).lower(), 0)
    yr  = int(m.group(2))
    if not mon:
        return None
    try:
        return date(yr, mon, 1)
    except ValueError:
        return None


def make_session():
    s = cffi.Session(impersonate="chrome")
    s.headers.update({"Referer": "https://www.bseindia.com/"})
    return s


def safe_name(text: str, max_len: int = 80) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(text))
    text = re.sub(r'\s+', '_', text.strip())
    text = re.sub(r'_+', '_', text)
    return text[:max_len].rstrip('_.')


def download(session, url: str, dest: Path) -> str:
    # already converted to markdown (PDF since deleted to reclaim disk) â†’ don't re-download
    try:
        md_equiv = (MD_DIR / dest.relative_to(PDF_DIR)).with_suffix('.md')
        if md_equiv.exists() and md_equiv.stat().st_size > 200:
            return "skip"
    except ValueError:
        pass
    for ext in (dest.suffix, '.html', '.pdf'):
        alt = dest.with_suffix(ext)
        if alt.exists() and alt.stat().st_size >= MIN_BYTES:
            return "skip"
    try:
        # (connect, read) tuple â€” a dead host that accepts then never responds
        # must fail fast instead of hanging past a single scalar timeout
        r = session.get(url, timeout=(10, 30), allow_redirects=True)
        if r.status_code != 200:
            return "fail"
        content = r.content
        if len(content) < MIN_BYTES:
            return "fail"
        ct = r.headers.get("Content-Type", "")
        is_html = "html" in ct.lower() or (
            content[:5] != b"%PDF-" and b"<html" in content[:500].lower()
        )
        out = dest.with_suffix('.html') if is_html else dest
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(content)
        return "ok"
    except Exception:
        return "fail"


def jobs_for(symbol: str, data: dict) -> list:
    docs = data.get("documents", {})
    base = PDF_DIR / symbol
    jobs = []

    # Annual reports: last 3 FY
    ar_dir = base / "annual_reports"
    for item in docs.get("annual_reports", []):
        url = item.get("url", "")
        if not url:
            continue
        label = item.get("label", "annual_report")
        yr = _year_from_text(label)
        if yr and yr < AR_MIN_YEAR:
            continue
        fname = safe_name(label.replace(" ", "_")) or "annual_report"
        jobs.append((url, ar_dir / f"{fname}.pdf"))

    # Concalls: last 1 year
    cc_dir = base / "concalls"
    for item in docs.get("concalls", []):
        d = _date_from_concall(item.get("date", ""))
        if d and d < CC_CUTOFF:
            continue
        date_slug = safe_name(item.get("date", "unknown").replace(" ", "_"))
        if item.get("transcript"):
            jobs.append((item["transcript"], cc_dir / f"{date_slug}_transcript.pdf"))
        if item.get("ppt"):
            jobs.append((item["ppt"], cc_dir / f"{date_slug}_presentation.pdf"))

    # Credit ratings: last 3 years
    cr_dir = base / "credit_ratings"
    for item in docs.get("credit_ratings", []):
        url = item.get("url", "")
        if not url:
            continue
        label = item.get("label", "credit_rating")
        d = _date_from_label(label)
        if d:
            if d < CR_CUTOFF:
                continue
        else:
            yr = _year_from_text(label)
            if yr and yr < CY - 3:
                continue
        fname = safe_name(label.replace(" ", "_")) or "credit_rating"
        jobs.append((url, cr_dir / f"{fname}.pdf"))

    # Announcements: 30 most recent
    ann_dir = base / "announcements"
    for item in list(data.get("announcements", []))[:ANN_LIMIT]:
        url = item.get("url", "")
        if not url:
            continue
        fname = safe_name(item.get("title", "announcement")[:70].replace(" ", "_"))
        jobs.append((url, ann_dir / f"{fname}.pdf"))

    return jobs


# hard wall-clock cap per company: no single bad company (dead-host URLs that
# ignore request timeouts) can ever wedge the whole run â€” abandon and move on
COMPANY_CAP = 150  # seconds

def run_jobs(jobs: list, workers: int, delay: float) -> dict:
    stats = {"ok": 0, "skip": 0, "fail": 0}

    def _do(url_dest):
        url, dest = url_dest
        s = make_session()
        r = download(s, url, dest)
        time.sleep(delay)
        return r

    ex = ThreadPoolExecutor(max_workers=workers)
    futs = {ex.submit(_do, j): j for j in jobs}
    try:
        for fut in as_completed(futs, timeout=COMPANY_CAP):
            stats[fut.result()] = stats.get(fut.result(), 0) + 1
    except FTimeout:
        done = sum(stats.values())
        stats["fail"] += len(jobs) - done   # count the abandoned (hung) jobs
    finally:
        # don't block on hung threads; drop pending work and return immediately
        ex.shutdown(wait=False, cancel_futures=True)
    return stats


def get_symbols():
    return [f.stem.replace("_screener", "") for f in sorted(STRUCT.glob("*_screener.json"))]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all",     action="store_true")
    ap.add_argument("--symbol",  help="Single NSE symbol")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--delay",   type=float, default=0.15)
    ap.add_argument("--start",   type=int, default=0,
                    help="Skip the first N companies (resume point)")
    args = ap.parse_args()

    log.info(f"Date limits â€” AR FY>={AR_MIN_YEAR}, Concalls>={CC_CUTOFF}, "
             f"CreditRatings>={CR_CUTOFF}, Announcements top-{ANN_LIMIT}")

    if args.symbol:
        f = STRUCT / f"{args.symbol}_screener.json"
        data = json.loads(f.read_text(encoding="utf-8"))
        jobs = jobs_for(args.symbol, data)
        log.info(f"{args.symbol}: {len(jobs)} links")
        stats = run_jobs(jobs, args.workers, args.delay)
        log.info(f"Done: {stats}")
        return

    if not args.all:
        ap.print_help()
        return

    symbols = get_symbols()
    if args.start:
        symbols = symbols[args.start:]
        log.info(f"Resuming from company #{args.start} ({len(symbols)} remaining)")
    log.info(f"Downloading PDFs for {len(symbols)} companies ...")
    total = {"ok": 0, "skip": 0, "fail": 0}

    for i, sym in enumerate(symbols):
        try:
            data = json.loads((STRUCT / f"{sym}_screener.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        jobs = jobs_for(sym, data)
        if not jobs:
            continue
        stats = run_jobs(jobs, args.workers, args.delay)
        for k in total:
            total[k] += stats.get(k, 0)

        if (i + 1) % 100 == 0:
            log.info(f"  {i+1}/{len(symbols)} â€” ok={total['ok']:,} skip={total['skip']:,} fail={total['fail']:,}")

    log.info(f"ALL DONE â€” ok={total['ok']:,} skip={total['skip']:,} fail={total['fail']:,}")


if __name__ == "__main__":
    main()
