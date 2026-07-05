"""
Screener.in Company Scraper — Comprehensive, Authenticated
===========================================================
Scrapes BOTH consolidated and standalone views for every company.
Requires SCREENER_SESSION_ID in .env (get from browser cookies after login).

What this captures per company:
  - top_ratios          : all custom + built-in ratios in the top band
  - about               : company description (login-gated)
  - pros / cons         : strengths / weaknesses (login-gated)
  - consolidated view:
      quarterly_results, profit_loss, balance_sheet,
      cash_flow, ratios, shareholding_quarterly, shareholding_yearly
  - standalone view:    (same financial tables, separate keys)
      quarterly_results, profit_loss, balance_sheet, cash_flow, ratios
  - documents:
      annual_reports, concalls, credit_ratings
  - announcements       : recent BSE/NSE filings list

Output: data/structured/{SYMBOL}_screener.json   (one JSON per company, no CSVs)

Usage:
  python 04_screener_scraper.py --symbol RELIANCE
  python 04_screener_scraper.py --symbol RELIANCE --standalone-only
  python 04_screener_scraper.py --all                   (all NSE companies)
  python 04_screener_scraper.py --all --no-resume       (re-scrape everything)
"""

import os, re, json, time, random, argparse, logging
from pathlib import Path
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
import pandas as pd
from dotenv import load_dotenv

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

STRUCT_DIR = ROOT / "data" / "structured"
STRUCT_DIR.mkdir(parents=True, exist_ok=True)

(ROOT / "data").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(ROOT / "data" / "screener_scrape.log", encoding="utf-8"),
    ]
)
log = logging.getLogger(__name__)

BASE = "https://www.screener.in"
SESSION_ID = os.getenv("SCREENER_SESSION_ID", "")

# ── Rate-limiting config ────────────────────────────────────────────────────────
# Screener.in is a small paid-subscription site — be respectful.
# These are MINIMUM delays; actual time per company is longer due to network.
DELAY_BETWEEN_VIEWS    = (1.0, 1.0)   # sleep between consolidated & standalone (1s)
DELAY_BETWEEN_COMPANIES = (2.0, 2.0)  # sleep between companies (2s)
PAUSE_EVERY            = 0            # 0 = disabled (no periodic long pauses)
PAUSE_DURATION         = (0, 0)       # unused when PAUSE_EVERY = 0
# 429 / 503 backoff ladder (seconds): 2min → 5min → 10min → 20min
RATE_LIMIT_BACKOFF     = [120, 300, 600, 1200]

# ── Custom ratios the user has configured on their Screener account ────────────
# These appear automatically in top_ratios when scraped with a valid session cookie.
# Listed here for documentation / post-processing cross-reference only.
CUSTOM_RATIO_NAMES = [
    "Acid Test Ratio", "Quick Ratio", "Cash Conversion Ratio",
    "Company Profit to Bond Yield", "Cushion", "EPS growth last year",
    "EV to Sales", "Intrinsic Value 1", "Net Profit Growth",
    "Net Profit Ratio", "WACC", "Working Capital Ratio",
    "Price to Sales", "Price to Free Cash Flow", "EVEBITDA",
    "Current ratio", "Interest Coverage Ratio", "PEG Ratio",
    "Working Capital to Sales ratio", "QoQ Profits", "QoQ Sales",
    "Net worth", "Market Cap to Sales", "Interest Coverage",
    "Enterprise Value to EBIT", "Debt Capacity", "Debt To Profit",
    "Total Capital Employed", "CROIC", "debtplus", "Leverage",
    "Dividend Payout", "Intrinsic Value",
    "cash debt contingent liabilities by mcap", "Cash by market cap",
    "52w Index", "Down from 52w high", "Up from 52w low", "From 52w high",
    "Mkt Cap To Debt Cap", "Dividend Payout Ratio", "Graham",
    "Price to Cash Flow", "ROCE3yr avg", "PB X PE", "NCAVPS",
    "Market Capt to Cash Flow", "Altman Z Score",
    "Market cap to quarterly profit",
]


# ── Session ────────────────────────────────────────────────────────────────────

def get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.screener.in/",
    })
    if SESSION_ID:
        s.cookies.set("sessionid", SESSION_ID, domain="www.screener.in")
        log.info("Session cookie loaded (authenticated)")
    else:
        log.warning("SCREENER_SESSION_ID missing — About/Pros/Cons/custom ratios will be empty")
    return s


