#!/usr/bin/env python3
"""
27_announcements_to_md.py â€” Turn BSE/NSE announcement PDFs into compact Markdown cards.

Every announcement's PAGE 1 is a cover letter to the exchanges with a fixed shape
(Date / Ref / Scrip code / Symbol / Subject / body / sender). We extract just that
into a small card; the messy attachment pages (newspaper cut-outs, financial tables
already held from Tijori/Screener) are ignored.

Scanned/image PDFs whose text won't extract fall back to a headline-only card built
from the exchange `title` in the screener JSON (flagged in data/parsed/_ann_flagged.csv).

DISK: announcements are bulky and one-shot, so every PDF is DELETED after a card is
written (parsed or fallback) when --delete is passed.

Output : data/markdown/{SYMBOL}/announcements/{name}.md
Flagged: data/parsed/_ann_flagged.csv
Usage:
  python 27_announcements_to_md.py --sample 20          # review -> data/_ann_review/, no delete
  python 27_announcements_to_md.py --symbol RAMCOSYS
  python 27_announcements_to_md.py --all --delete --workers 8
"""
import argparse, csv, json, logging, re
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT   = Path(__file__).parent.parent
STRUCT = ROOT / "data" / "structured"
PDFS   = ROOT / "data" / "pdfs"
MD_DIR = ROOT / "data" / "companies"
REVIEW = ROOT / "data" / "_ann_review"
PARSED = ROOT / "data" / "parsed"
FLAGGED = PARSED / "_ann_flagged.csv"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("ann2md")

# â”€â”€ mojibake repair (UTF-8 read as cp1252) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_MOJI = {
    "Ã¢â‚¬â„¢": "'", "Ã¢â‚¬Ëœ": "'", "Ã¢â‚¬Å“": '"', "Ã¢â‚¬\x9d": '"', "Ã¢â‚¬": '"', "Ã¢â‚¬â€œ": "-",
    "Ã¢â‚¬â€": "-", "Ã¢â‚¬Â¢": "-", "Ã¢â‚¬Â¦": "...", "Ã¢â€â€š": "|", "Ã‚ ": " ", "Ã‚": "",
    "MaÃ¢â‚¬â„¢am": "Ma'am", "Ã¢â‚¬â€¹": "",
}
def _demoji(s: str) -> str:
    for k, v in _MOJI.items():
        s = s.replace(k, v)
    return s

# â”€â”€ filename rebuilder (mirror 06_pdf_downloader.safe_name) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def safe_name(text: str, max_len: int = 80) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(text))
    text = re.sub(r'\s+', '_', text.strip())
    text = re.sub(r'_+', '_', text)
    return text[:max_len].rstrip('_.')

def title_to_stem(title: str) -> str:
    return safe_name(title[:70].replace(" ", "_"))

# â”€â”€ title -> (type, short description) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def split_title(title: str):
    t = title.strip()
    # "Announcement under Regulation 30 (LODR)-<Type> <date> - <desc>"
    typ = t
    desc = ""
    m = re.search(r'-\s*([^-]+?)\s+\d{1,2}[mhd]?\b', t)  # text before a date-ish token
    m2 = re.split(r'\s+-\s+', t, maxsplit=1)
    if len(m2) == 2:
        desc = m2[1].strip()
        typ = m2[0].strip()
    # strip leading "Announcement under Regulation N (LODR)-"
    typ = re.sub(r'^Announcement\s+under\s+Regulation[^-]*-\s*', '', typ, flags=re.I)
    typ = re.sub(r'\s+\d{1,2}[mhd]?(\s.*)?$', '', typ).strip(" -")
    return typ or "Announcement", desc

# â”€â”€ field extraction from page-1 text â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_DATE = re.compile(
    r'\b(\d{1,2}(?:st|nd|rd|th)?\s+'
    r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?,?\s+\d{4}'
    r'|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}'
    r'|\d{1,2}[-./]\d{1,2}[-./]\d{2,4})\b', re.I)
# 6-digit BSE code appearing within a short span after a BSE/Scrip/Stock cue
_SCRIP   = re.compile(r'(?:bse|scrip|security|stock)[^\n]{0,18}?(\d{6})\b', re.I)
_SYMBOL  = re.compile(r'(?:nse\s*symbol|symbol|scrip\s*name|stock\s*code|nse)\s*[:\-]\s*["\']?([A-Z][A-Z0-9&]{1,14})\b', re.I)
_REF     = re.compile(r'\bRef\.?\s*No\.?\s*[:\-]\s*([^\n]{2,40})', re.I)
_REG     = re.compile(r'Regulation\s+(\d{1,2}[A-Z]?)', re.I)
# strong anchors that mark the end of a Subject line / start of the letter body
_ANCHOR  = re.compile(
    r'\n\s*(?:Dear\b|Ref\.?\s*[:\-]|Pursuant\b|In\s+terms\b|With\s+reference\b|This\s+is\b'
    r'|We\s+(?:wish|are|have|hereby|refer|would|request)\b|Kindly\b|Yours\b|Thanking\b)', re.I)
