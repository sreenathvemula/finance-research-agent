"""
Screener.in Bulk Metrics Export — Full Coverage, JSON Merge
============================================================
Downloads ALL requested metrics for every company and merges them
into the existing per-company _screener.json files as `screener_metrics`.

Authenticated session (SCREENER_SESSION_ID in .env) unlocks all metrics
beyond the default 9 shown to anonymous visitors.

Usage:
  python 07_screener_bulk_export.py --test            # batch 1, first page only
  python 07_screener_bulk_export.py --batch 3         # run one batch
  python 07_screener_bulk_export.py --all             # download all 13 batches
  python 07_screener_bulk_export.py --update-json     # all batches + merge into JSON files
  python 07_screener_bulk_export.py --merge           # merge already-downloaded batches into JSONs

Output:
  data/structured/screener_batch_N_<name>.csv   (one per batch, cached)
  data/structured/<SYMBOL>_screener.json         (updated with screener_metrics key)
"""

import os, re, json, time, logging, argparse
from pathlib import Path
from io import StringIO
from unicodedata import normalize as unic_norm

import requests
from bs4 import BeautifulSoup
import pandas as pd
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ROOT       = Path(__file__).parent.parent
STRUCT_DIR = ROOT / "data" / "structured"
STRUCT_DIR.mkdir(parents=True, exist_ok=True)

BASE       = "https://www.screener.in"
SESSION_ID = os.getenv("SCREENER_SESSION_ID", "")

DELAY_BETWEEN_BATCHES = 3.0   # seconds between batch requests


# ── Session ───────────────────────────────────────────────────────────────────

def get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer":         "https://www.screener.in/",
        "Accept-Language": "en-US,en;q=0.9",
    })
    if SESSION_ID:
        s.cookies.set("sessionid", SESSION_ID, domain=".screener.in")
        log.info("Session cookie loaded — authenticated mode")
    else:
        log.warning("No SCREENER_SESSION_ID — running unauthenticated (only 9 metrics visible)")
    return s


# ── Column batches ─────────────────────────────────────────────────────────────
# 13 batches of ~20-30 columns each (screener rejects too many columns at once).
# Column names must exactly match screener.in's metric labels.

