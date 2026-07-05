#!/usr/bin/env python3
"""
11_merge_company_data.py — consolidate per-company files into one record.

Folds  <SYM>_technicals.json  and  <SYM>_xbrl.json  (and matched Tijori data)
INTO the canonical <SYM>_screener.json under keys: technicals, xbrl_quarterly,
tijori.  Idempotent (re-run anytime to refresh as background jobs progress).

  python 11_merge_company_data.py                      # merge only (no deletes)
  python 11_merge_company_data.py --clean-technicals   # also delete *_technicals.json
  python 11_merge_company_data.py --clean-xbrl         # also delete *_xbrl.json (after XBRL job done)
"""
import argparse, json, logging
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
STRUCT = ROOT / "data" / "structured"
TIJORI = ROOT / "data" / "tijori"
TRENDLYNE = ROOT / "data" / "trendlyne"

TL_CUTOFF = datetime(2025, 6, 1)  # last 4 quarters

def _parse_date(s):
    for fmt in ("%d %b %Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except (ValueError, AttributeError):
            pass
    return None
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("merge")


def load(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean-technicals", action="store_true")
    ap.add_argument("--clean-xbrl", action="store_true")
    args = ap.parse_args()

    # tijori symbol -> file (matched_symbol lives inside each tijori file)
    tij_by_sym = {}
    for f in TIJORI.glob("*.json"):
        d = load(f)
        if d and d.get("matched_symbol"):
            tij_by_sym[d["matched_symbol"]] = f

    # trendlyne symbol -> file
    tl_by_sym = {}
    for f in TRENDLYNE.glob("*_trendlyne.json"):
        sym = f.name[:-len("_trendlyne.json")]
        tl_by_sym[sym] = f

    screeners = sorted(STRUCT.glob("*_screener.json"))
    log.info(f"Merging into {len(screeners)} screener files "
             f"({len(tij_by_sym)} tijori matched, {len(tl_by_sym)} trendlyne)")
    c = {"tech": 0, "xbrl": 0, "tij": 0, "tl": 0, "files": 0}

    for p in screeners:
        sym = p.name[:-len("_screener.json")]
        j = load(p)
        if j is None:
            continue

        tp = STRUCT / f"{sym}_technicals.json"
        if tp.exists():
            td = load(tp)
            if td and td.get("technicals"):
                j["technicals"] = td["technicals"]
                c["tech"] += 1

        xp = STRUCT / f"{sym}_xbrl.json"
        if xp.exists():
            xd = load(xp)
            if xd and xd.get("quarters"):
                j["xbrl_quarterly"] = {"num_quarters": xd.get("num_quarters"),
                                       "quarters": xd["quarters"]}
                c["xbrl"] += 1

        if sym in tij_by_sym:
            td = load(tij_by_sym[sym])
            if td:
                j["tijori"] = {k: v for k, v in td.items() if k != "matched_symbol"}
                c["tij"] += 1

        if sym in tl_by_sym:
            tld = load(tl_by_sym[sym])
            if tld and tld.get("reports"):
                reports = []
                for r in tld["reports"]:
                    if not r.get("post_text"):
                        continue
                    dt = _parse_date(r.get("date", ""))
                    if dt is None or dt < TL_CUTOFF:
                        continue
                    reports.append({
                        "date": r.get("date", ""),
                        "broker": r.get("broker", ""),
                        "recommendation": r.get("recommendation", ""),
                        "target": r.get("target"),
                        "title": r.get("post_title", ""),
                        "text": r.get("post_text", ""),
                        "post_url": r.get("post_url", ""),
                    })
                if reports:
                    j["analyst_reports"] = reports
                    c["tl"] += 1

        p.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")
        c["files"] += 1
        if c["files"] % 500 == 0:
            log.info(f"  {c['files']}/{len(screeners)} ...")

    log.info(f"Merged: tech={c['tech']} xbrl={c['xbrl']} tij={c['tij']} tl={c['tl']} files={c['files']}")

    if args.clean_technicals:
        n = 0
        for f in STRUCT.glob("*_technicals.json"):
            f.unlink(); n += 1
        log.info(f"Deleted {n} *_technicals.json")
    if args.clean_xbrl:
        n = 0
        for f in STRUCT.glob("*_xbrl.json"):
            f.unlink(); n += 1
        log.info(f"Deleted {n} *_xbrl.json")


if __name__ == "__main__":
    main()
