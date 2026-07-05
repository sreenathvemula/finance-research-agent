#!/usr/bin/env python3
"""
28_annual_reports_to_md.py â€” Convert ONLY the clean prose of born-digital annual
reports to Markdown.

Annual reports are mostly noise for RAG (glossy multi-column marketing, image
spreads, financial tables already held in Screener/XBRL/Tijori, cover/divider
pages). We therefore:
  * SKIP scanned / image-only PDFs entirely (flagged) â€” only born-digital touched.
  * Within a born-digital AR, KEEP only "prose" pages = clean single-column text;
    DROP multi-column, table/numeric, image-heavy and thin pages.
  * Convert just the kept pages with pymupdf4llm.

Output : data/markdown/{SYMBOL}/annual_reports/{name}.md   (kept-prose only)
Flagged: data/parsed/_ar_flagged.csv                        (scanned / empty)
Usage:
  python 28_annual_reports_to_md.py --sample 15      # review -> data/_ar_review/
  python 28_annual_reports_to_md.py --symbol GHCL
  python 28_annual_reports_to_md.py --all --workers 6
"""
import argparse, csv, importlib.util, logging, statistics
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# reuse script 21's fast font-based extraction helpers (_is_bold, clean_pages)
_spec = importlib.util.spec_from_file_location("md21", Path(__file__).parent / "21_pdf_to_markdown.py")
md21 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(md21)

ROOT   = Path(__file__).parent.parent
PDFS   = ROOT / "data" / "pdfs"
MD_DIR = ROOT / "data" / "companies"
REVIEW = ROOT / "data" / "_ar_review"
PARSED = ROOT / "data" / "parsed"
FLAGGED = PARSED / "_ar_flagged.csv"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("ar2md")

MIN_MD = 400          # below this, conversion considered empty


def _block_text(b):
    return " ".join(sp["text"] for l in b.get("lines", []) for sp in l.get("spans", []))


def page_kind(page) -> str:
    """Classify a page: prose (KEEP) | multicol | table | image | thin."""
    d = page.get_text("dict")
    text = page.get_text("text")
    chars = len(text.strip())
    if chars < 250:
        return "thin"
    pa = page.rect.width * page.rect.height
    img_area = sum((b["bbox"][2]-b["bbox"][0])*(b["bbox"][3]-b["bbox"][1])
                   for b in d["blocks"] if b.get("type") == 1)
    if img_area / max(1, pa) > 0.28:
        return "image"
    digits = sum(c.isdigit() for c in text)
    if digits / max(1, chars) > 0.16:
        return "table"
    # genuine multi-column: two substantial text blocks side-by-side (horizontally
    # disjoint) AND vertically overlapping. Footers/page-numbers don't trigger this.
    tb = [b for b in d["blocks"] if b.get("type") == 0 and len(_block_text(b).strip()) > 60]
    for i in range(len(tb)):
        ax0, ay0, ax1, ay1 = tb[i]["bbox"]
        for j in range(i+1, len(tb)):
            bx0, by0, bx1, by1 = tb[j]["bbox"]
            horizontally_disjoint = min(ax1, bx1) < max(ax0, bx0) - 10
            yov = min(ay1, by1) - max(ay0, by0)
            if horizontally_disjoint and yov > 25:
                return "multicol"
    return "prose"


