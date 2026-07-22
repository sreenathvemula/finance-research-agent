"""Stock screening engine.

Builds one wide metrics table by merging:
  reference/entities.parquet                  (identity, sector, mcap, pe)
  companies/{SYM}/technicals.json             (momentum: returns, RSI, DMA, MACD)
  companies/{SYM}/valuation/valuation.json    (multiples: pb, roe, roce, D/E, ...)
and caches it to data/index/screener_metrics.parquet (rebuilt when >24h old).

Screening = objective filtering on user criteria. No recommendations.
"""
from __future__ import annotations

import json
import re
import time

import numpy as np
import pandas as pd

from .config import (COMPANIES, HIST_FUNDAMENTALS_CACHE, HIST_PRICE_RETURNS_CACHE,
                     SCREENER_CACHE)
from .data_access import entities, index_membership, prices, screener_data
from .fundamentals import _get, is_financial, num, series_map

_XBRL_FACE_VALUE_RE = re.compile(r"\|\s*Face Value Of Equity Share Capital\s*\|([^\n]*)\|")
_XBRL_PAID_UP_RE = re.compile(r"\|\s*Paid Up Value Of Equity Share Capital\s*\|([^\n]*)\|")


_TIJORI_RATIO_MAP = {   # tijori metric name -> our column (new metrics, no other source)
    "Current Ratio": "current_ratio",
    "Quick Ratio": "quick_ratio",
    "Interest Coverage Ratio": "interest_coverage_x",
    "ROIC (%)": "roic_pct",
    "Return on Assets (%)": "roa_pct",
    "Gross Margin (%)": "gross_margin_pct",
    "Net Profit Margin (%)": "net_margin_pct",
    "Asset Turnover ratio": "asset_turnover",
    "Fixed Asset Turnover": "fixed_asset_turnover",
    "Equity Multiplier": "equity_multiplier",
    "Free Cash Flow/Sales (%)": "fcf_margin_pct",
    "Total Debt (Crs)": "total_debt_cr",
    "Free Cash Flow(est) (Crs)": "fcf_cr",
}
_TIJORI_FALLBACK_MAP = {  # tijori metric name -> our column, ONLY used when the
    # primary source (screener.json valuation.json) is missing — tijori computes
    # these with a visibly different methodology (spot-checked on RELIANCE: its
    # ROCE/ROE/PE run ~30-40% off our primary source), so it fills gaps rather
    # than silently overriding/blending with the primary figure most rows use.
    "ROE (%)": "roe", "ROCE (%)": "roce", "P/E": "pe", "Price to Book": "pb",
    "EV/EBITDA": "ev_ebitda", "Dividend Yield (%)": "div_yield",
    "Debt to Equity Ratio": "debt_equity",
    "Inventory Days": "inventory_days", "Days Receivable": "debtor_days",
    "Days Payable": "payable_days", "Cash Conversion Cycle": "cash_conversion_cycle",
}


def _tijori_financial_snapshot(sym: str) -> dict:
    """One read of tijori.json -> {shares_crs, pledge_pct, <new ratio columns>,
    <fallback columns>}. Consolidated into a single file open (previously shares
    and pledge were separate reads) since most of these live in the same JSON."""
    out: dict = {}
    p = COMPANIES / sym / "tijori.json"
    if not p.exists():
        return out
    try:
        tj = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return out

    st = tj.get("shareholding_trend") or {}
    pledge_key = next((k for k in st if "pledge" in k.lower()), None)
    if pledge_key and st[pledge_key]:
        latest = next((v for _, v in reversed(st[pledge_key]) if v is not None), None)
        if latest is not None:
            out["promoter_pledge_pct"] = float(latest)

    fin = tj.get("financials") or {}
    for basis in ("standalone", "consolidated"):
        pl = ((fin.get(basis) or {}).get("profit_loss") or {}).get("metrics", {})
        shares = pl.get("Number of shares(Crs)")
        if shares:
            vals = [v for v in shares.values() if v]
            if vals and "shares_crs" not in out:
                out["shares_crs"] = float(vals[-1])
        ratios = ((fin.get(basis) or {}).get("ratios") or {}).get("metrics", {})
        for name, col in {**_TIJORI_RATIO_MAP, **_TIJORI_FALLBACK_MAP}.items():
            if col in out:
                continue
            series = ratios.get(name)
            if series:
                vals = [v for v in series.values() if v is not None]
                if vals:
                    out[col] = float(vals[-1])
        if out.get("shares_crs") and all(c in out for c in _TIJORI_RATIO_MAP.values()):
            break  # standalone already gave us everything; skip consolidated pass
    return out


_TIJORI_RATIO_MAP = {   # tijori metric name -> our column (new metrics, no other source)
    "Current Ratio": "current_ratio",
    "Quick Ratio": "quick_ratio",
    "Interest Coverage Ratio": "interest_coverage_x",
    "ROIC (%)": "roic_pct",
    "Return on Assets (%)": "roa_pct",
    "Gross Margin (%)": "gross_margin_pct",
    "Net Profit Margin (%)": "net_margin_pct",
    "Asset Turnover ratio": "asset_turnover",
    "Fixed Asset Turnover": "fixed_asset_turnover",
    "Equity Multiplier": "equity_multiplier",
    "Free Cash Flow/Sales (%)": "fcf_margin_pct",
    "Total Debt (Crs)": "total_debt_cr",
    "Free Cash Flow(est) (Crs)": "fcf_cr",
}
_TIJORI_FALLBACK_MAP = {  # tijori metric name -> our column, ONLY used when the
    # primary source (screener.json valuation.json) is missing — tijori computes
    # these with a visibly different methodology (spot-checked on RELIANCE: its
    # ROCE/ROE/PE run ~30-40% off our primary source), so it fills gaps rather
    # than silently overriding/blending with the primary figure most rows use.
    "ROE (%)": "roe", "ROCE (%)": "roce", "P/E": "pe", "Price to Book": "pb",
    "EV/EBITDA": "ev_ebitda", "Dividend Yield (%)": "div_yield",
    "Debt to Equity Ratio": "debt_equity",
    "Inventory Days": "inventory_days", "Days Receivable": "debtor_days",
    "Days Payable": "payable_days", "Cash Conversion Cycle": "cash_conversion_cycle",
}


def _tijori_financial_snapshot(sym: str) -> dict:
    """One read of tijori.json -> {shares_crs, pledge_pct, <new ratio columns>,
    <fallback columns>}. Consolidated into a single file open (previously shares
    and pledge were separate reads) since most of these live in the same JSON."""
    out: dict = {}
    p = COMPANIES / sym / "tijori.json"
    if not p.exists():
        return out
    try:
        tj = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return out

    st = tj.get("shareholding_trend") or {}
    pledge_key = next((k for k in st if "pledge" in k.lower()), None)
    if pledge_key and st[pledge_key]:
        latest = next((v for _, v in reversed(st[pledge_key]) if v is not None), None)
        if latest is not None:
            out["promoter_pledge_pct"] = float(latest)

    fin = tj.get("financials") or {}
    for basis in ("standalone", "consolidated"):
        pl = ((fin.get(basis) or {}).get("profit_loss") or {}).get("metrics", {})
        shares = pl.get("Number of shares(Crs)")
        if shares:
            vals = [v for v in shares.values() if v]
            if vals and "shares_crs" not in out:
                out["shares_crs"] = float(vals[-1])
        ratios = ((fin.get(basis) or {}).get("ratios") or {}).get("metrics", {})
        for name, col in {**_TIJORI_RATIO_MAP, **_TIJORI_FALLBACK_MAP}.items():
            if col in out:
                continue
            series = ratios.get(name)
            if series:
                vals = [v for v in series.values() if v is not None]
                if vals:
                    out[col] = float(vals[-1])
        if out.get("shares_crs") and all(c in out for c in _TIJORI_RATIO_MAP.values()):
            break  # standalone already gave us everything; skip consolidated pass
    return out


