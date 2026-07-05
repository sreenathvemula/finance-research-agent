#!/usr/bin/env python3
"""
29_xbrl_to_md.py - Convert per-company NSE/BSE result XBRL into Markdown time-series.

data/nse_xbrl/{SYMBOL}/{YYYY-MM-DD}_{S|C}.xml  (S=Standalone, C=Consolidated), one
file per reported quarter. Each file holds ~130 line items on several dated contexts
(current quarter + comparatives + year-to-date). The filing-template context ids are
fixed: One* = "3 months ended" (current quarter), Four* = year-to-date, *D duration /
*I instant. We take the OneD/OneI column of each file so every markdown column is a
TRUE standalone quarter; declared context dates cannot be trusted for this (many
filings stamp the YTD context with the quarter's dates). Files that carry no quarter
column at all are de-cumulated by subtracting the prior YTD from another filing.

Output: data/companies/{SYMBOL}/xbrl/standalone.md  +  consolidated.md
Usage:
  python 29_xbrl_to_md.py --symbol 20MICRONS      # one company -> markdown tree
  python 29_xbrl_to_md.py --sample 8
  python 29_xbrl_to_md.py --all --workers 8
"""
import argparse, logging, re
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT   = Path(__file__).parent.parent
XBRL   = ROOT / "data" / "nse_xbrl"
MD_DIR = ROOT / "data" / "companies"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("xbrl2md")

# pure-metadata / non-financial tags to drop from the line-item tables
_META = {
    "ScripCode","Symbol","MSEISymbol","NameOfTheCompany","ClassOfSecurity",
    "DateOfStartOfFinancialYear","DateOfEndOfFinancialYear",
    "DateOfBoardMeetingWhenFinancialResultsWereApproved",
    "DateOnWhichPriorIntimationOfTheMeetingForConsideringFinancialResultsWasInformedToTheExchange",
    "DescriptionOfPresentationCurrency","LevelOfRoundingUsedInFinancialStatements",
    "ReportingQuarter","StartTimeOfBoardMeeting","EndTimeOfBoardMeeting",
    "DateOfStartOfBoardMeeting","DateOfEndOfBoardMeeting",
    "WhetherCashFlowStatementIsApplicableOnCompany","TypeOfCashFlowStatement",
    "DeclarationOfUnmodifiedOpinionOrStatementOnImpactOfAuditQualification",
    "IsCompanyReportingMultisegmentOrSingleSegment","DescriptionOfSingleSegment",
    "DateOfStartOfReportingPeriod","DateOfEndOfReportingPeriod",
    "WhetherResultsAreAuditedOrUnaudited","NatureOfReportStandaloneConsolidated",
    "Disclaimer","DescriptionOfMultisegment",
}
# per-share / face-value / ratio fields: keep raw (do NOT scale to crore)
_RAW = ("EarningsPerShare", "PerShare", "FaceValue", "Ratio", "Percentage",
        "NominalValue", "PerEquityShare")
# duration-context items that are NOT additive across quarters (period-end stocks or
# per-period ratios) - carried as reported when a cumulative column is de-cumulated.
# EPS is deliberately additive here: YTD-EPS minus prior-YTD-EPS is the standard
# balancing-figure treatment.
_NONADDITIVE = ("FaceValue", "PaidUpValue", "Ratio", "Percentage", "NominalValue",
                "NumberOf", "NetWorth", "Reserve", "ShareCapital", "Outstanding")

# filing-template context ids (BSE in-bse-fin and SEBI in-capmkt use the same scheme):
# One* = current "3 months ended" column, Four* = current year-to-date column
Q_DUR, Q_INST, YTD_DUR = "OneD", "OneI", "FourD"


def _localname(tag):
    return tag.split("}")[-1] if "}" in tag else tag


def _date(s):
    try:
        y, m, d = map(int, s.split("-"))
        return date(y, m, d)
    except Exception:
        return None


def _days(s, e):
    ds, de = _date(s), _date(e)
    return (de - ds).days if ds and de else 9999


