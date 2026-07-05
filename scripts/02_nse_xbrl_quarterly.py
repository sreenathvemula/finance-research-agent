#!/usr/bin/env python3
"""
02_nse_xbrl_quarterly.py — Download & parse Indian quarterly result XBRL.

Indian listed companies file ONE Ind-AS XBRL (BSE-taxonomy, `in-bse-fin`) used by
both exchanges. BSE's own API is Akamai-IP-walled from datacenter IPs, but NSE
mirrors the *same* XBRL on its archive and is reachable via curl_cffi (Chrome TLS
impersonation). This script uses the NSE route.

Endpoints:
  GET nseindia.com/api/corporates-financial-results?index=equities&symbol=SYM&period=Quarterly
  -> filings up to the Dec-2024 quarter; key `xbrl` = plain .xml (in-bse-fin taxonomy).
  GET nseindia.com/api/integrated-filing-results?index=equities&symbol=SYM
      &period_ended=Quarterly&type=Integrated Filing- Financials
  -> Mar-2025 quarter onward (SEBI Integrated Filing regime); key `xbrl` = plain .xml
     (SEBI in-capmkt taxonomy — same tag names and One*/Four* context convention).

Coverage: real XBRL exists ~2018→present (pre-2018 links 404), each filing with P&L
line items, cost breakdown, per-segment revenue & profit, and notes — far deeper
than screener's ~12 quarters.

Outputs:
  data/nse_xbrl/<SYMBOL>/<period_end>_<C|S>.xml   raw archived XBRL
  data/structured/<SYMBOL>_xbrl.json              parsed structured quarters

Usage:
  python 02_nse_xbrl_quarterly.py --symbol RELIANCE        # single company (test)
  python 02_nse_xbrl_quarterly.py --symbol RELIANCE --period Annual
  python 02_nse_xbrl_quarterly.py --all                    # all companies (resumable)
  python 02_nse_xbrl_quarterly.py --all --workers 6 --limit 50
"""
import argparse, json, re, time, logging, sys
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import xml.etree.ElementTree as ET

from curl_cffi import requests as cffi

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
XBRL_RAW = DATA / "nse_xbrl"
STRUCT = DATA / "structured"
XBRL_RAW.mkdir(parents=True, exist_ok=True)
STRUCT.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("xbrl")

NSE = "https://www.nseindia.com"
RESULTS_API = f"{NSE}/api/corporates-financial-results"
INTEGRATED_API = f"{NSE}/api/integrated-filing-results"
# taxonomy namespaces: in-bse-fin (filings ≤ Dec-2024) and SEBI in-capmkt
# ("Integrated Filing (Financials)", mandatory from the Dec-2024 quarter; NSE serves
# those only via the integrated-filing endpoint, starting Mar-2025)
FIN_NS_MARKERS = ("bseindia.com/xbrl/fin", "sebi.gov.in/xbrl")
XBRLI = "{http://www.xbrl.org/2003/instance}"

# ── friendly tag map: local-name -> (clean_name, kind) ───────────────────────
# kind: money (÷1e7 → ₹ crore) | pershare | ratio | count
TAGS = {
    "RevenueFromOperations":                         ("Revenue from operations", "money"),
    "OtherIncome":                                   ("Other income", "money"),
    "Income":                                        ("Total income", "money"),
    "CostOfMaterialsConsumed":                       ("Cost of materials consumed", "money"),
    "PurchasesOfStockInTrade":                       ("Purchases of stock-in-trade", "money"),
    "ChangesInInventoriesOfFinishedGoodsWorkInProgressAndStockInTrade":
                                                     ("Changes in inventories", "money"),
    "EmployeeBenefitExpense":                        ("Employee benefit expense", "money"),
    "FinanceCosts":                                  ("Finance costs", "money"),
    "DepreciationDepletionAndAmortisationExpense":   ("Depreciation & amortisation", "money"),
    "OtherExpenses":                                 ("Other expenses", "money"),
    "Expenses":                                      ("Total expenses", "money"),
    "ProfitBeforeExceptionalItemsAndTax":            ("Profit before exceptional items & tax", "money"),
    "ExceptionalItemsBeforeTax":                     ("Exceptional items", "money"),
    "ProfitBeforeTax":                               ("Profit before tax", "money"),
    "CurrentTax":                                    ("Current tax", "money"),
    "DeferredTax":                                   ("Deferred tax", "money"),
    "TaxExpense":                                    ("Total tax expense", "money"),
    "ProfitLossForPeriodFromContinuingOperations":   ("Profit from continuing operations", "money"),
    "ProfitLossForPeriod":                           ("Net profit for period", "money"),
    "OtherComprehensiveIncomeNetOfTaxes":            ("Other comprehensive income", "money"),
    "ComprehensiveIncomeForThePeriod":               ("Total comprehensive income", "money"),
    "PaidUpValueOfEquityShareCapital":               ("Paid-up equity share capital", "money"),
    "BasicEarningsLossPerShareFromContinuingOperations":   ("Basic EPS", "pershare"),
    "DilutedEarningsLossPerShareFromContinuingOperations": ("Diluted EPS", "pershare"),
    "FaceValueOfEquityShareCapital":                 ("Face value", "pershare"),
    "DebtEquityRatio":                               ("Debt-equity ratio", "ratio"),
    "DebtServiceCoverageRatio":                      ("Debt service coverage ratio", "ratio"),
    "InterestServiceCoverageRatio":                  ("Interest service coverage ratio", "ratio"),
}
SEG_REVENUE_TAGS = {"SegmentRevenue", "SegmentRevenueFromOperations"}
SEG_RESULT_TAGS  = {"SegmentProfitLossBeforeTaxAndFinanceCosts", "SegmentProfitBeforeTax"}
# segment-value tables only; ReportableSegmentAssets/LiabilitiesAxis carry just names
SEG_AXES = {"ReportableSegmentsAxis", "ReportableSegmentsFinanceCostsAxis"}
NOTE_TAGS = {
    "DisclosureOfNotesOnFinancialResultsExplanatoryTextBlock": "notes_financial",
    "DisclosureOfNotesOnSegmentsExplanatoryTextBlock":         "notes_segment",
}

