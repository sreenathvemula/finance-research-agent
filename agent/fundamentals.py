"""Fundamental-analysis layer: derives multi-year trends, forensic red flags,
shareholding/pledge trends, capital allocation and business-mix views from the
screener.json + tijori.json data lake.

Pure computation over data_access loaders. Everything returns plain dicts so the
tool wrappers can JSON-dump them and Claude can reason over the numbers. We never
emit a buy/sell/hold verdict here — we surface the evidence (with the direction
of each signal made explicit) and leave judgement to the agent + user.
"""
from __future__ import annotations

from . import data_access as da


# ----------------------------------------------------------------- parsing --
def num(v):
    """Parse a screener/tijori cell -> float or None.
    Handles '1,234', '26%', '-124', '', 'â€"', None, ints/floats."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("%", "").replace("₹", "")
    if s in ("", "-", "–", "—", "NA", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def series_map(symbol: str, statement: str, basis: str | None = None):
    """-> ({metric_label: {period: float|None}}, [period,...]).  ({}, []) if absent."""
    df = da.financial_statement(symbol, statement, basis)
    if df is None:
        return {}, []
    periods = [c for c in df.columns if c != "item"]
    out: dict[str, dict] = {}
    for _, row in df.iterrows():
        label = str(row["item"]).strip()
        out[label] = {p: num(row[p]) for p in periods}
    return out, periods


def _get(smap: dict, *substr_options: str):
    """First metric series whose label contains one of the given substrings
    (case-insensitive). Screener labels carry trailing '+' / '%' noise, so we
    match on substrings not equality."""
    for opt in substr_options:
        ol = opt.lower()
        for label, ser in smap.items():
            if ol in label.lower():
                return ser
    return None


def _vals(ser: dict, periods: list[str]):
    """[(period, value)] for non-None values, in period order."""
    if not ser:
        return []
    return [(p, ser[p]) for p in periods if ser.get(p) is not None]


def cagr(ser: dict, periods: list[str]):
    v = _vals(ser, periods)
    if len(v) < 2:
        return None
    first, last, n = v[0][1], v[-1][1], len(v) - 1
    if first is None or first <= 0 or last is None or last <= 0:
        return None
    return round(((last / first) ** (1 / n) - 1) * 100, 1)


def _first_last(ser: dict, periods: list[str]):
    v = _vals(ser, periods)
    if not v:
        return None, None, None, None
    return v[0][0], v[0][1], v[-1][0], v[-1][1]


def _round(x, d=1):
    return None if x is None else round(x, d)


_FIN_KW = ("bank", "financ", "nbfc", "insurance", "broking", "amc",
           "asset management", "housing finance", "microfinance")


def is_financial(symbol: str) -> bool:
    """Banks/NBFCs/insurers need different analysis: their 'cash from operations'
    embeds loan-book movements, interest is a core input not a leverage cost, and
    debtor/inventory days are meaningless. We suppress those flags for them."""
    try:
        ent = da.entities()
        row = ent[ent["symbol"].str.upper() == symbol.upper()]
        if not row.empty:
            blob = " ".join(str(row.iloc[0].get(c, "")) for c in
                            ("sector", "nse_industry", "industry")).lower()
            if any(k in blob for k in _FIN_KW):
                return True
    except Exception:
        pass
    sj = da.screener_data(symbol) or {}
    blob = f"{sj.get('industry','')} {sj.get('about','')[:200]}".lower()
    return any(k in blob for k in _FIN_KW)


# ------------------------------------------------------- financial trends --
def financial_trends(symbol: str, basis: str | None = None) -> dict:
    """Multi-year fundamental trend analysis + derived red/green flags.

    Signals computed from ~12yr of P&L / balance-sheet / cash-flow / ratios.
    Each flag states its DIRECTION (concern vs strength) so the reader isn't
    left to guess. This is evidence, not a recommendation."""
    pl, pper = series_map(symbol, "profit_loss", basis)
    bs, bper = series_map(symbol, "balance_sheet", basis)
    cf, cper = series_map(symbol, "cash_flow", basis)
    rt, rper = series_map(symbol, "ratios", basis)
    if not pl:
        return {"symbol": symbol.upper(), "error": "no financial statements available"}

    sales = _get(pl, "Sales", "Revenue")
    npat = _get(pl, "Net Profit", "Net profit", "Profit after tax", "PAT")
    opm = _get(pl, "OPM")
    op = _get(pl, "Operating Profit")
    interest = _get(pl, "Interest")
    eps = _get(pl, "EPS")
    payout = _get(pl, "Dividend Payout")

    flags: list[dict] = []

    def flag(sev, category, msg):
        flags.append({"signal": sev, "category": category, "note": msg})

    fin = is_financial(symbol)
    metrics: dict = {"symbol": symbol.upper(),
                     "basis": basis or "consolidated (fallback standalone)",
                     "years_covered": f"{pper[0]}..{pper[-1]}" if pper else None,
                     "company_type": "financial (bank/NBFC/insurer)" if fin else "non-financial"}
    if fin:
        metrics["note"] = ("financial company: cash-flow-conversion, interest-coverage "
                           "and working-capital-days checks are omitted (not meaningful "
                           "for lenders); focus on growth, margins/NIM, ROE and dilution")

    sales_cagr = cagr(sales, pper)
    pat_cagr = cagr(npat, pper)
    eps_cagr = cagr(eps, pper)
    metrics["sales_cagr_pct"] = sales_cagr
    metrics["pat_cagr_pct"] = pat_cagr
    metrics["eps_cagr_pct"] = eps_cagr

    # growth quality: profit growing far faster than sales for years is often
    # margin expansion (good) but can also be low-quality (other income, one-offs)
    if sales_cagr is not None and pat_cagr is not None:
        if sales_cagr < 3 and pat_cagr < 0:
            flag("concern", "growth", f"stagnant sales ({sales_cagr}% CAGR) and shrinking profit ({pat_cagr}% CAGR)")
        elif pat_cagr - sales_cagr > 12 and sales_cagr < 8:
            flag("watch", "earnings_quality",
                 f"profit CAGR ({pat_cagr}%) far outpaces sales CAGR ({sales_cagr}%) on weak topline "
                 f"- verify it's real margin gain, not other income / one-offs")
        elif sales_cagr >= 12 and pat_cagr >= 12:
            flag("strength", "growth", f"durable double-digit compounding: sales {sales_cagr}%, profit {pat_cagr}%")

    # margins trend
    if opm:
        _, opm0, _, opm1 = _first_last(opm, pper)
        metrics["opm_first_pct"], metrics["opm_latest_pct"] = opm0, opm1
        if opm0 is not None and opm1 is not None:
            if opm1 - opm0 <= -4:
                flag("concern", "margins", f"operating margin compressed from {opm0}% to {opm1}%")
            elif opm1 - opm0 >= 4:
                flag("strength", "margins", f"operating margin expanded from {opm0}% to {opm1}%")

    # earnings quality: cumulative CFO vs cumulative PAT (accruals check)
    cfo = _get(cf, "Cash from Operating", "Operating Activity")
    if cfo and npat and not fin:
        cfo_sum = sum(v for _, v in _vals(cfo, cper))
        pat_sum = sum(v for _, v in _vals(npat, pper))
        if pat_sum and pat_sum > 0:
            ratio = round(cfo_sum / pat_sum, 2)
            metrics["cumulative_cfo_to_pat"] = ratio
            if ratio < 0.7:
                flag("concern", "earnings_quality",
                     f"cumulative operating cash flow is only {ratio}x cumulative net profit "
                     f"- profits are not converting to cash (possible aggressive accounting / working-capital drain)")
            elif ratio >= 0.9:
                flag("strength", "earnings_quality",
                     f"operating cash flow tracks profit well ({ratio}x cumulative) - clean earnings")

    # interest coverage (latest) - meaningless for lenders
    if op and interest and not fin:
        op_v = _vals(op, pper)
        int_v = _vals(interest, pper)
        if op_v and int_v and int_v[-1][1]:
            cov = round(op_v[-1][1] / int_v[-1][1], 1)
            metrics["interest_coverage_latest"] = cov
            if cov < 3:
                flag("concern", "leverage", f"weak interest coverage ({cov}x operating profit / interest)")
            elif cov > 8:
                flag("strength", "leverage", f"comfortable interest coverage ({cov}x)")

    # debt trend (balance sheet borrowings)
    borrow = _get(bs, "Borrowings", "Debt")
    if borrow:
        b0p, b0, b1p, b1 = _first_last(borrow, bper)
        metrics["borrowings_first"], metrics["borrowings_latest"] = b0, b1
        if b0 is not None and b1 is not None:
            if b0 > 0 and b1 / b0 > 2 and b1 - b0 > 500:
                flag("watch", "leverage", f"borrowings more than doubled ({b0} -> {b1} Rs cr, {b0p}->{b1p})")
            elif b1 < b0 * 0.6:
                flag("strength", "leverage", f"borrowings reduced ({b0} -> {b1} Rs cr)")

    # equity dilution - only a real concern when per-share growth lags total
    # profit growth (a bonus issue / split raises equity capital but is NOT
    # dilutive; EPS keeping pace with PAT proves that).
    eqc = _get(bs, "Equity Capital", "Share Capital")
    if eqc:
        e0p, e0, e1p, e1 = _first_last(eqc, bper)
        if e0 and e1 and e1 / e0 > 1.25:
            if (eps_cagr is not None and pat_cagr is not None
                    and pat_cagr - eps_cagr > 4):
                flag("watch", "dilution",
                     f"equity capital rose {e0}->{e1} ({e0p}->{e1p}) and EPS CAGR ({eps_cagr}%) "
                     f"lags profit CAGR ({pat_cagr}%) - genuine per-share dilution")
            # otherwise it's a bonus/split, not dilution - no flag

    # ROCE trend (ratios)
    roce = _get(rt, "ROCE")
    if roce:
        r0p, r0, r1p, r1 = _first_last(roce, rper)
        metrics["roce_first_pct"], metrics["roce_latest_pct"] = r0, r1
        if r0 is not None and r1 is not None:
            if r1 - r0 <= -5:
                flag("concern", "returns", f"ROCE declined from {r0}% to {r1}%")
            elif r1 >= 18 and r1 - r0 >= 0:
                flag("strength", "returns", f"high, stable/improving ROCE ({r0}% -> {r1}%)")

    # working-capital / debtor-day creep (not meaningful for lenders)
    dd = _get(rt, "Debtor Days") if not fin else None
    if dd:
        d0p, d0, d1p, d1 = _first_last(dd, rper)
        metrics["debtor_days_first"], metrics["debtor_days_latest"] = d0, d1
        if d0 is not None and d1 is not None and d1 - d0 > 20 and d1 > 60:
            flag("watch", "working_capital", f"debtor days rising ({d0} -> {d1}) - receivables building up")

    ccc = _get(rt, "Cash Conversion")
    if ccc:
        _, c0, _, c1 = _first_last(ccc, rper)
        metrics["cash_conversion_cycle_first"], metrics["cash_conversion_cycle_latest"] = c0, c1

    # dividend
    if payout:
        pv = _vals(payout, pper)
        if pv:
            metrics["dividend_payout_latest_pct"] = pv[-1][1]

    metrics["flags"] = flags
    metrics["flag_summary"] = {
        "concern": sum(1 for f in flags if f["signal"] == "concern"),
        "watch": sum(1 for f in flags if f["signal"] == "watch"),
        "strength": sum(1 for f in flags if f["signal"] == "strength"),
    }
    return metrics


# --------------------------------------------------------- forensic flags --
_FORENSIC_TOPICS = [
    ("Accounting Quality", ["contingent", "other income", "revenue recognition",
                            "depreciation", "accounting", "converting accounting"]),
    ("Promoter & Governance", ["pledge", "promoter"]),
    ("Balance Sheet & Debt", ["debt", "balance sheet", "working capital", "servicing"]),
    ("Growth & Returns", ["sales growth", "sales of the company", "roe", "roce",
                          "margin", "capex", "share price has increased"]),
    ("Valuation & Sentiment", ["valuation", "retail holding", "exuberance"]),
]


def _classify_topic(text: str) -> str:
    tl = text.lower()
    for topic, kws in _FORENSIC_TOPICS:
        if any(k in tl for k in kws):
            return topic
    return "Other"


def forensic_flags(symbol: str) -> dict:
    """Tijori's forensic checklist grouped by topic, plus the promoter-pledge
    reading. Each check is a plain-English observation; the agent reads its
    sentiment (Claude reliably tells 'does not have significant contingent
    liabilities' = good from 'margins are volatile' = concern)."""
    tj = da._read_json(da.company_dir(symbol) / "tijori.json")
    if not tj:
        return {"symbol": symbol.upper(), "error": "no tijori data"}
    checks = tj.get("forensics") or []
    grouped: dict[str, list[str]] = {}
    for c in checks:
        grouped.setdefault(_classify_topic(c), []).append(c)

    out: dict = {"symbol": symbol.upper(), "n_checks": len(checks), "by_topic": grouped}

    # explicit promoter pledge reading from the shareholding trend series
    st = tj.get("shareholding_trend") or {}
    pledge_key = next((k for k in st if "pledge" in k.lower()), None)
    if pledge_key and st[pledge_key]:
        ser = st[pledge_key]
        recent = [(d, v) for d, v in ser if v is not None][-8:]
        latest = recent[-1] if recent else None
        out["promoter_pledge"] = {
            "latest_pct": latest[1] if latest else None,
            "as_of": latest[0] if latest else None,
            "recent_series": recent,
            "flag": ("concern: promoters have pledged shares" if latest and latest[1] and latest[1] > 5
                     else "clean: negligible/zero promoter pledge"),
        }
    return out


# ------------------------------------------------------ shareholding trends --
def shareholding_trends(symbol: str) -> dict:
    """Quarter-over-quarter ownership shifts: promoter stake direction, FII/DII
    flows, and promoter pledge. Declining promoter stake + rising pledge is a
    classic governance red flag."""
    df = da.financial_statement(symbol, "shareholding")
    out: dict = {"symbol": symbol.upper()}
    if df is not None and not df.empty:
        periods = [c for c in df.columns if c != "item"]
        rows = {str(r["item"]).strip().rstrip("+").strip(): r for _, r in df.iterrows()}
        recent = periods[-6:]
        table = {}
        for cat, r in rows.items():
            table[cat] = {p: r[p] for p in recent}
        out["ownership_by_quarter"] = {"periods": recent, "categories": table}

        prom_key = next((k for k in rows if "promoter" in k.lower()), None)
        if prom_key:
            vals = [(p, num(rows[prom_key][p])) for p in periods]
            vals = [(p, v) for p, v in vals if v is not None]
            if len(vals) >= 2:
                delta = round(vals[-1][1] - vals[0][1], 2)
                out["promoter_stake"] = {
                    "earliest": vals[0], "latest": vals[-1], "change_pp": delta,
                    "flag": ("concern: promoter stake declining" if delta <= -1
                             else "stable/rising promoter stake" if delta >= -0.5
                             else "slightly lower promoter stake"),
                }
        fii_key = next((k for k in rows if k.lower().startswith("fii")), None)
        dii_key = next((k for k in rows if k.lower().startswith("dii")), None)
        for key, name in [(fii_key, "fii"), (dii_key, "dii")]:
            if key:
                vals = [(p, num(rows[key][p])) for p in periods]
                vals = [(p, v) for p, v in vals if v is not None]
                if len(vals) >= 2:
                    out[f"{name}_stake"] = {"earliest": vals[0], "latest": vals[-1],
                                            "change_pp": round(vals[-1][1] - vals[0][1], 2)}

    # merge pledge from forensic reader
    ff = forensic_flags(symbol)
    if "promoter_pledge" in ff:
        out["promoter_pledge"] = ff["promoter_pledge"]
    if "ownership_by_quarter" not in out and "promoter_pledge" not in out:
        out["error"] = "no shareholding data"
    return out


# --------------------------------------------------- capital allocation --
def _tj_series(items: list, name_substr: str):
    for it in items or []:
        if name_substr.lower() in str(it.get("name", "")).lower():
            return it.get("series") or []
    return []


def capital_allocation(symbol: str) -> dict:
    """How management deploys capital: operating cash flow vs capex vs free cash
    flow vs debt vs dividends over time. Tells whether growth is self-funded and
    whether cash is returned - central to judging management stewardship."""
    tj = da._read_json(da.company_dir(symbol) / "tijori.json")
    out: dict = {"symbol": symbol.upper()}
    if tj:
        capex = tj.get("capex") or []
        debt = tj.get("debt") or []
        cfo = _tj_series(capex, "cash from operations")
        cx = _tj_series(capex, "capex")
        fcf = _tj_series(capex, "free cash flow")
        netdebt = _tj_series(debt, "net debt")

        def tail(s, n=8):
            return [[d, v] for d, v in s][-n:]

        out["cash_from_operations"] = tail(cfo)
        out["capex"] = tail(cx)
        out["free_cash_flow"] = tail(fcf)
        out["net_debt"] = tail(netdebt)

        flags = []
        fcf_recent = [v for _, v in fcf[-5:] if v is not None]
        if fcf_recent:
            neg = sum(1 for v in fcf_recent if v < 0)
            if neg >= 3:
                flags.append("concern: free cash flow negative in most of last 5 years (capital-hungry / funding gap)")
            elif all(v > 0 for v in fcf_recent):
                flags.append("strength: consistently positive free cash flow")
        nd_recent = [v for _, v in netdebt[-5:] if v is not None]
        if len(nd_recent) >= 2:
            if nd_recent[-1] > nd_recent[0] * 1.5 and nd_recent[-1] > 0:
                flags.append("watch: net debt rising materially")
            elif nd_recent[-1] < 0:
                flags.append("strength: net cash (negative net debt)")
        out["flags"] = flags

    # dividend payout from P&L
    pl, pper = series_map(symbol, "profit_loss")
    payout = _get(pl, "Dividend Payout")
    if payout:
        pv = _vals(payout, pper)
        if pv:
            out["dividend_payout_pct_recent"] = pv[-5:]
    if len(out) == 1:
        out["error"] = "no capital-allocation data"
    return out


# ------------------------------------------------------- business profile --
def business_profile(symbol: str) -> dict:
    """What the company actually does: revenue mix (product / geography /
    segment), operating KPIs, market share and where capex is going. Sourced
    from tijori business intelligence."""
    tj = da._read_json(da.company_dir(symbol) / "tijori.json")
    out: dict = {"symbol": symbol.upper()}
    prof = da.profile(symbol) or {}
    out["about"] = (prof.get("about") or (da.screener_data(symbol) or {}).get("about") or "")[:1200]
    if tj:
        rm = tj.get("revenue_mix") or {}
        out["revenue_mix"] = {k: v for k, v in rm.items() if v}
        # latest operating KPIs (one point each)
        kpis = []
        for m in (tj.get("op_metrics") or []):
            ser = m.get("series") or []
            last = next(((d, v) for d, v in reversed(ser) if v is not None), None)
            if last:
                kpis.append({"metric": m.get("name"), "unit": m.get("unit"),
                             "latest": last[1], "as_of": last[0]})
        out["operating_kpis"] = kpis[:30]
        ms = tj.get("market_share") or []
        ms_pts = [(d, v) for d, v in ms if v is not None]
        if ms_pts:
            out["market_share_recent"] = ms_pts[-6:]
    if len(out) <= 2 and not out.get("about"):
        out["error"] = "no business-mix data"
    return out


# ---------------------------------------------------- competitive position --
def competitive_position(symbol: str) -> dict:
    """Peer benchmarking on operating metrics, market share and the peer set.
    Combines tijori benchmarking with the screener/profile peer list."""
    tj = da._read_json(da.company_dir(symbol) / "tijori.json")
    out: dict = {"symbol": symbol.upper()}
    if tj:
        bm = tj.get("benchmarking") or {}
        if bm.get("rows"):
            out["benchmarking"] = {"companies": bm.get("companies", []), "metrics": bm["rows"]}
        ms = tj.get("market_share") or []
        ms_pts = [(d, v) for d, v in ms if v is not None]
        if ms_pts:
            out["market_share_recent"] = ms_pts[-6:]
    peers = da.tijori_peers().get(symbol.upper()) or da.sector_peers().get(symbol.upper())
    if peers:
        out["peers"] = peers[:12] if isinstance(peers, list) else peers
    if len(out) == 1:
        out["error"] = "no competitive data"
    return out


# ------------------------------------------------------------ supply chain --
def supply_chain(symbol: str) -> dict:
    """Known suppliers/vendors (tijori). Procurement cost, raw-material sourcing
    and named customers usually need web research - the tool flags that gap so
    the agent knows to reach for web_research when the user asks."""
    tj = da._read_json(da.company_dir(symbol) / "tijori.json")
    out: dict = {"symbol": symbol.upper()}
    suppliers = (tj or {}).get("suppliers") or []
    out["suppliers"] = suppliers
    out["n_suppliers"] = len(suppliers)
    if not suppliers:
        out["note"] = ("no supplier data on file for this company - use web_research "
                       "for raw-material sourcing, procurement cost and customers")
    else:
        out["note"] = ("supplier list is from tijori; for raw-material cost drivers, "
                       "procurement geography and named customers, use web_research")
    return out


# ------------------------------------------------ management guidance track --
_GUIDANCE_QUERIES = [
    "revenue growth guidance outlook target for the year",
    "margin guidance EBITDA margin outlook",
    "capex capital expenditure plan expansion guidance",
    "management outlook expectations next year targets commitment",
    "demand outlook order book guidance commentary",
]


def quarterly_actuals(symbol: str) -> dict:
    """Compact table of the headline reported quarterly numbers (Sales, OPM%,
    Net Profit, EPS) over all available quarters - the 'actuals' side of the
    did-they-deliver check."""
    smap, periods = series_map(symbol, "quarterly_results")
    if not smap:
        return {}
    keep = {}
    for label in ("Sales", "OPM", "Net Profit", "EPS"):
        ser = _get(smap, label)
        if ser:
            keep[label] = {p: ser[p] for p in periods}
    return {"periods": periods, "metrics": keep}


def guidance_tracker(symbol: str, lookback_periods: int = 6, per_period: int = 2) -> dict:
    """Management-credibility evidence: forward-looking / guidance statements
    pulled from past earnings calls, paired with the ACTUAL results that were
    subsequently reported - so the agent can judge whether management delivered
    on what it promised. The tool surfaces both sides; the judgement is the
    agent's (and the user's).

    RAG-backed: requires the document index. Returns a clear notice if the
    index isn't ready or the company has no concalls."""
    out: dict = {"symbol": symbol.upper(),
                 "how_to_use": ("match each dated guidance statement against the actual "
                                "quarterly results that came AFTER it; a trustworthy "
                                "management's guidance is borne out by later actuals")}
    # actuals side (always available from structured data)
    out["actual_quarterly_results"] = quarterly_actuals(symbol)

    # guidance side (RAG over concalls)
    try:
        from . import rag
        seen: set[str] = set()
        statements: dict[str, list[dict]] = {}
        for q in _GUIDANCE_QUERIES:
            tl = rag.search_timeline(q, symbol,
                                     doc_types=["concall_transcript", "concall_presentation"],
                                     n_periods=lookback_periods, per_period=per_period)
            for period, hits in (tl or {}).items():
                for h in hits:
                    key = h["text"][:120]
                    if key in seen:
                        continue
                    seen.add(key)
                    statements.setdefault(period, []).append(
                        {"query_theme": q.split()[0], "score": h["score"],
                         "statement": h["text"][:700]})
        if statements:
            out["guidance_statements_by_period"] = statements
        else:
            out["guidance_note"] = ("no concall guidance retrieved - company may have no "
                                    "earnings-call transcripts indexed, or the index is "
                                    "still building")
    except Exception as e:
        out["guidance_note"] = f"guidance retrieval unavailable ({type(e).__name__}); actuals still shown"
    return out


# ---------------------------------------------------------- sector analysis --
def sector_analysis(sector: str | None = None, industry: str | None = None,
                    top_n: int = 25) -> dict:
    """Aggregate view of a sector/industry: how many companies, size distribution,
    and the leaders by market cap with headline valuation/return metrics. Gives a
    sector lens instead of N separate company lookups."""
    import html
    ent = da.entities()

    def _norm(s):  # decode &amp; etc so 'Chemicals & Petrochemicals' matches
        return html.unescape(str(s))

    def _sectors_list():
        return sorted({_norm(s) for s in ent["sector"].dropna().unique() if str(s).strip()})

    df = ent
    label = ""
    if industry:
        col = "nse_industry" if "nse_industry" in ent.columns else "industry"
        q = html.unescape(industry)
        df = ent[ent[col].map(_norm).str.contains(q, case=False, na=False, regex=False)]
        label = f"industry~'{industry}'"
    elif sector:
        q = html.unescape(sector)
        df = ent[ent["sector"].map(_norm).str.contains(q, case=False, na=False, regex=False)]
        label = f"sector~'{sector}'"
    else:
        return {"error": "provide sector or industry", "available_sectors": _sectors_list()}
    if df.empty:
        return {"error": f"no companies matched {label}", "available_sectors": _sectors_list()}

    import pandas as pd
    df = df.copy()
    for m in ("market_cap_cr", "pe", "roe", "roce"):
        if m in df.columns:
            df[m] = pd.to_numeric(df[m], errors="coerce")

    out: dict = {"query": label, "n_companies": int(len(df))}
    if "market_cap_cr" in df.columns:
        mc = df["market_cap_cr"].dropna()
        if not mc.empty:
            out["total_market_cap_cr"] = round(float(mc.sum()), 0)
            out["median_market_cap_cr"] = round(float(mc.median()), 0)
    for m in ("pe", "roe", "roce"):
        if m in df.columns and df[m].notna().any():
            out[f"median_{m}"] = round(float(df[m].median()), 1)

    cols = [c for c in ("symbol", "company_name", "market_cap_cr", "pe", "roe", "roce")
            if c in df.columns]
    top = df.sort_values("market_cap_cr", ascending=False, na_position="last").head(top_n)[cols]
    out["leaders_by_market_cap"] = top.where(top.notna(), None).to_dict("records")
    return out