# ── Fetch ──────────────────────────────────────────────────────────────────────

def fetch(session: requests.Session, url: str, retries: int = 4) -> BeautifulSoup | None:
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 404:
                return None
            if r.status_code in (429, 503, 502):
                # Rate limited or server overloaded — back off hard
                backoff = RATE_LIMIT_BACKOFF[min(attempt, len(RATE_LIMIT_BACKOFF) - 1)]
                log.warning(
                    f"HTTP {r.status_code} on {url} "
                    f"(attempt {attempt+1}/{retries}) — sleeping {backoff}s"
                )
                time.sleep(backoff)
                continue
            r.raise_for_status()
            return BeautifulSoup(r.text, "lxml")
        except requests.exceptions.Timeout:
            wait = 5 * (attempt + 1)
            log.warning(f"Timeout ({url}) — retry in {wait}s")
            time.sleep(wait)
        except Exception as e:
            wait = 3 ** attempt          # 1s, 3s, 9s
            log.warning(f"Fetch failed ({url}): {e} — retry in {wait}s")
            time.sleep(wait)
    return None


def company_url(symbol: str, view: str = "consolidated") -> str:
    if view == "standalone":
        return f"{BASE}/company/{symbol}/"
    return f"{BASE}/company/{symbol}/consolidated/"


# ── Section parsers ────────────────────────────────────────────────────────────

def parse_top_ratios(soup: BeautifulSoup) -> dict:
    """
    Top-of-page ratio band.
    Screener renders each ratio as one <li> with full text, e.g.:
      "Market Cap ₹ 17,09,909 Cr."
      "Stock P/E 22.0"
    We split on the LAST numeric token to get name / value.

    With a valid session cookie, user's custom ratios also appear here
    (if they've been added to the company page via 'Add ratio to table').
    The 9 default ones always present: Market Cap, Current Price, High/Low,
    Stock P/E, Book Value, Dividend Yield, ROCE, ROE, Face Value.
    """
    ratios = {}
    for li in soup.select("#top-ratios li"):
        full = li.get_text(" ", strip=True)
        if not full:
            continue

        # Try named spans first (some Screener versions)
        name_span = li.find("span", class_="name")
        val_span  = li.find("span", class_="number") or li.find("strong")
        if name_span and val_span:
            ratios[name_span.get_text(" ", strip=True)] = val_span.get_text(" ", strip=True)
        else:
            # Full text approach — split off the trailing value
            # "Market Cap ₹ 17,09,909 Cr." → name="Market Cap", val="₹ 17,09,909 Cr."
            # "Stock P/E 22.0"             → name="Stock P/E",  val="22.0"
            import re as _re
            # Split at the last run of digits/punctuation that looks like a value
            m = _re.match(r"^(.+?)\s+([\d,\.]+\s*%?.*?)$", full)
            if m:
                ratios[m.group(1).strip()] = m.group(2).strip()
            else:
                # Give up — store full text under its own key
                ratios[full] = ""
    return ratios


def parse_about(soup: BeautifulSoup) -> str:
    """
    Company description text — login-gated on Screener.
    The element has class 'about' (no id).  Structure:
      <div class="sub show-more-box about">...</div>
    """
    # Primary: div with class 'about'
    el = soup.find("div", class_=lambda c: c and "about" in c if c else False)
    if el:
        for tag in el.select("button, .read-more-toggle, .hidden"):
            tag.decompose()
        text = el.get_text(" ", strip=True)
        if text and "log in" not in text.lower() and len(text) > 20:
            return text
    # Fallbacks
    for sel in ["#about p", "#about"]:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(" ", strip=True)
            if text and "log in" not in text.lower() and len(text) > 20:
                return text
    return ""


def parse_pros_cons(soup: BeautifulSoup) -> tuple[list, list]:
    """Pros and Cons — login shows full list, anon shows 2-3 items."""
    pros = [li.get_text(" ", strip=True) for li in soup.select(".pros li")]
    cons = [li.get_text(" ", strip=True) for li in soup.select(".cons li")]
    return pros, cons


