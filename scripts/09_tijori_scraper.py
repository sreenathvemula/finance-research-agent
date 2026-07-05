#!/usr/bin/env python3
"""
09_tijori_scraper.py — Tijori Finance company scraper (comprehensive).

Captures from 4 sources per company:
  1. /company/<slug>/           — op_metrics, capex, debt, market_share,
                                   revenue_mix, forensics, peers, brands,
                                   corporate_actions, suppliers
  2. Excel download links (embedded in page HTML) — full financials as Excel
     (standalone + consolidated): balance_sheet, profit_loss, cash_flow,
     ratios, quarterly_results — far more granular than HTML parsing
  3. /company/<slug>/benchmarking/ — peer 5yr-avg comparison table
  4. /company/<slug>/shareholding/ — time-series by holder category

Auth: TIJORI_SESSION_ID cookie (sessionid) in .env. Personal account — gentle pacing.

Usage:
  python 09_tijori_scraper.py --slug reliance-industries-limited
  python 09_tijori_scraper.py --symbol RELIANCE
  python 09_tijori_scraper.py --all --delay 0.4         # full crawl (resumable)
  python 09_tijori_scraper.py --all --topup --delay 0.4 # add missing sections only
  python 09_tijori_scraper.py --all --limit 100
"""
import argparse, io, json, os, re, time, logging
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd

