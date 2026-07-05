#!/usr/bin/env python3
"""
34_valuation.py — Valuation engine (model estimates, NOT investment advice).

Combines everything built so far into a per-company valuation card:
  1. Current multiples      P/E, P/B, P/S, EV/EBITDA, div yield, ROE, ROCE, D/E
  2. Relative valuation     vs sector-index P/E (index_snapshot), vs peer-median
                            (entities by industry), vs own 10y P/E band (prices+EPS)
  3. Reverse DCF            growth the current price implies (2-stage FCFE on EPS)
  4. 2-stage DCF            value/share under bear/base/bull + a g×discount grid

Inputs: data/structured/{SYM}_screener.json (top_ratios, screener_metrics,
consolidated/standalone tables), data/reference/* (entities, index_snapshot,
index_membership, macro), data/companies/{SYM}/prices/*.parquet.

Output: data/companies/{SYM}/valuation/valuation.md  (+ valuation.json)
Usage:
  python 34_valuation.py --symbol RELIANCE
  python 34_valuation.py --all --workers 8
"""
import argparse, json, logging, math, re
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).parent.parent
STRUCT = ROOT / "data" / "structured"
COMP   = ROOT / "data" / "companies"
REF    = ROOT / "data" / "reference"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("valuation")


def _f(x):
    if x is None:
        return None
    s = re.sub(r"[,%]", "", str(x)).strip()
    if s in ("", "-", "NA", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _tbl(t):
    """screener table {columns:[...dates...], rows:[{<date>:val, 'metric':label}]}
    -> (cols, {label_lower: [floats aligned to cols]})."""
    if not isinstance(t, dict):
        return [], {}
    cols = t.get("columns", [])
    out = {}
    for r in t.get("rows", []):
        if not isinstance(r, dict):
            continue
        nm = str(r.get("metric", "")).strip()
        if not nm:
            continue
        out[nm.lower()] = [_f(r.get(c)) for c in cols]
    return cols, out


def _row(rows, *keys):
    for nm, vals in rows.items():
        if all(k in nm for k in keys):
            return vals
    return None


def _last(vals):
    if not vals:
        return None
    for v in reversed(vals):
        if v is not None:
            return v
    return None


def _cagr(vals, years=None):
    xs = [v for v in (vals or []) if v is not None and v > 0]
    if len(xs) < 2:
        return None
    n = (years or (len(xs) - 1))
    try:
        return (xs[-1] / xs[0]) ** (1 / n) - 1
    except Exception:
        return None


# ---- reference data (loaded once per process) ----------------------------------
def _load_ref():
    ref = {}
    try:
        import pandas as pd
        ent = pd.read_csv(REF / "entities.csv", dtype=str, keep_default_na=False)
        ent["pe_f"] = ent["pe"].map(_f)
        ref["entities"] = ent
    except Exception:
        ref["entities"] = None
    try:
        import pandas as pd
        snap = pd.read_csv(REF / "index_snapshot.csv")
        snap.columns = [c.strip() for c in snap.columns]
        ref["snapshot"] = {str(r["Index Name"]).strip().lower(): r for _, r in snap.iterrows()}
    except Exception:
        ref["snapshot"] = {}
    try:
        ref["membership"] = json.loads((REF / "index_membership.json").read_text(encoding="utf-8"))
    except Exception:
        ref["membership"] = {}
    try:
        import pandas as pd
        m = pd.read_csv(REF / "macro" / "macro_monthly.csv")
        ref["rf"] = float(m["gsec10y"].dropna().iloc[-1]) / 100.0
    except Exception:
        ref["rf"] = 0.07
    return ref


def _hist_pe_band(sym, eps_series, cols):
    """own historical P/E band: year-end price (prices parquet) / EPS that year."""
    try:
        import pandas as pd
        pq = list((COMP / sym / "prices").glob("*.parquet"))
        if not pq or not eps_series:
            return None
        px = pd.read_parquet(pq[0])
        dcol = "Date" if "Date" in px.columns else px.columns[0]
        # use 'Close' = split/bonus-adjusted (NOT 'Adj Close' which also strips
        # dividends and would distort P/E). Screener EPS is restated to current
        # shares, so both sides share the same adjusted basis.
        ccol = "Close" if "Close" in px.columns else [c for c in px.columns if "close" in c.lower()][0]
        px[dcol] = pd.to_datetime(px[dcol]).dt.tz_localize(None)
        px = px.sort_values(dcol)
        MON = {m: i for i, m in enumerate(
            ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"], 1)}
        pes = []
        for c, eps in zip(cols, eps_series):       # c like "Mar 2020" (fiscal period end)
            if not (eps and eps > 0):
                continue
            m = re.search(r"([A-Za-z]{3})\s*(20\d\d)", str(c))
            if not m:
                continue
            end = pd.Timestamp(int(m.group(2)), MON.get(m.group(1).lower(), 3), 1) + pd.offsets.MonthEnd(0)
            sub = px[px[dcol] <= end]              # price AT the fiscal period end
            if len(sub):
                pes.append(float(sub.iloc[-1][ccol]) / eps)
        pes = [p for p in pes if 0 < p < 300]
        if len(pes) >= 4:
            import statistics
            return {"median": float(statistics.median(pes)), "min": float(min(pes)),
                    "max": float(max(pes)), "n": len(pes)}
    except Exception:
        return None
    return None


# ---- DCF ------------------------------------------------------------------------
def _two_stage_value(eps0, g1, n, gt, ke):
    """PV of EPS growing at g1 for n yrs then terminal gt, discounted at ke."""
    if ke <= gt:
        return None
    pv, eps = 0.0, eps0
    for t in range(1, n + 1):
        eps *= (1 + g1)
        pv += eps / (1 + ke) ** t
    tv = eps * (1 + gt) / (ke - gt)
    pv += tv / (1 + ke) ** n
    return pv


def _reverse_dcf(price, eps0, ke, gt, n=10):
    """solve stage-1 growth g1 that makes the 2-stage value equal the price."""
    if not (price and eps0 and eps0 > 0 and ke > gt):
        return None
    lo, hi = -0.20, 0.60
    for _ in range(60):
        mid = (lo + hi) / 2
        v = _two_stage_value(eps0, mid, n, gt, ke)
        if v is None:
            return None
        if v > price:
            hi = mid
        else:
            lo = mid
    return round((lo + hi) / 2, 4)


def valuate(sym, ref):
    f = COMP / sym / "screener.json"
    if not f.exists():
        return None
    d = json.loads(f.read_text(encoding="utf-8"))
    tr = d.get("top_ratios", {}) or {}
    def trv(*names):
        for n in names:
            for k, v in tr.items():
                if n.lower() in k.lower():
                    return _f(v)
        return None
    price = trv("Current Price")
    mcap = trv("Market Cap")            # ₹ crore
    pe = trv("Stock P/E", "P/E")
    bv = trv("Book Value")
    dy = trv("Dividend Yield")
    roe = trv("ROE"); roce = trv("ROCE")
    sm = d.get("screener_metrics", {}) or {}
    sales = _f(sm.get("Sales")); ebitda = _f(sm.get("EBITDA")); pat = _f(sm.get("Profit after tax"))
    eps = _f(sm.get("EPS"))

    cons = d.get("consolidated") or d.get("standalone") or {}
    pc, pl = _tbl(cons.get("profit_loss"))
    bc, bs = _tbl(cons.get("balance_sheet"))
    cc, cf = _tbl(cons.get("cash_flow"))
    sales_hist = _row(pl, "sales") or _row(pl, "revenue")
    eps_hist = _row(pl, "eps")
    np_hist = _row(pl, "net profit") or _row(pl, "profit")
    borrow = _last(_row(bs, "borrowing") or [])
    cash = _last(_row(bs, "cash") or []) or 0
    equity_cap = _last(_row(bs, "equity capital") or [])
    reserves = _last(_row(bs, "reserves") or [])
    networth = (equity_cap or 0) + (reserves or 0) if (equity_cap or reserves) else None
    net_debt = (borrow or 0) - (cash or 0)

    # ---- multiples ----
    pb = (price / bv) if (price and bv) else None
    ps = (mcap / sales) if (mcap and sales) else None
    ev = (mcap + net_debt) if mcap is not None else None
    ev_ebitda = (ev / ebitda) if (ev and ebitda) else None
    de = (borrow / networth) if (borrow and networth) else None
    mult = {"price": price, "market_cap_cr": mcap, "pe": pe, "pb": round(pb, 2) if pb else None,
            "ps": round(ps, 2) if ps else None, "ev_ebitda": round(ev_ebitda, 2) if ev_ebitda else None,
            "div_yield": dy, "roe": roe, "roce": roce, "debt_equity": round(de, 2) if de else None,
            "net_debt_cr": round(net_debt, 0)}

    # ---- relative ----
    rel = {}
    ent = ref.get("entities")
    industry = ""
    if ent is not None:
        m = ent[ent["symbol"] == sym]
        if len(m):
            industry = (m.iloc[0].get("sector", "") or m.iloc[0].get("nse_industry", "") or "")
            if industry:
                col = "sector" if "sector" in ent.columns else "nse_industry"
                peers = ent[(ent[col] == industry) & (ent["pe_f"].notna()) & (ent["pe_f"] > 0) & (ent["pe_f"] < 200)]
                if len(peers) >= 3:
                    rel["peer_median_pe"] = round(float(peers["pe_f"].median()), 1)
                    rel["peer_count"] = int(len(peers))
    idxs = ref.get("membership", {}).get(sym) or ref.get("membership", {}).get((ent.iloc[0]["nse_symbol"] if ent is not None and len(ent[ent["symbol"]==sym]) else ""), [])
    for ix in (idxs or []):
        snap = ref["snapshot"].get(ix.lower())
        if snap is not None:
            rel["sector_index"] = ix
            rel["sector_index_pe"] = _f(snap.get("P/E"))
            break
    band = _hist_pe_band(sym, eps_hist, pc)
    if band:
        rel["own_pe_median_10y"] = round(band["median"], 1)
        rel["own_pe_range"] = [round(band["min"], 1), round(band["max"], 1)]
    # verdict vs each anchor
    verdicts = []
    if pe:
        for label, ref_pe in (("sector", rel.get("sector_index_pe")), ("peers", rel.get("peer_median_pe")),
                              ("own 10y median", rel.get("own_pe_median_10y"))):
            if ref_pe:
                pct = (pe / ref_pe - 1) * 100
                verdicts.append(f"{('+' if pct>=0 else '')}{pct:.0f}% vs {label} ({ref_pe})")
    rel["pe_vs_anchors"] = verdicts

    # ---- reverse DCF & DCF ----
    rf = ref.get("rf", 0.07)
    ke = round(rf + 1.0 * 0.055, 4)        # cost of equity = rf + beta(1.0)*ERP(5.5%)
    gt = 0.05                              # terminal growth (~ long-run nominal)
    implied_g = _reverse_dcf(price, eps, ke, gt) if (price and eps) else None
    hist_eps_cagr = _cagr(eps_hist)
    hist_sales_cagr = _cagr(sales_hist)
    dcf = {"cost_of_equity": ke, "terminal_growth": gt, "rf_10y_gsec": round(rf, 4),
           "implied_growth_10y": implied_g,
           "hist_eps_cagr": round(hist_eps_cagr, 4) if hist_eps_cagr else None,
           "hist_sales_cagr": round(hist_sales_cagr, 4) if hist_sales_cagr else None}
    # 2-stage DCF value/share under scenarios (growth anchored to history)
    base_g = max(min((hist_eps_cagr if hist_eps_cagr else 0.10), 0.25), 0.0)
    scen = {}
    if eps:
        for label, g in (("bear", max(base_g - 0.05, 0.0)), ("base", base_g), ("bull", base_g + 0.05)):
            v = _two_stage_value(eps, g, 10, gt, ke)
            scen[label] = {"growth": round(g, 3), "value_per_share": round(v, 1) if v else None,
                           "upside_pct": round((v / price - 1) * 100, 1) if (v and price) else None}
    dcf["scenarios"] = scen

    return {"symbol": sym, "company": d.get("company_name", sym), "industry": industry,
            "multiples": mult, "relative": rel, "dcf": dcf,
            "disclaimer": "Model estimates under stated assumptions — not investment advice."}


def _card_md(v):
    m, r, dc = v["multiples"], v["relative"], v["dcf"]
    L = [f"# {v['company']} ({v['symbol']}) — Valuation", ""]
    if v["industry"]:
        L.append(f"*Industry: {v['industry']}*\n")
    L.append("## Current multiples")
    L.append("| Metric | Value |\n|---|---|")
    for k, lab in [("price","Price ₹"),("market_cap_cr","Market cap ₹cr"),("pe","P/E"),("pb","P/B"),
                   ("ps","P/S"),("ev_ebitda","EV/EBITDA"),("div_yield","Div yield %"),
                   ("roe","ROE %"),("roce","ROCE %"),("debt_equity","Debt/Equity")]:
        if m.get(k) is not None:
            L.append(f"| {lab} | {m[k]:,} |")
    L.append("\n## Relative valuation")
    if r.get("pe_vs_anchors"):
        L.append("P/E vs anchors: " + "; ".join(r["pe_vs_anchors"]))
    for k, lab in [("sector_index","Sector index"),("sector_index_pe","Sector index P/E"),
                   ("peer_median_pe","Peer median P/E"),("own_pe_median_10y","Own 10y median P/E"),
                   ("own_pe_range","Own 10y P/E range")]:
        if r.get(k) is not None:
            L.append(f"- {lab}: {r[k]}")
    L.append("\n## DCF / reverse-DCF")
    L.append(f"- Cost of equity {dc['cost_of_equity']:.1%} (rf 10y G-sec {dc['rf_10y_gsec']:.1%} + 1.0×5.5% ERP); terminal {dc['terminal_growth']:.0%}")
    if dc.get("implied_growth_10y") is not None:
        L.append(f"- **Reverse-DCF**: current price implies ~**{dc['implied_growth_10y']:.1%}** EPS growth for 10y "
                 f"(history: EPS {(_pct(dc['hist_eps_cagr']))}, Sales {(_pct(dc['hist_sales_cagr']))})")
    if dc.get("scenarios"):
        L.append("- 2-stage DCF value/share:")
        L.append("\n| Scenario | Growth | Value ₹ | Upside |\n|---|---|---|---|")
        for s, sv in dc["scenarios"].items():
            L.append(f"| {s} | {sv['growth']:.0%} | {sv['value_per_share']} | {sv['upside_pct']}% |")
    L.append(f"\n_{v['disclaimer']}_")
    return "\n".join(L)


def _pct(x):
    return f"{x:.1%}" if x is not None else "n/a"


def _worker(sym):
    try:
        ref = _WORKER_REF
        v = valuate(sym, ref)
        if not v:
            return ("skip", sym)
        out = COMP / sym / "valuation"
        out.mkdir(parents=True, exist_ok=True)
        (out / "valuation.json").write_text(json.dumps(v, indent=1), encoding="utf-8")
        (out / "valuation.md").write_text(_card_md(v), encoding="utf-8")
        return ("ok", sym)
    except Exception as e:
        return ("fail", f"{sym}: {e}")


_WORKER_REF = None
def _init():
    global _WORKER_REF
    _WORKER_REF = _load_ref()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    if a.symbol:
        _init()
        v = valuate(a.symbol, _WORKER_REF)
        if v:
            out = COMP / a.symbol / "valuation"; out.mkdir(parents=True, exist_ok=True)
            (out / "valuation.json").write_text(json.dumps(v, indent=1), encoding="utf-8")
            (out / "valuation.md").write_text(_card_md(v), encoding="utf-8")
            print(_card_md(v))
        else:
            print("no data for", a.symbol)
        return
    syms = sorted(d.name for d in COMP.iterdir() if d.is_dir() and (d / "screener.json").exists())
    log.info(f"Valuing {len(syms)} companies ...")
    tot = {"ok": 0, "skip": 0, "fail": 0}
    with ProcessPoolExecutor(max_workers=a.workers, initializer=_init) as ex:
        for i, fut in enumerate(as_completed([ex.submit(_worker, s) for s in syms]), 1):
            st, _ = fut.result(); tot[st] = tot.get(st, 0) + 1
            if i % 500 == 0:
                log.info(f"  {i}/{len(syms)} — {tot}")
    log.info(f"DONE — {tot}")


if __name__ == "__main__":
    main()