def is_born_digital(doc) -> bool:
    n = len(doc)
    idx = range(0, n, max(1, n // 15))
    chars = [len(doc[i].get_text("text").strip()) for i in idx]
    return (sum(chars) / max(1, len(chars))) >= 150


def _fast_page(page, body) -> str:
    """Per-page markdown via script-21's font logic: bold spans -> **wrapped**,
    short larger-font lines -> '## **heading**'. Images/graphics ignored entirely."""
    lines = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            spans = [sp for sp in l.get("spans", []) if sp["text"].strip()]
            if not spans:
                continue
            parts = [f"**{sp['text'].strip()}**" if md21._is_bold(sp) else sp["text"].strip()
                     for sp in spans]
            txt = " ".join(parts)
            raw_len = sum(len(sp["text"]) for sp in spans)
            big = max(sp["size"] for sp in spans) >= body + 1.5
            # a real heading is big + short + NOT a wrapped paragraph tail
            # (those end in a period or start lowercase)
            if (big and raw_len <= 55 and "**" not in txt
                    and not txt.rstrip().endswith(('.', ',', ';'))
                    and txt[:1].isupper()):
                txt = f"## **{txt}**"
            lines.append(txt)
    return "\n".join(lines)


def convert(pdf: Path) -> tuple[str, str]:
    import fitz
    try:
        doc = fitz.open(pdf)
    except Exception as e:
        return ("fail", f"{pdf} :: {e}")
    n = len(doc)
    if n == 0 or not is_born_digital(doc):
        doc.close()
        return ("scanned", str(pdf))
    # body font size from a sample of pages (stable, cheap)
    sizes = [round(sp["size"]) for i in range(0, n, max(1, n // 20))
             for b in doc[i].get_text("dict")["blocks"] for l in b.get("lines", [])
             for sp in l.get("spans", []) if sp["text"].strip()]
    body = statistics.median(sizes) if sizes else 10
    pages_md = [_fast_page(doc[i], body) for i in range(n) if page_kind(doc[i]) == "prose"]
    doc.close()
    if not pages_md:
        return ("noprose", str(pdf))
    md = md21.clean_pages(pages_md)          # strip recurring headers/footers/page-nums
    if not md or len(md) < MIN_MD:
        return ("noprose", str(pdf))
    return ("ok", md)


def _worker(args):
    pdf_str, dst_str = args
    pdf, dst = Path(pdf_str), Path(dst_str)
    if dst.exists() and dst.stat().st_size > MIN_MD:
        return ("skip", pdf_str)
    status, payload = convert(pdf)
    if status == "ok":
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(payload, encoding="utf-8")
        return ("ok", pdf_str)
    return (status, payload)


def gather(symbols, out_root):
    jobs = []
    for sym in symbols:
        d = PDFS / sym / "annual_reports"
        if not d.is_dir():
            continue
        for pdf in d.glob("*.pdf"):
            dst = (out_root / sym / "annual_reports" / pdf.stem).with_suffix(".md")
            jobs.append((str(pdf), str(dst)))
    return jobs


def all_symbols():
    return sorted(d.name for d in PDFS.iterdir() if d.is_dir() and (d / "annual_reports").is_dir())


def run(symbols, out_root, workers):
    jobs = gather(symbols, out_root)
    if not jobs:
        log.info("No annual reports found."); return
    log.info(f"Converting {len(jobs):,} annual reports (born-digital, prose-only) -> {out_root} ...")
    tot = {"ok": 0, "skip": 0, "scanned": 0, "noprose": 0, "fail": 0}
    flagged = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_worker, j) for j in jobs]
        for i, fut in enumerate(as_completed(futs), 1):
            st, payload = fut.result()
            tot[st] = tot.get(st, 0) + 1
            if st in ("scanned", "noprose", "fail"):
                flagged.append((st, payload))
            if i % 100 == 0:
                log.info(f"  {i:,}/{len(jobs):,} â€” {tot}")
    if flagged:
        PARSED.mkdir(parents=True, exist_ok=True)
        with FLAGGED.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh); w.writerow(["status", "path"]); w.writerows(flagged)
    log.info(f"DONE â€” {tot}  (flagged {len(flagged)} -> {FLAGGED.name})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--sample", type=int)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    if args.sample:
        import random
        syms = all_symbols(); random.seed(5); random.shuffle(syms)
        picked = syms[:args.sample]
        jobs = gather(picked, REVIEW)
        log.info(f"Sample: {len(jobs)} ARs -> {REVIEW}")
        for j in jobs:
            st, _ = _worker(j)
            log.info(f"  [{st:8}] {Path(j[0]).parent.parent.name}")
        return

    syms = [args.symbol] if args.symbol else all_symbols()
    run(syms, MD_DIR, args.workers)


if __name__ == "__main__":
    main()
