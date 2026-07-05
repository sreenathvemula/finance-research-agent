#!/usr/bin/env python3
"""
21_pdf_to_markdown.py â€” Convert downloaded PDFs to Markdown for RAG.

Uses pymupdf4llm (pure CPU, fast, reliable on digital-born text PDFs like
concall transcripts and BSE filings). Markdown is ~2% the size of the PDF,
so this also frees disk: pass --delete-source to remove each PDF after a
successful conversion.

Input : data/pdfs/{SYMBOL}/{category}/*.pdf
Output: data/markdown/{SYMBOL}/{category}/*.md

Categories: concalls, annual_reports, credit_ratings, announcements.
Default processes ONLY concalls (the priority for RAG); use --category to pick.

Usage:
  python 21_pdf_to_markdown.py --symbol RELIANCE                  # one company, concalls
  python 21_pdf_to_markdown.py --all                             # all companies, concalls
  python 21_pdf_to_markdown.py --all --category concalls --delete-source
  python 21_pdf_to_markdown.py --all --category all              # every category
  python 21_pdf_to_markdown.py --status
"""
import argparse, logging, sys, time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT   = Path(__file__).parent.parent
PDFS   = ROOT / "data" / "pdfs"
MD_DIR = ROOT / "data" / "companies"
MD_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("pdf2md")

CATEGORIES = ["concalls", "annual_reports", "credit_ratings", "announcements"]
MIN_MD_CHARS = 200   # below this the conversion is considered failed

import re as _re
# standalone page numbers: "11", "Page 5 of 17", "- 3 -"
_PAGE_NUM   = _re.compile(r'^\s*[-â€“]?\s*(page\s+)?\d{1,3}(\s+of\s+\d{1,3})?\s*[-â€“]?\s*$', _re.I)
# digital-signature / e-sign boilerplate lines
_SIGNATURE  = _re.compile(r'digitally signed by|date:\s*20\d{2}[.\-/]\d{2}|\+05.?30.?$|FCS No\.|signature valid', _re.I)
# leftover pymupdf image markers (belt-and-suspenders; ignore_images should remove them)
_IMG_MARKER = _re.compile(r'\*\*==>.*?<==\*\*')
# running header lines that carry a date ("## _Company Ltd February 06, 2025_") â€”
# these recur per page but page-number variance can defeat exact-match detection
_MON = r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?'
_DATE_HDR = _re.compile(r'^\s*#{1,6}\s.*\b' + _MON + r'\s+\d{0,2},?\s*20\d{2}', _re.I)
# speaker labels (## **Name**, **Name:**, **Name**) â€” these RECUR across pages for a
# dominant speaker, so they must NEVER be treated as repeating header/footer boilerplate
_SPEAKER_LIKE = _re.compile(r"^\s*(?:#{1,6}\s*)?\*\*\s*[A-Z][A-Za-z.'â€™\- ]{1,40}?\s*:?\s*\*\*\s*$")


def clean_pages(pages: list[str]) -> str:
    """Strip recurring headers/footers, page numbers, signature blocks from per-page text.
    Recurring-line detection: any short line on >=40% of pages is boilerplate."""
    from collections import Counter
    # some transcripts are rendered as pipe-delimited tables ("|**Name:**|text|");
    # convert pipe cell-boundaries to newlines so speaker labels land at line start
    pages = [_re.sub(r'[ \t]*\|[ \t]*', '\n', pg) for pg in pages]
    n = max(1, len(pages))
    freq = Counter()
    for pg in pages:
        for s in {ln.strip() for ln in pg.splitlines() if 0 < len(ln.strip()) <= 90}:
            if _SPEAKER_LIKE.match(s):   # never count a speaker label as boilerplate
                continue
            freq[s] += 1
    boiler = {ln for ln, c in freq.items() if c >= max(3, n * 0.4)}

    out_pages = []
    for pg in pages:
        kept = []
        for ln in pg.splitlines():
            s = ln.strip()
            if not s:
                kept.append(ln); continue
            if s in boiler or _PAGE_NUM.match(s) or _SIGNATURE.search(s) or _DATE_HDR.match(s):
                continue
            kept.append(ln)
        out_pages.append("\n".join(kept))
    md = "\n\n".join(out_pages)
    md = _IMG_MARKER.sub("", md)
    md = _re.sub(r'\n{3,}', '\n\n', md)   # collapse blank runs left by stripping
    return md.strip()


def _is_bold(span) -> bool:
    return bool(span["flags"] & 16) or any(
        k in span["font"].lower() for k in ("bold", "black", "semibold", "heavy"))