_SUBMARK = re.compile(r'(?:^|\n)\s*(?:Sub|Subject|Sub\.)\s*[:\-]\s*', re.I)
_BODYDEAR = re.compile(r'Dear\s+Sir[^\n]*\n', re.I)
_CLOSING = re.compile(
    r'\s+(?:Thanking\s+you|Yours\s+(?:faithfully|sincerely|truly)|Encl[.: ]|'
    r'For\s+[A-Z][\w&.,\'\- ]+?(?:Limited|Ltd\.?))', re.I)
_SENDER_FOR = re.compile(r'\bFor\s+([A-Z][\w&.,\'\- ]+?(?:Limited|Ltd\.?))', re.I)
_DESIG   = re.compile(r'(Company Secretary[\w &\-]*|Managing Director|Whole[\s\-]?Time Director|'
                      r'Chief Financial Officer|Compliance Officer[\w &\-]*|Executive Director|'
                      r'Director|CFO|CEO|CS|President[\w &\-]*)', re.I)

def _clean(s: str) -> str:
    return re.sub(r'\s+', ' ', _demoji(s or "")).strip()

def looks_scanned(text: str) -> bool:
    t = text or ""
    if len(t.strip()) < 180:
        return True
    anchors = sum(1 for a in ("sub", "dear", "regulation", "bse", "nse", "scrip", "limited")
                  if a in t.lower())
    letters = sum(c.isalpha() or c.isspace() for c in t)
    ratio = letters / max(1, len(t))
    return anchors < 2 or ratio < 0.72

def parse_page1(text: str) -> dict:
    t = _demoji(text)
    out = {}
    # ---- subject + body ----
    sub = _SUBMARK.search(t)
    body = ""
    if sub:
        after = t[sub.end():]
        anc = _ANCHOR.search(after)
        subj = _clean(after[:anc.start()] if anc else after[:280])
        if len(subj) > 200:                       # over-captured -> trim to a sentence
            cut = subj.find('. ', 30)
            subj = (subj[:cut + 1] if 30 < cut < 200 else subj[:200]).rstrip()
        out["subject"] = subj
        if anc:
            body = after[anc.start():]
    bd = _BODYDEAR.search(t)                       # prefer the explicit "Dear Sir," body
    if bd:
        body = t[bd.end():]
    if body:
        sm = _SUBMARK.search(body[:120])          # drop a leading "Subject: ..." echo
        if sm:
            a2 = _ANCHOR.search(body[sm.end():])
            body = body[sm.end() + a2.start():] if a2 else body[sm.end():]
        body = _CLOSING.split(body)[0]
        out["body"] = _clean(body)[:1200]
    m = _SCRIP.search(t);  out["scrip"]  = m.group(1) if m else ""
    m = _SYMBOL.search(t); out["symbol"] = m.group(1) if m else ""
    m = _REF.search(t);    out["ref"]    = _clean(m.group(1)) if m else ""
    m = _DATE.search(t);   out["date"]   = _clean(m.group(1)) if m else ""
    m = _REG.search(t);    out["reg"]    = m.group(1) if m else ""
    mf = _SENDER_FOR.search(t)
    if mf:
        out["sender_co"] = _clean(mf.group(1))
        tail = t[mf.end():mf.end()+220]
        md = _DESIG.search(tail)
        # name = first non-empty line after "For <co>" that isn't a designation/sig
        name = ""
        for ln in tail.splitlines():
            s = ln.strip()
            if s and not _DESIG.search(s) and not re.match(r'(DIN|ACS|FCS|M\.?\s*No|Encl|Digitally)', s, re.I) \
               and len(s) < 45 and re.search(r'[A-Za-z]', s):
                name = _clean(s); break
        out["sender_name"] = name
        out["sender_desig"] = _clean(md.group(1)) if md else ""
    return out

