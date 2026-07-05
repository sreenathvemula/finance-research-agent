#!/usr/bin/env python3
"""
12_tijori_excel_downloader.py — Download Tijori Excel financials for all companies.

Each company page has embedded download links like:
  https://excel.tijorifinance.com/company/excel/{cid}/{token}/stand_{cid}.xlsx
  https://excel.tijorifinance.com/company/excel/{cid}/{token}/cons_{cid}.xlsx

The token is per-company (not a day name). We fetch the company page, extract the
links, and download both xlsx files.

Output: data/tijori_excel/{slug}/stand_{cid}.xlsx  (standalone)
                              /cons_{cid}.xlsx   (consolidated, if available)

Usage:
  python 12_tijori_excel_downloader.py --all --delay 0.4
  python 12_tijori_excel_downloader.py --all --workers 4 --delay 0.3
  python 12_tijori_excel_downloader.py --slug reliance-industries-limited
"""
import argparse, json, os, re, time, logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from curl_cffi import requests as cffi

ROOT = Path(__file__).parent.parent
TIJORI_DIR = ROOT / "data" / "tijori"
EXCEL_DIR  = ROOT / "data" / "tijori_excel"
EXCEL_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(ROOT / ".env")
SID = os.getenv("TIJORI_SESSION_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("tijori_xl")

BASE = "https://www.tijorifinance.com"
XL_RE = re.compile(
    r'https?://excel\.tijorifinance\.com/company/excel/\d+/\w+/(?:stand|cons)_\d+\.xlsx'
)


def make_session():
    s = cffi.Session(impersonate="chrome")
    s.cookies.set("sessionid", SID, domain=".tijorifinance.com")
    s.headers.update({"Referer": BASE + "/"})
    return s


def get_sitemap_slugs(session):
    sm = session.get(f"{BASE}/sitemap.xml", timeout=40).text
    return sorted(set(re.findall(
        r"<loc>(?:https?://[^<]*?)/company/([a-z0-9-]+)/?</loc>", sm
    )))


def download_slug(session, slug, delay=0.35):
    """Fetch company page, extract xlsx links, download both files. Returns status string."""
    out_dir = EXCEL_DIR / slug
    # Resume: skip if both files already present (or standalone only for BSE-only)
    existing = list(out_dir.glob("*.xlsx")) if out_dir.exists() else []
    if len(existing) >= 2:
        return "skip"
    if len(existing) == 1:
        # Only one file — might be standalone-only company; check if we already tried cons
        if out_dir.exists() and (out_dir / "_cons_missing").exists():
            return "skip"

    try:
        r = session.get(f"{BASE}/company/{slug}/", timeout=40)
    except Exception as e:
        return f"err:{type(e).__name__}"
    if r.status_code != 200:
        return f"http:{r.status_code}"

    links = {
        ("standalone" if "stand_" in u else "consolidated"): u
        for u in XL_RE.findall(r.text)
    }
    if not links:
        return "no_links"

    out_dir.mkdir(parents=True, exist_ok=True)
    downloaded = []
    for typ, url in links.items():
        fname = Path(url).name  # e.g. stand_242.xlsx
        fpath = out_dir / fname
        if fpath.exists() and fpath.stat().st_size > 1000:
            downloaded.append(typ)
            continue
        time.sleep(delay)
        try:
            xr = session.get(url, timeout=60)
            if xr.status_code == 200 and len(xr.content) > 1000:
                fpath.write_bytes(xr.content)
                downloaded.append(typ)
            else:
                # Mark cons as missing so we don't re-fetch
                if typ == "consolidated":
                    (out_dir / "_cons_missing").touch()
        except Exception:
            pass

    return f"ok:{'+'.join(downloaded)}" if downloaded else "dl_fail"


def run_all(delay, workers, force):
    session = make_session()
    log.info("Fetching sitemap ...")
    slugs = get_sitemap_slugs(session)
    log.info(f"{len(slugs)} companies in sitemap")

    if force:
        # Clear existing to re-download everything
        import shutil
        if EXCEL_DIR.exists():
            shutil.rmtree(EXCEL_DIR)
        EXCEL_DIR.mkdir(parents=True, exist_ok=True)

    counts = {}
    done = 0

    if workers <= 1:
        for slug in slugs:
            status = download_slug(session, slug, delay)
            key = status.split(":")[0]
            counts[key] = counts.get(key, 0) + 1
            done += 1
            if done % 200 == 0:
                log.info(f"  {done}/{len(slugs)} — {counts}")
            if key != "skip":
                time.sleep(delay)
    else:
        # Multi-threaded: each thread gets its own session
        def worker_fn(slug):
            ws = make_session()
            status = download_slug(ws, slug, delay)
            return slug, status

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(worker_fn, s): s for s in slugs}
            for fut in as_completed(futures):
                _, status = fut.result()
                key = status.split(":")[0]
                counts[key] = counts.get(key, 0) + 1
                done += 1
                if done % 200 == 0:
                    log.info(f"  {done}/{len(slugs)} — {counts}")

    log.info(f"Done. {counts}")
    # Summary
    xl_files = list(EXCEL_DIR.glob("**/*.xlsx"))
    log.info(f"Total xlsx files on disk: {len(xl_files)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="Download for all sitemap companies")
    ap.add_argument("--slug", help="Single company slug")
    ap.add_argument("--delay", type=float, default=0.35)
    ap.add_argument("--workers", type=int, default=1,
                    help="Parallel workers (each uses own session; be gentle)")
    ap.add_argument("--force", action="store_true", help="Re-download even if file exists")
    args = ap.parse_args()

    if not SID:
        print("TIJORI_SESSION_ID not set in .env"); return

    if args.slug:
        session = make_session()
        status = download_slug(session, args.slug, delay=args.delay)
        print(f"{args.slug}: {status}")
        files = list((EXCEL_DIR / args.slug).glob("*.xlsx")) if (EXCEL_DIR / args.slug).exists() else []
        for f in files:
            print(f"  {f.name}  ({f.stat().st_size // 1024} KB)")
    elif args.all:
        run_all(args.delay, args.workers, args.force)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
