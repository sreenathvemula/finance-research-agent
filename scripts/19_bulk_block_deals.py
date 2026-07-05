#!/usr/bin/env python3
"""
19_bulk_block_deals.py — Collect bulk & block deals (NSE + Trendlyne).

Sources:
  NSE API  — /api/historicalOR/bulk-block-short-deals (authoritative, historical)
  Trendlyne — portfolio/bulk-block-deals (last 2 trading days, NSE+BSE)

Usage:
  python 19_bulk_block_deals.py              # fetch today via Trendlyne (NSE+BSE)
  python 19_bulk_block_deals.py --backfill   # 1 year NSE history day-by-day
  python 19_bulk_block_deals.py --backfill --days 90  # last N days via NSE
  python 19_bulk_block_deals.py --status     # show collection summary

Output:
  data/deals/bulk_block_deals.csv   — master CSV (deduplicated)
  data/deals/run_log.csv            — run history
"""
import argparse, csv, logging, re, time
from datetime import date, datetime, timedelta
from pathlib import Path

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi

ROOT      = Path(__file__).parent.parent
DEALS_DIR = ROOT / "data" / "deals"
DEALS_DIR.mkdir(parents=True, exist_ok=True)

MASTER_CSV = DEALS_DIR / "bulk_block_deals.csv"
LOG_CSV    = DEALS_DIR / "run_log.csv"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("deals")

# ─── Common columns ────────────────────────────────────────────────────────────
FIELDNAMES = ["Stock", "Client Name", "Exchange", "Deal Type",
              "Action", "Date", "Avg. Price", "Quantity", "Percentage Traded %"]
DEDUP_KEY  = ("Stock", "Client Name", "Exchange", "Deal Type", "Action", "Date")


# ─── NSE helpers ───────────────────────────────────────────────────────────────
NSE_BASE = "https://www.nseindia.com"
NSE_API  = f"{NSE_BASE}/api/historicalOR/bulk-block-short-deals"
NSE_SEED_PAGES = [
    f"{NSE_BASE}",
    f"{NSE_BASE}/market-data/block-deals",
    f"{NSE_BASE}/report-detail/display-bulk-and-block-deals",
]

_MON_MAP = {
    "JAN": "Jan", "FEB": "Feb", "MAR": "Mar", "APR": "Apr",
    "MAY": "May", "JUN": "Jun", "JUL": "Jul", "AUG": "Aug",
    "SEP": "Sep", "OCT": "Oct", "NOV": "Nov", "DEC": "Dec",
}


def _nse_date_to_label(bd_dt_date: str) -> str:
    """'09-JUN-2025' → '09 Jun 2025'."""
    parts = bd_dt_date.upper().split("-")
    if len(parts) == 3:
        d, m, y = parts
        return f"{d} {_MON_MAP.get(m, m.capitalize())} {y}"
    return bd_dt_date


def make_nse_session() -> cffi.Session:
    s = cffi.Session(impersonate="chrome")
    s.headers.update({
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    for url in NSE_SEED_PAGES:
        s.get(url, timeout=30)
        time.sleep(2)
    s.headers.update({
        "Accept": "application/json, text/plain, */*",
        "Referer": NSE_SEED_PAGES[-1],
        "X-Requested-With": "XMLHttpRequest",
    })
    return s


def fetch_nse_deals_day(session: cffi.Session, dt: date) -> list[dict]:
    """Fetch NSE bulk + block deals for one calendar day. Returns mapped rows."""
    dt_str = dt.strftime("%d-%m-%Y")
    rows = []
    for option_type, deal_type in [("bulk_deals", "Bulk"), ("block_deals", "Block")]:
        try:
            r = session.get(
                NSE_API,
                params={"optionType": option_type, "from": dt_str, "to": dt_str},
                timeout=30,
            )
            if r.status_code != 200:
                continue
            ct = r.headers.get("Content-Type", "")
            if "json" not in ct:
                continue
            data = r.json().get("data", [])
            for rec in data:
                rows.append({
                    "Stock":                rec.get("BD_SYMBOL", ""),
                    "Client Name":          rec.get("BD_CLIENT_NAME", ""),
                    "Exchange":             "NSE",
                    "Deal Type":            deal_type,
                    "Action":               rec.get("BD_BUY_SELL", ""),
                    "Date":                 _nse_date_to_label(rec.get("BD_DT_DATE", "")),
                    "Avg. Price":           str(rec.get("BD_TP_WATP", "")),
                    "Quantity":             str(rec.get("BD_QTY_TRD", "")),
                    "Percentage Traded %":  "",
                })
        except Exception as e:
            log.debug(f"NSE {option_type} {dt_str}: {e}")
    return rows


def _trading_days(start: date, end: date) -> list[date]:
    """All calendar days from start to end excluding weekends."""
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:  # Mon–Fri
            days.append(d)
        d += timedelta(days=1)
    return days


# ─── Trendlyne helpers ─────────────────────────────────────────────────────────
TL_BASE  = "https://trendlyne.com"
TL_URL   = f"{TL_BASE}/portfolio/bulk-block-deals/"


def make_tl_session() -> cffi.Session:
    s = cffi.Session(impersonate="chrome")
    s.headers.update({"Referer": TL_BASE + "/"})
    s.get(TL_BASE, timeout=20)
    time.sleep(1.0)
    s.get(TL_URL, timeout=20)
    time.sleep(0.5)
    return s


def fetch_tl_deals(session: cffi.Session) -> list[dict]:
    """Scrape the Trendlyne page (last 2 trading days, NSE+BSE)."""
    r = session.get(TL_URL, timeout=20)
    if r.status_code != 200:
        log.warning(f"HTTP {r.status_code} fetching Trendlyne deals page")
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    rows = []
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) >= 8:
                row = {
                    "Stock":                cells[0].get_text(strip=True)[:60],
                    "Client Name":          cells[1].get_text(strip=True),
                    "Exchange":             cells[2].get_text(strip=True),
                    "Deal Type":            cells[3].get_text(strip=True),
                    "Action":               cells[4].get_text(strip=True),
                    "Date":                 cells[5].get_text(strip=True),
                    "Avg. Price":           cells[6].get_text(strip=True),
                    "Quantity":             cells[7].get_text(strip=True),
                    "Percentage Traded %":  cells[8].get_text(strip=True) if len(cells) > 8 else "",
                }
                if len(row["Date"]) > 5 and row["Exchange"] in ("NSE", "BSE"):
                    rows.append(row)
    return rows


# ─── CSV helpers ───────────────────────────────────────────────────────────────
def _existing_keys(csv_path: Path) -> set[tuple]:
    if not csv_path.exists():
        return set()
    keys = set()
    with csv_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            keys.add(tuple(row.get(k, "") for k in DEDUP_KEY))
    return keys


def append_new(rows: list[dict], existing: set[tuple]) -> tuple[int, set[tuple]]:
    """Append new rows; return (count_added, updated_existing_set)."""
    new_rows = [r for r in rows
                if tuple(r.get(k, "") for k in DEDUP_KEY) not in existing]
    if not new_rows:
        return 0, existing
    write_header = not MASTER_CSV.exists()
    with MASTER_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        if write_header:
            w.writeheader()
        w.writerows(new_rows)
    for r in new_rows:
        existing.add(tuple(r.get(k, "") for k in DEDUP_KEY))
    return len(new_rows), existing


def log_run(source: str, fetched: int, added: int):
    write_header = not LOG_CSV.exists()
    with LOG_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["run_at", "source", "fetched", "added"])
        w.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), source, fetched, added])


