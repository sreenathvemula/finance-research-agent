#!/usr/bin/env python3
"""
08_screener_metrics.py  -  Build screener_metrics for all company JSON files.

Three data sources are merged into a flat `screener_metrics` dict in each
*_screener.json file:

  1. screen/raw HTML scraping  -- price, returns, PE, growth (16 metrics)
  2. /api/company/{id}/quick_ratios/  -- Piotroski, G Factor, Industry PE, etc.
  3. Existing JSON financial tables  -- P&L, quarterly, balance sheet, cash flow

Usage
-----
  python 08_screener_metrics.py --screen      # Phase 1: scrape screen/raw (107 req)
  python 08_screener_metrics.py --ratios      # Phase 2: fetch quick_ratios (~6700 req)
  python 08_screener_metrics.py --build       # Phase 3: extract+merge → write JSON
  python 08_screener_metrics.py --all         # Phases 1+2+3 in sequence
  python 08_screener_metrics.py --verify N    # Show screener_metrics for company N
"""

import os, re, json, time, sys, argparse, logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ── paths ──────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
STRUCTURED = ROOT / "data" / "structured"
CACHE_DIR = ROOT / "data" / "screener_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

SCREEN_CACHE = CACHE_DIR / "screen_raw.json"
RATIOS_CACHE     = CACHE_DIR / "quick_ratios.json"
SCREEN_EXT_CACHE = CACHE_DIR / "screen_raw_extended.json"
IDS_CACHE        = CACHE_DIR / "company_ids.json"

load_dotenv(ROOT / ".env")
SID = os.getenv("SCREENER_SESSION_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

# ── HTTP session ───────────────────────────────────────────────────────────────
def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0",
        "Referer": "https://www.screener.in/screen/raw/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    if SID:
        s.cookies.set("sessionid", SID, domain=".screener.in")
    # prime CSRF cookie
    try:
        s.get("https://www.screener.in/", timeout=20)
    except Exception:
        pass
    return s


# ── helpers ────────────────────────────────────────────────────────────────────
def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def save_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_num(val):
    if val is None:
        return None
    v = str(val).replace(",", "").replace("%", "").strip()
    try:
        return float(v)
    except ValueError:
        return None


def cagr(current_raw, start_raw, years: int):
    c, s = parse_num(current_raw), parse_num(start_raw)
    if c is None or s is None or s == 0 or years <= 0:
        return None
    ratio = c / s
    if ratio <= 0:
        return None
    return round((ratio ** (1 / years) - 1) * 100, 2)


# ── Phase 1: scrape screen/raw pages ─────────────────────────────────────────
BASE_SCREEN = "https://www.screener.in/screen/raw/"
SCREEN_QUERY = "query=Market+Capitalization+%3E+0&sort=Market+Capitalization&order=desc"

# Extra column batches for Phase 1b — names must match screener's internal column identifiers
EXTRA_COLUMN_BATCHES = [
    # Balance sheet breakdown (condensed BS table lacks these)
    ["Inventory", "Trade Receivables", "Trade Payables", "Cash Equivalents",
     "Current Assets", "Current Liabilities", "Working Capital"],
    # More BS items
    ["Gross Block", "Secured Borrowings", "Unsecured Borrowings",
     "Revaluation reserve", "Number of equity shares"],
    # Short/medium price returns
    ["Return over 1week", "Return over 1month",
     "Return over 3months", "Return over 6months"],
    # Long price returns and all-time extremes
    ["Return over 7years", "Return over 10years",
     "High price all time", "Low price all time"],
    # Technical: moving averages + RSI
    ["DMA 50", "DMA 200", "DMA 50 previous day", "DMA 200 previous day", "RSI"],
    # Technical: volume
    ["Volume", "Volume 1week average", "Volume 1month average", "Volume 1year average"],
    # Historical PE
    ["Historical PE 3Years", "Historical PE 5Years",
     "Historical PE 7Years", "Historical PE 10Years"],
    # Market cap history
    ["Market Capitalization 3years back", "Market Capitalization 5years back",
     "Market Capitalization 7years back", "Market Capitalization 10years back"],
    # Book value + working capital history
    ["Book value 3years back", "Book value 5years back", "Book value 10years back",
     "Working capital 3Years back", "Working capital 5Years back"],
    # Screening flags and ratios
    ["Pledged percentage", "Unpledged promoter holding", "Exports percentage",
     "Inventory turnover ratio", "Number of Shareholders", "Is SME"],
    # Historical averages
    ["Return on assets 3years", "Return on assets 5years",
     "Return on equity 5years growth", "Average Working Capital Days 3years"],
    # Dividends and inventory history
    ["Average 5years dividend", "Average dividend payout 3years",
     "Inventory turnover ratio 3Years back", "Inventory turnover ratio 5Years back"],
    # MACD signals
    ["MACD", "MACD Previous Day", "MACD Signal", "MACD Signal Previous Day"],
    # Misc
    ["Working capital 7Years back", "Working capital 10Years back",
     "Number of Shareholders 1year back", "Price to Quarterly Earning",
     "Number of equity shares preceding year"],
]


def parse_screen_page(html: str) -> list[dict]:
    """Parse one screen/raw page; returns list of {symbol, metric_name: value, ...}."""
    soup = BeautifulSoup(html, "lxml")
    t = soup.find("table")
    if not t:
        return []
    tbody = t.find("tbody")
    if not tbody:
        return []
    rows = tbody.find_all("tr")
    if not rows:
        return []

    # header row (first tr in tbody)
    header_cells = rows[0].find_all(["td", "th"])
    headers = [c.get("data-tooltip") or c.get_text(strip=True) for c in header_cells]

    records = []
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) < 3:
            continue
        # extract symbol from Name cell (cells[1])
        name_cell = cells[1]
        a = name_cell.find("a")
        if not a:
            continue
        href = a.get("href", "")
        m = re.match(r"/company/([^/]+)/", href)
        if not m:
            continue
        symbol = m.group(1)

        rec = {"symbol": symbol}
        for i, cell in enumerate(cells):
            if i < len(headers) and headers[i] and headers[i] != "S.No." and headers[i] != "Name":
                rec[headers[i]] = cell.get_text(strip=True)
        records.append(rec)
    return records


def phase1_scrape_screen(force: bool = False):
    """Scrape all screen/raw pages and cache to SCREEN_CACHE."""
    if not force and SCREEN_CACHE.exists():
        log.info("screen/raw cache exists, skipping (use --force to re-scrape)")
        return load_json(SCREEN_CACHE, {})

    log.info("Phase 1: scraping screen/raw pages ...")
    s = make_session()
    screen_data: dict[str, dict] = {}  # symbol → {metric: value}

    page = 1
    consecutive_empty = 0
    while True:
        url = f"{BASE_SCREEN}?{SCREEN_QUERY}&page={page}"
        try:
            r = s.get(url, timeout=30)
            records = parse_screen_page(r.text)
        except Exception as e:
            log.warning(f"  page {page} error: {e}")
            records = []

        if not records:
            consecutive_empty += 1
            if consecutive_empty >= 3:
                break
            page += 1
            continue

        consecutive_empty = 0
        for rec in records:
            sym = rec.pop("symbol")
            screen_data[sym] = rec
        log.info(f"  page {page}: {len(records)} companies (total {len(screen_data)})")
        page += 1
        time.sleep(0.3)  # polite delay

    log.info(f"Phase 1 done: {len(screen_data)} companies from screen/raw")
    save_json(SCREEN_CACHE, screen_data)
    return screen_data


