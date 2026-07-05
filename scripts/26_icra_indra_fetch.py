#!/usr/bin/env python3
"""
26_icra_indra_fetch.py â€” Recover ICRA + India Ratings credit-rating content that
was lost as dead HTML shells (script 25 flags them).

WHY: the screener "rating update" links for these two agencies pointed at JS/portal
pages, so the saved .html files carry no rationale. The real sources are:
  * ICRA  : icra.in/Rationale/ShowRationaleReport/?Id=N  embeds a PDF at
            icra.in/Rating/ShowRationalReportFilePdf/N  -> full report (no paywall).
  * India Ratings (fitch): indiaratings.co.in/pressrelease/N is an Angular app whose
            public API gives the rationale OVERVIEW + the full bank-facility ratings
            table.  (The long "key rating drivers" narrative is login-gated, so this
            captures the summary + every instrument/rating, not the full narrative.)

Drives off data/structured/*_screener.json (same date cutoff as the downloader) and
writes markdown straight into the markdown tree, reusing script 25's PDFâ†’md cleaner.

Output : data/markdown/{SYMBOL}/credit_ratings/{label}.md   (or data/_cr_review2 for --sample)
Usage:
  python 26_icra_indra_fetch.py --sample 12
  python 26_icra_indra_fetch.py --symbol APCOTEXIND
  python 26_icra_indra_fetch.py --all --workers 8
"""
import argparse, html, importlib.util, json, logging, re, tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from curl_cffi import requests as cffi

ROOT   = Path(__file__).parent.parent
STRUCT = ROOT / "data" / "structured"
MD_DIR = ROOT / "data" / "companies"
REVIEW = ROOT / "data" / "_cr_review2"

# reuse script 25's PDF->markdown + boilerplate cleaner
_spec = importlib.util.spec_from_file_location("cr25", Path(__file__).parent / "25_credit_ratings_to_md.py")
cr25 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(cr25)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("icra_indra")

