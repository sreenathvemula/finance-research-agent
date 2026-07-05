#!/usr/bin/env python3
"""
35_index_weights.py — Free-float index weights for each Nifty index constituent.

Nifty indices are free-float market-cap weighted. niftyindices only publishes exact
weights in monthly factsheet PDFs, but we can reconstruct a close estimate from data
already on disk:
    free_float_mcap = market_cap × (1 − promoter%)
    weight          = ff_mcap / Σ ff_mcap within the index
market_cap from entities.csv, promoter% from each company's Screener shareholding.

Output: data/reference/index_weights.csv  (index, symbol, company, weight_pct,
        market_cap_cr, promoter_pct, free_float_mcap_cr)  — answers "is X a large
        weight in its index?"  Exact free-float weights would need the factsheet PDF.
Usage: python 35_index_weights.py
"""
import json, logging, re
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).parent.parent
REF  = ROOT / "data" / "reference"
COMP = ROOT / "data" / "companies"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("idxwt")


def _promoter_pct(sym):
    f = COMP / sym / "screener.json"
    if not f.exists():
        return None
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None
    for view in ("consolidated", "standalone"):
        sh = (d.get(view) or {}).get("shareholding", {})
        for period in ("quarterly", "yearly"):
            t = sh.get(period)
            if not isinstance(t, dict):
                continue
            cols = t.get("columns", [])
            for r in t.get("rows", []):
                cat = str(r.get("category", r.get("metric", ""))).lower()
                if "promoter" in cat:
                    for c in reversed(cols):                  # latest non-empty
                        v = r.get(c)
                        if v:
                            m = re.search(r"[\d.]+", str(v))
                            if m:
                                return float(m.group())
    return None


def main():
    membership = json.loads((REF / "index_membership.json").read_text(encoding="utf-8"))
    ent = pd.read_csv(REF / "entities.csv", dtype=str, keep_default_na=False)
    mcap = {r["symbol"]: r["market_cap_cr"] for _, r in ent.iterrows()}
    name = {r["symbol"]: r["company_name"] for _, r in ent.iterrows()}
    # map nse_symbol too (membership keys are NSE symbols)
    by_nse = {r["nse_symbol"]: r["symbol"] for _, r in ent.iterrows() if r["nse_symbol"]}

    # invert: index -> [symbols]
    idx_syms = {}
    for sym, idxs in membership.items():
        for ix in idxs:
            idx_syms.setdefault(ix, []).append(sym)

    prom_cache = {}
    rows = []
    for ix, syms in idx_syms.items():
        recs = []
        for nse in syms:
            sym = by_nse.get(nse, nse)
            mc = mcap.get(sym)
            try:
                mc = float(mc)
            except (TypeError, ValueError):
                continue
            if sym not in prom_cache:
                prom_cache[sym] = _promoter_pct(sym)
            prom = prom_cache[sym]
            ff = mc * (1 - (prom or 0) / 100.0)
            recs.append({"index": ix, "symbol": sym, "company": name.get(sym, sym),
                         "market_cap_cr": round(mc, 0), "promoter_pct": prom,
                         "free_float_mcap_cr": round(ff, 0)})
        tot = sum(r["free_float_mcap_cr"] for r in recs) or 1
        for r in recs:
            r["weight_pct"] = round(100 * r["free_float_mcap_cr"] / tot, 2)
        rows += sorted(recs, key=lambda r: -r["weight_pct"])
        if recs:
            top = max(recs, key=lambda r: r["weight_pct"])
            log.info(f"  {ix}: {len(recs)} stocks, top={top['symbol']} {top['weight_pct']}%")

    df = pd.DataFrame(rows)[["index", "symbol", "company", "weight_pct",
                             "market_cap_cr", "promoter_pct", "free_float_mcap_cr"]]
    df.to_csv(REF / "index_weights.csv", index=False, encoding="utf-8")
    log.info(f"index_weights.csv: {len(df)} rows across {df['index'].nunique()} indices -> {REF}")


if __name__ == "__main__":
    main()
