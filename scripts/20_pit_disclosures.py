#!/usr/bin/env python3
"""
20_pit_disclosures.py — Download SEBI PIT (insider trading) disclosures from NSE.

NSE's per-company API returns promoter/director buy+sell disclosures:
  GET /api/corporates-pit?index=equities&symbol={SYMBOL}

Returns last ~20 disclosures per company. Run periodically to collect new filings.

Output:
  data/pit/{SYMBOL}_pit.json    — raw API response per company
  data/pit/pit_master.csv       — consolidated CSV across all companies

Usage:
  python 20_pit_disclosures.py --all              # all companies (slow)
  python 20_pit_disclosures.py --symbol RELIANCE  # single company
  python 20_pit_disclosures.py --topup            # only missing/stale files
  python 20_pit_disclosures.py --status           # summary stats
"""
import argparse, csv, json, logging, time
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from curl_cffi import requests as cffi

ROOT    = Path(__file__).parent.parent
STRUCT  = ROOT / "data" / "structured"
PIT_DIR = ROOT / "data" / "pit"
PIT_DIR.mkdir(parents=True, exist_ok=True)

MASTER_CSV = PIT_DIR / "pit_master.csv"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("pit")

NSE        = "https://www.nseindia.com"
PIT_API    = f"{NSE}/api/corporates-pit?index=equities&symbol={{symbol}}"
SEED_URL   = f"{NSE}/companies-listing/corporate-filings-pit-disclosures"
STALE_DAYS = 7   # re-fetch file if older than this


def make_session():
    s = cffi.Session(impersonate="chrome")
    s.get(NSE, timeout=30)
    s.get(SEED_URL, timeout=30)
    return s


def fetch_pit(session, symbol: str) -> dict | None:
    try:
        r = session.get(PIT_API.format(symbol=symbol), timeout=20)
        if r.status_code != 200:
            return None
        d = r.json()
        if not isinstance(d, dict) or "data" not in d:
            return None
        return d
    except Exception:
        return None


def save_symbol(symbol: str, data: dict):
    out = PIT_DIR / f"{symbol}_pit.json"
    out.write_text(json.dumps({
        "symbol": symbol,
        "fetched_at": datetime.now().isoformat(),
        "data": data.get("data", []),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(data.get("data", []))


def get_symbols(topup: bool) -> list[str]:
    syms = [f.stem.replace("_screener", "") for f in sorted(STRUCT.glob("*_screener.json"))]
    if topup:
        cutoff = datetime.now() - timedelta(days=STALE_DAYS)
        syms = [s for s in syms
                if not (PIT_DIR / f"{s}_pit.json").exists()
                or datetime.fromtimestamp((PIT_DIR / f"{s}_pit.json").stat().st_mtime) < cutoff]
    return syms


def rebuild_master():
    """Rebuild pit_master.csv from all per-company JSON files."""
    fieldnames = [
        "symbol", "acqName", "acqMode", "acqfromDt", "acqtoDt",
        "buyQuantity", "buyValue", "sellQuantity", "sellValue",
        "befAcqSharesNo", "befAcqSharesPer",
        "afterAcqSharesNo", "afterAcqSharesPer",
        "company", "date", "anex", "did",
    ]
    rows = []
    for f in sorted(PIT_DIR.glob("*_pit.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            sym = d["symbol"]
            for rec in d.get("data", []):
                row = {"symbol": sym}
                row.update({k: rec.get(k, "") for k in fieldnames if k != "symbol"})
                rows.append(row)
        except Exception:
            continue
    with MASTER_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def status():
    files = list(PIT_DIR.glob("*_pit.json"))
    total_records = sum(
        len(json.loads(f.read_text(encoding="utf-8")).get("data", []))
        for f in files if f.name != "pit_master.csv"
    )
    print(f"PIT files: {len(files)} companies, {total_records:,} total disclosure records")
    if MASTER_CSV.exists():
        rows = sum(1 for _ in MASTER_CSV.open(encoding="utf-8")) - 1
        print(f"Master CSV: {rows:,} rows")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all",    action="store_true")
    ap.add_argument("--symbol", help="Single NSE symbol")
    ap.add_argument("--topup",  action="store_true", help="Re-fetch if file >7 days old")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--delay",   type=float, default=0.3)
    args = ap.parse_args()

    if args.status:
        status()
        return

    if args.symbol:
        sess = make_session()
        data = fetch_pit(sess, args.symbol)
        if data:
            n = save_symbol(args.symbol, data)
            log.info(f"{args.symbol}: {n} records saved")
        else:
            log.warning(f"{args.symbol}: no data returned")
        return

    if not (args.all or args.topup):
        ap.print_help()
        return

    symbols = get_symbols(topup=args.topup or True)
    log.info(f"Fetching PIT for {len(symbols)} companies ...")

    # Use a pool of sessions (re-seed every 200 companies)
    CHUNK = 200
    total_ok = total_empty = 0

    for c0 in range(0, len(symbols), CHUNK):
        chunk = symbols[c0:c0 + CHUNK]
        sessions = [make_session() for _ in range(args.workers)]

        def _work(args_inner):
            i, sym = args_inner
            sess = sessions[i % len(sessions)]
            data = fetch_pit(sess, sym)
            time.sleep(args.delay)
            if data and data.get("data"):
                return sym, save_symbol(sym, data)
            return sym, 0

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(_work, (j, sym)): sym for j, sym in enumerate(chunk)}
            for fut in as_completed(futs):
                sym, n = fut.result()
                if n:
                    total_ok += 1
                else:
                    total_empty += 1

        done = c0 + len(chunk)
        log.info(f"  {done}/{len(symbols)} — ok={total_ok}, empty={total_empty}")

    log.info("Rebuilding master CSV ...")
    n = rebuild_master()
    log.info(f"Done. Master CSV: {n:,} rows. ok={total_ok}, empty={total_empty}")


if __name__ == "__main__":
    main()
