"""MCP tool definitions exposing the Finance data lake to the Claude agent."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import sys

import pandas as pd

from claude_agent_sdk import tool

from . import data_access as da
from . import fundamentals as fu
from .config import DOC_TYPES


def _text(s: str) -> dict:
    return {"content": [{"type": "text", "text": s}]}


def _err(s: str) -> dict:
    return {"content": [{"type": "text", "text": f"ERROR: {s}"}], "is_error": True}


def _text_and_image(s: str, png_bytes: bytes) -> dict:
    return {"content": [
        {"type": "text", "text": s},
        {"type": "image", "data": base64.b64encode(png_bytes).decode("ascii"),
         "mimeType": "image/png"},
    ]}


def _js(obj, limit: int = 12000) -> str:
    s = json.dumps(obj, indent=1, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + "\n... (truncated)"


# ------------------------------------------------------------------ tools ---
@tool(
    "resolve_company",
    "Resolve a company name or partial name to its NSE/BSE symbol. Always use this "
    "first when the user mentions a company by name rather than symbol.",
    {"type": "object",
     "properties": {"query": {"type": "string", "description": "company name or symbol"}},
     "required": ["query"]},
)
async def resolve_company(args):
    hits = await asyncio.to_thread(da.resolve_symbol, args["query"])
    if not hits:
        return _text(f"No company matched '{args['query']}'.")
    return _text(_js(hits))


@tool(
    "company_overview",
    "Company profile card: about, sector, key metrics (mcap, PE, ROE/ROCE, sales/PAT), "
    "ownership split, index memberships with weights, peers, and available data inventory.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"}},
     "required": ["symbol"]},
)
async def company_overview(args):
    p = await asyncio.to_thread(da.profile, args["symbol"])
    if not p:
        return _err(f"no profile for {args['symbol']} (check symbol with resolve_company)")
    keep = {k: p.get(k) for k in
            ("symbol", "company", "sector", "about", "metrics", "ownership",
             "indices", "peers", "data")}
    return _text(_js(keep))


@tool(
    "financial_statements",
    "Historical financial statements from screener data (annual, ~10-12 years, Rs crore): "
    "statement is one of profit_loss | balance_sheet | cash_flow | quarterly_results | shareholding.",
    {"type": "object",
     "properties": {
         "symbol": {"type": "string"},
         "statement": {"type": "string",
                       "enum": ["profit_loss", "balance_sheet", "cash_flow",
                                "quarterly_results", "shareholding"]}},
     "required": ["symbol", "statement"]},
)
async def financial_statements(args):
    df = await asyncio.to_thread(da.financial_statement, args["symbol"], args["statement"])
    if df is None:
        return _err(f"no {args['statement']} data for {args['symbol']}")
    return _text(da.df_to_md(df))


@tool(
    "valuation_summary",
    "Valuation workup for a company: current multiples (PE/PB/PS/EV-EBITDA/yield, ROE, ROCE, "
    "D/E), relative valuation vs peers/sector-index/own 10y history, and a 3-scenario DCF "
    "(bear/base/bull with implied growth). Includes the data-source disclaimer.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"}},
     "required": ["symbol"]},
)
async def valuation_summary(args):
    v = await asyncio.to_thread(da.valuation, args["symbol"])
    if not v:
        return _err(f"no valuation data for {args['symbol']}")
    return _text(_js(v))


@tool(
    "technicals_momentum",
    "Technical indicators & momentum: price vs 50/200-DMA, RSI, MACD, returns over "
    "1d/1w/1m/3m/6m/1y/3y, 52-week-high distance. live=true recomputes from the price "
    "history parquet (freshest); live=false reads the stored snapshot.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"},
                    "live": {"type": "boolean", "default": True}},
     "required": ["symbol"]},
)
async def technicals_momentum(args):
    sym = args["symbol"]
    if args.get("live", True):
        t = await asyncio.to_thread(da.live_technicals, sym)
        if t:
            return _text(_js({"symbol": sym.upper(), "mode": "live_recompute", **t}))
    t = await asyncio.to_thread(da.stored_technicals, sym)
    if not t:
        return _err(f"no technicals for {sym}")
    return _text(_js(t))


@tool(
    "price_history",
    "OHLCV price history between dates (YYYY-MM-DD). interval: daily|weekly|monthly. "
    "Use for 'what happened to the stock around <date>' style questions.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"},
                    "start": {"type": "string"}, "end": {"type": "string"},
                    "interval": {"type": "string", "enum": ["daily", "weekly", "monthly"],
                                 "default": "monthly"}},
     "required": ["symbol"]},
)
async def price_history(args):
    def work():
        df = da.prices(args["symbol"])
        if df is None:
            return None
        if args.get("start"):
            df = df[df["Date"] >= pd.Timestamp(args["start"])]
        if args.get("end"):
            df = df[df["Date"] <= pd.Timestamp(args["end"])]
        if df.empty:
            return df
        iv = args.get("interval", "monthly")
        if iv != "daily":
            rule = {"weekly": "W", "monthly": "ME"}[iv]
            df = (df.set_index("Date")
                    .resample(rule)
                    .agg({"Open": "first", "High": "max", "Low": "min",
                          "Close": "last", "Volume": "sum"})
                    .dropna(subset=["Close"]).reset_index())
        df["Date"] = df["Date"].dt.date
        return df.round(2)
    df = await asyncio.to_thread(work)
    if df is None:
        return _err(f"no price data for {args['symbol']}")
    return _text(da.df_to_md(df, max_rows=80))


@tool(
    "screen_stocks",
    "Objective stock screen over ~3100 NSE/BSE companies, ~55 filterable columns across every "
    "standard screening dimension. Filter by sector/industry text, index membership (e.g. "
    "'Nifty 50'), numeric minimums/maximums on:\n"
    "VALUATION: market_cap_cr, price, pe, pb, ps, ev_ebitda, ev_sales, peg (PE / 3yr EPS CAGR — "
    "undefined when growth isn't positive), div_yield, earnings_yield_pct (100/PE), "
    "fcf_yield_pct, book_value_per_share.\n"
    "PROFITABILITY/RETURNS: roe, roce, roic_pct, roa_pct, gross_margin_pct, opm_pct, "
    "net_margin_pct.\n"
    "GROWTH: sales_growth_pct, eps_growth_pct (latest FY YoY), eps_cagr_3y_pct.\n"
    "LEVERAGE/LIQUIDITY/EFFICIENCY: debt_equity, interest_coverage_x, current_ratio, "
    "quick_ratio, asset_turnover, fixed_asset_turnover, equity_multiplier, fcf_margin_pct, "
    "total_debt_cr, debtor_days, inventory_days, payable_days, cash_conversion_cycle.\n"
    "GOVERNANCE/OWNERSHIP: promoter_stake_pct, promoter_pledge_pct, fii_stake_pct, "
    "dii_stake_pct, public_stake_pct, free_float_pct.\n"
    "PRICE/RISK/MOMENTUM: ret_1d/1w/1m/3m/6m/1y/3y (%), rsi, down_from_52w_high (negative %), "
    "annualized_volatility_pct, max_drawdown_pct (negative %, worst peak-to-trough), "
    "avg_daily_value_cr (traded-value liquidity, Rs cr), beta (vs Nifty50, 3y daily).\n"
    "CAVEAT: peg/eps_growth_pct/eps_cagr_3y_pct on micro-caps with a near-zero prior-year "
    "earnings base can show absurd percentages (700%+) that are statistically real but not "
    "economically meaningful — always pair a growth or PEG filter with a market_cap_cr floor "
    "(e.g. >=500) to screen out this noise, and sanity-check outliers before presenting them. "
    "Some metrics (roe/roce/pe/pb/ev_ebitda/div_yield/debt_equity/debtor_days/inventory_days/"
    "payable_days/cash_conversion_cycle) have a primary source and a tijori-sourced fallback "
    "used only when the primary is blank — tijori's own methodology runs meaningfully different "
    "on some names (spot-checked ~30-40% off on a large-cap), so it fills gaps, never overrides "
    "a present primary value. Momentum flags: above_dma50/above_dma200/macd_bullish. "
    "exclude_categories applies ethical "
    "exclusions BEFORE ranking (e.g. ['tobacco']) — only apply a category if the user has "
    "explicitly asked for it; each exclusion is logged with its reason, never silent. Returns "
    "a table + match count + a FUNNEL (companies remaining after each criterion, in order — "
    "so it's visible what each criterion actually cost) + NEAR MISSES (companies that failed "
    "exactly one criterion by <= near_miss_tolerance_pct of its threshold — i.e. the good "
    "companies you left out by being strict; set to 0 to disable). A company MISSING data for "
    "a filtered column PASSES that filter instead of being dropped — absence of a figure is "
    "not evidence it fails the bar; the funnel states how many passed a given step only "
    "because that column was empty for them, so it's never hidden, just not penalised. "
    "Screening only — never advice.",
    {"type": "object",
     "properties": {
         "sector": {"type": "string", "description": "substring match on sector/industry"},
         "industry": {"type": "string"},
         "index": {"type": "string", "description": "index name substring, e.g. Nifty 50"},
         "min": {"type": "object", "description": "column -> minimum value",
                 "additionalProperties": {"type": "number"}},
         "max": {"type": "object", "description": "column -> maximum value",
                 "additionalProperties": {"type": "number"}},
         "above_dma50": {"type": "boolean"},
         "above_dma200": {"type": "boolean"},
         "macd_bullish": {"type": "boolean"},
         "exclude_categories": {"type": "array", "items": {"type": "string"},
                                "description": "e.g. ['tobacco'] — only categories the "
                                "user explicitly confirmed"},
         "sort_by": {"type": "string", "default": "market_cap_cr"},
         "ascending": {"type": "boolean", "default": False},
         "limit": {"type": "integer", "default": 25},
         "near_miss_tolerance_pct": {"type": "number", "default": 15.0,
                                    "description": "0 disables near-miss reporting"}},
     "required": []},
)
async def screen_stocks(args):
    from .screener import screen
    def work():
        return screen(
            sector=args.get("sector"), industry=args.get("industry"),
            index=args.get("index"),
            min_filters=args.get("min"), max_filters=args.get("max"),
            above_dma50=args.get("above_dma50"), above_dma200=args.get("above_dma200"),
            macd_bullish=args.get("macd_bullish"),
            exclude_categories=args.get("exclude_categories"),
            sort_by=args.get("sort_by", "market_cap_cr"),
            ascending=bool(args.get("ascending", False)),
            limit=int(args.get("limit", 25)),
            near_miss_tolerance_pct=float(args.get("near_miss_tolerance_pct", 15.0)),
        )
    df, matched, excl = await asyncio.to_thread(work)
    parts = [f"{matched} companies matched; showing {len(df)} "
             f"(sorted by {args.get('sort_by', 'market_cap_cr')})."]
    if excl["applied"]:
        by_cat = ", ".join(f"{c}: {n}" for c, n in excl["by_category"].items()) or "none matched"
        parts.append(f"Ethical exclusions applied ({', '.join(excl['applied'])}): "
                     f"{excl['excluded_count']} companies removed before ranking ({by_cat}).")
    if excl["unknown"]:
        parts.append(f"NOTE: unknown exclusion categories ignored: {excl['unknown']} "
                     f"— not yet defined in agent/ethics.py.")
    if excl["funnel"]:
        def _step(f):
            s = f"{f['stage']}: {f['remaining']}"
            miss = f.get("passed_with_missing_data")
            if miss:
                s += f" (incl. {miss} passed with no data for this column)"
            return s
        parts.append("Funnel (companies you left at each step): " +
                     " -> ".join(_step(f) for f in excl["funnel"]))
    if excl["near_misses"]:
        nm_lines = "; ".join(f"{n['symbol']} (missed {n['missed_filter']} by {n['missed_by_pct']}%)"
                             for n in excl["near_misses"][:8])
        parts.append(f"Near misses — failed exactly one criterion by a small margin: {nm_lines}")
    head = "\n".join(parts) + "\n\n"
    return _text(head + da.df_to_md(df, max_rows=int(args.get("limit", 25))))


@tool(
    "screen_by_year",
    "Screen the universe by a SPECIFIC HISTORICAL YEAR, not today's snapshot — answers "
    "'ROCE > 20% in FY2024' or 'best/worst price performers in 2023'. "
    "kind='fundamental' (default): year = fiscal year ending March (year=2024 means FY ending "
    "Mar 2024); filter/sort columns: sales_cr, opm_pct, net_profit_cr, eps, roce_pct (non-"
    "financials), roe_pct (banks/NBFCs), sales_yoy_growth_pct, net_profit_yoy_growth_pct, "
    "eps_yoy_growth_pct. kind='price_return': year = calendar year; filter/sort on return_pct "
    "(split/bonus/dividend-adjusted close-to-close for that year). This is a point-in-time "
    "screen — not a recommendation, and extreme ratios on tiny-capital-base companies should "
    "be sanity-checked before acting on them.",
    {"type": "object",
     "properties": {
         "year": {"type": "integer", "description": "fiscal year (Mar-end) or calendar year per 'kind'"},
         "kind": {"type": "string", "enum": ["fundamental", "price_return"], "default": "fundamental"},
         "min": {"type": "object", "additionalProperties": {"type": "number"}},
         "max": {"type": "object", "additionalProperties": {"type": "number"}},
         "sector": {"type": "string"},
         "industry": {"type": "string"},
         "sort_by": {"type": "string"},
         "ascending": {"type": "boolean", "default": False},
         "limit": {"type": "integer", "default": 25}},
     "required": ["year"]},
)
async def screen_by_year(args):
    from .screener import screen_by_year as _sby
    def work():
        return _sby(
            int(args["year"]), kind=args.get("kind", "fundamental"),
            min_filters=args.get("min"), max_filters=args.get("max"),
            sector=args.get("sector"), industry=args.get("industry"),
            sort_by=args.get("sort_by"), ascending=bool(args.get("ascending", False)),
            limit=int(args.get("limit", 25)),
        )
    df, matched, note = await asyncio.to_thread(work)
    if matched == 0:
        return _err(f"no companies matched for {note}")
    head = f"{matched} companies matched ({note}); showing {len(df)}.\n\n"
    return _text(head + da.df_to_md(df, max_rows=int(args.get("limit", 25))))


@tool(
    "screen_consistency",
    "Screen the WHOLE universe for names that clear a bar EVERY year over N years — not just "
    "the latest year (screen_by_year) or today's snapshot (screen_stocks). This is the Coffee "
    "Can-style 'ROCE>=15% every year for 10 years' rule in one call, done for every company at "
    "once, instead of a coarse cut plus manual per-candidate verification. Pass any of: "
    "sales_cr, opm_pct, net_profit_cr, eps, roce_pct, roe_pct, sales_yoy_growth_pct, "
    "net_profit_yoy_growth_pct, eps_yoy_growth_pct — or metric='roce_or_roe' for a mixed "
    "universe (this is NOT a separate metric, it just applies ROCE to non-financials and ROE "
    "to banks/NBFCs per company, the standard Coffee Can convention — the output's "
    "metric_used column always states which one was actually used per row). "
    "max_violations lets a small number of off years through (0 = strict, every single year). "
    "Companies with less history than min_years_required are excluded, not penalised, if newer "
    "than the window. To combine two rules (e.g. Coffee Can's ROCE/ROE AND sales-growth "
    "together), call this twice and intersect the returned symbols. Screening only — never advice.",
    {"type": "object",
     "properties": {
         "metric": {"type": "string", "description": "a metric column name, or 'roce_or_roe' "
                    "for the ROCE/ROE-per-company-type blend (see description)"},
         "min_value": {"type": "number"},
         "n_years": {"type": "integer", "default": 10},
         "max_violations": {"type": "integer", "default": 0},
         "min_years_required": {"type": "integer", "default": 5},
         "sector": {"type": "string"},
         "industry": {"type": "string"},
         "sort_by": {"type": "string"},
         "ascending": {"type": "boolean", "default": False},
         "limit": {"type": "integer", "default": 25}},
     "required": ["metric", "min_value"]},
)
async def screen_consistency(args):
    from .screener import screen_consistency as _sc
    def work():
        return _sc(
            args["metric"], float(args["min_value"]),
            n_years=int(args.get("n_years", 10)),
            max_violations=int(args.get("max_violations", 0)),
            min_years_required=int(args.get("min_years_required", 5)),
            sector=args.get("sector"), industry=args.get("industry"),
            sort_by=args.get("sort_by"), ascending=bool(args.get("ascending", False)),
            limit=int(args.get("limit", 25)),
        )
    try:
        df, matched, note = await asyncio.to_thread(work)
    except ValueError as e:
        return _err(str(e))
    if matched == 0:
        return _err(f"no companies matched: {note}")
    head = f"{matched} companies matched ({note}); showing {len(df)}.\n\n"
    return _text(head + da.df_to_md(df, max_rows=int(args.get("limit", 25))))


@tool(
    "screen_consistency",
    "Screen the WHOLE universe for names that clear a bar EVERY year over N years — not just "
    "the latest year (screen_by_year) or today's snapshot (screen_stocks). This is the Coffee "
    "Can-style 'ROCE>=15% every year for 10 years' rule in one call, done for every company at "
    "once, instead of a coarse cut plus manual per-candidate verification. Pass any of: "
    "sales_cr, opm_pct, net_profit_cr, eps, roce_pct, roe_pct, sales_yoy_growth_pct, "
    "net_profit_yoy_growth_pct, eps_yoy_growth_pct — or metric='roce_or_roe' for a mixed "
    "universe (this is NOT a separate metric, it just applies ROCE to non-financials and ROE "
    "to banks/NBFCs per company, the standard Coffee Can convention — the output's "
    "metric_used column always states which one was actually used per row). "
    "max_violations lets a small number of off years through (0 = strict, every single year). "
    "Companies with less history than min_years_required are excluded, not penalised, if newer "
    "than the window. To combine two rules (e.g. Coffee Can's ROCE/ROE AND sales-growth "
    "together), call this twice and intersect the returned symbols. Screening only — never advice.",
    {"type": "object",
     "properties": {
         "metric": {"type": "string", "description": "a metric column name, or 'roce_or_roe' "
                    "for the ROCE/ROE-per-company-type blend (see description)"},
         "min_value": {"type": "number"},
         "n_years": {"type": "integer", "default": 10},
         "max_violations": {"type": "integer", "default": 0},
         "min_years_required": {"type": "integer", "default": 5},
         "sector": {"type": "string"},
         "industry": {"type": "string"},
         "sort_by": {"type": "string"},
         "ascending": {"type": "boolean", "default": False},
         "limit": {"type": "integer", "default": 25}},
     "required": ["metric", "min_value"]},
)
async def screen_consistency(args):
    from .screener import screen_consistency as _sc
    def work():
        return _sc(
            args["metric"], float(args["min_value"]),
            n_years=int(args.get("n_years", 10)),
            max_violations=int(args.get("max_violations", 0)),
            min_years_required=int(args.get("min_years_required", 5)),
            sector=args.get("sector"), industry=args.get("industry"),
            sort_by=args.get("sort_by"), ascending=bool(args.get("ascending", False)),
            limit=int(args.get("limit", 25)),
        )
    try:
        df, matched, note = await asyncio.to_thread(work)
    except ValueError as e:
        return _err(str(e))
    if matched == 0:
        return _err(f"no companies matched: {note}")
    head = f"{matched} companies matched ({note}); showing {len(df)}.\n\n"
    return _text(head + da.df_to_md(df, max_rows=int(args.get("limit", 25))))


@tool(
    "screen_by_year",
    "Screen the universe by a SPECIFIC HISTORICAL YEAR, not today's snapshot — answers "
    "'ROCE > 20% in FY2024' or 'best/worst price performers in 2023'. "
    "kind='fundamental' (default): year = fiscal year ending March (year=2024 means FY ending "
    "Mar 2024); filter/sort columns: sales_cr, opm_pct, net_profit_cr, eps, roce_pct (non-"
    "financials), roe_pct (banks/NBFCs), sales_yoy_growth_pct, net_profit_yoy_growth_pct, "
    "eps_yoy_growth_pct. kind='price_return': year = calendar year; filter/sort on return_pct "
    "(split/bonus/dividend-adjusted close-to-close for that year). This is a point-in-time "
    "screen — not a recommendation, and extreme ratios on tiny-capital-base companies should "
    "be sanity-checked before acting on them.",
    {"type": "object",
     "properties": {
         "year": {"type": "integer", "description": "fiscal year (Mar-end) or calendar year per 'kind'"},
         "kind": {"type": "string", "enum": ["fundamental", "price_return"], "default": "fundamental"},
         "min": {"type": "object", "additionalProperties": {"type": "number"}},
         "max": {"type": "object", "additionalProperties": {"type": "number"}},
         "sector": {"type": "string"},
         "industry": {"type": "string"},
         "sort_by": {"type": "string"},
         "ascending": {"type": "boolean", "default": False},
         "limit": {"type": "integer", "default": 25}},
     "required": ["year"]},
)
async def screen_by_year(args):
    from .screener import screen_by_year as _sby
    def work():
        return _sby(
            int(args["year"]), kind=args.get("kind", "fundamental"),
            min_filters=args.get("min"), max_filters=args.get("max"),
            sector=args.get("sector"), industry=args.get("industry"),
            sort_by=args.get("sort_by"), ascending=bool(args.get("ascending", False)),
            limit=int(args.get("limit", 25)),
        )
    df, matched, note = await asyncio.to_thread(work)
    if matched == 0:
        return _err(f"no companies matched for {note}")
    head = f"{matched} companies matched ({note}); showing {len(df)}.\n\n"
    return _text(head + da.df_to_md(df, max_rows=int(args.get("limit", 25))))


@tool(
    "screen_by_year",
    "Screen the universe by a SPECIFIC HISTORICAL YEAR, not today's snapshot — answers "
    "'ROCE > 20% in FY2024' or 'best/worst price performers in 2023'. "
    "kind='fundamental' (default): year = fiscal year ending March (year=2024 means FY ending "
    "Mar 2024); filter/sort columns: sales_cr, opm_pct, net_profit_cr, eps, roce_pct (non-"
    "financials), roe_pct (banks/NBFCs), sales_yoy_growth_pct, net_profit_yoy_growth_pct, "
    "eps_yoy_growth_pct. kind='price_return': year = calendar year; filter/sort on return_pct "
    "(split/bonus/dividend-adjusted close-to-close for that year). This is a point-in-time "
    "screen — not a recommendation, and extreme ratios on tiny-capital-base companies should "
    "be sanity-checked before acting on them.",
    {"type": "object",
     "properties": {
         "year": {"type": "integer", "description": "fiscal year (Mar-end) or calendar year per 'kind'"},
         "kind": {"type": "string", "enum": ["fundamental", "price_return"], "default": "fundamental"},
         "min": {"type": "object", "additionalProperties": {"type": "number"}},
         "max": {"type": "object", "additionalProperties": {"type": "number"}},
         "sector": {"type": "string"},
         "industry": {"type": "string"},
         "sort_by": {"type": "string"},
         "ascending": {"type": "boolean", "default": False},
         "limit": {"type": "integer", "default": 25}},
     "required": ["year"]},
)
async def screen_by_year(args):
    from .screener import screen_by_year as _sby
    def work():
        return _sby(
            int(args["year"]), kind=args.get("kind", "fundamental"),
            min_filters=args.get("min"), max_filters=args.get("max"),
            sector=args.get("sector"), industry=args.get("industry"),
            sort_by=args.get("sort_by"), ascending=bool(args.get("ascending", False)),
            limit=int(args.get("limit", 25)),
        )
    df, matched, note = await asyncio.to_thread(work)
    if matched == 0:
        return _err(f"no companies matched for {note}")
    head = f"{matched} companies matched ({note}); showing {len(df)}.\n\n"
    return _text(head + da.df_to_md(df, max_rows=int(args.get("limit", 25))))


@tool(
    "search_documents",
    "Semantic search over the indexed document corpus: earnings-call transcripts (Q&A level) "
    "and presentations, annual reports, credit-rating rationales, corporate announcements, "
    "XBRL filings. Filter by symbol, doc_types, and date range (YYYYMMDD ints — note: undated "
    "docs like announcements are excluded when date filters are set). For cross-company "
    "thematic queries (no symbol), set max_per_symbol (e.g. 2) so one company doesn't "
    "dominate. Use for qualitative questions: guidance, strategy, risks, capex plans, "
    "management commentary, rating rationale, segment detail. For NUMBERS use "
    "financial_statements instead.",
    {"type": "object",
     "properties": {
         "query": {"type": "string"},
         "symbol": {"type": "string"},
         "doc_types": {"type": "array", "items": {"type": "string", "enum": DOC_TYPES}},
         "date_from": {"type": "integer", "description": "e.g. 20240101"},
         "date_to": {"type": "integer"},
         "max_per_symbol": {"type": "integer",
                            "description": "cap hits per company on cross-company queries"},
         "top_k": {"type": "integer", "default": 8}},
     "required": ["query"]},
)
async def search_documents(args):
    from . import rag
    def work():
        return rag.search(args["query"], symbol=args.get("symbol"),
                          doc_types=args.get("doc_types"),
                          date_from=args.get("date_from"), date_to=args.get("date_to"),
                          k=int(args.get("top_k", 8)),
                          max_per_symbol=args.get("max_per_symbol"))
    try:
        hits = await asyncio.to_thread(work)
    except Exception as e:
        return _err(f"search failed: {e}. Has the index been built? "
                    "Run: python -m agent.build_index --symbols <SYMS>")
    if not hits:
        return _text("No results. The company may not be indexed yet "
                     "(python -m agent.build_index --symbols <SYM>).")
    parts = []
    for h in hits:
        parts.append(f"--- {h['symbol']} | {h['doc_type']} | {h['period'] or 'undated'} "
                     f"| score {h['score']} | {h['source']}\n{h['text']}")
    return _text("\n\n".join(parts))


@tool(
    "topic_timeline",
    "How a topic EVOLVED across quarters for one company: retrieves the best chunk(s) from "
    "each of the last N periods separately (one search per quarter, newest first), so every "
    "period is represented. Use for questions like 'how did margin guidance change over the "
    "last 2 years' or 'track capex commentary quarter by quarter'.",
    {"type": "object",
     "properties": {
         "query": {"type": "string"},
         "symbol": {"type": "string"},
         "doc_types": {"type": "array", "items": {"type": "string", "enum": DOC_TYPES},
                       "description": "default: concall transcripts + presentations"},
         "n_periods": {"type": "integer", "default": 8},
         "per_period": {"type": "integer", "default": 1}},
     "required": ["query", "symbol"]},
)
async def topic_timeline(args):
    from . import rag
    def work():
        return rag.search_timeline(args["query"], args["symbol"],
                                   doc_types=args.get("doc_types"),
                                   n_periods=int(args.get("n_periods", 8)),
                                   per_period=int(args.get("per_period", 1)))
    try:
        tl = await asyncio.to_thread(work)
    except Exception as e:
        return _err(f"timeline search failed: {e}")
    if not tl:
        return _text(f"No dated documents indexed for {args['symbol']}. "
                     "Run: python -m agent.build_index --symbols " + args["symbol"])
    parts = []
    for period, hits in tl.items():
        for h in hits:
            parts.append(f"=== {period} ({h['doc_type']}, score {h['score']}) ===\n{h['text']}")
    return _text("\n\n".join(parts))


@tool(
    "peers_and_index",
    "Peer companies (same industry) and index memberships for a symbol.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"}},
     "required": ["symbol"]},
)
async def peers_and_index(args):
    def work():
        sym = args["symbol"].upper()
        p = da.profile(sym) or {}
        out = {
            "symbol": sym,
            "peers_with_pe": p.get("peers"),
            "tijori_peers": da.tijori_peers().get(sym),
            "index_membership": da.index_membership().get(sym),
        }
        ent = da.entities()
        row = ent[ent["symbol"] == sym]
        if not row.empty:
            ind = row.iloc[0].get("industry")
            out["industry"] = ind
            sp = da.sector_peers()
            if ind in sp:
                out["industry_peer_symbols"] = sp[ind][:40]
        return out
    return _text(_js(await asyncio.to_thread(work)))


@tool(
    "macro_data",
    "Indian macro series (monthly/annual CSVs): cpi_yoy, gsec10y, iip, policy_rate, rate3m, "
    "usdinr, macro_monthly, macro_annual. Omit 'series' to list available ones. "
    "Returns the most recent 24 observations plus 10-year-ago context.",
    {"type": "object",
     "properties": {"series": {"type": "string"}},
     "required": []},
)
async def macro_data(args):
    def work():
        if not args.get("series"):
            return "Available series: " + ", ".join(da.macro_list())
        df = da.macro_series(args["series"])
        if df is None:
            return None
        return da.df_to_md(df.tail(24))
    r = await asyncio.to_thread(work)
    if r is None:
        return _err(f"unknown series '{args.get('series')}'. {', '.join(da.macro_list())}")
    return _text(r)


@tool(
    "index_data",
    "NSE index data. kind=prices: OHLCV history (monthly). kind=valuation: PE/PB/div-yield "
    "history. kind=list: available indices. Names e.g. nifty50, nifty500, bank, it, auto, "
    "energy, fmcg, metal, pharma.",
    {"type": "object",
     "properties": {"index": {"type": "string"},
                    "kind": {"type": "string", "enum": ["prices", "valuation", "list"],
                             "default": "prices"}},
     "required": []},
)
async def index_data(args):
    def work():
        kind = args.get("kind", "prices")
        if kind == "list" or not args.get("index"):
            return "Available indices: " + ", ".join(da.index_list())
        name = args["index"].lower().replace(" ", "")
        if kind == "valuation":
            df = da.index_valuation(name)
            return da.df_to_md(df.tail(30)) if df is not None else None
        df = da.index_prices(name)
        if df is None:
            return None
        m = (df.set_index("Date").resample("ME")
               .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"})
               .dropna().reset_index())
        m["Date"] = m["Date"].dt.date
        return da.df_to_md(m.round(1).tail(36))
    r = await asyncio.to_thread(work)
    if r is None:
        return _err(f"unknown index '{args.get('index')}'. Available: {', '.join(da.index_list())}")
    return _text(r)


@tool(
    "xbrl_quarterly",
    "NSE XBRL quarterly filings (de-cumulated to true standalone quarters, verified vs "
    "screener): full P&L line items per quarter + business-SEGMENT revenue/result where "
    "filed (e.g. Jio/Retail/O2C for Reliance — segment data is NOT in the screener CSVs). "
    "Only for companies whose XBRL has been refreshed with the fixed pipeline; falls back "
    "with instructions if absent. basis: consolidated|standalone.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"},
                    "basis": {"type": "string", "enum": ["consolidated", "standalone"],
                              "default": "consolidated"},
                    "quarters": {"type": "integer", "default": 8},
                    "include_segments": {"type": "boolean", "default": True}},
     "required": ["symbol"]},
)
async def xbrl_quarterly(args):
    def work():
        import json as _json
        from .config import STRUCTURED
        sym = args["symbol"].upper()
        p = STRUCTURED / f"{sym}_xbrl.json"
        if not p.exists():
            return (f"No refreshed XBRL for {sym}. Quarterly numbers are available via "
                    f"financial_statements(symbol, 'quarterly_results'). (To refresh XBRL: "
                    f"python scripts/02_nse_xbrl_quarterly.py --symbol {sym} then "
                    f"scripts/29_xbrl_to_md.py.)")
        d = _json.loads(p.read_text(encoding="utf-8"))
        want_cons = args.get("basis", "consolidated") == "consolidated"
        qs = [q for q in d["quarters"] if bool(q.get("consolidated")) == want_cons]
        qs.sort(key=lambda q: q.get("period_end", ""), reverse=True)
        qs = qs[: int(args.get("quarters", 8))]
        if not qs:
            return f"No {args.get('basis')} quarters in {sym} XBRL."
        headline = ["Revenue from operations", "Total income", "Total expenses",
                    "Profit before tax", "Net profit for period", "Basic EPS"]
        lines = [f"{sym} XBRL {args.get('basis', 'consolidated')} quarters "
                 f"(Rs crore, de-cumulated; * = derived by YTD subtraction):"]
        hdr = " | ".join(["item"] + [q["period_end"] for q in qs])
        lines += [hdr, " | ".join(["---"] * (len(qs) + 1))]
        keys = [k for k in qs[0].get("facts", {}) if k in headline] or \
               list(qs[0].get("facts", {}))[:8]
        for k in keys:
            row = [k] + [str(q.get("facts", {}).get(k, "")) for q in qs]
            lines.append(" | ".join(row))
        if args.get("include_segments", True):
            for q in qs[:4]:
                segs = q.get("segments") or q.get("facts", {}).get("segments")
                if segs:
                    lines.append(f"\nSegments {q['period_end']}: "
                                 + _js(segs, limit=1500))
        lines.append(f"\n(audited flags: "
                     + ", ".join(f"{q['period_end']}={q.get('audited', '?')}" for q in qs[:4])
                     + f"; source: NSE XBRL, fetched {d.get('fetched_at', '?')})")
        return "\n".join(lines)
    return _text(await asyncio.to_thread(work))


@tool(
    "xbrl_quarterly",
    "NSE XBRL quarterly filings (de-cumulated to true standalone quarters, verified vs "
    "screener): full P&L line items per quarter + business-SEGMENT revenue/result where "
    "filed (e.g. Jio/Retail/O2C for Reliance — segment data is NOT in the screener CSVs). "
    "Only for companies whose XBRL has been refreshed with the fixed pipeline; falls back "
    "with instructions if absent. basis: consolidated|standalone.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"},
                    "basis": {"type": "string", "enum": ["consolidated", "standalone"],
                              "default": "consolidated"},
                    "quarters": {"type": "integer", "default": 8},
                    "include_segments": {"type": "boolean", "default": True}},
     "required": ["symbol"]},
)
async def xbrl_quarterly(args):
    def work():
        import json as _json
        from .config import STRUCTURED
        sym = args["symbol"].upper()
        p = STRUCTURED / f"{sym}_xbrl.json"
        if not p.exists():
            return (f"No refreshed XBRL for {sym}. Quarterly numbers are available via "
                    f"financial_statements(symbol, 'quarterly_results'). (To refresh XBRL: "
                    f"python scripts/02_nse_xbrl_quarterly.py --symbol {sym} then "
                    f"scripts/29_xbrl_to_md.py.)")
        d = _json.loads(p.read_text(encoding="utf-8"))
        want_cons = args.get("basis", "consolidated") == "consolidated"
        qs = [q for q in d["quarters"] if bool(q.get("consolidated")) == want_cons]
        qs.sort(key=lambda q: q.get("period_end", ""), reverse=True)
        qs = qs[: int(args.get("quarters", 8))]
        if not qs:
            return f"No {args.get('basis')} quarters in {sym} XBRL."
        headline = ["Revenue from operations", "Total income", "Total expenses",
                    "Profit before tax", "Net profit for period", "Basic EPS"]
        lines = [f"{sym} XBRL {args.get('basis', 'consolidated')} quarters "
                 f"(Rs crore, de-cumulated; * = derived by YTD subtraction):"]
        hdr = " | ".join(["item"] + [q["period_end"] for q in qs])
        lines += [hdr, " | ".join(["---"] * (len(qs) + 1))]
        keys = [k for k in qs[0].get("facts", {}) if k in headline] or \
               list(qs[0].get("facts", {}))[:8]
        for k in keys:
            row = [k] + [str(q.get("facts", {}).get(k, "")) for q in qs]
            lines.append(" | ".join(row))
        if args.get("include_segments", True):
            for q in qs[:4]:
                segs = q.get("segments") or q.get("facts", {}).get("segments")
                if segs:
                    lines.append(f"\nSegments {q['period_end']}: "
                                 + _js(segs, limit=1500))
        lines.append(f"\n(audited flags: "
                     + ", ".join(f"{q['period_end']}={q.get('audited', '?')}" for q in qs[:4])
                     + f"; source: NSE XBRL, fetched {d.get('fetched_at', '?')})")
        return "\n".join(lines)
    return _text(await asyncio.to_thread(work))


@tool(
    "xbrl_quarterly",
    "NSE XBRL quarterly filings (de-cumulated to true standalone quarters, verified vs "
    "screener): full P&L line items per quarter + business-SEGMENT revenue/result where "
    "filed (e.g. Jio/Retail/O2C for Reliance — segment data is NOT in the screener CSVs). "
    "Only for companies whose XBRL has been refreshed with the fixed pipeline; falls back "
    "with instructions if absent. basis: consolidated|standalone.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"},
                    "basis": {"type": "string", "enum": ["consolidated", "standalone"],
                              "default": "consolidated"},
                    "quarters": {"type": "integer", "default": 8},
                    "include_segments": {"type": "boolean", "default": True}},
     "required": ["symbol"]},
)
async def xbrl_quarterly(args):
    def work():
        import json as _json
        from .config import STRUCTURED
        sym = args["symbol"].upper()
        p = STRUCTURED / f"{sym}_xbrl.json"
        if not p.exists():
            return (f"No refreshed XBRL for {sym}. Quarterly numbers are available via "
                    f"financial_statements(symbol, 'quarterly_results'). (To refresh XBRL: "
                    f"python scripts/02_nse_xbrl_quarterly.py --symbol {sym} then "
                    f"scripts/29_xbrl_to_md.py.)")
        d = _json.loads(p.read_text(encoding="utf-8"))
        want_cons = args.get("basis", "consolidated") == "consolidated"
        qs = [q for q in d["quarters"] if bool(q.get("consolidated")) == want_cons]
        qs.sort(key=lambda q: q.get("period_end", ""), reverse=True)
        qs = qs[: int(args.get("quarters", 8))]
        if not qs:
            return f"No {args.get('basis')} quarters in {sym} XBRL."
        headline = ["Revenue from operations", "Total income", "Total expenses",
                    "Profit before tax", "Net profit for period", "Basic EPS"]
        lines = [f"{sym} XBRL {args.get('basis', 'consolidated')} quarters "
                 f"(Rs crore, de-cumulated; * = derived by YTD subtraction):"]
        hdr = " | ".join(["item"] + [q["period_end"] for q in qs])
        lines += [hdr, " | ".join(["---"] * (len(qs) + 1))]
        keys = [k for k in qs[0].get("facts", {}) if k in headline] or \
               list(qs[0].get("facts", {}))[:8]
        for k in keys:
            row = [k] + [str(q.get("facts", {}).get(k, "")) for q in qs]
            lines.append(" | ".join(row))
        if args.get("include_segments", True):
            for q in qs[:4]:
                segs = q.get("segments") or q.get("facts", {}).get("segments")
                if segs:
                    lines.append(f"\nSegments {q['period_end']}: "
                                 + _js(segs, limit=1500))
        lines.append(f"\n(audited flags: "
                     + ", ".join(f"{q['period_end']}={q.get('audited', '?')}" for q in qs[:4])
                     + f"; source: NSE XBRL, fetched {d.get('fetched_at', '?')})")
        return "\n".join(lines)
    return _text(await asyncio.to_thread(work))


@tool(
    "xbrl_quarterly",
    "NSE XBRL quarterly filings (de-cumulated to true standalone quarters, verified vs "
    "screener): full P&L line items per quarter + business-SEGMENT revenue/result where "
    "filed (e.g. Jio/Retail/O2C for Reliance — segment data is NOT in the screener CSVs). "
    "Only for companies whose XBRL has been refreshed with the fixed pipeline; falls back "
    "with instructions if absent. basis: consolidated|standalone.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"},
                    "basis": {"type": "string", "enum": ["consolidated", "standalone"],
                              "default": "consolidated"},
                    "quarters": {"type": "integer", "default": 8},
                    "include_segments": {"type": "boolean", "default": True}},
     "required": ["symbol"]},
)
async def xbrl_quarterly(args):
    def work():
        import json as _json
        from .config import STRUCTURED
        sym = args["symbol"].upper()
        p = STRUCTURED / f"{sym}_xbrl.json"
        if not p.exists():
            return (f"No refreshed XBRL for {sym}. Quarterly numbers are available via "
                    f"financial_statements(symbol, 'quarterly_results'). (To refresh XBRL: "
                    f"python scripts/02_nse_xbrl_quarterly.py --symbol {sym} then "
                    f"scripts/29_xbrl_to_md.py.)")
        d = _json.loads(p.read_text(encoding="utf-8"))
        want_cons = args.get("basis", "consolidated") == "consolidated"
        qs = [q for q in d["quarters"] if bool(q.get("consolidated")) == want_cons]
        qs.sort(key=lambda q: q.get("period_end", ""), reverse=True)
        qs = qs[: int(args.get("quarters", 8))]
        if not qs:
            return f"No {args.get('basis')} quarters in {sym} XBRL."
        headline = ["Revenue from operations", "Total income", "Total expenses",
                    "Profit before tax", "Net profit for period", "Basic EPS"]
        lines = [f"{sym} XBRL {args.get('basis', 'consolidated')} quarters "
                 f"(Rs crore, de-cumulated; * = derived by YTD subtraction):"]
        hdr = " | ".join(["item"] + [q["period_end"] for q in qs])
        lines += [hdr, " | ".join(["---"] * (len(qs) + 1))]
        keys = [k for k in qs[0].get("facts", {}) if k in headline] or \
               list(qs[0].get("facts", {}))[:8]
        for k in keys:
            row = [k] + [str(q.get("facts", {}).get(k, "")) for q in qs]
            lines.append(" | ".join(row))
        if args.get("include_segments", True):
            for q in qs[:4]:
                segs = q.get("segments") or q.get("facts", {}).get("segments")
                if segs:
                    lines.append(f"\nSegments {q['period_end']}: "
                                 + _js(segs, limit=1500))
        lines.append(f"\n(audited flags: "
                     + ", ".join(f"{q['period_end']}={q.get('audited', '?')}" for q in qs[:4])
                     + f"; source: NSE XBRL, fetched {d.get('fetched_at', '?')})")
        return "\n".join(lines)
    return _text(await asyncio.to_thread(work))


@tool(
    "xbrl_quarterly",
    "NSE XBRL quarterly filings (de-cumulated to true standalone quarters, verified vs "
    "screener): full P&L line items per quarter + business-SEGMENT revenue/result where "
    "filed (e.g. Jio/Retail/O2C for Reliance — segment data is NOT in the screener CSVs). "
    "Only for companies whose XBRL has been refreshed with the fixed pipeline; falls back "
    "with instructions if absent. basis: consolidated|standalone.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"},
                    "basis": {"type": "string", "enum": ["consolidated", "standalone"],
                              "default": "consolidated"},
                    "quarters": {"type": "integer", "default": 8},
                    "include_segments": {"type": "boolean", "default": True}},
     "required": ["symbol"]},
)
async def xbrl_quarterly(args):
    def work():
        import json as _json
        from .config import STRUCTURED
        sym = args["symbol"].upper()
        p = STRUCTURED / f"{sym}_xbrl.json"
        if not p.exists():
            return (f"No refreshed XBRL for {sym}. Quarterly numbers are available via "
                    f"financial_statements(symbol, 'quarterly_results'). (To refresh XBRL: "
                    f"python scripts/02_nse_xbrl_quarterly.py --symbol {sym} then "
                    f"scripts/29_xbrl_to_md.py.)")
        d = _json.loads(p.read_text(encoding="utf-8"))
        want_cons = args.get("basis", "consolidated") == "consolidated"
        qs = [q for q in d["quarters"] if bool(q.get("consolidated")) == want_cons]
        qs.sort(key=lambda q: q.get("period_end", ""), reverse=True)
        qs = qs[: int(args.get("quarters", 8))]
        if not qs:
            return f"No {args.get('basis')} quarters in {sym} XBRL."
        headline = ["Revenue from operations", "Total income", "Total expenses",
                    "Profit before tax", "Net profit for period", "Basic EPS"]
        lines = [f"{sym} XBRL {args.get('basis', 'consolidated')} quarters "
                 f"(Rs crore, de-cumulated; * = derived by YTD subtraction):"]
        hdr = " | ".join(["item"] + [q["period_end"] for q in qs])
        lines += [hdr, " | ".join(["---"] * (len(qs) + 1))]
        keys = [k for k in qs[0].get("facts", {}) if k in headline] or \
               list(qs[0].get("facts", {}))[:8]
        for k in keys:
            row = [k] + [str(q.get("facts", {}).get(k, "")) for q in qs]
            lines.append(" | ".join(row))
        if args.get("include_segments", True):
            for q in qs[:4]:
                segs = q.get("segments") or q.get("facts", {}).get("segments")
                if segs:
                    lines.append(f"\nSegments {q['period_end']}: "
                                 + _js(segs, limit=1500))
        lines.append(f"\n(audited flags: "
                     + ", ".join(f"{q['period_end']}={q.get('audited', '?')}" for q in qs[:4])
                     + f"; source: NSE XBRL, fetched {d.get('fetched_at', '?')})")
        return "\n".join(lines)
    return _text(await asyncio.to_thread(work))


@tool(
    "xbrl_quarterly",
    "NSE XBRL quarterly filings (de-cumulated to true standalone quarters, verified vs "
    "screener): full P&L line items per quarter + business-SEGMENT revenue/result where "
    "filed (e.g. Jio/Retail/O2C for Reliance — segment data is NOT in the screener CSVs). "
    "Only for companies whose XBRL has been refreshed with the fixed pipeline; falls back "
    "with instructions if absent. basis: consolidated|standalone.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"},
                    "basis": {"type": "string", "enum": ["consolidated", "standalone"],
                              "default": "consolidated"},
                    "quarters": {"type": "integer", "default": 8},
                    "include_segments": {"type": "boolean", "default": True}},
     "required": ["symbol"]},
)
async def xbrl_quarterly(args):
    def work():
        import json as _json
        from .config import STRUCTURED
        sym = args["symbol"].upper()
        p = STRUCTURED / f"{sym}_xbrl.json"
        if not p.exists():
            return (f"No refreshed XBRL for {sym}. Quarterly numbers are available via "
                    f"financial_statements(symbol, 'quarterly_results'). (To refresh XBRL: "
                    f"python scripts/02_nse_xbrl_quarterly.py --symbol {sym} then "
                    f"scripts/29_xbrl_to_md.py.)")
        d = _json.loads(p.read_text(encoding="utf-8"))
        want_cons = args.get("basis", "consolidated") == "consolidated"
        qs = [q for q in d["quarters"] if bool(q.get("consolidated")) == want_cons]
        qs.sort(key=lambda q: q.get("period_end", ""), reverse=True)
        qs = qs[: int(args.get("quarters", 8))]
        if not qs:
            return f"No {args.get('basis')} quarters in {sym} XBRL."
        headline = ["Revenue from operations", "Total income", "Total expenses",
                    "Profit before tax", "Net profit for period", "Basic EPS"]
        lines = [f"{sym} XBRL {args.get('basis', 'consolidated')} quarters "
                 f"(Rs crore, de-cumulated; * = derived by YTD subtraction):"]
        hdr = " | ".join(["item"] + [q["period_end"] for q in qs])
        lines += [hdr, " | ".join(["---"] * (len(qs) + 1))]
        keys = [k for k in qs[0].get("facts", {}) if k in headline] or \
               list(qs[0].get("facts", {}))[:8]
        for k in keys:
            row = [k] + [str(q.get("facts", {}).get(k, "")) for q in qs]
            lines.append(" | ".join(row))
        if args.get("include_segments", True):
            for q in qs[:4]:
                segs = q.get("segments") or q.get("facts", {}).get("segments")
                if segs:
                    lines.append(f"\nSegments {q['period_end']}: "
                                 + _js(segs, limit=1500))
        lines.append(f"\n(audited flags: "
                     + ", ".join(f"{q['period_end']}={q.get('audited', '?')}" for q in qs[:4])
                     + f"; source: NSE XBRL, fetched {d.get('fetched_at', '?')})")
        return "\n".join(lines)
    return _text(await asyncio.to_thread(work))


@tool(
    "xbrl_quarterly",
    "NSE XBRL quarterly filings (de-cumulated to true standalone quarters, verified vs "
    "screener): full P&L line items per quarter + business-SEGMENT revenue/result where "
    "filed (e.g. Jio/Retail/O2C for Reliance — segment data is NOT in the screener CSVs). "
    "Only for companies whose XBRL has been refreshed with the fixed pipeline; falls back "
    "with instructions if absent. basis: consolidated|standalone.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"},
                    "basis": {"type": "string", "enum": ["consolidated", "standalone"],
                              "default": "consolidated"},
                    "quarters": {"type": "integer", "default": 8},
                    "include_segments": {"type": "boolean", "default": True}},
     "required": ["symbol"]},
)
async def xbrl_quarterly(args):
    def work():
        import json as _json
        from .config import STRUCTURED
        sym = args["symbol"].upper()
        p = STRUCTURED / f"{sym}_xbrl.json"
        if not p.exists():
            return (f"No refreshed XBRL for {sym}. Quarterly numbers are available via "
                    f"financial_statements(symbol, 'quarterly_results'). (To refresh XBRL: "
                    f"python scripts/02_nse_xbrl_quarterly.py --symbol {sym} then "
                    f"scripts/29_xbrl_to_md.py.)")
        d = _json.loads(p.read_text(encoding="utf-8"))
        want_cons = args.get("basis", "consolidated") == "consolidated"
        qs = [q for q in d["quarters"] if bool(q.get("consolidated")) == want_cons]
        qs.sort(key=lambda q: q.get("period_end", ""), reverse=True)
        qs = qs[: int(args.get("quarters", 8))]
        if not qs:
            return f"No {args.get('basis')} quarters in {sym} XBRL."
        headline = ["Revenue from operations", "Total income", "Total expenses",
                    "Profit before tax", "Net profit for period", "Basic EPS"]
        lines = [f"{sym} XBRL {args.get('basis', 'consolidated')} quarters "
                 f"(Rs crore, de-cumulated; * = derived by YTD subtraction):"]
        hdr = " | ".join(["item"] + [q["period_end"] for q in qs])
        lines += [hdr, " | ".join(["---"] * (len(qs) + 1))]
        keys = [k for k in qs[0].get("facts", {}) if k in headline] or \
               list(qs[0].get("facts", {}))[:8]
        for k in keys:
            row = [k] + [str(q.get("facts", {}).get(k, "")) for q in qs]
            lines.append(" | ".join(row))
        if args.get("include_segments", True):
            for q in qs[:4]:
                segs = q.get("segments") or q.get("facts", {}).get("segments")
                if segs:
                    lines.append(f"\nSegments {q['period_end']}: "
                                 + _js(segs, limit=1500))
        lines.append(f"\n(audited flags: "
                     + ", ".join(f"{q['period_end']}={q.get('audited', '?')}" for q in qs[:4])
                     + f"; source: NSE XBRL, fetched {d.get('fetched_at', '?')})")
        return "\n".join(lines)
    return _text(await asyncio.to_thread(work))


@tool(
    "xbrl_quarterly",
    "NSE XBRL quarterly filings (de-cumulated to true standalone quarters, verified vs "
    "screener): full P&L line items per quarter + business-SEGMENT revenue/result where "
    "filed (e.g. Jio/Retail/O2C for Reliance — segment data is NOT in the screener CSVs). "
    "Only for companies whose XBRL has been refreshed with the fixed pipeline; falls back "
    "with instructions if absent. basis: consolidated|standalone.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"},
                    "basis": {"type": "string", "enum": ["consolidated", "standalone"],
                              "default": "consolidated"},
                    "quarters": {"type": "integer", "default": 8},
                    "include_segments": {"type": "boolean", "default": True}},
     "required": ["symbol"]},
)
async def xbrl_quarterly(args):
    def work():
        import json as _json
        from .config import STRUCTURED
        sym = args["symbol"].upper()
        p = STRUCTURED / f"{sym}_xbrl.json"
        if not p.exists():
            return (f"No refreshed XBRL for {sym}. Quarterly numbers are available via "
                    f"financial_statements(symbol, 'quarterly_results'). (To refresh XBRL: "
                    f"python scripts/02_nse_xbrl_quarterly.py --symbol {sym} then "
                    f"scripts/29_xbrl_to_md.py.)")
        d = _json.loads(p.read_text(encoding="utf-8"))
        want_cons = args.get("basis", "consolidated") == "consolidated"
        qs = [q for q in d["quarters"] if bool(q.get("consolidated")) == want_cons]
        qs.sort(key=lambda q: q.get("period_end", ""), reverse=True)
        qs = qs[: int(args.get("quarters", 8))]
        if not qs:
            return f"No {args.get('basis')} quarters in {sym} XBRL."
        headline = ["Revenue from operations", "Total income", "Total expenses",
                    "Profit before tax", "Net profit for period", "Basic EPS"]
        lines = [f"{sym} XBRL {args.get('basis', 'consolidated')} quarters "
                 f"(Rs crore, de-cumulated; * = derived by YTD subtraction):"]
        hdr = " | ".join(["item"] + [q["period_end"] for q in qs])
        lines += [hdr, " | ".join(["---"] * (len(qs) + 1))]
        keys = [k for k in qs[0].get("facts", {}) if k in headline] or \
               list(qs[0].get("facts", {}))[:8]
        for k in keys:
            row = [k] + [str(q.get("facts", {}).get(k, "")) for q in qs]
            lines.append(" | ".join(row))
        if args.get("include_segments", True):
            for q in qs[:4]:
                segs = q.get("segments") or q.get("facts", {}).get("segments")
                if segs:
                    lines.append(f"\nSegments {q['period_end']}: "
                                 + _js(segs, limit=1500))
        lines.append(f"\n(audited flags: "
                     + ", ".join(f"{q['period_end']}={q.get('audited', '?')}" for q in qs[:4])
                     + f"; source: NSE XBRL, fetched {d.get('fetched_at', '?')})")
        return "\n".join(lines)
    return _text(await asyncio.to_thread(work))


@tool(
    "xbrl_quarterly",
    "NSE XBRL quarterly filings (de-cumulated to true standalone quarters, verified vs "
    "screener): full P&L line items per quarter + business-SEGMENT revenue/result where "
    "filed (e.g. Jio/Retail/O2C for Reliance — segment data is NOT in the screener CSVs). "
    "Only for companies whose XBRL has been refreshed with the fixed pipeline; falls back "
    "with instructions if absent. basis: consolidated|standalone.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"},
                    "basis": {"type": "string", "enum": ["consolidated", "standalone"],
                              "default": "consolidated"},
                    "quarters": {"type": "integer", "default": 8},
                    "include_segments": {"type": "boolean", "default": True}},
     "required": ["symbol"]},
)
async def xbrl_quarterly(args):
    def work():
        import json as _json
        from .config import STRUCTURED
        sym = args["symbol"].upper()
        p = STRUCTURED / f"{sym}_xbrl.json"
        if not p.exists():
            return (f"No refreshed XBRL for {sym}. Quarterly numbers are available via "
                    f"financial_statements(symbol, 'quarterly_results'). (To refresh XBRL: "
                    f"python scripts/02_nse_xbrl_quarterly.py --symbol {sym} then "
                    f"scripts/29_xbrl_to_md.py.)")
        d = _json.loads(p.read_text(encoding="utf-8"))
        want_cons = args.get("basis", "consolidated") == "consolidated"
        qs = [q for q in d["quarters"] if bool(q.get("consolidated")) == want_cons]
        qs.sort(key=lambda q: q.get("period_end", ""), reverse=True)
        qs = qs[: int(args.get("quarters", 8))]
        if not qs:
            return f"No {args.get('basis')} quarters in {sym} XBRL."
        headline = ["Revenue from operations", "Total income", "Total expenses",
                    "Profit before tax", "Net profit for period", "Basic EPS"]
        lines = [f"{sym} XBRL {args.get('basis', 'consolidated')} quarters "
                 f"(Rs crore, de-cumulated; * = derived by YTD subtraction):"]
        hdr = " | ".join(["item"] + [q["period_end"] for q in qs])
        lines += [hdr, " | ".join(["---"] * (len(qs) + 1))]
        keys = [k for k in qs[0].get("facts", {}) if k in headline] or \
               list(qs[0].get("facts", {}))[:8]
        for k in keys:
            row = [k] + [str(q.get("facts", {}).get(k, "")) for q in qs]
            lines.append(" | ".join(row))
        if args.get("include_segments", True):
            for q in qs[:4]:
                segs = q.get("segments") or q.get("facts", {}).get("segments")
                if segs:
                    lines.append(f"\nSegments {q['period_end']}: "
                                 + _js(segs, limit=1500))
        lines.append(f"\n(audited flags: "
                     + ", ".join(f"{q['period_end']}={q.get('audited', '?')}" for q in qs[:4])
                     + f"; source: NSE XBRL, fetched {d.get('fetched_at', '?')})")
        return "\n".join(lines)
    return _text(await asyncio.to_thread(work))


@tool(
    "xbrl_quarterly",
    "NSE XBRL quarterly filings (de-cumulated to true standalone quarters, verified vs "
    "screener): full P&L line items per quarter + business-SEGMENT revenue/result where "
    "filed (e.g. Jio/Retail/O2C for Reliance — segment data is NOT in the screener CSVs). "
    "Only for companies whose XBRL has been refreshed with the fixed pipeline; falls back "
    "with instructions if absent. basis: consolidated|standalone.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"},
                    "basis": {"type": "string", "enum": ["consolidated", "standalone"],
                              "default": "consolidated"},
                    "quarters": {"type": "integer", "default": 8},
                    "include_segments": {"type": "boolean", "default": True}},
     "required": ["symbol"]},
)
async def xbrl_quarterly(args):
    def work():
        import json as _json
        from .config import STRUCTURED
        sym = args["symbol"].upper()
        p = STRUCTURED / f"{sym}_xbrl.json"
        if not p.exists():
            return (f"No refreshed XBRL for {sym}. Quarterly numbers are available via "
                    f"financial_statements(symbol, 'quarterly_results'). (To refresh XBRL: "
                    f"python scripts/02_nse_xbrl_quarterly.py --symbol {sym} then "
                    f"scripts/29_xbrl_to_md.py.)")
        d = _json.loads(p.read_text(encoding="utf-8"))
        want_cons = args.get("basis", "consolidated") == "consolidated"
        qs = [q for q in d["quarters"] if bool(q.get("consolidated")) == want_cons]
        qs.sort(key=lambda q: q.get("period_end", ""), reverse=True)
        qs = qs[: int(args.get("quarters", 8))]
        if not qs:
            return f"No {args.get('basis')} quarters in {sym} XBRL."
        headline = ["Revenue from operations", "Total income", "Total expenses",
                    "Profit before tax", "Net profit for period", "Basic EPS"]
        lines = [f"{sym} XBRL {args.get('basis', 'consolidated')} quarters "
                 f"(Rs crore, de-cumulated; * = derived by YTD subtraction):"]
        hdr = " | ".join(["item"] + [q["period_end"] for q in qs])
        lines += [hdr, " | ".join(["---"] * (len(qs) + 1))]
        keys = [k for k in qs[0].get("facts", {}) if k in headline] or \
               list(qs[0].get("facts", {}))[:8]
        for k in keys:
            row = [k] + [str(q.get("facts", {}).get(k, "")) for q in qs]
            lines.append(" | ".join(row))
        if args.get("include_segments", True):
            for q in qs[:4]:
                segs = q.get("segments") or q.get("facts", {}).get("segments")
                if segs:
                    lines.append(f"\nSegments {q['period_end']}: "
                                 + _js(segs, limit=1500))
        lines.append(f"\n(audited flags: "
                     + ", ".join(f"{q['period_end']}={q.get('audited', '?')}" for q in qs[:4])
                     + f"; source: NSE XBRL, fetched {d.get('fetched_at', '?')})")
        return "\n".join(lines)
    return _text(await asyncio.to_thread(work))


@tool(
    "xbrl_quarterly",
    "NSE XBRL quarterly filings (de-cumulated to true standalone quarters, verified vs "
    "screener): full P&L line items per quarter + business-SEGMENT revenue/result where "
    "filed (e.g. Jio/Retail/O2C for Reliance — segment data is NOT in the screener CSVs). "
    "Only for companies whose XBRL has been refreshed with the fixed pipeline; falls back "
    "with instructions if absent. basis: consolidated|standalone.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"},
                    "basis": {"type": "string", "enum": ["consolidated", "standalone"],
                              "default": "consolidated"},
                    "quarters": {"type": "integer", "default": 8},
                    "include_segments": {"type": "boolean", "default": True}},
     "required": ["symbol"]},
)
async def xbrl_quarterly(args):
    def work():
        import json as _json
        from .config import STRUCTURED
        sym = args["symbol"].upper()
        p = STRUCTURED / f"{sym}_xbrl.json"
        if not p.exists():
            return (f"No refreshed XBRL for {sym}. Quarterly numbers are available via "
                    f"financial_statements(symbol, 'quarterly_results'). (To refresh XBRL: "
                    f"python scripts/02_nse_xbrl_quarterly.py --symbol {sym} then "
                    f"scripts/29_xbrl_to_md.py.)")
        d = _json.loads(p.read_text(encoding="utf-8"))
        want_cons = args.get("basis", "consolidated") == "consolidated"
        qs = [q for q in d["quarters"] if bool(q.get("consolidated")) == want_cons]
        qs.sort(key=lambda q: q.get("period_end", ""), reverse=True)
        qs = qs[: int(args.get("quarters", 8))]
        if not qs:
            return f"No {args.get('basis')} quarters in {sym} XBRL."
        headline = ["Revenue from operations", "Total income", "Total expenses",
                    "Profit before tax", "Net profit for period", "Basic EPS"]
        lines = [f"{sym} XBRL {args.get('basis', 'consolidated')} quarters "
                 f"(Rs crore, de-cumulated; * = derived by YTD subtraction):"]
        hdr = " | ".join(["item"] + [q["period_end"] for q in qs])
        lines += [hdr, " | ".join(["---"] * (len(qs) + 1))]
        keys = [k for k in qs[0].get("facts", {}) if k in headline] or \
               list(qs[0].get("facts", {}))[:8]
        for k in keys:
            row = [k] + [str(q.get("facts", {}).get(k, "")) for q in qs]
            lines.append(" | ".join(row))
        if args.get("include_segments", True):
            for q in qs[:4]:
                segs = q.get("segments") or q.get("facts", {}).get("segments")
                if segs:
                    lines.append(f"\nSegments {q['period_end']}: "
                                 + _js(segs, limit=1500))
        lines.append(f"\n(audited flags: "
                     + ", ".join(f"{q['period_end']}={q.get('audited', '?')}" for q in qs[:4])
                     + f"; source: NSE XBRL, fetched {d.get('fetched_at', '?')})")
        return "\n".join(lines)
    return _text(await asyncio.to_thread(work))


@tool(
    "xbrl_quarterly",
    "NSE XBRL quarterly filings (de-cumulated to true standalone quarters, verified vs "
    "screener): full P&L line items per quarter + business-SEGMENT revenue/result where "
    "filed (e.g. Jio/Retail/O2C for Reliance — segment data is NOT in the screener CSVs). "
    "Only for companies whose XBRL has been refreshed with the fixed pipeline; falls back "
    "with instructions if absent. basis: consolidated|standalone.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"},
                    "basis": {"type": "string", "enum": ["consolidated", "standalone"],
                              "default": "consolidated"},
                    "quarters": {"type": "integer", "default": 8},
                    "include_segments": {"type": "boolean", "default": True}},
     "required": ["symbol"]},
)
async def xbrl_quarterly(args):
    def work():
        import json as _json
        from .config import STRUCTURED
        sym = args["symbol"].upper()
        p = STRUCTURED / f"{sym}_xbrl.json"
        if not p.exists():
            return (f"No refreshed XBRL for {sym}. Quarterly numbers are available via "
                    f"financial_statements(symbol, 'quarterly_results'). (To refresh XBRL: "
                    f"python scripts/02_nse_xbrl_quarterly.py --symbol {sym} then "
                    f"scripts/29_xbrl_to_md.py.)")
        d = _json.loads(p.read_text(encoding="utf-8"))
        want_cons = args.get("basis", "consolidated") == "consolidated"
        qs = [q for q in d["quarters"] if bool(q.get("consolidated")) == want_cons]
        qs.sort(key=lambda q: q.get("period_end", ""), reverse=True)
        qs = qs[: int(args.get("quarters", 8))]
        if not qs:
            return f"No {args.get('basis')} quarters in {sym} XBRL."
        headline = ["Revenue from operations", "Total income", "Total expenses",
                    "Profit before tax", "Net profit for period", "Basic EPS"]
        lines = [f"{sym} XBRL {args.get('basis', 'consolidated')} quarters "
                 f"(Rs crore, de-cumulated; * = derived by YTD subtraction):"]
        hdr = " | ".join(["item"] + [q["period_end"] for q in qs])
        lines += [hdr, " | ".join(["---"] * (len(qs) + 1))]
        keys = [k for k in qs[0].get("facts", {}) if k in headline] or \
               list(qs[0].get("facts", {}))[:8]
        for k in keys:
            row = [k] + [str(q.get("facts", {}).get(k, "")) for q in qs]
            lines.append(" | ".join(row))
        if args.get("include_segments", True):
            for q in qs[:4]:
                segs = q.get("segments") or q.get("facts", {}).get("segments")
                if segs:
                    lines.append(f"\nSegments {q['period_end']}: "
                                 + _js(segs, limit=1500))
        lines.append(f"\n(audited flags: "
                     + ", ".join(f"{q['period_end']}={q.get('audited', '?')}" for q in qs[:4])
                     + f"; source: NSE XBRL, fetched {d.get('fetched_at', '?')})")
        return "\n".join(lines)
    return _text(await asyncio.to_thread(work))


@tool(
    "xbrl_quarterly",
    "NSE XBRL quarterly filings (de-cumulated to true standalone quarters, verified vs "
    "screener): full P&L line items per quarter + business-SEGMENT revenue/result where "
    "filed (e.g. Jio/Retail/O2C for Reliance — segment data is NOT in the screener CSVs). "
    "Only for companies whose XBRL has been refreshed with the fixed pipeline; falls back "
    "with instructions if absent. basis: consolidated|standalone.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"},
                    "basis": {"type": "string", "enum": ["consolidated", "standalone"],
                              "default": "consolidated"},
                    "quarters": {"type": "integer", "default": 8},
                    "include_segments": {"type": "boolean", "default": True}},
     "required": ["symbol"]},
)
async def xbrl_quarterly(args):
    def work():
        import json as _json
        from .config import STRUCTURED
        sym = args["symbol"].upper()
        p = STRUCTURED / f"{sym}_xbrl.json"
        if not p.exists():
            return (f"No refreshed XBRL for {sym}. Quarterly numbers are available via "
                    f"financial_statements(symbol, 'quarterly_results'). (To refresh XBRL: "
                    f"python scripts/02_nse_xbrl_quarterly.py --symbol {sym} then "
                    f"scripts/29_xbrl_to_md.py.)")
        d = _json.loads(p.read_text(encoding="utf-8"))
        want_cons = args.get("basis", "consolidated") == "consolidated"
        qs = [q for q in d["quarters"] if bool(q.get("consolidated")) == want_cons]
        qs.sort(key=lambda q: q.get("period_end", ""), reverse=True)
        qs = qs[: int(args.get("quarters", 8))]
        if not qs:
            return f"No {args.get('basis')} quarters in {sym} XBRL."
        headline = ["Revenue from operations", "Total income", "Total expenses",
                    "Profit before tax", "Net profit for period", "Basic EPS"]
        lines = [f"{sym} XBRL {args.get('basis', 'consolidated')} quarters "
                 f"(Rs crore, de-cumulated; * = derived by YTD subtraction):"]
        hdr = " | ".join(["item"] + [q["period_end"] for q in qs])
        lines += [hdr, " | ".join(["---"] * (len(qs) + 1))]
        keys = [k for k in qs[0].get("facts", {}) if k in headline] or \
               list(qs[0].get("facts", {}))[:8]
        for k in keys:
            row = [k] + [str(q.get("facts", {}).get(k, "")) for q in qs]
            lines.append(" | ".join(row))
        if args.get("include_segments", True):
            for q in qs[:4]:
                segs = q.get("segments") or q.get("facts", {}).get("segments")
                if segs:
                    lines.append(f"\nSegments {q['period_end']}: "
                                 + _js(segs, limit=1500))
        lines.append(f"\n(audited flags: "
                     + ", ".join(f"{q['period_end']}={q.get('audited', '?')}" for q in qs[:4])
                     + f"; source: NSE XBRL, fetched {d.get('fetched_at', '?')})")
        return "\n".join(lines)
    return _text(await asyncio.to_thread(work))


@tool(
    "xbrl_quarterly",
    "NSE XBRL quarterly filings (de-cumulated to true standalone quarters, verified vs "
    "screener): full P&L line items per quarter + business-SEGMENT revenue/result where "
    "filed (e.g. Jio/Retail/O2C for Reliance — segment data is NOT in the screener CSVs). "
    "Only for companies whose XBRL has been refreshed with the fixed pipeline; falls back "
    "with instructions if absent. basis: consolidated|standalone.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"},
                    "basis": {"type": "string", "enum": ["consolidated", "standalone"],
                              "default": "consolidated"},
                    "quarters": {"type": "integer", "default": 8},
                    "include_segments": {"type": "boolean", "default": True}},
     "required": ["symbol"]},
)
async def xbrl_quarterly(args):
    def work():
        import json as _json
        from .config import STRUCTURED
        sym = args["symbol"].upper()
        p = STRUCTURED / f"{sym}_xbrl.json"
        if not p.exists():
            return (f"No refreshed XBRL for {sym}. Quarterly numbers are available via "
                    f"financial_statements(symbol, 'quarterly_results'). (To refresh XBRL: "
                    f"python scripts/02_nse_xbrl_quarterly.py --symbol {sym} then "
                    f"scripts/29_xbrl_to_md.py.)")
        d = _json.loads(p.read_text(encoding="utf-8"))
        want_cons = args.get("basis", "consolidated") == "consolidated"
        qs = [q for q in d["quarters"] if bool(q.get("consolidated")) == want_cons]
        qs.sort(key=lambda q: q.get("period_end", ""), reverse=True)
        qs = qs[: int(args.get("quarters", 8))]
        if not qs:
            return f"No {args.get('basis')} quarters in {sym} XBRL."
        headline = ["Revenue from operations", "Total income", "Total expenses",
                    "Profit before tax", "Net profit for period", "Basic EPS"]
        lines = [f"{sym} XBRL {args.get('basis', 'consolidated')} quarters "
                 f"(Rs crore, de-cumulated; * = derived by YTD subtraction):"]
        hdr = " | ".join(["item"] + [q["period_end"] for q in qs])
        lines += [hdr, " | ".join(["---"] * (len(qs) + 1))]
        keys = [k for k in qs[0].get("facts", {}) if k in headline] or \
               list(qs[0].get("facts", {}))[:8]
        for k in keys:
            row = [k] + [str(q.get("facts", {}).get(k, "")) for q in qs]
            lines.append(" | ".join(row))
        if args.get("include_segments", True):
            for q in qs[:4]:
                segs = q.get("segments") or q.get("facts", {}).get("segments")
                if segs:
                    lines.append(f"\nSegments {q['period_end']}: "
                                 + _js(segs, limit=1500))
        lines.append(f"\n(audited flags: "
                     + ", ".join(f"{q['period_end']}={q.get('audited', '?')}" for q in qs[:4])
                     + f"; source: NSE XBRL, fetched {d.get('fetched_at', '?')})")
        return "\n".join(lines)
    return _text(await asyncio.to_thread(work))


@tool(
    "xbrl_quarterly",
    "NSE XBRL quarterly filings (de-cumulated to true standalone quarters, verified vs "
    "screener): full P&L line items per quarter + business-SEGMENT revenue/result where "
    "filed (e.g. Jio/Retail/O2C for Reliance — segment data is NOT in the screener CSVs). "
    "Only for companies whose XBRL has been refreshed with the fixed pipeline; falls back "
    "with instructions if absent. basis: consolidated|standalone.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"},
                    "basis": {"type": "string", "enum": ["consolidated", "standalone"],
                              "default": "consolidated"},
                    "quarters": {"type": "integer", "default": 8},
                    "include_segments": {"type": "boolean", "default": True}},
     "required": ["symbol"]},
)
async def xbrl_quarterly(args):
    def work():
        import json as _json
        from .config import STRUCTURED
        sym = args["symbol"].upper()
        p = STRUCTURED / f"{sym}_xbrl.json"
        if not p.exists():
            return (f"No refreshed XBRL for {sym}. Quarterly numbers are available via "
                    f"financial_statements(symbol, 'quarterly_results'). (To refresh XBRL: "
                    f"python scripts/02_nse_xbrl_quarterly.py --symbol {sym} then "
                    f"scripts/29_xbrl_to_md.py.)")
        d = _json.loads(p.read_text(encoding="utf-8"))
        want_cons = args.get("basis", "consolidated") == "consolidated"
        qs = [q for q in d["quarters"] if bool(q.get("consolidated")) == want_cons]
        qs.sort(key=lambda q: q.get("period_end", ""), reverse=True)
        qs = qs[: int(args.get("quarters", 8))]
        if not qs:
            return f"No {args.get('basis')} quarters in {sym} XBRL."
        headline = ["Revenue from operations", "Total income", "Total expenses",
                    "Profit before tax", "Net profit for period", "Basic EPS"]
        lines = [f"{sym} XBRL {args.get('basis', 'consolidated')} quarters "
                 f"(Rs crore, de-cumulated; * = derived by YTD subtraction):"]
        hdr = " | ".join(["item"] + [q["period_end"] for q in qs])
        lines += [hdr, " | ".join(["---"] * (len(qs) + 1))]
        keys = [k for k in qs[0].get("facts", {}) if k in headline] or \
               list(qs[0].get("facts", {}))[:8]
        for k in keys:
            row = [k] + [str(q.get("facts", {}).get(k, "")) for q in qs]
            lines.append(" | ".join(row))
        if args.get("include_segments", True):
            for q in qs[:4]:
                segs = q.get("segments") or q.get("facts", {}).get("segments")
                if segs:
                    lines.append(f"\nSegments {q['period_end']}: "
                                 + _js(segs, limit=1500))
        lines.append(f"\n(audited flags: "
                     + ", ".join(f"{q['period_end']}={q.get('audited', '?')}" for q in qs[:4])
                     + f"; source: NSE XBRL, fetched {d.get('fetched_at', '?')})")
        return "\n".join(lines)
    return _text(await asyncio.to_thread(work))


@tool(
    "insider_trading",
    "PIT (insider trading) disclosures for a company, if available.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"}},
     "required": ["symbol"]},
)
async def insider_trading(args):
    d = await asyncio.to_thread(da.pit_disclosures, args["symbol"])
    if not d:
        return _err(f"no PIT data for {args['symbol']}")
    return _text(_js(d))


@tool(
    "financial_health",
    "Multi-year fundamental HEALTH check (~12y): computes sales/profit/EPS CAGR, margin "
    "trend, earnings quality (cumulative operating-cash-flow vs profit = accruals check), "
    "interest coverage, debt trend, genuine per-share dilution, ROCE trend and debtor-day "
    "creep — then emits directional flags (concern / watch / strength). Auto-adjusts for "
    "banks/NBFCs (skips cash-conversion & working-capital checks that don't apply to lenders). "
    "This is the go-to tool for 'find issues with the financials'.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"},
                    "basis": {"type": "string", "enum": ["consolidated", "standalone"],
                              "description": "default: consolidated, fallback standalone"}},
     "required": ["symbol"]},
)
async def financial_health(args):
    r = await asyncio.to_thread(fu.financial_trends, args["symbol"], args.get("basis"))
    if r.get("error"):
        return _err(f"{args['symbol']}: {r['error']}")
    return _text(_js(r))


@tool(
    "forensic_checks",
    "Forensic / governance red-flag checklist (from tijori): ~17 plain-English checks grouped "
    "by topic (Accounting Quality, Promoter & Governance, Balance Sheet & Debt, Growth & "
    "Returns, Valuation & Sentiment) plus an explicit promoter-pledge reading. Use alongside "
    "financial_health when scrutinising accounting quality or management integrity.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"}},
     "required": ["symbol"]},
)
async def forensic_checks(args):
    r = await asyncio.to_thread(fu.forensic_flags, args["symbol"])
    if r.get("error"):
        return _err(f"{args['symbol']}: {r['error']}")
    return _text(_js(r))


@tool(
    "shareholding_trends",
    "Quarter-over-quarter ownership shifts: promoter stake direction, FII/DII flows and "
    "promoter share pledge. Declining promoter stake and/or rising pledge is a classic "
    "governance warning. Complements the shareholding snapshot in financial_statements.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"}},
     "required": ["symbol"]},
)
async def shareholding_trends(args):
    r = await asyncio.to_thread(fu.shareholding_trends, args["symbol"])
    if r.get("error"):
        return _err(f"{args['symbol']}: {r['error']}")
    return _text(_js(r))


@tool(
    "capital_allocation",
    "How management deploys capital over time: operating cash flow vs capex vs free cash flow "
    "vs net debt vs dividend payout. Answers 'is growth self-funded or debt-fuelled, and does "
    "management return cash' — central to judging stewardship/trustworthiness. Returns a 2D "
    "per-year trend chart (PNG) alongside the data when at least 2 years of cash-flow history "
    "are available.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"}},
     "required": ["symbol"]},
)
async def capital_allocation(args):
    def work():
        r = fu.capital_allocation(args["symbol"])
        if r.get("error"):
            return r, None
        cfo, capex, fcf = r.get("cash_from_operations"), r.get("capex"), r.get("free_cash_flow")
        if cfo and capex and fcf and len(cfo) >= 2:
            from . import charts
            try:
                png = charts.capital_allocation_chart(args["symbol"], cfo, capex, fcf)
                return r, png
            except Exception:
                return r, None
        return r, None
    r, png = await asyncio.to_thread(work)
    if r.get("error"):
        return _err(f"{args['symbol']}: {r['error']}")
    if png:
        return _text_and_image(_js(r), png)
    return _text(_js(r))


@tool(
    "management_guidance",
    "MANAGEMENT CREDIBILITY tracker: retrieves past forward-looking / guidance statements from "
    "earnings calls (revenue/margin/capex/outlook) and pairs them with the ACTUAL quarterly "
    "results that followed, so you can judge whether management delivered on its promises. "
    "Surfaces both sides as evidence — you make the trustworthiness call. Requires the document "
    "index; returns actuals + a notice if no concalls are indexed.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"},
                    "lookback_periods": {"type": "integer", "default": 6}},
     "required": ["symbol"]},
)
async def management_guidance(args):
    def work():
        return fu.guidance_tracker(args["symbol"], int(args.get("lookback_periods", 6)))
    try:
        r = await asyncio.to_thread(work)
    except Exception as e:
        return _err(f"guidance tracking failed: {e}")
    return _text(_js(r))


@tool(
    "business_profile",
    "What the company actually DOES: revenue mix (product / geography / segment), operating "
    "KPIs (store counts, ARPU, GRM, capacity utilisation, etc.), market share and capex "
    "allocation by segment. The starting point for a business/moat/SWOT study.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"}},
     "required": ["symbol"]},
)
async def business_profile(args):
    r = await asyncio.to_thread(fu.business_profile, args["symbol"])
    if r.get("error"):
        return _err(f"{args['symbol']}: {r['error']}")
    return _text(_js(r))


@tool(
    "competitive_position",
    "Competitive standing: head-to-head benchmarking vs named peers on operating metrics, "
    "market-share trend, and the peer set. Use for 'how does X stack up against competitors' "
    "and as the competition input to a SWOT.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"}},
     "required": ["symbol"]},
)
async def competitive_position(args):
    r = await asyncio.to_thread(fu.competitive_position, args["symbol"])
    if r.get("error"):
        return _err(f"{args['symbol']}: {r['error']}")
    return _text(_js(r))


@tool(
    "supply_chain",
    "Known suppliers/vendors for a company (from tijori). For raw-material sourcing, "
    "procurement-cost drivers and named customers not in local data, the tool flags that "
    "web_research is needed — pair it with WebSearch when the user asks about the supply chain.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"}},
     "required": ["symbol"]},
)
async def supply_chain(args):
    r = await asyncio.to_thread(fu.supply_chain, args["symbol"])
    return _text(_js(r))


@tool(
    "price_analytics",
    "Trader-oriented price statistics beyond basic momentum: 52-week range & position, "
    "distance from all-time high, max drawdown & current drawdown, annualised volatility, "
    "volume trend vs 200-day average, 50/200-DMA crossover (golden/death cross), and relative "
    "strength vs NIFTY over 3m/1y. Performance metrics use split/bonus-adjusted prices.",
    {"type": "object",
     "properties": {"symbol": {"type": "string"},
                    "benchmark": {"type": "string", "default": "nifty50",
                                  "description": "index for relative strength (nifty50, it, bank, ...)"}},
     "required": ["symbol"]},
)
async def price_analytics(args):
    r = await asyncio.to_thread(da.price_analytics, args["symbol"], args.get("benchmark", "nifty50"))
    if r is None:
        return _err(f"no price history for {args['symbol']}")
    return _text(_js(r))


@tool(
    "portfolio_risk",
    "Portfolio-level risk from ACTUAL historical daily returns — not single-stock "
    "volatility/beta treated as a portfolio proxy. Takes named holdings + weights (any "
    "positive numbers; renormalized to sum to 1, flagged if so), pulls each symbol's real "
    "daily price history, and computes: the correlation matrix between holdings, portfolio "
    "annualized volatility (Markowitz w'*cov*w, cross-checked two ways), and — via the "
    "`empyrical` risk-stats library (empyrical-reloaded, the maintained fork of Quantopian's "
    "widely-used open-source package) applied to the actual weighted portfolio-return series "
    "— CAGR/max drawdown/Calmar ratio, Sharpe and Sortino ratios (risk-free rate from "
    "macro_data's 10y G-Sec unless supplied), HISTORICAL (empirical-percentile, non-"
    "parametric) VaR and CVaR at 95%/99% — not a normal-distribution z-score approximation, "
    "and portfolio beta vs a benchmark index cross-checked against the weighted average of "
    "individual betas. Also reports a Herfindahl-index-based 'effective number of positions' "
    "(how concentrated the weights actually are, distinct from the raw holding count) — not "
    "an empyrical metric, computed directly. Use whenever the user gives actual holdings/"
    "weights and asks 'how risky is my portfolio' — this is the real computation, not the "
    "individual-stock screen_stocks columns treated as a stand-in for portfolio risk.",
    {"type": "object",
     "properties": {
         "holdings": {"type": "array",
                      "items": {"type": "object",
                                "properties": {"symbol": {"type": "string"},
                                               "weight": {"type": "number"}},
                                "required": ["symbol", "weight"]},
                      "description": "e.g. [{'symbol':'TCS','weight':0.3}, "
                                     "{'symbol':'HDFCBANK','weight':0.7}] — weights need not "
                                     "sum to 1, they'll be renormalized"},
         "benchmark": {"type": "string", "default": "nifty50"},
         "years": {"type": "number", "default": 3.0,
                   "description": "lookback window in years if start/end not given"},
         "start": {"type": "string", "description": "YYYY-MM-DD"},
         "end": {"type": "string", "description": "YYYY-MM-DD"},
         "risk_free_pct": {"type": "number",
                           "description": "annual %, overrides the macro_data 10y G-Sec default"},
     },
     "required": ["holdings"]},
)
async def portfolio_risk(args):
    from . import portfolio_risk as pr
    def work():
        return pr.compute(args["holdings"], benchmark=args.get("benchmark", "nifty50"),
                          years=float(args.get("years", 3.0)),
                          start=args.get("start"), end=args.get("end"),
                          risk_free_pct=args.get("risk_free_pct"))
    try:
        r = await asyncio.to_thread(work)
    except ValueError as e:
        return _err(str(e))
    return _text(_js(r))


@tool(
    "sector_analysis",
    "Aggregate view of a sector or industry: company count, total & median market cap, median "
    "valuation, and the leaders by market cap with headline metrics. Pass sector OR industry "
    "text (partial match, e.g. 'Banks', 'IT - Software', 'Pharmaceuticals'). Omit both to list "
    "available sectors. Use for 'analyse the X sector' instead of looping companies.",
    {"type": "object",
     "properties": {"sector": {"type": "string"},
                    "industry": {"type": "string"},
                    "top_n": {"type": "integer", "default": 25}},
     "required": []},
)
async def sector_analysis(args):
    def work():
        return fu.sector_analysis(args.get("sector"), args.get("industry"),
                                  int(args.get("top_n", 25)))
    r = await asyncio.to_thread(work)
    return _text(_js(r))


@tool(
    "refresh_company_data",
    "Re-scrape ONE company's data from its live sources and promote the result into "
    "data/companies/<SYM>/ — the folder every other tool actually reads. Use when asked to "
    "'refresh'/'update'/'get current data for' a company: this data lake is a point-in-time "
    "snapshot, typically weeks old for screener/tijori/prices (xbrl is usually already fresh). "
    "sources (default all four): 'screener' (backs financial_statements/financial_health/ "
    "forensic_checks), 'prices' (price_history/technicals_momentum), 'xbrl' (xbrl_quarterly), "
    "'tijori' (backs forensic_checks/business_profile/capital_allocation/supply_chain — SKIPPED "
    "with an explanatory message unless TIJORI_SESSION_ID is set in the project's .env, since "
    "Tijori needs a logged-in session cookie, not an API key). Each source is a real network "
    "scrape (~10-90s), run sequentially — this is slow compared to the read-only tools, so only "
    "call it when the user actually wants fresher data, not on every question.",
    {"type": "object",
     "properties": {
         "symbol": {"type": "string"},
         "sources": {"type": "array",
                     "items": {"type": "string",
                               "enum": ["screener", "tijori", "prices", "xbrl"]},
                     "description": "which sources to refresh; default: all four"},
     },
     "required": ["symbol"]},
)
async def refresh_company_data(args):
    from .config import ROOT, SCRIPTS, STRUCTURED, COMPANIES

    sym = args["symbol"].upper()
    wanted = args.get("sources") or ["screener", "tijori", "prices", "xbrl"]
    cdir = COMPANIES / sym
    cdir.mkdir(parents=True, exist_ok=True)
    results = []

    def mtime(p):
        try:
            return p.stat().st_mtime
        except FileNotFoundError:
            return None

    async def run_script(script_name, extra_args=(), timeout=150):
        cmd = [sys.executable, str(SCRIPTS / script_name), "--symbol", sym, *extra_args]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=str(ROOT),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return proc.returncode == 0, out.decode("utf-8", errors="replace")[-1500:]
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return False, f"timed out after {timeout}s"
        except Exception as e:
            return False, f"failed to launch: {e}"

    if "screener" in wanted:
        before = mtime(cdir / "screener.json")
        ok, log = await run_script("04_screener_scraper.py")
        staged = STRUCTURED / f"{sym}_screener.json"
        if ok and staged.exists():
            shutil.copy2(staged, cdir / "screener.json")
            results.append({"source": "screener", "ok": True,
                            "before_mtime": before, "after_mtime": mtime(cdir / "screener.json")})
        else:
            results.append({"source": "screener", "ok": False, "log_tail": log})

    if "xbrl" in wanted:
        p = STRUCTURED / f"{sym}_xbrl.json"
        before = mtime(p)
        ok, log = await run_script("02_nse_xbrl_quarterly.py")
        after = mtime(p)
        entry = {"source": "xbrl", "ok": ok and after is not None and after != before,
                 "before_mtime": before, "after_mtime": after}
        if not entry["ok"]:
            entry["log_tail"] = log
        results.append(entry)

    if "prices" in wanted:
        pfile = cdir / "prices" / f"{sym}.parquet"
        before = mtime(pfile)
        ok, log = await run_script("03_yfinance_prices.py")
        staged_p = ROOT / "data" / "prices" / f"{sym}.parquet"
        staged_t = STRUCTURED / f"{sym}_technicals.json"
        moved = []
        if ok and staged_p.exists():
            (cdir / "prices").mkdir(parents=True, exist_ok=True)
            shutil.copy2(staged_p, pfile)
            moved.append("prices")
        if ok and staged_t.exists():
            shutil.copy2(staged_t, cdir / "technicals.json")
            moved.append("technicals")
        entry = {"source": "prices", "ok": bool(moved), "moved": moved,
                 "before_mtime": before, "after_mtime": mtime(pfile)}
        if not moved:
            entry["log_tail"] = log
        results.append(entry)

    if "tijori" in wanted:
        env_path = ROOT / ".env"
        sid = os.environ.get("TIJORI_SESSION_ID", "")
        if not sid and env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("TIJORI_SESSION_ID="):
                    sid = line.split("=", 1)[1].strip()
        if not sid:
            results.append({
                "source": "tijori", "ok": False,
                "log_tail": ("skipped: TIJORI_SESSION_ID not set. Log into tijorifinance.com in "
                             "a browser, copy the 'sessionid' cookie value from devtools, and "
                             f"add a line TIJORI_SESSION_ID=<value> to {env_path} to enable this "
                             "source."),
            })
        else:
            before = mtime(cdir / "tijori.json")
            ok, log = await run_script("09_tijori_scraper.py")
            tijori_dir = ROOT / "data" / "tijori"
            staged = None
            if ok and tijori_dir.exists():
                cands = sorted(tijori_dir.glob("*.json"),
                               key=lambda p: p.stat().st_mtime, reverse=True)
                staged = cands[0] if cands else None
            if ok and staged:
                shutil.copy2(staged, cdir / "tijori.json")
                results.append({"source": "tijori", "ok": True,
                                "before_mtime": before, "after_mtime": mtime(cdir / "tijori.json")})
            else:
                results.append({"source": "tijori", "ok": False, "log_tail": log})

    return _text(_js({
        "symbol": sym,
        "results": results,
        "note": ("mtimes are unix epoch seconds. ok=true means the file the OTHER tools read "
                 "was actually updated in data/companies/<SYM>/, not just that the scraper ran. "
                 "Re-call the relevant tool (financial_statements, forensic_checks, etc.) now to "
                 "see the refreshed numbers — nothing is cached in-process."),
    }))


ALL_TOOLS = [
    resolve_company, company_overview, financial_statements, valuation_summary,
    technicals_momentum, price_history, price_analytics, screen_stocks, screen_by_year,
    screen_consistency, sector_analysis, search_documents, topic_timeline, peers_and_index,
    macro_data, index_data, insider_trading, xbrl_quarterly, refresh_company_data,
    # analytical / research layer
    financial_health, forensic_checks, shareholding_trends, capital_allocation,
    management_guidance, business_profile, competitive_position, supply_chain,
    portfolio_risk,
]
