#!/usr/bin/env python3
"""
03_yfinance_prices.py — OHLCV price history for all NSE companies via Yahoo Finance
(no auth, unlike the Upstox script 03_upstox_prices.py).

NSE tickers use the ".NS" suffix on Yahoo (e.g. RELIANCE.NS). Yahoo carries full
daily history (Reliance: 2010→today). This unlocks the technicals still missing
from screener_metrics: DMA 50/200, RSI, MACD, volume averages, and multi-period
price returns.

Outputs:
  data/prices/<SYMBOL>.parquet            per-company OHLCV (Date,Open,High,Low,Close,Adj Close,Volume)
  data/structured/<SYMBOL>_technicals.json  computed technical snapshot (with --technicals)

Usage:
  python 03_yfinance_prices.py --symbol RELIANCE         # single (test)
  python 03_yfinance_prices.py --all                     # download all (resumable)
  python 03_yfinance_prices.py --all --batch 100
  python 03_yfinance_prices.py --technicals --all        # compute technicals from parquets
"""
import argparse, json, logging, time, math
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
import numpy as np
import yfinance as yf
import warnings
warnings.filterwarnings("ignore")

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
PRICES = DATA / "prices"
STRUCT = DATA / "structured"
PRICES.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("prices")

START = "2005-01-01"
OHLCV_COLS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]


def nse_symbols():
    master = STRUCT / "company_master.json"
    recs = json.loads(master.read_text(encoding="utf-8"))
    return sorted({(r.get("nse_symbol") or "").strip()
                   for r in recs if (r.get("nse_symbol") or "").strip()})


def bse_only_symbols():
    """Return (screener_key, bse_code) for companies that have no NSE parquet yet."""
    master = STRUCT / "company_master.json"
    recs = json.loads(master.read_text(encoding="utf-8"))
    nse_have = {f.stem for f in PRICES.glob("*.parquet")}
    result = []
    for r in recs:
        nse = (r.get("nse_symbol") or "").strip()
        bse = (r.get("bse_code") or r.get("bse_symbol") or "").strip()
        screener_key = (r.get("symbol") or r.get("nse_symbol") or bse or "").strip()
        if not screener_key:
            continue
        if screener_key not in nse_have and bse and bse != nse:
            result.append((screener_key, bse))
    # Also pick up screener files named with numeric BSE codes
    screener_syms = {f.stem.replace("_screener", "") for f in STRUCT.glob("*_screener.json")}
    have = {f.stem for f in PRICES.glob("*.parquet")}
    for s in screener_syms - have:
        if s not in [x[0] for x in result]:
            result.append((s, s))  # try symbol directly as BSE code
    return result


def save_one(symbol, df):
    if df is None or df.empty:
        return False
    df = df.copy()
    # flatten MultiIndex columns; pick whichever level holds the OHLCV field names
    if isinstance(df.columns, pd.MultiIndex):
        lvl0 = set(df.columns.get_level_values(0))
        df.columns = df.columns.get_level_values(0 if "Close" in lvl0 else 1)
    df = df.dropna(how="all")
    if df.empty:
        return False
    df = df.reset_index()
    keep = ["Date"] + [c for c in OHLCV_COLS if c in df.columns]
    if "Close" not in keep:
        return False
    df = df[keep]
    df.to_parquet(PRICES / f"{symbol}.parquet", index=False)
    return True


def download_batch(symbols, force=False, exchange="NS"):
    """Download a batch of symbols; save each to parquet. Returns counts.
    exchange: 'NS' for NSE, 'BO' for BSE.
    For BSE, symbols may be numeric BSE codes (e.g. '500325') or NSE-style names.
    """
    todo = symbols if force else [s for s in symbols if not (PRICES / f"{s}.parquet").exists()]
    if not todo:
        return {"skip": len(symbols)}
    tickers = [f"{s}.{exchange}" for s in todo]
    data = yf.download(tickers, start=START, group_by="ticker", threads=True,
                       progress=False, auto_adjust=False)
    counts = {"ok": 0, "empty": 0, "skip": len(symbols) - len(todo)}
    for s in todo:
        t = f"{s}.{exchange}"
        try:
            sub = data[t] if len(tickers) > 1 else data
        except (KeyError, TypeError):
            counts["empty"] += 1
            continue
        counts["ok" if save_one(s, sub) else "empty"] += 1
    return counts


