#!/usr/bin/env python3
"""
36_profile_card.py — Per-company "profile card": a short synthesized overview that
doubles as (a) a RAG entry-point chunk and (b) the agent's company manifest.

Merges (lightly) what's already on disk into one card per company:
  business (Screener about) · sector · current key metrics · latest ownership ·
  valuation snapshot (from valuation.json) · sector peers · index membership+weight ·
  AND a manifest of which data sources exist (so the agent knows what to retrieve).

Output: data/companies/{SYM}/profile.md  +  profile.json
Usage:
  python 36_profile_card.py --symbol RELIANCE
  python 36_profile_card.py --all --workers 8
"""
import argparse, json, logging, re
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).parent.parent
STRUCT = ROOT / "data" / "structured"
COMP   = ROOT / "data" / "companies"
REF    = ROOT / "data" / "reference"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("profile")

_REF = {}
def _init():
    import pandas as pd
    try:
        _REF["ent"] = pd.read_csv(REF / "entities.csv", dtype=str, keep_default_na=False)
        _REF["ent"]["mcap_f"] = pd.to_numeric(_REF["ent"]["market_cap_cr"], errors="coerce")
        _REF["ent"]["pe_f"] = pd.to_numeric(_REF["ent"]["pe"], errors="coerce")
    except Exception:
        _REF["ent"] = None
    for fn, key in [("index_membership.json", "memb"), ("sector_peers.json", "speers"),
                    ("tijori_peers.json", "tpeers")]:
        try:
            _REF[key] = json.loads((REF / fn).read_text(encoding="utf-8"))
        except Exception:
            _REF[key] = {}
    try:
        import pandas as pd
        w = pd.read_csv(REF / "index_weights.csv")
        _REF["weights"] = {(r["index"], r["symbol"]): r["weight_pct"] for _, r in w.iterrows()}
    except Exception:
        _REF["weights"] = {}


def _num(x):
    if x is None:
        return None
    s = re.sub(r"[,%₹]", "", str(x)).strip()
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


def _latest_shareholding(d):
    out = {}
    for view in ("consolidated", "standalone"):
        sh = (d.get(view) or {}).get("shareholding", {})
        t = sh.get("quarterly") or sh.get("yearly")
        if isinstance(t, dict) and t.get("columns"):
            last = t["columns"][-1]
            for r in t.get("rows", []):
                cat = str(r.get("category", r.get("metric", ""))).rstrip("+ ").strip()
                v = r.get(last)
                if cat and v and any(k in cat.lower() for k in ("promoter", "fii", "dii", "government", "public")):
                    out[cat] = v
            if out:
                out["_as_of"] = last
                return out
    return out


def _technicals(sym):
    f = COMP / sym / "technicals.json"
    if not f.exists():
        return {}
    try:
        t = json.loads(f.read_text(encoding="utf-8")).get("technicals", {})
    except Exception:
        return {}
    if not t:
        return {}
    price, d50, d200 = t.get("Current price"), t.get("DMA 50"), t.get("DMA 200")
    rsi, dfh = t.get("RSI"), t.get("Down from 52w high")
    r1y, r3y = t.get("Return over 1year"), t.get("Return over 3years")
    pos = ""
    if price and d50 and d200:
        pos = ("above 50 & 200-DMA" if price > d50 and price > d200 else
               "below 50 & 200-DMA" if price < d50 and price < d200 else "between 50/200-DMA")
    zone = ("oversold" if rsi < 30 else "overbought" if rsi > 70 else "neutral") if rsi is not None else ""
    out = {"price": price, "down_from_52w_high_pct": dfh, "dma_position": pos,
           "rsi": rsi, "rsi_zone": zone, "return_1y_pct": r1y, "return_3y_pct": r3y}
    parts = []
    if price is not None: parts.append(f"₹{price:,.0f}")
    if dfh is not None: parts.append(f"{dfh:+.0f}% from 52w high")
    if pos: parts.append(pos)
    if rsi is not None: parts.append(f"RSI {rsi:.0f}" + (f" ({zone})" if zone else ""))
    rets = [x for x in ((f"1yr {r1y:+.0f}%" if r1y is not None else None),
                        (f"3yr {r3y:+.0f}%" if r3y is not None else None)) if x]
    if rets: parts.append(", ".join(rets))
    out["line"] = " · ".join(parts)
    return out