# column prefixes in the BSE template: One = current quarter, Four = year-to-date
Q_DUR, Q_INST = "OneD", "OneI"
YTD_DUR, YTD_INST = "FourD", "FourI"


def make_session() -> "cffi.Session":
    s = cffi.Session(impersonate="chrome")
    s.get(NSE, timeout=30)
    s.get(f"{NSE}/companies-listing/corporate-filings-financial-results", timeout=30)
    return s


def get_filings(session, symbol, period="Quarterly"):
    try:
        r = session.get(RESULTS_API,
                        params={"index": "equities", "symbol": symbol, "period": period},
                        timeout=30)
        if r.status_code != 200:
            return []
        j = r.json()
        return j if isinstance(j, list) else j.get("data", [])
    except Exception as e:
        log.debug(f"{symbol}: filings error {e}")
        return []


def get_integrated_filings(session, symbol):
    """Filings under SEBI's Integrated Filing (Financials) regime (Mar-2025 quarter on).

    Rows are normalized to the corporates-financial-results shape so the download
    loop can treat both sources identically. Revisions share a qe_Date; rows are
    ordered latest-broadcast first so the dedup in process_symbol keeps the revision.
    """
    try:
        r = session.get(INTEGRATED_API,
                        params={"index": "equities", "symbol": symbol,
                                "period_ended": "Quarterly",
                                "type": "Integrated Filing- Financials"},
                        timeout=30)
        if r.status_code != 200:
            return []
        j = r.json()
        rows = j.get("data", []) if isinstance(j, dict) else j
    except Exception as e:
        log.debug(f"{symbol}: integrated filings error {e}")
        return []
    rows.sort(key=lambda f: f.get("broadcast_Date") or "", reverse=True)
    out = []
    for f in rows:
        out.append({
            "xbrl": f.get("xbrl", ""),
            "consolidated": "Consolidated" if f.get("consolidated") == "Consolidated"
                            else "Non-Consolidated",
            "toDate": f.get("qe_Date") or "",          # e.g. "31-MAR-2026"
            "fromDate": "",
            "audited": f.get("audited", ""),
            "filingDate": f.get("broadcast_Date") or "",
            "relatingTo": "",
            "financialYear": "",
        })
    return out


def _num(text, kind):
    try:
        v = float(text)
    except (TypeError, ValueError):
        return None
    if kind == "money":
        return round(v / 1e7, 2)          # absolute ₹ → ₹ crore
    return round(v, 4)


def _segment_contexts(root):
    """Map current-quarter segment context id -> segment index (int).

    Context *ids* differ by taxonomy (in-bse-fin: OneReportableSegmentRevenue01D,
    in-capmkt: OneReportable1D / OneReportableFinance1D), but both declare the
    reportable-segment member on the context, so key off the axis/member instead.
    Totals (OneD/FourD) have no member; YTD columns are excluded by the One prefix.
    """
    out = {}
    for ctx in root.iter(XBRLI + "context"):
        cid = ctx.get("id", "")
        if not cid.startswith("One"):
            continue
        for m in ctx.iter():
            if not m.tag.endswith("}explicitMember"):
                continue
            axis = (m.get("dimension") or "").split(":")[-1]
            if axis not in SEG_AXES:
                continue
            num = re.search(r"(\d+)Member$", (m.text or "").strip())
            if num:
                out[cid] = int(num.group(1))
    return out