# ── Phase 1b: screen/raw with extra columns ──────────────────────────────────
def phase1b_scrape_extra_columns(force: bool = False):
    """Scrape screen/raw pages with extra column batches (balance sheet, technical, historical)."""
    existing: dict = {} if force else load_json(SCREEN_EXT_CACHE, {})

    log.info("Phase 1b: scraping screen/raw extra column batches ...")
    s = make_session()

    for batch_idx, batch_cols in enumerate(EXTRA_COLUMN_BATCHES):
        cols_str = ",".join(batch_cols)
        log.info(f"  batch {batch_idx + 1}/{len(EXTRA_COLUMN_BATCHES)}: {batch_cols}")

        page = 1
        consecutive_empty = 0
        batch_rows = 0

        while True:
            try:
                r = s.get(BASE_SCREEN, params={
                    "query": "Market Capitalization > 0",
                    "sort": "Market Capitalization",
                    "order": "desc",
                    "columns": cols_str,
                    "page": str(page),
                }, timeout=30)
                records = parse_screen_page(r.text)
            except Exception as e:
                log.warning(f"  batch {batch_idx+1} page {page} error: {e}")
                records = []

            if not records:
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    break
                page += 1
                continue

            consecutive_empty = 0
            for rec in records:
                sym = rec.pop("symbol", None)
                if sym:
                    existing.setdefault(sym, {}).update(
                        {k: v for k, v in rec.items() if v and v != ""}
                    )
            batch_rows += len(records)
            page += 1
            time.sleep(0.3)

        save_json(SCREEN_EXT_CACHE, existing)
        log.info(f"  batch {batch_idx + 1} done: {batch_rows} company-rows, "
                 f"{len(existing)} unique companies")

    log.info(f"Phase 1b done: {len(existing)} companies in extended cache")
    return existing


# ── Phase 2: quick_ratios API ──────────────────────────────────────────────────
SEARCH_API   = "https://www.screener.in/api/company/search/"
RATIOS_API   = "https://www.screener.in/api/company/{id}/quick_ratios/"
MAX_WORKERS  = 12
RATE_DELAY   = 0.10   # seconds between requests per thread


def search_company(session: requests.Session, symbol: str) -> int | None:
    """Return screener numeric company ID for a given symbol, or None."""
    try:
        r = session.get(SEARCH_API, params={"q": symbol}, timeout=10)
        if r.status_code != 200:
            return None
        results = r.json()
        if not results:
            return None
        # Prefer exact match on URL slug
        for res in results:
            slug = re.search(r"/company/([^/]+)/", res.get("url", ""))
            if slug and slug.group(1).upper() == symbol.upper():
                return res["id"]
        return results[0]["id"]
    except Exception:
        return None


def fetch_quick_ratios(session: requests.Session, company_id: int) -> dict:
    """Fetch 18 quick ratios for a company from screener API."""
    try:
        url = RATIOS_API.format(id=company_id)
        r = session.get(url, timeout=10)
        if r.status_code != 200:
            return {}
        soup = BeautifulSoup(r.text, "lxml")
        ratios = {}
        for li in soup.find_all("li"):
            txt = li.get_text(strip=True)
            # Format: "PEG Ratio-3.57" or "Piotroski score7.00"
            # Try splitting on last number-like portion
            m = re.match(r"^(.*?)(-?\d[\d,.]*)$", txt)
            if m:
                name = m.group(1).strip()
                val  = m.group(2).strip()
                if name:
                    ratios[name] = val
        return ratios
    except Exception:
        return {}


def _fetch_one(args):
    sym, session = args
    time.sleep(RATE_DELAY)
    cid = search_company(session, sym)
    if cid is None:
        return sym, None
    time.sleep(RATE_DELAY)
    ratios = fetch_quick_ratios(session, cid)
    return sym, ratios


def phase2_fetch_ratios(symbols: list[str], force: bool = False):
    """Fetch quick_ratios for all symbols; cache results."""
    existing: dict = load_json(RATIOS_CACHE, {})

    if not force:
        missing = [s for s in symbols if s not in existing]
    else:
        missing = symbols

    if not missing:
        log.info(f"quick_ratios cache complete ({len(existing)} companies)")
        return existing

    log.info(f"Phase 2: fetching quick_ratios for {len(missing)} companies "
             f"({len(existing)} already cached) ...")
    # Use a pool of sessions to avoid sharing
    sessions = [make_session() for _ in range(MAX_WORKERS)]

    done = 0
    errors = 0
    batch_size = 50

    for batch_start in range(0, len(missing), batch_size):
        batch = missing[batch_start:batch_start + batch_size]
        # Distribute sessions round-robin
        args_list = [(sym, sessions[i % MAX_WORKERS]) for i, sym in enumerate(batch)]

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as exe:
            futs = {exe.submit(_fetch_one, a): a[0] for a in args_list}
            for fut in as_completed(futs):
                sym = futs[fut]
                try:
                    _, ratios = fut.result()
                    if ratios is not None:
                        existing[sym] = ratios
                    else:
                        existing.setdefault(sym, {})
                    done += 1
                except Exception as e:
                    log.warning(f"  {sym}: {e}")
                    errors += 1

        # Save checkpoint after each batch
        save_json(RATIOS_CACHE, existing)
        pct = (batch_start + len(batch)) / len(missing) * 100
        log.info(f"  {batch_start + len(batch)}/{len(missing)} ({pct:.0f}%) — "
                 f"{done} ok, {errors} errors")

    log.info(f"Phase 2 done: {done} companies fetched, {errors} errors")
    return existing


# ── Phase 3: extract metrics from existing JSON tables ─────────────────────────
def tbl_val(table: dict, metric_pattern: str, col_offset: int = -1,
            use_category: bool = False) -> str | None:
    """
    Get value from a financial table.
    table  = dict with 'rows' and 'columns' keys
    metric_pattern = substring to match against row['metric'] (or row['category'])
    col_offset = index into columns list (-1 = latest)
    """
    if not table or "rows" not in table or "columns" not in table:
        return None
    cols = table["columns"]
    if not cols:
        return None
    try:
        col = cols[col_offset]
    except IndexError:
        return None
    key = "category" if use_category else "metric"
    for row in table.get("rows", []):
        row_key = row.get(key, "")
        if metric_pattern.lower() in row_key.lower():
            val = row.get(col, "")
            return val if val != "" else None
    return None


def tbl_val_raw(table: dict, metric_pattern: str, col_offset: int) -> str | None:
    """Same as tbl_val but also works when col_offset would be out of range."""
    if not table or "rows" not in table or "columns" not in table:
        return None
    cols = table["columns"]
    n = len(cols)
    if n == 0:
        return None
    # compute actual index
    idx = n + col_offset if col_offset < 0 else col_offset
    if idx < 0 or idx >= n:
        return None
    col = cols[idx]
    for row in table.get("rows", []):
        if metric_pattern.lower() in row.get("metric", "").lower():
            val = row.get(col, "")
            return val if val != "" else None
    return None


