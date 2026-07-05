#!/usr/bin/env python3
"""
23_concall_pipeline.py â€” Convert + parse + quality-gate concall PDFs, manage disk.

For each concall PDF (transcript or presentation):
  1. Convert PDF -> markdown (pymupdf4llm).  Markdown is ALWAYS kept.
  2. Transcripts : parse into exchanges (script 22) and run assess_quality().
     Presentations: assess conversion text yield (image-only decks yield ~no text).
  3. Decide PDF fate:
       GOOD parse  -> safe to delete the PDF (with --delete-good)
       WEAK parse  -> KEEP the PDF and FLAG it (for LLM extraction / redownload later)
  4. Append every weak/kept item to data/parsed/_flagged.csv.

Nothing is deleted unless you pass --delete-good. Default is a dry run that just
reports what WOULD happen, so you can eyeball the flagged list first.

Output:
  data/markdown/{SYMBOL}/concalls/*.md         (always)
  data/parsed/{SYMBOL}/concalls/*.json         (transcripts only)
  data/parsed/_flagged.csv                      (weak transcripts + image-only decks)

Usage:
  python 23_concall_pipeline.py --symbol INFY                 # dry run, one company
  python 23_concall_pipeline.py --all                         # dry run, everything
  python 23_concall_pipeline.py --all --delete-good           # actually reclaim disk
  python 23_concall_pipeline.py --status                      # show flagged summary
"""
import argparse, csv, importlib.util, json, logging, re
from datetime import datetime
from pathlib import Path

ROOT   = Path(__file__).parent.parent
PDFS   = ROOT / "data" / "pdfs"
MD_DIR = ROOT / "data" / "companies"
PARSED = ROOT / "data" / "parsed"
PARSED.mkdir(parents=True, exist_ok=True)
FLAGGED_CSV = PARSED / "_flagged.csv"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("concall")

# import the transcript parser (22) and the clean converter (21) as modules
def _imp(name, fname):
    s = importlib.util.spec_from_file_location(name, Path(__file__).parent / fname)
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
tp  = _imp("tp",  "22_transcript_parser.py")
c21 = _imp("c21", "21_pdf_to_markdown.py")

MIN_PRES_PROSE = 2_000   # min chars of REAL prose for a deck to be useful text


def real_prose(md: str) -> str:
    """Substantive prose = lines long enough to be sentences (not bullets/labels/codes)."""
    return " ".join(l for l in md.splitlines() if len(l.strip()) > 40)


def to_markdown(pdf: Path, md: Path, reconvert: bool = False) -> str | None:
    """Convert a PDF to CLEAN markdown (idempotent). Returns the markdown text or None."""
    if md.exists() and md.stat().st_size > 200 and not reconvert:
        return md.read_text(encoding="utf-8")
    try:
        text = c21.pdf_to_clean_markdown(pdf)
        if not text or len(text) < 50:
            return None
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(text, encoding="utf-8")
        return text
    except Exception as e:
        log.warning(f"convert fail {pdf.name}: {e}")
        return None


def flag(rows: list[dict]):
    if not rows:
        return
    write_header = not FLAGGED_CSV.exists()
    with FLAGGED_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ts", "symbol", "kind", "file", "reasons", "pdf_kept"])
        if write_header:
            w.writeheader()
        w.writerows(rows)