def parse_xbrl(xml_text):
    """Parse one in-bse-fin / in-capmkt XBRL file → structured dict (current-quarter focus)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    facts_q, facts_ytd, meta, notes = {}, {}, {}, {}
    seg_name, seg_rev, seg_res = {}, {}, {}
    seg_ctx = _segment_contexts(root)

    for el in root.iter():
        if "}" not in el.tag:
            continue
        uri, local = el.tag[1:].split("}")
        if not any(m in uri for m in FIN_NS_MARKERS):
            continue
        ctx = el.get("contextRef", "")
        txt = (el.text or "").strip()
        if not txt:
            continue

        # metadata / notes (string-valued)
        if local == "NatureOfReportStandaloneConsolidated":
            meta["nature"] = txt
        elif local == "ReportingQuarter":
            meta["reporting_quarter"] = txt
        elif local == "DateOfStartOfReportingPeriod":
            meta["period_start"] = txt
        elif local == "DateOfEndOfReportingPeriod":
            meta["period_end"] = txt
        elif local == "WhetherResultsAreAuditedOrUnaudited":
            meta["audited"] = txt
        elif local in NOTE_TAGS:
            notes[NOTE_TAGS[local]] = txt

        # headline P&L (current quarter = OneD/OneI; YTD = FourD/FourI)
        if local in TAGS:
            clean, kind = TAGS[local]
            val = _num(txt, kind)
            if val is None:
                continue
            if ctx in (Q_DUR, Q_INST):
                facts_q[clean] = val
            elif ctx in (YTD_DUR, YTD_INST):
                facts_ytd[clean] = val

        # segments (current-quarter column only: contexts carrying a segment member)
        if local == "DescriptionOfReportableSegment" and ctx in seg_ctx:
            seg_name[seg_ctx[ctx]] = txt
        elif local in SEG_REVENUE_TAGS and ctx in seg_ctx:
            seg_rev[seg_ctx[ctx]] = _num(txt, "money")
        elif local in SEG_RESULT_TAGS and ctx in seg_ctx:
            seg_res[seg_ctx[ctx]] = _num(txt, "money")

    segments = {}
    for idx, name in seg_name.items():
        segments[name] = {"revenue": seg_rev.get(idx), "result": seg_res.get(idx)}

    if not facts_q and not segments:
        return None
    return {"meta": meta, "quarter": facts_q, "ytd": facts_ytd,
            "segments": segments, "notes": notes}


_YEAR_RE = re.compile(r"(\d{4})")


def _filing_year(f):
    m = _YEAR_RE.search(f.get("toDate", "") or f.get("financialYear", ""))
    return int(m.group(1)) if m else 0


def process_symbol(session, symbol, period="Quarterly", force=False, since_year=2018):
    out_path = STRUCT / f"{symbol}_xbrl.json"
    if out_path.exists() and not force:
        return "skip"

    # integrated-filing endpoint first (latest quarters, incl. revisions), then the
    # legacy endpoint (everything up to Dec-2024); dedup below keeps the first seen
    filings = get_integrated_filings(session, symbol) if period == "Quarterly" else []
    filings += get_filings(session, symbol, period)
    if not filings:
        return "no-filings"

    # real XBRL exists only ~2018+; pre-2018 links 404 — skip to avoid wasted requests
    filings = [f for f in filings if _filing_year(f) >= since_year]
    if not filings:
        return "no-recent"

    raw_dir = XBRL_RAW / symbol
    raw_dir.mkdir(exist_ok=True)
    quarters, n_ok, seen = [], 0, set()

    for f in filings:
        url = f.get("xbrl")
        if not url or not str(url).startswith("http"):
            continue
        try:
            rr = session.get(url, timeout=40)
        except Exception:
            continue
        body = rr.text or ""
        if not body.lstrip().startswith("<?xml") or not any(m in body for m in FIN_NS_MARKERS):
            continue                                  # dead link / old era / not XBRL
        parsed = parse_xbrl(body)
        if not parsed:
            continue
        consol = (f.get("consolidated", "") == "Consolidated")
        # both sides use `or ""`, not .get(key, ""): the legacy endpoint's raw rows
        # (get_filings, passed through unshaped) can carry an explicit null for
        # toDate/fromDate, and .get(key, default) only applies the default when
        # the key is ABSENT — not when present with value None.
        pend = parsed["meta"].get("period_end") or (f.get("toDate") or "")
        tag = "C" if consol else "S"
        if (pend, tag) in seen:
            continue                                  # revision/duplicate row
        seen.add((pend, tag))
        (raw_dir / f"{pend}_{tag}.xml").write_text(body, encoding="utf-8")
        quarters.append({
            "period_end": pend,
            "period_start": parsed["meta"].get("period_start") or (f.get("fromDate") or ""),
            "relating_to": f.get("relatingTo", ""),
            "financial_year": f.get("financialYear", ""),
            "consolidated": consol,
            "audited": f.get("audited", ""),
            "filing_date": f.get("filingDate", ""),
            "xbrl_url": url,
            "facts": parsed["quarter"],
            "ytd": parsed["ytd"],
            "segments": parsed["segments"],
            "notes": parsed["notes"],
        })
        n_ok += 1
        time.sleep(0.15)

    if not quarters:
        return "no-xbrl"

    quarters.sort(key=lambda q: (q["period_end"], q["consolidated"]), reverse=True)
    record = {
        "symbol": symbol,
        "source": "NSE archive (in-bse-fin XBRL)",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "period": period,
        "num_quarters": len(quarters),
        "quarters": quarters,
    }
    out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"ok:{n_ok}"


def symbol_universe():
    """NSE symbols of all NSE-listed companies (from company_master.json).

    BSE-only companies (no nse_symbol) can't be fetched via the NSE archive.
    Falls back to *_screener.json filenames if the master is unavailable.
    """
    master = STRUCT / "company_master.json"
    if master.exists():
        recs = json.loads(master.read_text(encoding="utf-8"))
        syms = sorted({(r.get("nse_symbol") or "").strip()
                       for r in recs if (r.get("nse_symbol") or "").strip()})
        if syms:
            return syms
    return sorted(p.name[:-14] for p in STRUCT.glob("*_screener.json"))


def run_all(period, workers, limit, force, since_year=2018, chunk=48):
    syms = symbol_universe()
    if limit:
        syms = syms[:limit]
    # pre-skip already-saved (resumable) so progress + retries are meaningful
    if not force:
        syms = [s for s in syms if not (STRUCT / f"{s}_xbrl.json").exists()]
    log.info(f"XBRL {period}: {len(syms)} to fetch, {workers} workers, re-seed every {chunk}")

    counts, done = {}, 0
    # "no-xbrl"/"no-filings" are often transient throttling — retry once with a fresh
    # session (a fresh session demonstrably still works when long-lived ones get throttled)
    RETRYABLE = {"no-xbrl", "no-filings"}

    for c0 in range(0, len(syms), chunk):
        chunk_syms = syms[c0:c0 + chunk]
        sessions = [make_session() for _ in range(workers)]   # fresh each chunk

        def work(args):
            i, sym = args
            sess = sessions[i % workers]
            try:
                st = process_symbol(sess, sym, period, force, since_year)
                if st in RETRYABLE:
                    st2 = process_symbol(make_session(), sym, period, force, since_year)
                    if st2.startswith("ok"):
                        st = st2
                return sym, st
            except Exception as e:
                return sym, f"err:{type(e).__name__}"

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(work, (i, s)): s for i, s in enumerate(chunk_syms)}
            for fut in as_completed(futs):
                sym, status = fut.result()
                counts[status.split(":")[0]] = counts.get(status.split(":")[0], 0) + 1
                done += 1
        log.info(f"  {done}/{len(syms)} — {counts}")
    log.info(f"Done. {counts}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", help="single symbol (e.g. RELIANCE)")
    ap.add_argument("--period", default="Quarterly", choices=["Quarterly", "Annual"])
    ap.add_argument("--all", action="store_true", help="all companies (resumable)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="cap symbols (testing)")
    ap.add_argument("--since-year", type=int, default=2018, help="skip filings before this year")
    ap.add_argument("--force", action="store_true", help="re-fetch even if output exists")
    args = ap.parse_args()

    if args.symbol:
        sess = make_session()
        status = process_symbol(sess, args.symbol.upper(), args.period, force=True)
        print(f"{args.symbol}: {status}")
        p = STRUCT / f"{args.symbol.upper()}_xbrl.json"
        if p.exists():
            rec = json.loads(p.read_text(encoding="utf-8"))
            print(f"  {rec['num_quarters']} quarters saved -> {p.name}")
            q = rec["quarters"][0]
            print(f"  latest: {q['period_end']} ({'Consolidated' if q['consolidated'] else 'Standalone'})")
            print(f"  facts: {json.dumps(q['facts'], indent=1)[:600]}")
            print(f"  segments: {json.dumps(q['segments'], indent=1)[:600]}")
    elif args.all:
        run_all(args.period, args.workers, args.limit, args.force, args.since_year)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