COLUMN_BATCHES = {
    1: {
        "name": "PL_Recent_A",
        "cols": [
            "Sales", "OPM", "Profit after tax", "EPS", "Return on capital employed",
            "Operating profit", "Interest", "Depreciation", "Net profit", "Other income",
            "Current Tax", "Tax", "EBIT",
            "Sales last year", "Operating profit last year", "Other income last year",
            "EBIDT last year", "Depreciation last year", "EBIT last year",
            "Interest last year", "Profit before tax last year", "Tax last year",
            "Profit after tax last year", "Extraordinary items last year",
            "Net Profit last year", "EPS last year", "OPM last year",
        ],
    },
    2: {
        "name": "PL_Recent_B",
        "cols": [
            "NPM last year", "Material cost last year", "Employee cost last year",
            "Dividend last year", "Change in promoter holding",
            "TTM Result Date", "Last annual result date",
            "Sales preceding year", "Operating profit preceding year",
            "Other income preceding year", "EBIDT preceding year",
            "Depreciation preceding year", "EBIT preceding year",
            "Interest preceding year", "Profit before tax preceding year",
            "Tax preceding year", "Profit after tax preceding year",
            "Extraordinary items preceding year", "Net Profit preceding year",
            "Dividend preceding year", "OPM preceding year", "NPM preceding year",
            "EPS preceding year", "Sales preceding 12months", "Net profit preceding 12months",
        ],
    },
    3: {
        "name": "PL_Historical",
        "cols": [
            "Sales growth 3Years", "Sales growth 5Years",
            "Sales growth 7Years", "Sales growth 10Years",
            "Profit growth 3Years", "Profit growth 5Years",
            "Profit growth 7Years", "Profit growth 10Years",
            "Sales growth 5years median", "Sales growth 10years median",
            "EBIDT growth 3Years", "EBIDT growth 5Years",
            "EBIDT growth 7Years", "EBIDT growth 10Years",
            "EPS growth 3Years", "EPS growth 5Years",
            "EPS growth 7Years", "EPS growth 10Years",
            "Change in promoter holding 3Years",
            "Average Earnings 5Year", "Average Earnings 10Year",
            "Average EBIT 5Year", "Average EBIT 10Year",
        ],
    },
    4: {
        "name": "Quarterly_Recent_A",
        "cols": [
            "Sales latest quarter", "Profit after tax latest quarter",
            "YOY Quarterly sales growth", "YOY Quarterly profit growth",
            "Sales growth", "Profit growth",
            "Operating profit latest quarter", "Other income latest quarter",
            "EBIDT latest quarter", "Depreciation latest quarter",
            "EBIT latest quarter", "Interest latest quarter",
            "Profit before tax latest quarter", "Tax latest quarter",
            "Extraordinary items latest quarter", "Net Profit latest quarter",
            "GPM latest quarter", "OPM latest quarter", "NPM latest quarter",
            "Equity Capital latest quarter", "EPS latest quarter",
            "Operating profit 2quarters back", "Operating profit 3quarters back",
            "Sales 2quarters back", "Sales 3quarters back",
            "Net profit 2quarters back", "Net profit 3quarters back",
        ],
    },
    5: {
        "name": "Quarterly_Recent_B",
        "cols": [
            "Operating profit growth", "Last result date",
            "Expected quarterly sales growth", "Expected quarterly sales",
            "Expected quarterly operating profit", "Expected quarterly net profit",
            "Expected quarterly EPS",
            "Sales preceding quarter", "Operating profit preceding quarter",
            "Other income preceding quarter", "EBIDT preceding quarter",
            "Depreciation preceding quarter", "EBIT preceding quarter",
            "Interest preceding quarter", "Profit before tax preceding quarter",
            "Tax preceding quarter", "Profit after tax preceding quarter",
            "Extraordinary items preceding quarter", "Net Profit preceding quarter",
            "OPM preceding quarter", "NPM preceding quarter",
            "Equity Capital preceding quarter", "EPS preceding quarter",
        ],
    },
    6: {
        "name": "Quarterly_Historical",
        "cols": [
            "Sales preceding year quarter",
            "Operating profit preceding year quarter",
            "Other income preceding year quarter",
            "EBIDT preceding year quarter",
            "Depreciation preceding year quarter",
            "EBIT preceding year quarter",
            "Interest preceding year quarter",
            "Profit before tax preceding year quarter",
            "Tax preceding year quarter",
            "Profit after tax preceding year quarter",
            "Extraordinary items preceding year quarter",
            "Net Profit preceding year quarter",
            "OPM preceding year quarter",
            "NPM preceding year quarter",
            "Equity Capital preceding year quarter",
            "EPS preceding year quarter",
        ],
    },
    7: {
        "name": "BalanceSheet_Recent",
        "cols": [
            "Debt", "Equity capital", "Preference capital", "Reserves",
            "Secured loan", "Unsecured loan", "Balance sheet total",
            "Gross block", "Revaluation reserve", "Accumulated depreciation",
            "Net block", "Capital work in progress", "Investments",
            "Current assets", "Current liabilities",
            "Book value of unquoted investments",
            "Market value of quoted investments",
            "Contingent liabilities", "Total Assets", "Working capital",
            "Lease liabilities", "Inventory", "Trade receivables",
            "Face value", "Cash Equivalents", "Advance from Customers", "Trade Payables",
        ],
    },
    8: {
        "name": "BalanceSheet_Preceding_Historical",
        "cols": [
            "Number of equity shares preceding year",
            "Debt preceding year", "Working capital preceding year",
            "Net block preceding year", "Gross block preceding year",
            "Capital work in progress preceding year",
            "Working capital 3Years back", "Working capital 5Years back",
            "Working capital 7Years back", "Working capital 10Years back",
            "Debt 3Years back", "Debt 5Years back",
            "Debt 7Years back", "Debt 10Years back",
            "Net block 3Years back", "Net block 5Years back", "Net block 7Years back",
        ],
    },
    9: {
        "name": "CashFlow",
        "cols": [
            "Cash from operations last year", "Free cash flow last year",
            "Cash from investing last year", "Cash from financing last year",
            "Net cash flow last year",
            "Cash beginning of last year", "Cash end of last year",
            "Free cash flow preceding year", "Cash from operations preceding year",
            "Cash from investing preceding year", "Cash from financing preceding year",
            "Net cash flow preceding year",
            "Cash beginning of preceding year", "Cash end of preceding year",
            "Free cash flow 3years", "Free cash flow 5years",
            "Free cash flow 7years", "Free cash flow 10years",
            "Operating cash flow 3years", "Operating cash flow 5years",
            "Operating cash flow 7years", "Operating cash flow 10years",
            "Investing cash flow 3years", "Investing cash flow 5years",
            "Investing cash flow 7years", "Investing cash flow 10years",
            "Cash 3Years back", "Cash 5Years back", "Cash 7Years back",
        ],
    },
    10: {
        "name": "Ratios_Valuation_A",
        "cols": [
            "Market Capitalization", "Price to Earning", "Dividend yield",
            "Price to book value", "Return on assets", "Debt to equity",
            "Return on equity", "Promoter holding", "Earnings yield",
            "Pledged percentage", "Industry PE", "Enterprise Value",
            "Number of equity shares", "Price to Quarterly Earning",
            "Book value", "Inventory turnover ratio", "Quick ratio",
            "Exports percentage", "Piotroski score", "G Factor",
            "Asset Turnover Ratio", "Financial leverage",
            "Number of Shareholders", "Unpledged promoter holding",
            "Return on invested capital", "Debtor days", "Industry PBV",
            "Credit rating", "Working Capital Days",
        ],
    },
    11: {
        "name": "Ratios_Valuation_B",
        "cols": [
            "Earning Power", "Graham Number", "Cash Conversion Cycle",
            "Days Payable Outstanding", "Days Receivable Outstanding",
            "Days Inventory Outstanding",
            "Public holding", "FII holding", "Change in FII holding",
            "DII holding", "Change in DII holding",
            "Price to Sales", "Price to Free Cash Flow", "EVEBITDA",
            "Current ratio", "Interest Coverage Ratio", "PEG Ratio",
            "Book value preceding year",
            "Return on capital employed preceding year",
            "Return on assets preceding year",
            "Return on equity preceding year",
            "Number of Shareholders preceding quarter",
            "Average return on equity 5Years", "Average return on equity 3Years",
            "OPM 5Year", "OPM 10Year",
            "Average return on capital employed 3Years",
            "Average return on capital employed 5Years",
            "Altman Z Score",
        ],
    },
    12: {
        "name": "Ratios_Historical",
        "cols": [
            "Number of equity shares 10years back",
            "Book value 3years back", "Book value 5years back", "Book value 10years back",
            "Inventory turnover ratio 3Years back", "Inventory turnover ratio 5Years back",
            "Inventory turnover ratio 7Years back", "Inventory turnover ratio 10Years back",
            "Exports percentage 3Years back", "Exports percentage 5Years back",
            "Average 5years dividend", "Average dividend payout 3years",
            "Average return on capital employed 7Years",
            "Average return on capital employed 10Years",
            "Average return on equity 10Years", "Average return on equity 7Years",
            "Return on equity 5years growth",
            "Number of Shareholders 1year back",
            "Average debtor days 3years",
            "Debtor days 3years back", "Debtor days 5years back",
            "Return on assets 5years", "Return on assets 3years",
            "Historical PE 3Years", "Historical PE 5Years",
            "Historical PE 7Years", "Historical PE 10Years",
            "Market Capitalization 3years back", "Market Capitalization 5years back",
            "Market Capitalization 7years back", "Market Capitalization 10years back",
            "Average Working Capital Days 3years",
            "Change in FII holding 3Years", "Change in DII holding 3Years",
        ],
    },
    13: {
        "name": "Price_Technical",
        "cols": [
            "Current price",
            "Return over 3months", "Return over 6months",
            "Is SME", "Is not SME",
            "Volume 1month average", "Volume 1week average", "Volume",
            "High price", "Low price",
            "High price all time", "Low price all time",
            "Return over 1day", "Return over 1week", "Return over 1month",
            "DMA 50", "DMA 200", "DMA 50 previous day", "DMA 200 previous day",
            "RSI", "MACD", "MACD Previous Day", "MACD Signal", "MACD Signal Previous Day",
            "Return over 1year", "Return over 3years", "Return over 5years",
            "Volume 1year average", "Return over 7years", "Return over 10years",
        ],
    },
}