def parse_financial_table(soup: BeautifulSoup, section_id: str) -> dict | None:
    """
    Generic parser for P&L, Balance Sheet, Cash Flow, Ratios, Quarterly Results.
    Returns:
      {
        "columns": ["Mar 2024", "Mar 2023", ...],
        "rows": [
          {"metric": "Sales", "Mar 2024": "258,212", "Mar 2023": "239,619", ...},
          ...
        ]
      }
    or None if the section doesn't exist on this page.
    """
    section = soup.find(id=section_id)
    if not section:
        return None

    table = section.find("table")
    if not table:
        return None

    # Headers
    thead = table.find("thead")
    ths   = thead.find_all("th") if thead else table.find_all("th")
    all_cols = [th.get_text(strip=True) for th in ths]

    rows = []
    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if not cells or not any(c for c in cells):
            continue

        if len(cells) == len(all_cols):
            row = {all_cols[i]: cells[i] for i in range(len(all_cols))}
            # Rename first column (metric name column) to "metric"
            first_col = all_cols[0] if all_cols else None
            if first_col is not None and first_col in row and first_col != "metric":
                row["metric"] = row.pop(first_col)
        else:
            # Column count mismatch (expandable row, sub-items etc.)
            row = {"metric": cells[0], "values": cells[1:]}

        rows.append(row)

    data_cols = [c for c in all_cols[1:] if c] if all_cols else []
    return {"columns": data_cols, "rows": rows}


def parse_shareholding(soup: BeautifulSoup) -> dict:
    """
    Screener embeds BOTH quarterly and yearly shareholding tables in the DOM.
    One table has class/data attribute marking it as yearly; the other is quarterly.
    Returns { "quarterly": {...}, "yearly": {...} }
    """
    section = soup.find(id="shareholding")
    result: dict = {"quarterly": None, "yearly": None}

    if not section:
        return result

    def parse_one_table(table) -> dict:
        thead = table.find("thead")
        ths   = thead.find_all("th") if thead else table.find_all("th")
        all_cols = [th.get_text(strip=True) for th in ths]

        rows = []
        tbody = table.find("tbody") or table
        for tr in tbody.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if not cells or not any(c for c in cells):
                continue
            if len(cells) == len(all_cols) and all_cols:
                row = {all_cols[i]: cells[i] for i in range(len(all_cols))}
                first_col = all_cols[0]
                if first_col in row:
                    row["category"] = row.pop(first_col)
                rows.append(row)
            else:
                rows.append({"category": cells[0], "values": cells[1:]})

        data_cols = [c for c in all_cols[1:] if c] if all_cols else []
        return {"columns": data_cols, "rows": rows}

    tables = section.find_all("table")
    for i, table in enumerate(tables):
        classes = " ".join(table.get("class", []))
        data_period = table.get("data-period", "")
        label_hint  = (classes + " " + data_period).lower()

        if "yearly" in label_hint or "annual" in label_hint:
            result["yearly"] = parse_one_table(table)
        elif "quarterly" in label_hint or "quarter" in label_hint:
            result["quarterly"] = parse_one_table(table)
        else:
            # No class hint — first table = quarterly, second = yearly (Screener convention)
            key = "quarterly" if i == 0 else "yearly"
            result[key] = parse_one_table(table)

    return result


def _has_class(el, cls_name: str) -> bool:
    """Helper: True if element's class list contains cls_name."""
    return cls_name in " ".join(el.get("class", []))