def _xbrl_face_value_and_paidup(sym: str) -> tuple[float | None, float | None]:
    """(face_value_rs, paid_up_equity_capital_cr) from the XBRL markdown, using
    the latest (rightmost) non-blank cell in each row. Both are stable, low-noise
    XBRL tags (unlike revenue/profit, which the project's own audit found mixed
    quarterly/cumulative — see config.py's DOC_TYPES comment), so this is safe to
    lean on even though XBRL is excluded elsewhere."""
    for basis in ("standalone", "consolidated"):
        p = COMPANIES / sym / "xbrl" / f"{basis}.md"
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        fv_m = _XBRL_FACE_VALUE_RE.search(text)
        pu_m = _XBRL_PAID_UP_RE.search(text)
        if not (fv_m and pu_m):
            continue
        fv_cells = [c.strip() for c in fv_m.group(1).split("|") if c.strip()]
        pu_cells = [c.strip().replace(",", "") for c in pu_m.group(1).split("|") if c.strip()]
        if fv_cells and pu_cells:
            try:
                fv, pu = float(fv_cells[-1]), float(pu_cells[-1])
                if fv > 0 and pu > 0:
                    return fv, pu
            except ValueError:
                continue
    return None, None

TECH_MAP = {
    "Current price": "price_t",
    "DMA 50": "dma50", "DMA 200": "dma200",
    "RSI": "rsi", "MACD": "macd", "MACD Signal": "macd_signal",
    "Return over 1day": "ret_1d", "Return over 1week": "ret_1w",
    "Return over 1month": "ret_1m", "Return over 3months": "ret_3m",
    "Return over 6months": "ret_6m", "Return over 1year": "ret_1y",
    "Return over 3years": "ret_3y",
    "Down from 52w high": "down_from_52w_high",
}
VAL_MAP = {
    "pb": "pb", "ps": "ps", "ev_ebitda": "ev_ebitda", "div_yield": "div_yield",
    "roe": "roe", "roce": "roce", "debt_equity": "debt_equity", "net_debt_cr": "net_debt_cr",
}

FILTERABLE = [
    # valuation
    "market_cap_cr", "price", "pe", "pb", "ps", "ev_ebitda", "ev_sales", "peg",
    "div_yield", "earnings_yield_pct", "fcf_yield_pct", "book_value_per_share",
    # profitability / returns
    "roe", "roce", "roic_pct", "roa_pct", "gross_margin_pct", "opm_pct", "net_margin_pct",
    # growth
    "sales_growth_pct", "eps_growth_pct", "eps_cagr_3y_pct",
    # leverage / liquidity / efficiency
    "debt_equity", "interest_coverage_x", "current_ratio", "quick_ratio",
    "asset_turnover", "fixed_asset_turnover", "equity_multiplier", "fcf_margin_pct",
    "total_debt_cr", "debtor_days", "inventory_days", "payable_days", "cash_conversion_cycle",
    # governance / ownership
    "promoter_stake_pct", "promoter_pledge_pct", "fii_stake_pct", "dii_stake_pct",
    "public_stake_pct", "free_float_pct",
    # price / risk / momentum
    "ret_1d", "ret_1w", "ret_1m", "ret_3m", "ret_6m", "ret_1y", "ret_3y", "rsi",
    "down_from_52w_high", "annualized_volatility_pct", "max_drawdown_pct",
    "avg_daily_value_cr", "beta",
]