def download_bse_missing(batch=50, force=False):
    """Download prices for companies missing from NSE, trying BSE (.BO) suffix."""
    pairs = bse_only_symbols()
    if not pairs:
        log.info("No BSE-only companies to download")
        return
    log.info(f"BSE fallback: {len(pairs)} companies to try ...")
    totals = {"ok": 0, "empty": 0, "skip": 0}
    for i in range(0, len(pairs), batch):
        chunk = pairs[i:i + batch]
        # For BSE we use the bse_code as ticker but save under screener_key
        todo = [(key, bse) for key, bse in chunk
                if force or not (PRICES / f"{key}.parquet").exists()]
        if not todo:
            totals["skip"] += len(chunk)
            continue
        tickers = [f"{bse}.BO" for _, bse in todo]
        data = yf.download(tickers, start=START, group_by="ticker", threads=True,
                           progress=False, auto_adjust=False)
        for key, bse in todo:
            t = f"{bse}.BO"
            try:
                sub = data[t] if len(tickers) > 1 else data
            except (KeyError, TypeError):
                totals["empty"] += 1
                continue
            totals["ok" if save_one(key, sub) else "empty"] += 1
        log.info(f"  BSE {min(i+batch,len(pairs))}/{len(pairs)} — {totals}")
        time.sleep(1.0)
    log.info(f"BSE done. {totals}")


def run_download(symbols, batch, force):
    log.info(f"Prices: {len(symbols)} symbols, batch {batch}")
    totals = {}
    for i in range(0, len(symbols), batch):
        chunk = symbols[i:i + batch]
        c = download_batch(chunk, force=force)
        for k, v in c.items():
            totals[k] = totals.get(k, 0) + v
        log.info(f"  {min(i+batch,len(symbols))}/{len(symbols)} — {totals}")
        time.sleep(1.0)
    log.info(f"Done. {totals}")


# ── technicals ───────────────────────────────────────────────────────────────
def _rsi(close, n=14):
    d = close.diff()
    gain = d.clip(lower=0).ewm(alpha=1/n, min_periods=n).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1/n, min_periods=n).mean()
    rs = gain / loss
    return 100 - 100 / (1 + rs)


def _ret(close, n):
    if len(close) <= n:
        return None
    a, b = close.iloc[-1], close.iloc[-1 - n]
    return round((a / b - 1) * 100, 2) if b else None


def compute_technicals(symbol):
    p = PRICES / f"{symbol}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    if df.empty or "Close" not in df:
        return None
    c = df["Close"].astype(float).dropna()
    v = df["Volume"].astype(float) if "Volume" in df else pd.Series(dtype=float)
    if len(c) < 20:
        return None
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

    m = {
        "Current price": g(c),
        "DMA 50": g(dma50), "DMA 200": g(dma200),
        "DMA 50 previous day": g(dma50, -2), "DMA 200 previous day": g(dma200, -2),
        "RSI": g(rsi), "MACD": g(macd), "MACD Previous Day": g(macd, -2),
        "MACD Signal": g(signal), "MACD Signal Previous Day": g(signal, -2),
        "Volume": g(v), "High price all time": round(float(c.max()), 2),
        "Low price all time": round(float(c.min()), 2),
        "Return over 1day": _ret(c, 1), "Return over 1week": _ret(c, 5),
        "Return over 1month": _ret(c, 21), "Return over 3months": _ret(c, 63),
        "Return over 6months": _ret(c, 126), "Return over 1year": _ret(c, 252),
        "Return over 3years": _ret(c, 756), "Return over 7years": _ret(c, 1764),
        "Return over 10years": _ret(c, 2520),
    }
    if not v.empty:
        m["Volume 1week average"] = g(v.rolling(5).mean())
        m["Volume 1month average"] = g(v.rolling(21).mean())
        m["Volume 1year average"] = g(v.rolling(252).mean())
    if len(last252):
        hi = float(last252.max())
        m["Down from 52w high"] = round((float(c.iloc[-1]) / hi - 1) * 100, 2) if hi else None
    return {k: val for k, val in m.items() if val is not None}


def run_technicals(symbols):
    log.info(f"Technicals: {len(symbols)} symbols")
    ok = 0
    for i, s in enumerate(symbols):
        t = compute_technicals(s)
        if t:
            out = {"symbol": s, "computed_at": datetime.now(timezone.utc).isoformat(),
                   "technicals": t}
            (STRUCT / f"{s}_technicals.json").write_text(
                json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
            ok += 1
        if (i + 1) % 250 == 0:
            log.info(f"  {i+1}/{len(symbols)} — {ok} ok")
    log.info(f"Done. {ok} technicals written")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", help="single symbol (test)")
    ap.add_argument("--all", action="store_true", help="Download all NSE symbols")
    ap.add_argument("--bse", action="store_true", help="Download BSE fallback for missing companies")
    ap.add_argument("--batch", type=int, default=100)
    ap.add_argument("--technicals", action="store_true", help="compute technicals from parquets")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if args.symbol:
        s = args.symbol.upper()
        if args.technicals:
            t = compute_technicals(s)
            print(json.dumps(t, indent=2))
        else:
            c = download_batch([s], force=True)
            print(f"{s}: {c}")
            t = compute_technicals(s)
            print("technicals:", json.dumps(t, indent=2) if t else "none")
        return

    syms = nse_symbols()
    if args.technicals:
        run_technicals(syms)
    elif args.bse:
        download_bse_missing(batch=args.batch, force=args.force)
    elif args.all:
        run_download(syms, args.batch, args.force)
        log.info("Running BSE fallback for remaining companies ...")
        download_bse_missing(batch=args.batch, force=args.force)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