def parse_documents(soup: BeautifulSoup) -> dict:
    """
    Parses the #documents section which has four subsections:

    Announcements : div.documents.flex-column  (no annual-reports/concalls/credit-ratings)
    Annual reports: div.documents.annual-reports.flex-column
    Credit ratings: div.documents.credit-ratings.flex-column
    Concalls      : div.documents.concalls.flex-column
                      → div.show-more-box → ul.list-links → li entries
                        Each li: <div>DATE</div> <a>Transcript</a> <a>PPT</a> [<a>REC</a>]
    """
    docs_section = soup.find(id="documents")
    result: dict = {
        "annual_reports":  [],
        "concalls":        [],
        "credit_ratings":  [],
        "announcements":   [],
    }
    if not docs_section:
        return result

    # Helper — find a div that HAS a given css class token
    def find_by_cls(cls_token: str):
        """Find first div within docs_section that includes cls_token in its class list."""
        for div in docs_section.find_all("div", class_=True):
            if cls_token in div.get("class", []):
                return div
        return None

    # ── Annual reports ─────────────────────────────────────────────────────────
    ar_div = find_by_cls("annual-reports")
    if ar_div:
        result["annual_reports"] = [
            {"label": a.get_text(" ", strip=True), "url": a["href"]}
            for a in ar_div.find_all("a", href=True)
        ]

    # ── Credit ratings ─────────────────────────────────────────────────────────
    cr_div = find_by_cls("credit-ratings")
    if cr_div:
        result["credit_ratings"] = [
            {"label": a.get_text(" ", strip=True), "url": a["href"]}
            for a in cr_div.find_all("a", href=True)
        ]

    # ── Concalls — grouped by quarter ──────────────────────────────────────────
    # Structure: div.documents.concalls → div.show-more-box → ul.list-links → li
    # Each li: <div class="nowrap">Apr 2026</div> + <a>Transcript</a> + <a>PPT</a>
    cc_div = find_by_cls("concalls")
    if cc_div:
        # The actual list is inside a show-more-box
        show_more = cc_div.find("div", class_="show-more-box")
        container = show_more if show_more else cc_div
        for li in container.find_all("li"):
            # Date: div with class "nowrap"
            date_div = li.find("div", class_="nowrap")
            date = date_div.get_text(strip=True) if date_div else ""

            entry: dict = {"date": date}
            for a in li.find_all("a", href=True):
                label = a.get_text(strip=True).lower()
                href  = a["href"]
                if "transcript" in label or "raw" in a.get("title", "").lower():
                    entry["transcript"] = href
                elif label == "ppt":
                    entry["ppt"] = href
                elif label == "rec":
                    entry["recording"] = href
                else:
                    entry[label] = href      # unknown type — keep with its text label

            if any(k in entry for k in ("transcript", "ppt", "recording")):
                result["concalls"].append(entry)

    # ── Announcements — div.documents that has NO other type-class ─────────────
    # It's the first "documents" div (not annual-reports, not credit-ratings, not concalls)
    for div in docs_section.find_all("div", class_="documents"):
        cls_set = set(div.get("class", []))
        if not cls_set & {"annual-reports", "credit-ratings", "concalls"}:
            for li in div.find_all("li"):
                a = li.find("a", href=True)
                if a:
                    result["announcements"].append({
                        "title": a.get_text(" ", strip=True),
                        "url":   a["href"],
                    })
            break   # only the first matching div

    return result


def parse_announcements(soup: BeautifulSoup) -> list[dict]:
    """
    Announcements are now captured inside parse_documents.
    This function is kept as a no-op stub for API compatibility.
    """
    return []


# ── View scrape ────────────────────────────────────────────────────────────────

# Screener section IDs (can vary slightly by company page version)
SECTION_IDS = {
    "quarterly_results": ["quarters", "quarterly-shp", "quarterly-results"],
    "profit_loss":       ["profit-loss", "profit_loss"],
    "balance_sheet":     ["balance-sheet", "balance_sheet"],
    "cash_flow":         ["cash-flow", "cash_flow"],
    "ratios":            ["ratios"],
}


def scrape_view(session: requests.Session, symbol: str, view: str) -> dict:
    """
    Fetches and parses one view (consolidated or standalone).
    Returns dict of financial tables + shared metadata (if consolidated).
    """
    url = company_url(symbol, view)
    soup = fetch(session, url)
    if soup is None:
        log.warning(f"{symbol} {view}: 404 / unreachable")
        return {}

    out: dict = {}

    # Financial tables — try each possible section ID until one works
    for table_key, candidate_ids in SECTION_IDS.items():
        for sid in candidate_ids:
            data = parse_financial_table(soup, sid)
            if data:
                out[table_key] = data
                break

    # Shareholding — quarterly + yearly
    out["shareholding"] = parse_shareholding(soup)

    # Metadata only from consolidated (avoid duplicate network + parse)
    if view == "consolidated":
        out["__about"]      = parse_about(soup)
        out["__pros"], out["__cons"] = parse_pros_cons(soup)
        out["__top_ratios"] = parse_top_ratios(soup)
        docs                = parse_documents(soup)
        # announcements are embedded inside the documents section on Screener
        out["__announcements"] = docs.pop("announcements", [])
        out["__documents"]     = docs

    return out


# ── Main scrape ────────────────────────────────────────────────────────────────

