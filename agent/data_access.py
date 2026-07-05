"""Loaders over the Finance data lake: entities, per-company JSON/CSV, prices, macro, indices."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .config import COMPANIES, REFERENCE

STATEMENTS = {
    "profit_loss": "{sym}_profit_loss.csv",
    "balance_sheet": "{sym}_balance_sheet.csv",
    "cash_flow": "{sym}_cash_flow.csv",
    "quarterly_results": "{sym}_quarterly_results.csv",
    "shareholding": "{sym}_shareholding.csv",
}


# ---------------------------------------------------------------- entities --
@lru_cache(maxsize=1)
def entities() -> pd.DataFrame:
    return pd.read_parquet(REFERENCE / "entities.parquet")


def resolve_symbol(query: str, limit: int = 8) -> list[dict]:
    """Resolve a user-supplied name/symbol to candidate companies.

    An exact-symbol hit is pinned first (typing a ticker means that company),
    but namesakes are still returned — otherwise a query like 'Balkrishna',
    which happens to be a tiny paper mill's ticker, would hide the ₹40,000cr
    tyre major of the same name. Everything else ranks by market cap, coerced
    to NUMERIC (the column is stored as strings, so a naive sort ordered
    '58.4' above '40480.0')."""
    ent = entities()
    q = query.strip()
    exact = ent[ent["symbol"].str.upper() == q.upper()]
    name_hits = ent[ent["company_name"].str.contains(q, case=False, na=False, regex=False)]
    out = pd.concat([exact, name_hits]).drop_duplicates(subset="symbol")
    if out.empty:  # token-wise match: all words present
        toks = [t for t in q.lower().split() if len(t) > 2]
        if toks:
            m = pd.Series(True, index=ent.index)
            low = ent["company_name"].str.lower().fillna("")
            for t in toks:
                m &= low.str.contains(t, regex=False)
            out = ent[m]
    if out.empty:
        return []
    out = out.copy()
    out["_mcap"] = pd.to_numeric(out.get("market_cap_cr"), errors="coerce").fillna(-1)
    # pin exact-symbol match, then order by numeric market cap
    exact_syms = set(exact["symbol"])
    out["_exact"] = out["symbol"].isin(exact_syms).astype(int)
    out = out.sort_values(["_exact", "_mcap"], ascending=[False, False])
    cols = ["symbol", "company_name", "sector", "nse_industry", "market_cap_cr", "pe",
            "has_concalls", "has_annual_reports", "has_prices"]
    cols = [c for c in cols if c in out.columns]
    return out.head(limit)[cols].to_dict("records")


def company_dir(symbol: str) -> Path:
    return COMPANIES / symbol.upper()


def latest_reported_period(symbol: str) -> str | None:
    """Latest quarter this company has actually filed results for (last column
    of quarterly_results.csv). Use this whenever a query names a company but
    no period — never guess a 'current quarter' from today's calendar date,
    since filings lag the calendar by weeks."""
    df = financial_statement(symbol, "quarterly_results")
    if df is None or df.shape[1] < 2:
        return None
    return str(df.columns[-1])


# --------------------------------------------------------- per-company JSON --
def _read_json(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def profile(symbol: str):
    return _read_json(company_dir(symbol) / "profile.json")


def screener_data(symbol: str):
    return _read_json(company_dir(symbol) / "screener.json")


def stored_technicals(symbol: str):
    return _read_json(company_dir(symbol) / "technicals.json")


def valuation(symbol: str):
    return _read_json(company_dir(symbol) / "valuation" / "valuation.json")


def pit_disclosures(symbol: str):
    return _read_json(company_dir(symbol) / "pit" / f"{symbol.upper()}_pit.json")


# ------------------------------------------------------------ financials --
# screener.json is the real source for 3117 companies; the per-company CSVs
# only ever existed for RELIANCE (a scraping-era artifact). We read the JSON
# and shape it to the same (item, <period columns>) frame the CSVs used, so
# every caller keeps working unchanged.
def _screener_section(symbol: str, statement: str, basis: str | None = None):
    """Return (columns, rows) for a statement from screener.json.

    basis: 'consolidated' | 'standalone' | None (prefer consolidated, then
    standalone — whichever actually has rows)."""
    sj = screener_data(symbol)
    if not sj:
        return None
    order = [basis] if basis else ["consolidated", "standalone"]
    for b in order:
        block = (sj.get(b) or {}).get(statement)
        if not block:
            continue
        if statement == "shareholding":            # nested quarterly/yearly
            sub = block.get("quarterly") or block.get("yearly")
            if sub and sub.get("rows"):
                return b, sub["columns"], sub["rows"], "category"
            continue
        if block.get("rows"):
            return b, block["columns"], block["rows"], "metric"
    return None


def screener_statement_df(symbol: str, statement: str,
                          basis: str | None = None) -> pd.DataFrame | None:
    got = _screener_section(symbol, statement, basis)
    if not got:
        return None
    _basis, columns, rows, label_key = got
    data = []
    for r in rows:
        item = r.get(label_key, "")
        row = {"item": item}
        for c in columns:
            row[c] = r.get(c, "")
        data.append(row)
    return pd.DataFrame(data, columns=["item", *columns])


SCREENER_STATEMENTS = {"profit_loss", "balance_sheet", "cash_flow",
                       "quarterly_results", "ratios", "shareholding"}


def financial_statement(symbol: str, statement: str,
                        basis: str | None = None) -> pd.DataFrame | None:
    sym = symbol.upper()
    # 1) legacy per-company CSV if present (RELIANCE only, but honor it)
    fname = STATEMENTS.get(statement)
    if fname:
        path = company_dir(sym) / fname.format(sym=sym)
        if path.exists():
            df = pd.read_csv(path)
            if df.columns[0].startswith("Unnamed"):
                df = df.rename(columns={df.columns[0]: "item"})
            return df
    # 2) screener.json (the real source for the whole universe)
    if statement in SCREENER_STATEMENTS:
        return screener_statement_df(sym, statement, basis)
    if fname is None:
        raise ValueError(
            f"unknown statement '{statement}'; one of "
            f"{sorted(set(STATEMENTS) | SCREENER_STATEMENTS)}")
    return None


# ------------------------------------------------------------------- prices --
def prices(symbol: str) -> pd.DataFrame | None:
    p = company_dir(symbol) / "prices" / f"{symbol.upper()}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df["Date"] = pd.to_datetime(df["Date"])
    return df.sort_values("Date").reset_index(drop=True)


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def live_technicals(symbol: str) -> dict | None:
    """Recompute technicals/momentum from the price parquet (same metrics as scripts/03)."""
    df = prices(symbol)
    if df is None or df.empty or "Close" not in df:
        return None
    c = df["Close"].astype(float).dropna()
    if len(c) < 20:
        return None
    last_date = df.loc[c.index[-1], "Date"]
    v = df["Volume"].astype(float) if "Volume" in df else pd.Series(dtype=float)
    dma50, dma200 = c.rolling(50).mean(), c.rolling(200).mean()
    ema12, ema26 = c.ewm(span=12).mean(), c.ewm(span=26).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9).mean()
    rsi = _rsi(c)
    last252 = c.tail(252)

    def g(series, i=-1):
        try:
            x = series.iloc[i]
            return round(float(x), 2) if pd.notna(x) else None
        except (IndexError, ValueError):
            return None

    def ret(n):
        if len(c) <= n:
            return None
        a, b = c.iloc[-1], c.iloc[-1 - n]
        return round((a / b - 1) * 100, 2) if b else None

    m = {
        "as_of": str(pd.Timestamp(last_date).date()),
        "Current price": g(c),
        "DMA 50": g(dma50), "DMA 200": g(dma200),
        "RSI": g(rsi), "MACD": g(macd), "MACD Signal": g(signal),
        "Volume": g(v),
        "High price all time": round(float(c.max()), 2),
        "Low price all time": round(float(c.min()), 2),
        "Return over 1day": ret(1), "Return over 1week": ret(5),
        "Return over 1month": ret(21), "Return over 3months": ret(63),
        "Return over 6months": ret(126), "Return over 1year": ret(252),
        "Return over 3years": ret(756), "Return over 5years": ret(1260),
    }
    if len(last252):
        hi = float(last252.max())
        if hi:
            m["Down from 52w high"] = round((float(c.iloc[-1]) / hi - 1) * 100, 2)
    if m.get("DMA 50") and m.get("Current price"):
        m["Above DMA50"] = m["Current price"] > m["DMA 50"]
    if m.get("DMA 200") and m.get("Current price"):
        m["Above DMA200"] = m["Current price"] > m["DMA 200"]
    return {k: val for k, val in m.items() if val is not None}


def price_analytics(symbol: str, benchmark: str = "nifty50") -> dict | None:
    """Trader-oriented price stats beyond basic momentum: 52w range & position,
    max drawdown, annualized volatility, volume trend, moving-average crossover
    (golden/death cross) and relative strength vs a benchmark index."""
    df = prices(symbol)
    if df is None or df.empty or "Close" not in df:
        return None
    c = df["Close"].astype(float).dropna()          # nominal price levels
    if len(c) < 30:
        return None
    # Adjusted series (splits/bonuses/dividends) for all PERFORMANCE metrics -
    # raw Close produces phantom -50% "drawdowns" on ex-bonus dates. 52w range,
    # current price and DMAs stay on nominal Close (what the user sees quoted).
    ca = df["Adj Close"].astype(float).dropna() if "Adj Close" in df else c
    dates = df.loc[c.index, "Date"]
    cur = float(c.iloc[-1])
    last_date = pd.Timestamp(dates.iloc[-1])
    out: dict = {"symbol": symbol.upper(), "as_of": str(last_date.date()),
                 "current_price": round(cur, 2),
                 "performance_basis": "Adj Close (split/bonus/dividend-adjusted)"}

    last252 = c.tail(252)
    hi52, lo52 = float(last252.max()), float(last252.min())
    out["high_52w"], out["low_52w"] = round(hi52, 2), round(lo52, 2)
    out["pct_below_52w_high"] = round((cur / hi52 - 1) * 100, 1) if hi52 else None
    out["pct_above_52w_low"] = round((cur / lo52 - 1) * 100, 1) if lo52 else None

    # all-time high & drawdown on ADJUSTED series (correct peak-to-trough)
    athc = float(ca.max())
    out["pct_below_all_time_high"] = round((float(ca.iloc[-1]) / athc - 1) * 100, 1) if athc else None
    roll_max = ca.cummax()
    dd = (ca / roll_max - 1) * 100
    out["max_drawdown_pct"] = round(float(dd.min()), 1)
    out["current_drawdown_pct"] = round(float(dd.iloc[-1]), 1)

    # annualized volatility from last-1y adjusted daily returns
    rets = ca.pct_change().dropna()
    if len(rets) >= 30:
        out["annualized_volatility_pct"] = round(float(rets.tail(252).std() * (252 ** 0.5) * 100), 1)

    # volume trend: recent 20d avg vs 200d avg
    if "Volume" in df:
        v = df["Volume"].astype(float).dropna()
        if len(v) >= 200:
            v20, v200 = float(v.tail(20).mean()), float(v.tail(200).mean())
            out["avg_volume_20d"] = int(v20)
            out["volume_vs_200d_avg_pct"] = round((v20 / v200 - 1) * 100, 1) if v200 else None

    # moving-average crossover state
    dma50, dma200 = c.rolling(50).mean(), c.rolling(200).mean()
    if pd.notna(dma50.iloc[-1]) and pd.notna(dma200.iloc[-1]):
        d50, d200 = float(dma50.iloc[-1]), float(dma200.iloc[-1])
        out["dma50"], out["dma200"] = round(d50, 2), round(d200, 2)
        out["ma_structure"] = ("golden (50DMA above 200DMA - bullish structure)"
                               if d50 > d200 else
                               "death (50DMA below 200DMA - bearish structure)")

    # relative strength vs benchmark over 3m / 1y (adjusted series)
    def ret_over(series, days):
        if len(series) <= days:
            return None
        b = series.iloc[-1 - days]
        return (series.iloc[-1] / b - 1) * 100 if b else None
    bench = index_prices(benchmark)
    rs = {}
    for label, days in [("3m", 63), ("1y", 252)]:
        sr = ret_over(ca, days)
        if sr is None:
            continue
        br = None
        if bench is not None and "Close" in bench:
            bc = bench["Close"].astype(float).dropna()
            br = ret_over(bc, days)
        if br is not None:
            rs[label] = {"stock_return_pct": round(float(sr), 1),
                         f"{benchmark}_return_pct": round(float(br), 1),
                         "relative_strength_pct": round(float(sr - br), 1)}
        else:
            rs[label] = {"stock_return_pct": round(float(sr), 1)}
    if rs:
        out["relative_strength"] = rs
    return out


# -------------------------------------------------------------- reference ---
@lru_cache(maxsize=1)
def index_membership() -> dict:
    return json.loads((REFERENCE / "index_membership.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def sector_peers() -> dict:
    return json.loads((REFERENCE / "sector_peers.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def tijori_peers() -> dict:
    return json.loads((REFERENCE / "tijori_peers.json").read_text(encoding="utf-8"))


def macro_list() -> list[str]:
    return sorted(p.stem for p in (REFERENCE / "macro").glob("*.csv"))


def macro_series(name: str) -> pd.DataFrame | None:
    p = REFERENCE / "macro" / f"{name}.csv"
    if not p.exists():
        return None
    return pd.read_csv(p)


def index_list() -> list[str]:
    return sorted(p.stem for p in (REFERENCE / "indices").glob("*.parquet"))


def index_prices(name: str) -> pd.DataFrame | None:
    p = REFERENCE / "indices" / f"{name}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df["Date"] = pd.to_datetime(df["Date"])
    return df.sort_values("Date").reset_index(drop=True)


def index_valuation(name: str) -> pd.DataFrame | None:
    p = REFERENCE / "index_valuation" / f"{name}.csv"
    if not p.exists():
        return None
    return pd.read_csv(p)


# ---------------------------------------------------------------- helpers ---
def df_to_md(df: pd.DataFrame, max_rows: int = 60) -> str:
    if df is None or df.empty:
        return "(no data)"
    shown = df.head(max_rows)
    try:
        txt = shown.to_markdown(index=False)
    except ImportError:
        txt = shown.to_string(index=False)
    if len(df) > max_rows:
        txt += f"\n... ({len(df) - max_rows} more rows)"
    return txt