def fast_pages(pdf) -> list[str]:
    """Extract per-page markdown using PyMuPDF directly (~30x faster than pymupdf4llm).
    Reconstructs the only markup our parser needs â€” speaker labels â€” from font info:
    bold spans -> **wrapped**, short larger-font lines -> '## **header**'. The font
    layout analysis pymupdf4llm spends ~6s/pdf on is skipped; we do ~0.2s/pdf."""
    import fitz, statistics
    doc = fitz.open(pdf)
    sizes = [round(sp["size"]) for pg in doc for b in pg.get_text("dict")["blocks"]
             for l in b.get("lines", []) for sp in l.get("spans", []) if sp["text"].strip()]
    body = statistics.median(sizes) if sizes else 10
    pages = []
    for pg in doc:
        lines = []
        for b in pg.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                spans = [sp for sp in l.get("spans", []) if sp["text"].strip()]
                if not spans:
                    continue
                parts = [f"**{sp['text'].strip()}**" if _is_bold(sp) else sp["text"].strip()
                         for sp in spans]
                txt = " ".join(parts)
                raw_len = sum(len(sp["text"]) for sp in spans)
                big = max(sp["size"] for sp in spans) >= body + 1.5
                if big and raw_len <= 55 and "**" not in txt:   # short header line, no bold
                    txt = f"## **{txt}**"
                lines.append(txt)
        pages.append("\n".join(lines))
    doc.close()
    return pages


def pdf_to_clean_markdown(pdf, engine="fast") -> str | None:
    """Convert a PDF to clean markdown, then strip headers/footers/page-nums/signatures.
    engine='fast' (default): PyMuPDF font-based extraction, ~0.2s/pdf.
    engine='pymupdf4llm': slower (~6s/pdf) reference layout engine."""
    if engine == "pymupdf4llm":
        import pymupdf4llm
        chunks = pymupdf4llm.to_markdown(
            str(pdf), ignore_images=True, ignore_graphics=True,
            page_chunks=True, show_progress=False)
        pages = [c.get("text", "") for c in chunks]
    else:
        pages = fast_pages(pdf)
    if not pages:
        return None
    return clean_pages(pages)


def convert_one(args) -> tuple[str, str]:
    """Worker: convert a single PDF to clean markdown. Returns (status, path)."""
    pdf_path_str, md_path_str, delete_source = args
    pdf_path = Path(pdf_path_str)
    md_path  = Path(md_path_str)

    if md_path.exists() and md_path.stat().st_size > MIN_MD_CHARS:
        if delete_source and pdf_path.exists():
            try: pdf_path.unlink()
            except OSError: pass
        return ("skip", md_path_str)

    try:
        md = pdf_to_clean_markdown(pdf_path)
        if not md or len(md) < MIN_MD_CHARS:
            return ("empty", md_path_str)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(md, encoding="utf-8")
        if delete_source and pdf_path.exists():
            try: pdf_path.unlink()
            except OSError: pass
        return ("ok", md_path_str)
    except Exception as e:
        return ("fail", f"{md_path_str} :: {e}")


def gather_jobs(symbols: list[str], categories: list[str], delete_source: bool):
    jobs = []
    for sym in symbols:
        for cat in categories:
            src = PDFS / sym / cat
            if not src.is_dir():
                continue
            for pdf in src.glob("*.pdf"):
                rel = pdf.relative_to(PDFS)
                md_path = (MD_DIR / rel).with_suffix(".md")
                jobs.append((str(pdf), str(md_path), delete_source))
    return jobs


def all_symbols() -> list[str]:
    return sorted([d.name for d in PDFS.iterdir() if d.is_dir()])


def run(symbols, categories, workers, delete_source):
    jobs = gather_jobs(symbols, categories, delete_source)
    if not jobs:
        log.info("No PDFs to convert.")
        return
    log.info(f"Converting {len(jobs):,} PDFs ({', '.join(categories)}) "
             f"with {workers} workers{' [deleting source]' if delete_source else ''} ...")
    total = {"ok": 0, "skip": 0, "empty": 0, "fail": 0}
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(convert_one, j) for j in jobs]
        for i, fut in enumerate(as_completed(futs), 1):
            status, _ = fut.result()
            total[status] = total.get(status, 0) + 1
            if i % 500 == 0:
                rate = i / (time.time() - t0)
                log.info(f"  {i:,}/{len(jobs):,} â€” {total} ({rate:.0f}/s)")
    log.info(f"DONE â€” {total}  in {(time.time()-t0)/60:.1f} min")


def status():
    n_md = sum(1 for _ in MD_DIR.rglob("*.md"))
    md_size = sum(f.stat().st_size for f in MD_DIR.rglob("*.md")) / 1e6
    print(f"Markdown files: {n_md:,}  ({md_size:,.0f} MB)")
    # by category
    from collections import Counter
    c = Counter()
    for f in MD_DIR.rglob("*.md"):
        c[f.parent.name] += 1
    for cat, n in c.most_common():
        print(f"  {cat:18s}: {n:,}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", help="Single symbol")
    ap.add_argument("--all", action="store_true", help="All companies")
    ap.add_argument("--category", default="concalls",
                    help="concalls|annual_reports|credit_ratings|announcements|all (default concalls)")
    ap.add_argument("--delete-source", action="store_true",
                    help="Delete each PDF after successful conversion (frees disk)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.status:
        status(); return

    cats = CATEGORIES if args.category == "all" else [args.category]
    if any(c not in CATEGORIES for c in cats):
        log.error(f"Bad category. Choose from {CATEGORIES} or 'all'."); sys.exit(1)

    if args.symbol:
        run([args.symbol], cats, args.workers, args.delete_source)
    elif args.all:
        run(all_symbols(), cats, args.workers, args.delete_source)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