def scrape_company(symbol: str, session: requests.Session,
                   include_standalone: bool = True) -> dict:
    """
    Full company scrape — consolidated + standalone.
    Returns the final JSON dict ready to save.
    """
    log.info(f"Scraping: {symbol}")

    result: dict = {
        "symbol":        symbol,
        "scraped_at":    datetime.now(timezone.utc).isoformat(),
        "about":         "",
        "pros":          [],
        "cons":          [],
        "top_ratios":    {},
        "documents":     {"annual_reports": [], "concalls": [], "credit_ratings": []},
        "announcements": [],
        "consolidated":  {},
        "standalone":    {},
    }

    # ── Consolidated
    con = scrape_view(session, symbol, "consolidated")
    result["about"]         = con.pop("__about", "")
    result["pros"]          = con.pop("__pros",  [])
    result["cons"]          = con.pop("__cons",  [])
    result["top_ratios"]    = con.pop("__top_ratios", {})
    result["documents"]     = con.pop("__documents",  result["documents"])
    result["announcements"] = con.pop("__announcements", [])
    result["consolidated"]  = con

    # ── Standalone  (polite pause between the two requests for this company)
    if include_standalone:
        time.sleep(random.uniform(*DELAY_BETWEEN_VIEWS))
        sta = scrape_view(session, symbol, "standalone")
        for k in list(sta.keys()):
            if k.startswith("__"):
                sta.pop(k)
        result["standalone"] = sta

    # ── Log summary
    docs = result["documents"]
    custom_found = [k for k in result["top_ratios"] if k in CUSTOM_RATIO_NAMES]
    log.info(
        f"{symbol} done | about={len(result['about'])}c "
        f"pros={len(result['pros'])} cons={len(result['cons'])} "
        f"custom_ratios={len(custom_found)}/{len(CUSTOM_RATIO_NAMES)} "
        f"AR={len(docs['annual_reports'])} "
        f"concalls={len(docs['concalls'])} "
        f"ratings={len(docs['credit_ratings'])}"
    )
    return result


def save_json(data: dict, symbol: str) -> Path:
    out = STRUCT_DIR / f"{symbol}_screener.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info(f"Saved -> {out}")
    return out


# ── Bulk ──────────────────────────────────────────────────────────────────────

def load_companies() -> list[dict]:
    """
    Load all 3,356 main-board companies from company_master.json.
    Each company gets a 'key' — the identifier used for the Screener URL:
      - NSE symbol  (preferred, works for NSE+BSE and NSE-only companies)
      - BSE code    (fallback for BSE-only companies: screener.in/company/523031/)
    Returns list of dicts: {key, company_name, nse_symbol, bse_code, isin, listed_on, bse_group}
    """
    master_path = STRUCT_DIR / "company_master.json"
    if master_path.exists():
        with open(master_path, encoding="utf-8") as f:
            raw = json.load(f)
        companies = []
        for c in raw:
            nse = c.get("nse_symbol")
            bse = c.get("bse_code")
            # Sanitise — pandas NaN serialises as None in JSON but be safe
            if isinstance(nse, float): nse = None
            if isinstance(bse, float): bse = None
            nse = str(nse).strip() if nse else None
            bse = str(int(float(bse))).strip() if bse else None
            key = nse if nse else bse
            if not key:
                continue
            companies.append({
                "key":          key,
                "company_name": c.get("company_name", ""),
                "nse_symbol":   nse,
                "bse_code":     bse,
                "isin":         c.get("isin") or "",
                "listed_on":    c.get("listed_on") or "",
                "bse_group":    c.get("bse_group") or "",
                "industry":     c.get("industry") or "",
            })
        log.info(f"Loaded {len(companies)} companies from company_master.json")
        return companies

    # Fallback — NSE-only list (backward compat)
    nse_path = STRUCT_DIR / "nse_equity_list.csv"
    if nse_path.exists():
        df = pd.read_csv(nse_path)
        col = next((c for c in ["nse_symbol", "SYMBOL"] if c in df.columns), df.columns[0])
        syms = df[col].dropna().str.strip().tolist()
        log.warning(f"company_master.json not found — falling back to {len(syms)} NSE symbols")
        return [{"key": s, "company_name": "", "nse_symbol": s, "bse_code": None,
                 "isin": "", "listed_on": "NSE", "bse_group": "", "industry": ""} for s in syms]

    log.error("No company list found — run 01_company_list.py first")
    return []


def is_done(key: str, done_set: set[str]) -> bool:
    """Return True if company was already scraped (success or confirmed-404)."""
    return key in done_set