def _fy_start(end):
    """Indian financial-year start (Apr 1) for a period ending `end` (ISO string)."""
    d = _date(end)
    if not d:
        return None
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1).isoformat()


def _prev_quarter_end(end):
    """Last day of the calendar quarter before the one containing `end`."""
    d = _date(end)
    if not d:
        return None
    qstart_month = ((d.month - 1) // 3) * 3 + 1
    return (date(d.year, qstart_month, 1) - timedelta(days=1)).isoformat()


def parse_file(path: Path):
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return None
    # declared contexts -> (kind, start, end). Some filings (esp. 2018-2021) leave the
    # main OneD/OneI/FourD contexts UNDECLARED while facts still reference them, and
    # declared context dates are unreliable: the YTD context is routinely stamped with
    # the current quarter's dates. Selection therefore goes by template id first.
    ctx = {}
    for c in root.iter():
        if _localname(c.tag) == "context":
            p = c.find("{*}period")
            if p is None:
                continue
            inst = p.find("{*}instant")
            sd, ed = p.find("{*}startDate"), p.find("{*}endDate")
            if inst is not None:
                ctx[c.get("id")] = ("I", inst.text, inst.text)
            elif sd is not None and ed is not None:
                ctx[c.get("id")] = ("D", sd.text, ed.text)
    meta = {}
    items_by_ctx = {}
    for el in root.iter():
        ln = _localname(el.tag)
        cref = el.get("contextRef")
        if not cref or el.text is None or not el.text.strip():
            continue
        val = el.text.strip()
        if ln in _META:
            meta.setdefault(ln, val)
        else:
            items_by_ctx.setdefault(cref, {})[ln] = val
    rstart = meta.get("DateOfStartOfReportingPeriod")
    rend   = meta.get("DateOfEndOfReportingPeriod")
    if not rend:
        return None

    def _numeric(cid):
        return {k: float(v) for k, v in items_by_ctx.get(cid, {}).items() if _is_num(v)}

    # -- quarter (primary) duration facts --
    items_q, q_period, cumulative = {}, None, False
    if items_by_ctx.get(Q_DUR):
        items_q = _numeric(Q_DUR)
        if ctx.get(Q_DUR, ("",))[0] == "D":
            q_period = (ctx[Q_DUR][1], ctx[Q_DUR][2])
        elif rstart:
            q_period = (rstart, rend)
        # rare: a filer's "current period" column itself spans >1 quarter
        cumulative = bool(q_period) and _days(*q_period) > 100
    else:
        # no template quarter column: fall back to declared contexts ending at rend.
        # A real statement column has dozens of facts; segment-member contexts have
        # 1-2, so a sparse "quarter" context must not shadow a rich cumulative one
        # (the latter gets de-cumulated downstream).
        dur_end = [cid for cid, (k, s, e) in ctx.items()
                   if k == "D" and e == rend and items_by_ctx.get(cid)]
        quarterish = [c for c in dur_end
                      if _days(ctx[c][1], ctx[c][2]) <= 100 and len(items_by_ctx[c]) >= 8]
        if quarterish:
            primary = max(quarterish, key=lambda c: len(items_by_ctx[c]))
        elif dur_end:
            primary = max(dur_end, key=lambda c: len(items_by_ctx[c]))
        else:
            primary = None
        if primary:
            items_q = _numeric(primary)
            q_period = (ctx[primary][1], ctx[primary][2])
            cumulative = _days(*q_period) > 100
        else:
            # last resort: any single undeclared context
            for cref in items_by_ctx:
                if cref not in ctx:
                    items_q = _numeric(cref)
                    if rstart:
                        q_period = (rstart, rend)
                    break

    # -- instant facts (balance-sheet items at period end) --
    if items_by_ctx.get(Q_INST):
        items_i = _numeric(Q_INST)
    else:
        inst = [cid for cid, (k, s, e) in ctx.items()
                if k == "I" and e == rend and items_by_ctx.get(cid)]
        items_i = _numeric(max(inst, key=lambda c: len(items_by_ctx[c]))) if inst else {}

    # -- year-to-date facts (kept so cumulative-only filings can be de-cumulated) --
    items_ytd, ytd_period = {}, None
    if items_by_ctx.get(YTD_DUR):
        items_ytd = _numeric(YTD_DUR)
        c = ctx.get(YTD_DUR)
        if c and c[0] == "D" and _days(c[1], c[2]) > 100:
            ytd_period = (c[1], c[2])                 # trustworthy stamped YTD dates
        else:
            # stamped with the quarter's dates (or undeclared): derive Apr-1 FY start
            fy = meta.get("DateOfStartOfFinancialYear") or _fy_start(rend)
            if fy:
                ytd_period = (fy, rend)

    if not items_q and not items_i:
        return None
    return {
        "end": rend,
        "q_period": q_period,
        "cumulative": cumulative,
        "items_q": items_q,
        "items_i": items_i,
        "ytd_period": ytd_period,
        "items_ytd": items_ytd,
        "audited": meta.get("WhetherResultsAreAuditedOrUnaudited", ""),
        "rquarter": meta.get("ReportingQuarter", ""),
        "company": meta.get("NameOfTheCompany", ""),
        "scrip": meta.get("ScripCode", ""),
        "currency": meta.get("DescriptionOfPresentationCurrency", "INR"),
    }


def _is_num(v):
    try:
        float(v); return True
    except ValueError:
        return False


def _pretty(name):
    s = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', name)
    s = re.sub(r'(?<=[A-Z])(?=[A-Z][a-z])', ' ', s)
    return s


def _fmt(name, v):
    f = float(v)
    if any(k in name for k in _RAW):
        return f"{f:g}"
    return f"{f/1e7:,.2f}"          # rupees -> crore


def _subtract(cum, prior):
    """quarter = cumulative --' prior-cumulative, per line item.

    Non-additive items (ratios, face value, paid-up capital, ...) are period-end /
    per-period values: carried as reported. Additive items missing from the prior
    cumulative are dropped rather than shown cumulative."""
    out = {}
    for k, v in cum.items():
        if any(p in k for p in _NONADDITIVE):
            out[k] = v
        elif k in prior:
            out[k] = v - prior[k]
    return out


def _decumulate(parsed):
    """Fix columns whose primary duration facts are cumulative (no quarter column in
    the filing). Subtracts the prior cumulative for the same FY, sourced from another
    filing's YTD context or from the sum of the intervening true quarters."""
    # cumulative-period lookup across all filings of this variant
    cum_map = {}
    for d in parsed:
        if d["ytd_period"] and d["items_ytd"]:
            cum_map.setdefault(tuple(d["ytd_period"]), d["items_ytd"])
        if d["q_period"] and not d["cumulative"] and d["items_q"]:
            cum_map.setdefault(tuple(d["q_period"]), d["items_q"])   # Q1: quarter == YTD
    quarters_by_end = {d["end"]: d for d in parsed
                       if d["q_period"] and not d["cumulative"] and d["items_q"]}
    for d in parsed:
        if not d["cumulative"] or not d["q_period"]:
            continue
        s, e = d["q_period"]
        prev_e = _prev_quarter_end(e)
        if not prev_e or prev_e < s:                  # spans a single quarter after all
            d["cumulative"] = False
            continue
        prior = cum_map.get((s, prev_e))
        if prior is None:
            # sum the true quarters covering s..prev_e (walk back one quarter at a time)
            acc, cursor = {}, prev_e
            while cursor and cursor >= s:
                f = quarters_by_end.get(cursor)
                if not f:
                    acc = None; break
                acc = f["items_q"] if not acc else \
                    {k: acc[k] + f["items_q"][k] for k in acc.keys() & f["items_q"].keys()}
                cursor = _prev_quarter_end(cursor)
            if acc and cursor and cursor < s:
                prior = acc
        if prior is not None:
            d["items_q"] = _subtract(d["items_q"], prior)
            d["cumulative"] = False
            d["derived"] = True
        # else: leave cumulative=True -> column flagged with + in the table


def build_md(symbol, variant_files, variant_name):
    """variant_files: list[Path] for one statement type (S or C)."""
    parsed = [p for p in (parse_file(f) for f in variant_files) if p]
    if not parsed:
        return None
    parsed.sort(key=lambda d: d["end"])
    _decumulate(parsed)
    for d in parsed:
        d["items"] = {**d["items_q"], **d["items_i"]}
    cols = [d["end"] for d in parsed]
    # union of line items, ordered by first appearance
    order, seen = [], set()
    for d in parsed:
        for k in d["items"]:
            if k not in seen:
                seen.add(k); order.append(k)
    company = next((d["company"] for d in parsed if d["company"]), symbol)
    scrip = next((d["scrip"] for d in parsed if d["scrip"]), "")
    hdr = [f"# {company} \u2014 {variant_name} quarterly results (XBRL)",
           f"_Symbol: {symbol}{(' -- BSE: '+scrip) if scrip else ''} -- "
           f"values in \u20b9 crore (EPS/ratios as reported) \u00b7 {len(cols)} quarters._", ""]
    # audited row; * = quarter derived by subtracting prior YTD, + = still cumulative
    aud = {}
    flagged, unresolved = False, False
    for d in parsed:
        mark = "*" if d.get("derived") else ("+" if d["cumulative"] else "")
        flagged |= mark == "*"
        unresolved |= mark == "+"
        aud[d["end"]] = (d["audited"][:1] or "-") + mark
    head = "| Line item | " + " | ".join(cols) + " |"
    sep  = "|---|" + "|".join(["---"] * len(cols)) + "|"
    rows = [head, sep,
            "| **Audited (A/U)** | " + " | ".join(aud.get(c, "-") for c in cols) + " |"]
    valmap = {d["end"]: d["items"] for d in parsed}
    for k in order:
        cells = []
        for c in cols:
            v = valmap[c].get(k)
            cells.append(_fmt(k, v) if v is not None else "")
        rows.append(f"| {_pretty(k)} | " + " | ".join(cells) + " |")
    notes = []
    if flagged:
        notes.append("\\* quarter derived by de-cumulating a YTD-only filing.")
    if unresolved:
        notes.append("+ cumulative period as filed (no prior YTD available to subtract).")
    if notes:
        rows += [""] + notes
    return "\n".join(hdr + rows) + "\n"


def process_symbol(symbol, out_root):
    d = XBRL / symbol
    if not d.is_dir():
        return (symbol, 0)
    files = list(d.glob("*.xml"))
    written = 0
    for variant, label in (("S", "Standalone"), ("C", "Consolidated")):
        vf = [f for f in files if f.stem.endswith("_" + variant)]
        if not vf:
            continue
        md = build_md(symbol, vf, label)
        if md:
            dst = out_root / symbol / "xbrl" / f"{label.lower()}.md"
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(md, encoding="utf-8")
            written += 1
    return (symbol, written)


def _worker(args):
    return process_symbol(*args)


def all_symbols():
    return sorted(d.name for d in XBRL.iterdir() if d.is_dir())


def run(symbols, out_root, workers):
    log.info(f"Converting XBRL for {len(symbols)} companies -> {out_root} ...")
    tot = files = 0
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_worker, (s, out_root)) for s in symbols]
        for i, fut in enumerate(as_completed(futs), 1):
            _, w = fut.result()
            tot += 1 if w else 0
            files += w
            if i % 200 == 0:
                log.info(f"  {i:,}/{len(symbols):,} - {files} md files")
    log.info(f"DONE - {files} markdown files for {tot} companies")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--sample", type=int)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    if args.sample:
        syms = all_symbols()[:args.sample]
        for s in syms:
            _, w = process_symbol(s, MD_DIR)
            log.info(f"  {s}: {w} files")
        return
    syms = [args.symbol] if args.symbol else all_symbols()
    run(syms, MD_DIR, args.workers)


if __name__ == "__main__":
    main()