def build(sym):
    f = COMP / sym / "screener.json"
    if not f.exists():
        return None
    d = json.loads(f.read_text(encoding="utf-8"))
    ent = _REF.get("ent")
    erow = None
    if ent is not None:
        m = ent[ent["symbol"] == sym]
        erow = m.iloc[0] if len(m) else None
    sector = (erow.get("sector") if erow is not None else "") or ""
    nse = (erow.get("nse_symbol") if erow is not None else "") or ""
    bse = (erow.get("bse_code") if erow is not None else "") or ""
    isin = (erow.get("isin") if erow is not None else "") or ""
    name = d.get("company_name") or sym

    tr = d.get("top_ratios", {}) or {}
    def trv(*ns):
        for n in ns:
            for k, v in tr.items():
                if n.lower() in k.lower():
                    return v
        return None
    sm = d.get("screener_metrics", {}) or {}
    metrics = {
        "price": trv("Current Price"), "market_cap_cr": trv("Market Cap"),
        "pe": trv("Stock P/E", "P/E"), "book_value": trv("Book Value"),
        "div_yield": trv("Dividend Yield"), "roe": trv("ROE"), "roce": trv("ROCE"),
        "sales_ttm_cr": sm.get("Sales"), "pat_ttm_cr": sm.get("Profit after tax"),
        "ebitda_cr": sm.get("EBITDA"), "opm": sm.get("OPM"),
    }
    share = _latest_shareholding(d)

    # valuation snapshot
    val = {}
    vp = COMP / sym / "valuation" / "valuation.json"
    if vp.exists():
        try:
            val = json.loads(vp.read_text(encoding="utf-8"))
        except Exception:
            val = {}

    # peers (same sector, by mcap, top 6 excl self) + their P/E
    peers = []
    if sector and ent is not None:
        ps = ent[(ent["sector"] == sector) & (ent["symbol"] != sym)].copy()
        ps = ps.sort_values("mcap_f", ascending=False).head(6)
        peers = [{"symbol": r["symbol"], "company": r["company_name"], "pe": r["pe"]} for _, r in ps.iterrows()]
    if not peers:
        peers = [{"symbol": p, "company": p, "pe": ""} for p in (_REF.get("tpeers", {}).get(sym) or [])[:6]]

    # index membership + weight
    idxs = []
    for ix in (_REF.get("memb", {}).get(nse) or _REF.get("memb", {}).get(sym) or []):
        idxs.append({"index": ix, "weight_pct": _REF.get("weights", {}).get((ix, sym))})

    # data manifest
    cd = COMP / sym
    def cnt(sub, pat="*.md"):
        p = cd / sub
        return len(list(p.glob(pat))) if p.is_dir() else 0
    manifest = {
        "concalls": cnt("concalls"), "credit_ratings": cnt("credit_ratings"),
        "annual_reports": cnt("annual_reports"), "announcements": cnt("announcements"),
        "xbrl": cnt("xbrl"), "prices": (cd / "prices").is_dir(),
        "shareholding": bool(share), "valuation": vp.exists(),
    }
    return {"symbol": sym, "company": name, "sector": sector, "nse": nse, "bse": bse, "isin": isin,
            "about": d.get("about", ""), "metrics": metrics, "ownership": share,
            "technicals": _technicals(sym),
            "valuation": val.get("relative", {}), "dcf": val.get("dcf", {}),
            "peers": peers, "indices": idxs, "data": manifest}