def run_all(session: requests.Session, resume: bool = True):
    companies = load_companies()
    if not companies:
        return

    total = len(companies)

    # Resume: any existing *_screener.json means it was processed
    done: set[str] = set()
    if resume:
        done = {p.stem.replace("_screener", "") for p in STRUCT_DIR.glob("*_screener.json")}
        remaining = total - len(done)
        log.info(f"Resume mode | total={total} done={len(done)} remaining={remaining}")

    errors:    list[str] = []
    not_found: list[str] = []

    _initial_done = len(done)   # companies already done before this run (for periodic-pause counter)
    start_time = time.time()

    for i, company in enumerate(companies):
        key = company["key"]

        if is_done(key, done):
            continue

        try:
            data = scrape_company(key, session, include_standalone=True)

            # Attach master metadata to every JSON
            data["company_name"] = company["company_name"]
            data["nse_symbol"]   = company["nse_symbol"]
            data["bse_code"]     = company["bse_code"]
            data["isin"]         = company["isin"]
            data["listed_on"]    = company["listed_on"]
            data["bse_group"]    = company["bse_group"]
            data["industry"]     = company["industry"]

            # If Screener returned no tables at all → mark as not found
            con_tables = [k for k in data.get("consolidated", {})
                          if k not in ("shareholding",) and isinstance(data["consolidated"][k], dict)]
            if not con_tables and not data.get("about"):
                data["screener_status"] = "not_found"
                not_found.append(key)
            else:
                data["screener_status"] = "ok"

            save_json(data, key)
            done.add(key)

        except KeyboardInterrupt:
            log.info("Interrupted — progress saved. Re-run with --all to resume.")
            break
        except Exception as e:
            log.error(f"[{i+1}/{total}] {key}: {e}")
            errors.append(key)

        # ── Polite pacing ──────────────────────────────────────────────────────
        # Count how many we've actually scraped this run (not resumed)
        scraped_this_run = len(done) - _initial_done

        # Every PAUSE_EVERY companies: take a longer rest (disabled when PAUSE_EVERY = 0)
        if PAUSE_EVERY and scraped_this_run > 0 and scraped_this_run % PAUSE_EVERY == 0:
            rest = random.uniform(*PAUSE_DURATION)
            log.info(f"Periodic rest after {scraped_this_run} scraped — sleeping {rest:.0f}s")
            time.sleep(rest)
        else:
            time.sleep(random.uniform(*DELAY_BETWEEN_COMPANIES))

        if (i + 1) % 100 == 0:
            elapsed  = time.time() - start_time
            rate     = (i + 1) / elapsed * 60  # companies per minute
            eta_min  = (total - i - 1) / max(rate, 0.1)
            log.info(
                f"Progress {i+1}/{total} | "
                f"done={len(done)} errors={len(errors)} not_found={len(not_found)} | "
                f"rate={rate:.1f}/min ETA={eta_min:.0f}min"
            )

    elapsed = time.time() - start_time
    log.info(
        f"=== DONE === {len(done)}/{total} scraped in {elapsed/60:.1f} min | "
        f"errors={len(errors)} not_found={len(not_found)}"
    )
    if errors:
        log.info(f"Error keys: {errors[:30]}")
    if not_found:
        log.info(f"Not on Screener: {not_found[:30]}")


# ── Verification ──────────────────────────────────────────────────────────────