def extract_table_metrics(jdata: dict) -> dict:
    """
    Extract all available screener metric names → raw values from the
    existing JSON financial tables (consolidated preferred, else standalone).
    """
    m: dict = {}

    def section(key):
        c = jdata.get("consolidated", {}).get(key, {})
        s = jdata.get("standalone", {}).get(key, {})
        # Always prefer consolidated when it has actual data
        if c and c.get("columns") and c.get("rows"):
            return c
        return s

    pl  = section("profit_loss")
    qr  = section("quarterly_results")
    bs  = section("balance_sheet")
    cf  = section("cash_flow")
    rat = section("ratios")
    sh  = section("shareholding")
    sh_q = sh.get("quarterly", {}) if isinstance(sh, dict) else {}

    pl_cols = pl.get("columns", [])
    n_annual = len(pl_cols)

    # ── Profit & Loss: current year ──────────────────────────────────────────
    def pl_v(metric, offset=-1):
        return tbl_val_raw(pl, metric, offset)

    m["Sales"]              = pl_v("Sales+")
    m["Operating profit"]   = pl_v("Operating Profit")
    m["OPM"]                = pl_v("OPM %")
    m["Other income"]       = pl_v("Other Income")
    m["Depreciation"]       = pl_v("Depreciation")
    m["Interest"]           = pl_v("Interest")
    m["Profit after tax"]   = pl_v("Net Profit+")
    m["Net profit"]         = pl_v("Net Profit+")
    m["Tax"]                = pl_v("Tax+")
    m["Current Tax"]        = pl_v("Tax+")
    m["EPS"]                = pl_v("EPS in Rs")
    m["Dividend last year"] = pl_v("Dividend", -2)

    # EBIT ≈ Profit before tax + Interest
    pbt = parse_num(pl_v("Profit before tax"))
    interest = parse_num(pl_v("Interest"))
    if pbt is not None and interest is not None:
        m["EBIT"] = str(round(pbt + interest, 2))
    # EBIDT (EBITDA) ≈ Operating Profit + Depreciation (screener's definition)
    op = parse_num(pl_v("Operating Profit"))
    dep = parse_num(pl_v("Depreciation"))
    if op is not None and dep is not None:
        m["EBITDA"] = str(round(op + dep, 2))

    # Tax amount = PBT × Tax% / 100  (screener shows Tax % not amount)
    tax_pct = parse_num(pl_v("Tax %"))
    if pbt is not None and tax_pct is not None:
        m["Tax"] = str(round(pbt * tax_pct / 100, 2))
        m["Current Tax"] = m["Tax"]

    # ── P&L: last year (offset -2) ──────────────────────────────────────────
    m["Sales last year"]                = pl_v("Sales+", -2)
    m["Operating profit last year"]     = pl_v("Operating Profit", -2)
    m["OPM last year"]                  = pl_v("OPM %", -2)
    m["Other income last year"]         = pl_v("Other Income", -2)
    m["Depreciation last year"]         = pl_v("Depreciation", -2)
    m["EBIT last year"] = None
    pbt_ly = parse_num(pl_v("Profit before tax", -2))
    int_ly = parse_num(pl_v("Interest", -2))
    if pbt_ly is not None and int_ly is not None:
        m["EBIT last year"] = str(round(pbt_ly + int_ly, 2))
    op_ly = parse_num(pl_v("Operating Profit", -2))
    dep_ly = parse_num(pl_v("Depreciation", -2))
    if op_ly is not None and dep_ly is not None:
        m["EBIDT last year"] = str(round(op_ly + dep_ly, 2))
    m["Interest last year"]              = pl_v("Interest", -2)
    m["Profit before tax last year"]     = pl_v("Profit before tax", -2)
    m["Tax last year"]                   = pl_v("Tax+", -2)
    pbt_ly_num = parse_num(pl_v("Profit before tax", -2))
    tax_pct_ly = parse_num(pl_v("Tax %", -2))
    if pbt_ly_num is not None and tax_pct_ly is not None and m.get("Tax last year") is None:
        m["Tax last year"] = str(round(pbt_ly_num * tax_pct_ly / 100, 2))
    m["Profit after tax last year"]      = pl_v("Net Profit+", -2)
    m["Net Profit last year"]            = pl_v("Net Profit+", -2)
    m["EPS last year"]                   = pl_v("EPS in Rs", -2)
    m["NPM last year"]                   = None  # computed below

    # ── P&L: preceding year (offset -3) ────────────────────────────────────
    m["Sales preceding year"]            = pl_v("Sales+", -3)
    m["Operating profit preceding year"] = pl_v("Operating Profit", -3)
    m["OPM preceding year"]              = pl_v("OPM %", -3)
    m["Other income preceding year"]     = pl_v("Other Income", -3)
    m["Depreciation preceding year"]     = pl_v("Depreciation", -3)
    m["Interest preceding year"]         = pl_v("Interest", -3)
    m["Profit before tax preceding year"]= pl_v("Profit before tax", -3)
    m["Tax preceding year"]              = pl_v("Tax+", -3)
    pbt_py_num = parse_num(pl_v("Profit before tax", -3))
    tax_pct_py = parse_num(pl_v("Tax %", -3))
    if pbt_py_num is not None and tax_pct_py is not None and m.get("Tax preceding year") is None:
        m["Tax preceding year"] = str(round(pbt_py_num * tax_pct_py / 100, 2))
    m["Profit after tax preceding year"] = pl_v("Net Profit+", -3)
    m["Net Profit preceding year"]       = pl_v("Net Profit+", -3)
    m["EPS preceding year"]              = pl_v("EPS in Rs", -3)
    m["OPM preceding year"]              = pl_v("OPM %", -3)
    m["NPM preceding year"]              = None

    # EBIT and EBIDT for preceding year
    pbt_py3 = parse_num(pl_v("Profit before tax", -3))
    int_py3 = parse_num(pl_v("Interest", -3))
    op_py3  = parse_num(pl_v("Operating Profit", -3))
    dep_py3 = parse_num(pl_v("Depreciation", -3))
    if pbt_py3 is not None and int_py3 is not None:
        m["EBIT preceding year"] = str(round(pbt_py3 + int_py3, 2))
    if op_py3 is not None and dep_py3 is not None:
        m["EBIDT preceding year"] = str(round(op_py3 + dep_py3, 2))

    # compute NPM last year / preceding year
    for (suffix, s_offset, n_offset) in [
        ("last year", -2, -2),
        ("preceding year", -3, -3),
    ]:
        sales_raw = pl_v("Sales+", n_offset)
        np_raw    = pl_v("Net Profit+", n_offset)
        s_num, np_num = parse_num(sales_raw), parse_num(np_raw)
        if s_num and s_num != 0 and np_num is not None:
            m[f"NPM {suffix}"] = str(round(np_num / s_num * 100, 2))

    # OPM 5Y and 10Y averages  (simple mean of available annual OPM values)
    opm_vals = []
    for i in range(min(n_annual, 10)):
        v = parse_num(pl_v("OPM %", -(i + 1)))
        if v is not None:
            opm_vals.append(v)
    if len(opm_vals) >= 5:
        m["OPM 5Year"] = str(round(sum(opm_vals[:5]) / 5, 2))
    if len(opm_vals) >= 10:
        m["OPM 10Year"] = str(round(sum(opm_vals[:10]) / 10, 2))

    # Average Earnings (Net Profit) 5/10 year
    np_vals = [parse_num(pl_v("Net Profit+", -(i+1))) for i in range(min(n_annual, 10))]
    np_vals = [v for v in np_vals if v is not None]
    if len(np_vals) >= 5:
        m["Average Earnings 5Year"] = str(round(sum(np_vals[:5]) / 5, 2))
    if len(np_vals) >= 10:
        m["Average Earnings 10Year"] = str(round(sum(np_vals[:10]) / 10, 2))

    # ── Growth rates (CAGR from P&L) ────────────────────────────────────────
    def pl_growth(metric_pat, years):
        if n_annual < years + 1:
            return None
        return cagr(pl_v(metric_pat, -1), pl_v(metric_pat, -(years + 1)), years)

    for yrs, label in [(3, "3Years"), (5, "5Years"), (7, "7Years"), (10, "10Years")]:
        m[f"Sales growth {label}"]   = pl_growth("Sales+", yrs)
        m[f"Profit growth {label}"]  = pl_growth("Net Profit+", yrs)
        m[f"EPS growth {label}"]     = pl_growth("EPS in Rs", yrs)
        m[f"EBIDT growth {label}"]   = None  # computed if EBITDA available

    # ── Quarterly results ────────────────────────────────────────────────────
    def qr_v(metric, offset=-1):
        return tbl_val_raw(qr, metric, offset)

    qr_cols = qr.get("columns", [])
    n_q = len(qr_cols)

    m["Sales latest quarter"]               = qr_v("Sales+")
    m["Operating profit latest quarter"]    = qr_v("Operating Profit")
    m["OPM latest quarter"]                 = qr_v("OPM %")
    m["Other income latest quarter"]        = qr_v("Other Income")
    m["Depreciation latest quarter"]        = qr_v("Depreciation")
    m["Interest latest quarter"]            = qr_v("Interest")
    m["Profit before tax latest quarter"]   = qr_v("Profit before tax")
    m["Tax latest quarter"]                 = qr_v("Tax %")
    m["Net Profit latest quarter"]          = qr_v("Net Profit+")
    m["Profit after tax latest quarter"]    = qr_v("Net Profit+")
    m["EPS latest quarter"]                 = qr_v("EPS in Rs")
    m["OPM latest quarter"]                 = qr_v("OPM %")

    m["Sales preceding quarter"]              = qr_v("Sales+", -2)
    m["Operating profit preceding quarter"]  = qr_v("Operating Profit", -2)
    m["Net Profit preceding quarter"]        = qr_v("Net Profit+", -2)
    m["Profit after tax preceding quarter"]  = qr_v("Net Profit+", -2)
    m["EPS preceding quarter"]               = qr_v("EPS in Rs", -2)

    # 2 and 3 quarters back
    m["Operating profit 2quarters back"]    = qr_v("Operating Profit", -3)
    m["Operating profit 3quarters back"]    = qr_v("Operating Profit", -4)
    m["Sales 2quarters back"]               = qr_v("Sales+", -3)
    m["Sales 3quarters back"]               = qr_v("Sales+", -4)
    m["Net profit 2quarters back"]          = qr_v("Net Profit+", -3)
    m["Net profit 3quarters back"]          = qr_v("Net Profit+", -4)

    # Preceding year quarter (4 quarters back from latest)
    if n_q >= 5:
        m["Sales preceding year quarter"]               = qr_v("Sales+", -5)
        m["Operating profit preceding year quarter"]    = qr_v("Operating Profit", -5)
        m["Net Profit preceding year quarter"]          = qr_v("Net Profit+", -5)
        m["Profit after tax preceding year quarter"]    = qr_v("Net Profit+", -5)
        m["EPS preceding year quarter"]                 = qr_v("EPS in Rs", -5)
        m["OPM preceding year quarter"]                 = qr_v("OPM %", -5)

    # YoY quarterly growth
    s_lq = parse_num(qr_v("Sales+", -1))
    s_yq = parse_num(qr_v("Sales+", -5))
    if s_lq is not None and s_yq and s_yq != 0:
        m["YOY Quarterly sales growth"] = str(round((s_lq / s_yq - 1) * 100, 2))
    np_lq = parse_num(qr_v("Net Profit+", -1))
    np_yq = parse_num(qr_v("Net Profit+", -5))
    if np_lq is not None and np_yq and np_yq != 0:
        m["YOY Quarterly profit growth"] = str(round((np_lq / np_yq - 1) * 100, 2))

    # QoQ growth
    s_pq = parse_num(qr_v("Sales+", -2))
    if s_lq is not None and s_pq and s_pq != 0:
        m["Sales growth"] = str(round((s_lq / s_pq - 1) * 100, 2))
    np_pq = parse_num(qr_v("Net Profit+", -2))
    if np_lq is not None and np_pq and np_pq != 0:
        m["Profit growth"] = str(round((np_lq / np_pq - 1) * 100, 2))

    # ── Quarterly: detailed metrics for preceding / preceding year quarters ──
    # Latest quarter derived
    pbt_lq2 = parse_num(m.get("Profit before tax latest quarter"))
    int_lq2  = parse_num(m.get("Interest latest quarter"))
    op_lq2   = parse_num(m.get("Operating profit latest quarter"))
    dep_lq2  = parse_num(m.get("Depreciation latest quarter"))
    sal_lq2  = parse_num(m.get("Sales latest quarter"))
    np_lq2   = parse_num(m.get("Net Profit latest quarter"))
    if pbt_lq2 is not None and int_lq2 is not None:
        m["EBIT latest quarter"] = str(round(pbt_lq2 + int_lq2, 2))
    if op_lq2 is not None and dep_lq2 is not None:
        m["EBIDT latest quarter"] = str(round(op_lq2 + dep_lq2, 2))
    if np_lq2 is not None and sal_lq2 and sal_lq2 != 0:
        m["NPM latest quarter"] = str(round(np_lq2 / sal_lq2 * 100, 2))

    # Preceding quarter detail (offset -2)
    m["Profit before tax preceding quarter"] = qr_v("Profit before tax", -2)
    m["Interest preceding quarter"]          = qr_v("Interest", -2)
    m["Depreciation preceding quarter"]      = qr_v("Depreciation", -2)
    m["Other income preceding quarter"]      = qr_v("Other Income", -2)
    m["Tax preceding quarter"]               = qr_v("Tax %", -2)
    pbt_pq2 = parse_num(m.get("Profit before tax preceding quarter"))
    int_pq2  = parse_num(m.get("Interest preceding quarter"))
    op_pq2   = parse_num(m.get("Operating profit preceding quarter"))
    dep_pq2  = parse_num(m.get("Depreciation preceding quarter"))
    sal_pq2  = parse_num(m.get("Sales preceding quarter"))
    np_pq2   = parse_num(m.get("Net Profit preceding quarter"))
    if pbt_pq2 is not None and int_pq2 is not None:
        m["EBIT preceding quarter"] = str(round(pbt_pq2 + int_pq2, 2))
    if op_pq2 is not None and dep_pq2 is not None:
        m["EBIDT preceding quarter"] = str(round(op_pq2 + dep_pq2, 2))
    if op_pq2 is not None and sal_pq2 and sal_pq2 != 0:
        m["OPM preceding quarter"] = str(round(op_pq2 / sal_pq2 * 100, 2))
    if np_pq2 is not None and sal_pq2 and sal_pq2 != 0:
        m["NPM preceding quarter"] = str(round(np_pq2 / sal_pq2 * 100, 2))

    # Preceding year quarter detail (offset -5)
    if n_q >= 5:
        m["Profit before tax preceding year quarter"] = qr_v("Profit before tax", -5)
        m["Interest preceding year quarter"]          = qr_v("Interest", -5)
        m["Depreciation preceding year quarter"]      = qr_v("Depreciation", -5)
        m["Other income preceding year quarter"]      = qr_v("Other Income", -5)
        m["Tax preceding year quarter"]               = qr_v("Tax %", -5)
        pbt_pyq2 = parse_num(qr_v("Profit before tax", -5))
        int_pyq2 = parse_num(qr_v("Interest", -5))
        op_pyq2  = parse_num(m.get("Operating profit preceding year quarter"))
        dep_pyq2 = parse_num(qr_v("Depreciation", -5))
        sal_pyq2 = parse_num(m.get("Sales preceding year quarter"))
        np_pyq2  = parse_num(m.get("Net Profit preceding year quarter"))
        if pbt_pyq2 is not None and int_pyq2 is not None:
            m["EBIT preceding year quarter"] = str(round(pbt_pyq2 + int_pyq2, 2))
        if op_pyq2 is not None and dep_pyq2 is not None:
            m["EBIDT preceding year quarter"] = str(round(op_pyq2 + dep_pyq2, 2))
        if np_pyq2 is not None and sal_pyq2 and sal_pyq2 != 0:
            m["NPM preceding year quarter"] = str(round(np_pyq2 / sal_pyq2 * 100, 2))

    # TTM (Trailing Twelve Months) = sum of last 4 quarters
    sales_q4 = [parse_num(qr_v("Sales+", -(i + 1))) for i in range(min(n_q, 4))]
    np_q4    = [parse_num(qr_v("Net Profit+", -(i + 1))) for i in range(min(n_q, 4))]
    if all(v is not None for v in sales_q4) and len(sales_q4) == 4:
        m["Sales preceding 12months"] = str(round(sum(sales_q4), 2))
    if all(v is not None for v in np_q4) and len(np_q4) == 4:
        m["Net profit preceding 12months"] = str(round(sum(np_q4), 2))

    # ── Balance sheet ────────────────────────────────────────────────────────
    def bs_v(metric, offset=-1):
        return tbl_val_raw(bs, metric, offset)

    bs_cols = bs.get("columns", [])
    n_bs = len(bs_cols)

    m["Debt"]                   = bs_v("Borrowings") or bs_v("Debt")
    m["Equity capital"]         = bs_v("Share Capital") or bs_v("Equity Capital")
    m["Preference capital"]     = bs_v("Preference Capital")
    m["Reserves"]               = bs_v("Reserves")
    m["Secured loan"]           = bs_v("Secured Loan")
    m["Unsecured loan"]         = bs_v("Unsecured Loan")
    m["Total Assets"]           = bs_v("Total Assets")
    m["Balance sheet total"]    = bs_v("Total Assets")
    m["Net block"]              = bs_v("Fixed Assets") or bs_v("Net Block")
    m["Gross block"]            = bs_v("Gross Block")
    m["Capital work in progress"] = bs_v("CWIP")
    m["Investments"]            = bs_v("Investments")
    m["Other Assets"]           = bs_v("Other Assets")
    m["Trade receivables"]      = bs_v("Trade Receivables") or bs_v("Debtors")
    m["Inventory"]              = bs_v("Inventory")
    m["Trade Payables"]         = bs_v("Trade Payables") or bs_v("Creditors")
    m["Cash Equivalents"]       = bs_v("Cash") or bs_v("Cash Equivalent")

    # Debt / balance sheet history
    for yrs in [3, 5, 7, 10]:
        m[f"Debt {yrs}Years back"]     = bs_v("Borrowings", -(yrs + 1)) or bs_v("Debt", -(yrs + 1))
        m[f"Net block {yrs}Years back"]= bs_v("Fixed Assets", -(yrs + 1))

    m["Debt preceding year"]    = bs_v("Borrowings", -2) or bs_v("Debt", -2)
    m["Net block preceding year"]= bs_v("Fixed Assets", -2)

    # Working capital = Current Assets – Current Liabilities (not always in table directly)
    m["Working capital"]        = bs_v("Working Capital")

    # ── Cash flow ────────────────────────────────────────────────────────────
    def cf_v(metric, offset=-1):
        return tbl_val_raw(cf, metric, offset)

    cf_cols = cf.get("columns", [])
    n_cf = len(cf_cols)

    m["Cash from operations last year"]     = cf_v("Cash from Operating", -2)
    m["Free cash flow last year"]           = cf_v("Free Cash Flow", -2)
    m["Cash from investing last year"]      = cf_v("Cash from Investing", -2)
    m["Cash from financing last year"]      = cf_v("Cash from Financing", -2)
    m["Net cash flow last year"]            = cf_v("Net Cash Flow", -2)

    m["Free cash flow preceding year"]      = cf_v("Free Cash Flow", -3)
    m["Cash from operations preceding year"]= cf_v("Cash from Operating", -3)
    m["Cash from investing preceding year"] = cf_v("Cash from Investing", -3)
    m["Cash from financing preceding year"] = cf_v("Cash from Financing", -3)
    m["Net cash flow preceding year"]       = cf_v("Net Cash Flow", -3)

    # Multi-year aggregated cash flows (sum over N years)
    def cf_sum(metric_pat, years):
        vals = [parse_num(cf_v(metric_pat, -(i + 1))) for i in range(min(n_cf, years))]
        vals = [v for v in vals if v is not None]
        return str(round(sum(vals), 2)) if vals else None

    for yrs in [3, 5, 7, 10]:
        m[f"Free cash flow {yrs}years"]         = cf_sum("Free Cash Flow", yrs)
        m[f"Operating cash flow {yrs}years"]    = cf_sum("Cash from Operating", yrs)
        m[f"Investing cash flow {yrs}years"]    = cf_sum("Cash from Investing", yrs)

    # Cash beginning/end of year
    m["Cash end of last year"]   = cf_v("Cash from Operating", -2)   # approximation
    m["Cash 3Years back"]        = cf_v("Free Cash Flow", -4)
    m["Cash 5Years back"]        = cf_v("Free Cash Flow", -6)
    m["Cash 7Years back"]        = cf_v("Free Cash Flow", -8)

    # ── Ratios from ratios table ──────────────────────────────────────────────
    def rat_v(metric, offset=-1):
        return tbl_val_raw(rat, metric, offset)

    m["Debtor days"]             = rat_v("Debtor Days")
    m["Days Receivable Outstanding"] = rat_v("Debtor Days")
    m["Days Payable Outstanding"]= rat_v("Days Payable")
    m["Cash Conversion Cycle"]   = rat_v("Cash Conversion")
    m["Working Capital Days"]    = rat_v("Working Capital Days")
    m["Return on capital employed"] = rat_v("ROCE %")
    m["Return on capital employed preceding year"] = rat_v("ROCE %", -2)

    # History of debtor days
    for yrs in [3, 5]:
        m[f"Debtor days {yrs}years back"] = rat_v("Debtor Days", -(yrs + 1))

    avg_dd = []
    for i in range(min(len(rat.get("columns", [])), 3)):
        v = parse_num(rat_v("Debtor Days", -(i + 1)))
        if v is not None:
            avg_dd.append(v)
    if avg_dd:
        m["Average debtor days 3years"] = str(round(sum(avg_dd) / len(avg_dd), 2))

    # ── Shareholding ─────────────────────────────────────────────────────────
    if sh_q and "rows" in sh_q and "columns" in sh_q:
        sh_cols = sh_q.get("columns", [])
        n_sh = len(sh_cols)
        for row in sh_q.get("rows", []):
            cat = row.get("category", "")
            latest_val = row.get(sh_cols[-1], "") if sh_cols else ""

            if "Promoter" in cat:
                m["Promoter holding"]    = latest_val
                # Change vs 3 months ago (1 quarter)
                if n_sh >= 4:
                    prev3 = parse_num(row.get(sh_cols[-4], ""))
                    curr  = parse_num(latest_val)
                    if curr is not None and prev3 is not None:
                        m["Change in promoter holding"] = str(round(curr - prev3, 2))
                # Change over 3 years (use up to 12 quarters back; screener stores ~12Q)
                back12 = min(13, n_sh)
                if n_sh >= back12 and back12 > 1:
                    prev12 = parse_num(row.get(sh_cols[-back12], ""))
                    curr   = parse_num(latest_val)
                    if curr is not None and prev12 is not None:
                        m["Change in promoter holding 3Years"] = str(round(curr - prev12, 2))

            elif "FII" in cat or "Foreign Institutional" in cat:
                m["FII holding"] = latest_val
                if n_sh >= 4:
                    p3 = parse_num(row.get(sh_cols[-4], ""))
                    c  = parse_num(latest_val)
                    if c is not None and p3 is not None:
                        m["Change in FII holding"] = str(round(c - p3, 2))
                back12 = min(13, n_sh)
                if n_sh >= back12 and back12 > 1:
                    p12 = parse_num(row.get(sh_cols[-back12], ""))
                    c   = parse_num(latest_val)
                    if c is not None and p12 is not None:
                        m["Change in FII holding 3Years"] = str(round(c - p12, 2))

            elif "DII" in cat or "Domestic Institutional" in cat:
                m["DII holding"] = latest_val
                if n_sh >= 4:
                    p3 = parse_num(row.get(sh_cols[-4], ""))
                    c  = parse_num(latest_val)
                    if c is not None and p3 is not None:
                        m["Change in DII holding"] = str(round(c - p3, 2))
                back12 = min(13, n_sh)
                if n_sh >= back12 and back12 > 1:
                    p12 = parse_num(row.get(sh_cols[-back12], ""))
                    c   = parse_num(latest_val)
                    if c is not None and p12 is not None:
                        m["Change in DII holding 3Years"] = str(round(c - p12, 2))

            elif "Public" in cat:
                m["Public holding"] = latest_val

            elif "Number of Shareholders" in cat or "No. of shareholders" in cat:
                m["Number of Shareholders"]              = latest_val
                if n_sh >= 2:
                    m["Number of Shareholders preceding quarter"] = \
                        row.get(sh_cols[-2], "") or None
                if n_sh >= 5:
                    m["Number of Shareholders 1year back"] = \
                        row.get(sh_cols[-5], "") or None

    # ── Top ratios (current price data already available) ───────────────────
    tr = jdata.get("top_ratios", {})
    if tr:
        def tr_v(key):
            v = tr.get(key, "")
            return v if v != "" else None

        m.setdefault("Market Capitalization", tr_v("Market Cap"))
        m.setdefault("Current price",         tr_v("Current Price"))
        m.setdefault("Book value",             tr_v("Book Value"))
        m.setdefault("Face value",             tr_v("Face Value"))
        m.setdefault("Price to Earning",       tr_v("Stock P/E"))
        m.setdefault("Dividend yield",         tr_v("Dividend Yield"))
        m.setdefault("Return on equity",       tr_v("ROE"))
        m.setdefault("Return on capital employed", tr_v("ROCE"))

        # High / Low split (screener shows "H / L" or sometimes just the high price)
        hl = tr_v("High / Low")
        if hl:
            if "/" in hl:
                parts = [p.strip() for p in hl.split("/")]
                m.setdefault("High price", parts[0])
                m.setdefault("Low price",  parts[1] if len(parts) > 1 else None)
            else:
                m.setdefault("High price", hl.strip())

    # ── Derived / computed metrics ──────────────────────────────────────────
    # These mirror what screener.in computes in quick_ratios / ratios tables.

    # Market Cap (in Cr) — normalize "42,848" → 42848
    mc = parse_num(m.get("Market Capitalization"))
    sales_curr = parse_num(m.get("Sales"))
    np_curr = parse_num(m.get("Net profit"))
    debt = parse_num(m.get("Debt"))
    eq = parse_num(m.get("Equity capital"))
    res = parse_num(m.get("Reserves"))
    ebitda = parse_num(m.get("EBITDA"))
    ebit = parse_num(m.get("EBIT"))
    total_assets = parse_num(m.get("Total Assets"))
    interest = parse_num(m.get("Interest"))
    bv = parse_num(m.get("Book value"))   # per share from top_ratios (₹)
    pe = parse_num(m.get("Price to Earning"))
    current_price = parse_num(m.get("Current price"))
    cash = parse_num(m.get("Cash Equivalents"))

    # Debt to equity = Total Debt / (Equity + Reserves)
    equity_total = (eq or 0) + (res or 0)
    if debt is not None and equity_total > 0:
        m.setdefault("Debt to equity", str(round(debt / equity_total, 2)))

    # Return on assets = Net Profit / Total Assets (%)
    if np_curr is not None and total_assets and total_assets > 0:
        m.setdefault("Return on assets", str(round(np_curr / total_assets * 100, 2)))

    # Asset Turnover = Sales / Total Assets
    if sales_curr is not None and total_assets and total_assets > 0:
        m.setdefault("Asset Turnover Ratio", str(round(sales_curr / total_assets, 2)))

    # Interest Coverage = EBIT / Interest
    if ebit is not None and interest and interest > 0:
        m.setdefault("Interest Coverage Ratio", str(round(ebit / interest, 2)))

    # Net Profit Margin (NPM) = Net Profit / Sales (%)
    if np_curr is not None and sales_curr and sales_curr > 0:
        m.setdefault("NPM", str(round(np_curr / sales_curr * 100, 2)))

    # Price to Sales = Market Cap / Sales (both in Cr)
    if mc is not None and sales_curr and sales_curr > 0:
        m.setdefault("Price to Sales", str(round(mc / sales_curr, 2)))

    # Price to Free Cash Flow = Market Cap / Free Cash Flow last year
    fcf_ly = parse_num(m.get("Free cash flow last year"))
    if mc is not None and fcf_ly and fcf_ly > 0:
        m.setdefault("Price to Free Cash Flow", str(round(mc / fcf_ly, 2)))

    # EV/EBITDA  EV = Market Cap + Debt - Cash
    if mc is not None and ebitda and ebitda > 0:
        ev = mc + (debt or 0) - (cash or 0)
        m.setdefault("EVEBITDA", str(round(ev / ebitda, 2)))

    # Enterprise Value
    if mc is not None and debt is not None:
        ev_val = mc + debt - (cash or 0)
        m.setdefault("Enterprise Value", str(round(ev_val, 2)))

    # Earnings yield = EPS / Current Price (%)
    eps_curr = parse_num(m.get("EPS"))
    if eps_curr is not None and current_price and current_price > 0:
        m.setdefault("Earnings yield", str(round(eps_curr / current_price * 100, 2)))

    # Price to book value = Current Price / Book Value per share
    if current_price is not None and bv and bv > 0:
        m.setdefault("Price to book value", str(round(current_price / bv, 2)))

    # Financial leverage = Total Assets / Equity (simplified)
    if total_assets is not None and equity_total > 0:
        m.setdefault("Financial leverage", str(round(total_assets / equity_total, 2)))

    # Return on equity (if not from top_ratios) = Net Profit / Equity (%)
    if np_curr is not None and equity_total > 0:
        m.setdefault("Return on equity", str(round(np_curr / equity_total * 100, 2)))

    # Graham Number = sqrt(22.5 × EPS × Book Value per share)
    # (needs per-share EPS and per-share book value)
    if eps_curr is not None and bv and eps_curr > 0 and bv > 0:
        import math
        graham = math.sqrt(22.5 * eps_curr * bv)
        m.setdefault("Graham Number", str(round(graham, 2)))

    # Average EBIT 5/10 Year
    ebit_vals = []
    for i in range(min(n_annual, 10)):
        pbt_i = parse_num(pl_v("Profit before tax", -(i + 1)))
        int_i = parse_num(pl_v("Interest", -(i + 1)))
        if pbt_i is not None and int_i is not None:
            ebit_vals.append(pbt_i + int_i)
    if len(ebit_vals) >= 5:
        m.setdefault("Average EBIT 5Year", str(round(sum(ebit_vals[:5]) / 5, 2)))
    if len(ebit_vals) >= 10:
        m.setdefault("Average EBIT 10Year", str(round(sum(ebit_vals[:10]) / 10, 2)))

    # EBIDT (EBITDA) growth rates
    ebitda_vals = []
    for i in range(min(n_annual, 11)):
        op_i = parse_num(pl_v("Operating Profit", -(i + 1)))
        dep_i = parse_num(pl_v("Depreciation", -(i + 1)))
        if op_i is not None and dep_i is not None:
            ebitda_vals.append(op_i + dep_i)
        else:
            ebitda_vals.append(None)

    def ebitda_cagr(years):
        if len(ebitda_vals) <= years:
            return None
        return cagr(ebitda_vals[0], ebitda_vals[years], years)

    for yrs, label in [(3, "3Years"), (5, "5Years"), (7, "7Years"), (10, "10Years")]:
        m[f"EBIDT growth {label}"] = ebitda_cagr(yrs)

    # Average Return on Capital Employed (ROCE) — from ratios table
    roce_vals = [parse_num(rat_v("ROCE %", -(i + 1))) for i in range(min(len(rat.get("columns", [])), 10))]
    roce_vals = [v for v in roce_vals if v is not None]
    if len(roce_vals) >= 3:
        m.setdefault("Average return on capital employed 3Years",
                     str(round(sum(roce_vals[:3]) / 3, 2)))
    if len(roce_vals) >= 5:
        m.setdefault("Average return on capital employed 5Years",
                     str(round(sum(roce_vals[:5]) / 5, 2)))
    if len(roce_vals) >= 7:
        m.setdefault("Average return on capital employed 7Years",
                     str(round(sum(roce_vals[:7]) / 7, 2)))
    if len(roce_vals) >= 10:
        m.setdefault("Average return on capital employed 10Years",
                     str(round(sum(roce_vals[:10]) / 10, 2)))

    # Average ROE — from P&L
    roe_vals = []
    for i in range(min(n_annual, 10)):
        np_i = parse_num(pl_v("Net Profit+", -(i + 1)))
        # simplified: use equity+reserves from current balance sheet
        if np_i is not None and equity_total > 0:
            roe_vals.append(np_i / equity_total * 100)
    if len(roe_vals) >= 3:
        m.setdefault("Average return on equity 3Years",
                     str(round(sum(roe_vals[:3]) / 3, 2)))
    if len(roe_vals) >= 5:
        m.setdefault("Average return on equity 5Years",
                     str(round(sum(roe_vals[:5]) / 5, 2)))
    if len(roe_vals) >= 7:
        m.setdefault("Average return on equity 7Years",
                     str(round(sum(roe_vals[:7]) / 7, 2)))
    if len(roe_vals) >= 10:
        m.setdefault("Average return on equity 10Years",
                     str(round(sum(roe_vals[:10]) / 10, 2)))

    # ── Additional derived metrics ──────────────────────────────────────────
    # Number of equity shares (Cr) = Equity Capital (Cr) / Face Value (₹)
    eq_cap_num = parse_num(m.get("Equity capital"))
    fv_num = parse_num(m.get("Face value"))
    if eq_cap_num is not None and fv_num and fv_num > 0:
        m.setdefault("Number of equity shares", str(round(eq_cap_num / fv_num, 2)))

    # Return on assets preceding year = Net Profit last year / Total Assets last year
    ta_ly = parse_num(bs_v("Total Assets", -2))
    np_ly = parse_num(m.get("Net Profit last year"))
    if np_ly is not None and ta_ly and ta_ly > 0:
        m.setdefault("Return on assets preceding year", str(round(np_ly / ta_ly * 100, 2)))

    # Return on equity preceding year = Net Profit last year / (Equity+Reserves) last year
    eq_cap_ly = parse_num(bs_v("Equity Capital", -2))
    res_ly    = parse_num(bs_v("Reserves", -2))
    eq_total_ly = (eq_cap_ly or 0) + (res_ly or 0)
    if np_ly is not None and eq_total_ly > 0:
        m.setdefault("Return on equity preceding year",
                     str(round(np_ly / eq_total_ly * 100, 2)))

    # Historical ROA 3/5 year averages
    roa_hist = []
    for i in range(min(min(n_annual, n_bs), 6)):
        np_i = parse_num(pl_v("Net Profit+", -(i + 1)))
        ta_i = parse_num(bs_v("Total Assets", -(i + 1)))
        if np_i is not None and ta_i and ta_i > 0:
            roa_hist.append(np_i / ta_i * 100)
        else:
            roa_hist.append(None)
    roa_clean = [v for v in roa_hist if v is not None]
    if len(roa_clean) >= 3:
        m.setdefault("Return on assets 3years",
                     str(round(sum(roa_clean[:3]) / 3, 2)))
    if len(roa_clean) >= 5:
        m.setdefault("Return on assets 5years",
                     str(round(sum(roa_clean[:5]) / 5, 2)))

    # ROE growth 5years (CAGR of annual ROE)
    roe_hist_g = []
    for i in range(min(min(n_annual, n_bs), 7)):
        np_i = parse_num(pl_v("Net Profit+", -(i + 1)))
        eq_i = parse_num(bs_v("Equity Capital", -(i + 1)))
        res_i = parse_num(bs_v("Reserves", -(i + 1)))
        et_i = (eq_i or 0) + (res_i or 0)
        roe_hist_g.append(np_i / et_i * 100 if np_i is not None and et_i > 0 else None)
    if (len(roe_hist_g) >= 6 and roe_hist_g[0] is not None and
            roe_hist_g[5] is not None and roe_hist_g[5] > 0):
        g = cagr(roe_hist_g[0], roe_hist_g[5], 5)
        if g is not None:
            m.setdefault("Return on equity 5years growth", str(g))

    # Average Working Capital Days 3years
    rat_cols_n = len(rat.get("columns", []))
    wcd_v = [parse_num(rat_v("Working Capital Days", -(i + 1)))
             for i in range(min(rat_cols_n, 3))]
    wcd_v = [v for v in wcd_v if v is not None]
    if wcd_v:
        m.setdefault("Average Working Capital Days 3years",
                     str(round(sum(wcd_v) / len(wcd_v), 2)))

    # Average dividend (payout) 3years and 5years
    div_v = [parse_num(pl_v("Dividend", -(i + 1))) for i in range(min(n_annual, 5))]
    div_v = [v for v in div_v if v is not None]
    if len(div_v) >= 3:
        m.setdefault("Average dividend payout 3years",
                     str(round(sum(div_v[:3]) / 3, 2)))
    if len(div_v) >= 5:
        m.setdefault("Average 5years dividend",
                     str(round(sum(div_v[:5]) / 5, 2)))

    # Book value preceding year (from balance sheet Equity+Reserves last year / shares)
    bv_eq_ly = (eq_cap_ly or 0) + (res_ly or 0)
    eq_shares = parse_num(m.get("Number of equity shares"))  # in Cr shares
    if bv_eq_ly > 0 and eq_shares and eq_shares > 0:
        # BV per share (₹) = (Equity+Reserves in Cr × 1Cr) / (Shares in Cr × 1Cr) = ratio
        m.setdefault("Book value preceding year", str(round(bv_eq_ly / eq_shares, 2)))

    # clean up None values in non-essential fields
    return {k: v for k, v in m.items() if v is not None}


