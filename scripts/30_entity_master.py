#!/usr/bin/env python3
"""
30_entity_master.py — Build the entity-resolution + sector master table.

The connective tissue for the RAG/agent: one row per company resolving
symbol ↔ NSE symbol ↔ BSE code ↔ ISIN ↔ legal name, with industry/sector and
market cap, plus industry→[symbols] peer groups and Tijori's explicit peers.

Sources (all already on disk):
  data/structured/*_screener.json  — symbol, company_name, nse_symbol, bse_code,
                                      isin, listed_on, bse_group, industry, top_ratios
  data/companies/{SYM}/...          — which doc/data categories exist per company
  Tijori (screener.tijori.peers / benchmarking) — explicit peer sets

Output:
  data/reference/entities.csv  + entities.parquet   (master table)
  data/reference/sector_peers.json                  (industry -> [symbols])
  data/reference/tijori_peers.json                  (symbol -> [peer names])
Usage: python 30_entity_master.py
"""
import json, logging, re
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT   = Path(__file__).parent.parent
STRUCT = ROOT / "data" / "structured"
COMP   = ROOT / "data" / "companies"
REF    = ROOT / "data" / "reference"
REF.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("entity")

CATS = ["concalls", "credit_ratings", "announcements", "annual_reports", "xbrl", "pit", "prices"]


def _num(x):
    if x is None:
        return None
    s = re.sub(r"[^\d.\-]", "", str(x))
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


def _top_ratio(top, *names):
    """top_ratios may be a dict or list of {name,value}; pull first matching name."""
    if isinstance(top, dict):
        for n in names:
            for k, v in top.items():
                if n.lower() in k.lower():
                    return v
    elif isinstance(top, list):
        for it in top:
            label = str(it.get("name", it.get("label", "")))
            for n in names:
                if n.lower() in label.lower():
                    return it.get("value")
    return None


def main():
    files = sorted(COMP.glob("*/screener.json"))
    log.info(f"Reading {len(files)} screener files ...")
    rows, tijori_peers = [], {}
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        sym = d.get("symbol") or f.parent.name
        top = d.get("top_ratios")
        mcap = _num(_top_ratio(top, "Market Cap"))
        price = _num(_top_ratio(top, "Current Price", "Price"))
        pe = _num(_top_ratio(top, "Stock P/E", "P/E"))
        comp_dir = COMP / sym
        have = {c: (comp_dir / c).is_dir() for c in CATS} if comp_dir.is_dir() else {c: False for c in CATS}
        rows.append({
            "symbol": sym,
            "company_name": d.get("company_name") or "",
            "nse_symbol": d.get("nse_symbol") or "",
            "bse_code": str(d.get("bse_code") or ""),
            "isin": d.get("isin") or "",
            "industry": (d.get("industry") or "").strip(),
            "bse_group": d.get("bse_group") or "",
            "listed_on": ",".join(d["listed_on"]) if isinstance(d.get("listed_on"), list) else (d.get("listed_on") or ""),
            "market_cap_cr": mcap,
            "price": price,
            "pe": pe,
            **{f"has_{c}": have[c] for c in CATS},
        })
        # tijori explicit peers (names)
        tj = d.get("tijori") if isinstance(d.get("tijori"), dict) else {}
        peers = tj.get("peers")
        names = []
        if isinstance(peers, dict):
            for r in peers.get("rows", []):
                nm = r.get("name") or r.get("company") or (r.get("values", {}) or {}).get("name")
                if nm:
                    names.append(nm)
        bm = tj.get("benchmarking")
        if isinstance(bm, dict) and bm.get("companies"):
            names += [c for c in bm["companies"]]
        if names:
            tijori_peers[sym] = sorted(set(names))

    df = pd.DataFrame(rows)
    df.to_csv(REF / "entities.csv", index=False, encoding="utf-8")
    try:
        df.to_parquet(REF / "entities.parquet", index=False)
    except Exception as e:
        log.warning(f"parquet skipped: {e}")

    # industry -> [symbols] peer groups
    groups = defaultdict(list)
    for r in rows:
        if r["industry"]:
            groups[r["industry"]].append(r["symbol"])
    (REF / "sector_peers.json").write_text(
        json.dumps({k: sorted(v) for k, v in sorted(groups.items())}, indent=1), encoding="utf-8")
    (REF / "tijori_peers.json").write_text(json.dumps(tijori_peers, indent=1), encoding="utf-8")

    # diagnostics
    n_ind = df["industry"].replace("", pd.NA).notna().sum()
    log.info(f"entities: {len(df)} rows | with industry: {n_ind} | distinct industries: {df['industry'].replace('',pd.NA).nunique()}")
    log.info(f"with ISIN: {(df['isin']!='').sum()} | with BSE code: {(df['bse_code']!='').sum()} | with mktcap: {df['market_cap_cr'].notna().sum()}")
    top_inds = df[df['industry']!='']['industry'].value_counts().head(12)
    log.info("top industries:\n" + top_inds.to_string())
    log.info(f"tijori peer-sets: {len(tijori_peers)}")
    log.info(f"-> {REF}")


if __name__ == "__main__":
    main()