def verify(print_report: bool = True) -> dict:
    """
    Check download completeness against company_master.json.
    Returns summary dict.
    """
    companies = load_companies()
    total     = len(companies)
    all_keys  = {c["key"] for c in companies}

    json_files = list(STRUCT_DIR.glob("*_screener.json"))
    done_keys  = {p.stem.replace("_screener", "") for p in json_files}

    missing    = sorted(all_keys - done_keys)
    extra      = sorted(done_keys - all_keys)      # scraped but not in master (e.g. test runs)

    ok, not_found_keys, empty = [], [], []
    for p in json_files:
        key = p.stem.replace("_screener", "")
        if key not in all_keys:
            continue
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            status = d.get("screener_status", "ok")
            if status == "not_found":
                not_found_keys.append(key)
            elif d.get("consolidated"):
                ok.append(key)
            else:
                empty.append(key)
        except Exception:
            empty.append(key)

    summary = {
        "total_in_master":   total,
        "total_json_files":  len(json_files),
        "ok":                len(ok),
        "not_on_screener":   len(not_found_keys),
        "empty_or_error":    len(empty),
        "missing":           len(missing),
        "extra_test_files":  len(extra),
        "pct_complete":      round(len(done_keys & all_keys) / total * 100, 1),
    }

    if print_report:
        print("\n" + "=" * 60)
        print("  SCREENER DOWNLOAD STATUS")
        print("=" * 60)
        print(f"  Companies in master    : {total:,}")
        print(f"  JSON files on disk     : {len(json_files):,}")
        print(f"  Successfully scraped   : {len(ok):,}")
        print(f"  Not on Screener (404)  : {len(not_found_keys):,}")
        print(f"  Empty / parse error    : {len(empty):,}")
        print(f"  Still missing          : {len(missing):,}")
        print(f"  Completion             : {summary['pct_complete']}%")
        if missing[:10]:
            print(f"\n  First missing: {missing[:10]}")
        if not_found_keys[:5]:
            print(f"  Not on Screener sample: {not_found_keys[:5]}")
        print()

    return summary


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Screener.in company scraper")
    p.add_argument("--symbol",          type=str,  help="Single symbol, e.g. RELIANCE")
    p.add_argument("--standalone-only", action="store_true",
                   help="Scrape standalone view only (skip consolidated)")
    p.add_argument("--all",             action="store_true", help="Scrape all 3,356 companies")
    p.add_argument("--no-resume",       action="store_true", help="Re-scrape even if JSON exists")
    p.add_argument("--verify",          action="store_true", help="Check download completeness")
    args = p.parse_args()

    session = get_session()

    if args.symbol:
        # Single company test
        include_standalone = not args.standalone_only
        data = scrape_company(args.symbol, session, include_standalone=include_standalone)
        out  = save_json(data, args.symbol)

        # ── Human-readable summary ──
        print(f"\n{'='*60}")
        print(f"  {args.symbol}")
        print(f"{'='*60}")
        print(f"About     : {data['about'][:180] or '(empty — check session cookie)'}")
        print(f"Pros      : {len(data['pros'])} items")
        print(f"Cons      : {len(data['cons'])} items")
        print(f"Top ratios: {len(data['top_ratios'])} total")
        custom_found = [k for k in data['top_ratios'] if k in CUSTOM_RATIO_NAMES]
        print(f"  Custom ratios present: {len(custom_found)}/{len(CUSTOM_RATIO_NAMES)}")
        if custom_found:
            for k in custom_found[:10]:
                print(f"    {k}: {data['top_ratios'][k]}")
        print()
        print("Consolidated tables:")
        for k, v in data["consolidated"].items():
            if isinstance(v, dict):
                rows = v.get("rows", []) if "rows" in v else []
                cols = v.get("columns", [])
                if k == "shareholding":
                    for period, sv in v.items():
                        if sv:
                            print(f"  shareholding.{period}: {len(sv.get('rows',[]))} rows x {len(sv.get('columns',[]))} cols")
                else:
                    print(f"  {k}: {len(rows)} metrics x {len(cols)+1} periods")
        print()
        print("Standalone tables:")
        for k, v in data["standalone"].items():
            if isinstance(v, dict):
                rows = v.get("rows", []) if "rows" in v else []
                cols = v.get("columns", [])
                if k == "shareholding":
                    for period, sv in v.items():
                        if sv:
                            print(f"  shareholding.{period}: {len(sv.get('rows',[]))} rows x {len(sv.get('columns',[]))} cols")
                else:
                    print(f"  {k}: {len(rows)} metrics x {len(cols)+1} periods")
        print()
        docs = data["documents"]
        print(f"Annual Reports : {len(docs['annual_reports'])}")
        if docs['annual_reports']:
            for ar in docs['annual_reports'][:3]:
                print(f"  {ar['label']} -> {ar['url'][:60]}")
        print(f"Concalls       : {len(docs['concalls'])}")
        print(f"Credit Ratings : {len(docs['credit_ratings'])}")
        print(f"Announcements  : {len(data['announcements'])}")
        print(f"\nSaved: {out}")

    elif args.all:
        run_all(session, resume=not args.no_resume)

    elif args.verify:
        verify()

    else:
        print(__doc__)
        print(f"\nSession: {'ACTIVE (authenticated)' if SESSION_ID else 'NOT SET'}")
        print(f"Custom ratios tracked: {len(CUSTOM_RATIO_NAMES)}")
        companies = load_companies()
        print(f"Companies to scrape:   {len(companies):,}")
        # Quick status without full verify
        done = sum(1 for p in STRUCT_DIR.glob("*_screener.json"))
        print(f"JSON files on disk:    {done:,}")