def to_md(p):
    m = p["metrics"]; rel = p.get("valuation", {}); dc = p.get("dcf", {})
    L = [f"# {p['company']} ({p['symbol']})", ""]
    head = [x for x in (f"**Sector:** {p['sector']}" if p['sector'] else "",
                        f"**NSE:** {p['nse']}" if p['nse'] else "", f"**BSE:** {p['bse']}" if p['bse'] else "",
                        f"**ISIN:** {p['isin']}" if p['isin'] else "") if x]
    L.append("  |  ".join(head))
    if m.get("market_cap_cr") or m.get("price"):
        L.append(f"**Market cap:** ₹{m.get('market_cap_cr','?')} Cr  |  **Price:** ₹{m.get('price','?')}\n")
    if p["about"]:
        L.append("## Business"); L.append(p["about"]); L.append("")
    L.append("## Key metrics")
    L.append("| Metric | Value |\n|---|---|")
    for k, lab in [("pe","P/E"),("book_value","Book value ₹"),("roe","ROE %"),("roce","ROCE %"),
                   ("div_yield","Div yield %"),("opm","OPM %"),("sales_ttm_cr","Sales TTM ₹cr"),
                   ("pat_ttm_cr","PAT TTM ₹cr"),("ebitda_cr","EBITDA ₹cr")]:
        if m.get(k) not in (None, ""):
            L.append(f"| {lab} | {m[k]} |")
    if p["ownership"]:
        own = [f"{k} {v}" for k, v in p["ownership"].items() if not k.startswith("_")]
        L.append(f"\n## Ownership (as of {p['ownership'].get('_as_of','?')})")
        L.append(" · ".join(own))
    if p.get("technicals", {}).get("line"):
        L.append(f"\n**Technicals:** {p['technicals']['line']}")
    if rel.get("pe_vs_anchors") or dc.get("implied_growth_10y") is not None:
        L.append("\n## Valuation snapshot")
        if rel.get("pe_vs_anchors"):
            L.append("- P/E " + "; ".join(rel["pe_vs_anchors"]))
        if dc.get("implied_growth_10y") is not None:
            heg = dc.get("hist_eps_cagr")
            heg_s = f"{heg:.1%}" if heg is not None else "n/a"
            L.append(f"- Reverse-DCF implies ~{dc['implied_growth_10y']:.1%} EPS growth (hist EPS {heg_s})")
        sc = dc.get("scenarios", {}).get("base")
        if sc and sc.get("value_per_share"):
            L.append(f"- DCF base value ₹{sc['value_per_share']} ({sc.get('upside_pct')}% vs price)")
    if p["peers"]:
        L.append("\n## Sector peers")
        L.append(" · ".join(f"{pr['symbol']}" + (f" (PE {pr['pe']})" if pr['pe'] else "") for pr in p["peers"]))
    if p["indices"]:
        L.append("\n## Index membership")
        L.append(" · ".join(f"{i['index']}" + (f" ({i['weight_pct']}% wt)" if i.get('weight_pct') else "") for i in p["indices"]))
    dm = p["data"]
    L.append("\n## Available data (for retrieval)")
    L.append(" · ".join(f"{k}: {v}" for k, v in dm.items()))
    L.append("\n_Synthesized from structured data; valuation = model estimate, not investment advice._")
    return "\n".join(L)


def _worker(sym):
    try:
        p = build(sym)
        if not p:
            return ("skip", sym)
        out = COMP / sym
        out.mkdir(parents=True, exist_ok=True)
        (out / "profile.json").write_text(json.dumps(p, indent=1, ensure_ascii=False), encoding="utf-8")
        (out / "profile.md").write_text(to_md(p), encoding="utf-8")
        return ("ok", sym)
    except Exception as e:
        return ("fail", f"{sym}: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol"); ap.add_argument("--all", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    if a.symbol:
        _init()
        p = build(a.symbol)
        if p:
            out = COMP / a.symbol; out.mkdir(parents=True, exist_ok=True)
            (out / "profile.json").write_text(json.dumps(p, indent=1, ensure_ascii=False), encoding="utf-8")
            (out / "profile.md").write_text(to_md(p), encoding="utf-8")
            print(to_md(p))
        else:
            print("no data for", a.symbol)
        return
    syms = sorted(d.name for d in COMP.iterdir() if d.is_dir() and (d / "screener.json").exists())
    log.info(f"Building {len(syms)} profile cards ...")
    tot = {"ok": 0, "skip": 0, "fail": 0}
    with ProcessPoolExecutor(max_workers=a.workers, initializer=_init) as ex:
        for i, fut in enumerate(as_completed([ex.submit(_worker, s) for s in syms]), 1):
            st, _ = fut.result(); tot[st] = tot.get(st, 0) + 1
            if i % 500 == 0:
                log.info(f"  {i}/{len(syms)} — {tot}")
    log.info(f"DONE — {tot}")


if __name__ == "__main__":
    main()