def process_symbol(symbol: str, delete_good: bool, reconvert: bool = False) -> dict:
    cc_pdf = PDFS / symbol / "concalls"
    if not cc_pdf.is_dir():
        return {}
    counts = {"good": 0, "weak": 0, "pres_ok": 0, "pres_imageonly": 0, "deleted": 0, "fail": 0}
    flagged = []

    for pdf in sorted(cc_pdf.glob("*.pdf")):
        is_pres = "presentation" in pdf.stem.lower() or "ppt" in pdf.stem.lower()
        md_path = (MD_DIR / pdf.relative_to(PDFS)).with_suffix(".md")
        md = to_markdown(pdf, md_path, reconvert=reconvert)
        if md is None:
            counts["fail"] += 1
            flagged.append({"ts": datetime.now().isoformat(timespec="seconds"),
                            "symbol": symbol, "kind": "convert_fail",
                            "file": pdf.name, "reasons": "no_text_from_pdf", "pdf_kept": 1})
            continue

        if is_pres:
            # presentations: no Q&A parse; gate on REAL PROSE (charts/numbers come from
            # Screener+Tijori, so an image-only deck with little prose is useless to us).
            prose = real_prose(md)
            if len(prose) >= MIN_PRES_PROSE:
                counts["pres_ok"] += 1
                if delete_good:
                    pdf.unlink(missing_ok=True); counts["deleted"] += 1
            else:
                counts["pres_imageonly"] += 1
                flagged.append({"ts": datetime.now().isoformat(timespec="seconds"),
                                "symbol": symbol, "kind": "presentation",
                                "file": pdf.name, "reasons": f"low_prose({len(prose)}chars)",
                                "pdf_kept": 1})
            continue

        # transcripts: parse + quality gate
        parsed = tp.parse_transcript(md, symbol, tp.parse_period(pdf.stem))
        q = tp.assess_quality(parsed, md)
        parsed["parse_quality"] = q["quality"]
        parsed["quality_reasons"] = q["reasons"]
        out = PARSED / symbol / "concalls" / f"{pdf.stem}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")

        if q["quality"] == "good":
            counts["good"] += 1
            if delete_good:
                pdf.unlink(missing_ok=True); counts["deleted"] += 1
        else:
            counts["weak"] += 1
            flagged.append({"ts": datetime.now().isoformat(timespec="seconds"),
                            "symbol": symbol, "kind": "transcript",
                            "file": pdf.name, "reasons": ";".join(q["reasons"]), "pdf_kept": 1})

    return counts, flagged


def _worker(args):
    """Top-level worker for ProcessPoolExecutor (must be picklable)."""
    symbol, delete_good, reconvert = args
    try:
        return symbol, process_symbol(symbol, delete_good, reconvert=reconvert)
    except Exception as e:
        return symbol, ({"fail": 1}, [{"ts": datetime.now().isoformat(timespec="seconds"),
                        "symbol": symbol, "kind": "symbol_error",
                        "file": "", "reasons": str(e)[:120], "pdf_kept": 1}])


def all_symbols() -> list[str]:
    return sorted([d.name for d in PDFS.iterdir() if d.is_dir() and (d / "concalls").is_dir()])


def run(symbols, delete_good, reconvert=False, workers=6):
    from concurrent.futures import ProcessPoolExecutor, as_completed
    mode = "DELETE-GOOD" if delete_good else "DRY-RUN (no deletes)"
    log.info(f"Concall pipeline [{mode}{', RECONVERT' if reconvert else ''}] "
             f"over {len(symbols)} companies, {workers} workers ...")
    tot = {}
    done = 0
    tasks = [(s, delete_good, reconvert) for s in symbols]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_worker, t) for t in tasks]
        for fut in as_completed(futs):
            symbol, (counts, flagged) = fut.result()
            for k, v in counts.items():
                tot[k] = tot.get(k, 0) + v
            flag(flagged)                       # central, serial write â€” no races
            done += 1
            if done % 100 == 0:
                log.info(f"  {done}/{len(symbols)} â€” {tot}")
    log.info(f"DONE â€” {tot}")
    if not delete_good and tot.get("good"):
        log.info(f"Dry run: {tot['good']} transcripts + {tot.get('pres_ok',0)} decks are GOOD "
                 f"and would be deleted with --delete-good. {tot.get('weak',0)} weak kept & flagged.")


def status():
    if not FLAGGED_CSV.exists():
        print("No flagged items yet."); return
    rows = list(csv.DictReader(FLAGGED_CSV.open(encoding="utf-8")))
    from collections import Counter
    by_kind = Counter(r["kind"] for r in rows)
    by_reason = Counter(r["reasons"].split("(")[0].split(";")[0] for r in rows)
    print(f"Flagged items: {len(rows):,}")
    for k, n in by_kind.most_common():
        print(f"  {k:16s}: {n:,}")
    print("Top reasons:")
    for r, n in by_reason.most_common(8):
        print(f"  {r:24s}: {n:,}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--delete-good", action="store_true",
                    help="Actually delete PDFs whose markdown/parse is GOOD (reclaim disk)")
    ap.add_argument("--reconvert", action="store_true",
                    help="Re-convert PDFs even if markdown exists (apply latest cleaner)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.status:
        status()
    elif args.symbol:
        counts, flagged = process_symbol(args.symbol, args.delete_good, reconvert=args.reconvert)
        flag(flagged)
        log.info(counts)
    elif args.all:
        run(all_symbols(), args.delete_good, reconvert=args.reconvert, workers=args.workers)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