ALL_COLS = sorted({c for b in COLUMN_BATCHES.values() for c in b["cols"]})


# ── Download helpers ──────────────────────────────────────────────────────────

def _batch_csv_path(batch_num: int) -> Path:
    name = COLUMN_BATCHES[batch_num]["name"]
    return STRUCT_DIR / f"screener_batch_{batch_num:02d}_{name}.csv"


def _fetch_export(session: requests.Session, cols: list[str],
                  page: int = 1, retries: int = 4) -> pd.DataFrame | None:
    """
    Try CSV export first (returns all companies in one shot when authenticated).
    Falls back to HTML table scraping if CSV fails.
    """
    col_str  = ",".join(cols)
    base_qs  = f"?q=&sort=Market+Capitalization&order=desc"

    # ── CSV export (authenticated: returns full list at once) ─────────────────
    for attempt in range(retries):
        try:
            url = f"{BASE}/screens/equity/{base_qs}&export=csv&columns={col_str}"
            r   = session.get(url, timeout=60)

            if r.status_code == 200 and (
                "text/csv" in r.headers.get("content-type", "") or
                r.text.strip().startswith("S.No.,")
            ):
                df = pd.read_csv(StringIO(r.text))
                log.info(f"  CSV export: {df.shape}")
                return df

            # Rate-limited
            if r.status_code in (429, 503):
                wait = [60, 120, 300, 600][min(attempt, 3)]
                log.warning(f"  Rate-limited (HTTP {r.status_code}) — sleeping {wait}s")
                time.sleep(wait)
                continue

            # Not CSV — fall through to HTML scraping
            break

        except Exception as e:
            log.warning(f"  CSV export attempt {attempt+1} failed: {e}")
            time.sleep(5 * (attempt + 1))

    # ── HTML table scraping (paginated) ───────────────────────────────────────
    all_dfs: list[pd.DataFrame] = []
    headers: list[str] = []

    for pg in range(1, 200):
        try:
            url  = f"{BASE}/screens/equity/{base_qs}&page={pg}&columns={col_str}"
            r    = session.get(url, timeout=30)
            soup = BeautifulSoup(r.text, "lxml")

            table = (
                soup.select_one("table.data-table") or
                soup.select_one("#data-table") or
                soup.find("table")
            )
            if not table:
                break

            if not headers:
                headers = [th.get_text(strip=True) for th in table.find_all("th")]

            rows = []
            for tr in (table.find("tbody") or table).find_all("tr"):
                cells = [td.get_text(strip=True) for td in tr.find_all("td")]
                if cells:
                    rows.append(cells)
            if not rows:
                break

            df = pd.DataFrame(rows, columns=headers[:len(rows[0])] if headers else None)
            all_dfs.append(df)
            log.info(f"  HTML page {pg}: {len(df)} rows")
            time.sleep(0.8)

        except Exception as e:
            log.warning(f"  HTML page {pg} failed: {e}")
            break

    if all_dfs:
        return pd.concat(all_dfs, ignore_index=True)
    return None