def build_metrics(force: bool = False) -> pd.DataFrame:
    if SCREENER_CACHE.exists() and not force:
        age_h = (time.time() - SCREENER_CACHE.stat().st_mtime) / 3600
        if age_h < 24:
            return pd.read_parquet(SCREENER_CACHE)

    ent = entities().copy()
    rows = []
    for sym in ent["symbol"]:
        rec: dict = {"symbol": sym}
        tp = COMPANIES / sym / "technicals.json"
        if tp.exists():
            try:
                t = json.loads(tp.read_text(encoding="utf-8")).get("technicals", {})
                for k, col in TECH_MAP.items():
                    if k in t:
                        rec[col] = t[k]
            except Exception:
                pass
        pp = COMPANIES / sym / "profile.json"
        if pp.exists():
            try:
                rec["about"] = json.loads(pp.read_text(encoding="utf-8")).get("about", "")
            except Exception:
                pass
        pp = COMPANIES / sym / "profile.json"
        if pp.exists():
            try:
                rec["about"] = json.loads(pp.read_text(encoding="utf-8")).get("about", "")
            except Exception:
                pass
        pp = COMPANIES / sym / "profile.json"
        if pp.exists():
            try:
                rec["about"] = json.loads(pp.read_text(encoding="utf-8")).get("about", "")
            except Exception:
                pass
        pp = COMPANIES / sym / "profile.json"
        if pp.exists():
            try:
                rec["about"] = json.loads(pp.read_text(encoding="utf-8")).get("about", "")
            except Exception:
                pass
        pp = COMPANIES / sym / "profile.json"
        if pp.exists():
            try:
                rec["about"] = json.loads(pp.read_text(encoding="utf-8")).get("about", "")
            except Exception:
                pass
        vp = COMPANIES / sym / "valuation" / "valuation.json"
        if vp.exists():
            try:
                mult = json.loads(vp.read_text(encoding="utf-8")).get("multiples", {})
                for k, col in VAL_MAP.items():
                    if mult.get(k) is not None:
                        rec[col] = mult[k]
            except Exception:
                pass
        rows.append(rec)

    met = pd.DataFrame(rows)
    df = ent.merge(met, on="symbol", how="left")
    for c in ("market_cap_cr", "price", "pe"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["above_dma50"] = df["price_t"] > df["dma50"]
    df["above_dma200"] = df["price_t"] > df["dma200"]
    df["macd_bullish"] = df["macd"] > df["macd_signal"]

    # market cap fallback: screener.in has no market_cap_cr for ~800 companies
    # (usually obscure/thinly-traded BSE names it doesn't fully cover). Simple
    # fix: market cap = price x shares outstanding. Try tijori's own
    # "Number of shares(Crs)" first (one direct number, one multiply, covers
    # more of the gap); fall back to XBRL's face-value/paid-up-capital (also
    # just a multiply+divide) when tijori doesn't have it either. price_t is
    # technicals.json's own independently-computed price (populated even when
    # screener.json is fully blank for these names — verified on real cases).
    df["market_cap_computed_from"] = None   # "tijori" | "xbrl" | None
    need_mcap = df[df["market_cap_cr"].isna() & df["price_t"].notna()]
    for i, sym in need_mcap["symbol"].items():
        price_t = float(df.at[i, "price_t"])
        shares_crs = _tijori_shares_crs(sym)
        if shares_crs:
            df.at[i, "market_cap_cr"] = round(price_t * shares_crs, 1)
            df.at[i, "market_cap_computed_from"] = "tijori"
            continue
        fv, pu = _xbrl_face_value_and_paidup(sym)
        if fv and pu:
            df.at[i, "market_cap_cr"] = round(price_t * pu / fv, 1)
            df.at[i, "market_cap_computed_from"] = "xbrl"

    # market cap fallback: screener.in has no market_cap_cr for ~800 companies
    # (usually obscure/thinly-traded BSE names it doesn't fully cover). Simple
    # fix: market cap = price x shares outstanding. Try tijori's own
    # "Number of shares(Crs)" first (one direct number, one multiply, covers
    # more of the gap); fall back to XBRL's face-value/paid-up-capital (also
    # just a multiply+divide) when tijori doesn't have it either. price_t is
    # technicals.json's own independently-computed price (populated even when
    # screener.json is fully blank for these names — verified on real cases).
    df["market_cap_computed_from"] = None   # "tijori" | "xbrl" | None
    need_mcap = df[df["market_cap_cr"].isna() & df["price_t"].notna()]
    for i, sym in need_mcap["symbol"].items():
        price_t = float(df.at[i, "price_t"])
        shares_crs = _tijori_shares_crs(sym)
        if shares_crs:
            df.at[i, "market_cap_cr"] = round(price_t * shares_crs, 1)
            df.at[i, "market_cap_computed_from"] = "tijori"
            continue
        fv, pu = _xbrl_face_value_and_paidup(sym)
        if fv and pu:
            df.at[i, "market_cap_cr"] = round(price_t * pu / fv, 1)
            df.at[i, "market_cap_computed_from"] = "xbrl"

    memb = index_membership()
    df["indices"] = df["symbol"].map(lambda s: "|".join(memb.get(s, [])))

    SCREENER_CACHE.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(SCREENER_CACHE, index=False)
    return df


def _find_near_misses(pre_numeric: pd.DataFrame, numeric_filters: list[tuple[str, str, float]],
                      tolerance_pct: float) -> list[dict]:
    """Companies that failed EXACTLY ONE of the numeric filters, by no more than
    tolerance_pct of that filter's threshold, while passing every other one —
    the 'left out by a small margin' set, so a strict screen's cost is visible."""
    pass_mask = {}
    miss_margin = {}   # % by which the filter was missed, only where it WAS missed
    for col, kind, v in numeric_filters:
        vals = pd.to_numeric(pre_numeric[col], errors="coerce")
        # missing data PASSES (matches screen()'s rule — absence isn't failure),
        # so it can never be "the one filter that was missed" here either.
        if kind == "min":
            pass_mask[col] = vals.isna() | (vals >= float(v))
            denom = abs(v) if v else 1.0
            miss_margin[col] = ((float(v) - vals) / denom * 100).clip(lower=0)
        else:
            pass_mask[col] = vals.isna() | (vals <= float(v))
            denom = abs(v) if v else 1.0
            miss_margin[col] = ((vals - float(v)) / denom * 100).clip(lower=0)

    pass_df = pd.DataFrame(pass_mask)
    n_failed = (~pass_df).sum(axis=1)
    exactly_one = n_failed == 1
    if not exactly_one.any():
        return []

    out = []
    idx = pre_numeric.index[exactly_one]
    for i in idx:
        failed_cols = [c for c in pass_mask if not pass_mask[c].loc[i]]
        if len(failed_cols) != 1:
            continue
        col = failed_cols[0]
        margin = float(miss_margin[col].loc[i])
        # NaN margin means the value itself is missing, not "close but short" —
        # that's not a near miss, it's unknown data; exclude it rather than
        # report a meaningless "missed by nan%".
        if pd.isna(margin) or margin > tolerance_pct:
            continue
        kind = next(k for c, k, v in numeric_filters if c == col)
        thresh = next(v for c, k, v in numeric_filters if c == col)
        out.append({
            "symbol": pre_numeric.loc[i, "symbol"],
            "company_name": pre_numeric.loc[i].get("company_name"),
            "missed_filter": f"{col} {'>=' if kind == 'min' else '<='} {thresh}",
            "actual_value": round(float(pd.to_numeric(pre_numeric.loc[i, col], errors="coerce")), 2),
            "missed_by_pct": round(margin, 1),
        })
    out.sort(key=lambda r: r["missed_by_pct"])
    return out[:15]


def _find_near_misses(pre_numeric: pd.DataFrame, numeric_filters: list[tuple[str, str, float]],
                      tolerance_pct: float) -> list[dict]:
    """Companies that failed EXACTLY ONE of the numeric filters, by no more than
    tolerance_pct of that filter's threshold, while passing every other one —
    the 'left out by a small margin' set, so a strict screen's cost is visible."""
    pass_mask = {}
    miss_margin = {}   # % by which the filter was missed, only where it WAS missed
    for col, kind, v in numeric_filters:
        vals = pd.to_numeric(pre_numeric[col], errors="coerce")
        # missing data PASSES (matches screen()'s rule — absence isn't failure),
        # so it can never be "the one filter that was missed" here either.
        if kind == "min":
            pass_mask[col] = vals.isna() | (vals >= float(v))
            denom = abs(v) if v else 1.0
            miss_margin[col] = ((float(v) - vals) / denom * 100).clip(lower=0)
        else:
            pass_mask[col] = vals.isna() | (vals <= float(v))
            denom = abs(v) if v else 1.0
            miss_margin[col] = ((vals - float(v)) / denom * 100).clip(lower=0)

    pass_df = pd.DataFrame(pass_mask)
    n_failed = (~pass_df).sum(axis=1)
    exactly_one = n_failed == 1
    if not exactly_one.any():
        return []

    out = []
    idx = pre_numeric.index[exactly_one]
    for i in idx:
        failed_cols = [c for c in pass_mask if not pass_mask[c].loc[i]]
        if len(failed_cols) != 1:
            continue
        col = failed_cols[0]
        margin = float(miss_margin[col].loc[i])
        # NaN margin means the value itself is missing, not "close but short" —
        # that's not a near miss, it's unknown data; exclude it rather than
        # report a meaningless "missed by nan%".
        if pd.isna(margin) or margin > tolerance_pct:
            continue
        kind = next(k for c, k, v in numeric_filters if c == col)
        thresh = next(v for c, k, v in numeric_filters if c == col)
        out.append({
            "symbol": pre_numeric.loc[i, "symbol"],
            "company_name": pre_numeric.loc[i].get("company_name"),
            "missed_filter": f"{col} {'>=' if kind == 'min' else '<='} {thresh}",
            "actual_value": round(float(pd.to_numeric(pre_numeric.loc[i, col], errors="coerce")), 2),
            "missed_by_pct": round(margin, 1),
        })
    out.sort(key=lambda r: r["missed_by_pct"])
    return out[:15]


def screen(
    sector: str | None = None,
    industry: str | None = None,
    index: str | None = None,
    min_filters: dict | None = None,     # {"roe": 15, "ret_1y": 0, ...}
    max_filters: dict | None = None,     # {"pe": 25, "debt_equity": 0.5, ...}
    above_dma50: bool | None = None,
    above_dma200: bool | None = None,
    macd_bullish: bool | None = None,
    exclude_categories: list[str] | None = None,
    sort_by: str = "market_cap_cr",
    ascending: bool = False,
    limit: int = 25,
    near_miss_tolerance_pct: float = 15.0,   # 0 disables near-miss reporting
    force_rebuild: bool = False,
) -> tuple[pd.DataFrame, int, dict]:
    """Return (result_df, matched_count, exclusion_summary).

    exclusion_summary: {"applied": [...], "unknown": [...], "excluded_count": N,
    "by_category": {cat: count}, "funnel": [...], "near_misses": [...]}.
    "funnel" shows the remaining count after each filter is applied, in order —
    "how many did each criterion drop." "near_misses" lists companies that
    failed EXACTLY ONE numeric filter, by no more than near_miss_tolerance_pct
    of that filter's threshold, and would otherwise have matched — i.e. the
    ones you left out by a small margin, so a screen's strictness is visible,
    not just its final count. A company MISSING data for a filtered column
    PASSES that filter rather than being dropped — absence of a figure is not
    evidence it fails the bar. Each funnel entry's "passed_with_missing_data"
    states how many cleared that step only because the column was empty for
    them, so that's visible too, not silently hidden inside the pass count.
    """
    df = build_metrics(force=force_rebuild)
    exclusion_summary = {"applied": [], "unknown": [], "excluded_count": 0,
                         "by_category": {}, "funnel": [], "near_misses": []}

    if exclude_categories:
        from . import ethics
        unknown = ethics.unknown_categories(exclude_categories)
        known = [c for c in exclude_categories if c not in unknown]
        if known:
            df, excluded_df = ethics.apply_exclusions(df, known)
            exclusion_summary["applied"] = known
            exclusion_summary["excluded_count"] = len(excluded_df)
            if not excluded_df.empty:
                for cat in known:
                    n = excluded_df["exclusion_reason"].str.startswith(f"[{cat}]").sum()
                    if n:
                        exclusion_summary["by_category"][cat] = int(n)
        exclusion_summary["unknown"] = unknown
    exclusion_summary["funnel"].append({"stage": "universe (post ethics)", "remaining": int(len(df))})

    def has(col):
        return col in df.columns

    if sector:
        m = pd.Series(False, index=df.index)
        for col in ("sector", "industry", "nse_industry", "screener_industry"):
            if has(col):
                m |= df[col].astype(str).str.contains(sector, case=False, na=False, regex=False)
        df = df[m]
        exclusion_summary["funnel"].append({"stage": f"sector~'{sector}'", "remaining": int(len(df))})
    if industry:
        m = pd.Series(False, index=df.index)
        for col in ("industry", "nse_industry", "screener_industry"):
            if has(col):
                m |= df[col].astype(str).str.contains(industry, case=False, na=False, regex=False)
        df = df[m]
        exclusion_summary["funnel"].append({"stage": f"industry~'{industry}'", "remaining": int(len(df))})
    if index:
        df = df[df["indices"].str.contains(index, case=False, na=False, regex=False)]
        exclusion_summary["funnel"].append({"stage": f"index~'{index}'", "remaining": int(len(df))})

    # candidates before numeric filters — the base for near-miss comparison
    pre_numeric = df

    numeric_filters: list[tuple[str, str, float]] = (  # (col, "min"|"max", value)
        [(c, "min", v) for c, v in (min_filters or {}).items() if has(c)] +
        [(c, "max", v) for c, v in (max_filters or {}).items() if has(c)]
    )
    for col, kind, v in numeric_filters:
        vals = pd.to_numeric(df[col], errors="coerce")
        # missing data is NOT evidence a company fails the criterion — a company
        # with no reported debt_equity isn't necessarily over-levered, it's just
        # unmeasured for that one column. It PASSES this filter rather than being
        # silently dropped; the funnel below reports how many passed this way so
        # it's never hidden that the criterion couldn't actually be verified for
        # them (check the result table's cell for that column — it will be blank).
        cleared = (vals >= float(v)) if kind == "min" else (vals <= float(v))
        unverified = int(vals.isna().sum())
        df = df[cleared | vals.isna()]
        exclusion_summary["funnel"].append({
            "stage": f"{col} {'>=' if kind == 'min' else '<='} {v}",
            "remaining": int(len(df)),
            "passed_with_missing_data": unverified,
        })

    if numeric_filters and near_miss_tolerance_pct > 0 and not pre_numeric.empty:
        exclusion_summary["near_misses"] = _find_near_misses(
            pre_numeric, numeric_filters, near_miss_tolerance_pct)

    for flag, want in (("above_dma50", above_dma50), ("above_dma200", above_dma200),
                       ("macd_bullish", macd_bullish)):
        if want is not None and has(flag):
            df = df[df[flag] == bool(want)]
            exclusion_summary["funnel"].append({"stage": f"{flag}={want}", "remaining": int(len(df))})

    matched = len(df)
    if sort_by not in df.columns:
        sort_by = "market_cap_cr"
    df = df.sort_values(sort_by, ascending=ascending, na_position="last")

    show_cols = ["symbol", "company_name", "sector", "nse_industry", "market_cap_cr",
                 "price", "pe", "pb", "roe", "roce", "debt_equity", "div_yield",
                 "ret_1m", "ret_3m", "ret_1y", "rsi", "above_dma200", "down_from_52w_high"]
    show_cols = [c for c in show_cols if c in df.columns]
    extra = [c for c in {sort_by, *((min_filters or {}).keys()), *((max_filters or {}).keys())}
             if c in df.columns and c not in show_cols]
    return (df[show_cols + extra].head(limit).reset_index(drop=True), matched,
            exclusion_summary)


# ============================================================ historical screens
# screen()/build_metrics() above only see the LATEST snapshot per company (today's
# ROCE, today's trailing return). "ROCE > 20% in FY2024" or "best performers in
# calendar 2023" need a per-YEAR value across the whole universe. These build a
# long-format (symbol, year, ...) cache in one pass over all companies (~45-55s
# cold; reused on disk like screener_metrics.parquet) so a year-screen is one
# lookup, never a per-company tool-call loop.

def build_historical_fundamentals(force: bool = False) -> pd.DataFrame:
    """Long-format table: one row per (symbol, fiscal_year) with Sales, OPM%,
    Net Profit, EPS (from profit_loss) and ROCE%/ROE% (from ratios; ROE for
    banks/NBFCs, ROCE otherwise), plus YoY growth of Sales/Net Profit/EPS."""
    if HIST_FUNDAMENTALS_CACHE.exists() and not force:
        age_h = (time.time() - HIST_FUNDAMENTALS_CACHE.stat().st_mtime) / 3600
        if age_h < 24:
            return pd.read_parquet(HIST_FUNDAMENTALS_CACHE)

    ent = entities().set_index("symbol")
    rows = []
    for sym in ent.index:
        pl, pper = series_map(sym, "profit_loss")
        if not pl:
            continue
        rt, rper = series_map(sym, "ratios")
        fin = is_financial(sym)
        sales = _get(pl, "Sales", "Revenue")
        opm = _get(pl, "OPM")
        npat = _get(pl, "Net Profit", "Net profit", "Profit after tax", "PAT")
        eps = _get(pl, "EPS")
        roce = _get(rt, "ROCE") if not fin else None
        roe = _get(rt, "ROE") if fin else None
        prev_sales = prev_npat = prev_eps = None
        for i, p in enumerate(pper):
            s = sales.get(p) if sales else None
            npv = npat.get(p) if npat else None
            epsv = eps.get(p) if eps else None
            rec = {
                "symbol": sym, "period": p,
                "fy_year": int(p.split()[-1]) if p.split()[-1].isdigit() else None,
                "sales_cr": s, "opm_pct": opm.get(p) if opm else None,
                "net_profit_cr": npv, "eps": epsv,
                "roce_pct": roce.get(p) if roce else None,
                "roe_pct": roe.get(p) if roe else None,
                "is_financial": fin,
                "sales_yoy_growth_pct": (round((s / prev_sales - 1) * 100, 1)
                                        if s and prev_sales and prev_sales > 0 else None),
                "net_profit_yoy_growth_pct": (round((npv / prev_npat - 1) * 100, 1)
                                             if npv and prev_npat and prev_npat > 0 else None),
                "eps_yoy_growth_pct": (round((epsv / prev_eps - 1) * 100, 1)
                                      if epsv and prev_eps and prev_eps > 0 else None),
            }
            rows.append(rec)
            if s is not None:
                prev_sales = s
            if npv is not None:
                prev_npat = npv
            if epsv is not None:
                prev_eps = epsv

    hist = pd.DataFrame(rows)
    keep_ent_cols = [c for c in ("company_name", "sector", "nse_industry", "market_cap_cr")
                     if c in ent.columns]
    hist = hist.merge(ent[keep_ent_cols], left_on="symbol", right_index=True, how="left")
    HIST_FUNDAMENTALS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    hist.to_parquet(HIST_FUNDAMENTALS_CACHE, index=False)
    return hist


def build_historical_price_returns(force: bool = False) -> pd.DataFrame:
    """Long-format table: one row per (symbol, calendar_year) with the close-to-
    close return for that YEAR specifically (split/bonus/dividend-adjusted),
    not a trailing return from today. Answers 'best/worst performers in <year>'."""
    if HIST_PRICE_RETURNS_CACHE.exists() and not force:
        age_h = (time.time() - HIST_PRICE_RETURNS_CACHE.stat().st_mtime) / 3600
        if age_h < 24:
            return pd.read_parquet(HIST_PRICE_RETURNS_CACHE)

    ent = entities().set_index("symbol")
    rows = []
    for sym in ent.index:
        df = prices(sym)
        if df is None or df.empty:
            continue
        col = "Adj Close" if "Adj Close" in df else "Close"
        d = df[["Date", col]].dropna()
        if d.empty:
            continue
        d["year"] = d["Date"].dt.year
        for yr, grp in d.groupby("year"):
            grp = grp.sort_values("Date")
            start, end = float(grp[col].iloc[0]), float(grp[col].iloc[-1])
            if start > 0:
                rows.append({"symbol": sym, "calendar_year": int(yr),
                            "return_pct": round((end / start - 1) * 100, 1),
                            "start_price": round(start, 2), "end_price": round(end, 2),
                            "trading_days": len(grp)})

    hist = pd.DataFrame(rows)
    keep_ent_cols = [c for c in ("company_name", "sector", "nse_industry", "market_cap_cr")
                     if c in ent.columns]
    hist = hist.merge(ent[keep_ent_cols], left_on="symbol", right_index=True, how="left")
    HIST_PRICE_RETURNS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    hist.to_parquet(HIST_PRICE_RETURNS_CACHE, index=False)
    return hist


def screen_by_year(
    year: int,
    kind: str = "fundamental",              # "fundamental" | "price_return"
    min_filters: dict | None = None,
    max_filters: dict | None = None,
    sector: str | None = None,
    industry: str | None = None,
    sort_by: str | None = None,
    ascending: bool = False,
    limit: int = 25,
    force_rebuild: bool = False,
) -> tuple[pd.DataFrame, int, str]:
    """Screen the universe by a SPECIFIC historical year, not the latest snapshot.

    kind='fundamental': year = fiscal year ending March (e.g. year=2024 -> "Mar
    2024"); filters on sales_cr / opm_pct / net_profit_cr / eps / roce_pct /
    roe_pct / *_yoy_growth_pct.
    kind='price_return': year = calendar year; filters on return_pct.

    Returns (result_df, matched_count, note) — note explains the year/period
    actually used and any partial-coverage caveat.
    """
    if kind == "price_return":
        df = build_historical_price_returns(force=force_rebuild)
        df = df[df["calendar_year"] == int(year)]
        note = f"calendar year {year} close-to-close return (Adj Close, {len(df)} companies with data)"
        default_sort = "return_pct"
    else:
        df = build_historical_fundamentals(force=force_rebuild)
        df = df[df["fy_year"] == int(year)]
        note = f"fiscal year ending Mar {year} ({len(df)} companies with a filed FY{year} statement)"
        default_sort = "net_profit_yoy_growth_pct"

    # if the caller filtered on exactly one metric, default the ranking to THAT
    # metric rather than a generic fallback — otherwise a growth-% column can be
    # dominated by tiny-base outliers (e.g. 7000% growth off a near-zero prior year)
    # unrelated to what was actually asked for.
    filtered_cols = list((min_filters or {}).keys()) + list((max_filters or {}).keys())
    if len(set(filtered_cols)) == 1:
        default_sort = filtered_cols[0]

    # if the caller filtered on exactly one metric, default the ranking to THAT
    # metric rather than a generic fallback — otherwise a growth-% column can be
    # dominated by tiny-base outliers (e.g. 7000% growth off a near-zero prior year)
    # unrelated to what was actually asked for.
    filtered_cols = list((min_filters or {}).keys()) + list((max_filters or {}).keys())
    if len(set(filtered_cols)) == 1:
        default_sort = filtered_cols[0]

    # if the caller filtered on exactly one metric, default the ranking to THAT
    # metric rather than a generic fallback — otherwise a growth-% column can be
    # dominated by tiny-base outliers (e.g. 7000% growth off a near-zero prior year)
    # unrelated to what was actually asked for.
    filtered_cols = list((min_filters or {}).keys()) + list((max_filters or {}).keys())
    if len(set(filtered_cols)) == 1:
        default_sort = filtered_cols[0]

    if sector:
        df = df[df.get("sector", pd.Series(dtype=str)).astype(str)
                .str.contains(sector, case=False, na=False, regex=False)]
    if industry and "nse_industry" in df.columns:
        df = df[df["nse_industry"].astype(str).str.contains(industry, case=False, na=False, regex=False)]

    # missing data for a filtered column PASSES rather than being excluded — same
    # rule as screen(): a company not having that figure for that year isn't
    # evidence it fails the bar. (This only concerns rows that already exist for
    # `year` — a company with no filing at all for that fiscal year was already
    # dropped by the fy_year match above, which is a real absence, not this case.)
    for col, v in (min_filters or {}).items():
        if col in df.columns:
            vals = pd.to_numeric(df[col], errors="coerce")
            df = df[(vals >= float(v)) | vals.isna()]
    for col, v in (max_filters or {}).items():
        if col in df.columns:
            vals = pd.to_numeric(df[col], errors="coerce")
            df = df[(vals <= float(v)) | vals.isna()]

    matched = len(df)
    sort_col = sort_by if sort_by in df.columns else default_sort
    df = df.sort_values(sort_col, ascending=ascending, na_position="last")
    return df.head(limit).reset_index(drop=True), matched, note


# ================================================================ consistency
# screen_by_year answers ONE year. Coffee-Can-style rules ("ROCE>=15% EVERY year
# for 10 years") need every year in a window to clear the bar — until now that
# meant a coarse screen_stocks cut + manual per-candidate verification (as
# investing-principles/SKILL.md instructs). This does the every-year check for
# the WHOLE universe in one pass over the already-cached historical table.

_METRIC_COLS = ("sales_cr", "opm_pct", "net_profit_cr", "eps", "roce_pct", "roe_pct",
               "sales_yoy_growth_pct", "net_profit_yoy_growth_pct", "eps_yoy_growth_pct")


def screen_consistency(
    metric: str,                    # one of _METRIC_COLS, or 'roce_or_roe' (uses ROCE for
                                     # non-financials, ROE for banks/NBFCs, per company —
                                     # NOT a third metric, just picks the correct real one)
    min_value: float,
    n_years: int = 10,
    max_violations: int = 0,        # years allowed below the bar (0 = every single year)
    min_years_required: int = 5,    # skip companies with less history than this
    sector: str | None = None,
    industry: str | None = None,
    sort_by: str | None = None,
    ascending: bool = False,
    limit: int = 25,
    force_rebuild: bool = False,
) -> tuple[pd.DataFrame, int, str]:
    """Screen the WHOLE universe for names where `metric` cleared `min_value` in
    (n_years - max_violations) or more of the last n_years (or all years on file,
    if a company has fewer than n_years of history — never penalised for being
    younger than the window, but excluded below min_years_required since too
    little history isn't a meaningful consistency claim).

    Returns (result_df, matched_count, note). result_df columns include
    years_checked, years_available, worst_year/worst_value, violations, the
    metric's mean/latest over the window, and (for metric='roce_or_roe') a
    metric_used column stating ROCE or ROE per row so it's never hidden which
    figure was actually applied. Sorted by the metric's minimum (weakest year)
    descending by default — rewarding the most consistently strong, not just
    the highest average.
    """
    mixed = metric == "roce_or_roe"
    col = "roce_pct" if mixed else metric
    if col not in _METRIC_COLS:
        raise ValueError(f"metric must be 'roce_or_roe' or one of {_METRIC_COLS}")

    hist = build_historical_fundamentals(force=force_rebuild)
    if sector:
        hist = hist[hist.get("sector", pd.Series(dtype=str)).astype(str)
                    .str.contains(sector, case=False, na=False, regex=False)]
    if industry and "nse_industry" in hist.columns:
        hist = hist[hist["nse_industry"].astype(str).str.contains(industry, case=False, na=False, regex=False)]
    hist = hist.dropna(subset=["fy_year"]).sort_values(["symbol", "fy_year"])

    rows = []
    for sym, grp in hist.groupby("symbol", sort=False):
        window = grp.tail(n_years)
        is_fin = bool(window["is_financial"].iloc[-1]) if "is_financial" in window else False
        if mixed:
            vals = window.apply(
                lambda r: r["roe_pct"] if r.get("is_financial") else r["roce_pct"], axis=1)
        else:
            vals = window[col]
        present = vals.dropna()
        if len(present) < min_years_required:
            continue
        violations = int((present < min_value).sum())
        if violations > max_violations:
            continue
        rows.append({
            "symbol": sym,
            "company_name": window["company_name"].iloc[-1] if "company_name" in window else None,
            "sector": window["sector"].iloc[-1] if "sector" in window else None,
            "metric_used": ("ROE" if is_fin else "ROCE") if mixed else metric,
            "years_checked": int(len(present)),
            "years_available_in_window": int(len(window)),
            "violations": violations,
            "worst_year": int(window.loc[present.idxmin(), "fy_year"]) if len(present) else None,
            "worst_value": round(float(present.min()), 1) if len(present) else None,
            "mean_value": round(float(present.mean()), 1) if len(present) else None,
            "latest_value": round(float(present.iloc[-1]), 1) if len(present) else None,
        })

    out = pd.DataFrame(rows)
    matched = len(out)
    metric_label = "ROCE (non-financials) or ROE (banks/NBFCs), per company" if mixed else metric
    note = (f"{metric_label} >= {min_value} in >= {n_years - max_violations} of the last "
           f"{n_years} fiscal years on file (min {min_years_required} years required)")
    if matched:
        sort_col = sort_by if sort_by in out.columns else "worst_value"
        out = out.sort_values(sort_col, ascending=ascending, na_position="last")
    return out.head(limit).reset_index(drop=True), matched, note


# ================================================================ consistency
# screen_by_year answers ONE year. Coffee-Can-style rules ("ROCE>=15% EVERY year
# for 10 years") need every year in a window to clear the bar — until now that
# meant a coarse screen_stocks cut + manual per-candidate verification (as
# investing-principles/SKILL.md instructs). This does the every-year check for
# the WHOLE universe in one pass over the already-cached historical table.

_METRIC_COLS = ("sales_cr", "opm_pct", "net_profit_cr", "eps", "roce_pct", "roe_pct",
               "sales_yoy_growth_pct", "net_profit_yoy_growth_pct", "eps_yoy_growth_pct")


def screen_consistency(
    metric: str,                    # one of _METRIC_COLS, or 'roce_or_roe' (uses ROCE for
                                     # non-financials, ROE for banks/NBFCs, per company —
                                     # NOT a third metric, just picks the correct real one)
    min_value: float,
    n_years: int = 10,
    max_violations: int = 0,        # years allowed below the bar (0 = every single year)
    min_years_required: int = 5,    # skip companies with less history than this
    sector: str | None = None,
    industry: str | None = None,
    sort_by: str | None = None,
    ascending: bool = False,
    limit: int = 25,
    force_rebuild: bool = False,
) -> tuple[pd.DataFrame, int, str]:
    """Screen the WHOLE universe for names where `metric` cleared `min_value` in
    (n_years - max_violations) or more of the last n_years (or all years on file,
    if a company has fewer than n_years of history — never penalised for being
    younger than the window, but excluded below min_years_required since too
    little history isn't a meaningful consistency claim).

    Returns (result_df, matched_count, note). result_df columns include
    years_checked, years_available, worst_year/worst_value, violations, the
    metric's mean/latest over the window, and (for metric='roce_or_roe') a
    metric_used column stating ROCE or ROE per row so it's never hidden which
    figure was actually applied. Sorted by the metric's minimum (weakest year)
    descending by default — rewarding the most consistently strong, not just
    the highest average.
    """
    mixed = metric == "roce_or_roe"
    col = "roce_pct" if mixed else metric
    if col not in _METRIC_COLS:
        raise ValueError(f"metric must be 'roce_or_roe' or one of {_METRIC_COLS}")

    hist = build_historical_fundamentals(force=force_rebuild)
    if sector:
        hist = hist[hist.get("sector", pd.Series(dtype=str)).astype(str)
                    .str.contains(sector, case=False, na=False, regex=False)]
    if industry and "nse_industry" in hist.columns:
        hist = hist[hist["nse_industry"].astype(str).str.contains(industry, case=False, na=False, regex=False)]
    hist = hist.dropna(subset=["fy_year"]).sort_values(["symbol", "fy_year"])

    rows = []
    for sym, grp in hist.groupby("symbol", sort=False):
        window = grp.tail(n_years)
        is_fin = bool(window["is_financial"].iloc[-1]) if "is_financial" in window else False
        if mixed:
            vals = window.apply(
                lambda r: r["roe_pct"] if r.get("is_financial") else r["roce_pct"], axis=1)
        else:
            vals = window[col]
        present = vals.dropna()
        if len(present) < min_years_required:
            continue
        violations = int((present < min_value).sum())
        if violations > max_violations:
            continue
        rows.append({
            "symbol": sym,
            "company_name": window["company_name"].iloc[-1] if "company_name" in window else None,
            "sector": window["sector"].iloc[-1] if "sector" in window else None,
            "metric_used": ("ROE" if is_fin else "ROCE") if mixed else metric,
            "years_checked": int(len(present)),
            "years_available_in_window": int(len(window)),
            "violations": violations,
            "worst_year": int(window.loc[present.idxmin(), "fy_year"]) if len(present) else None,
            "worst_value": round(float(present.min()), 1) if len(present) else None,
            "mean_value": round(float(present.mean()), 1) if len(present) else None,
            "latest_value": round(float(present.iloc[-1]), 1) if len(present) else None,
        })

    out = pd.DataFrame(rows)
    matched = len(out)
    metric_label = "ROCE (non-financials) or ROE (banks/NBFCs), per company" if mixed else metric
    note = (f"{metric_label} >= {min_value} in >= {n_years - max_violations} of the last "
           f"{n_years} fiscal years on file (min {min_years_required} years required)")
    if matched:
        sort_col = sort_by if sort_by in out.columns else "worst_value"
        out = out.sort_values(sort_col, ascending=ascending, na_position="last")
    return out.head(limit).reset_index(drop=True), matched, note


# ============================================================ historical screens
# screen()/build_metrics() above only see the LATEST snapshot per company (today's
# ROCE, today's trailing return). "ROCE > 20% in FY2024" or "best performers in
# calendar 2023" need a per-YEAR value across the whole universe. These build a
# long-format (symbol, year, ...) cache in one pass over all companies (~45-55s
# cold; reused on disk like screener_metrics.parquet) so a year-screen is one
# lookup, never a per-company tool-call loop.

def build_historical_fundamentals(force: bool = False) -> pd.DataFrame:
    """Long-format table: one row per (symbol, fiscal_year) with Sales, OPM%,
    Net Profit, EPS (from profit_loss) and ROCE%/ROE% (from ratios; ROE for
    banks/NBFCs, ROCE otherwise), plus YoY growth of Sales/Net Profit/EPS."""
    if HIST_FUNDAMENTALS_CACHE.exists() and not force:
        age_h = (time.time() - HIST_FUNDAMENTALS_CACHE.stat().st_mtime) / 3600
        if age_h < 24:
            return pd.read_parquet(HIST_FUNDAMENTALS_CACHE)

    ent = entities().set_index("symbol")
    rows = []
    for sym in ent.index:
        pl, pper = series_map(sym, "profit_loss")
        if not pl:
            continue
        rt, rper = series_map(sym, "ratios")
        fin = is_financial(sym)
        sales = _get(pl, "Sales", "Revenue")
        opm = _get(pl, "OPM")
        npat = _get(pl, "Net Profit", "Net profit", "Profit after tax", "PAT")
        eps = _get(pl, "EPS")
        roce = _get(rt, "ROCE") if not fin else None
        roe = _get(rt, "ROE") if fin else None
        prev_sales = prev_npat = prev_eps = None
        for i, p in enumerate(pper):
            s = sales.get(p) if sales else None
            npv = npat.get(p) if npat else None
            epsv = eps.get(p) if eps else None
            rec = {
                "symbol": sym, "period": p,
                "fy_year": int(p.split()[-1]) if p.split()[-1].isdigit() else None,
                "sales_cr": s, "opm_pct": opm.get(p) if opm else None,
                "net_profit_cr": npv, "eps": epsv,
                "roce_pct": roce.get(p) if roce else None,
                "roe_pct": roe.get(p) if roe else None,
                "is_financial": fin,
                "sales_yoy_growth_pct": (round((s / prev_sales - 1) * 100, 1)
                                        if s and prev_sales and prev_sales > 0 else None),
                "net_profit_yoy_growth_pct": (round((npv / prev_npat - 1) * 100, 1)
                                             if npv and prev_npat and prev_npat > 0 else None),
                "eps_yoy_growth_pct": (round((epsv / prev_eps - 1) * 100, 1)
                                      if epsv and prev_eps and prev_eps > 0 else None),
            }
            rows.append(rec)
            if s is not None:
                prev_sales = s
            if npv is not None:
                prev_npat = npv
            if epsv is not None:
                prev_eps = epsv

    hist = pd.DataFrame(rows)
    keep_ent_cols = [c for c in ("company_name", "sector", "nse_industry", "market_cap_cr")
                     if c in ent.columns]
    hist = hist.merge(ent[keep_ent_cols], left_on="symbol", right_index=True, how="left")
    HIST_FUNDAMENTALS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    hist.to_parquet(HIST_FUNDAMENTALS_CACHE, index=False)
    return hist


def build_historical_price_returns(force: bool = False) -> pd.DataFrame:
    """Long-format table: one row per (symbol, calendar_year) with the close-to-
    close return for that YEAR specifically (split/bonus/dividend-adjusted),
    not a trailing return from today. Answers 'best/worst performers in <year>'."""
    if HIST_PRICE_RETURNS_CACHE.exists() and not force:
        age_h = (time.time() - HIST_PRICE_RETURNS_CACHE.stat().st_mtime) / 3600
        if age_h < 24:
            return pd.read_parquet(HIST_PRICE_RETURNS_CACHE)

    ent = entities().set_index("symbol")
    rows = []
    for sym in ent.index:
        df = prices(sym)
        if df is None or df.empty:
            continue
        col = "Adj Close" if "Adj Close" in df else "Close"
        d = df[["Date", col]].dropna()
        if d.empty:
            continue
        d["year"] = d["Date"].dt.year
        for yr, grp in d.groupby("year"):
            grp = grp.sort_values("Date")
            start, end = float(grp[col].iloc[0]), float(grp[col].iloc[-1])
            if start > 0:
                rows.append({"symbol": sym, "calendar_year": int(yr),
                            "return_pct": round((end / start - 1) * 100, 1),
                            "start_price": round(start, 2), "end_price": round(end, 2),
                            "trading_days": len(grp)})

    hist = pd.DataFrame(rows)
    keep_ent_cols = [c for c in ("company_name", "sector", "nse_industry", "market_cap_cr")
                     if c in ent.columns]
    hist = hist.merge(ent[keep_ent_cols], left_on="symbol", right_index=True, how="left")
    HIST_PRICE_RETURNS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    hist.to_parquet(HIST_PRICE_RETURNS_CACHE, index=False)
    return hist


def screen_by_year(
    year: int,
    kind: str = "fundamental",              # "fundamental" | "price_return"
    min_filters: dict | None = None,
    max_filters: dict | None = None,
    sector: str | None = None,
    industry: str | None = None,
    sort_by: str | None = None,
    ascending: bool = False,
    limit: int = 25,
    force_rebuild: bool = False,
) -> tuple[pd.DataFrame, int, str]:
    """Screen the universe by a SPECIFIC historical year, not the latest snapshot.

    kind='fundamental': year = fiscal year ending March (e.g. year=2024 -> "Mar
    2024"); filters on sales_cr / opm_pct / net_profit_cr / eps / roce_pct /
    roe_pct / *_yoy_growth_pct.
    kind='price_return': year = calendar year; filters on return_pct.

    Returns (result_df, matched_count, note) — note explains the year/period
    actually used and any partial-coverage caveat.
    """
    if kind == "price_return":
        df = build_historical_price_returns(force=force_rebuild)
        df = df[df["calendar_year"] == int(year)]
        note = f"calendar year {year} close-to-close return (Adj Close, {len(df)} companies with data)"
        default_sort = "return_pct"
    else:
        df = build_historical_fundamentals(force=force_rebuild)
        df = df[df["fy_year"] == int(year)]
        note = f"fiscal year ending Mar {year} ({len(df)} companies with a filed FY{year} statement)"
        default_sort = "net_profit_yoy_growth_pct"

    if sector:
        df = df[df.get("sector", pd.Series(dtype=str)).astype(str)
                .str.contains(sector, case=False, na=False, regex=False)]
    if industry and "nse_industry" in df.columns:
        df = df[df["nse_industry"].astype(str).str.contains(industry, case=False, na=False, regex=False)]

    # missing data for a filtered column PASSES rather than being excluded — same
    # rule as screen(): a company not having that figure for that year isn't
    # evidence it fails the bar. (This only concerns rows that already exist for
    # `year` — a company with no filing at all for that fiscal year was already
    # dropped by the fy_year match above, which is a real absence, not this case.)
    for col, v in (min_filters or {}).items():
        if col in df.columns:
            vals = pd.to_numeric(df[col], errors="coerce")
            df = df[(vals >= float(v)) | vals.isna()]
    for col, v in (max_filters or {}).items():
        if col in df.columns:
            vals = pd.to_numeric(df[col], errors="coerce")
            df = df[(vals <= float(v)) | vals.isna()]

    matched = len(df)
    sort_col = sort_by if sort_by in df.columns else default_sort
    df = df.sort_values(sort_col, ascending=ascending, na_position="last")
    return df.head(limit).reset_index(drop=True), matched, note


# ============================================================ historical screens
# screen()/build_metrics() above only see the LATEST snapshot per company (today's
# ROCE, today's trailing return). "ROCE > 20% in FY2024" or "best performers in
# calendar 2023" need a per-YEAR value across the whole universe. These build a
# long-format (symbol, year, ...) cache in one pass over all companies (~45-55s
# cold; reused on disk like screener_metrics.parquet) so a year-screen is one
# lookup, never a per-company tool-call loop.

def build_historical_fundamentals(force: bool = False) -> pd.DataFrame:
    """Long-format table: one row per (symbol, fiscal_year) with Sales, OPM%,
    Net Profit, EPS (from profit_loss) and ROCE%/ROE% (from ratios; ROE for
    banks/NBFCs, ROCE otherwise), plus YoY growth of Sales/Net Profit/EPS."""
    if HIST_FUNDAMENTALS_CACHE.exists() and not force:
        age_h = (time.time() - HIST_FUNDAMENTALS_CACHE.stat().st_mtime) / 3600
        if age_h < 24:
            return pd.read_parquet(HIST_FUNDAMENTALS_CACHE)

    ent = entities().set_index("symbol")
    rows = []
    for sym in ent.index:
        pl, pper = series_map(sym, "profit_loss")
        if not pl:
            continue
        rt, rper = series_map(sym, "ratios")
        fin = is_financial(sym)
        sales = _get(pl, "Sales", "Revenue")
        opm = _get(pl, "OPM")
        npat = _get(pl, "Net Profit", "Net profit", "Profit after tax", "PAT")
        eps = _get(pl, "EPS")
        roce = _get(rt, "ROCE") if not fin else None
        roe = _get(rt, "ROE") if fin else None
        prev_sales = prev_npat = prev_eps = None
        for i, p in enumerate(pper):
            s = sales.get(p) if sales else None
            npv = npat.get(p) if npat else None
            epsv = eps.get(p) if eps else None
            rec = {
                "symbol": sym, "period": p,
                "fy_year": int(p.split()[-1]) if p.split()[-1].isdigit() else None,
                "sales_cr": s, "opm_pct": opm.get(p) if opm else None,
                "net_profit_cr": npv, "eps": epsv,
                "roce_pct": roce.get(p) if roce else None,
                "roe_pct": roe.get(p) if roe else None,
                "is_financial": fin,
                "sales_yoy_growth_pct": (round((s / prev_sales - 1) * 100, 1)
                                        if s and prev_sales and prev_sales > 0 else None),
                "net_profit_yoy_growth_pct": (round((npv / prev_npat - 1) * 100, 1)
                                             if npv and prev_npat and prev_npat > 0 else None),
                "eps_yoy_growth_pct": (round((epsv / prev_eps - 1) * 100, 1)
                                      if epsv and prev_eps and prev_eps > 0 else None),
            }
            rows.append(rec)
            if s is not None:
                prev_sales = s
            if npv is not None:
                prev_npat = npv
            if epsv is not None:
                prev_eps = epsv

    hist = pd.DataFrame(rows)
    keep_ent_cols = [c for c in ("company_name", "sector", "nse_industry", "market_cap_cr")
                     if c in ent.columns]
    hist = hist.merge(ent[keep_ent_cols], left_on="symbol", right_index=True, how="left")
    HIST_FUNDAMENTALS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    hist.to_parquet(HIST_FUNDAMENTALS_CACHE, index=False)
    return hist


def build_historical_price_returns(force: bool = False) -> pd.DataFrame:
    """Long-format table: one row per (symbol, calendar_year) with the close-to-
    close return for that YEAR specifically (split/bonus/dividend-adjusted),
    not a trailing return from today. Answers 'best/worst performers in <year>'."""
    if HIST_PRICE_RETURNS_CACHE.exists() and not force:
        age_h = (time.time() - HIST_PRICE_RETURNS_CACHE.stat().st_mtime) / 3600
        if age_h < 24:
            return pd.read_parquet(HIST_PRICE_RETURNS_CACHE)

    ent = entities().set_index("symbol")
    rows = []
    for sym in ent.index:
        df = prices(sym)
        if df is None or df.empty:
            continue
        col = "Adj Close" if "Adj Close" in df else "Close"
        d = df[["Date", col]].dropna()
        if d.empty:
            continue
        d["year"] = d["Date"].dt.year
        for yr, grp in d.groupby("year"):
            grp = grp.sort_values("Date")
            start, end = float(grp[col].iloc[0]), float(grp[col].iloc[-1])
            if start > 0:
                rows.append({"symbol": sym, "calendar_year": int(yr),
                            "return_pct": round((end / start - 1) * 100, 1),
                            "start_price": round(start, 2), "end_price": round(end, 2),
                            "trading_days": len(grp)})

    hist = pd.DataFrame(rows)
    keep_ent_cols = [c for c in ("company_name", "sector", "nse_industry", "market_cap_cr")
                     if c in ent.columns]
    hist = hist.merge(ent[keep_ent_cols], left_on="symbol", right_index=True, how="left")
    HIST_PRICE_RETURNS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    hist.to_parquet(HIST_PRICE_RETURNS_CACHE, index=False)
    return hist


def screen_by_year(
    year: int,
    kind: str = "fundamental",              # "fundamental" | "price_return"
    min_filters: dict | None = None,
    max_filters: dict | None = None,
    sector: str | None = None,
    industry: str | None = None,
    sort_by: str | None = None,
    ascending: bool = False,
    limit: int = 25,
    force_rebuild: bool = False,
) -> tuple[pd.DataFrame, int, str]:
    """Screen the universe by a SPECIFIC historical year, not the latest snapshot.

    kind='fundamental': year = fiscal year ending March (e.g. year=2024 -> "Mar
    2024"); filters on sales_cr / opm_pct / net_profit_cr / eps / roce_pct /
    roe_pct / *_yoy_growth_pct.
    kind='price_return': year = calendar year; filters on return_pct.

    Returns (result_df, matched_count, note) — note explains the year/period
    actually used and any partial-coverage caveat.
    """
    if kind == "price_return":
        df = build_historical_price_returns(force=force_rebuild)
        df = df[df["calendar_year"] == int(year)]
        note = f"calendar year {year} close-to-close return (Adj Close, {len(df)} companies with data)"
        default_sort = "return_pct"
    else:
        df = build_historical_fundamentals(force=force_rebuild)
        df = df[df["fy_year"] == int(year)]
        note = f"fiscal year ending Mar {year} ({len(df)} companies with a filed FY{year} statement)"
        default_sort = "net_profit_yoy_growth_pct"

    if sector:
        df = df[df.get("sector", pd.Series(dtype=str)).astype(str)
                .str.contains(sector, case=False, na=False, regex=False)]
    if industry and "nse_industry" in df.columns:
        df = df[df["nse_industry"].astype(str).str.contains(industry, case=False, na=False, regex=False)]

    for col, v in (min_filters or {}).items():
        if col in df.columns:
            df = df[pd.to_numeric(df[col], errors="coerce") >= float(v)]
    for col, v in (max_filters or {}).items():
        if col in df.columns:
            df = df[pd.to_numeric(df[col], errors="coerce") <= float(v)]

    matched = len(df)
    sort_col = sort_by if sort_by in df.columns else default_sort
    df = df.sort_values(sort_col, ascending=ascending, na_position="last")
    return df.head(limit).reset_index(drop=True), matched, note