def build_card(sym, title, parsed, scanned):
    typ, desc = split_title(title or "")
    typ = _clean(typ)[:60].rstrip(" -.")
    subject = parsed.get("subject") or desc or typ or "Announcement"
    if len(subject) > 200:                         # never let a runaway subject be the title
        subject = (desc or typ or subject[:120]).strip()
    bse = parsed.get("scrip", "")
    nse = parsed.get("symbol", "") or sym
    date = parsed.get("date", "")
    reg = parsed.get("reg", "")
    ref = parsed.get("ref", "")
    sender = ", ".join(x for x in (parsed.get("sender_name",""), parsed.get("sender_desig","")) if x)
    meta = [f"**Company:** {sym}"]
    if bse: meta.append(f"**BSE:** {bse}")
    meta.append(f"**NSE:** {nse}")
    if date: meta.append(f"**Date:** {date}")
    if typ:  meta.append(f"**Type:** {typ}")
    if reg:  meta.append(f"**Regulation:** {reg}")
    line2 = []
    if ref: line2.append(f"**Ref:** {ref}")
    if sender: line2.append(f"**Filed by:** {sender}")
    parts = [f"# {subject}", "", "- " + "  |  ".join(meta)]
    if line2: parts.append("- " + "  |  ".join(line2))
    parts.append("")
    if scanned:
        parts.append(f"_{typ}. Scanned/image filing â€” full text not machine-extractable; "
                     f"headline from exchange metadata. (PDF removed to save space.)_")
    else:
        body = parsed.get("body") or desc
        if body: parts.append(body)
        parts.append("")
        parts.append("_Cover-letter summary of a BSE/NSE filing; attachment pages omitted._")
    return "\n".join(parts).strip()

# â”€â”€ per-company processing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def title_map(sym):
    f = STRUCT / f"{sym}_screener.json"
    if not f.exists():
        return {}
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}
    anns = d.get("announcements", [])
    if not anns and isinstance(d.get("documents"), dict):
        anns = d["documents"].get("announcements", [])
    return {title_to_stem(a.get("title", "")): a.get("title", "") for a in anns if a.get("title")}

def process_symbol(sym, out_root, delete):
    src = PDFS / sym / "announcements"
    if not src.is_dir():
        return []
    tmap = title_map(sym)
    rows = []
    for pdf in src.glob("*.pdf"):
        title = tmap.get(pdf.stem, pdf.stem.replace("_", " "))
        dst = (out_root / sym / "announcements" / pdf.stem).with_suffix(".md")
        if dst.exists() and dst.stat().st_size > 120:
            if delete:
                try: pdf.unlink()
                except OSError: pass
            rows.append(("skip", sym)); continue
        status = "ok"
        try:
            import fitz
            doc = fitz.open(pdf)
            page1 = doc[0].get_text("text") if len(doc) else ""
            doc.close()
        except Exception:
            page1 = ""
        scanned = looks_scanned(page1)
        parsed = {} if scanned else parse_page1(page1)
        if not scanned and not parsed.get("subject") and not parsed.get("body"):
            scanned = True            # extraction yielded nothing usable
        if scanned:
            status = "fallback"
        md = build_card(sym, title, parsed, scanned)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(md, encoding="utf-8")
        if delete:
            try: pdf.unlink()
            except OSError: pass
        rows.append((status, sym))
    return rows

def _worker(args):
    return process_symbol(*args)

def all_symbols():
    return sorted(d.name for d in PDFS.iterdir() if d.is_dir() and (d / "announcements").is_dir())

def run(symbols, out_root, delete, workers):
    log.info(f"Processing announcements for {len(symbols)} companies -> {out_root}"
             f"{' [DELETING pdfs]' if delete else ''} ...")
    tot = {"ok": 0, "fallback": 0, "skip": 0}
    flagged = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_worker, (s, out_root, delete)) for s in symbols]
        for i, fut in enumerate(as_completed(futs), 1):
            for st, sym in fut.result():
                tot[st] = tot.get(st, 0) + 1
                if st == "fallback":
                    flagged.append(sym)
            if i % 200 == 0:
                log.info(f"  {i:,}/{len(symbols):,} companies â€” {tot}")
    if flagged:
        PARSED.mkdir(parents=True, exist_ok=True)
        with FLAGGED.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh); w.writerow(["symbol"]); w.writerows([[s] for s in flagged])
    log.info(f"DONE â€” {tot}  (fallback cards: {len(flagged)})")

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--sample", type=int)
    ap.add_argument("--delete", action="store_true", help="delete each PDF after writing its card")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    if args.sample:
        import random
        syms = all_symbols(); random.seed(13); random.shuffle(syms)
        picked = syms[:max(args.sample, 1)]
        log.info(f"Sample: {len(picked)} companies -> {REVIEW} (no delete)")
        for s in picked:
            for st, _ in process_symbol(s, REVIEW, False):
                pass
        log.info("sample done")
        return

    syms = [args.symbol] if args.symbol else all_symbols()
    run(syms, MD_DIR, args.delete, args.workers)

if __name__ == "__main__":
    main()