# ─── Commands ──────────────────────────────────────────────────────────────────
def run_trendlyne():
    log.info("Seeding Trendlyne session ...")
    sess = make_tl_session()
    log.info("Fetching latest bulk & block deals (Trendlyne) ...")
    rows = fetch_tl_deals(sess)
    if not rows:
        log.warning("No deals scraped from Trendlyne")
        log_run("trendlyne", 0, 0)
        return
    from collections import Counter
    date_counts = Counter(r["Date"] for r in rows)
    log.info(f"Fetched {len(rows)} deals: {dict(date_counts)}")
    existing = _existing_keys(MASTER_CSV)
    added, _ = append_new(rows, existing)
    log.info(f"Added {added} new rows (skipped {len(rows) - added} duplicates)")
    log_run("trendlyne", len(rows), added)


def run_backfill(days: int):
    today = date.today()
    start = today - timedelta(days=days)
    trading = _trading_days(start, today - timedelta(days=1))  # exclude today
    log.info(f"NSE backfill: {start} → {today - timedelta(days=1)}, {len(trading)} trading days")

    log.info("Seeding NSE session (takes ~6s) ...")
    sess = make_nse_session()

    existing = _existing_keys(MASTER_CSV)
    total_fetched = total_added = 0
    RESEED_EVERY = 120  # requests before re-seeding

    for i, day in enumerate(trading):
        # Re-seed every RESEED_EVERY days to keep session alive
        if i > 0 and i % RESEED_EVERY == 0:
            log.info(f"  Re-seeding NSE session at day {i}/{len(trading)} ...")
            sess = make_nse_session()

        rows = fetch_nse_deals_day(sess, day)
        fetched = len(rows)
        total_fetched += fetched

        if rows:
            added, existing = append_new(rows, existing)
            total_added += added
        else:
            added = 0

        if (i + 1) % 20 == 0 or i == len(trading) - 1:
            log.info(f"  {i+1}/{len(trading)} days — total fetched={total_fetched:,}, added={total_added:,}")

        time.sleep(1.0)  # be respectful

    log.info(f"Backfill complete. Fetched {total_fetched:,} rows, added {total_added:,} to CSV.")
    log_run("nse-backfill", total_fetched, total_added)


def status():
    if not MASTER_CSV.exists():
        print("No data collected yet.")
        return
    rows = list(csv.DictReader(MASTER_CSV.open(encoding="utf-8")))
    dates = sorted({r.get("Date", "") for r in rows if r.get("Date")})
    exchanges = {r.get("Exchange", "") for r in rows}
    deal_types = {r.get("Deal Type", "") for r in rows}
    print(f"Total rows  : {len(rows):,}")
    print(f"Trading days: {len(dates)}  ({dates[0] if dates else '?'} to {dates[-1] if dates else '?'})")
    print(f"Exchanges   : {exchanges}")
    print(f"Deal types  : {deal_types}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backfill", action="store_true",
                    help="Fetch NSE historical data day-by-day (default: last 365 days)")
    ap.add_argument("--days", type=int, default=365,
                    help="Number of calendar days to backfill (default: 365)")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.status:
        status()
    elif args.backfill:
        run_backfill(args.days)
    else:
        run_trendlyne()


if __name__ == "__main__":
    main()