TODAY     = date.today()
CR_CUTOFF = date(TODAY.year - 10, TODAY.month, TODAY.day)   # match 06_pdf_downloader
ICRA_PDF  = "https://www.icra.in/Rating/ShowRationalReportFilePdf/{id}"
INDRA     = "https://www.indiaratings.co.in/"
_MONTHS = {m: i for i, m in enumerate(
    ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"], 1)}


def safe_name(text: str, max_len: int = 80) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(text))
    text = re.sub(r'\s+', '_', text.strip())
    return re.sub(r'_+', '_', text)[:max_len].rstrip('_.')


def _date_from_label(label: str):
    m = re.search(r'(\d{1,2})\s+([A-Za-z]{3})\s+(20\d{2})', label)
    if not m:
        return None
    mon = _MONTHS.get(m.group(2).lower(), 0)
    try:
        return date(int(m.group(3)), mon, int(m.group(1))) if mon else None
    except ValueError:
        return None


def make_session():
    s = cffi.Session(impersonate="chrome")
    s.headers.update({"Referer": INDRA})
    return s


# â”€â”€ ICRA: download embedded PDF, convert with script-25 pipeline â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def icra_md(session, rid: str) -> str | None:
    r = session.get(ICRA_PDF.format(id=rid), timeout=60)
    if r.status_code != 200 or r.content[:5] != b"%PDF-":
        return None
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
        tf.write(r.content); tmp = Path(tf.name)
    try:
        return cr25.pdf_to_md(tmp)
    finally:
        try: tmp.unlink()
        except OSError: pass


# â”€â”€ India Ratings: compose markdown from public JSON API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _html_to_text(s: str) -> str:
    s = re.sub(r'(?i)<br\s*/?>', '\n', s or "")
    s = re.sub(r'(?i)</p>', '\n\n', s)
    s = re.sub(r'<[^>]+>', ' ', s)
    s = html.unescape(s)
    s = re.sub(r'[ \t]+', ' ', s)
    return re.sub(r'\n{3,}', '\n\n', s).strip()


def indra_md(session, prid: str) -> str | None:
    try:
        meta = session.get(INDRA + f"pressReleases/GetPressreleaseData_BeforeLogin?pressReleaseId={prid}", timeout=40).json()
    except Exception:
        return None
    rec = meta[0] if isinstance(meta, list) and meta else meta
    if not isinstance(rec, dict):
        return None
    title = (rec.get("pressReleaseTitle") or "").strip()
    eff   = (rec.get("effectiveDate") or "").strip()
    analyst = (rec.get("primaryAnalystName") or "").strip()
    overview = _html_to_text(rec.get("overview") or "")
    krd = _html_to_text(rec.get("keyRatingDrivers") or "")
    parts = []
    if title: parts.append(f"## **{title}**")
    if eff:   parts.append(eff)
    if analyst: parts.append(f"Primary analyst: {analyst}")
    if overview:
        parts.append("## **Rationale**"); parts.append(overview)
    if krd:
        parts.append("## **Key rating drivers**"); parts.append(krd)
    # bank-facility ratings table
    try:
        bf = session.get(INDRA + f"pressReleases/GetBankFacilityDataRatingLetter?pressReleaseId={prid}", timeout=40).json()
        brec = bf[0] if isinstance(bf, list) and bf else bf
        rows = brec.get("bankFacilitiesList", []) if isinstance(brec, dict) else []
    except Exception:
        rows = []
    if rows:
        tbl = ["## **Rating details**", "",
               "| Instrument | Bank | Rating | Rated amount (Rs cr) | Issuance | Maturity | Coupon |",
               "|---|---|---|---|---|---|---|"]
        for r in rows:
            cells = [str(r.get(k) or "").replace("|", "/").strip() for k in
                     ("instrument", "bankName", "rating", "ratedAmount", "issuanceDate", "maturityDate", "coupon")]
            cells = ["" if c in ("-", "None") else c for c in cells]
            tbl.append("| " + " | ".join(cells) + " |")
        parts.append("\n".join(tbl))           # whole table as one block (contiguous rows)
    body = "\n\n".join(parts).strip()
    if len(body) < 120:                       # nothing useful came back
        return None
    parts.append("\n_Source: India Ratings & Research (indiaratings.co.in) public press-release API; "
                 "full rationale narrative is available on the India Ratings website._")
    return cr25.strip_boilerplate("\n\n".join(parts))


# â”€â”€ job discovery from structured JSON â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def jobs_for(sym: str, out_root: Path):
    f = STRUCT / f"{sym}_screener.json"
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []
    docs = data.get("documents", {})
    jobs = []
    for it in (docs.get("credit_ratings", []) if isinstance(docs, dict) else []):
        url = it.get("url", "") or ""
        label = it.get("label", "credit_rating")
        d = _date_from_label(label)
        if d and d < CR_CUTOFF:
            continue
        m_icra = re.search(r'icra\.in/Rationale/ShowRationaleReport/?\?Id=(\d+)', url, re.I)
        m_ind  = re.search(r'indiaratings\.co\.in/pressrelease/(\d+)', url, re.I)
        if not (m_icra or m_ind):
            continue
        agency = "icra" if m_icra else "fitch"
        rid = (m_icra or m_ind).group(1)
        fname = safe_name(label.replace(" ", "_")) or f"{agency}_{rid}"
        dst = out_root / sym / "credit_ratings" / f"{fname}.md"
        jobs.append((sym, agency, rid, dst))
    return jobs


def do_job(job):
    sym, agency, rid, dst = job
    if dst.exists() and dst.stat().st_size > 300:
        return ("skip", sym, agency)
    s = make_session()
    try:
        md = icra_md(s, rid) if agency == "icra" else indra_md(s, rid)
    except Exception:
        md = None
    if not md or len(md) < 300:
        return ("dead", sym, agency)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(md, encoding="utf-8")
    return ("ok", sym, agency)


def all_symbols():
    return sorted(f.stem.replace("_screener", "") for f in STRUCT.glob("*_screener.json"))


def run(symbols, out_root, workers):
    jobs = [j for s in symbols for j in jobs_for(s, out_root)]
    if not jobs:
        log.info("No ICRA/India-Ratings links found."); return
    log.info(f"Fetching {len(jobs):,} ICRA/India-Ratings reports -> {out_root} ({workers} workers) ...")
    tot = {"ok": 0, "skip": 0, "dead": 0}
    by_ag = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(do_job, j) for j in jobs]
        for i, fut in enumerate(as_completed(futs), 1):
            st, sym, ag = fut.result()
            tot[st] = tot.get(st, 0) + 1
            by_ag.setdefault(ag, {"ok": 0, "dead": 0, "skip": 0})[st] += 1
            if i % 200 == 0:
                log.info(f"  {i:,}/{len(jobs):,} â€” {tot}")
    log.info(f"DONE â€” {tot}  by agency: {by_ag}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--sample", type=int)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    if args.sample:
        import random
        syms = all_symbols(); random.seed(11); random.shuffle(syms)
        picked = []
        for s in syms:
            js = jobs_for(s, REVIEW)
            if js:
                picked.append(random.choice(js))      # 1 per company, mixed agencies
            if len(picked) >= args.sample:
                break
        log.info(f"Sample: {len(picked)} reports -> {REVIEW}")
        for j in picked:
            st, sym, ag = do_job(j)
            log.info(f"  [{st:4}] {ag:6} {sym}  -> {j[3].name}")
        return

    syms = [args.symbol] if args.symbol else all_symbols()
    run(syms, MD_DIR, args.workers)


if __name__ == "__main__":
    main()