# ── Phase 4: merge and write screener_metrics to JSON files ─────────────────
def build_and_save(screen_data: dict, ratios_data: dict, ext_data: dict = None):
    """
    For each *_screener.json file:
      1. Extract table metrics (from existing JSON data)
      2. Overlay screen/raw data (Phase 1)
      3. Overlay extended screen/raw data (Phase 1b — extra columns)
      4. Overlay quick_ratios data (Phase 2)
      5. Save `screener_metrics` key back to the file
    """
    if ext_data is None:
        ext_data = load_json(SCREEN_EXT_CACHE, {})

    json_files = sorted(STRUCTURED.glob("*_screener.json"))
    log.info(f"Phase 3: building screener_metrics for {len(json_files)} files ...")

    updated = skipped = 0
    for path in json_files:
        try:
            jdata = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"  {path.name}: read error {e}")
            skipped += 1
            continue

        # Collect all metrics
        sm = extract_table_metrics(jdata)

        # Overlay screen/raw data (matched by NSE symbol or BSE code)
        sym = jdata.get("symbol", "") or jdata.get("nse_symbol", "")
        sr = screen_data.get(sym, {})
        for k, v in sr.items():
            if v and v != "":
                sm.setdefault(k, v)

        # Overlay extended screen/raw data (Phase 1b)
        for k, v in ext_data.get(sym, {}).items():
            if v and v != "":
                sm.setdefault(k, v)

        # Overlay quick_ratios
        qr = ratios_data.get(sym, {})
        for k, v in qr.items():
            if v and v != "":
                sm.setdefault(k, v)

        # Remove empty / None
        sm = {k: v for k, v in sm.items() if v is not None and str(v).strip() != ""}

        jdata["screener_metrics"] = sm
        try:
            path.write_text(json.dumps(jdata, ensure_ascii=False, indent=2), encoding="utf-8")
            updated += 1
        except Exception as e:
            log.warning(f"  {path.name}: write error {e}")
            skipped += 1

        if updated % 500 == 0 and updated > 0:
            log.info(f"  {updated}/{len(json_files)} written ...")

    log.info(f"Phase 3 done: {updated} updated, {skipped} skipped")