from dotenv import load_dotenv
from curl_cffi import requests as cffi
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent.parent
STRUCT = ROOT / "data" / "structured"
TIJORI = ROOT / "data" / "tijori"
TIJORI.mkdir(parents=True, exist_ok=True)
load_dotenv(ROOT / ".env")
SID = os.getenv("TIJORI_SESSION_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("tijori")

BASE = "https://www.tijorifinance.com"
OP_API = BASE + "/api/v1/ind/company_op_metrics/{cid}/{mid}/"
EXTRA_ENDPOINTS = {
    "capex":        BASE + "/api/v1/ind/capex/{cid}/",
    "debt":         BASE + "/api/v1/ind/debt/{cid}/",
    "market_share": BASE + "/api/v1/ind/company_market_share_data/{cid}/",
}

FULL_KEYS = ("revenue_mix", "financials", "benchmarking", "shareholding_trend")


def make_session():
    s = cffi.Session(impersonate="chrome")
    s.cookies.set("sessionid", SID, domain=".tijorifinance.com")
    s.headers.update({"Referer": BASE + "/"})
    return s


def _norm(name):
    n = (name or "").lower()
    n = re.sub(r"&", " and ", n)
    n = re.sub(r"\b(ltd|limited|the|inc|corp|co|company|pvt|private)\b", " ", n)
    n = re.sub(r"[^a-z0-9]+", "", n)
    return n


def load_symbol_index():
    master = STRUCT / "company_master.json"
    if not master.exists():
        return {}
    idx = {}
    for r in json.loads(master.read_text(encoding="utf-8")):
        sym = (r.get("nse_symbol") or "").strip()
        nm = r.get("company_name") or ""
        if sym and nm:
            idx[_norm(nm)] = sym
    return idx


# ── Overview page parsing ─────────────────────────────────────────────────────

def parse_company(html):
    cid_m = re.search(r"/company_op_metrics/(\d+)/", html)
    company_id = cid_m.group(1) if cid_m else None
    soup = BeautifulSoup(html, "lxml")
    name_el = soup.select_one("h1") or soup.find("title")
    company_name = name_el.get_text(strip=True) if name_el else ""
    metrics = []
    for li in soup.select("li.Company_opmetric"):
        mid = li.get("metricid")
        if not mid:
            continue
        name = re.sub(r"Source$", "", li.get_text(strip=True)).strip()
        metrics.append({
            "id": mid,
            "name": name,
            "unit": (li.get("unit") or "").strip(),
            "indent": int(li.get("indent") or 0),
            "parent": li.get("parent") or "0",
        })
    return company_id, company_name, metrics


def parse_page_extras(html):
    """Server-rendered inline sections: revenue_mix, forensics, peers, brands,
    corporate_actions, suppliers. Also returns excel_links for financial download."""
    soup = BeautifulSoup(html, "lxml")
    ex = {}
    cid_el = soup.find(attrs={"company-id": True})
    ex["company_id_attr"] = cid_el.get("company-id") if cid_el else None

    # Excel download links (per-company token embedded in href)
    xlsx_urls = re.findall(
        r'https?://excel\.tijorifinance\.com/company/excel/\d+/\w+/(?:stand|cons)_\d+\.xlsx', html)
    ex["excel_links"] = {
        ("standalone" if "stand_" in u else "consolidated"): u for u in xlsx_urls
    }

    rroot = soup.find(id="revenuemix")
    rmix = {}
    if rroot:
        for chart in rroot.select("[chart-data]"):
            cont = chart.find_parent(class_="charts_cont") or chart.parent
            h = cont.find(["h4", "h3"]) if cont else None
            title = (h.get_text(strip=True) if h else chart.get("id", "chart"))
            raw = chart.get("chart-data", "")
            for attempt in (raw, raw.replace("&quot;", '"')):
                try:
                    rmix[title] = json.loads(attempt); break
                except (ValueError, TypeError):
                    continue
    ex["revenue_mix"] = rmix or None

    pj = soup.find(id="peers_table_data")
    if pj and pj.get_text(strip=True):
        try:
            ex["peers"] = json.loads(pj.get_text())
        except (ValueError, TypeError):
            ex["peers"] = None

    fel = soup.find(id="forensics")
    if fel:
        rows = []
        for row in fel.select("tr, li"):
            t = row.get_text(" ", strip=True)
            if t and len(t) > 5 and "View More" not in t:
                rows.append(t[:220])
        ex["forensics"] = rows or None

    # Brands
    brands_el = soup.find(class_=re.compile(r"brand", re.I))
    if brands_el:
        ex["brands"] = [b.get_text(strip=True) for b in brands_el.find_all(["li", "span", "a"])
                        if b.get_text(strip=True) and len(b.get_text(strip=True)) < 60]

    # Corporate actions (dividends, bonuses, etc.)
    ca_el = soup.find(id=re.compile(r"corporate|dividend|bonus", re.I))
    if ca_el:
        actions = []
        for row in ca_el.select("tr"):
            cells = [c.get_text(strip=True) for c in row.find_all("td")]
            if len(cells) >= 2 and cells[0]:
                actions.append(cells[:3])
        ex["corporate_actions"] = actions or None

    # Suppliers
    sup_el = soup.find(class_=re.compile(r"supplier", re.I)) or soup.find(id=re.compile(r"supplier", re.I))
    if sup_el:
        ex["suppliers"] = [s.get_text(strip=True) for s in sup_el.find_all(["li", "a"])
                           if s.get_text(strip=True)]

    return ex


# ── Excel financial download ──────────────────────────────────────────────────

def _excel_sheet_to_dict(content_bytes, sheet_name):
    """Convert one Excel sheet to {metric_name: {date: value}} mapping."""
    try:
        df = pd.read_excel(io.BytesIO(content_bytes), sheet_name=sheet_name)
    except Exception:
        return None
    if df.empty or df.columns[0].lower() not in ("dates", "date", "unnamed: 0"):
        return None
    df = df.rename(columns={df.columns[0]: "metric"})
    df = df.dropna(subset=["metric"])
    df["metric"] = df["metric"].astype(str).str.strip()
    date_cols = [str(c) for c in df.columns[1:]]
    metrics = {}
    for _, row in df.iterrows():
        name = row["metric"]
        if not name or name == "nan":
            continue
        vals = {}
        for col, dcol in zip(df.columns[1:], date_cols):
            v = row[col]
            if pd.notna(v) and v != 0 or v == 0:
                try:
                    vals[dcol] = float(v) if pd.notna(v) else None
                except (ValueError, TypeError):
                    vals[dcol] = str(v) if pd.notna(v) else None
        metrics[name] = {k: v for k, v in vals.items() if v is not None}
    return {"dates": date_cols, "metrics": metrics} if metrics else None


def fetch_excel_financials(session, excel_links):
    """Download Excel files using embedded links and return structured data."""
    if not excel_links:
        return None
    result = {}
    sheet_key = {
        "BalanceSheet": "balance_sheet",
        "Profit&Loss":  "profit_loss",
        "CashFlow":     "cash_flow",
        "Ratios":       "ratios",
        "QuarterlyResults": "quarterly",
    }
    for typ, url in excel_links.items():
        try:
            r = session.get(url, timeout=60)
            if r.status_code != 200:
                continue
            content = r.content
            xf = pd.ExcelFile(io.BytesIO(content))
            typ_data = {}
            for sheet in xf.sheet_names:
                key = sheet_key.get(sheet, sheet.lower().replace(" ", "_"))
                parsed = _excel_sheet_to_dict(content, sheet)
                if parsed:
                    typ_data[key] = parsed
            if typ_data:
                result[typ] = typ_data
        except Exception:
            pass
    return result or None


# ── Benchmarking page parsing ─────────────────────────────────────────────────

def parse_benchmarking_page(html):
    """Parse peer comparison table (5yr averages, ratios, shareholding metrics)."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find(id="bchTable")
    if not table:
        return {}

    headers = [th.get_text(strip=True) for th in table.select("thead th")]
    company_names = [h for h in headers if h]

    rows = []
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td")
        if len(cells) < 2:
            continue
        metric = cells[0].get_text(strip=True)
        if not metric or metric in ("Operational Metrics", "Financials", "Shareholdings"):
            continue
        values = {}
        for i, c in enumerate(cells[1:]):
            raw = re.sub(r"\s+", " ", c.get_text(strip=True)).strip()
            # Strip trailing unit labels; extract leading number if present
            num_m = re.match(r"^(-?\d[\d,.]*)", raw.replace(",", ""))
            if i < len(company_names):
                if not raw or raw.lower() in ("n/a", "-", "—"):
                    values[company_names[i]] = None
                elif num_m:
                    try:
                        values[company_names[i]] = float(num_m.group(1))
                    except ValueError:
                        values[company_names[i]] = raw
                else:
                    values[company_names[i]] = raw or None
        if any(v is not None for v in values.values()):
            rows.append({"metric": metric, "values": values})

    return {"companies": company_names, "rows": rows} if rows else {}


# ── Shareholding page parsing ─────────────────────────────────────────────────

def parse_shareholding_page(html):
    """Extract shareholding time series from var trendData script."""
    m = re.search(r"var\s+trendData\s*=\s*(\[.+?\]);", html, re.DOTALL)
    if not m:
        return {}
    import ast
    try:
        # Tijori uses single-quoted JS syntax, not JSON
        raw = ast.literal_eval(m.group(1))
    except Exception:
        try:
            raw = json.loads(m.group(1))
        except (ValueError, TypeError):
            return {}

    result = {}
    for series in raw:
        name = series.get("name", "")
        pts = series.get("data") or []
        converted = []
        for pt in pts:
            if isinstance(pt, list) and len(pt) == 2 and pt[1] is not None:
                try:
                    d = datetime.utcfromtimestamp(pt[0] / 1000).strftime("%Y-%m-%d")
                    converted.append([d, pt[1]])
                except (ValueError, OSError, TypeError):
                    continue
        if converted:
            result[name] = converted
    return result


# ── Op-metric series fetching ─────────────────────────────────────────────────

def fetch_series(session, cid, mid):
    try:
        r = session.get(OP_API.format(cid=cid, mid=mid), timeout=25)
        if r.status_code != 200:
            return None, None
        j = r.json()
        if not isinstance(j, dict):
            return None, None
    except Exception:
        return None, None
    raw = j.get("data") or []
    series = []
    for pt in raw:
        if isinstance(pt, list) and len(pt) == 2 and pt[1] is not None:
            try:
                d = datetime.utcfromtimestamp(pt[0] / 1000).strftime("%Y-%m-%d")
                series.append([d, pt[1]])
            except (ValueError, OSError, TypeError):
                continue
    peers = [{"company_id": p.get("company_id"), "name": p.get("name"),
              "metric_id": p.get("mapped_metric_id")} for p in (j.get("peers") or [])]
    return series, peers


def _epoch_series(points):
    out = []
    for pt in points or []:
        if isinstance(pt, list) and len(pt) == 2 and pt[1] is not None:
            try:
                out.append([datetime.utcfromtimestamp(pt[0] / 1000).strftime("%Y-%m-%d"), pt[1]])
            except (ValueError, OSError, TypeError):
                continue
    return out


def fetch_blob(session, url):
    try:
        r = session.get(url, timeout=25)
        if r.status_code != 200:
            return None
        j = r.json()
    except Exception:
        return None
    d = j.get("data") if isinstance(j, dict) else j
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except (ValueError, TypeError):
            return None
    if isinstance(d, list) and d and isinstance(d[0], dict) and "data" in d[0]:
        return [{"name": s.get("name"), "series": _epoch_series(s.get("data"))} for s in d]
    if isinstance(d, list):
        return _epoch_series(d)
    return d


# ── Main scrape function ──────────────────────────────────────────────────────

def scrape_company(session, slug, sym_index, force=False, topup=False, delay=0.3):
    out = TIJORI / f"{slug}.json"
    existing = None
    if out.exists():
        try:
            existing = json.loads(out.read_text(encoding="utf-8"))
        except Exception:
            pass

    if existing and not force:
        if all(k in existing for k in FULL_KEYS):
            return "skip"
        if topup:
            # Only add missing sections to already-scraped file
            return _topup_company(session, slug, existing, delay, out)

    # Full scrape: overview page
    try:
        r = session.get(f"{BASE}/company/{slug}/", timeout=40)
    except Exception as e:
        return f"err:{type(e).__name__}"
    if r.status_code != 200:
        return f"http:{r.status_code}"

    cid, name, metrics = parse_company(r.text)
    extras = parse_page_extras(r.text)
    cid = cid or extras.get("company_id_attr")
    matched = sym_index.get(_norm(name))

    excel_links = extras.get("excel_links") or {}
    record = {
        "slug": slug,
        "tijori_company_id": cid,
        "company_name": name,
        "matched_symbol": matched,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "num_op_metrics": len(metrics),
        "op_metrics": [],
        "capex": None, "debt": None, "market_share": None,
        "revenue_mix": extras.get("revenue_mix"),
        "peers": extras.get("peers"),
        "forensics": extras.get("forensics"),
        "brands": extras.get("brands"),
        "corporate_actions": extras.get("corporate_actions"),
        "suppliers": extras.get("suppliers"),
        "financials": None,
        "benchmarking": None,
        "shareholding_trend": None,
    }

    if cid:
        for m in metrics:
            time.sleep(delay)
            series, peers = fetch_series(session, cid, m["id"])
            if series:
                m = {**m, "series": series, "peers": peers}
            record["op_metrics"].append(m)
        for key, tmpl in EXTRA_ENDPOINTS.items():
            time.sleep(delay)
            record[key] = fetch_blob(session, tmpl.format(cid=cid))

    # Excel financials (download directly from embedded links in page HTML)
    if excel_links:
        time.sleep(delay)
        record["financials"] = fetch_excel_financials(session, excel_links)

    # Benchmarking page
    time.sleep(delay)
    try:
        rb = session.get(f"{BASE}/company/{slug}/benchmarking/", timeout=40)
        if rb.status_code == 200:
            record["benchmarking"] = parse_benchmarking_page(rb.text) or None
    except Exception:
        pass

    # Shareholding page
    time.sleep(delay)
    try:
        rs = session.get(f"{BASE}/company/{slug}/shareholding/", timeout=40)
        if rs.status_code == 200:
            record["shareholding_trend"] = parse_shareholding_page(rs.text) or None
    except Exception:
        pass

    out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    n_with = sum(1 for m in record["op_metrics"] if m.get("series"))
    tags = "+".join(k for k in ("capex","debt","market_share","revenue_mix","forensics",
                                "financials","benchmarking","shareholding_trend")
                    if record.get(k))
    return (f"ok:{len(metrics)}m/{n_with}s/[{tags or 'none'}]"
            + (f"/{matched}" if matched else "/unmapped"))


def _topup_company(session, slug, existing, delay, out):
    """Fetch only missing sections (financials/benchmarking/shareholding_trend) for existing file."""
    changed = False
    missing = [k for k in FULL_KEYS if not existing.get(k)]
    if not missing:
        return "skip"

    for section in missing:
        time.sleep(delay)
        try:
            if section == "financials":
                # Re-fetch overview page to get fresh Excel links
                r = session.get(f"{BASE}/company/{slug}/", timeout=40)
                if r.status_code == 200:
                    ex = parse_page_extras(r.text)
                    xl = fetch_excel_financials(session, ex.get("excel_links") or {})
                    existing["financials"] = xl
                    changed = True
            elif section == "benchmarking":
                r = session.get(f"{BASE}/company/{slug}/benchmarking/", timeout=40)
                if r.status_code == 200:
                    existing["benchmarking"] = parse_benchmarking_page(r.text) or None
                    changed = True
            elif section == "shareholding_trend":
                r = session.get(f"{BASE}/company/{slug}/shareholding/", timeout=40)
                if r.status_code == 200:
                    existing["shareholding_trend"] = parse_shareholding_page(r.text) or None
                    changed = True
            elif section == "revenue_mix":
                r = session.get(f"{BASE}/company/{slug}/", timeout=40)
                if r.status_code == 200:
                    extras = parse_page_extras(r.text)
                    existing["revenue_mix"] = extras.get("revenue_mix")
                    existing.setdefault("peers", extras.get("peers"))
                    existing.setdefault("forensics", extras.get("forensics"))
                    changed = True
        except Exception:
            pass

    if changed:
        existing["scraped_at"] = datetime.now(timezone.utc).isoformat()
        out.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        return f"topup:{'+'.join(missing)}"
    return "topup:noop"


def get_sitemap_slugs(session):
    sm = session.get(f"{BASE}/sitemap.xml", timeout=40).text
    urls = re.findall(r"<loc>(?:https?://[^<]*?)/company/([a-z0-9-]+)/?</loc>", sm)
    return sorted(set(urls))


def resolve_symbol_to_slug(session, symbol):
    master = STRUCT / "company_master.json"
    recs = json.loads(master.read_text(encoding="utf-8"))
    name = next((r.get("company_name") for r in recs
                 if (r.get("nse_symbol") or "").upper() == symbol.upper()), None)
    if not name:
        return None
    target = _norm(name)
    for slug in get_sitemap_slugs(session):
        if _norm(slug.replace("-", " ")) == target:
            return slug
    toks = [t for t in re.sub(r"[^a-z0-9 ]", " ", name.lower()).split() if t][:2]
    if toks:
        pref = "-".join(toks)
        for slug in get_sitemap_slugs(session):
            if slug.startswith(pref):
                return slug
    return None


def run_all(limit, delay, force, topup):
    session = make_session()
    sym_index = load_symbol_index()
    slugs = get_sitemap_slugs(session)
    if limit:
        slugs = slugs[:limit]
    mode = "topup" if topup else "full"
    log.info(f"Tijori {mode}: {len(slugs)} companies, delay {delay}s")
    counts, done = {}, 0
    for slug in slugs:
        status = scrape_company(session, slug, sym_index, force, topup, delay)
        key = status.split(":")[0]
        counts[key] = counts.get(key, 0) + 1
        done += 1
        if done % 100 == 0:
            log.info(f"  {done}/{len(slugs)} — {counts}")
        time.sleep(delay)
    log.info(f"Done. {counts}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slug", help="Tijori company slug")
    ap.add_argument("--symbol", help="NSE symbol")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--topup", action="store_true",
                    help="Only fetch missing sections (financials/benchmarking/shareholding) for existing files")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.35)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if not SID:
        print("TIJORI_SESSION_ID not set in .env"); return

    if args.slug or args.symbol:
        session = make_session()
        sym_index = load_symbol_index()
        slug = args.slug
        if not slug:
            slug = resolve_symbol_to_slug(session, args.symbol)
            if not slug:
                print(f"Could not resolve {args.symbol} to a Tijori slug"); return
            print(f"Resolved {args.symbol} -> {slug}")
        status = scrape_company(session, slug, sym_index, force=True, delay=args.delay)
        print(f"{slug}: {status}")
        p = TIJORI / f"{slug}.json"
        if p.exists():
            rec = json.loads(p.read_text(encoding="utf-8"))
            print(f"  company_id={rec['tijori_company_id']} name={rec['company_name']!r} "
                  f"matched={rec['matched_symbol']}")
            fin = rec.get("financials") or {}
            print(f"  financials sections: {list(fin.keys())}")
            bench = rec.get("benchmarking") or {}
            print(f"  benchmarking rows: {len(bench.get('rows', []))}")
            sh = rec.get("shareholding_trend") or {}
            print(f"  shareholding series: {list(sh.keys())}")
            for m in rec["op_metrics"]:
                ser = m.get("series") or []
                rng = f"{ser[0][0]}..{ser[-1][0]} ({len(ser)}pts)" if ser else "no series"
                print(f"    [{m['id']:>5}] {m['name'][:42]:42} {m['unit']:>8}  {rng}")
    elif args.all:
        run_all(args.limit, args.delay, args.force, args.topup)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