def run_batch(batch_num: int, session: requests.Session,
              test: bool = False, force: bool = False) -> pd.DataFrame:
    """Download one column batch and save as CSV. Returns DataFrame."""
    out_path = _batch_csv_path(batch_num)

    if out_path.exists() and not force:
        log.info(f"Batch {batch_num} cached → {out_path.name}")
        return pd.read_csv(out_path, low_memory=False)

    batch = COLUMN_BATCHES[batch_num]
    log.info(f"=== Batch {batch_num:02d}: {batch['name']} ({len(batch['cols'])} cols) ===")

    cols = batch["cols"] if not test else batch["cols"][:5]
    df   = _fetch_export(session, cols)

    if df is None or df.empty:
        log.error(f"Batch {batch_num}: no data returned")
        return pd.DataFrame()

    df.to_csv(out_path, index=False)
    log.info(f"Batch {batch_num} saved: {df.shape} → {out_path.name}")
    return df


# ── Name normalisation for matching ──────────────────────────────────────────

_STRIP = re.compile(
    r"\b(ltd|limited|pvt|private|co|company|corp|corporation|inc|"
    r"llp|llc|enterprises|industries|holdings|group|and|&)\b",
    re.I,
)

def _norm(name: str) -> str:
    if not name or not isinstance(name, str):
        return ""
    s = unic_norm("NFKD", name).encode("ascii", "ignore").decode()
    s = s.lower()
    s = _STRIP.sub(" ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ── Company lookup (name → JSON path) ────────────────────────────────────────

def build_lookup() -> tuple[dict[str, Path], dict[str, Path]]:
    """
    Returns:
      name_lookup  : {normalised_company_name → json_path}
      sym_lookup   : {nse_symbol_upper → json_path, bse_code → json_path}
    """
    name_lk: dict[str, Path] = {}
    sym_lk:  dict[str, Path] = {}

    for jp in STRUCT_DIR.glob("*_screener.json"):
        try:
            with open(jp, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue

        cn  = d.get("company_name", "")
        nse = d.get("nse_symbol") or ""
        bse = str(d.get("bse_code") or "")

        key = _norm(cn)
        if key:
            name_lk[key] = jp
        if nse:
            sym_lk[nse.upper().strip()] = jp
        if bse:
            sym_lk[bse.strip()] = jp

    log.info(f"Lookup built: {len(name_lk)} names, {len(sym_lk)} symbols")
    return name_lk, sym_lk


def _resolve(row_name: str, name_lk: dict, sym_lk: dict) -> Path | None:
    # 1. Exact normalised name match
    key = _norm(row_name)
    if key in name_lk:
        return name_lk[key]
    # 2. Symbol match (useful when Name col is actually the NSE ticker)
    upper = row_name.strip().upper()
    if upper in sym_lk:
        return sym_lk[upper]
    # 3. Partial: if the key is a substring of a known name (last resort)
    for known, path in name_lk.items():
        if key and (key in known or known in key):
            return path
    return None


# ── Merge batch CSVs into per-company JSON files ──────────────────────────────

def _name_col(df: pd.DataFrame) -> str | None:
    """Find the company-name column in a batch DataFrame."""
    for c in df.columns:
        cl = c.lower().strip()
        if cl in ("name", "company", "company name", "company_name"):
            return c
    # Heuristic: second column after S.No. is usually Name
    if len(df.columns) >= 2:
        return df.columns[1]
    return None


def merge_batches_into_json(force_reload: bool = False) -> dict:
    """
    Read every cached batch CSV, match rows to existing *_screener.json files,
    and write/update the `screener_metrics` key in each JSON.
    Returns a summary dict.
    """
    name_lk, sym_lk = build_lookup()

    # Accumulate all metrics per JSON path
    pending: dict[Path, dict[str, str]] = {}

    for batch_num in range(1, len(COLUMN_BATCHES) + 1):
        csv_path = _batch_csv_path(batch_num)
        if not csv_path.exists():
            log.warning(f"Batch {batch_num} CSV not found — skipping")
            continue

        df = pd.read_csv(csv_path, low_memory=False)
        name_col = _name_col(df)
        if name_col is None:
            log.warning(f"Batch {batch_num}: cannot identify Name column — skipping")
            continue

        # Columns that contain metric data (not S.No. or Name)
        metric_cols = [c for c in df.columns if c not in (
            "S.No.", "S.No", name_col, "Unnamed: 0"
        )]

        matched = unmatched = 0
        for _, row in df.iterrows():
            row_name = str(row.get(name_col, "")).strip()
            if not row_name or row_name.lower() in ("nan", ""):
                continue

            jp = _resolve(row_name, name_lk, sym_lk)
            if jp is None:
                unmatched += 1
                continue

            metrics = pending.setdefault(jp, {})
            for col in metric_cols:
                val = row.get(col)
                if pd.notna(val) and str(val).strip() not in ("", "-", "nan"):
                    metrics[col] = str(val).strip()
            matched += 1

        log.info(
            f"Batch {batch_num:02d}: matched={matched} unmatched={unmatched}"
        )

    # Write updates to JSON files
    written = skipped = 0
    for jp, metrics in pending.items():
        if not metrics:
            continue
        try:
            with open(jp, encoding="utf-8") as f:
                data = json.load(f)
            existing = data.get("screener_metrics", {})
            existing.update(metrics)
            data["screener_metrics"] = existing
            with open(jp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            written += 1
        except Exception as e:
            log.warning(f"Failed to update {jp.name}: {e}")
            skipped += 1

    total_metrics = sum(len(m) for m in pending.values())
    log.info(
        f"Merge done: {written} JSONs updated, {skipped} skipped, "
        f"{total_metrics:,} metric-values written"
    )
    return {"written": written, "skipped": skipped, "total_metrics": total_metrics}


# ── Verify ────────────────────────────────────────────────────────────────────

def verify():
    """Quick check: how many JSONs have screener_metrics and how many are empty."""
    total = with_data = no_data = 0
    metric_counts: list[int] = []

    for jp in STRUCT_DIR.glob("*_screener.json"):
        total += 1
        try:
            with open(jp, encoding="utf-8") as f:
                d = json.load(f)
            sm = d.get("screener_metrics", {})
            if sm:
                with_data += 1
                metric_counts.append(len(sm))
            else:
                no_data += 1
        except Exception:
            no_data += 1

    avg = sum(metric_counts) / len(metric_counts) if metric_counts else 0
    print(f"\n{'='*55}")
    print(f"  SCREENER METRICS STATUS")
    print(f"{'='*55}")
    print(f"  Total JSON files       : {total:,}")
    print(f"  Have screener_metrics  : {with_data:,}")
    print(f"  Missing screener_metrics: {no_data:,}")
    print(f"  Avg metrics per company: {avg:.0f}")
    print(f"  Total metric columns   : {len(ALL_COLS)}")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Screener.in bulk metrics downloader")
    p.add_argument("--test",        action="store_true",
                   help="Quick test: batch 1 only (no caching)")
    p.add_argument("--batch",       type=int, default=None,
                   help="Download a single batch (1-13)")
    p.add_argument("--all",         action="store_true",
                   help="Download all 13 batches")
    p.add_argument("--update-json", action="store_true",
                   help="Download all batches then merge into per-company JSONs")
    p.add_argument("--merge",       action="store_true",
                   help="Merge already-downloaded batch CSVs into per-company JSONs")
    p.add_argument("--force",       action="store_true",
                   help="Re-download even if batch CSV already exists")
    p.add_argument("--verify",      action="store_true",
                   help="Show how many JSONs have screener_metrics")
    args = p.parse_args()

    if not SESSION_ID:
        print("\nWARNING: SCREENER_SESSION_ID not set in .env — most metrics will be empty.\n")

    session = get_session()

    if args.verify:
        verify()

    elif args.test:
        print("TEST MODE: batch 1 only")
        df = run_batch(1, session, test=True, force=True)
        print(df.head(3).to_string())
        print(f"\nColumns: {list(df.columns)}")
        print(f"Rows: {len(df)}")

    elif args.batch is not None:
        if args.batch not in COLUMN_BATCHES:
            print(f"Batch {args.batch} not found. Valid: 1-{len(COLUMN_BATCHES)}")
        else:
            df = run_batch(args.batch, session, force=args.force)
            print(f"\nBatch {args.batch}: {df.shape}")
            if not df.empty:
                print(df.head(3).to_string())

    elif args.all:
        for i in sorted(COLUMN_BATCHES):
            run_batch(i, session, force=args.force)
            time.sleep(DELAY_BETWEEN_BATCHES)
        log.info("All batches done.")

    elif args.update_json:
        for i in sorted(COLUMN_BATCHES):
            run_batch(i, session, force=args.force)
            time.sleep(DELAY_BETWEEN_BATCHES)
        merge_batches_into_json()
        verify()

    elif args.merge:
        merge_batches_into_json()
        verify()

    else:
        print(__doc__)
        print(f"Session: {'ACTIVE (authenticated)' if SESSION_ID else 'NOT SET'}")
        print(f"\nBatches ({len(COLUMN_BATCHES)} total, {len(ALL_COLS)} unique metrics):")
        for k, v in COLUMN_BATCHES.items():
            done = _batch_csv_path(k).exists()
            status = "✓" if done else " "
            print(f"  [{status}] {k:2d}: {v['name']:35s} ({len(v['cols'])} metrics)")