# ── entrypoint ─────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--screen",     action="store_true", help="Phase 1: scrape screen/raw pages")
    parser.add_argument("--screen-ext", action="store_true", help="Phase 1b: extra column batches")
    parser.add_argument("--ratios",     action="store_true", help="Phase 2: fetch quick_ratios API")
    parser.add_argument("--build",      action="store_true", help="Phase 3: extract+merge → write JSON")
    parser.add_argument("--all",        action="store_true", help="Run all phases (1+1b+2+3)")
    parser.add_argument("--force",      action="store_true", help="Force re-fetch ignoring cache")
    parser.add_argument("--verify",     metavar="SYMBOL",   help="Print screener_metrics for one company")
    args = parser.parse_args()

    if args.verify:
        sym = args.verify.upper()
        p = STRUCTURED / f"{sym}_screener.json"
        if not p.exists():
            print(f"File not found: {p}")
            sys.exit(1)
        jdata = json.loads(p.read_text(encoding="utf-8"))
        sm = jdata.get("screener_metrics", {})
        print(f"\nscreener_metrics for {sym} ({len(sm)} metrics):")
        for k, v in sorted(sm.items()):
            print(f"  {k!r:55} : {v!r}")
        return

    if not (args.screen or getattr(args, "screen_ext", False) or
            args.ratios or args.build or args.all):
        parser.print_help()
        return

    run_screen     = args.screen or args.all
    run_screen_ext = getattr(args, "screen_ext", False) or args.all
    run_ratios     = args.ratios or args.all
    run_build      = args.build  or args.all

    screen_data: dict = {}
    ext_data: dict    = {}
    ratios_data: dict = {}

    if run_screen:
        screen_data = phase1_scrape_screen(force=args.force)
    else:
        screen_data = load_json(SCREEN_CACHE, {})

    if run_screen_ext:
        ext_data = phase1b_scrape_extra_columns(force=args.force)
    else:
        ext_data = load_json(SCREEN_EXT_CACHE, {})

    if run_ratios:
        # Only fetch quick_ratios for companies found in screen/raw data — these
        # are definitely on screener.in. BSE-only numeric codes are skipped since
        # they are often not indexed by screener's API and cause many empty calls.
        screen_cache = load_json(SCREEN_CACHE, {})
        all_symbols = list(screen_cache.keys())  # symbols from screen/raw scraping
        log.info(f"Fetching quick_ratios for {len(all_symbols)} screen/raw companies")
        ratios_data = phase2_fetch_ratios(all_symbols, force=args.force)
    else:
        ratios_data = load_json(RATIOS_CACHE, {})

    if run_build:
        build_and_save(screen_data, ratios_data, ext_data)


if __name__ == "__main__":
    main()
